"""
Define HTTP async client
"""
from httpx import AsyncClient
import asyncio

http_client: AsyncClient | None = None

def init_http_client():
  """
  Initialize http_client
  """
  global http_client
  http_client = AsyncClient(timeout=120, trust_env=True)  # trust_env=True picks up proxy settings


async def disposed_http_client():
  """
  Release http_client
  """
  await http_client.aclose()

async def main_test():
  init_http_client()
  response = await http_client.get(url="http://127.0.0.1:18081/orders/o30001")
  # print(response.json())
  data = response.json()['data']
  print(data)

if __name__ == '__main__':
  asyncio.run(main_test())