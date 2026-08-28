"""
Slot-write guard.

This is only for writes from **external sources** — the set_slots the LLM extracted, and values
carried in when the user clicks a card. Neither source is trustworthy: the LLM will sweep an
entity from the previous turn into this turn's slots (measured: right after 「查订单 o30002 的
物流」 the user asks 「看看类似的商品推荐」 and o30002 lands in product_id), and a card can carry
an id that does not match its own type.

`state.set_slots` has three call sites in the repo. The first two must go through the guard, and
the third must not:

1. `CommandProcessor._update_slots` — set_slots extracted by the LLM. **Guarded.**
2. `FlowExecutor._try_set_slots_from_object` — an id carried in by a clicked card. **Guarded.**
   This is the one that gets missed: it bypasses CommandProcessor, so guarding only the first
   site lets a dirty value the command layer dropped be written again through this path —
   measured, the log said "slot dropped" while the bot happily echoed o30002 back.
3. `state.set_slots(action_result.updated_slots)` inside `FlowExecutor` — slots produced by an
   action. **Never guard this one.** `Flow.slots` is the list of slots *to collect from the user*,
   derived from the collect steps, and order_status / order_summary / tracking_number /
   logistics_company / logistics_status / logistics_traces are all written back by actions —
   not one of them is on that list. Measured, guarding it does not drop some of them, it drops
   all four, and the shipping reply degrades to the stump
   "Order o30002 is being delivered by , tracking number . Current progress: ." 
"""

import logging
import re
from typing import Any

from business_agent.task.flows.flows import Flow

logger = logging.getLogger(__name__)


def matches_pattern(pattern: str | None, value: Any) -> bool:
  """
  Goal: check a slot value against the declared format
  Args:
      pattern: the regex from the slot definition; None means unconstrained
      value: the value about to be written
  Returns:
      whether to let it through
  """
  if pattern is None:
    return True
  if not isinstance(value, str):
    # A format is declared but the value is not a string — treat it as a mismatch rather than
    # coercing it implicitly
    return False
  return re.fullmatch(pattern, value.strip()) is not None


def accept_slots(flow: Flow | None,
                 slots: dict[str, Any],
                 source: str) -> dict[str, Any]:
  """
  Goal: filter out slots that do not belong to this flow or do not match their format, and
        return the subset that may be written

  Dropping rather than rejecting the whole turn: the user's intent is usually right, and only
  the slot value swept along with it is wrong. Once dropped, the collect step asks the user for
  it normally, which beats sending the whole turn to clarification. Every drop is logged — never
  silent.
  Args:
      flow: the current flow; None means nothing is running, so the slots have nowhere to go
      slots: the slots about to be written
      source: origin label, used only in logs (e.g. "set_slots command" / "card backfill")
  Returns:
      the subset that may be written
  """
  if flow is None:
    logger.warning("[%s] dropped slots %s: no business flow is running to write them to",
                   source, sorted(slots))
    return {}

  accepted: dict[str, Any] = {}
  for slot_name, value in slots.items():
    slot_definition = flow.slots.get(slot_name)

    # Gate one: the slot name does not belong to this flow
    if slot_definition is None:
      logger.warning(
        "[%s] dropped slot %s=%r: flow %s only declares %s",
        source, slot_name, value, flow.id, sorted(flow.slots)
      )
      continue

    # Gate two: the format does not match
    if not matches_pattern(slot_definition.pattern, value):
      logger.warning(
        "[%s] dropped slot %s=%r: does not match format %s (flow %s)",
        source, slot_name, value, slot_definition.pattern, flow.id
      )
      continue

    # Write the stripped value, not the original. A mismatch between the two breaks this
    # guard's own invariant — a value that was let through and persisted must itself pass the
    # guard again. Measured consequence: " p2016 " matched after stripping and was let through,
    # but the padded original was persisted, downstream quote() turned the spaces into %20, the
    # commerce service returned 404, and the reply echoed the id " p2016 " instead of a product
    # name. Leading and trailing whitespace can only be noise from the LLM or a card, so
    # removing it loses nothing.
    accepted[slot_name] = value.strip() if isinstance(value, str) else value

  return accepted
