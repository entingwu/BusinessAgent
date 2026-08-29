"""
A separate connection for knowledge-base metadata.

## Why it does not reuse db_client's

`knowledge_sources` / `knowledge_chunks` / `retrieval_traces` record which embedding model and
which corpus the current index was built from. But **the vector index itself is a per-checkout,
gitignored thing** (a Chroma directory, a Milvus container) while the metadata lives in the one
shared MySQL.

When two things are shared to different degrees, this happens: on 2026-08-28 this branch rebuilt
the index with BGE-M3 (45 -> 47 chunks) and rewrote the shared metadata table to `BAAI/bge-m3`,
while everyone on main still had 45 chunks from `text-embedding-v3` in their Chroma. The
acceptance check in CLAUDE.md — "`vector_chunks` must equal `metadata_chunks`" — then failed for
all of them, **for a reason that had nothing to do with their own environment**.

Worse, it misleads silently: `ingest` decides what to skip by comparing the `content_hash` in the
metadata and never checks whether the local vector store actually holds those vectors. Someone on
main running plain `ingest` gets "all sources skipped, completed successfully" and carries on with
an index built by a different model.

## The conclusion: isolate the metadata wherever the index is isolated

`KNOWLEDGE_DATABASE_URL` defaults to `DATABASE_URL`, so main's behaviour is unchanged. A branch
that swaps the embedding model or the vector store points it at its own database, physically
separated from main.

This is the same judgement this project keeps arriving at: **something shared should either be
genuinely shared or physically separated; the worst state is "looks shared, means something
different".**
"""
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from business_agent.config.settings import settings

_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def get_knowledge_session_factory() -> async_sessionmaker[AsyncSession]:
  """
  Goal: the session factory for knowledge metadata, as a per-process singleton.
        Falls back to DATABASE_URL when KNOWLEDGE_DATABASE_URL is unset, matching the behaviour
        before this split.
  Returns: async_sessionmaker[AsyncSession]
  """
  global _engine, _factory
  if _factory is None:
    url = settings.knowledge_database_url or settings.database_url
    _engine = create_async_engine(url=url, echo=False)
    _factory = async_sessionmaker(_engine, expire_on_commit=False)
  return _factory


def get_knowledge_engine() -> AsyncEngine:
  """Goal: the engine for knowledge metadata, used when creating tables."""
  get_knowledge_session_factory()
  assert _engine is not None
  return _engine


async def dispose() -> None:
  if _engine is not None:
    await _engine.dispose()
