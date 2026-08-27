from typing import Any

from business_agent.domain.state import DialogueState
from business_agent.task.action.base import Action, ActionResult
from business_agent.task.action.customer.shared import fetch_order

class ActionLookupOrderStatus(Action):
  name = "action_lookup_order_status"

  async def run(self, action_kwargs: dict[str, Any], state: DialogueState) -> ActionResult:
    """
    Goal: 调用获取订单信息的接口，并且返回行动的结果对象
    """
    # 1. 获取请求参数
    order_number = state.active_task.slots.get('order_number')

    # 2. 给中台服务发送获取订单状态的请求
    payload = await fetch_order(order_number)

    if payload is None:
      return ActionResult(updated_slots={
        "order_status": "订单状态未知",
        "order_summary": "暂时无法拿到该订单信息"
      })

    # 3. 封装到ActionResult的slots中返回
    return ActionResult(updated_slots={
      "order_status": payload.get("status_desc") or payload.get("status") or "unknown",
      "order_summary": self._build_order_summary(payload),
    })

  def _build_order_summary(self, payload: dict[str, Any]) -> str:
    parts = []
    if payload.get("amount"):
      parts.append(f"订单金额 ￥{payload['amount']}")
    items = payload.get("items") or []

    if items:
      titles = [str(item.get("title") or "").strip() 
                for item in items[:2] if item.get("title")]
      if titles:
        parts.append("商品： " + ",".join(titles))
    return ".".join(parts) + "." if parts else ""
    