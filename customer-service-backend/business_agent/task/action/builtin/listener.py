from typing import Any

from business_agent.domain.state import DialogueState
from business_agent.task.action.base import Action, ActionResult


class ActionListener(Action):
  name = "action_listen"

  async def run(self,
                action_args: dict[str, Any],
                state:DialogueState) -> ActionResult:
      return ActionResult()