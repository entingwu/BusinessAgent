from typing import Any

from econ_agent.domain.state import DialogueState
from econ_agent.task.action.base import Action, ActionResult


class ActionListener(Action):
  name = "action_listen"

  async def run(self,
                action_args: dict[str, Any],
                state:DialogueState) -> ActionResult:
      return ActionResult()