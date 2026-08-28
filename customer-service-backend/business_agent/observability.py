"""
Central configuration for structured logging. Spec 5.3, observability, tier 1.

Background: there was no central configuration anywhere in the repo — intent recognition used
print, the slot guard used logging.warning with nobody configuring a handler, and the retrieval
path had the knowledge package attach its own. In a server process the root logger sat at
WARNING, so nothing but the prints was visible.

The approach: attach one handler to the `business_agent` logger, called from the API layer's
lifespan. `business_agent.knowledge` attaches its own handler with propagate=False, so the two
never double-print, and its level stays under KNOWLEDGE_LOG_LEVEL separately — retrieval logging
is voluminous and needs to be silenceable on its own.

Fields are packed into the message as key=value rather than JSON: at this size people read these
logs far more often than machines parse them, and key=value both greps well and scans at a
glance. Swapping in a different formatter when a log system arrives leaves every call site
untouched.
"""

import logging
import sys

from business_agent.config.settings import settings

_HANDLER_TAG = "business_agent.app_handler"
_LOGGER_NAME = "business_agent"


def configure_logging() -> None:
  """
  Goal: attach the handler to business_agent. Idempotent — modules get imported more than once,
        and two handlers would print every line twice.
  """
  logger = logging.getLogger(_LOGGER_NAME)
  level_name = (settings.log_level or "INFO").upper()
  logger.setLevel(getattr(logging, level_name, logging.INFO))

  if any(getattr(handler, "_tag", None) == _HANDLER_TAG for handler in logger.handlers):
    return

  handler = logging.StreamHandler(stream=sys.stdout)
  handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
  handler._tag = _HANDLER_TAG  # type: ignore[attr-defined]
  logger.addHandler(handler)

  # With its own handler it stops propagating, so uvicorn's root handler does not print it again
  logger.propagate = False


def brief(value: object, limit: int = 400) -> str:
  """
  Goal: squeeze arguments and results down to one readable line

  Dumping a full product list or chunk body into the log drowns the useful information, and both
  are already queryable from retrieval_traces or the commerce service. What a log needs is "what
  was called, how long it took, did it work" — not the data itself.

  The cap is 400 rather than something shorter because a TurnPlan measures 208 characters in
  practice, and an earlier limit of 120 cut off exactly the `SetSlotsCommand(...)` part — the one
  part that matters when working out which slot the LLM put an order number into. The truncation
  point belongs where information density is low, not wherever a round number falls.
  """
  text = str(value)
  return text if len(text) <= limit else f"{text[:limit]}…({len(text)} chars)"
