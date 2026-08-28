"""
Persisting retrieval traces.

Spec 3.1.2 requires "recording hit chunk ids and similarities internally", and 5.2 requires that
"answers grounded in document knowledge trace back to specific chunks". Logs satisfy the first,
but logs roll over and cannot be queried by turn_id, so hits are also written to the
retrieval_traces table (schema and rationale in repository/knowledge_repository.py).

Three hard rules:

1. **A failed write must never break the turn.** This sits on the main conversation path, and a
   trace is audit data, not business data. If it cannot be written, degrade to a warning log and
   return the reply as normal.
2. **Use its own session.** DialogueStateService's session carries the DialogueState write, and
   an audit write has no business sharing that transaction — a failed audit write must not roll
   the dialogue state back with it.
3. **Do not change the "no LLM call" property of the miss and degraded paths.** There is a single
   INSERT here; the fallback text is still a constant, so fabrication remains mechanically
   impossible.
"""
import logging
from typing import Any, Iterable

from business_agent.config.settings import settings
from business_agent.domain.state import DialogueState
from business_agent.infrastructure.knowledge_db import get_knowledge_engine, get_knowledge_session_factory
from business_agent.infrastructure.embedding import get_embedding_backend
from business_agent.knowledge.provider.provider import KnowledgeChunk
from business_agent.repository.knowledge_repository import KnowledgeRepository, ensure_tables

logger = logging.getLogger(__name__)

# How this turn ended
OUTCOME_ANSWERED = "answered"        # answered from the retrieved chunks
OUTCOME_NO_HIT = "no_hit"            # a miss, or everything below threshold — fallback text
OUTCOME_UNAVAILABLE = "unavailable"  # vector store / embedding unavailable — degraded text

# Why a chunk did not make it into the prompt
DROP_TOP_K = "top_k"                 # similarity ranked outside Top-K
DROP_CONTEXT_BUDGET = "context_budget"  # out of context token budget; dropped lowest similarity first

_tables_ready = False


class KnowledgeTraceRecorder:
  """
  Goal: write one turn's retrieval trace into retrieval_traces
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
    Goal: record the trace evidence for one retrieval. Every exception is swallowed and only
          logged.
    Args:
        state: the dialogue state; the correlation keys (sender_id / session_id / turn_id /
            message_id) come from it
        outcome: OUTCOME_ANSWERED / OUTCOME_NO_HIT / OUTCOME_UNAVAILABLE
        provider_ids: the providers queried this turn
        selected: the chunks that actually reached the prompt
        dropped: (chunk, reason) pairs — those cut by Top-K or by the context budget
        note: free-form detail; on the degraded path it carries the error summary
    Returns: the number of rows written; 0 when disabled or on failure
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
    except Exception as error:  # noqa: BLE001 - a trace write must never affect the conversation
      logger.warning("retrieval_trace_write_failed outcome=%s sender_id=%s error=%s",
                     outcome, getattr(state, "sender_id", None), error)
      return 0

  # ---------------- internals ----------------

  def _build_rows(self,
                  state: DialogueState,
                  *,
                  outcome: str,
                  provider_ids: list[str],
                  selected: list[KnowledgeChunk],
                  dropped: list[tuple[KnowledgeChunk, str]],
                  note: str | None) -> list[dict[str, Any]]:
    """
    Goal: assemble the rows to write. One row per hit chunk; on a miss or a degraded turn, one
          row per provider with chunk_id left empty.
    Args:
        see record()
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
      # Record the threshold that actually gated this turn, not a fixed one. When rerank is on
      # the gate is rerank_score_min and the score column holds rerank scores; writing
      # knowledge_score_threshold here would put two different scales in adjacent columns
      # (e.g. "score 0.9513 / threshold 0.58") and mislead anyone reading the table later.
      "threshold": settings.rerank_score_min if settings.rerank_enabled else settings.knowledge_score_threshold,
      "embedding_model": get_embedding_backend().name,
    }

    rows: list[dict[str, Any]] = []

    for chunk in selected:
      rows.append({**base, **self._chunk_fields(chunk), "selected": True, "drop_reason": None, "note": None})

    for chunk, reason in dropped:
      rows.append({**base, **self._chunk_fields(chunk), "selected": False, "drop_reason": reason, "note": None})

    # No chunks at all (miss / degraded): keep one row per provider — "why did this turn fall
    # back?" is trace evidence just as much as a hit is
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
    Goal: write through its own session, sharing no transaction with the dialogue state
    Args:
        rows
    Returns: the number of rows written
    """
    global _tables_ready

    if get_knowledge_session_factory() is None:
      logger.warning("retrieval_trace_skipped reason=db_not_initialized rows=%s", len(rows))
      return 0

    # The server process never runs the ingest script, so the tables may not exist yet — create
    # them once per process here
    if not _tables_ready:
      await ensure_tables(get_knowledge_engine())
      _tables_ready = True

    async with get_knowledge_session_factory()() as session:
      repository = KnowledgeRepository(session)
      written = await repository.record_retrieval_traces(rows)
      await repository.commit()
      return written
