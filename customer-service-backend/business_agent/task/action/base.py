import hashlib

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from business_agent.domain.messages import BotMessage
from business_agent.domain.state import DialogueState

@dataclass(slots=True)
class ActionResult:
  messages: list[BotMessage]=field(default_factory=list)
  updated_slots: dict[str, Any]=field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class SlotSpec:
  """
  A slot an action reads. Spec 3.1.5 requires every tool to declare its inputs.
  """
  name: str                    # slot name, one-to-one with the slots in flow_config
  required: bool = True        # whether the action can run without this slot
  description: str = ""        # what this action uses it for


class Action(ABC):
  """
  Base class for every action.

  Beyond run(), a subclass also **declares what it is** — spec 3.1.5 (tier 1) requires every tool
  to declare its name, inputs, outputs and whether it writes. These declarations are not
  documentation, they are meant to be read by code:

  - `reads` / `writes` turn "which slots this action depends on and which it produces" into a
    readable fact, instead of an implicit convention scattered across each action as
    `state.active_task.slots.get(...)`;
  - `is_write` is the prerequisite for spec 3.3.5's ordering flow: a write must be confirmed with
    the user first, and without this flag the engine cannot tell "just checking" from "actually
    placing the order";
  - `idempotency_slots` declares which slots make up the idempotency key (spec B.4, tier 2,
    "add idempotency key declarations"). On a retried write it is what decides "this is the same
    transaction", so clicking twice does not place two orders.

  A read-only action leaves all three empty; the defaults are read-only with no idempotency key.

  Note that nothing in the engine consumes these declarations yet — they are metadata until the
  ordering flow lands. Do not assume `is_write=True` is already protecting anything.
  """
  name: str
  description: str = ""

  # Inputs: which slots this action reads
  reads: tuple[SlotSpec, ...] = ()

  # Outputs: which slots this action writes back. Names only — the shape of the values is the
  # action's own responsibility
  writes: tuple[str, ...] = ()

  # Whether this is a write (it changes state in the business system: placing an order, issuing
  # a refund, chasing a shipment...). Read-only lookups are always False. The engine uses this to
  # decide whether to confirm with the user first.
  is_write: bool = False

  # Which slots make up the idempotency key. Meaningful for writes only.
  # An ordering action might declare ("order_draft_id",), so retrying the same draft never
  # produces a second order.
  idempotency_slots: tuple[str, ...] = ()

  @abstractmethod
  async def run(self, action_kwargs: dict[str, Any], state: DialogueState) -> ActionResult:
    pass

  @classmethod
  def missing_required_slots(cls, state: DialogueState) -> list[str]:
    """
    Goal: check the current state against the `reads` declaration for missing required slots
    Args:
        state: the current dialogue state
    Returns:
        the names of the required slots that are missing; empty when none are
    """
    slots = state.active_task.slots if state.active_task is not None else {}
    # Test for "has no value" rather than falsiness: a quantity of 0, an empty string and False
    # are all legitimate slot values, and a falsy test would read them as unanswered, making the
    # flow ask again for something the user already told us.
    return [
      spec.name for spec in cls.reads
      if spec.required and cls._is_blank(slots.get(spec.name))
    ]

  @staticmethod
  def _is_blank(value: Any) -> bool:
    """Whether a slot counts as unfilled: only None and whitespace-only strings do — 0 and False
    both count as filled."""
    return value is None or (isinstance(value, str) and not value.strip())

  @classmethod
  def idempotency_key(cls, state: DialogueState) -> str | None:
    """
    Goal: build this write's idempotency key from the declared slots.
    Read-only actions, and actions that declare no idempotency_slots, return None.
    If any component slot is empty the result is also None — better to make the caller handle
    that than to produce a key that looks valid while not actually being unique, which is more
    dangerous than having no key at all.
    Args:
        state: the current dialogue state
    Returns:
        the idempotency key string, or None
    """
    if not cls.is_write or not cls.idempotency_slots:
      return None
    slots = state.active_task.slots if state.active_task is not None else {}
    values = [slots.get(name) for name in cls.idempotency_slots]
    if any(cls._is_blank(value) for value in values):
      return None

    # Hash the values rather than concatenating them, which solves three things at once:
    # 1. without escaping, slot values containing a colon let different combinations collapse to
    #    the same key — and uniqueness is this layer's entire purpose;
    # 2. long slots such as an address would push the concatenation past the commerce service's
    #    64-character idempotency_key limit;
    # 3. idempotency keys reach logs and error messages, and hashing keeps the delivery address
    #    out of both.
    # The action name stays as a prefix so a key is still recognisable at a glance when
    # debugging.
    digest = hashlib.sha256(
      "\u0000".join(str(value) for value in values).encode("utf-8")
    ).hexdigest()[:32]
    return f"{cls.name}:{digest}"
