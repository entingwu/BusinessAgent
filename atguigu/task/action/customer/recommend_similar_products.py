from typing import Any

from atguigu.domain.state import DialogueState
from atguigu.task.action.base import Action, ActionResult

class ActionRecommandSimilarProduct(Action):
  name = "action_recommend_similar_products"

  async def run(self, action_kwargs: dict[str, Any], state: DialogueState) -> ActionResult:
    """
    TODO (Do not provide temporarilyl)
    Args:
        action_kwargs:
        state:    
    """
    return ActionResult()