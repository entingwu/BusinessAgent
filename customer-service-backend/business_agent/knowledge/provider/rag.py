"""
向量检索 Provider：Top-K + 相似度阈值 + metadata 过滤（规范 C.4.5 第一档）

未命中或全部低于阈值时**返回空列表**，不返回兜底话术——
兜底怎么说由 KnowledgeResponder 决定，Provider 只负责「有没有召回」。
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
  Goal: 把一句自然语言问题变成一组带溯源信息的分片
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
                     score_threshold: float | None = None) -> list[KnowledgeChunk]:
    """
    Goal: 向量检索 Top-K，按阈值过滤，返回带来源标识与相似度的分片
    Args:
        query_text: 用户提问
        source_types: metadata 过滤，例如 ("faq",)；None 表示不过滤
        top_k / score_threshold: 覆盖默认参数（阈值校准时用）
    Returns: list[KnowledgeChunk] 按相似度从高到低；未命中返回空列表
    Raises: KnowledgeUnavailableError 检索链路不可用时抛出，由上层降级
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

    chunks = [_to_chunk(match) for match in matches if match.score >= effective_threshold]
    chunks.sort(key=lambda chunk: chunk.score or 0.0, reverse=True)

    # 内部记录命中的分片 ID 与相似度，回复可溯源（规范 5.2 / 5.3）
    logger.info(
      "knowledge_retrieval query=%r filters=%s top_k=%s threshold=%s hits=%s dropped=%s traces=%s",
      query_text, filters, effective_top_k, effective_threshold,
      len(chunks), len(matches) - len(chunks),
      [chunk.trace() for chunk in chunks],
    )
    return chunks


def _to_chunk(match: VectorMatch) -> KnowledgeChunk:
  """
  Goal: VectorMatch -> KnowledgeChunk，向量库的结构到此为止
  Args:
      match
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
  )


class VectorKnowledgeProvider(Provider):
  """
  Goal: 所有基于向量库的 Provider 的公共实现。
        子类只需声明 provider_id 与 source_types（metadata 过滤条件）。
  """
  source_types: tuple[str, ...] = ()

  def __init__(self, retriever: KnowledgeRetriever | None = None):
    self._retriever = retriever or KnowledgeRetriever()

  async def retrival(self, state: DialogueState) -> list[KnowledgeChunk]:
    """
    Goal: 用本轮用户提问做向量检索
    Args:
        state: 对话状态
    Returns: list[KnowledgeChunk]；未命中返回空列表
    Raises: KnowledgeUnavailableError
    """
    query_text = self._build_query_text(state)
    return await self._retriever.retrieve(query_text, source_types=self.source_types)

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
