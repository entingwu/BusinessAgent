"""
多路检索结果的融合 —— RRF（Reciprocal Rank Fusion）。

## 它融合的是什么

**不是 dense 与 sparse。** 那两路的融合由 Milvus 的 `WeightedRanker` 在
`hybrid_search` 内部完成，一次调用就出结果。

RRF 融合的是**多个检索器**：原问题一路、HyDE 假设性答案一路。这两路用的是
不同的查询文本，各自返回一个排序列表，需要合并成一个。

这个区分很容易混淆，混淆的代价是估算时把 RRF 重复计算一次——本项目就犯过。

## 为什么按名次而不是按分数

不同检索路的分数不可比：原问题那路的余弦分和 HyDE 那路的余弦分，量纲一样但
分布不同（HyDE 文本更长更书面，整体分数偏高）。**按名次融合天然免疫这种尺度差异**，
这是 RRF 相对加权求和最实用的性质。

公式来自 knowledge_base/atguigu 的 node_rrf._rrf_merge：

    score(doc) = Σ  weight_i / (k + rank_i)

k=60 是 RRF 的经验常数，作用是压平头部——不加 k 的话第 1 名的权重是第 2 名的
两倍，太陡；k=60 让前十几名的权重差异变得平缓，避免某一路的第 1 名直接主导结果。
"""
from typing import Any, Iterable, TypeVar

T = TypeVar("T")

RRF_K = 60


def rrf_merge(ranked_lists: Iterable[tuple[list[T], float]],
              *,
              key: Any,
              k: int = RRF_K,
              max_results: int | None = None) -> list[T]:
  """
  Goal: 把多路已排序的检索结果按 RRF 融合成一路。
  Args:
      ranked_lists: [(已按相关性降序的列表, 该路权重), ...]
      key: 从元素取唯一标识的函数，用于跨路去重
      k: RRF 常数，默认 60
      max_results: 截断，None 表示不截断
  Returns: list[T] 按融合分降序；同一文档在多路出现时只保留一份（取先遇到的那个对象）
  """
  scores: dict[Any, float] = {}
  items: dict[Any, T] = {}
  for ranked, weight in ranked_lists:
    for rank, item in enumerate(ranked):
      identity = key(item)
      scores[identity] = scores.get(identity, 0.0) + weight / (k + rank + 1)
      # 只保留先遇到的对象：权重高的那一路先传进来，它带的分数与元数据更可信
      items.setdefault(identity, item)
  ordered = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
  merged = [items[identity] for identity, _ in ordered]
  return merged[:max_results] if max_results else merged
