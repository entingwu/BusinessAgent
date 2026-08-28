from pydantic_settings import SettingsConfigDict, BaseSettings
from pathlib import Path

# config->business_agent->customer-service-backend
PROJECT_DIR = Path(__file__).resolve().parents[2]

ENV_FILE_PATH = PROJECT_DIR / ".env"

class Settings(BaseSettings):
  llm_model: str
  llm_base_url: str
  llm_api_key: str
  commerce_api_base_url: str
  database_url: str
  app_host: str
  app_port: int  # APP_PORT=18082, coerced to int automatically

  # Application log level. It has a default, so .env does not need to change — unlike the RAG
  # keys below, a missing value here should not stop the service from starting.
  log_level: str = "INFO"

  # The merchant's own handoff keywords, comma-separated (spec 3.3.4, "configured keyword
  # matched"). It defaults to empty — leaving it unset simply disables that trigger and should
  # not stop the service from starting.
  handoff_keywords: str = ""

  # ---------------- knowledge base / RAG (selection rationale in appendix C.4) ----------------
  # Embedding reuses the LLM's DashScope credentials (LLM_API_KEY / LLM_BASE_URL); there is no
  # second set of credentials. Ingest and retrieval must use the same model, so the model name is
  # configured in exactly this one place.
  # Embedding backend: dashscope (hosted, dense only) | bge_m3 (local, dense + sparse).
  # Hybrid retrieval needs sparse vectors, so the Milvus path requires bge_m3.
  embedding_backend: str
  # Inference device, bge_m3 only: cpu | mps | cuda.
  # Measured on M1 Max — mps is 2x faster per query (0.148s vs 0.308s) but slower in batch
  # (0.038s vs 0.022s per item): MPS has a fixed per-call overhead batching cannot amortise.
  # Use mps for queries, cpu for the ingest CLI. use_fp16 stays off — it needs CUDA.
  embedding_device: str
  embedding_model: str                  # dashscope: text-embedding-v3 | bge_m3: BAAI/bge-m3
  embedding_dimensions: int             # 1024; changing models forces a full reindex
  embedding_batch_size: int             # the DashScope-compatible API accepts at most 10 per call

  # Vector store backend: chroma (dense only, the fallback) | milvus (dense + sparse hybrid)
  vector_backend: str
  milvus_uri: str                       # milvus backend only, e.g. http://127.0.0.1:19530
  vector_store_dir: str                 # Chroma persistence directory (relative paths resolve against PROJECT_DIR)
  vector_collection_name: str           # collection name (Chroma collection / Milvus collection)

  knowledge_source_dir: str             # knowledge source directory (relative paths resolve against PROJECT_DIR)
  knowledge_chunk_size: int             # chunk size in estimated tokens; spec default 500-800
  knowledge_chunk_overlap: int          # overlap in estimated tokens; spec default 80-150
  knowledge_top_k: int                  # retrieval Top-K
  knowledge_score_threshold: float      # cosine threshold; below it counts as a miss (used when rerank is off)

  # Reranking. When enabled the gate moves from the vector score to the rerank score — they mean
  # different things: vector similarity is "how alike", rerank is "can this answer the question".
  # Measured on this corpus the vector hit/miss ranges overlap completely while the rerank ranges
  # are cleanly separated, and the rerank scale holds across languages.
  rerank_enabled: bool
  rerank_model: str                     # DashScope gte-rerank-v2
  rerank_candidates: int                # how many candidates go into reranking; recall must be wider than Top-K
  rerank_score_min: float               # if even the top candidate scores below this, it is a miss

  # HyDE: have the LLM write a hypothetical answer, retrieve with that too, fuse by RRF.
  # It adds an LLM call — measured at 2.0-3.7s, more than BGE-M3 inference + Milvus + rerank
  # combined. Measured to change nothing on this corpus (recall was never the bottleneck),
  # so it is off by default; the code stays because the graph's fan-out needs a second path.
  hyde_enabled: bool
  hyde_weight: float                    # weight of the HyDE path in RRF; the direct path is always 1.0

  # Orchestrate retrieval with LangGraph. Both implementations produce the same results; the
  # difference is how "fallback never calls the LLM" is guaranteed — the function path relies on
  # an if-return mid-function, the graph on topology: node_fallback / node_degrade do not route
  # to node_answer, which you can confirm by looking at the graph rather than reading the code.
  knowledge_graph_enabled: bool
  knowledge_context_max_tokens: int     # cap on the total length of chunks put into the prompt

  # Dedicated connection for knowledge metadata. Empty = use DATABASE_URL (main's behaviour).
  # A branch that changes the embedding model or the vector store MUST point this at its own
  # database: the vector index is a local gitignored directory while the metadata lives in the
  # shared MySQL, so sharing it makes the "vector_chunks == metadata_chunks" acceptance check
  # fail for everyone else — for reasons unrelated to their own environment.
  # Full rationale in infrastructure/knowledge_db.py
  knowledge_database_url: str = ""

  knowledge_log_level: str              # log level for the knowledge package; retrieval traces depend on it
  knowledge_trace_enabled: bool         # whether to write each turn's hits and similarities to retrieval_traces

  model_config= SettingsConfigDict(env_file=ENV_FILE_PATH, env_file_encoding="utf-8")

  def resolved_vector_store_dir(self) -> Path:
    """
    Goal: resolve VECTOR_STORE_DIR to an absolute path (relative paths are based on
          customer-service-backend)
    Returns: Path
    """
    return self._resolve(self.vector_store_dir)

  def resolved_knowledge_source_dir(self) -> Path:
    """
    Goal: resolve KNOWLEDGE_SOURCE_DIR to an absolute path
    Returns: Path
    """
    return self._resolve(self.knowledge_source_dir)

  @staticmethod
  def _resolve(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else (PROJECT_DIR / path).resolve()

settings = Settings() # type:ignore

if __name__ == '__main__':
  print(settings.llm_model)
  print(settings.llm_base_url)
  print(settings.app_port)
  print(settings.embedding_model, settings.embedding_dimensions)
  print(settings.resolved_vector_store_dir())
  print(settings.resolved_knowledge_source_dir())
