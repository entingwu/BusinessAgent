from typing import Any
from urllib.parse import quote

from econ_agent.config.settings import settings
from econ_agent.infrastructure import http_client


def _base_url() -> str:
   """
   Goal: 获取中台服务地址
   """
   return settings.commerce_api_base_url.rstrip("/")

def _extract_data(result: dict | None) -> dict | None:
    data = result.get("data") if isinstance(result, dict) else None
    return data if isinstance(data, dict) else None
   

async def fetch_order(order_id: str) -> dict | None:
    """
    Goal: 根据订单ID，拿到订单数据
    """
    try:
      r = await http_client.http_client.get(f"{_base_url()}/orders/{quote(order_id)}")
      return _extract_data(r.json())
    except Exception:
      return None

async def fetch_logistics(order_id: str) -> dict | None:
    """
    Goal: 根据订单ID，拿到订单物流信息
    """
    try:
      r = await http_client.http_client.get(f"{_base_url()}/orders/{quote(order_id)}/logistics")
      return _extract_data(r.json())
    except Exception:
      return None

async def fetch_product(product_id: str) -> dict | None:
    """
    Goal: 根据商品ID，获取商品的数据
    """
    try:
      r = await http_client.http_client.get(f"{_base_url()}/products/{quote(product_id)}")
      return _extract_data(r.json())
    except Exception:
        return None