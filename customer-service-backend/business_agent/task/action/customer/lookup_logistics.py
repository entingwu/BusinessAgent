from datetime import datetime
from typing import Any

from business_agent.domain.state import DialogueState
from business_agent.task.action.base import Action, ActionResult, SlotSpec
from business_agent.task.action.customer.shared import fetch_logistics

class ActionLookupLogistics(Action):
  name = "action_lookup_logistics"
  description = ("Look up the carrier, tracking number, current progress and tracking events "
                 "for an order")
  reads = (SlotSpec(name="order_number", description="Order number to track"),)
  writes = ("tracking_number", "logistics_company", "logistics_status", "logistics_traces")
  is_write = False

  async def run(self, action_kwargs: dict[str, Any], state: DialogueState) -> ActionResult:
    """
    Goal: call the shipping-info API and return the action result
    Args:
        action_kwargs:
        state:

    Returns:

    """
    # 1. Read the request parameters
    order_number = state.active_task.slots.get('order_number')

    # 2. Send the request to the commerce service
    payload = await fetch_logistics(order_number)

    # 3. Wrap the answer into the ActionResult slots
    if payload is None:
        return ActionResult(updated_slots={
            "tracking_number": "unknown",
            "logistics_company": "unknown",
            "logistics_status": "I could not retrieve the shipping information right now — please try again shortly",
            "logistics_traces": "",
        })

    return ActionResult(updated_slots={
        "tracking_number": payload.get("tracking_number") or "unknown",
        "logistics_company": payload.get("logistics_company") or "unknown",
        # The commerce service's status_desc already ends in a full stop and the template adds
        # another, so concatenating them produces a doubled stop. Strip the trailing punctuation
        # when writing the slot and leave the stop to the template.
        "logistics_status": (payload.get("status_desc") or payload.get("status") or "unknown").rstrip("。."),
        "logistics_traces": self._build_traces(payload.get("traces")),
    })

  # Cap on how many tracking events to show. The API returns them newest first, so the most
  # recent few are enough — laying all of them out turns one reply into a wall of text.
  MAX_TRACES = 5

  def _build_traces(self, traces: Any) -> str:
    """
    Goal: render the tracking events from the commerce service into ready-to-display lines
    Args:
        traces: data.traces as returned by the commerce service's /orders/{id}/logistics,
                shaped like [{"time": "2025-02-20T08:30:00", "desc": "..."}, ...]
    Returns:
        one event per line; an empty string when there are none, which is how the template knows
        to omit the section
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
    Goal: compress an ISO timestamp to "02-20 08:30". The year carries no information for a
          tracking event, and neither do the seconds.
          Anything unparseable is returned as-is — better ugly than losing the timestamp.
    """
    if not raw:
      return ""
    try:
      return datetime.fromisoformat(str(raw)).strftime("%m-%d %H:%M")
    except ValueError:
      return str(raw)