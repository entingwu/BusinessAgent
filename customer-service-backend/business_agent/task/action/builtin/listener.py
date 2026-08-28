from typing import Any

from business_agent.domain.state import DialogueState
from business_agent.task.action.base import Action, ActionResult


class ActionListener(Action):
  name = "action_listen"
  description = ("Stop and wait for the user's next message; produces no reply and calls no "
                 "external system")
  is_write = False

  async def run(self,
                action_args: dict[str, Any],
                state:DialogueState) -> ActionResult:
      return ActionResult()