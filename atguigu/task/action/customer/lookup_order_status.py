from typing import Any

from atguigu.domain.state import DialogueState
from atguigu.task.action.base import Action, ActionResult

class ActionLookupOrderStatus(Action):
  name = "action_lookup_order_status"

  async def run(self, action_kwargs: dict[str, Any], state: DialogueState) -> ActionResult:
    
    pass