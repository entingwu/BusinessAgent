"""
Fusing results from multiple retrievers — RRF (Reciprocal Rank Fusion).

## What it actually fuses

**Not dense and sparse.** Milvus fuses those inside `hybrid_search` with its `WeightedRanker`,
and one call produces the result.

RRF fuses **multiple retrievers**: one route for the original question, one for the HyDE
hypothetical answer. The two use different query text, each returns its own ranked list, and the
lists have to become one.

The distinction is easy to blur, and blurring it double-counts RRF when estimating work — which
happened on this project.

## Why by rank rather than by score

Scores from different retrieval routes are not comparable: the cosine score from the
original-question route and from the HyDE route share units but not distribution (HyDE text is
longer and more formal, so its scores run high). **Fusing by rank is immune to that difference in
scale**, which is RRF's most practical property over a weighted sum.

The formula follows atguigu's node_rrf._rrf_merge:

    score(doc) = Σ  weight_i / (k + rank_i)

k=60 is RRF's conventional constant, and its job is to flatten the head of the list. Without k the
first rank carries twice the weight of the second, which is too steep; k=60 makes the weights
across the first dozen or so ranks gentle, so no single route's top hit dominates the result.
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
  Goal: fuse several ranked result lists into one with RRF.
  Args:
      ranked_lists: [(list sorted by descending relevance, that route's weight), ...]
      key: extracts a unique identifier from an element, used to de-duplicate across routes
      k: the RRF constant, 60 by default
      max_results: truncation; None means no truncation
  Returns: list[T] in descending fused score. A document appearing in several routes is kept once,
           as the first object encountered.
  """
  scores: dict[Any, float] = {}
  items: dict[Any, T] = {}
  for ranked, weight in ranked_lists:
    for rank, item in enumerate(ranked):
      identity = key(item)
      scores[identity] = scores.get(identity, 0.0) + weight / (k + rank + 1)
      # Keep the first object encountered: the higher-weighted route is passed in first, and its
      # score and metadata are the more trustworthy ones
      items.setdefault(identity, item)
  ordered = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
  merged = [items[identity] for identity, _ in ordered]
  return merged[:max_results] if max_results else merged
