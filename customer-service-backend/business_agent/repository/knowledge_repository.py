"""
知识源与知识分片的元数据落库（规范 C.4.8）

向量与文档正文在 Chroma 里，这里存的是「运维视角」需要的东西：
知识源类型、名称、入库时间、**Embedding 模型名**。
模型名必须落库——换了模型就必须全量重建索引（维度与向量空间都变，规范 C.4.4），
没有这一列就看不出来索引已经脏了。

另外还有一张 retrieval_traces：入库侧的两张表回答「知识库里有什么」，
retrieval_traces 回答「哪一轮回复用了哪几片、相似度多少」，两者没有关联关系，
不要指望 knowledge_chunks 能替代它做溯源。
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Index, Integer, String, TEXT, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.mysql import insert

from business_agent.repository.base import Base


def _now() -> datetime:
  return datetime.now(timezone.utc).replace(tzinfo=None)


class KnowledgeSourceRecord(Base):
  """知识源：一份 FAQ 表 / 一篇政策文档"""
  __tablename__ = "knowledge_sources"

  source_id: Mapped[str] = mapped_column(String(128), primary_key=True)
  source_type: Mapped[str] = mapped_column(String(32), nullable=False)          # faq / document
  name: Mapped[str] = mapped_column(String(255), nullable=False)                # 展示用名称
  file_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
  embedding_model: Mapped[str] = mapped_column(String(64), nullable=False)
  embedding_dimensions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
  chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
  content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
  ingested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class KnowledgeChunkRecord(Base):
  """知识分片：来源标识（知识源 ID、标题、片段位置）在这里，检索结果靠它溯源"""
  __tablename__ = "knowledge_chunks"

  chunk_id: Mapped[str] = mapped_column(String(160), primary_key=True)
  source_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
  source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
  source_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
  title: Mapped[str] = mapped_column(String(255), nullable=False, default="")      # 分片标题（章节名 / FAQ 问题）
  position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)        # 片段在知识源内的序号
  token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
  content: Mapped[str] = mapped_column(TEXT, nullable=False)
  embedding_model: Mapped[str] = mapped_column(String(64), nullable=False, default="")
  ingested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class RetrievalTraceRecord(Base):
  """
  Goal: 按轮次记录知识检索的溯源证据，回答「这一轮回复用了哪几片、相似度多少」
        （规范 3.1.2「内部记录命中的分片 ID 与相似度」、5.2「基于文档知识的回复可溯源到具体分片」）。

  为什么单独一张表，而不是塞进 dialogue_states 的 state_json：
    1. state_json 是整个 DialogueState 的序列化结果，往里塞检索明细会让它继续膨胀，
       而规范 第一档 本来就要把消息拆表，此处不宜再加重
    2. 溯源是审计数据，读法是「按 turn_id / sender_id 查」，需要索引，JSON 列做不到
    3. 溯源写失败绝不能影响对话，独立表可以独立事务、独立降级

  为什么一片一行而不是一轮一行 JSON：
    调阈值时要按 score 做聚合（「兜底那批的最高分都落在哪个区间」），一片一行才查得动。

  未命中与降级也会落一行（chunk_id 为空、outcome 标明原因）——
  「这一轮为什么兜底了」和「这一轮引用了什么」同等重要。

  **不落的是「低于阈值被 Provider 挡掉的候选」**：它们从来没进过 responder，
  Provider.retrival 的签名（返回 list[KnowledgeChunk]）不允许把它们额外带出来，
  而为此在 Provider 上挂实例状态会在引擎被缓存复用时变成并发隐患。
  这批候选的 chunk_id 与相似度打在 knowledge_retrieval 那行日志的 rejected=[...] 里，
  「差多少才够阈值」去日志查（日志级别由 KNOWLEDGE_LOG_LEVEL 控制）。
  """
  __tablename__ = "retrieval_traces"

  id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

  # 关联键：全部取自 DialogueState，不需要改 domain 层
  sender_id: Mapped[str] = mapped_column(String(128), nullable=False)
  session_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
  turn_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
  message_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")

  # 本轮结局：answered（基于分片作答）/ no_hit（未命中兜底）/ unavailable（检索链路降级）
  outcome: Mapped[str] = mapped_column(String(16), nullable=False)
  provider_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")

  # 分片溯源信息，未命中与降级时为空
  chunk_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
  source_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
  source_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
  source_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
  position: Mapped[int | None] = mapped_column(Integer, nullable=True)
  score: Mapped[float | None] = mapped_column(Float, nullable=True)

  # 是否真的进了提示词。Top-K 与上下文 token 预算截断掉的分片也记，便于事后调阈值
  selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
  drop_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

  # 降级时的失败原因摘要（轮级：哪个 Provider 先炸导致整轮降级）；命中与未命中时为空
  note: Mapped[str | None] = mapped_column(String(255), nullable=True)

  # 当时的检索配置，换阈值 / 换模型后回读历史记录不至于对不上账
  threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
  embedding_model: Mapped[str] = mapped_column(String(64), nullable=False, default="")

  created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)

  __table_args__ = (
    Index("ix_retrieval_traces_turn", "turn_id"),
    Index("ix_retrieval_traces_sender_created", "sender_id", "created_at"),
  )


@dataclass(slots=True)
class SourceSummary:
  """Goal: 给命令行展示用的知识源摘要，不把 ORM 对象漏出仓储层"""
  source_id: str
  source_type: str
  name: str
  chunk_count: int
  embedding_model: str
  ingested_at: datetime

  def to_dict(self) -> dict[str, Any]:
    return {
      "source_id": self.source_id,
      "source_type": self.source_type,
      "name": self.name,
      "chunk_count": self.chunk_count,
      "embedding_model": self.embedding_model,
      "ingested_at": self.ingested_at.isoformat(),
    }


async def ensure_tables(engine) -> None:
  """
  Goal: 建表。只建知识库这三张，不碰 dialogue_states。
  Args:
      engine: AsyncEngine
  """
  async with engine.begin() as conn:
    await conn.run_sync(
      Base.metadata.create_all,
      tables=[KnowledgeSourceRecord.__table__, KnowledgeChunkRecord.__table__,
              RetrievalTraceRecord.__table__],
      checkfirst=True,
    )


class KnowledgeRepository:
  """Goal: 知识源 / 分片元数据的读写"""

  def __init__(self, session: AsyncSession):
    self._session = session

  async def upsert_source(self,
                          *,
                          source_id: str,
                          source_type: str,
                          name: str,
                          file_path: str,
                          embedding_model: str,
                          embedding_dimensions: int,
                          chunk_count: int,
                          content_hash: str) -> None:
    """
    Goal: 新增或更新一个知识源的元数据（按 source_id 幂等）
    Args:
        source_id / source_type / name / file_path
        embedding_model / embedding_dimensions: 换模型必须重建索引，故与索引一起落库
        chunk_count: 本次入库的分片数
        content_hash: 原文哈希，用于判断是否需要重新入库
    """
    values = {
      "source_id": source_id,
      "source_type": source_type,
      "name": name,
      "file_path": file_path,
      "embedding_model": embedding_model,
      "embedding_dimensions": embedding_dimensions,
      "chunk_count": chunk_count,
      "content_hash": content_hash,
      "ingested_at": _now(),
    }
    insert_stmt = insert(KnowledgeSourceRecord).values(**values)
    update_stmt = insert_stmt.on_duplicate_key_update(
      **{key: insert_stmt.inserted[key] for key in values if key != "source_id"}
    )
    await self._session.execute(update_stmt)

  async def replace_chunks(self, source_id: str, chunk_rows: list[dict[str, Any]]) -> None:
    """
    Goal: 用新分片整体替换该知识源的旧分片（先删后插，保证更新后不留孤儿分片）
    Args:
        source_id: 知识源 ID
        chunk_rows: 分片字典列表，键与 KnowledgeChunkRecord 的列同名
    """
    await self._session.execute(
      delete(KnowledgeChunkRecord).where(KnowledgeChunkRecord.source_id == source_id)
    )
    if chunk_rows:
      await self._session.execute(insert(KnowledgeChunkRecord), chunk_rows)

  async def delete_source(self, source_id: str) -> int:
    """
    Goal: 删除知识源及其全部分片元数据
    Args:
        source_id
    Returns: int 删除的分片数
    """
    cursor = await self._session.execute(
      delete(KnowledgeChunkRecord).where(KnowledgeChunkRecord.source_id == source_id)
    )
    await self._session.execute(
      delete(KnowledgeSourceRecord).where(KnowledgeSourceRecord.source_id == source_id)
    )
    return cursor.rowcount or 0

  async def get_source_hash(self, source_id: str) -> str | None:
    """
    Goal: 取知识源上次入库时的原文哈希，用于跳过没变化的文档
    Args:
        source_id
    Returns: str | None
    """
    cursor = await self._session.execute(
      select(KnowledgeSourceRecord.content_hash).where(KnowledgeSourceRecord.source_id == source_id)
    )
    return cursor.scalar_one_or_none()

  async def list_sources(self) -> list[SourceSummary]:
    """
    Goal: 列出全部知识源
    Returns: list[SourceSummary]
    """
    cursor = await self._session.execute(
      select(KnowledgeSourceRecord).order_by(KnowledgeSourceRecord.source_id)
    )
    return [
      SourceSummary(
        source_id=record.source_id,
        source_type=record.source_type,
        name=record.name,
        chunk_count=record.chunk_count,
        embedding_model=record.embedding_model,
        ingested_at=record.ingested_at,
      )
      for record in cursor.scalars().all()
    ]

  async def count_chunks(self) -> int:
    """
    Goal: 分片元数据总数（与向量库 count() 对账用）
    Returns: int
    """
    cursor = await self._session.execute(select(func.count()).select_from(KnowledgeChunkRecord))
    return int(cursor.scalar_one())

  async def record_retrieval_traces(self, trace_rows: list[dict[str, Any]]) -> int:
    """
    Goal: 批量写入一轮对话的检索溯源记录
    Args:
        trace_rows: 字典列表，键与 RetrievalTraceRecord 的列同名
    Returns: int 写入行数
    """
    if not trace_rows:
      return 0
    await self._session.execute(insert(RetrievalTraceRecord), trace_rows)
    return len(trace_rows)

  async def list_retrieval_traces(self, sender_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """
    Goal: 按用户回读检索溯源记录（排查「这一轮凭什么这么答」用）
    Args:
        sender_id / limit
    Returns: list[dict]
    """
    cursor = await self._session.execute(
      select(RetrievalTraceRecord)
      .where(RetrievalTraceRecord.sender_id == sender_id)
      .order_by(RetrievalTraceRecord.id.desc())
      .limit(limit)
    )
    return [
      {
        "turn_id": record.turn_id,
        "outcome": record.outcome,
        "provider_id": record.provider_id,
        "chunk_id": record.chunk_id,
        "source_title": record.source_title,
        "score": record.score,
        "selected": record.selected,
        "note": record.note,
        "drop_reason": record.drop_reason,
        "threshold": record.threshold,
        "created_at": record.created_at.isoformat(),
      }
      for record in cursor.scalars().all()
    ]

  async def delete_retrieval_traces(self, sender_id_prefix: str) -> int:
    """
    Goal: 清理探针数据（sender_id 前缀匹配）
    Args:
        sender_id_prefix
    Returns: int 删除行数
    """
    cursor = await self._session.execute(
      delete(RetrievalTraceRecord).where(RetrievalTraceRecord.sender_id.like(f"{sender_id_prefix}%"))
    )
    return cursor.rowcount or 0

  async def commit(self) -> None:
    await self._session.commit()


async def main_test():
  from business_agent.infrastructure import db_client

  db_client.init_db_engine()
  await ensure_tables(db_client.session_engine)
  async with db_client.session_factory() as session:
    repository = KnowledgeRepository(session)
    for summary in await repository.list_sources():
      print(summary.to_dict())
    print(f"chunks={await repository.count_chunks()}")
  await db_client.dispose_engine()


if __name__ == '__main__':
  asyncio.run(main_test())
