from typing import Any
from urllib.parse import quote

from business_agent.config.settings import settings
from business_agent.infrastructure import http_client


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

async def search_products(*,
                          keyword: str | None = None,
                          min_price: float | None = None,
                          max_price: float | None = None,
                          attrs: dict[str, str] | None = None,
                          in_stock: bool | None = None,
                          limit: int = 5) -> dict | None:
    """
    Goal: 按偏好检索商品目录（规范 3.3.3 第 2 步）

    中台的属性名是白名单：use_case / style / spec / size / color / brand / warranty。
    传白名单外的名字会返回 400 而不是空列表——这是中台有意的设计，
    「字段不存在」与「没有匹配商品」必须可区分，否则 Agent 会把参数错误
    当成业务结论告诉用户「没有找到商品」。所以这里不做静默过滤，
    传错就让它 400，由调用方在日志里看见。

    Args:
        attrs: 属性过滤，如 {"use_case": "办公", "style": "极简"}
    Returns:
        中台返回的 data 部分（含 items / total / has_more）；请求失败返回 None
    """
    params: list[tuple[str, str]] = []
    if keyword:
        params.append(("q", keyword))
    if min_price is not None:
        params.append(("min_price", str(min_price)))
    if max_price is not None:
        params.append(("max_price", str(max_price)))
    if in_stock is not None:
        params.append(("in_stock", "true" if in_stock else "false"))
    for key, value in (attrs or {}).items():
        if value:
            params.append(("attr", f"{key}:{value}"))
    params.append(("limit", str(limit)))

    try:
        response = await http_client.http_client.get(f"{_base_url()}/products", params=params)
        return _extract_data(response.json())
    except Exception:
        return None
