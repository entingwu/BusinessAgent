"""
检索溯源落库

规范 3.1.2 要求「内部记录命中的分片 ID 与相似度」，5.2 要求「基于文档知识的回复可溯源到
具体分片」。日志能满足前者，但日志会滚掉、也没法按 turn_id 查回来，所以命中结果同时写进
retrieval_traces 表（表结构与理由见 repository/knowledge_repository.py）。

三条硬规矩：

1. **落库失败不能把整轮对话搞崩**。这是对话主链路，溯源是审计数据不是业务数据，
   写不进去就降级成一条 warning 日志，回复照常返回。
2. **用自己的 session**。DialogueStateService 的 session 承载的是 DialogueState 的写入，
   审计写入不该和它共用事务——审计写挂了不能把对话状态一起回滚掉。
3. **不改变未命中与降级路径「不调用 LLM」的性质**。这里只有一次 INSERT，
   兜底话术仍然是常量，机制上依旧无法编造。
"""
import logging
from typing import Any, Iterable

from business_agent.config.settings import settings
from business_agent.domain.state import DialogueState
from business_agent.infrastructure import db_client
from business_agent.infrastructure.llm_client import embedding_model_name
from business_agent.knowledge.provider.provider import KnowledgeChunk
from business_agent.repository.knowledge_repository import KnowledgeRepository, ensure_tables

logger = logging.getLogger(__name__)

# 本轮结局
OUTCOME_ANSWERED = "answered"        # 基于命中分片作答
OUTCOME_NO_HIT = "no_hit"            # 未命中或全部低于阈值，走兜底话术
OUTCOME_UNAVAILABLE = "unavailable"  # 向量库 / Embedding 不可用，走降级话术

# 分片没进提示词的原因
DROP_TOP_K = "top_k"                 # 相似度排在 Top-K 之外
DROP_CONTEXT_BUDGET = "context_budget"  # 上下文 token 预算不够，按相似度从低到高截断

_tables_ready = False


class KnowledgeTraceRecorder:
  """
  Goal: 把一轮检索的溯源信息写进 retrieval_traces
  """

  async def record(self,
                   state: DialogueState,
                   *,
                   outcome: str,
                   provider_ids: Iterable[str],
                   selected: Iterable[KnowledgeChunk] = (),
                   dropped: Iterable[tuple[KnowledgeChunk, str]] = (),
                   note: str | None = None) -> int:
    """
    Goal: 记录一轮检索的溯源证据。任何异常都吞掉，只留日志。
    Args:
        state: 对话状态，关联键（sender_id / session_id / turn_id / message_id）从这里取
        outcome: OUTCOME_ANSWERED / OUTCOME_NO_HIT / OUTCOME_UNAVAILABLE
        provider_ids: 本轮参与检索的 Provider ID
        selected: 真正进了提示词的分片
        dropped: (分片, 丢弃原因) 列表——Top-K 之外或上下文预算截断掉的
        note: 补充说明，降级时放错误摘要
    Returns: int 写入行数；未启用或失败时返回 0
    """
    if not settings.knowledge_trace_enabled:
      return 0

    try:
      rows = self._build_rows(
        state,
        outcome=outcome,
        provider_ids=list(provider_ids),
        selected=list(selected),
        dropped=list(dropped),
        note=note,
      )
      if not rows:
        return 0
      return await self._write(rows)
    except Exception as error:  # noqa: BLE001 - 溯源写入绝不允许影响对话
      logger.warning("retrieval_trace_write_failed outcome=%s sender_id=%s error=%s",
                     outcome, getattr(state, "sender_id", None), error)
      return 0

  # ---------------- 内部 ----------------

  def _build_rows(self,
                  state: DialogueState,
                  *,
                  outcome: str,
                  provider_ids: list[str],
                  selected: list[KnowledgeChunk],
                  dropped: list[tuple[KnowledgeChunk, str]],
                  note: str | None) -> list[dict[str, Any]]:
    """
    Goal: 组装待写入的行。命中一片一行；未命中与降级按 Provider 各一行（chunk_id 留空）。
    Args:
        见 record
    Returns: list[dict]
    """
    session = state.current_session()
    pending_turn = state.pending_turn
    user_message = pending_turn.user_message if pending_turn is not None else None

    base: dict[str, Any] = {
      "sender_id": state.sender_id,
      "session_id": session.session_id if session is not None else "",
      "turn_id": pending_turn.turn_id if pending_turn is not None else "",
      "message_id": getattr(user_message, "message_id", "") or "",
      "outcome": outcome,
      "threshold": settings.knowledge_score_threshold,
      "embedding_model": embedding_model_name(),
    }

    rows: list[dict[str, Any]] = []

    for chunk in selected:
      rows.append({**base, **self._chunk_fields(chunk), "selected": True, "drop_reason": None, "note": None})

    for chunk, reason in dropped:
      rows.append({**base, **self._chunk_fields(chunk), "selected": False, "drop_reason": reason, "note": None})

    # 没有任何分片进来（未命中 / 降级）：按 Provider 各留一行，
    # 「这一轮为什么兜底了」同样是溯源证据
    if not rows:
      for provider_id in provider_ids or [""]:
        rows.append({
          **base,
          "provider_id": provider_id,
          "chunk_id": None,
          "source_id": None,
          "source_type": None,
          "source_title": None,
          "position": None,
          "score": None,
          "selected": False,
          "drop_reason": outcome,
          "note": note[:255] if note else None,
        })

    return rows

  @staticmethod
  def _chunk_fields(chunk: KnowledgeChunk) -> dict[str, Any]:
    return {
      "provider_id": chunk.provider_id or "",
      "chunk_id": chunk.chunk_id,
      "source_id": chunk.source_id,
      "source_type": chunk.source_type,
      "source_title": (chunk.source_title or "")[:255] or None,
      "position": chunk.position,
      "score": chunk.score,
    }

  async def _write(self, rows: list[dict[str, Any]]) -> int:
    """
    Goal: 用独立 session 写入，不与对话状态共用事务
    Args:
        rows
    Returns: int 写入行数
    """
    global _tables_ready

    if db_client.session_factory is None:
      logger.warning("retrieval_trace_skipped reason=db_not_initialized rows=%s", len(rows))
      return 0

    # 服务进程不跑入库脚本，表可能还不存在，这里按进程做一次性建表
    if not _tables_ready:
      await ensure_tables(db_client.session_engine)
      _tables_ready = True

    async with db_client.session_factory() as session:
      repository = KnowledgeRepository(session)
      written = await repository.record_retrieval_traces(rows)
      await repository.commit()
      return written
