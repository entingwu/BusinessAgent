"""
Milvus vector client — hybrid retrieval (dense + sparse).

Shares the neutral VectorRecord / VectorMatch shapes with ChromaVectorClient, so the ingest
pipeline and the retrieval side barely change; only the implementation swaps.

## Why the four Chroma signatures could not survive unchanged

C.4.3 originally fixed the abstraction at four methods — upsert / query / delete / count — and
claimed that "keep those four signatures and swapping stores is half a day's work". That holds for
pure dense retrieval and does not hold for hybrid: query has to accept a dense and a sparse input
together. So query grows a sparse_vector parameter here, which the Chroma side ignores.
**An abstraction can save you the implementation work; it cannot save you the work of a change in
semantics.**

## Milvus fuses dense and sparse itself

hybrid_search(reqs=[dense_req, sparse_req], ranker=WeightedRanker(w1, w2))
One call does it; there is no RRF to write. The node_rrf in knowledge_base/atguigu fuses
**multiple retrievers** (original question / HyDE / web), not dense and sparse — the two are easy
to conflate, and conflating them double-counts the work when estimating.
"""
import asyncio
from typing import Any

from business_agent.config.settings import settings
from business_agent.infrastructure.vector_client import (
  VectorMatch,
  VectorRecord,
  VectorStoreUnavailableError,
)

COLLECTION_FIELDS = ("source_id", "source_type", "source_name", "title", "position", "token_count", "embedding_model")


def escape_milvus_string(value: str) -> str:
  """
  Goal: escape a string inside a Milvus filter expression, so it cannot break parsing or inject
        expression syntax.
  Args:
      value: the raw string
  Returns: the escaped, safe string
  """
  return value.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")


def _build_filter(filters: dict[str, Any] | None) -> str:
  """
  Goal: translate a neutral {field: value or list} filter into a Milvus boolean expression.
        Chroma takes a where dict, Milvus takes an expression string — the difference is absorbed
        here so neither syntax leaks into knowledge/.
  Args:
      filters: e.g. {"source_type": ["document", "faq"]}
  Returns: e.g. 'source_type in ["document", "faq"]'; empty string when there is nothing to filter
  """
  if not filters:
    return ""
  clauses: list[str] = []
  for field_name, value in filters.items():
    values = value if isinstance(value, (list, tuple, set)) else [value]
    quoted = ", ".join(f'"{escape_milvus_string(str(item))}"' for item in values)
    clauses.append(f"{field_name} in [{quoted}]")
  return " and ".join(clauses)


