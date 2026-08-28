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

  vector_store_dir: str                 # Chroma persistence directory (relative paths resolve against PROJECT_DIR)
  vector_collection_name: str           # Chroma collection name

  knowledge_source_dir: str             # knowledge source directory (relative paths resolve against PROJECT_DIR)
  knowledge_chunk_size: int             # chunk size in estimated tokens; spec default 500-800
  knowledge_chunk_overlap: int          # overlap in estimated tokens; spec default 80-150
  knowledge_top_k: int                  # retrieval Top-K
  knowledge_score_threshold: float      # cosine similarity threshold; below it counts as a miss
  knowledge_context_max_tokens: int     # cap on the total length of chunks put into the prompt

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
