"""
Embedding 后端的统一入口。

两个后端并存，由 EMBEDDING_BACKEND 切换：

  dashscope  托管 text-embedding-v3，只产 dense。第一版实现，作为退路保留，
             也是 A/B 对照的基准（基线数字见 knowledge_eval/BASELINE_*.md）。
  bge_m3     本地 BGE-M3，一次产 dense + sparse。混合检索的前提——
             sparse 分量是 Milvus hybrid_search 的第二路输入。

为什么保留两条：换 Embedding 会让相似度阈值作废（分数分布不同），
只有能来回切才能证明「重做是进步而不是退步」。选型依据见 RAG_ref.md。

实测（M1 Max，2026-08-28，单条查询延迟中位数）：
  dashscope 托管 API   0.68-0.85s   延迟主要在网络往返
  bge_m3 device=cpu    0.308s
  bge_m3 device=mps    0.148s       交互路径最快
批量则相反：cpu 0.022s/条 优于 mps 0.038s/条——MPS 每次调用的固定开销
批量时摊薄不掉。因此 EMBEDDING_DEVICE 可配，查询侧用 mps、入库脚本用 cpu。
"""
from dataclasses import dataclass, field
from typing import Any

from business_agent.config.settings import settings


class EmbeddingUnavailableError(RuntimeError):
  """
  Goal: Embedding 服务不可用。上层据此走「暂时查不了，帮你转人工」的降级路径，
        不得退化为模型自身知识作答（规范 5.1 / C.4.7）。
  """


@dataclass(slots=True)
class EmbeddingResult:
  """
  Goal: 一批文本的向量化结果。sparse 为空表示后端不产稀疏向量（dashscope 后端）。
  Args:
      dense: 每条文本一个稠密向量
      sparse: 每条文本一个 {token_id: weight} 字典；dashscope 后端下为空列表
  """
  dense: list[list[float]]
  sparse: list[dict[int, float]] = field(default_factory=list)

  @property
  def has_sparse(self) -> bool:
    return bool(self.sparse)


class EmbeddingBackend:
  """所有后端的共同签名。入库与检索必须走同一个实例，杜绝两边配不一致。"""

  name: str
  dimensions: int

  async def embed_documents(self, texts: list[str]) -> EmbeddingResult:
    raise NotImplementedError

  async def embed_query(self, text: str) -> EmbeddingResult:
    raise NotImplementedError


class DashScopeBackend(EmbeddingBackend):
  """
  Goal: 托管 text-embedding-v3。只产 dense，走 Chroma 那条退路时使用。
  """

  def __init__(self) -> None:
    self.name = settings.embedding_model
    self.dimensions = settings.embedding_dimensions
    # 延迟导入：装了 bge_m3 后端的环境不一定还需要 langchain_openai
    from business_agent.infrastructure.llm_client import embedding_client
    self._client = embedding_client

  async def embed_documents(self, texts: list[str]) -> EmbeddingResult:
    try:
      return EmbeddingResult(dense=await self._client.aembed_documents(texts))
    except Exception as error:
      raise EmbeddingUnavailableError(f"dashscope embed_documents failed: {error}") from error

  async def embed_query(self, text: str) -> EmbeddingResult:
    try:
      return EmbeddingResult(dense=[await self._client.aembed_query(text)])
    except Exception as error:
      raise EmbeddingUnavailableError(f"dashscope embed_query failed: {error}") from error


