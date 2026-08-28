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
from business_agent.infrastructure.llm_client import EmbeddingUnavailableError, embed_query
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
    self._score_threshold = score_threshold if score_threshold is not None else settings.knowledge_score_threshold

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
    effective_threshold = score_threshold if score_threshold is not None else self._score_threshold

    filters = {"source_type": list(source_types)} if source_types else None

    try:
      vector = await embed_query(query_text)
      matches: list[VectorMatch] = await self._vector_client.query(
        vector=vector,
        top_k=effective_top_k,
        filters=filters,
      )
    except (EmbeddingUnavailableError, VectorStoreUnavailableError) as error:
      logger.warning("knowledge_retrieval_unavailable filters=%s error=%s", filters, error)
      raise KnowledgeUnavailableError(str(error)) from error

    chunks = [_to_chunk(match, provider_id) for match in matches if match.score >= effective_threshold]
    chunks.sort(key=lambda chunk: chunk.score or 0.0, reverse=True)

    # Candidates rejected by the threshold go into the log too: on a miss, "how far short of the
    # threshold was it?" is the single most useful number when tuning. These do not reach the
    # retrieval_traces table (which only records chunks that made it to the responder), so the
    # log is their only home.
    rejected = [
      {"chunk_id": match.id, "score": round(match.score, 4)}
      for match in matches if match.score < effective_threshold
    ]

    # Record hit chunk ids and similarities internally so answers stay traceable (spec 5.2 / 5.3)
    logger.info(
      "knowledge_retrieval query=%r filters=%s top_k=%s threshold=%s hits=%s dropped=%s traces=%s rejected=%s",
      query_text, filters, effective_top_k, effective_threshold,
      len(chunks), len(rejected),
      [chunk.trace() for chunk in chunks],
      rejected,
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
  for question in ("七天无理由退货怎么算时间", "退款多久到账", "帮我看看今天火星的天气"):
    chunks = await retriever.retrieve(question)
    print(f"\nQ: {question}  hits={len(chunks)}")
    for chunk in chunks:
      print(f"  {chunk.score:.4f}  {chunk.chunk_id}  {chunk.source_title}")


if __name__ == '__main__':
  logging.basicConfig(level=logging.INFO)
  asyncio.run(main_test())
