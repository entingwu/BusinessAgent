"""
Vector store client (Chroma implementation; selection rationale in appendix C.4.3 of
meta-business-agent.md).

The abstraction exposes exactly four methods: upsert / query / delete / count.
Chroma's raw response shape — parallel arrays of ids / documents / metadatas / distances — is
translated into VectorMatch inside this module and **must never leak into any layer of
knowledge/**. The spec puts it plainly: if it leaks, swapping stores becomes two days of rework.
"""
import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from business_agent.config.settings import settings


class VectorStoreUnavailableError(RuntimeError):
  """
  Goal: signal that the vector store is unavailable. Callers use this to take the degraded path
        ("cannot look this up right now, let me hand you to a human") and must never fall back to
        answering from the model's own knowledge (spec 5.1 / C.4.7).
  """


@dataclass(slots=True)
class VectorRecord:
  """
  Goal: one record to write into the vector store (a neutral shape, independent of any store)
  """
  id: str
  vector: list[float]
  document: str
  metadata: dict[str, Any] = field(default_factory=dict)
  # 稀疏向量 {token_id: weight}，仅 bge_m3 后端产出。Chroma 不支持稀疏检索，
  # 它会忽略这个字段；Milvus 用它做混合检索的第二路。
  sparse: dict[int, float] | None = None


@dataclass(slots=True)
class VectorMatch:
  """
  Goal: one retrieval hit
  Attributes:
      score: cosine similarity in [-1, 1], higher is more similar (already converted from
          Chroma's cosine distance)
  """
  id: str
  document: str
  metadata: dict[str, Any]
  score: float


def _build_where(filters: dict[str, Any] | None) -> dict[str, Any] | None:
  """
  Goal: translate plain filters such as {"source_type": "faq"} or
        {"source_type": ["faq", "document"]} into Chroma's where syntax. Several conditions
        require an explicit $and.
  Args:
      filters: metadata filters; a list value is treated as $in
  Returns: a Chroma where clause, or None when there is nothing to filter on
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
  Goal: the local persistent Chroma implementation. Runs in-process and adds no external
        service (spec C.4.1, rule 1). Migrating to PGVector or Qdrant means replacing this class
        alone; the signatures do not change.
  """

  def __init__(self,
               persist_dir: Path | None = None,
               collection_name: str | None = None):
    self._persist_dir = persist_dir or settings.resolved_vector_store_dir()
    self._collection_name = collection_name or settings.vector_collection_name
    self._collection = None

  # ---------------- internals: lazy initialisation ----------------

  def _get_collection(self):
    """
    Goal: connect to Chroma lazily. Importing this module must not do disk IO, or every import
          in the project ends up paying for the vector store's availability.
    Returns: chromadb Collection
    Raises: VectorStoreUnavailableError
    """
    if self._collection is not None:
      return self._collection

    try:
      import chromadb

      self._persist_dir.mkdir(parents=True, exist_ok=True)
      client = chromadb.PersistentClient(path=str(self._persist_dir))
      # Cosine similarity: Chroma defaults to L2, so space=cosine has to be stated explicitly —
      # otherwise the threshold means something else entirely (spec C.4.2, retrieval).
      # embedding_function=None: vectors are computed externally by DashScope and passed in, so
      # Chroma never spins up its bundled local ONNX model.
      self._collection = client.get_or_create_collection(
        name=self._collection_name,
        metadata={"hnsw:space": "cosine"},
        embedding_function=None,
      )
      return self._collection
    except Exception as error:  # noqa: BLE001
      raise VectorStoreUnavailableError(f"chroma unavailable at {self._persist_dir}: {error}") from error

  # ---------------- the four public methods ----------------

  async def upsert(self, chunks: list[VectorRecord]) -> int:
    """
    Goal: write or overwrite a batch of vector records (idempotent on id)
    Args:
        chunks: a list of VectorRecord
    Returns: how many records were actually written
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
                  filters: dict[str, Any] | None = None,
                  sparse_vector: dict[int, float] | None = None) -> list[VectorMatch]:
    """
    Goal: retrieve Top-K by cosine similarity, with optional metadata filtering
    Args:
        vector: the query vector
        top_k: maximum number of hits to return
        filters: metadata filters, e.g. {"source_type": ["faq"]}
    Returns: list[VectorMatch], sorted by similarity, highest first
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
    Goal: delete every chunk belonging to a knowledge source. Updating a source deletes before
          writing, which keeps the index in step with the document.
    Args:
        source_id: the knowledge source id
    Returns: how many records were deleted
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

  async def count(self, source_id: str | None = None) -> int:
    """
    Goal: how many chunks the index holds — the whole collection, or one source.

    The per-source form exists so ingest can tell "this source is already indexed" from
    "the metadata says it is, but the local index does not have it". Those carry the same
    ingest fingerprint and are completely different situations.
    Args: source_id: count only this source's chunks; None counts everything
    Returns: int
    Raises: VectorStoreUnavailableError — the skip decision in ingest and the empty-index guard
        in retrieval both depend on catching this. A backend that is down must not be
        indistinguishable from an index that is empty.
    """
    collection = await self._call(self._get_collection)
    if source_id is None:
      return await self._call(collection.count)
    raw = await self._call(lambda: collection.get(where={"source_id": source_id}, include=[]))
    return len(raw.get("ids") or [])

  # ---------------- internals: thread-pool dispatch ----------------

  @staticmethod
  async def _call(func):
    """
    Goal: the Chroma client is synchronous, so run it in a thread rather than blocking the event
          loop
    """
    try:
      return await asyncio.to_thread(func)
    except VectorStoreUnavailableError:
      raise
    except Exception as error:  # noqa: BLE001
      raise VectorStoreUnavailableError(f"chroma operation failed: {error}") from error


def _to_matches(raw: dict[str, Any]) -> list[VectorMatch]:
  """
  Goal: translate Chroma's parallel-array response into a list of VectorMatch.
        cosine distance = 1 - cosine similarity, converted back to similarity here.
  Args:
      raw: the raw return value of collection.query
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


def get_vector_client():
  """
  Goal: pick the vector store backend named by VECTOR_BACKEND (per-process singleton).

        chroma  dense only; the first implementation, kept as the fallback and as the
                A/B baseline for the rebuild
        milvus  dense + sparse hybrid retrieval

        Both backends share VectorRecord / VectorMatch, so nothing above knowledge/ is aware
        of which one is in use. Only one Chroma PersistentClient should ever be open on a given
        directory, and re-opening a Milvus connection per call is pointless — hence the singleton.
  Returns: ChromaVectorClient | MilvusVectorClient
  """
  global _vector_client
  if _vector_client is not None:
    return _vector_client
  backend = settings.vector_backend
  if backend == "chroma":
    _vector_client = ChromaVectorClient()
  elif backend == "milvus":
    # 延迟导入：用 chroma 的环境不必装 pymilvus
    from business_agent.infrastructure.milvus_client import MilvusVectorClient
    _vector_client = MilvusVectorClient()
  else:
    raise VectorStoreUnavailableError(
      f"未知的 VECTOR_BACKEND={backend!r}，可选：chroma、milvus")
  return _vector_client


async def main_test():
  client = get_vector_client()
  print(f"collection={settings.vector_collection_name} dir={settings.resolved_vector_store_dir()}")
  print(f"count={await client.count()}")


if __name__ == '__main__':
  asyncio.run(main_test())