class MilvusVectorClient:
  """
  Goal: the hybrid retrieval implementation on Milvus. The collection carries two vector fields:
        dense_vector (COSINE) and sparse_vector (IP, sparse inverted index).
  """

  def __init__(self, uri: str | None = None, collection: str | None = None) -> None:
    self._uri = uri or settings.milvus_uri
    self._collection = collection or settings.vector_collection_name
    self._client: Any | None = None

  def _get_client(self) -> Any:
    if self._client is not None:
      return self._client
    try:
      from pymilvus import MilvusClient
    except ImportError as error:
      raise VectorStoreUnavailableError(f"pymilvus is not installed: {error}") from error
    try:
      self._client = MilvusClient(uri=self._uri)
      self._ensure_collection(self._client)
      self._ensure_loaded(self._client)
    except Exception as error:
      raise VectorStoreUnavailableError(f"milvus unavailable at {self._uri}: {error}") from error
    return self._client

  def _ensure_collection(self, client: Any) -> None:
    """
    Goal: create the collection, idempotently. The schema follows atguigu's
          _create_chunks_collection, with this project's provenance fields substituted in.
    """
    from pymilvus import DataType

    if client.has_collection(self._collection):
      return
    schema = client.create_schema(auto_id=False, enable_dynamic_field=True)
    schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=256)
    schema.add_field("content", DataType.VARCHAR, max_length=65535)
    # These are the provenance fields and also what metadata filtering runs on. They are declared
    # explicitly rather than left to the dynamic field, because Milvus can only apply scalar
    # filters to declared fields.
    schema.add_field("source_id", DataType.VARCHAR, max_length=256)
    schema.add_field("source_type", DataType.VARCHAR, max_length=64)
    schema.add_field("source_name", DataType.VARCHAR, max_length=256)
    schema.add_field("title", DataType.VARCHAR, max_length=512)
    schema.add_field("position", DataType.INT64)
    schema.add_field("token_count", DataType.INT64)
    schema.add_field("embedding_model", DataType.VARCHAR, max_length=128)
    schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
    schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=settings.embedding_dimensions)

    index_params = client.prepare_index_params()
    index_params.add_index(field_name="sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="IP")
    index_params.add_index(field_name="dense_vector", index_type="AUTOINDEX", metric_type="COSINE")
    client.create_collection(collection_name=self._collection, schema=schema, index_params=index_params)

  def _ensure_loaded(self, client: Any) -> None:
    """
    Goal: a Milvus collection has to be loaded into memory before it can be searched; searching
          an unloaded collection errors outright. load is idempotent and has no side effect when
          repeated, so it is ensured every time the client is fetched.
    """
    try:
      client.load_collection(self._collection)
    except Exception as error:
      raise VectorStoreUnavailableError(f"milvus load_collection failed: {error}") from error

  # ---------------- the four methods ----------------

  async def upsert(self, chunks: list[VectorRecord]) -> int:
    """
    Goal: write or overwrite chunks. Milvus's upsert overwrites by primary key.
    Args: every chunk needs a dense vector; one without a sparse vector can only be found by the
          dense route
    Returns: how many records were written
    """
    if not chunks:
      return 0
    client = self._get_client()
    rows = []
    for chunk in chunks:
      row: dict[str, Any] = {
        "chunk_id": chunk.id,
        "content": chunk.document,
        "dense_vector": chunk.vector,
        # A missing sparse vector becomes an empty dict: Milvus accepts an empty sparse vector,
        # so the record is findable by the dense route instead of failing to write at all.
        "sparse_vector": chunk.sparse or {},
      }
      for name in COLLECTION_FIELDS:
        value = chunk.metadata.get(name)
        row[name] = (0 if name in ("position", "token_count") else "") if value is None else value
      rows.append(row)
    await self._call(lambda: client.upsert(collection_name=self._collection, data=rows))
    # flush() is mandatory. Without it the vectors are written and searchable, but
    # get_collection_stats keeps reporting row_count 0 — "ingest succeeded, count is 0" looks
    # identical to the fingerprint skip trap while being a completely unrelated mechanism,
    # which is exactly what sends you debugging the wrong half.
    await self._call(lambda: client.flush(self._collection))
    return len(rows)

  async def query(self,
                  vector: list[float],
                  top_k: int,
                  filters: dict[str, Any] | None = None,
                  sparse_vector: dict[int, float] | None = None) -> list[VectorMatch]:
    """
    Goal: retrieve. With a sparse_vector it runs hybrid search; without one it degrades to pure
          dense.
    Args:
        vector: the dense query vector
        top_k: how many hits to return
        filters: metadata filters, see _build_filter
        sparse_vector: the sparse query vector; None skips hybrid
    Returns: list[VectorMatch] in descending score order
    """
    client = self._get_client()
    expr = _build_filter(filters)
    output = ["chunk_id", "content", *COLLECTION_FIELDS]

    if sparse_vector:
      from pymilvus import AnnSearchRequest, WeightedRanker
      dense_req = AnnSearchRequest(
        data=[vector], anns_field="dense_vector",
        param={"metric_type": "COSINE"}, expr=expr or None, limit=top_k)
      sparse_req = AnnSearchRequest(
        data=[sparse_vector], anns_field="sparse_vector",
        param={"metric_type": "IP"}, expr=expr or None, limit=top_k)
      raw = await self._call(lambda: client.hybrid_search(
        collection_name=self._collection,
        reqs=[dense_req, sparse_req],
        # The (0.8, 0.2) weights follow atguigu: dense leads, sparse covers proper nouns and
        # keywords. norm_score=True makes the two comparable — without normalisation the IP score
        # is on a different scale from cosine and weighting them is meaningless.
        ranker=WeightedRanker(0.8, 0.2, norm_score=True),
        limit=top_k, output_fields=output))
    else:
      raw = await self._call(lambda: client.search(
        collection_name=self._collection, data=[vector], anns_field="dense_vector",
        search_params={"metric_type": "COSINE"}, filter=expr, limit=top_k, output_fields=output))
    return _to_matches(raw)

  async def delete(self, source_id: str) -> int:
    """
    Goal: delete every chunk of one knowledge source. Ingest deletes before writing so an update
          leaves no orphaned vectors.
    Returns: how many records were deleted
    """
    client = self._get_client()
    expr = f'source_id == "{escape_milvus_string(source_id)}"'
    existing = await self._call(lambda: client.query(
      collection_name=self._collection, filter=expr, output_fields=["chunk_id"]))
    if not existing:
      return 0
    await self._call(lambda: client.delete(collection_name=self._collection, filter=expr))
    return len(existing)

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
    client = self._get_client()
    # count(*) rather than get_collection_stats: the latter only reflects flushed segments and
    # reports 0 for freshly written vectors, which reads exactly like a failed ingest.
    expr = "position >= 0"
    if source_id is not None:
      expr = f'source_id == "{escape_milvus_string(source_id)}"'
    rows = await self._call(lambda: client.query(
      collection_name=self._collection, filter=expr, output_fields=["count(*)"]))
    if not rows:
      return 0
    first = rows[0]
    return int(first.get("count(*)", 0)) if isinstance(first, dict) else 0

  @staticmethod
  async def _call(func):
    """
    Goal: pymilvus is a synchronous client, so calls go to a thread pool rather than blocking the
          event loop. Every exception is translated into VectorStoreUnavailableError so callers
          degrade instead of crashing.
    """
    try:
      return await asyncio.to_thread(func)
    except VectorStoreUnavailableError:
      raise
    except Exception as error:
      raise VectorStoreUnavailableError(f"milvus call failed: {error}") from error


def _to_matches(raw: Any) -> list[VectorMatch]:
  """
  Goal: translate Milvus's nested response into VectorMatch, so its shape never leaks into
        knowledge/. Both search and hybrid_search return [[hit, ...]]; take the first query's
        results.
  """
  if not raw:
    return []
  hits = raw[0] if isinstance(raw[0], (list, tuple)) else raw
  matches: list[VectorMatch] = []
  for hit in hits:
    entity = hit.get("entity", hit) if isinstance(hit, dict) else {}
    metadata = {name: entity.get(name) for name in COLLECTION_FIELDS}
    matches.append(VectorMatch(
      id=entity.get("chunk_id") or (hit.get("id") if isinstance(hit, dict) else ""),
      document=entity.get("content", ""),
      metadata=metadata,
      score=float(hit.get("distance", 0.0)) if isinstance(hit, dict) else 0.0,
    ))
  return matches
