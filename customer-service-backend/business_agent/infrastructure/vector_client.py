"""
向量库客户端（Chroma 实现，选型见 meta-business-agent.md 附录 C.4.3）

抽象边界只暴露四个方法：upsert / query / delete / count。
Chroma 的原始返回结构（ids / documents / metadatas / distances 的平行数组）
一律在本模块内翻译成 VectorMatch，**不允许漏进 knowledge/ 任何一层**——
规范原话：漏进去换库就是两天的返工。
"""
import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from business_agent.config.settings import settings


class VectorStoreUnavailableError(RuntimeError):
  """
  Goal: 向量库不可用。上层据此走「暂时查不了，帮你转人工」的降级路径，
        不得退化为用模型自身知识作答（规范 5.1 / C.4.7）。
  """


@dataclass(slots=True)
class VectorRecord:
  """
  Goal: 写入向量库的一条记录（中立结构，与具体向量库无关）
  """
  id: str
  vector: list[float]
  document: str
  metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class VectorMatch:
  """
  Goal: 检索命中的一条记录
  Attributes:
      score: 余弦相似度，取值 [-1, 1]，越大越相似（已由 Chroma 的 cosine distance 换算）
  """
  id: str
  document: str
  metadata: dict[str, Any]
  score: float


def _build_where(filters: dict[str, Any] | None) -> dict[str, Any] | None:
  """
  Goal: 把 {"source_type": "faq"} / {"source_type": ["faq", "document"]} 这类朴素过滤条件
        翻译成 Chroma 的 where 语法。多个条件需要显式 $and。
  Args:
      filters: metadata 过滤条件，值为列表时按 $in 处理
  Returns: Chroma where 子句，无过滤时返回 None
  """
  if not filters:
    return None

  clauses: list[dict[str, Any]] = []
  for key, value in filters.items():
    if isinstance(value, (list, tuple, set)):
      values = list(value)
      if not values:
        continue
      clauses.append({key: {"$in": values}})
    elif isinstance(value, dict):
      clauses.append({key: value})
    else:
      clauses.append({key: {"$eq": value}})

  if not clauses:
    return None
  if len(clauses) == 1:
    return clauses[0]
  return {"$and": clauses}


