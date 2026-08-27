from datetime import datetime
from typing import Any

from business_agent.domain.state import DialogueState
from business_agent.task.action.base import Action, ActionResult
from business_agent.task.action.customer.shared import fetch_logistics

class ActionLookupLogistics(Action):
  name = "action_lookup_logistics"

  async def run(self, action_kwargs: dict[str, Any], state: DialogueState) -> ActionResult:
    """
    Goal: 调用获取订单物流信息的接口 并且返回行动的结果对象
    Args:
        action_kwargs:
        state:

    Returns:

    """
    # 1. 获取请求参数
    order_number = state.active_task.slots.get('order_number')

    # 2. 给中台服务发送获取订单状态的请求
    payload = await fetch_logistics(order_number)

    # 3. 封装到ActionResult的slots中返回
    if payload is None:
        return ActionResult(updated_slots={
            "tracking_number": "未知",
            "logistics_company": "未知",
            "logistics_status": "暂时无法查到物流信息，请稍后再试",
            "logistics_traces": "",
        })

    return ActionResult(updated_slots={
        "tracking_number": payload.get("tracking_number") or "未知",
        "logistics_company": payload.get("logistics_company") or "未知",
        # 中台的 status_desc 本身以句号结尾，而模板也会补一个，直接拼会出现「。。」。
        # 在写入槽位时统一去掉句尾标点，句号交给模板加
        "logistics_status": (payload.get("status_desc") or payload.get("status") or "未知").rstrip("。."),
        "logistics_traces": self._build_traces(payload.get("traces")),
    })

  # 展示的轨迹条数上限：接口按时间倒序返回，取最近的几条即可，
  # 全量铺开会把一条回复撑成一堵墙
  MAX_TRACES = 5

  def _build_traces(self, traces: Any) -> str:
    """
    Goal: 把中台返回的物流轨迹节点渲染成可直接展示的多行文本
    Args:
        traces: 中台 /orders/{id}/logistics 返回的 data.traces，形如
                [{"time": "2025-02-20T08:30:00", "desc": "快件已到达..."}, ...]
    Returns:
        每行一个节点的字符串；无轨迹时返回空串，由模板决定不展示这一段
    """
    if not isinstance(traces, list):
      return ""

    lines = []
    for trace in traces[:self.MAX_TRACES]:
      if not isinstance(trace, dict):
        continue
      desc = str(trace.get("desc") or "").strip()
      if not desc:
        continue
      lines.append(f"· {self._format_time(trace.get('time'))} {desc}".strip())

    return "\n".join(lines)

  def _format_time(self, raw: Any) -> str:
    """
    Goal: 把 ISO 时间压缩成 "02-20 08:30"。年份对物流节点没有信息量，秒也没有。
          解析不了就原样返回，宁可难看也不要丢掉时间信息
    """
    if not raw:
      return ""
    try:
      return datetime.fromisoformat(str(raw)).strftime("%m-%d %H:%M")
    except ValueError:
      return str(raw)