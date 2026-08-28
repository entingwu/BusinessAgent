from typing import Any
from urllib.parse import quote

from business_agent.config.settings import settings
from business_agent.infrastructure import http_client


def _base_url() -> str:
   """
   Goal: the commerce service base URL
   """
   return settings.commerce_api_base_url.rstrip("/")

def _extract_data(result: dict | None) -> dict | None:
    data = result.get("data") if isinstance(result, dict) else None
    return data if isinstance(data, dict) else None
   

async def fetch_order(order_id: str) -> dict | None:
    """
    Goal: fetch an order by order id
    """
    try:
      r = await http_client.http_client.get(f"{_base_url()}/orders/{quote(order_id)}")
      return _extract_data(r.json())
    except Exception:
      return None

async def fetch_logistics(order_id: str) -> dict | None:
    """
    Goal: fetch an order's shipping information by order id
    """
    try:
      r = await http_client.http_client.get(f"{_base_url()}/orders/{quote(order_id)}/logistics")
      return _extract_data(r.json())
    except Exception:
      return None

async def fetch_product(product_id: str) -> dict | None:
    """
    Goal: fetch a product by product id
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
    Goal: search the product catalogue by preference (spec 3.3.3, step 2)

    The commerce service whitelists attribute names: use_case / style / spec / size / color /
    brand / warranty. An attribute outside the whitelist returns 400 rather than an empty list —
    that is deliberate on the commerce side. "This field does not exist" and "no product matches"
    must stay distinguishable, or the Agent reports a parameter error to the user as a business
    conclusion ("I could not find any products"). So nothing is filtered out silently here: a bad
    name is allowed to 400 so the caller sees it in the log.

    Args:
        attrs: attribute filters, e.g. {"use_case": "办公", "style": "极简"}
            (the values stay in Chinese — they are matching keys against the commerce catalogue)
    Returns:
        the data section of the commerce response (items / total / has_more); None if the
        request failed
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
