"""
Vector retrieval providers: Top-K + similarity threshold + metadata filter (spec C.4.5, tier 1).

On a miss, or when everything scores below the threshold, this **returns an empty list** rather
than fallback text — how the fallback is worded is KnowledgeResponder's decision, and a provider
answers only "did anything come back".
"""
import asyncio
import logging

from business_agent.chat_history.builder import ChatHistoryBuilder
from business_agent.config.settings import settings
from business_agent.domain.state import DialogueState
from business_agent.infrastructure.embedding import EmbeddingUnavailableError, get_embedding_backend
from business_agent.infrastructure.reranker import RerankUnavailableError, cliff_cutoff, rerank
from business_agent.knowledge.fusion import rrf_merge
from business_agent.knowledge.hyde import HydeUnavailableError, generate_hypothetical_answer
from business_agent.knowledge.thresholds import resolve_vector_threshold
from business_agent.infrastructure.vector_client import (
  ChromaVectorClient,
  VectorMatch,
  VectorStoreUnavailableError,
  get_vector_client,
)
from business_agent.knowledge.provider.provider import KnowledgeChunk, KnowledgeUnavailableError, Provider

logger = logging.getLogger(__name__)


class KnowledgeRetriever:
  """
  Goal: turn one natural-language question into a set of chunks carrying provenance
  """

  def __init__(self,
               vector_client: ChromaVectorClient | None = None,
               top_k: int | None = None,
               score_threshold: float | None = None):
    self._vector_client = vector_client or get_vector_client()
    self._top_k = top_k if top_k is not None else settings.knowledge_top_k
    # Kept as None when not given, so the per-question resolver runs at call time. Storing a
    # settings value here instead would freeze one language's threshold onto the instance.
    self._score_threshold = score_threshold
    self._rerank_enabled = settings.rerank_enabled
    self._hyde_enabled = settings.hyde_enabled

  async def retrieve(self,
                     query_text: str,
                     source_types: tuple[str, ...] | list[str] | None = None,
                     top_k: int | None = None,
                     score_threshold: float | None = None,
                     provider_id: str | None = None) -> list[KnowledgeChunk]:
    """
    Goal: retrieve Top-K by vector, filter by threshold, and return chunks with provenance and
          similarity
    Args:
        query_text: the user's question
        source_types: metadata filter, e.g. ("faq",); None means no filtering
        top_k / score_threshold: override the defaults (used during threshold calibration)
        provider_id: the caller's provider id, stamped on each chunk so traces keep the origins
            apart
    Returns: list[KnowledgeChunk] sorted by similarity, highest first; empty on a miss
    Raises: KnowledgeUnavailableError when the retrieval stack is unavailable, for the caller to
        degrade on
    """
    query_text = (query_text or "").strip()
    if not query_text:
      return []

    effective_top_k = top_k if top_k is not None else self._top_k
    # The threshold is resolved per question, not per retriever: the two calibrated values are on
    # the same cosine scale but were measured on different query languages. An explicit override —
    # per call, or per instance, which is how `calibrate` disarms the gate — still wins over both.
    explicit = score_threshold if score_threshold is not None else self._score_threshold
    effective_threshold = explicit if explicit is not None else resolve_vector_threshold(query_text)

    filters = {"source_type": list(source_types)} if source_types else None

    # When the graph is on, the whole retrieval is delegated to LangGraph. This is the only place
    # the two paths diverge — the Provider and everything above it are unaware, because the graph
    # is an implementation detail rather than an interface.
    if settings.knowledge_graph_enabled:
      return await self._retrieve_via_graph(
        query_text, filters, effective_top_k, effective_threshold, provider_id)

    # An empty index is not a miss. Returning [] here makes the responder tell the user
    # "I could not find that in the merchant's knowledge base" — which is false: the knowledge
    # base has content, it simply was not indexed in this checkout. Saying something untrue is
    # worse than saying "I cannot check right now", so this takes the unavailable path instead.
    try:
      if await self._vector_client.count() == 0:
        raise VectorStoreUnavailableError(
          "vector index is empty — run `python -m business_agent.knowledge.ingest ingest --force`")
    except VectorStoreUnavailableError as error:
      logger.warning("knowledge_retrieval_unavailable filters=%s error=%s", filters, error)
      raise KnowledgeUnavailableError(str(error)) from error

    try:
      embedded = await get_embedding_backend().embed_query(query_text)
      # Only the bge_m3 backend produces sparse vectors. If one is given we run hybrid search;
      # otherwise this degrades to dense only. This line is the sole fork between the two paths.
      # With rerank on, widen recall: vector search is responsible for finding everything, rerank
      # for ordering it correctly. Retrieving only Top-K and then reranking lets the vector score
      # decide the candidate set, leaving rerank to choose among its mistakes.
      recall_k = settings.rerank_candidates if self._rerank_enabled else effective_top_k

      async def search(embedding) -> list[VectorMatch]:
        return await self._vector_client.query(
          vector=embedding.dense[0],
          top_k=recall_k,
          filters=filters,
          sparse_vector=embedding.sparse[0] if embedding.has_sparse else None,
        )

      matches: list[VectorMatch] = await search(embedded)
      hyde_used = False

      if self._hyde_enabled:
        # Second route: have the LLM write a hypothetical answer in the register of the documents,
        # then retrieve with that. The point is to cross the wording gap between a spoken question
        # and a written clause.
        try:
          hypothetical = await generate_hypothetical_answer(query_text)
          hyde_embedded = await get_embedding_backend().embed_query(hypothetical)
          hyde_matches = await search(hyde_embedded)
          # RRF fuses the two routes by rank. By rank rather than by score, because the two score
          # distributions differ (HyDE text is longer and more formal, so its scores run high), and
          # rank-based fusion is immune to that difference in scale.
          matches = rrf_merge(
            [(matches, 1.0), (hyde_matches, settings.hyde_weight)],
            key=lambda match: match.id,
            max_results=recall_k,
          )
          hyde_used = True
        except (HydeUnavailableError, EmbeddingUnavailableError) as error:
          # HyDE improves recall; if it fails we fall back to the single route rather than failing
          # the whole turn.
          logger.warning("hyde_unavailable falling back to single-path retrieval: %s", error)
    except (EmbeddingUnavailableError, VectorStoreUnavailableError) as error:
      logger.warning("knowledge_retrieval_unavailable filters=%s error=%s", filters, error)
      raise KnowledgeUnavailableError(str(error)) from error

    scoring = "vector"
    if self._rerank_enabled and matches:
      try:
        # Rerank: the meaning of the score changes from "does this look similar" to "can this
        # answer the question". Measured, the two part company whenever surface phrasing is
        # similar — "do you take cash on delivery" scored 0.79 by vector (which reads as a strong
        # hit) and 0.17 by rerank.
        rerank_scores = await rerank(query_text, [match.document for match in matches])
        ranked = sorted(zip(matches, rerank_scores), key=lambda pair: pair[1], reverse=True)
        keep = cliff_cutoff(
          [score for _, score in ranked],
          score_min=settings.rerank_score_min,
          max_top_k=effective_top_k,
        )
        matches = [match for match, _ in ranked[:keep]]
        # Write the rerank score back onto match.score: the threshold check, the trace rows and
        # the logs downstream all read that field. Converting once here keeps two different score
        # semantics from coexisting downstream.
        for match, (_, score) in zip(matches, ranked[:keep]):
          match.score = score
        rejected_pairs = [(match, score) for match, score in ranked[keep:]]
        scoring = "rerank"
      except RerankUnavailableError as error:
        # Rerank improves precision, and losing it should not leave the user without an answer —
        # fall back to the vector score and the vector threshold. This degradation differs from
        # "the vector store is unavailable": that one must fall back to a human, this one can carry
        # on.
        logger.warning("rerank_unavailable falling back to vector score: %s", error)
        matches = [match for match in matches if match.score >= effective_threshold][:effective_top_k]
        rejected_pairs = []
    else:
      rejected_pairs = [(match, match.score) for match in matches if match.score < effective_threshold]
      matches = [match for match in matches if match.score >= effective_threshold][:effective_top_k]

    chunks = [_to_chunk(match, provider_id) for match in matches]
    chunks.sort(key=lambda chunk: chunk.score or 0.0, reverse=True)

    # Candidates rejected by the threshold go into the log too: on a miss, "how far short of the
    # threshold was it?" is the single most useful number when tuning. These do not reach the
    # retrieval_traces table (which only records chunks that made it to the responder), so the
    # log is their only home.
    rejected = [{"chunk_id": match.id, "score": round(score, 4)} for match, score in rejected_pairs[:5]]

    # Record hit chunk ids and similarities internally so answers stay traceable (spec 5.2 / 5.3)
    logger.info(
      "knowledge_retrieval query=%r filters=%s top_k=%s scoring=%s hyde=%s threshold=%s hits=%s dropped=%s traces=%s rejected=%s",
      query_text, filters, effective_top_k, scoring, hyde_used,
      settings.rerank_score_min if scoring == "rerank" else effective_threshold,
      len(chunks), len(rejected),
      [chunk.trace() for chunk in chunks],
      rejected,
    )
    return chunks


  async def _retrieve_via_graph(self, query_text, filters, top_k, threshold, provider_id):
    """
    Goal: run retrieval through the LangGraph orchestration. Produces the same list[KnowledgeChunk]
          as the functional path.
    Raises: KnowledgeUnavailableError when the graph reports unavailable, so callers degrade as
            before
    """
    from business_agent.knowledge.graph import OUTCOME_UNAVAILABLE, get_knowledge_graph

    source_types = (filters or {}).get("source_type")
    state = await get_knowledge_graph().run(query_text, source_types, top_k, threshold)
    if state.get("outcome") == OUTCOME_UNAVAILABLE:
      error = state.get("error", "knowledge graph reported unavailable")
      logger.warning("knowledge_retrieval_unavailable filters=%s error=%s", filters, error)
      raise KnowledgeUnavailableError(error)

    chunks = [_to_chunk(match, provider_id) for match in (state.get("selected") or [])]
    # `rejected` mirrors the functional path: how far a threshold-rejected candidate fell short is
    # the only data there is when tuning the threshold, and the comment in knowledge_repository
    # points people at this log line explicitly. Dropping it on the graph path would break that
    # promise.
    selected_ids = {chunk.chunk_id for chunk in chunks}
    rejected = [
      {"chunk_id": match.id, "score": round(match.score, 4)}
      for match in (state.get("matches_fused") or []) if match.id not in selected_ids
    ][:5]
    logger.info(
      "knowledge_retrieval query=%r filters=%s top_k=%s scoring=graph outcome=%s hits=%s traces=%s rejected=%s",
      query_text, filters, top_k, state.get("outcome"), len(chunks),
      [chunk.trace() for chunk in chunks], rejected,
    )
    return chunks


