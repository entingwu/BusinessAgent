"""
Goal: 
From specific Action object from Register center, 
find run for action object
"""

from dataclasses import dataclass, field
from typing import Any

from atguigu.domain.state import DialogueState
from atguigu.task.action.base import ActionResult
from atguigu.task.action.register import ActionRegister

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
    action_result = await action.run(action_call.action_kwargs,state)

    return action_result