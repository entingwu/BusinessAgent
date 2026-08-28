from typing import Any

from business_agent.domain.state import DialogueState
from business_agent.task.action.base import Action, ActionResult, SlotSpec
from business_agent.task.action.customer.shared import fetch_order

class ActionLookupOrderStatus(Action):
  name = "action_lookup_order_status"
  description = "按订单号查询订单状态与金额、商品明细"
  reads = (SlotSpec(name="order_number", description="要查询的订单号"),)
  writes = ("order_status", "order_summary")
  # 只读查询，不改变中台任何状态
  is_write = False

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
      # 与 lookup_logistics 同样的处理：中台的 status_desc 自带句号，
      # 而 YAML 模板也会补一个，直接拼会出现「派往目的地。。」
      "order_status": (payload.get("status_desc") or payload.get("status") or "unknown").rstrip("。."),
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
        parts.append("商品：" + "、".join(titles))
    # 中文行文统一用全角标点，原来用 ASCII 的 "." 和 "," 拼出来是
    # 「订单金额 ￥899.00.商品: 耳机.」这种半中半英的样子
    return "，".join(parts) + "。" if parts else ""
    