from typing import Any

from atguigu.domain.state import DialogueState
from atguigu.task.action.base import Action, ActionResult

class ActionLookupLogistics(Action):
  name = "action_lookup_logistics"

  async def run(self, action_kwargs: dict[str, Any], state: DialogueState) -> ActionResult:
    
    pass