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
        "name": "系统",
        "description": "服务可用性与基础检查接口。",
    },
    {
        "name": "用户",
        "description": "查询用户相关的订单列表和商品列表。",
    },
    {
        "name": "订单",
        "description": "订单详情、订单状态、物流信息，以及订单相关操作请求。",
    },
    {
        "name": "商品",
        "description": "商品检索与商品详情查询接口。",
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
