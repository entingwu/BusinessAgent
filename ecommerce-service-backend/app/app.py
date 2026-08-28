from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import router
from app.database import engine


@asynccontextmanager
async def lifespan(_: FastAPI):
  """
  fastapi lifespan callback function
  Returns
  """
  # 1. Initalize all resources
  # engine / SessionLocal 在 app.database 导入时就已创建，这里无需再初始化
  print("Start app, to execute callback function")

  # 2. Execute router request (/api)
  yield

  # 3. Release all resources.
  print("Stop app, to execute callback function")
  engine.dispose()


openapi_tags = [
    {
        "name": "System",
        "description": "Service availability and basic health checks.",
    },
    {
        "name": "Users",
        "description": "A user's order list and product list.",
    },
    {
        "name": "Orders",
        "description": "Order detail, order status, shipment tracking, and order-related operations.",
    },
    {
        "name": "Products",
        "description": "Product search and product detail.",
    },
]


app = FastAPI(
    title="Commerce Service",
    version="0.1.0",
    description="Demo commerce service providing orders, logistics, products and order operations for the customer-service project.",
    lifespan=lifespan,
    openapi_tags=openapi_tags,
)

app.include_router(router)
