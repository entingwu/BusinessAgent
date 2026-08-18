"""
Start uvicorn Web Service
"""

import sys
from pathlib import Path

# main.py -> api -> atguigu -> customer-service-backend
PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
  sys.path.insert(0, str(PROJECT_DIR))

import uvicorn

from atguigu.config.settings import settings

if __name__ == '__main__':
  uvicorn.run(app="atguigu.api.app:app", host=settings.app_host, port=settings.app_port)