class ChromaVectorClient:
  """
  Goal: Chroma 本地持久化实现。进程内可跑，零新增外部服务（规范 C.4.1 第 1 条）。
        迁移到 PGVector / Qdrant 时只需换掉本类，签名不变。
  """

  def __init__(self,
               persist_dir: Path | None = None,
               collection_name: str | None = None):
    self._persist_dir = persist_dir or settings.resolved_vector_store_dir()
    self._collection_name = collection_name or settings.vector_collection_name
    self._collection = None

  # ---------------- 内部：延迟初始化 ----------------

  def _get_collection(self):
    """
    Goal: 延迟建立 Chroma 连接。导入本模块不应产生磁盘 IO，
          否则任何 import 都要为向量库的可用性买单。
    Returns: chromadb Collection
    Raises: VectorStoreUnavailableError
    """
    if self._collection is not None:
      return self._collection

    try:
      import chromadb

      self._persist_dir.mkdir(parents=True, exist_ok=True)
      client = chromadb.PersistentClient(path=str(self._persist_dir))
      # 余弦相似度：Chroma 默认是 L2，必须显式指定 space=cosine，
      # 否则阈值 0.35 的语义完全对不上（规范 C.4.2 检索环节）。
      # embedding_function=None：向量由 DashScope 在外部算好后传入，
      # 不让 Chroma 拉起自带的本地 ONNX 模型。
      self._collection = client.get_or_create_collection(
        name=self._collection_name,
        metadata={"hnsw:space": "cosine"},
        embedding_function=None,
      )
      return self._collection
    except Exception as error:  # noqa: BLE001
      raise VectorStoreUnavailableError(f"chroma unavailable at {self._persist_dir}: {error}") from error

  # ---------------- 对外四个方法 ----------------

  async def upsert(self, chunks: list[VectorRecord]) -> int:
    """
    Goal: 写入 / 覆盖一批向量记录（按 id 幂等）
    Args:
        chunks: VectorRecord 列表
    Returns: int 实际写入条数
    Raises: VectorStoreUnavailableError
    """
    if not chunks:
      return 0

    def _run() -> int:
      collection = self._get_collection()
      collection.upsert(
        ids=[chunk.id for chunk in chunks],
        embeddings=[chunk.vector for chunk in chunks],
        documents=[chunk.document for chunk in chunks],
        metadatas=[chunk.metadata for chunk in chunks],
      )
      return len(chunks)

    return await self._call(_run)

  async def query(self,
                  vector: list[float],
                  top_k: int,
                  filters: dict[str, Any] | None = None) -> list[VectorMatch]:
    """
    Goal: 按余弦相似度检索 Top-K，支持 metadata 过滤
    Args:
        vector: 查询向量
        top_k: 返回条数上限
        filters: metadata 过滤条件，例如 {"source_type": ["faq"]}
    Returns: list[VectorMatch] 按相似度从高到低排序
    Raises: VectorStoreUnavailableError
    """

    def _run() -> list[VectorMatch]:
      collection = self._get_collection()
      raw = collection.query(
        query_embeddings=[vector],
        n_results=max(1, top_k),
        where=_build_where(filters),
        include=["documents", "metadatas", "distances"],
      )
      return _to_matches(raw)

    return await self._call(_run)

  async def delete(self, source_id: str) -> int:
    """
    Goal: 删除某个知识源的全部分片（知识源更新时先删后写，保证索引与文档同步）
    Args:
        source_id: 知识源 ID
    Returns: int 删除条数
    Raises: VectorStoreUnavailableError
    """

    def _run() -> int:
      collection = self._get_collection()
      existing = collection.get(where={"source_id": {"$eq": source_id}}, include=[])
      ids = existing.get("ids") or []
      if not ids:
        return 0
      collection.delete(ids=ids)
      return len(ids)

    return await self._call(_run)

  async def count(self) -> int:
    """
    Goal: 返回索引内分片总数
    Returns: int
    Raises: VectorStoreUnavailableError
    """

    def _run() -> int:
      return self._get_collection().count()

    return await self._call(_run)

  # ---------------- 内部：线程池调度 ----------------

  @staticmethod
  async def _call(func):
    """
    Goal: Chroma 客户端是同步的，放到线程里跑，避免阻塞事件循环
    """
    try:
      return await asyncio.to_thread(func)
    except VectorStoreUnavailableError:
      raise
    except Exception as error:  # noqa: BLE001
      raise VectorStoreUnavailableError(f"chroma operation failed: {error}") from error


def _to_matches(raw: dict[str, Any]) -> list[VectorMatch]:
  """
  Goal: 把 Chroma 的平行数组返回结构翻译成 VectorMatch 列表
        cosine distance = 1 - cosine similarity，这里换算回相似度
  Args:
      raw: collection.query 的原始返回
  Returns: list[VectorMatch]
  """
  ids = (raw.get("ids") or [[]])[0]
  documents = (raw.get("documents") or [[]])[0]
  metadatas = (raw.get("metadatas") or [[]])[0]
  distances = (raw.get("distances") or [[]])[0]

  matches: list[VectorMatch] = []
  for index, chunk_id in enumerate(ids):
    distance = distances[index] if index < len(distances) else None
    score = 1.0 - float(distance) if distance is not None else 0.0
    matches.append(VectorMatch(
      id=chunk_id,
      document=documents[index] if index < len(documents) else "",
      metadata=dict(metadatas[index]) if index < len(metadatas) and metadatas[index] else {},
      score=score,
    ))
  return matches


_vector_client: ChromaVectorClient | None = None


def get_vector_client() -> ChromaVectorClient:
  """
  Goal: 进程内单例。Chroma 的 PersistentClient 同一目录只应开一份。
  Returns: ChromaVectorClient
  """
  global _vector_client
  if _vector_client is None:
    _vector_client = ChromaVectorClient()
  return _vector_client


async def main_test():
  client = get_vector_client()
  print(f"collection={settings.vector_collection_name} dir={settings.resolved_vector_store_dir()}")
  print(f"count={await client.count()}")


if __name__ == '__main__':
  asyncio.run(main_test())
