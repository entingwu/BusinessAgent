"""
LangGraph orchestration for the knowledge track — retrieval through to answering, as one graph.

## Why this path is worth drawing as a graph

LangGraph earns its keep by expressing **branching and joining**. For a straight A->B->C, plain
function calls read better and a StateGraph only adds reading cost. This path has two real
branches:

1. **Multi-route retrieval** (`node_multi_search` branches -> `node_join` merges): one route for
   the original question, one for the HyDE hypothetical answer, fused with RRF.
2. **Three-way split** (the conditional edges after `node_threshold`): hit / miss / unavailable.

**The second one is what this graph is actually for.**

## The graph turns a safety constraint into a topological property

Spec 5.2 and acceptance 7.1-3 require that a miss must not fabricate. In a functional
implementation that guarantee is one `if not selected: return` in the middle of
`KnowledgeResponder.respond()` — true, but you have to read the whole function and confirm that
`chain.ainvoke` comes after that return before you can believe it. The second audit round judged
"traceable" to be a stub for exactly this reason: something real was there, but invisible in the code.

Drawn as a graph, `node_fallback` and `node_degrade` **have no topological path to
`node_answer`**. "The fallback cannot call an LLM" stops being a property you verify by reading
and becomes one you verify by looking at the edges.

> **Porting discipline**: `node_fallback` and `node_degrade` may only return constant text.
> Anyone adding a "let the model polish the fallback wording" call inside those two nodes is
> breaking the red line — that is precisely why the `no_relevant_answer` branch was rejected
> (it goes through action_response's rephrase mode, which calls the LLM).

The node classes are organised after knowledge_base/atguigu's main_graph2.py:
`_init_nodes` / `_register_nodes` / `_setup_routes` plus lazy compilation.
The difference is that this project is async throughout, so nodes are `async def` and execution
goes through `ainvoke`.
"""
import logging
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from business_agent.config.settings import settings
from business_agent.domain.messages import BotMessage
from business_agent.infrastructure.embedding import EmbeddingUnavailableError, get_embedding_backend
from business_agent.infrastructure.reranker import RerankUnavailableError, cliff_cutoff, rerank
from business_agent.infrastructure.vector_client import VectorStoreUnavailableError, get_vector_client
from business_agent.knowledge.fusion import rrf_merge
from business_agent.knowledge.hyde import HydeUnavailableError, generate_hypothetical_answer
from business_agent.knowledge.thresholds import resolve_vector_threshold

logger = logging.getLogger(__name__)

OUTCOME_ANSWERED = "answered"
OUTCOME_NO_HIT = "no_hit"
OUTCOME_UNAVAILABLE = "unavailable"


class QueryGraphState(TypedDict, total=False):
  """
  Goal: the graph's state, and the only way nodes communicate. Each node reads only the keys it
        needs and writes only the keys it produces; LangGraph merges them. total=False lets a node
        return just the keys it wrote, which is what makes a node testable on its own.
  """
  question: str
  source_types: list[str]
  # Per-call overrides. Both default to the configured values; `calibrate` is the only caller that
  # sets them, and it sets score_threshold to -1.0 to disarm the gate so the raw distribution is
  # visible. Reading settings directly in a node instead of these keys is what made the override
  # silently do nothing on the graph path — the calibration then measured a distribution the
  # threshold had already censored, and no verdict it produced could ever report an overlap.
  top_k: int
  score_threshold: float
  hyde_doc: str
  matches_direct: list[Any]
  matches_hyde: list[Any]
  matches_fused: list[Any]
  selected: list[Any]
  outcome: str
  scoring_degraded: bool
  error: str
  answer_chunks: list[Any]


def _top_k(state: QueryGraphState) -> int:
  """Goal: the effective Top-K — the per-call override if one was given, else the configured one."""
  value = state.get("top_k")
  return settings.knowledge_top_k if value is None else value


def _score_threshold(state: QueryGraphState) -> float:
  """
  Goal: the effective vector threshold. Only the degraded path reads it; the normal path is gated
        by rerank_score_min, which is on a different scale (see node_rerank). An explicit per-call
        override wins, so calibration can disarm the gate; otherwise the shared resolver picks the
        value calibrated for the script the question is written in.
  """
  value = state.get("score_threshold")
  if value is not None:
    return value
  return resolve_vector_threshold(state.get("question") or "")


