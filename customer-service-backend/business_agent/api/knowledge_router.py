"""
Read-only endpoints backing the Knowledge console page.

Everything here already existed as CLI output (`python -m business_agent.knowledge.ingest
stats / list / query`). This exposes the same facts over HTTP so the console can show what the
retrieval chain is actually running on, instead of a "Soon" badge over a working capability.

**Read-only on purpose.** Ingesting rewrites the shared index and re-embeds every chunk; that
belongs to a deliberate CLI invocation, not to a button someone can click while a demo is running.
"""
from fastapi import APIRouter

from business_agent.api.schemas import KnowledgeProbeRequest, KnowledgeProbeResponse, KnowledgeStatsResponse
from business_agent.config.settings import settings
from business_agent.infrastructure.vector_client import get_vector_client
from business_agent.knowledge.provider.rag import KnowledgeRetriever
from business_agent.repository.knowledge_repository import KnowledgeRepository, ensure_tables
from business_agent.infrastructure import knowledge_db

router = APIRouter()


@router.get("/api/knowledge/stats", response_model=KnowledgeStatsResponse)
async def knowledge_stats() -> KnowledgeStatsResponse:
  """
  Goal: what the retrieval chain is running on right now — backend, model, sources, chunk counts.
  Returns: KnowledgeStatsResponse

  vector_chunks and metadata_chunks come from two independent places (the vector store and the
  metadata database) and must agree. They are both surfaced rather than reduced to a boolean so
  the page can show which side is short when they do not.
  """
  await ensure_tables(knowledge_db.get_knowledge_engine())
  async with knowledge_db.get_knowledge_session_factory()() as session:
    sources = await KnowledgeRepository(session).list_sources()
  vector_chunks = await get_vector_client().count()
  metadata_chunks = sum(source.chunk_count for source in sources)
  return KnowledgeStatsResponse(
    vector_backend=settings.vector_backend,
    embedding_backend=settings.embedding_backend,
    embedding_model=settings.embedding_model,
    rerank_enabled=settings.rerank_enabled,
    graph_enabled=settings.knowledge_graph_enabled,
    top_k=settings.knowledge_top_k,
    score_gate=settings.rerank_score_min if settings.rerank_enabled else settings.knowledge_score_threshold,
    score_gate_scale="rerank relevance" if settings.rerank_enabled else "vector cosine",
    vector_chunks=vector_chunks,
    metadata_chunks=metadata_chunks,
    sources=[
      {"source_id": s.source_id, "source_type": s.source_type, "name": s.name,
       "chunk_count": s.chunk_count, "embedding_model": s.embedding_model}
      for s in sources
    ],
  )


@router.post("/api/knowledge/probe", response_model=KnowledgeProbeResponse)
async def knowledge_probe(request: KnowledgeProbeRequest) -> KnowledgeProbeResponse:
  """
  Goal: run one retrieval and show what came back, **without calling the LLM**.

  This is the single most useful thing when tuning: it separates "retrieval did not find it"
  from "retrieval found it and the model answered badly". Those two look identical from the
  chat window and have completely different fixes.
  Args: request.text — the question to retrieve for
  Returns: KnowledgeProbeResponse
  """
  chunks = await KnowledgeRetriever().retrieve(request.text)
  return KnowledgeProbeResponse(
    query=request.text,
    hit=bool(chunks),
    gate=settings.rerank_score_min if settings.rerank_enabled else settings.knowledge_score_threshold,
    chunks=[
      {"chunk_id": c.chunk_id or "", "source_title": c.source_title or "",
       "source_type": c.source_type or "", "score": round(c.score or 0.0, 4),
       "excerpt": (c.content or "")[:200]}
      for c in chunks
    ],
  )
