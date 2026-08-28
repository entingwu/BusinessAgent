from typing import Any

from business_agent.domain.state import DialogueState
from business_agent.task.action.base import Action, ActionResult


class ActionListener(Action):
  name = "action_listen"
  description = "停下来等用户下一句话，不产生回复也不调用任何外部系统"
  is_write = False

  async def run(self,
                action_args: dict[str, Any],
                state:DialogueState) -> ActionResult:
      return ActionResult()