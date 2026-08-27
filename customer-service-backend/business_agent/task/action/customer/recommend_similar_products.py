from typing import Any

from business_agent.domain.messages import BotMessage
from business_agent.domain.state import DialogueState
from business_agent.task.action.base import Action, ActionResult
from business_agent.task.action.customer.shared import fetch_product

class ActionRecommandSimilarProduct(Action):
  name = "action_recommend_similar_products"

  async def run(self, action_kwargs: dict[str, Any], state: DialogueState) -> ActionResult:
    """
    Goal: 调用获取相似商品推荐的接口，并且返回行动的结果对象
    Args:
        action_kwargs:
        state:    
    """
    product_id = state.active_task.slots.get("product_id")
    label = product_id or "这件商品"

    payload = await fetch_product(product_id)
    if payload:
      label = str(payload.get("title") or "") or label

    text = (
      f"我已经收到你对\"{label}\"的相似商品推荐需求。"
      "不过当前版本还没有接入正式的推荐系统，稍后可以继续补上这部分能力。"
    )
    return ActionResult(messages=[BotMessage(text=text)])