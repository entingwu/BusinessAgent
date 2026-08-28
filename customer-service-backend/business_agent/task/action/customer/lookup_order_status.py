from typing import Any

from business_agent.domain.state import DialogueState
from business_agent.task.action.base import Action, ActionResult, SlotSpec
from business_agent.task.action.customer.shared import fetch_order

class ActionLookupOrderStatus(Action):
  name = "action_lookup_order_status"
  description = "Look up an order's status, amount and line items by order number"
  reads = (SlotSpec(name="order_number", description="Order number to look up"),)
  writes = ("order_status", "order_summary")
  # A read-only query; it changes nothing in the commerce service
  is_write = False

  async def run(self, action_kwargs: dict[str, Any], state: DialogueState) -> ActionResult:
    """
    Goal: call the order-info API and return the action result
    """
    # 1. Read the request parameters
    order_number = state.active_task.slots.get('order_number')

    # 2. Send the request to the commerce service
    payload = await fetch_order(order_number)

    if payload is None:
      return ActionResult(updated_slots={
        "order_status": "unknown",
        "order_summary": "I could not retrieve this order right now."
      })

    # 3. Wrap the answer into the ActionResult slots
    return ActionResult(updated_slots={
      # Same treatment as lookup_logistics: the commerce service's status_desc already ends in a
      # full stop and the YAML template adds another, so concatenating them doubles it.
      "order_status": (payload.get("status_desc") or payload.get("status") or "unknown").rstrip("。."),
      "order_summary": self._build_order_summary(payload),
    })

  def _build_order_summary(self, payload: dict[str, Any]) -> str:
    parts = []
    if payload.get("amount"):
      parts.append(f"Order total ￥{payload['amount']}")
    items = payload.get("items") or []

    if items:
      titles = [str(item.get("title") or "").strip()
                for item in items[:2] if item.get("title")]
      if titles:
        parts.append("Items: " + ", ".join(titles))
    # The Chinese values from the commerce service use fullwidth punctuation, so joining them
    # with ASCII "." and "," produced the half-and-half look of
    # 「订单金额 ￥899.00.商品: 耳机.」
    return "，".join(parts) + "。" if parts else ""
    