class KnowledgeQueryGraph:
  """
  Goal: orchestrate retrieval through to answering. Transparent above the provider layer —
        a caller sees "ask a question, get chunks and an outcome" and never knows there is a
        graph.
  """

  def __init__(self) -> None:
    self.workflow = StateGraph(QueryGraphState)
    self._register_nodes()
    self._setup_routes()
    self._compiled: Any | None = None

  # ---------------- nodes ----------------

  async def node_entry(self, state: QueryGraphState) -> QueryGraphState:
    """Entry point: normalise the input. Retrieves nothing."""
    return {"question": (state.get("question") or "").strip()}

  async def node_hyde(self, state: QueryGraphState) -> QueryGraphState:
    """
    Generate a hypothetical answer. Returns an empty string when disabled or on failure, which
    tells the downstream node to skip the second route and degrade to single-route retrieval
    rather than failing the turn — HyDE is an enhancement, not a requirement.
    """
    if not settings.hyde_enabled:
      return {"hyde_doc": ""}
    try:
      return {"hyde_doc": await generate_hypothetical_answer(state["question"])}
    except HydeUnavailableError as error:
      logger.warning("hyde_unavailable, single-path retrieval: %s", error)
      return {"hyde_doc": ""}

  async def _search(self, text: str, source_types: list[str] | None, top_k: int) -> list[Any]:
    embedded = await get_embedding_backend().embed_query(text)
    return await get_vector_client().query(
      vector=embedded.dense[0],
      top_k=settings.rerank_candidates if settings.rerank_enabled else top_k,
      filters={"source_type": source_types} if source_types else None,
      sparse_vector=embedded.sparse[0] if embedded.has_sparse else None,
    )

  async def node_search_direct(self, state: QueryGraphState) -> QueryGraphState:
    """First route: retrieve using the original question."""
    try:
      # Same reasoning as the function path: an empty index is an outage, not a miss.
      # "Not in our knowledge base" would be a false statement about a knowledge base that has
      # content — it just is not indexed here.
      if await get_vector_client().count() == 0:
        raise VectorStoreUnavailableError(
          "vector index is empty — run `python -m business_agent.knowledge.ingest ingest --force`")
      return {"matches_direct": await self._search(
        state["question"], state.get("source_types"), _top_k(state))}
    except (EmbeddingUnavailableError, VectorStoreUnavailableError) as error:
      # If this route fails the whole chain is unavailable — there is no evidence to answer from.
      return {"matches_direct": [], "error": str(error)}

  async def node_search_hyde(self, state: QueryGraphState) -> QueryGraphState:
    """Second route: retrieve using the hypothetical answer. Skipped when there is no hyde_doc."""
    hyde_doc = state.get("hyde_doc") or ""
    if not hyde_doc:
      return {"matches_hyde": []}
    try:
      return {"matches_hyde": await self._search(
        hyde_doc, state.get("source_types"), _top_k(state))}
    except (EmbeddingUnavailableError, VectorStoreUnavailableError) as error:
      # A failure here does not affect the first route, so no error is written — degrade, not fail.
      logger.warning("hyde_search_failed, keeping direct path only: %s", error)
      return {"matches_hyde": []}

  async def node_rrf(self, state: QueryGraphState) -> QueryGraphState:
    """
    Fuse the two routes with RRF. By rank rather than by score: the two routes have different
    score distributions (HyDE text is longer and more formal), and ranks are immune to that
    difference in scale.
    """
    direct, hyde = state.get("matches_direct") or [], state.get("matches_hyde") or []
    if not hyde:
      return {"matches_fused": direct}
    return {"matches_fused": rrf_merge(
      [(direct, 1.0), (hyde, settings.hyde_weight)],
      key=lambda match: match.id,
      max_results=settings.rerank_candidates,
    )}

  async def node_rerank(self, state: QueryGraphState) -> QueryGraphState:
    """
    Rerank: the score changes meaning from "how similar" to "can this answer the question".
    If the rerank service is down, fall back to vector scores and carry on — less precise, but
    still grounded, which is a different situation from the vector store being unavailable.
    """
    matches = state.get("matches_fused") or []
    if not matches or not settings.rerank_enabled:
      return {"matches_fused": matches}
    try:
      scores = await rerank(state["question"], [match.document for match in matches])
    except RerankUnavailableError as error:
      # Falling back to vector scores must also fall back to the **vector threshold**. Falling
      # back on the score alone would leave the downstream gate comparing rerank_score_min (order
      # 0.155) against vector scores (order 0.6-0.8): the threshold stops filtering, almost every
      # question is judged a hit, and "the fallback cannot fabricate" degrades from a topological
      # property into a matter of prompt compliance.
      #
      # The mirror-image mistake is just as easy to make and was made here on 2026-08-28: setting
      # KNOWLEDGE_SCORE_THRESHOLD from a `calibrate` run that reported rerank-scale scores. The
      # two scales differ by roughly 4x in either direction.
      logger.warning("rerank_unavailable, falling back to vector score AND vector threshold: %s", error)
      return {"matches_fused": matches, "scoring_degraded": True}
    ranked = sorted(zip(matches, scores), key=lambda pair: pair[1], reverse=True)
    for match, score in ranked:
      match.score = score
    return {"matches_fused": [match for match, _ in ranked]}

  async def node_threshold(self, state: QueryGraphState) -> QueryGraphState:
    """
    Threshold check plus cliff cutoff. **This node only decides; it produces no wording.**
    Wording belongs to the three nodes downstream, and keeping the decision separate from the
    phrasing is what makes this graph readable.
    """
    if state.get("error"):
      return {"selected": [], "outcome": OUTCOME_UNAVAILABLE}
    matches = state.get("matches_fused") or []
    # scoring_degraded means rerank is down and the scores are vector scores, so the vector
    # threshold is the one that must be applied
    top_k = _top_k(state)
    if settings.rerank_enabled and not state.get("scoring_degraded"):
      keep = cliff_cutoff([match.score for match in matches],
                          score_min=settings.rerank_score_min,
                          max_top_k=top_k)
    else:
      threshold = _score_threshold(state)
      passed = [match for match in matches if match.score >= threshold]
      keep = min(len(passed), top_k)
      matches = passed
    selected = matches[:keep]
    return {"selected": selected, "outcome": OUTCOME_ANSWERED if selected else OUTCOME_NO_HIT}

  def route_by_outcome(self, state: QueryGraphState) -> str:
    """
    Goal: the three-way split. **This is the most important edge in the graph** — it turns "the
          fallback does not call an LLM" from an if buried inside a function into a property you
          can see in the topology.
    """
    outcome = state.get("outcome")
    if outcome == OUTCOME_UNAVAILABLE:
      return "node_degrade"
    if outcome == OUTCOME_NO_HIT:
      return "node_fallback"
    return "node_answer"

  async def node_answer(self, state: QueryGraphState) -> QueryGraphState:
    """Hit: hand the selected chunks up for generation. The graph itself never calls an LLM;
    generation stays in the responder."""
    return {"answer_chunks": state.get("selected") or []}

  async def node_fallback(self, state: QueryGraphState) -> QueryGraphState:
    """Miss. **Constant semantics only — never call an LLM here.**"""
    return {"answer_chunks": []}

  async def node_degrade(self, state: QueryGraphState) -> QueryGraphState:
    """Chain unavailable. **Equally, never call an LLM here.**"""
    return {"answer_chunks": []}

  # ---------------- assembly ----------------

  def _register_nodes(self) -> None:
    self.workflow.add_node("node_entry", self.node_entry)
    self.workflow.add_node("node_hyde", self.node_hyde)
    # Virtual nodes: they express the branch and the join and change no state. Same shape as
    # atguigu's.
    self.workflow.add_node("node_multi_search", lambda state: {})
    self.workflow.add_node("node_search_direct", self.node_search_direct)
    self.workflow.add_node("node_search_hyde", self.node_search_hyde)
    self.workflow.add_node("node_join", lambda state: {})
    self.workflow.add_node("node_rrf", self.node_rrf)
    self.workflow.add_node("node_rerank", self.node_rerank)
    self.workflow.add_node("node_threshold", self.node_threshold)
    self.workflow.add_node("node_answer", self.node_answer)
    self.workflow.add_node("node_fallback", self.node_fallback)
    self.workflow.add_node("node_degrade", self.node_degrade)

  def _setup_routes(self) -> None:
    self.workflow.set_entry_point("node_entry")
    self.workflow.add_edge("node_entry", "node_hyde")
    self.workflow.add_edge("node_hyde", "node_multi_search")
    # Branch: the two retrieval routes
    self.workflow.add_edge("node_multi_search", "node_search_direct")
    self.workflow.add_edge("node_multi_search", "node_search_hyde")
    # Join
    self.workflow.add_edge("node_search_direct", "node_join")
    self.workflow.add_edge("node_search_hyde", "node_join")
    self.workflow.add_edge("node_join", "node_rrf")
    self.workflow.add_edge("node_rrf", "node_rerank")
    self.workflow.add_edge("node_rerank", "node_threshold")
    # Three-way split: fallback and degrade have no path to node_answer
    self.workflow.add_conditional_edges("node_threshold", self.route_by_outcome, {
      "node_answer": "node_answer",
      "node_fallback": "node_fallback",
      "node_degrade": "node_degrade",
    })
    for terminal in ("node_answer", "node_fallback", "node_degrade"):
      self.workflow.add_edge(terminal, END)

  def compile(self) -> Any:
    """Lazy compilation: compiled once on first execution, reused thereafter."""
    if self._compiled is None:
      self._compiled = self.workflow.compile()
    return self._compiled

  async def run(self,
                question: str,
                source_types: list[str] | None = None,
                top_k: int | None = None,
                score_threshold: float | None = None) -> QueryGraphState:
    """
    Goal: run one retrieval.
    Args:
        top_k / score_threshold: per-call overrides, None meaning "use the configured value".
            They exist for calibration, and they must be carried in the state rather than read
            from settings inside the nodes — see the note on QueryGraphState.
    Returns: a QueryGraphState carrying outcome and selected; the caller picks its wording from
             outcome
    """
    initial: QueryGraphState = {"question": question, "source_types": source_types or []}
    if top_k is not None:
      initial["top_k"] = top_k
    if score_threshold is not None:
      initial["score_threshold"] = score_threshold
    return await self.compile().ainvoke(initial)


_graph: KnowledgeQueryGraph | None = None


def get_knowledge_graph() -> KnowledgeQueryGraph:
  """Per-process singleton: compiled once and reused."""
  global _graph
  if _graph is None:
    _graph = KnowledgeQueryGraph()
  return _graph
