"""
入库流水线：加载 → 切分 → 向量化 → 写索引 + 写元数据

支持知识源的新增 / 更新 / 删除，更新后索引同步生效：
更新走「先按 source_id 删旧分片，再写新分片」，避免留下孤儿向量。
"""
import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from business_agent.config.settings import settings
from business_agent.infrastructure import db_client
from business_agent.infrastructure.llm_client import embed_documents, embedding_model_name
from business_agent.infrastructure.vector_client import ChromaVectorClient, VectorRecord, get_vector_client
from business_agent.knowledge.ingest.loader import LoadedSource, discover_files, load_source
from business_agent.knowledge.ingest.splitter import PreparedChunk, embedding_text, split_source
from business_agent.repository.knowledge_repository import KnowledgeRepository, ensure_tables


@dataclass(slots=True)
class SourceResult:
  """Goal: 单个知识源的入库结果"""
  source_id: str
  source_type: str
  name: str
  chunk_count: int
  status: str            # ingested / skipped / deleted


@dataclass(slots=True)
class IngestReport:
  """Goal: 一次入库执行的汇总"""
  results: list[SourceResult] = field(default_factory=list)
  vector_count: int = 0
  embedding_model: str = ""

  def to_dict(self) -> dict[str, Any]:
    return {
      "embedding_model": self.embedding_model,
      "vector_count": self.vector_count,
      "sources": [
        {
          "source_id": result.source_id,
          "source_type": result.source_type,
          "name": result.name,
          "chunk_count": result.chunk_count,
          "status": result.status,
        }
        for result in self.results
      ],
    }


class IngestPipeline:
  """
  Goal: 把 knowledge_source/ 下的文档写进 Chroma 索引与 MySQL 元数据表
  """

  def __init__(self,
               vector_client: ChromaVectorClient | None = None,
               source_dir: Path | None = None):
    self._vector_client = vector_client or get_vector_client()
    self._source_dir = source_dir or settings.resolved_knowledge_source_dir()

  async def ingest(self,
                   repository: KnowledgeRepository,
                   *,
                   source_ids: list[str] | None = None,
                   force: bool = False) -> IngestReport:
    """
    Goal: 全量或按知识源 ID 增量入库
    Args:
        repository: 元数据仓储
        source_ids: 只入库这些知识源；None 表示全部
        force: 内容未变化时也重新切分与向量化
    Returns: IngestReport
    """
    report = IngestReport(embedding_model=embedding_model_name())

    for file_path in discover_files(self._source_dir):
      source = load_source(self._source_dir, file_path)
      if source_ids and source.source_id not in source_ids:
        continue

      previous_hash = await repository.get_source_hash(source.source_id)
      if not force and previous_hash == source.content_hash:
        report.results.append(SourceResult(
          source_id=source.source_id,
          source_type=source.source_type,
          name=source.name,
          chunk_count=0,
          status="skipped",
        ))
        continue

      chunk_count = await self._ingest_one(repository, source)
      report.results.append(SourceResult(
        source_id=source.source_id,
        source_type=source.source_type,
        name=source.name,
        chunk_count=chunk_count,
        status="ingested",
      ))

    await repository.commit()
    report.vector_count = await self._vector_client.count()
    return report

  async def delete(self, repository: KnowledgeRepository, source_id: str) -> SourceResult:
    """
    Goal: 删除一个知识源：索引与元数据一起删，删完检索不到
    Args:
        repository / source_id
    Returns: SourceResult
    """
    removed_vectors = await self._vector_client.delete(source_id)
    await repository.delete_source(source_id)
    await repository.commit()
    return SourceResult(
      source_id=source_id,
      source_type="",
      name="",
      chunk_count=removed_vectors,
      status="deleted",
    )

  async def _ingest_one(self, repository: KnowledgeRepository, source: LoadedSource) -> int:
    """
    Goal: 单个知识源的入库：切分 → 向量化 → 先删后写 → 落元数据
    Args:
        repository / source
    Returns: int 写入的分片数
    """
    chunks: list[PreparedChunk] = split_source(source)
    if not chunks:
      await self._vector_client.delete(source.source_id)
      await repository.replace_chunks(source.source_id, [])
      return 0

    model_name = embedding_model_name()
    vectors = await embed_documents([embedding_text(chunk) for chunk in chunks])

    records = [
      VectorRecord(
        id=chunk.chunk_id,
        vector=vector,
        document=chunk.content,
        metadata=chunk.metadata(model_name),
      )
      for chunk, vector in zip(chunks, vectors)
    ]

    # 更新语义：先删除该知识源的旧分片，再写入新分片，索引同步生效
    await self._vector_client.delete(source.source_id)
    await self._vector_client.upsert(records)

    await repository.upsert_source(
      source_id=source.source_id,
      source_type=source.source_type,
      name=source.name,
      file_path=source.file_path,
      embedding_model=model_name,
      embedding_dimensions=settings.embedding_dimensions,
      chunk_count=len(chunks),
      content_hash=source.content_hash,
    )
    await repository.replace_chunks(source.source_id, [
      {
        "chunk_id": chunk.chunk_id,
        "source_id": chunk.source_id,
        "source_type": chunk.source_type,
        "source_name": chunk.source_name,
        "title": chunk.title,
        "position": chunk.position,
        "token_count": chunk.token_count,
        "content": chunk.content,
        "embedding_model": model_name,
      }
      for chunk in chunks
    ])

    return len(chunks)


async def run_with_repository(handler):
  """
  Goal: 命令行场景下的资源管理：起 DB 引擎 → 建表 → 交出 repository → 释放
  Args:
      handler: async callable，接收 KnowledgeRepository
  Returns: handler 的返回值
  """
  db_client.init_db_engine()
  await ensure_tables(db_client.session_engine)
  try:
    async with db_client.session_factory() as session:
      return await handler(KnowledgeRepository(session))
  finally:
    await db_client.dispose_engine()


async def main_test():
  pipeline = IngestPipeline()
  report = await run_with_repository(lambda repository: pipeline.ingest(repository))
  print(report.to_dict())


if __name__ == '__main__':
  asyncio.run(main_test())
