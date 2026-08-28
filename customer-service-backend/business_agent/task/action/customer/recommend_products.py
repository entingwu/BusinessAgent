"""
Recommend products from stated preferences. Spec 3.3.3, acceptance criterion 5.

How this differs from recommend_similar_products, which it replaced: that one took a single
product_id and returned a placeholder sentence. This one collects preference dimensions, really
queries the commerce service, and returns candidates as a card list.
"""

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from business_agent.domain.messages import BotMessage, FocusedObject
from business_agent.domain.state import DialogueState
from business_agent.task.action.base import Action, ActionResult, SlotSpec
from business_agent.task.action.customer.shared import search_products

logger = logging.getLogger(__name__)

# Slot name -> commerce attribute name. The commerce service whitelists attribute names and
# returns 400 for anything else, so the mapping is written out here rather than guessed from the
# slot name.
SLOT_TO_ATTR: dict[str, str] = {
  "product_use_case": "use_case",
  "product_style": "style",
  "product_size": "size",
}

# How many cards at most. Too many is the same as not filtering — the user still has to read
# through them. Spec 3.3.3 asks for convergence within two rounds, and only a small candidate set
# per round actually converges.
MAX_CARDS = 4

# Candidate values for the refinement buttons. They offer values, never dimension names — see
# the comment on wait_more in user_flows.yml. The values come from the allowed list in each
# slot's description, which the planner recognises.
#
# STYLE_VALUES is **deliberately kept in Chinese** and was not englishified with the UI: these
# values are written verbatim into the product_style slot and then sent to the commerce service
# as an attribute filter, and what the commerce service stores is "style": "极简".
# Translating them here without changing the commerce seed data guarantees an empty result on
# click — and an empty result is indistinguishable from "there really is no matching product",
# so the failure is silent. Changing them means changing four independent facts at once, which is
# its own piece of work.
# BUDGET_VALUES can be translated, because _parse_budget only extracts digits and never matches
# strings.
STYLE_VALUES = ("极简", "商务", "电竞", "北欧")
BUDGET_VALUES = ("Under 300", "Under 500", "Under 1000")


