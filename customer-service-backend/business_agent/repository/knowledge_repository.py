"""
知识源与知识分片的元数据落库（规范 C.4.8）

向量与文档正文在 Chroma 里，这里存的是「运维视角」需要的东西：
知识源类型、名称、入库时间、**Embedding 模型名**。
模型名必须落库——换了模型就必须全量重建索引（维度与向量空间都变，规范 C.4.4），
没有这一列就看不出来索引已经脏了。
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Integer, String, TEXT, delete, func, select
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
  Goal: 建表。只建知识库这两张，不碰 dialogue_states。
  Args:
      engine: AsyncEngine
  """
  async with engine.begin() as conn:
    await conn.run_sync(
      Base.metadata.create_all,
      tables=[KnowledgeSourceRecord.__table__, KnowledgeChunkRecord.__table__],
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
