"""
Goal: 
From specific Action object from Register center, 
find run for action object
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from business_agent.domain.state import DialogueState
from business_agent.observability import brief
from business_agent.task.action.base import ActionResult
from business_agent.task.action.register import ActionRegister

logger = logging.getLogger(__name__)

@dataclass(slots=True)
class ActionCall:
  action_name: str
  action_kwargs: dict[str, Any] = field(default_factory=dict)


class ActionRunner:

  def __init__(self, action_register: ActionRegister):
    self.action_register = action_register


  async def run(self, action_call: ActionCall, state: DialogueState) -> ActionResult:
    """
    调用时机: 流程推进器在推进流程且流程步骤是action类型时候, 会调用到
    """
    action = self.action_register.get_action(action_call.action_name)

    # 规范 5.3 第一档：记录工具调用的入参、出参与耗时。
    # 挂在 runner 而不是各个 action 里——这里是所有动作的唯一必经点，
    # 挂一次就覆盖全部，且新增 action 自动带上，不会有人忘记加
    started_at = time.perf_counter()
    try:
      action_result = await action.run(action_call.action_kwargs, state)
    except Exception as error:
      logger.warning(
        "action name=%s sender_id=%s elapsed_ms=%.0f outcome=error error=%s",
        action_call.action_name, state.sender_id,
        (time.perf_counter() - started_at) * 1000, brief(error),
      )
      raise

    elapsed_ms = (time.perf_counter() - started_at) * 1000
    logger.info(
      "action name=%s sender_id=%s elapsed_ms=%.0f args=%s slots_in=%s "
      "messages=%d cards=%d slots_out=%s",
      action_call.action_name, state.sender_id, elapsed_ms,
      brief(action_call.action_kwargs),
      # 真实入参在槽位里而不是 action_kwargs：查订单/查物流/推荐三个 action
      # 都从 state.active_task.slots 取值，只记 action_kwargs 的话每次都是
      # args={}，回答不了「这次到底查的哪个订单号」——而那正是 5.3 要日志的原因
      brief(self._slots_of(state)),
      len(action_result.messages),
      sum(len(message.cards) for message in action_result.messages),
      brief(action_result.updated_slots),
    )
    return action_result

  def _slots_of(self, state: DialogueState) -> dict[str, Any]:
    return dict(state.active_task.slots) if state.active_task is not None else {}