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

if __name__ == '__main__':
  uvicorn.run(app="business_agent.api.app:app", host=settings.app_host, port=settings.app_port)
