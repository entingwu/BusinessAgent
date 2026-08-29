"""
Reranking — DashScope's hosted gte-rerank.

## It solves what vector similarity cannot

Vector similarity measures how alike two pieces of text are; reranking measures whether a piece of
text can answer the question. The two part company whenever surface phrasing is similar. Measured
here on 2026-08-28:

| case | vector top | rerank top |
|---|---|---|
| zh hit, 「退货运费谁承担」 | 0.8263 | 0.9472 |
| zh miss, 「怎么开增值税发票」 | 0.6825 | 0.0060 |
| zh outlier, 「支持货到付款吗」 | 0.7900 | 0.1676 |
| en hit, Who pays the return shipping fee? | 0.6707 | 0.4781 |
| en miss, Do you offer gift wrapping? | 0.7101 | 0.0711 |

On vector scores the hit range (0.67-0.83) and the miss range (0.68-0.79) **overlap completely**,
so no threshold works at all. On rerank scores hits (0.478-0.947) and misses (0.006-0.168) are
separated by a wide gap that one threshold covers, and it covers it **across languages** — which
is the most important property it has over a vector threshold.

Caveat on those figures: the sample above is small and was taken before the corpus was
englishified. A later full-set calibration narrowed the gap considerably. The shape of the finding
holds; the numbers are illustrative, not current. Re-derive with `ingest calibrate`.

## Differences from knowledge_base/atguigu

atguigu uses the `dashscope` SDK's `TextReRank.call`, which is synchronous. This calls the REST
endpoint through httpx instead: the project is async throughout and a synchronous call would block
the event loop, and httpx is already a dependency so no new SDK is needed for one endpoint.

**The one thing that must be copied verbatim is the `scores[item.index] = score` scatter.** The
rerank endpoint returns results reordered by relevance, not in the order they were sent. Writing
them back positionally instead of by index misaligns scores with documents — and a misalignment
raises nothing, it just makes the answers inexplicable.
"""
import asyncio
import logging

import httpx

from business_agent.config.settings import settings

logger = logging.getLogger(__name__)

RERANK_URL = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"


class RerankUnavailableError(RuntimeError):
  """
  Goal: the rerank service is unavailable. Callers decide how to degrade; this project skips
        reranking and falls back to vector scores rather than failing the turn — reranking
        improves precision, and losing it should not leave the user with no answer at all.
  """


async def rerank(query: str, documents: list[str], *, timeout: float = 15.0) -> list[float]:
  """
  Goal: score candidate documents by relevance to the query.
  Args:
      query: the user's question
      documents: candidate document bodies, in the caller's candidate order
  Returns: relevance scores, same length and same order as documents, roughly in [0, 1]
  Raises: RerankUnavailableError on failure, leaving the degradation choice to the caller
  """
  if not documents:
    return []
  payload = {
    "model": settings.rerank_model,
    "input": {"query": query, "documents": documents},
    # return_documents=False: we want scores only, not the endpoint echoing every body back.
    # top_n is the full length: the point is to score every candidate; cutting is the caller's
    # job, by threshold and cliff.
    "parameters": {"return_documents": False, "top_n": len(documents)},
  }
  # Retry once. A rerank failure degrades silently to vector scores: precision drops and nobody
  # notices, because a reply still comes back. Measured over a calibration run of 34 consecutive
  # calls, transient failures happen occasionally and a single retry covers them. Not retrying
  # means letting answer quality vary at random.
  last_error: Exception | None = None
  for attempt in range(2):
    try:
      async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
          RERANK_URL,
          headers={"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"},
          json=payload,
        )
      if response.status_code != httpx.codes.OK:
        raise RerankUnavailableError(f"rerank HTTP {response.status_code}: {response.text[:200]}")
      results = response.json().get("output", {}).get("results", [])
      break
    except Exception as error:
      last_error = error
      if attempt == 0:
        await asyncio.sleep(0.5)
  else:
    # Include the exception type: some httpx exceptions have an empty str(), and logging the
    # message alone produces "rerank call failed: " with no information in it.
    raise RerankUnavailableError(
      f"rerank call failed after 2 attempts: {type(last_error).__name__}: {last_error}"
    ) from last_error

  # Scatter back by index: the endpoint returns results in descending relevance, so they must be
  # placed back by index or scores and documents end up misaligned.
  scores = [0.0] * len(documents)
  for item in results:
    index = item.get("index")
    if isinstance(index, int) and 0 <= index < len(scores):
      scores[index] = float(item.get("relevance_score", 0.0))
  return scores


def cliff_cutoff(scores: list[float],
                 *,
                 score_min: float,
                 max_top_k: int,
                 min_top_k: int = 1,
                 gap_abs: float = 0.10,
                 gap_ratio: float = 0.25) -> int:
  """
  Goal: cliff cutoff — decide how many to keep from where the scores drop, rather than keeping a
        fixed count. The idea comes from atguigu's node_rerank._step3_cliff_cutoff: relevant
        documents cluster together and irrelevant ones fall off a cliff, and cutting at the cliff
        is more accurate than a fixed Top-K.

  Args:
      scores: relevance scores, already sorted descending
      score_min: if even the first is below this, the whole thing is a miss and 0 is returned
      max_top_k / min_top_k: hard bounds
      gap_abs / gap_ratio: absolute and relative drop; either one triggers the cut
  Returns: how many to keep; 0 means a miss
  """
  if not scores or scores[0] < score_min:
    return 0
  upper = min(max_top_k, len(scores))
  lower = min(max(min_top_k, 1), upper)
  for index in range(lower - 1, upper - 1):
    current, following = scores[index], scores[index + 1]
    if current - following >= gap_abs or (current - following) / (abs(current) + 1e-6) >= gap_ratio:
      return index + 1
  return upper


async def main_test():
  # Fixtures mirror the shipped corpus, which is English. They used to be Chinese, which meant
  # this self-test exercised text that no longer exists anywhere in the index.
  cases = [("Who pays for return shipping",
            ["Q: Who pays for return shipping? A: For a 7-day no-questions-asked return you pay "
             "the shipping both ways.",
             "Where we deliver. We ship within mainland China."]),
           ("Do you take cash on delivery",
            ["Q: Do you ship overseas or to Hong Kong / Macau / Taiwan? A: We currently ship "
             "within mainland China only.",
             "Where we deliver. We ship within mainland China."])]
  for query, docs in cases:
    scores = await rerank(query, docs)
    keep = cliff_cutoff(sorted(scores, reverse=True), score_min=settings.rerank_score_min, max_top_k=5)
    print(f"{query}  scores={[round(s, 4) for s in scores]}  keep={keep}")


if __name__ == "__main__":
  logging.basicConfig(level=logging.INFO)
  asyncio.run(main_test())
