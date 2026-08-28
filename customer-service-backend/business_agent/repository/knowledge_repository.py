"""
Metadata persistence for knowledge sources and chunks (spec C.4.8).

The vectors and the document text live in Chroma; what is stored here is what an operator needs:
source type, name, ingest time, and **the embedding model name**.

The model name has to be persisted — changing models forces a full reindex (both the dimensions
and the vector space change, spec C.4.4), and without this column there is no way to see that the
index has gone stale.

There is also a third table, retrieval_traces. The two ingest-side tables answer "what is in the
knowledge base"; retrieval_traces answers "which chunks did this turn's answer use, and at what
similarity". They are not related to one another — do not expect knowledge_chunks to serve as a
substitute for tracing.
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
  """A knowledge source: one FAQ table, or one policy document."""
  __tablename__ = "knowledge_sources"

  source_id: Mapped[str] = mapped_column(String(128), primary_key=True)
  source_type: Mapped[str] = mapped_column(String(32), nullable=False)          # faq / document
  name: Mapped[str] = mapped_column(String(255), nullable=False)                # display name
  file_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
  embedding_model: Mapped[str] = mapped_column(String(64), nullable=False)
  embedding_dimensions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
  chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
  content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
  ingested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class KnowledgeChunkRecord(Base):
  """A knowledge chunk. Its provenance — source id, title, position — lives here, and that is
  what makes a retrieval result traceable."""
  __tablename__ = "knowledge_chunks"

  chunk_id: Mapped[str] = mapped_column(String(160), primary_key=True)
  source_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
  source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
  source_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
  title: Mapped[str] = mapped_column(String(255), nullable=False, default="")      # chunk title (section name / FAQ question)
  position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)        # index of the chunk within its source
  token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
  content: Mapped[str] = mapped_column(TEXT, nullable=False)
  embedding_model: Mapped[str] = mapped_column(String(64), nullable=False, default="")
  ingested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class RetrievalTraceRecord(Base):
  """
  Goal: record per-turn trace evidence for knowledge retrieval, answering "which chunks did this
        turn's answer use, and at what similarity" (spec 3.1.2, "record hit chunk ids and
        similarities internally", and 5.2, "answers grounded in document knowledge trace back to
        specific chunks").

  Why a separate table rather than stuffing it into dialogue_states.state_json:
    1. state_json is the serialisation of the entire DialogueState; adding retrieval detail keeps
       inflating it, and tier 1 of the spec already calls for splitting messages out — this is no
       place to add weight.
    2. A trace is audit data, read by "query by turn_id / sender_id". That needs indexes, which a
       JSON column cannot give.
    3. A failed trace write must never affect the conversation, and a separate table can have its
       own transaction and its own degradation.

  Why one row per chunk rather than one JSON row per turn:
    Tuning the threshold means aggregating by score ("what range do the top scores of the
    fallback turns fall into"), and only one-row-per-chunk is queryable that way.

  Misses and degraded turns get a row too (chunk_id empty, outcome saying why) — "why did this
  turn fall back?" matters exactly as much as "what did this turn cite?".

  **What is deliberately not recorded: candidates the provider rejected for scoring below the
  threshold.** They never reach the responder, and Provider.retrival's signature (returning
  list[KnowledgeChunk]) has no way to carry them out; hanging instance state on the provider to
  do so would become a concurrency hazard once the engine is cached and reused.
  Those candidates' chunk ids and similarities go into the rejected=[...] field of the
  knowledge_retrieval log line instead, so "how far short of the threshold was it?" is answered
  from the log (level controlled by KNOWLEDGE_LOG_LEVEL).
  """
  __tablename__ = "retrieval_traces"

  id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

  # Correlation keys, all taken from DialogueState — no change to the domain layer needed
  sender_id: Mapped[str] = mapped_column(String(128), nullable=False)
  session_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
  turn_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
  message_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")

  # How the turn ended: answered (built from chunks) / no_hit (miss, fallback) /
  # unavailable (retrieval stack degraded)
  outcome: Mapped[str] = mapped_column(String(16), nullable=False)
  provider_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")

  # Chunk provenance; empty on a miss or a degraded turn
  chunk_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
  source_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
  source_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
  source_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
  position: Mapped[int | None] = mapped_column(Integer, nullable=True)
  score: Mapped[float | None] = mapped_column(Float, nullable=True)

  # Whether it actually reached the prompt. Chunks cut by Top-K or by the context token budget
  # are recorded too, which is what makes tuning the threshold afterwards possible
  selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
  drop_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

  # Failure summary on a degraded turn (turn-level: which provider failed first and took the
  # whole turn down); empty on hits and misses
  note: Mapped[str | None] = mapped_column(String(255), nullable=True)

  # The retrieval configuration at the time, so historical rows still reconcile after the
  # threshold or the model changes
  threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
  embedding_model: Mapped[str] = mapped_column(String(64), nullable=False, default="")

  created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)

  __table_args__ = (
    Index("ix_retrieval_traces_turn", "turn_id"),
    Index("ix_retrieval_traces_sender_created", "sender_id", "created_at"),
  )


@dataclass(slots=True)
class SourceSummary:
  """Goal: a knowledge-source summary for the CLI to display, so no ORM object leaks out of the
  repository layer."""
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
  Goal: create tables. Only these three knowledge tables — never dialogue_states.
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
  """Goal: read and write knowledge source / chunk metadata."""

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
    Goal: insert or update one knowledge source's metadata (idempotent on source_id)
    Args:
        source_id / source_type / name / file_path
        embedding_model / embedding_dimensions: changing models forces a reindex, so they are
            persisted alongside the index
        chunk_count: how many chunks this ingest produced
        content_hash: hash of the source text, used to decide whether a re-ingest is needed
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
    Goal: replace this source's chunks wholesale (delete then insert, so an update leaves no
          orphaned chunks behind)
    Args:
        source_id: the knowledge source id
        chunk_rows: a list of dicts whose keys match KnowledgeChunkRecord's columns
    """
    await self._session.execute(
      delete(KnowledgeChunkRecord).where(KnowledgeChunkRecord.source_id == source_id)
    )
    if chunk_rows:
      await self._session.execute(insert(KnowledgeChunkRecord), chunk_rows)

  async def delete_source(self, source_id: str) -> int:
    """
    Goal: delete a knowledge source and all of its chunk metadata
    Args:
        source_id
    Returns: the number of chunks deleted
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
    Goal: read a source's content hash from its last ingest, so unchanged documents are skipped
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
    Goal: list every knowledge source
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
    Goal: total chunk-metadata count, for reconciling against the vector store's count()
    Returns: int
    """
    cursor = await self._session.execute(select(func.count()).select_from(KnowledgeChunkRecord))
    return int(cursor.scalar_one())

  async def record_retrieval_traces(self, trace_rows: list[dict[str, Any]]) -> int:
    """
    Goal: bulk-write one turn's retrieval trace rows
    Args:
        trace_rows: a list of dicts whose keys match RetrievalTraceRecord's columns
    Returns: the number of rows written
    """
    if not trace_rows:
      return 0
    await self._session.execute(insert(RetrievalTraceRecord), trace_rows)
    return len(trace_rows)

  async def list_retrieval_traces(self, sender_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """
    Goal: read retrieval traces back by user, for investigating "what justified this answer?"
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
    Goal: clean up probe data, matched by sender_id prefix
    Args:
        sender_id_prefix
    Returns: the number of rows deleted
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
