"""
Goal: 
From specific Action object from Register center, 
find run for action object
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from business_agent.domain.state import DialogueState
from business_agent.observability import brief
from business_agent.task.action.base import ActionResult
from business_agent.task.action.register import ActionRegister

logger = logging.getLogger(__name__)

@dataclass(slots=True)
class ActionCall:
  action_name: str
  action_kwargs: dict[str, Any] = field(default_factory=dict)


class ActionRunner:

  def __init__(self, action_register: ActionRegister):
    self.action_register = action_register


  async def run(self, action_call: ActionCall, state: DialogueState) -> ActionResult:
    """
    Called by the flow executor whenever it advances onto a step of type action.
    """
    action = self.action_register.get_action(action_call.action_name)

    # Spec 5.3, tier 1: record each tool call's inputs, outputs and duration.
    # It lives on the runner rather than in each action because this is the one point every
    # action must pass through — instrumenting it once covers all of them, and a new action gets
    # it automatically instead of relying on someone remembering.
    started_at = time.perf_counter()
    try:
      action_result = await action.run(action_call.action_kwargs, state)
    except Exception as error:
      logger.warning(
        "action name=%s sender_id=%s elapsed_ms=%.0f outcome=error error=%s",
        action_call.action_name, state.sender_id,
        (time.perf_counter() - started_at) * 1000, brief(error),
      )
      raise

    elapsed_ms = (time.perf_counter() - started_at) * 1000
    logger.info(
      "action name=%s sender_id=%s elapsed_ms=%.0f args=%s slots_in=%s "
      "messages=%d cards=%d slots_out=%s",
      action_call.action_name, state.sender_id, elapsed_ms,
      brief(action_call.action_kwargs),
      # The real inputs live in the slots, not in action_kwargs: the order lookup, shipment
      # lookup and recommendation actions all read from state.active_task.slots, so logging only
      # action_kwargs gives args={} every time and cannot answer "which order number was this
      # actually looking up" — which is the whole reason 5.3 asks for the log.
      brief(self._slots_of(state)),
      len(action_result.messages),
      sum(len(message.cards) for message in action_result.messages),
      brief(action_result.updated_slots),
    )
    return action_result

  def _slots_of(self, state: DialogueState) -> dict[str, Any]:
    return dict(state.active_task.slots) if state.active_task is not None else {}