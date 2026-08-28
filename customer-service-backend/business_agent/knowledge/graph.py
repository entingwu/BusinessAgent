"""
知识轨道的 LangGraph 编排 —— 检索到作答的完整一张图。

## 为什么这条链路值得画成图

LangGraph 的价值在于表达**分叉与汇合**。如果流程是 A→B→C，普通函数调用更清楚，
套一层 StateGraph 只是增加阅读成本。这条链路有两处真实分叉：

1. **多路检索**（`node_multi_search` 分叉 → `node_join` 汇合）：原问题一路、
   HyDE 假设性答案一路，RRF 融合。
2. **三态分流**（`node_threshold` 之后的条件边）：命中 / 未命中 / 链路不可用。

**第二处才是这张图真正的价值。**

## 这张图把一条安全约束变成了拓扑性质

规范 5.2 与验收 7.1-3 要求「未命中不得编造」。在函数式实现里，这个保证是
`KnowledgeResponder.respond()` 中间的一句 `if not selected: return` ——它成立，
但要读完整个函数、确认 `chain.ainvoke` 在那个 return 之后，才能确信。审计第二轮
把「可溯源」判成桩，正是因为类似的东西藏在代码里看不出来。

画成图之后，`node_fallback` 与 `node_degrade` **在拓扑上就不通向 `node_answer`**。
「兜底路径不可能调用 LLM」从一个需要阅读验证的性质，变成一个看图就能确认的性质。

> **移植纪律**：`node_fallback` / `node_degrade` 里只允许返回常量文案。任何人想在
> 这两个节点里加一次「让模型润色一下兜底话术」的调用，都是在破坏红线——那正是当初
> 否掉 `no_relevant_answer` 分支的原因（它走 action_response 的 rephrase 模式会调 LLM）。

节点类的组织方式照搬 knowledge_base/atguigu 的 main_graph2.py：
`_init_nodes` / `_register_nodes` / `_setup_routes` + 懒编译。
差异是本项目全链路 async，节点是 `async def`，执行走 `ainvoke`。
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

logger = logging.getLogger(__name__)

OUTCOME_ANSWERED = "answered"
OUTCOME_NO_HIT = "no_hit"
OUTCOME_UNAVAILABLE = "unavailable"


class QueryGraphState(TypedDict, total=False):
  """
  Goal: 图的状态。节点之间唯一的通信方式——每个节点只读它需要的键、只写它产出的键，
        LangGraph 负责合并。total=False 让节点可以只返回自己写的那几个键，
        这是节点能独立测试的前提。
  """
  question: str
  source_types: list[str]
  hyde_doc: str
  matches_direct: list[Any]
  matches_hyde: list[Any]
  matches_fused: list[Any]
  selected: list[Any]
  outcome: str
  error: str
  answer_chunks: list[Any]


class KnowledgeQueryGraph:
  """
  Goal: 编排知识检索到作答的完整流程。对 Provider 以上透明——
        调用方只看到「问一句、拿回分片与结局」，不感知图的存在。
  """

  def __init__(self) -> None:
    self.workflow = StateGraph(QueryGraphState)
    self._register_nodes()
    self._setup_routes()
    self._compiled: Any | None = None

  # ---------------- 节点 ----------------

  async def node_entry(self, state: QueryGraphState) -> QueryGraphState:
    """入口：规整输入。不做任何检索。"""
    return {"question": (state.get("question") or "").strip()}

  async def node_hyde(self, state: QueryGraphState) -> QueryGraphState:
    """
    生成假设性答案。关闭或失败时返回空串——下游据此跳过第二路，
    退化成单路检索而不是整轮失败：HyDE 是增强项，不是必需项。
    """
    if not settings.hyde_enabled:
      return {"hyde_doc": ""}
    try:
      return {"hyde_doc": await generate_hypothetical_answer(state["question"])}
    except HydeUnavailableError as error:
      logger.warning("hyde_unavailable, single-path retrieval: %s", error)
      return {"hyde_doc": ""}

  async def _search(self, text: str, source_types: list[str] | None) -> list[Any]:
    embedded = await get_embedding_backend().embed_query(text)
    return await get_vector_client().query(
      vector=embedded.dense[0],
      top_k=settings.rerank_candidates if settings.rerank_enabled else settings.knowledge_top_k,
      filters={"source_type": source_types} if source_types else None,
      sparse_vector=embedded.sparse[0] if embedded.has_sparse else None,
    )

  async def node_search_direct(self, state: QueryGraphState) -> QueryGraphState:
    """第一路：用原问题检索。"""
    try:
      return {"matches_direct": await self._search(state["question"], state.get("source_types"))}
    except (EmbeddingUnavailableError, VectorStoreUnavailableError) as error:
      # 这一路挂了就是整条链路不可用——没有任何依据可以作答。
      return {"matches_direct": [], "error": str(error)}

  async def node_search_hyde(self, state: QueryGraphState) -> QueryGraphState:
    """第二路：用假设性答案检索。没有 hyde_doc 就跳过。"""
    hyde_doc = state.get("hyde_doc") or ""
    if not hyde_doc:
      return {"matches_hyde": []}
    try:
      return {"matches_hyde": await self._search(hyde_doc, state.get("source_types"))}
    except (EmbeddingUnavailableError, VectorStoreUnavailableError) as error:
      # 第二路挂了不影响第一路，不写 error——降级而不是失败。
      logger.warning("hyde_search_failed, keeping direct path only: %s", error)
      return {"matches_hyde": []}

  async def node_rrf(self, state: QueryGraphState) -> QueryGraphState:
    """
    RRF 融合两路。按名次而非分数——两路的分数分布不同（HyDE 文本更长更书面），
    按名次天然免疫这种尺度差异。
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
    重排：分数语义从「像不像」换成「能不能回答」。
    重排服务挂了退回向量分继续——精度下降但仍有依据，与向量库不可用不同。
    """
    matches = state.get("matches_fused") or []
    if not matches or not settings.rerank_enabled:
      return {"matches_fused": matches}
    try:
      scores = await rerank(state["question"], [match.document for match in matches])
    except RerankUnavailableError as error:
      logger.warning("rerank_unavailable, keeping vector score: %s", error)
      return {"matches_fused": matches}
    ranked = sorted(zip(matches, scores), key=lambda pair: pair[1], reverse=True)
    for match, score in ranked:
      match.score = score
    return {"matches_fused": [match for match, _ in ranked]}

  async def node_threshold(self, state: QueryGraphState) -> QueryGraphState:
    """
    阈值判定 + 断崖截断。**这个节点只做判定，不产生任何话术**——
    话术归下游三个节点，判定与措辞分开是这张图能读懂的前提。
    """
    if state.get("error"):
      return {"selected": [], "outcome": OUTCOME_UNAVAILABLE}
    matches = state.get("matches_fused") or []
    if settings.rerank_enabled:
      keep = cliff_cutoff([match.score for match in matches],
                          score_min=settings.rerank_score_min,
                          max_top_k=settings.knowledge_top_k)
    else:
      passed = [match for match in matches if match.score >= settings.knowledge_score_threshold]
      keep = min(len(passed), settings.knowledge_top_k)
      matches = passed
    selected = matches[:keep]
    return {"selected": selected, "outcome": OUTCOME_ANSWERED if selected else OUTCOME_NO_HIT}

  def route_by_outcome(self, state: QueryGraphState) -> str:
    """
    Goal: 三态分流。**这是本图最重要的一条边**——它把「兜底不调 LLM」
          从一句藏在函数中间的 if 变成了拓扑上可见的性质。
    """
    outcome = state.get("outcome")
    if outcome == OUTCOME_UNAVAILABLE:
      return "node_degrade"
    if outcome == OUTCOME_NO_HIT:
      return "node_fallback"
    return "node_answer"

  async def node_answer(self, state: QueryGraphState) -> QueryGraphState:
    """命中：把选中的分片交给上层生成。图本身不调 LLM，生成留在 responder。"""
    return {"answer_chunks": state.get("selected") or []}

  async def node_fallback(self, state: QueryGraphState) -> QueryGraphState:
    """未命中。**只允许返回常量语义，不得在此调用 LLM。**"""
    return {"answer_chunks": []}

  async def node_degrade(self, state: QueryGraphState) -> QueryGraphState:
    """链路不可用。**同样不得调用 LLM。**"""
    return {"answer_chunks": []}

  # ---------------- 装配 ----------------

  def _register_nodes(self) -> None:
    self.workflow.add_node("node_entry", self.node_entry)
    self.workflow.add_node("node_hyde", self.node_hyde)
    # 虚拟节点：只为表达分叉与汇合，不改状态。照搬 atguigu 的写法。
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
    # 分叉：两路检索并行
    self.workflow.add_edge("node_multi_search", "node_search_direct")
    self.workflow.add_edge("node_multi_search", "node_search_hyde")
    # 汇合
    self.workflow.add_edge("node_search_direct", "node_join")
    self.workflow.add_edge("node_search_hyde", "node_join")
    self.workflow.add_edge("node_join", "node_rrf")
    self.workflow.add_edge("node_rrf", "node_rerank")
    self.workflow.add_edge("node_rerank", "node_threshold")
    # 三态分流：兜底与降级在拓扑上不通向 node_answer
    self.workflow.add_conditional_edges("node_threshold", self.route_by_outcome, {
      "node_answer": "node_answer",
      "node_fallback": "node_fallback",
      "node_degrade": "node_degrade",
    })
    for terminal in ("node_answer", "node_fallback", "node_degrade"):
      self.workflow.add_edge(terminal, END)

  def compile(self) -> Any:
    """懒编译：首次执行时编译一次，之后复用。"""
    if self._compiled is None:
      self._compiled = self.workflow.compile()
    return self._compiled

  async def run(self, question: str, source_types: list[str] | None = None) -> QueryGraphState:
    """
    Goal: 跑一次检索。
    Returns: QueryGraphState 含 outcome 与 selected；调用方据 outcome 决定话术
    """
    return await self.compile().ainvoke({"question": question, "source_types": source_types or []})


_graph: KnowledgeQueryGraph | None = None


def get_knowledge_graph() -> KnowledgeQueryGraph:
  """进程内单例：编译一次即可复用。"""
  global _graph
  if _graph is None:
    _graph = KnowledgeQueryGraph()
  return _graph
