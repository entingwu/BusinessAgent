"""
Milvus 向量客户端 —— 混合检索（dense + sparse）。

与 ChromaVectorClient 共用 VectorRecord / VectorMatch 两个中立结构，
所以入库流水线与检索侧基本不用改，只换实现。

## 为什么不能沿用 Chroma 那套四方法签名不变

C.4.3 当初把抽象定成 upsert / query / delete / count 四个方法，说「控制住这四个
签名，换库是半天的活」。这句话对纯 dense 成立，对混合检索不成立——query 需要
同时接受 dense 与 sparse 两路输入。所以这里给 query 加了 sparse_vector 参数，
Chroma 那边忽略它。**抽象能省的是实现的活，省不掉语义变化的活。**

## dense + sparse 的融合由 Milvus 内建完成

hybrid_search(reqs=[dense_req, sparse_req], ranker=WeightedRanker(w1, w2))
一次调用完成，不需要自己写 RRF。knowledge_base/atguigu 里那个 node_rrf 融合的
是**多路检索器**（原问题 / HyDE / 联网），不是 dense 与 sparse——这两件事很容易
混为一谈，估算时会重复计算一次。
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
  Goal: 转义 Milvus 过滤表达式里的字符串，避免解析失败或表达式注入。
  Args:
      value: 原始字符串
  Returns: str 转义后的安全字符串
  """
  return value.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")


def _build_filter(filters: dict[str, Any] | None) -> str:
  """
  Goal: 把中立的 {字段: 值或值列表} 过滤条件译成 Milvus 的布尔表达式。
        Chroma 用 where 字典，Milvus 用字符串表达式——差异挡在这里，
        不让任何一方的语法漏进 knowledge/。
  Args:
      filters: 例 {"source_type": ["document", "faq"]}
  Returns: str 例 'source_type in ["document", "faq"]'；无条件时返回空串
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
  Goal: Milvus 上的混合检索实现。collection 建两个向量字段：
        dense_vector（COSINE）与 sparse_vector（IP，稀疏倒排索引）。
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
      raise VectorStoreUnavailableError(f"pymilvus 未安装: {error}") from error
    try:
      self._client = MilvusClient(uri=self._uri)
      self._ensure_collection(self._client)
      self._ensure_loaded(self._client)
    except Exception as error:
      raise VectorStoreUnavailableError(f"milvus unavailable at {self._uri}: {error}") from error
    return self._client

  def _ensure_collection(self, client: Any) -> None:
    """
    Goal: 建表（幂等）。schema 照搬 knowledge_base/atguigu 的 _create_chunks_collection，
          字段换成本项目的溯源字段。
    """
    from pymilvus import DataType

    if client.has_collection(self._collection):
      return
    schema = client.create_schema(auto_id=False, enable_dynamic_field=True)
    schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=256)
    schema.add_field("content", DataType.VARCHAR, max_length=65535)
    # 下面这些是溯源字段，也是 metadata 过滤的依据；显式建字段而不是塞进
    # 动态字段，因为 Milvus 只能对显式字段做标量过滤。
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
    Goal: Milvus 的 collection 必须 load 进内存才能检索，未 load 时 search 直接报错。
          load 是幂等的，重复调用无副作用，所以每次取客户端时都确保一次。
    """
    try:
      client.load_collection(self._collection)
    except Exception as error:
      raise VectorStoreUnavailableError(f"milvus load_collection failed: {error}") from error

  # ---------------- 四个方法 ----------------

  async def upsert(self, chunks: list[VectorRecord]) -> int:
    """
    Goal: 写入或覆盖分片。Milvus 的 upsert 按主键覆盖。
    Args: chunks 每条须带 vector（dense）；sparse 缺失时该条只能被 dense 检索命中
    Returns: int 写入条数
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
        # 稀疏向量缺失时给一个空字典：Milvus 允许空稀疏向量，
        # 该条就只会被 dense 那一路命中，不会整条写不进去。
        "sparse_vector": chunk.sparse or {},
      }
      for name in COLLECTION_FIELDS:
        value = chunk.metadata.get(name)
        row[name] = (0 if name in ("position", "token_count") else "") if value is None else value
      rows.append(row)
    await self._call(lambda: client.upsert(collection_name=self._collection, data=rows))
    # 必须 flush。不 flush 的话数据虽然写进去了、也能查到，但 get_collection_stats
    # 的 row_count 恒为 0——「入库报成功、计数是 0」这个症状和 Chroma 那个
    # content_hash 跳过坑一模一样，机制却完全不同，排查时极易走错方向。
    await self._call(lambda: client.flush(self._collection))
    return len(rows)

  async def query(self,
                  vector: list[float],
                  top_k: int,
                  filters: dict[str, Any] | None = None,
                  sparse_vector: dict[int, float] | None = None) -> list[VectorMatch]:
    """
    Goal: 检索。给了 sparse_vector 就走混合检索，否则退化为纯 dense。
    Args:
        vector: dense 查询向量
        top_k: 返回条数
        filters: metadata 过滤，见 _build_filter
        sparse_vector: 稀疏查询向量；None 时不走混合
    Returns: list[VectorMatch] 按分数降序
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
        # 权重照搬 atguigu 的 (0.8, 0.2)：dense 主导，sparse 负责专有名词与
        # 关键词。norm_score=True 让两路分数可比——不归一化的话 IP 分数
        # 量纲和余弦不同，加权是没意义的。
        ranker=WeightedRanker(0.8, 0.2, norm_score=True),
        limit=top_k, output_fields=output))
    else:
      raw = await self._call(lambda: client.search(
        collection_name=self._collection, data=[vector], anns_field="dense_vector",
        search_params={"metric_type": "COSINE"}, filter=expr, limit=top_k, output_fields=output))
    return _to_matches(raw)

  async def delete(self, source_id: str) -> int:
    """
    Goal: 删掉一个知识源的全部分片。入库前先删后写，避免更新后留下孤儿向量。
    Returns: int 删除条数
    """
    client = self._get_client()
    expr = f'source_id == "{escape_milvus_string(source_id)}"'
    existing = await self._call(lambda: client.query(
      collection_name=self._collection, filter=expr, output_fields=["chunk_id"]))
    if not existing:
      return 0
    await self._call(lambda: client.delete(collection_name=self._collection, filter=expr))
    return len(existing)

  async def count(self) -> int:
    """
    Goal: 当前索引里的分片总数。与元数据表的条数对不上就是索引没建好。
    Returns: int
    """
    client = self._get_client()
    # 用 count(*) 查询而不是 get_collection_stats：后者只反映已 flush 的段，
    # 刚写入未 flush 时会报 0，让人误以为入库失败。
    rows = await self._call(lambda: client.query(
      collection_name=self._collection, filter="position >= 0", output_fields=["count(*)"]))
    if not rows:
      return 0
    first = rows[0]
    return int(first.get("count(*)", 0)) if isinstance(first, dict) else 0

  @staticmethod
  async def _call(func):
    """
    Goal: pymilvus 是同步客户端，丢进线程池避免阻塞事件循环；
          异常统一翻译成 VectorStoreUnavailableError，让上层走降级而不是崩掉。
    """
    try:
      return await asyncio.to_thread(func)
    except VectorStoreUnavailableError:
      raise
    except Exception as error:
      raise VectorStoreUnavailableError(f"milvus call failed: {error}") from error


def _to_matches(raw: Any) -> list[VectorMatch]:
  """
  Goal: 把 Milvus 的嵌套返回结构翻译成 VectorMatch，不让它漏进 knowledge/。
        search 与 hybrid_search 都返回 [[hit, ...]]，取第一路查询的结果。
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
