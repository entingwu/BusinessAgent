"""
结构化日志的集中配置。对应规范 5.3 可观测性第一档。

背景：此前全仓没有一处集中配置——意图识别用 print、槽位守卫用 logging.warning
但没人配 handler、检索那一路由 knowledge 包自己挂 handler。服务进程跑起来时
root logger 停在 WARNING，于是除了 print 之外什么都看不见。

做法：给 `business_agent` 这个 logger 挂一个 handler，由 api 层在 lifespan 里调用。
`business_agent.knowledge` 自己挂了 handler 且 propagate=False，所以两边不会重复输出，
它的级别仍由 KNOWLEDGE_LOG_LEVEL 单独控制——检索日志量大，需要能单独关掉。

字段用 key=value 拼在消息里而不是 JSON：这个规模下人直接读日志的次数远多于
机器解析，key=value 既能 grep 也能扫一眼看懂。真要接日志系统时再换 formatter，
调用点不用动。
"""

import logging
import sys

from business_agent.config.settings import settings

_HANDLER_TAG = "business_agent.app_handler"
_LOGGER_NAME = "business_agent"


def configure_logging() -> None:
  """
  Goal: 给 business_agent 挂 handler。幂等——uvicorn --reload 会重复导入，
        挂两个 handler 会让每行日志打两遍
  """
  logger = logging.getLogger(_LOGGER_NAME)
  level_name = (settings.log_level or "INFO").upper()
  logger.setLevel(getattr(logging, level_name, logging.INFO))

  if any(getattr(handler, "_tag", None) == _HANDLER_TAG for handler in logger.handlers):
    return

  handler = logging.StreamHandler(stream=sys.stdout)
  handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
  handler._tag = _HANDLER_TAG  # type: ignore[attr-defined]
  logger.addHandler(handler)

  # 自带 handler 就不再冒泡，避免 uvicorn 的 root handler 再打一遍
  logger.propagate = False


def brief(value: object, limit: int = 400) -> str:
  """
  Goal: 把入参出参压成一行能看的长度

  日志里塞完整的商品列表或分片正文会把有用信息淹掉，而且这些内容
  在 retrieval_traces / 中台那边本来就查得到。日志要的是「调了什么、
  多久、成没成」，不是数据本身。

  上限 400 而不是更短：TurnPlan 实测 208 字符，早先设 120 时恰好把
  `SetSlotsCommand(...)` 那段砍掉——而排查「LLM 把订单号填进了哪个槽位」
  时唯一有用的就是那一段。截断点要落在信息密度低的地方，不是固定长度好看
  """
  text = str(value)
  return text if len(text) <= limit else f"{text[:limit]}…({len(text)} chars)"
