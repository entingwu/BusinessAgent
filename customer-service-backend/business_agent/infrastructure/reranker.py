"""
重排（rerank）—— DashScope 托管的 gte-rerank。

## 它解决的是向量相似度解决不了的问题

向量相似度衡量的是「两段文本像不像」，rerank 衡量的是「这段文本能不能回答这个问题」。
这两件事在表面句式相似时会分道扬镳，本项目实测的对照（2026-08-28）：

| 场景 | 向量 top | rerank top |
|---|---|---|
| 中文命中「退货运费谁承担」 | 0.8263 | 0.9472 |
| 中文未命中「怎么开增值税发票」 | 0.6825 | 0.0060 |
| 中文离群点「支持货到付款吗」 | 0.7900 | 0.1676 |
| 英文命中 Who pays the return shipping fee? | 0.6707 | 0.4781 |
| 英文未命中 Do you offer gift wrapping? | 0.7101 | 0.0711 |

向量分的命中区间(0.67-0.83)与未命中区间(0.68-0.79)**完全重叠**，没有任何阈值可用；
rerank 分的命中(0.478-0.947)与未命中(0.006-0.168)之间隔着巨大空档，一个阈值通吃，
而且**跨语言通用**——这是它相对向量阈值最重要的性质。

## 与 knowledge_base/atguigu 的差异

atguigu 用 `dashscope` SDK 的 `TextReRank.call`，是同步的。这里改用 httpx 直接打
REST 接口：一是本项目全链路 async，同步调用会阻塞事件循环；二是 httpx 已是既有依赖，
不必为一个接口新增一个 SDK。

**唯一必须照抄的是 `scores[item.index] = score` 那个散射回填**——rerank 接口返回的
结果是按相关性重排过的，不是按传入顺序。不按 index 回填就会造成分数与文档错位，
而错位不报错，只会让答案莫名其妙。
"""
import asyncio
import logging

import httpx

from business_agent.config.settings import settings

logger = logging.getLogger(__name__)

RERANK_URL = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"


class RerankUnavailableError(RuntimeError):
  """
  Goal: 重排服务不可用。上层据此决定降级策略——本项目选择「跳过重排、退回向量分」，
        而不是整轮失败：重排是提升精度的环节，它挂了不该让用户拿不到答案。
  """


async def rerank(query: str, documents: list[str], *, timeout: float = 15.0) -> list[float]:
  """
  Goal: 对候选文档按与 query 的相关性打分。
  Args:
      query: 用户问题
      documents: 候选文档正文，顺序与调用方的候选列表一致
  Returns: list[float] 与 documents 等长、同序的相关性分数，取值约 [0, 1]
  Raises: RerankUnavailableError 调用失败时抛出，由上层决定降级
  """
  if not documents:
    return []
  payload = {
    "model": settings.rerank_model,
    "input": {"query": query, "documents": documents},
    # return_documents=False：只要分数，不要接口把原文再回传一遍。
    # top_n 取全长：我们要的是给每一条打分，截断由调用方按阈值与断崖决定。
    "parameters": {"return_documents": False, "top_n": len(documents)},
  }
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
  except RerankUnavailableError:
    raise
  except Exception as error:
    raise RerankUnavailableError(f"rerank call failed: {error}") from error

  # 散射回填：接口按相关性倒序返回，必须用 index 放回原位置，否则分数与文档错位。
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
  Goal: 断崖截断——按分数落差决定取几条，而不是取固定条数。
        思路来自 knowledge_base/atguigu 的 node_rerank._step3_cliff_cutoff：
        相关的文档分数会扎堆，不相关的会断崖式掉下去，在断崖处切比取固定 Top-K 更准。

  Args:
      scores: 已按降序排列的相关性分数
      score_min: 第一条都低于它就整体判未命中，返回 0
      max_top_k / min_top_k: 硬上下限
      gap_abs / gap_ratio: 绝对落差与相对落差，任一触发即在此处截断
  Returns: int 应保留的条数；0 表示未命中
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
  cases = [("退货运费谁承担", ["问：退货运费谁出？ 答：七天无理由退货的往返运费由消费者承担。",
                              "配送范围 平台覆盖中国大陆全境及港澳台地区。"]),
           ("支持货到付款吗", ["问：支持海外或港澳台配送吗？ 答：支持中国大陆及港澳台配送。",
                              "配送范围 平台覆盖中国大陆全境及港澳台地区。"])]
  for query, docs in cases:
    scores = await rerank(query, docs)
    keep = cliff_cutoff(sorted(scores, reverse=True), score_min=settings.rerank_score_min, max_top_k=5)
    print(f"{query}  scores={[round(s, 4) for s in scores]}  保留={keep}")


if __name__ == "__main__":
  logging.basicConfig(level=logging.INFO)
  asyncio.run(main_test())