def _to_chunk(match: VectorMatch, provider_id: str | None = None) -> KnowledgeChunk:
  """
  Goal: VectorMatch -> KnowledgeChunk. The vector store's own shapes stop here.
  Args:
      match
      provider_id: the id of the provider that returned it
  Returns: KnowledgeChunk
  """
  metadata = match.metadata or {}
  return KnowledgeChunk(
    content=match.document,
    chunk_id=str(metadata.get("chunk_id") or match.id),
    source_id=str(metadata.get("source_id")) if metadata.get("source_id") else None,
    source_type=str(metadata.get("source_type")) if metadata.get("source_type") else None,
    source_title=str(metadata.get("title")) if metadata.get("title") else metadata.get("source_name"),
    position=int(metadata["position"]) if metadata.get("position") is not None else None,
    score=match.score,
    provider_id=provider_id,
  )


class VectorKnowledgeProvider(Provider):
  """
  Goal: the shared implementation for every vector-backed provider.
        A subclass only declares provider_id and source_types (its metadata filter).
  """
  source_types: tuple[str, ...] = ()

  def __init__(self, retriever: KnowledgeRetriever | None = None):
    self._retriever = retriever or KnowledgeRetriever()

  async def retrival(self, state: DialogueState) -> list[KnowledgeChunk]:
    """
    Goal: run a vector search using this turn's user question
    Args:
        state: the dialogue state
    Returns: list[KnowledgeChunk]; empty on a miss
    Raises: KnowledgeUnavailableError
    """
    query_text = self._build_query_text(state)
    # provider_id is passed down so chunks come back knowing their origin, which is what the
    # responder writes traces by
    return await self._retriever.retrieve(
      query_text, source_types=self.source_types, provider_id=self.provider_id)

  @staticmethod
  def _build_query_text(state: DialogueState) -> str:
    pending_turn = state.pending_turn
    if pending_turn is None or pending_turn.user_message is None:
      return ""
    return ChatHistoryBuilder.build_user_message_str(pending_turn.user_message)


async def main_test():
  retriever = KnowledgeRetriever()
  # Two answerable questions plus one the corpus deliberately cannot answer, so a run exercises
  # both the hit and the miss path. The Chinese probe is kept on purpose: the corpus is English and
  # BGE-M3 is multilingual, so it is the one line here that exercises cross-lingual retrieval.
  for question in ("What day does the 7-day no-questions-asked window start from",
                   "How long until the money is back on my card",
                   "退货运费谁承担",
                   "What is the weather on Mars today"):
    chunks = await retriever.retrieve(question)
    print(f"\nQ: {question}  hits={len(chunks)}")
    for chunk in chunks:
      print(f"  {chunk.score:.4f}  {chunk.chunk_id}  {chunk.source_title}")


if __name__ == '__main__':
  logging.basicConfig(level=logging.INFO)
  asyncio.run(main_test())
