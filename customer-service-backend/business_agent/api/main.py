"""
Start uvicorn Web Service
"""

import sys
from pathlib import Path

# main.py -> api -> business_agent -> customer-service-backend
PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
  sys.path.insert(0, str(PROJECT_DIR))

import uvicorn

from business_agent.config.settings import settings
from business_agent.infrastructure.embedding import warmup_embedding_backend
from business_agent.observability import configure_logging

if __name__ == '__main__':
  configure_logging()

  # Warming up has to happen **before** uvicorn.run(), and cannot move into lifespan.
  #
  # The local embedding backend (BGE-M3) forks child processes while loading, and lifespan already
  # runs inside uvloop's event loop. Forking there corrupts the loop's file-descriptor state, and
  # measured, it takes the whole process down with SIGSEGV (top of stack:
  # kevent -> uv__io_poll -> uvloop Loop._run).
  #
  # The symptom is very hard to attribute: the service starts normally, the first knowledge
  # question is even answered correctly, and then the process simply vanishes — no traceback, no
  # shutdown log, the port just goes free. The system crash report is the only evidence.
  #
  # Here the fork happens before the event loop exists, so the conflict cannot arise. It also
  # moves the model load out of the first knowledge request, which measured 15.8s.
  # With the DashScope backend this is a no-op and costs nothing.
  warmup_embedding_backend()

  uvicorn.run(app="business_agent.api.app:app", host=settings.app_host, port=settings.app_port)