class BgeM3Backend(EmbeddingBackend):
  """
  Goal: 本地 BGE-M3，一次产 dense + sparse。

  两处与 knowledge_base/atguigu 的差异，都是本机约束造成的：
    1. use_fp16 恒为 False —— 半精度推理需要 CUDA，Apple Silicon 没有。
    2. device 可配 mps —— atguigu 只考虑 cuda/cpu，M1 的 Metal 后端比 cpu 快一倍。

  模型 2.3GB，首次构造会下载（实测 710s），之后从 HF 缓存加载约 1.7s。
  构造放在首次调用时（不在 import 期），否则任何 import 这个模块的命令
  都要等模型加载——包括只想看配置的 -m business_agent.config.settings。
  """

  def __init__(self) -> None:
    self.name = settings.embedding_model
    self.dimensions = settings.embedding_dimensions
    self._ef: Any | None = None

  def _ensure_model(self) -> Any:
    if self._ef is not None:
      return self._ef
    try:
      from pymilvus.model.hybrid import BGEM3EmbeddingFunction
    except ImportError as error:
      # pymilvus[model] 还需要 datasets 与 FlagEmbedding，但它不声明为硬依赖，
      # 而是在首次构造时试图 pip install——uv 管理的 venv 里没有 pip 会直接失败。
      # 两者已显式写进 pyproject.toml。
      raise EmbeddingUnavailableError(f"BGE-M3 依赖缺失: {error}") from error
    try:
      self._ef = BGEM3EmbeddingFunction(
        model_name=settings.embedding_model,
        device=settings.embedding_device,
        use_fp16=False,  # 需要 CUDA，本机没有
      )
    except Exception as error:
      raise EmbeddingUnavailableError(f"BGE-M3 加载失败: {error}") from error
    return self._ef

  @staticmethod
  def _to_sparse(raw: Any) -> list[dict[int, float]]:
    """
    Goal: 把 BGE-M3 的稀疏输出统一成 [{token_id: weight}]，Milvus 的
          SPARSE_FLOAT_VECTOR 就吃这个形状。

    这里有一个实测踩到的坑：**同一个对象的两个方法返回不同的稀疏格式**——
    encode_documents 返回 scipy 的 csr_array，encode_queries 返回 coo_array。
    coo 没有 indices/indptr，直接按 csr 的方式读会 AttributeError。
    而且 csr_array 是新 API，没有旧 csr_matrix 的 getrow()。

    所以统一先 tocsr() 归一，再用 indptr 切行——这样两种格式都能吃下，
    也不依赖任何单一版本才有的方法。
    """
    matrix = raw.tocsr() if hasattr(raw, "tocsr") else raw
    indptr, indices, data = matrix.indptr, matrix.indices, matrix.data
    return [
      {
        int(token): float(weight)
        for token, weight in zip(indices[indptr[row] : indptr[row + 1]], data[indptr[row] : indptr[row + 1]])
      }
      for row in range(matrix.shape[0])
    ]

  async def _encode(self, texts: list[str], *, as_query: bool) -> EmbeddingResult:
    import asyncio
    model = self._ensure_model()
    encode = model.encode_queries if as_query else model.encode_documents
    try:
      # 推理是 CPU/GPU 密集的同步调用，丢进线程池，避免阻塞事件循环——
      # 对话链路是 async 的，这里卡住会拖住整个请求。
      raw = await asyncio.to_thread(encode, texts)
    except Exception as error:
      raise EmbeddingUnavailableError(f"BGE-M3 推理失败: {error}") from error
    return EmbeddingResult(
      dense=[vector.tolist() if hasattr(vector, "tolist") else list(vector) for vector in raw["dense"]],
      sparse=self._to_sparse(raw["sparse"]),
    )

  async def embed_documents(self, texts: list[str]) -> EmbeddingResult:
    return await self._encode(texts, as_query=False)

  async def embed_query(self, text: str) -> EmbeddingResult:
    return await self._encode([text], as_query=True)


_BACKENDS = {"dashscope": DashScopeBackend, "bge_m3": BgeM3Backend}
_instance: EmbeddingBackend | None = None


def get_embedding_backend() -> EmbeddingBackend:
  """
  Goal: 取当前配置的 Embedding 后端（进程内单例）。
        入库与检索都从这里取，保证用的是同一个模型。
  Returns: EmbeddingBackend
  """
  global _instance
  if _instance is None:
    backend = settings.embedding_backend
    if backend not in _BACKENDS:
      raise EmbeddingUnavailableError(
        f"未知的 EMBEDDING_BACKEND={backend!r}，可选：{sorted(_BACKENDS)}"
      )
    _instance = _BACKENDS[backend]()
  return _instance
