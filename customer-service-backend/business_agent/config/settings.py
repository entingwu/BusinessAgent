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
  app_port: int # APP_PORT=18082 自动转换成int类型

  # ---------------- 知识库 / RAG（选型见 meta-business-agent.md 附录 C.4）----------------
  # Embedding 与 LLM 同源复用 DashScope 凭据（LLM_API_KEY / LLM_BASE_URL），不引第二套凭据。
  # 入库与检索必须走同一个模型，因此模型名只在这一处配置。
  embedding_model: str                  # DashScope text-embedding-v3
  embedding_dimensions: int             # 1024，换模型必须全量重建索引
  embedding_batch_size: int             # DashScope 兼容接口单次最多 10 条

  vector_store_dir: str                 # Chroma 本地持久化目录（相对路径按 PROJECT_DIR 解析）
  vector_collection_name: str           # Chroma collection 名

  knowledge_source_dir: str             # 知识源文档目录（相对路径按 PROJECT_DIR 解析）
  knowledge_chunk_size: int             # 分片大小，单位 token（估算），规范默认 500–800
  knowledge_chunk_overlap: int          # 重叠长度，单位 token（估算），规范默认 80–150
  knowledge_top_k: int                  # 检索 Top-K
  knowledge_score_threshold: float      # 余弦相似度阈值，低于视为未命中
  knowledge_context_max_tokens: int     # 拼进提示词的分片总长上限

  model_config= SettingsConfigDict(env_file=ENV_FILE_PATH, env_file_encoding="utf-8")

  def resolved_vector_store_dir(self) -> Path:
    """
    Goal: 把 VECTOR_STORE_DIR 解析成绝对路径（相对路径以 customer-service-backend 为基准）
    Returns: Path
    """
    return self._resolve(self.vector_store_dir)

  def resolved_knowledge_source_dir(self) -> Path:
    """
    Goal: 把 KNOWLEDGE_SOURCE_DIR 解析成绝对路径
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
