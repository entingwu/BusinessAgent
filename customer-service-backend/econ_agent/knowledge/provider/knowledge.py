import asyncio
import json
from typing import Any

from econ_agent.config.settings import settings
from econ_agent.domain.state import DialogueState
from econ_agent.infrastructure import http_client
from econ_agent.knowledge.provider.provider import KnowledgeChunk, Provider


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
            )
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
    return [KnowledgeChunk(content=f"商品信息:\n{text}")]

  async def _get_product_info_by_id(self, product_id: str) -> dict[str, Any]:
      url = f"{_base_url()}/products/{product_id}"
      response = await http_client.http_client.get(url)
      return response.json()["data"]


class RagDefaultProvider(Provider):
  provider_id = "rag.default"

  async def retrival(self, state: DialogueState) -> list[KnowledgeChunk]:
    """
    调用知识库执行
    Args:
        state
    Returns:
    """
    return  [KnowledgeChunk(content="暂未对接FAQ,无法查询到有效的知识内容")]


class FaqDefaultProvider(Provider):
  provider_id = "faq.default"

  async def retrival(self, state: DialogueState) -> list[KnowledgeChunk]:
    """
    调用常见问题集文档自行接入
    Args:
        state
    Returns:
    """
    return [KnowledgeChunk(content="暂未对接RAG,无法查询到有效的知识内容")]