import asyncio
import json
from typing import Any

from business_agent.config.settings import settings
from business_agent.domain.state import DialogueState
from business_agent.infrastructure import http_client
from business_agent.knowledge.provider.provider import KnowledgeChunk, Provider
from business_agent.knowledge.provider.rag import VectorKnowledgeProvider

# 知识源类型（与 knowledge/ingest/loader.py 的 SOURCE_TYPE_* 保持一致）
SOURCE_TYPE_FAQ = "faq"
SOURCE_TYPE_DOCUMENT = "document"
SOURCE_TYPE_API = "api"


def _base_url() -> str:
  """
  Goal: 获取中台服务地址（去掉末尾斜杠，避免拼出 //orders 这类路径）
  """
  return settings.commerce_api_base_url.rstrip("/")


class ApiOrderProvider(Provider):
  provider_id = "api.order"

  async def retrival(self, state: DialogueState) -> list[KnowledgeChunk]:
    """
    调用订单接口，将查询到该接口的数据封装到知识检索结果对象的content中.
    Args:
        state
    Returns:
    """
    focused_object = state.focused_object
    order_number = focused_object.id

    order_payload, logistics_payload = await asyncio.gather(
        self._fetch_order(order_number),
        self._fetch_logistics(order_number),
    )

    return [
        KnowledgeChunk(
            content="订单与物流信息：\n"
                    + json.dumps(
                {
                    "order_number": order_number,
                    "order": order_payload,
                    "logistics": logistics_payload,
                },
                ensure_ascii=False,
                indent=2,
            ),
            # 实时数据来自业务接口，是权威结果：不参与相似度阈值过滤（规范 3.1.1 / 验收标准 4）
            chunk_id=f"api.order:{order_number}",
            source_id="api.order",
            source_type=SOURCE_TYPE_API,
            source_title=f"订单接口 {order_number}",
            position=0,
        )
    ]

  async def _fetch_order(self, order_number) -> dict[str, Any]:
    url = f"{_base_url()}/orders/{order_number}"
    response = await http_client.http_client.get(url)
    return response.json()["data"]

  async def _fetch_logistics(self, order_number) -> dict[str, Any]:
      url = f"{_base_url()}/orders/{order_number}/logistics"
      response = await http_client.http_client.get(url)
      return response.json().get("data", {})


class ApiProductProvider(Provider):
  provider_id = "api.product"

  async def retrival(self, state: DialogueState) -> list[KnowledgeChunk]:
    """
    调用订单接口，将查询到该接口的数据封装到知识检索结果对象的content中.
    Args:
        state
    Returns:
    """
    product_id = state.focused_object.id
    data: dict[str, Any] = await self._get_product_info_by_id(product_id)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    return [KnowledgeChunk(
        content=f"商品信息:\n{text}",
        # 价格与库存等易变数据只能来自接口，不入知识库（规范 3.1.1）
        chunk_id=f"api.product:{product_id}",
        source_id="api.product",
        source_type=SOURCE_TYPE_API,
        source_title=f"商品接口 {product_id}",
        position=0,
    )]

  async def _get_product_info_by_id(self, product_id: str) -> dict[str, Any]:
      url = f"{_base_url()}/products/{product_id}"
      response = await http_client.http_client.get(url)
      return response.json()["data"]


class RagDefaultProvider(VectorKnowledgeProvider):
  """
  Goal: 商家文档知识检索（退货 / 退款 / 配送 / 平台规则等政策文档）
        向量 Top-K + 阈值 + metadata 过滤，未命中返回空列表，兜底话术由 responder 决定。
  """
  provider_id = "rag.default"
  source_types = (SOURCE_TYPE_DOCUMENT,)


class FaqDefaultProvider(VectorKnowledgeProvider):
  """
  Goal: FAQ 条目检索。FAQ 一条一片入库，命中的就是完整问答对。
  """
  provider_id = "faq.default"
  source_types = (SOURCE_TYPE_FAQ,)
