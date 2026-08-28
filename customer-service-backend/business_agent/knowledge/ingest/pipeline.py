"""
The ingest pipeline: load -> split -> embed -> write index + write metadata.

Sources can be added, updated and deleted, and the index stays in step after an update: an update
deletes the old chunks by source_id before writing the new ones, so no orphaned vectors are left
behind.
"""
import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from business_agent.config.settings import settings
from business_agent.infrastructure import knowledge_db
from business_agent.infrastructure.embedding import get_embedding_backend
from business_agent.infrastructure.vector_client import ChromaVectorClient, VectorRecord, get_vector_client
from business_agent.knowledge.ingest.loader import LoadedSource, discover_files, load_source
from business_agent.knowledge.ingest.splitter import PreparedChunk, embedding_text, split_source
from business_agent.repository.knowledge_repository import KnowledgeRepository, ensure_tables


@dataclass(slots=True)
class SourceResult:
  """Goal: the ingest result for one knowledge source."""
  source_id: str
  source_type: str
  name: str
  chunk_count: int
  status: str            # ingested / skipped / deleted


@dataclass(slots=True)
class IngestReport:
  """Goal: the summary of one ingest run."""
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
  Goal: write the documents under knowledge_source/ into the Chroma index and the MySQL metadata
        tables
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
    Goal: ingest everything, or incrementally by source id
    Args:
        repository: the metadata repository
        source_ids: ingest only these sources; None means all of them
        force: re-split and re-embed even when the content has not changed
    Returns: IngestReport
    """
    report = IngestReport(embedding_model=get_embedding_backend().name)

    for file_path in discover_files(self._source_dir):
      source = load_source(self._source_dir, file_path)
      if source_ids and source.source_id not in source_ids:
        continue

      previous_hash = await repository.get_source_hash(source.source_id)
      # Rows written before the relative-path change store an absolute path. content_hash has not
      # changed, so the plain skip would leave new code running against old data — and nothing
      # would warn about it. Detect the stale format and re-ingest that source automatically,
      # rather than documenting "remember to run --force after merging".
      previous_path = await repository.get_source_file_path(source.source_id)
      stale_path_format = bool(previous_path) and Path(previous_path).is_absolute()
      if not force and previous_hash == source.content_hash and not stale_path_format:
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
    Goal: delete a knowledge source — index and metadata together, so it can no longer be
          retrieved
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
    Goal: ingest one source: split -> embed -> delete then write -> persist metadata
    Args:
        repository / source
    Returns: the number of chunks written
    """
    chunks: list[PreparedChunk] = split_source(source)
    if not chunks:
      await self._vector_client.delete(source.source_id)
      await repository.replace_chunks(source.source_id, [])
      return 0

    backend = get_embedding_backend()
    model_name = backend.name
    result = await backend.embed_documents([embedding_text(chunk) for chunk in chunks])

    # sparse 只有 bge_m3 后端产出；dashscope 后端下 result.sparse 为空列表，
    # 这里给 None，向量库那边按「没有稀疏向量」处理（Chroma 本就忽略，
    # Milvus 会写入空稀疏向量，该条只能被 dense 那一路命中）。
    sparse_rows = result.sparse if result.has_sparse else [None] * len(chunks)

    records = [
      VectorRecord(
        id=chunk.chunk_id,
        vector=vector,
        document=chunk.content,
        metadata=chunk.metadata(model_name),
        sparse=sparse,
      )
      for chunk, vector, sparse in zip(chunks, result.dense, sparse_rows)
    ]

    # Update semantics: delete this source's old chunks first, then write the new ones, so the
    # index stays in step
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
  Goal: resource management for the CLI: start the DB engine -> create tables -> hand over the
        repository -> release
  Args:
      handler: an async callable that receives a KnowledgeRepository
  Returns: whatever handler returned
  """
  await ensure_tables(knowledge_db.get_knowledge_engine())
  try:
    async with knowledge_db.get_knowledge_session_factory()() as session:
      return await handler(KnowledgeRepository(session))
  finally:
    await knowledge_db.dispose()


async def main_test():
  pipeline = IngestPipeline()
  report = await run_with_repository(lambda repository: pipeline.ingest(repository))
  print(report.to_dict())


if __name__ == '__main__':
  asyncio.run(main_test())
