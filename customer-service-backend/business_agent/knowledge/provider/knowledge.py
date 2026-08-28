import asyncio
import json
from typing import Any

from business_agent.config.settings import settings
from business_agent.domain.state import DialogueState
from business_agent.infrastructure import http_client
from business_agent.knowledge.provider.provider import KnowledgeChunk, Provider
from business_agent.knowledge.provider.rag import VectorKnowledgeProvider

# Knowledge source types (kept in step with SOURCE_TYPE_* in knowledge/ingest/loader.py)
SOURCE_TYPE_FAQ = "faq"
SOURCE_TYPE_DOCUMENT = "document"
SOURCE_TYPE_API = "api"


def _base_url() -> str:
  """
  Goal: the commerce service base URL, with any trailing slash removed so paths like //orders
        cannot be built
  """
  return settings.commerce_api_base_url.rstrip("/")


class ApiOrderProvider(Provider):
  provider_id = "api.order"

  async def retrival(self, state: DialogueState) -> list[KnowledgeChunk]:
    """
    Call the order API and wrap what it returns into the content of a knowledge chunk.
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
            content="Order and shipping information:\n"
                    + json.dumps(
                {
                    "order_number": order_number,
                    "order": order_payload,
                    "logistics": logistics_payload,
                },
                ensure_ascii=False,
                indent=2,
            ),
            # Live data from a business API is authoritative: it never takes part in
            # similarity-threshold filtering (spec 3.1.1 / acceptance criterion 4)
            chunk_id=f"api.order:{order_number}",
            source_id="api.order",
            source_type=SOURCE_TYPE_API,
            source_title=f"order API {order_number}",
            position=0,
            provider_id="api.order",
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
    Call the product API and wrap what it returns into the content of a knowledge chunk.
    Args:
        state
    Returns:
    """
    product_id = state.focused_object.id
    data: dict[str, Any] = await self._get_product_info_by_id(product_id)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    return [KnowledgeChunk(
        content=f"Product information:\n{text}",
        # Volatile data such as price and stock may only come from the API, never the knowledge
        # base (spec 3.1.1)
        chunk_id=f"api.product:{product_id}",
        source_id="api.product",
        source_type=SOURCE_TYPE_API,
        source_title=f"product API {product_id}",
        position=0,
        provider_id="api.product",
    )]

  async def _get_product_info_by_id(self, product_id: str) -> dict[str, Any]:
      url = f"{_base_url()}/products/{product_id}"
      response = await http_client.http_client.get(url)
      return response.json()["data"]


class RagDefaultProvider(VectorKnowledgeProvider):
  """
  Goal: retrieval over the merchant's documents (returns, refunds, shipping, platform rules and
        other policy documents). Vector Top-K + threshold + metadata filter; a miss returns an
        empty list, and the fallback wording is the responder's decision.
  """
  provider_id = "rag.default"
  source_types = (SOURCE_TYPE_DOCUMENT,)


class FaqDefaultProvider(VectorKnowledgeProvider):
  """
  Goal: FAQ entry retrieval. FAQs are ingested one entry per chunk, so a hit is already a
        complete question-and-answer pair.
  """
  provider_id = "faq.default"
  source_types = (SOURCE_TYPE_FAQ,)
