"""
Database Engine and Factory
"""
import asyncio

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import text

from business_agent.config.settings import settings

session_engine: AsyncEngine | None = None

session_factory: async_sessionmaker[AsyncSession] | None = None


def init_db_engine():
  global session_engine, session_factory

  session_engine = create_async_engine(url=settings.database_url, echo=True) # echo=True: 控制台显示SQL语句的执行

  session_factory = async_sessionmaker(session_engine, expire_on_commit=False)  # expire_on_commit=True/False

async def dispose_engine():
  await session_engine.dispose()

async def main_test():
  init_db_engine()

  async with session_factory() as session:
    cursor = await session.execute(text("select 1"))
    print(cursor.mappings().fetchone()) # 元组：索引取元组中的元素 {'1': 1} 字典：方便根据列名来获取

  await dispose_engine()

if __name__ == '__main__':
    asyncio.run(main_test())