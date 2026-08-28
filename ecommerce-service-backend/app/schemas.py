from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class ApiResponse(BaseModel):
    code: int = 0
    message: str = "ok"
    data: Any


class OrderSummaryData(BaseModel):
    order_id: str
    title: str
    status: str
    amount: Decimal
    created_at: datetime
    cover_url: str | None = None


class ProductSummaryData(BaseModel):
    product_id: str
    title: str
    price: Decimal
    cover_url: str | None = None


class UserOrdersData(BaseModel):
    user_id: str
    orders: list[OrderSummaryData]


class UserProductsData(BaseModel):
    user_id: str
    products: list[ProductSummaryData]


class OrderItemData(BaseModel):
    product_id: str
    title: str
    quantity: int
    price: Decimal


class OrderDetailData(BaseModel):
    order_id: str
    status: str
    status_desc: str
    amount: Decimal
    created_at: datetime
    receiver_name: str
    receiver_phone_masked: str
    receiver_address: str
    items: list[OrderItemData]


class OrderStatusData(BaseModel):
    order_id: str
    status: str
    status_desc: str


class LogisticsTraceData(BaseModel):
    time: datetime
    desc: str


class LogisticsData(BaseModel):
    order_id: str
    logistics_company: str
    tracking_number: str
    status: str
    status_desc: str
    traces: list[LogisticsTraceData]


class ProductData(BaseModel):
    product_id: str
    title: str
    description: str
    price: Decimal
    stock_status: str
    stock_quantity: int
    cover_url: str | None = None
    attributes: dict[str, Any]


class ProductSearchItemData(BaseModel):
    product_id: str
    title: str
    price: Decimal
    cover_url: str | None = None
    stock_status: str
    stock_quantity: int
    # stock_status 是 VARCHAR 字符串，语义判定收在中台，调用方不必自己猜哪些取值算有货
    in_stock: bool
    attributes: dict[str, Any]


class ProductSearchData(BaseModel):
    items: list[ProductSearchItemData]
    total: int
    limit: int
    offset: int
    has_more: bool


class UrgeShippingRequest(BaseModel):
    submitted_by: str = Field(default="system")
    note: str = Field(default="Customer would like the order shipped sooner")


class RefundRequestBody(BaseModel):
    submitted_by: str = Field(default="system")
    reason: str


class OperationResultData(BaseModel):
    request_type: str
    request_id: str
    order_id: str
    status: str
    status_desc: str


class CreateOrderItemRequest(BaseModel):
    product_id: str
    quantity: int = Field(ge=1, le=99, description="Quantity, 1-99")


class CreateOrderRequest(BaseModel):
    """
    Request body for creating an order. Spec 3.3.5: collect SKU, quantity, address and
    delivery method, confirm, then create.
    Payment is out of scope here: a new order is created as Awaiting payment, and its payment
    status is written back later by the business platform.
    """
    user_id: str
    items: list[CreateOrderItemRequest] = Field(min_length=1, max_length=20)
    receiver_name: str = Field(min_length=1, max_length=64)
    receiver_phone: str = Field(min_length=1, max_length=32, description="Full phone number; masked server-side before it is stored")
    receiver_address: str = Field(min_length=1, max_length=255)
    delivery_method: str = Field(default="Standard shipping", max_length=32)
    # 幂等键由调用方生成（对话侧应当一次下单会话固定一个 key）。
    # 同一个 key 重复提交只会产生一笔订单，重复请求原样返回首次结果。
    idempotency_key: str = Field(min_length=8, max_length=64)


class CreateOrderResultData(BaseModel):
    order_id: str
    status: str
    status_desc: str
    amount: Decimal
    delivery_method: str
    created_at: datetime
    items: list[OrderItemData]
    # 这一笔是不是幂等重放。true 表示订单此前已创建，本次没有产生新订单，
    # 调用方据此区分「下单成功」与「重复提交」，不要把重放当成第二笔成交。
    idempotent_replay: bool
