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
    effective_threshold = score_threshold if score_threshold is not None else self._score_threshold

    filters = {"source_type": list(source_types)} if source_types else None

    # 图开启时把整条检索委托给 LangGraph。分歧只在这一处，
    # Provider 与其以上完全不感知——图是实现细节不是接口。
    if settings.knowledge_graph_enabled:
      return await self._retrieve_via_graph(
        query_text, filters, effective_top_k, effective_threshold, provider_id)

    try:
      embedded = await get_embedding_backend().embed_query(query_text)
      # sparse 只有 bge_m3 后端产出；给了就走混合检索，没给就退化为纯 dense。
      # 这一句是 dense-only 与 hybrid 两条路唯一的分叉点。
      # 开了 rerank 就把召回加宽：向量检索负责「捞得全」，rerank 负责「排得准」。
      # 只捞 Top-K 再重排等于让向量分决定了候选集，rerank 只能在它的错误里挑。
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
        # 第二路：先让 LLM 写一段书面语气的假设性答案，用它再检索一次。
        # 目的是跨越「口语提问」与「书面条款」之间的措辞鸿沟。
        try:
          hypothetical = await generate_hypothetical_answer(query_text)
          hyde_embedded = await get_embedding_backend().embed_query(hypothetical)
          hyde_matches = await search(hyde_embedded)
          # RRF 按名次融合两路。按名次而不是按分数，是因为两路的分数分布不同
          # （HyDE 文本更长更书面，整体偏高），按名次天然免疫这种尺度差异。
          matches = rrf_merge(
            [(matches, 1.0), (hyde_matches, settings.hyde_weight)],
            key=lambda match: match.id,
            max_results=recall_k,
          )
          hyde_used = True
        except (HydeUnavailableError, EmbeddingUnavailableError) as error:
          # HyDE 是提升召回的增强项，挂了退回单路检索，不让整轮失败。
          logger.warning("hyde_unavailable falling back to single-path retrieval: %s", error)
    except (EmbeddingUnavailableError, VectorStoreUnavailableError) as error:
      logger.warning("knowledge_retrieval_unavailable filters=%s error=%s", filters, error)
      raise KnowledgeUnavailableError(str(error)) from error

    scoring = "vector"
    if self._rerank_enabled and matches:
      try:
        # 重排：分数语义从「像不像」换成「能不能回答」。实测两者在表面句式相似时
        # 会分道扬镳——「支持货到付款吗」的向量分 0.79（像强命中），rerank 0.17。
        rerank_scores = await rerank(query_text, [match.document for match in matches])
        ranked = sorted(zip(matches, rerank_scores), key=lambda pair: pair[1], reverse=True)
        keep = cliff_cutoff(
          [score for _, score in ranked],
          score_min=settings.rerank_score_min,
          max_top_k=effective_top_k,
        )
        matches = [match for match, _ in ranked[:keep]]
        # 把 rerank 分写回 match.score：下游的阈值判定、溯源落库、日志用的都是它，
        # 换算在这里一次完成，不让两套分数语义同时存在于下游。
        for match, (_, score) in zip(matches, ranked[:keep]):
          match.score = score
        rejected_pairs = [(match, score) for match, score in ranked[keep:]]
        scoring = "rerank"
      except RerankUnavailableError as error:
        # 重排是提升精度的环节，它挂了不该让用户拿不到答案——退回向量分与向量阈值。
        # 这条降级与「向量库不可用」不同：那个必须兜底转人工，这个可以继续。
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
    Goal: 走 LangGraph 编排的检索。与函数式那条产出相同的 list[KnowledgeChunk]。
    Raises: KnowledgeUnavailableError 图判定为 unavailable 时抛出，让上层照旧降级
    """
    from business_agent.knowledge.graph import OUTCOME_UNAVAILABLE, get_knowledge_graph

    source_types = (filters or {}).get("source_type")
    state = await get_knowledge_graph().run(query_text, source_types)
    if state.get("outcome") == OUTCOME_UNAVAILABLE:
      error = state.get("error", "knowledge graph reported unavailable")
      logger.warning("knowledge_retrieval_unavailable filters=%s error=%s", filters, error)
      raise KnowledgeUnavailableError(error)

    chunks = [_to_chunk(match, provider_id) for match in (state.get("selected") or [])]
    # rejected 与函数式那条对齐：被阈值挡掉的候选「差多少才够」是调阈值时唯一的数据来源，
    # knowledge_repository 的注释也明写着来这行日志查。图路径漏掉它，那条承诺就不成立了。
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
  for question in ("七天无理由退货怎么算时间", "退款多久到账", "帮我看看今天火星的天气"):
    chunks = await retriever.retrieve(question)
    print(f"\nQ: {question}  hits={len(chunks)}")
    for chunk in chunks:
      print(f"  {chunk.score:.4f}  {chunk.chunk_id}  {chunk.source_title}")


if __name__ == '__main__':
  logging.basicConfig(level=logging.INFO)
  asyncio.run(main_test())