class ActionRecommendProducts(Action):
  name = "action_recommend_products"
  description = ("Search commerce products by use case, budget and style, returning product "
                 "cards plus quick replies for narrowing down")
  # None of the preference slots are required: a missing one just means one fewer filter, and
  # candidates can still be returned. Marking them required would stall the flow the moment a
  # user says "just recommend me something".
  reads = (
    SlotSpec(name="product_use_case", required=False,
             description="Use case; maps to the commerce use_case attribute filter"),
    SlotSpec(name="product_style", required=False,
             description="Style preference; maps to the commerce style attribute filter"),
    # product_size is **deliberately not listed**, even though SLOT_TO_ATTR maps it:
    # the mapping code is live, but its input is dead. product_size is neither declared in
    # user_flows.yml's slots nor gathered by any collect step, so slot_guard drops it before the
    # write (measured log line: "dropped slot product_size='大号': flow product_recommendation
    # only declares ..."), and at runtime slots.get("product_size") is always None.
    #
    # It was briefly added to `reads` on the grounds that "passing this slot to the action really
    # does change the result set" — but that measurement called the action directly and bypassed
    # the guard, so it did not reflect the real pipeline. Declaring a slot that can never take
    # effect makes `reads` just as untrue as omitting one that does.
    #
    # To make it genuinely live, first declare it as a collectable slot in user_flows.yml
    # (commerce carries both S/M/L and 标准/大号/小号).
    SlotSpec(name="product_budget", required=False,
             description="Budget cap; the digits are parsed out and used as max_price"),
    SlotSpec(name="product_round", required=False,
             description="Which refinement round this is; used to vary the quick replies"),
  )
  writes = ("product_round",)
  # A read-only query against commerce; it changes no business state
  is_write = False

  async def run(self, action_kwargs: dict[str, Any], state: DialogueState) -> ActionResult:
    """
    Goal: search products from the preference slots collected so far, returning a card list plus
          quick replies
    """
    slots = state.active_task.slots if state.active_task is not None else {}

    attrs = {attr: slots.get(slot) for slot, attr in SLOT_TO_ATTR.items() if slots.get(slot)}
    max_price = self._parse_budget(slots.get("product_budget"))

    data = await search_products(
      attrs=attrs,
      max_price=max_price,
      # Spec 3.3.3, "stock is queried live and never cached": only recommend what is in stock.
      # Out-of-stock items are left to the substitute logic behind "show me others".
      in_stock=True,
      limit=MAX_CARDS,
    )

    # An API failure and "there really is no matching product" must stay distinguishable. The
    # first must never be reported as "I could not find any" — that is telling the user our own
    # outage as though it were a business conclusion.
    if data is None:
      logger.warning("recommend_products search_failed attrs=%s max_price=%s", attrs, max_price)
      # Write the counter here too. Miss any one of the three return paths and wait_more's exit
      # condition can never hold — while commerce stays down, every round goes back to recommend
      # and the flow can never leave. That is exactly what the "the loop needs an exit" note in
      # user_flows.yml is guarding against.
      return ActionResult(
        messages=[BotMessage(
          text="The product service is not responding right now, so I cannot look this up. "
               "You can ask me again shortly, or I can hand you to a human agent.",
          suggestions=["Try again later", "Talk to a human"],
        )],
        updated_slots={"product_round": self._next_round(slots)},
      )

    items = data.get("items") or []
    if not items:
      # An empty result needs a way out more than a full one does: with no buttons, the user is
      # left to work out how to change the criteria on their own
      return ActionResult(
        messages=[BotMessage(
          text=self._no_match_text(attrs, max_price),
          suggestions=self._refine_suggestions(slots),
        )],
        updated_slots={"product_round": self._next_round(slots)},
      )

    cards = [self._to_card(item) for item in items]
    total = data.get("total") or len(items)
    return ActionResult(
      messages=[BotMessage(
        text=self._headline(attrs, max_price, shown=len(cards), total=total),
        cards=cards,
        suggestions=self._refine_suggestions(slots),
      )],
      updated_slots={"product_round": self._next_round(slots)},
    )

  def _next_round(self, slots: dict[str, Any]) -> str:
    """The refinement round. wait_more uses it to decide whether to loop back to recommend — the
    loop has to have an exit."""
    try:
      return str(int(slots.get("product_round") or 0) + 1)
    except (TypeError, ValueError):
      return "1"

  def _refine_suggestions(self, slots: dict[str, Any]) -> list[str]:
    """
    Goal: offer refinement options that genuinely change the next search

    Only values not already chosen — listing the currently selected style again means clicking it
    changes nothing, which is exactly why these buttons were previously judged dead.
    """
    current_style = str(slots.get("product_style") or "")
    suggestions = [style for style in STYLE_VALUES if style != current_style][:2]

    current_budget = str(slots.get("product_budget") or "")
    tighter = next((budget for budget in BUDGET_VALUES if budget != current_budget), None)
    if tighter:
      suggestions.append(tighter)

    suggestions.append("No thanks")
    return suggestions

  def _to_card(self, item: dict[str, Any]) -> FocusedObject:
    """
    Goal: a commerce product item -> the business-object card from appendix E.1

    What goes into attributes is dictated by the front-end card: it reads price, cover_url and
    description, falling back to showing the price when description is absent. So stock status
    and the key spec are folded into description, which is what makes "includes live stock"
    actually visible in the UI.
    """
    attributes = item.get("attributes") or {}
    return FocusedObject(
      id=str(item.get("product_id") or ""),
      title=str(item.get("title") or ""),
      type="product",
      attributes={
        "price": item.get("price"),
        "cover_url": item.get("cover_url"),
        "description": self._describe(item, attributes),
        "stock_status": item.get("stock_status"),
        **{key: value for key, value in attributes.items() if key in ("use_case", "style", "color", "size")},
      },
    )

  def _describe(self, item: dict[str, Any], attributes: dict[str, Any]) -> str:
    parts = [str(item.get("stock_status") or "").strip()]
    spec = str(attributes.get("spec") or "").strip()
    if spec:
      # spec is free text and can be long; the card has no room, so take the first segment
      parts.append(spec.split("/")[0].strip())
    return " · ".join(part for part in parts if part)

  def _parse_budget(self, raw: Any) -> float | None:
    """
    Goal: parse inputs like "500", "500 yuan" or "under 500" into a price cap.
          Unparseable input returns None — better to apply no filter than to filter every
          candidate away because the parse went wrong.
    """
    if raw is None:
      return None
    digits = "".join(char for char in str(raw) if char.isdigit() or char == ".")
    if not digits:
      return None
    try:
      return float(Decimal(digits))
    except (InvalidOperation, ValueError):
      return None

  def _headline(self, attrs: dict[str, str], max_price: float | None, *, shown: int, total: int) -> str:
    condition = self._condition_text(attrs, max_price)
    more = f" ({total} in total, showing {shown})" if total > shown else ""
    return f"Here is what I found {condition}{more}:"

  def _no_match_text(self, attrs: dict[str, str], max_price: float | None) -> str:
    # Spec 3.3.3: "say so honestly when nothing matches; never invent a product"
    return (f"I could not find any products {self._condition_text(attrs, max_price)}. "
            "Want to raise the budget, or try a different style?")

  def _condition_text(self, attrs: dict[str, str], max_price: float | None) -> str:
    # Labels in English; the values stay as commerce stores them (see the STYLE_VALUES note above)
    labels = {"use_case": "use case", "style": "style", "size": "size"}
    parts = [f"{labels.get(key, key)} {value}" for key, value in attrs.items()]
    if max_price is not None:
      parts.append(f"under {max_price:.0f}")
    return "for " + ", ".join(parts) if parts else "matching your request"
