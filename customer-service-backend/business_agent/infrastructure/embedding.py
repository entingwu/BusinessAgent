"""
The single entry point for embedding backends.

Two backends coexist, selected by EMBEDDING_BACKEND:

  dashscope  hosted text-embedding-v3, dense only. The first implementation, kept as a fallback
             and as the A/B baseline (baseline figures in knowledge_eval/BASELINE_*.md).
  bge_m3     local BGE-M3, producing dense and sparse in one pass. Hybrid retrieval depends on
             it — the sparse component is the second leg of Milvus hybrid_search.

Why both are kept: swapping the embedding model voids the calibrated similarity threshold, since
the score distribution moves. Being able to switch back and forth is the only way to show a
rebuild is an improvement rather than a regression. Selection rationale in RAG_ref.md.

Measured (M1 Max, 2026-08-28, median single-query latency):
  dashscope hosted API   0.68-0.85s   latency is mostly the network round trip
  bge_m3 device=cpu    0.308s
  bge_m3 device=mps    0.148s       fastest on the interactive path
Batching inverts this: cpu at 0.022s per item beats mps at 0.038s, because MPS's fixed per-call
overhead does not amortise across a batch. Hence EMBEDDING_DEVICE is configurable — mps for
queries, cpu for the ingest script.
"""
import logging
from dataclasses import dataclass, field
from typing import Any

from business_agent.config.settings import settings


logger = logging.getLogger(__name__)


class EmbeddingUnavailableError(RuntimeError):
  """
  Goal: the embedding service is unavailable. Callers take the degraded path ("cannot look this
        up right now, let me hand you to a human") and must never fall back to answering from the
        model's own knowledge (spec 5.1 / C.4.7).
  """


@dataclass(slots=True)
class EmbeddingResult:
  """
  Goal: the embeddings for a batch of texts. An empty sparse list means the backend produces no
        sparse vectors (the dashscope backend).
  Args:
      dense: one dense vector per text
      sparse: one {token_id: weight} dict per text; empty list under the dashscope backend
  """
  dense: list[list[float]]
  sparse: list[dict[int, float]] = field(default_factory=list)

  @property
  def has_sparse(self) -> bool:
    return bool(self.sparse)


class EmbeddingBackend:
  """The signature every backend shares. Ingest and retrieval must use the same instance, so the
  two can never be configured differently."""

  name: str
  dimensions: int

  async def embed_documents(self, texts: list[str]) -> EmbeddingResult:
    raise NotImplementedError

  async def embed_query(self, text: str) -> EmbeddingResult:
    raise NotImplementedError


class DashScopeBackend(EmbeddingBackend):
  """
  Goal: hosted text-embedding-v3. Dense only; used on the Chroma fallback chain.
  """

  def __init__(self) -> None:
    self.name = settings.embedding_model
    self.dimensions = settings.embedding_dimensions
    # Imported lazily: an environment on the bge_m3 backend may not need langchain_openai at all
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
  Goal: local BGE-M3, producing dense and sparse in one pass.

  Two differences from knowledge_base/atguigu, both forced by this machine:
    1. use_fp16 is always False — half precision inference needs CUDA, which Apple Silicon lacks.
    2. device can be mps — atguigu only considers cuda/cpu, and M1's Metal backend is twice as
       fast as cpu here.

  The model is 2.3GB. The first construction downloads it (measured 710s); afterwards it loads
  from the HF cache in about 1.7s. Construction is deferred to first use rather than import time,
  or every command that imports this module would wait for the model — including
  `-m business_agent.config.settings`, which only wants to print configuration.

  Note that "first use" must not be inside a running event loop; see warmup_embedding_backend.
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
      # pymilvus[model] also needs datasets and FlagEmbedding, but it does not declare them as
      # hard dependencies — it tries to pip install them on first construction, which fails
      # outright in a uv-managed venv that has no pip. Both are listed explicitly in
      # pyproject.toml.
      raise EmbeddingUnavailableError(f"BGE-M3 dependency missing: {error}") from error
    try:
      self._ef = BGEM3EmbeddingFunction(
        model_name=settings.embedding_model,
        device=settings.embedding_device,
        use_fp16=False,  # needs CUDA, which this machine does not have
      )
    except Exception as error:
      raise EmbeddingUnavailableError(f"BGE-M3 failed to load: {error}") from error
    return self._ef

  @staticmethod
  def _to_sparse(raw: Any) -> list[dict[int, float]]:
    """
    Goal: normalise BGE-M3's sparse output to [{token_id: weight}], which is the shape Milvus's
          SPARSE_FLOAT_VECTOR expects.

    There is a measured trap here: **two methods on the same object return different sparse
    formats**. encode_documents returns a scipy csr_array; encode_queries returns a coo_array.
    coo has no indices/indptr, so reading it the csr way raises AttributeError. And csr_array is
    the new API — it has no getrow(), which the old csr_matrix had.

    So everything is normalised through tocsr() first and then sliced by indptr. That accepts both
    formats and depends on no method that exists in only one version.
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
      # Inference is a CPU/GPU-bound synchronous call, so it goes to a thread pool rather than
      # blocking the event loop — the dialogue path is async, and stalling here stalls the whole
      # request.
      raw = await asyncio.to_thread(encode, texts)
    except Exception as error:
      raise EmbeddingUnavailableError(f"BGE-M3 inference failed: {error}") from error
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


def warmup_embedding_backend() -> None:
  """
  Goal: load the model into memory **before** the event loop starts

  BgeM3Backend is lazy: _ensure_model() runs on the first retrieval. By then the process is
  already inside uvloop, and loading BGE-M3 forks child processes, which corrupts the loop's
  file-descriptor state. Measured, it takes the whole uvicorn process down with **SIGSEGV**:

      exception  EXC_BAD_ACCESS / SIGSEGV  (possible pointer authentication failure)
      frames     kevent → uv__io_poll → uv_run → uvloop Loop._run

  The symptom gives you nothing to go on: the service starts normally, the first knowledge
  question is even answered correctly, and then the process vanishes — no traceback, no shutdown
  log, the port simply goes free. The system crash report is the only evidence. The gRPC warning
  printed just before loading, `FD from fork parent still in poll list`, is the same problem
  showing early.

  Called before uvicorn.run(), the fork happens before the event loop exists and the conflict
  cannot arise. It also moves the model load out of the first knowledge request, measured at 15.8s.

  It never raises: when the model cannot load, the retrieval path already has its
  KnowledgeUnavailableError degradation, and a failed warmup should not stop the service from
  starting.
  """
  try:
    backend = get_embedding_backend()
  except Exception as error:  # noqa: BLE001 - a failed warmup must not stop startup
    logger.warning("embedding_warmup_skipped error=%r", error)
    return
  ensure = getattr(backend, "_ensure_model", None)
  if ensure is None:
    return
  try:
    ensure()
    logger.info("embedding_warmup_done backend=%s model=%s",
                settings.embedding_backend, settings.embedding_model)
  except Exception as error:  # noqa: BLE001
    logger.warning("embedding_warmup_failed error=%r", error)


def get_embedding_backend() -> EmbeddingBackend:
  """
  Goal: the configured embedding backend, as a per-process singleton.
        Ingest and retrieval both take it from here, so they cannot end up on different models.
  Returns: EmbeddingBackend
  """
  global _instance
  if _instance is None:
    backend = settings.embedding_backend
    if backend not in _BACKENDS:
      raise EmbeddingUnavailableError(
        f"unknown EMBEDDING_BACKEND={backend!r}; valid values are {sorted(_BACKENDS)}"
      )
    _instance = _BACKENDS[backend]()
  return _instance
