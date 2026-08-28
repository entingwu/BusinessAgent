"""
knowledge package initialisation: actually turn the retrieval trace logs on.

Background: the only logging configuration in the repo lived in a few `__main__` debug entry
points. In a server process (uvicorn) the root logger sat at WARNING, so the two logger.info
lines in knowledge/responder.py and knowledge/provider/rag.py that record "hit chunk id +
similarity" printed nothing at all — spec 3.1.2's "record hit chunk ids and similarities
internally" was running empty.

The approach: this package attaches its own StreamHandler to the `business_agent.knowledge`
logger and sets its level, with `propagate = False`, depending on and changing nothing in the
root / uvicorn logging configuration (`api/` is outside this module's scope).
This file runs before any module under `knowledge` is imported, so both the server process and
the CLI entry points are covered.

The level is controlled by KNOWLEDGE_LOG_LEVEL; setting it to WARNING silences the retrieval
logs.
"""
import logging
import sys

from business_agent.config.settings import settings

_HANDLER_TAG = "business_agent.knowledge.trace_handler"


def _configure_logger() -> None:
  """
  Goal: attach a handler of our own to business_agent.knowledge — idempotent, and affecting no
        other logger
  """
  logger = logging.getLogger("business_agent.knowledge")

  level_name = (settings.knowledge_log_level or "INFO").upper()
  logger.setLevel(getattr(logging, level_name, logging.INFO))

  # On a repeated import, do not attach a second handler or every line prints twice
  if any(getattr(handler, "_tag", None) == _HANDLER_TAG for handler in logger.handlers):
    return

  handler = logging.StreamHandler(stream=sys.stdout)
  handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
  handler._tag = _HANDLER_TAG  # type: ignore[attr-defined]
  logger.addHandler(handler)

  # With a handler of its own, stop propagating so a configured root handler does not print it
  # a second time
  logger.propagate = False


_configure_logger()
