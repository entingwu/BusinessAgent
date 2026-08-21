from typing import Any

from atguigu.domain.messages import BotMessage
from atguigu.domain.state import DialogueState
from atguigu.task.action.base import Action, ActionResult


class ActionResponse(Action):
  name = "action_response"

  async def run(self, action_kwargs: dict[str, Any], state: DialogueState) -> ActionResult:
    """
    According to action_kwargs text content, analyze placeholder.
    封装到ActionResult的BotMessage内容
    """
    return ActionResult(messages=[
      BotMessage(text="rendered_text")
    ])