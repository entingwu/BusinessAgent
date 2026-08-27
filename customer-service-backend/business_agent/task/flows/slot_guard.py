"""
槽位写入守卫。

只用于**外部来源**的槽位写入——LLM 抽出来的 set_slots，以及用户点卡片带进来的值。
这两个来源都不可信：LLM 会把上一轮的实体顺手填进这一轮的槽位（实测「查订单 o30002
的物流」之后紧接着问「看看类似的商品推荐」，o30002 会被填进 product_id），卡片则可能
携带与其类型不符的 id。

`state.set_slots` 全仓有三个入口，前两个必须过守卫，第三个必须不过：

1. `CommandProcessor._update_slots` —— LLM 抽出来的 set_slots。**过守卫。**
2. `FlowExecutor._try_set_slots_from_object` —— 用户点卡片带进来的 id。**过守卫。**
   这一条容易漏：它不经过 CommandProcessor，只堵第 1 个入口时，命令层丢掉的脏值
   会被这条路原样再写一次——实测日志显示「丢弃槽位」而机器人照样吐出了 o30002。
3. `FlowExecutor` 里 `state.set_slots(action_result.updated_slots)` —— 动作产出的槽位。
   **绝对不要过守卫。** `Flow.slots` 是由 collect 步骤推导出来的「要向用户收集的槽位」
   清单，而 order_status / order_summary / tracking_number / logistics_company /
   logistics_status / logistics_traces 这些是动作写回去的，一个都不在清单里。
   实测误套的后果不是丢一部分，是四个槽位全丢，物流回复变成
   「订单o30002由配送，物流单号是。当前进度：。」这样的残句。
"""

import logging
import re
from typing import Any

from business_agent.task.flows.flows import Flow

logger = logging.getLogger(__name__)


def matches_pattern(pattern: str | None, value: Any) -> bool:
  """
  Goal: 判断槽位值是否满足声明的格式
  Args:
      pattern: 槽位定义里的正则，None 表示不约束
      value: 待写入的值
  Returns:
      是否放行
  """
  if pattern is None:
    return True
  if not isinstance(value, str):
    # 配了格式约束却给了非字符串，按不匹配处理，不做隐式转换
    return False
  return re.fullmatch(pattern, value.strip()) is not None


def accept_slots(flow: Flow | None,
                 slots: dict[str, Any],
                 source: str) -> dict[str, Any]:
  """
  Goal: 过滤掉不属于该流程、或格式不符的槽位，返回允许写入的子集

  丢弃而不是整轮拒绝：用户的意图本身通常是对的，错的只是被顺手带上的槽位值。
  丢掉之后 collect 步骤会正常向用户要，这比让整轮走澄清体验好。丢弃一律留日志，不静默。
  Args:
      flow: 当前流程；None 表示没有流程在跑，此时槽位无处安放
      slots: 待写入的槽位
      source: 来源标识，只用于日志（如 "set_slots 命令" / "卡片回填"）
  Returns:
      允许写入的槽位子集
  """
  if flow is None:
    logger.warning("[%s] 丢弃槽位 %s：当前没有可写入的业务流程", source, sorted(slots))
    return {}

  accepted: dict[str, Any] = {}
  for slot_name, value in slots.items():
    slot_definition = flow.slots.get(slot_name)

    # 关卡一：槽位名不属于这个流程
    if slot_definition is None:
      logger.warning(
        "[%s] 丢弃槽位 %s=%r：流程 %s 只声明了 %s",
        source, slot_name, value, flow.id, sorted(flow.slots)
      )
      continue

    # 关卡二：格式不符
    if not matches_pattern(slot_definition.pattern, value):
      logger.warning(
        "[%s] 丢弃槽位 %s=%r：不匹配格式 %s（流程 %s）",
        source, slot_name, value, slot_definition.pattern, flow.id
      )
      continue

    # 写入 strip 过的值，而不是原值。两者不一致会破坏这道守卫自己的不变量——
    # 放行并落库的值必须自己也能再次通过这道守卫。实测后果：" p2016 " 按 strip 后匹配
    # 被放行，但落库的是带空格的原值，下游 quote() 成 %20 打到中台 404，回复回显
    # " p2016 " 这个 id 而不是商品名。首尾空白只可能是 LLM 或卡片带来的噪声，去掉无损。
    accepted[slot_name] = value.strip() if isinstance(value, str) else value

  return accepted
