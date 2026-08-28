"""
Define Fast API instance
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from business_agent.api import chat_router, knowledge_router
from business_agent.infrastructure.db_client import dispose_engine, init_db_engine
from business_agent.infrastructure.http_client import init_http_client, disposed_http_client
from business_agent.observability import configure_logging

@asynccontextmanager
async def lifespan(_: FastAPI):
  # 1. Initialize all resources
  #    Logging is configured first, or none of the log lines from the rest of startup appear
  configure_logging()
  init_db_engine()
  init_http_client()

  # 2. Execute router request
  yield

  # 3. Release all resources
  await dispose_engine()
  await disposed_http_client()


app = FastAPI(description="Start chatbot Fast API instance", lifespan=lifespan)

app.include_router(chat_router.router)
app.include_router(knowledge_router.router)
