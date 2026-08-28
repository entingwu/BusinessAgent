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

  # 预热必须在 uvicorn.run() **之前**，不能放进 lifespan。
  #
  # 本地 Embedding 后端（BGE-M3）加载时会 fork 子进程，而 lifespan 已经跑在
  # uvloop 的事件循环里；在那里 fork 会破坏事件循环的 fd 状态，实测把整个进程
  # 打成 SIGSEGV（栈顶是 kevent → uv__io_poll → uvloop Loop._run）。症状极难
  # 归因：服务正常启动、第一个知识问题也正常答完，然后进程凭空消失——没有
  # traceback、没有 shutdown 日志，只有系统崩溃报告里有线索。
  #
  # 放在这里，fork 发生在事件循环存在之前，冲突不成立；顺带把首个知识请求的
  # 模型加载耗时（实测 15.8s）挪到启动期。
  # 用 DashScope 后端时它是空转，不产生任何代价。
  warmup_embedding_backend()

  uvicorn.run(app="business_agent.api.app:app", host=settings.app_host, port=settings.app_port)
