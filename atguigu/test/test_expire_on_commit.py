import asyncio

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import String
from atguigu.config.settings import settings


class Base(DeclarativeBase):
  pass

# Product List Data Model
class Product(Base):
  __tablename__ = "products"
  id: Mapped[int] = mapped_column(primary_key=True)
  title: Mapped[str] = mapped_column(String(255))

db_engine = create_async_engine(url=settings.database_url, echo=True)
# 在提交数据库事务之后，依然保留内存中已查询出来的对象数据
# 改成 True 再跑一次，第 3 步会重新发 SELECT（在已关闭的事务里触发懒加载）
session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

async def prepare_table():
  """建表 + 造一条测试数据，保证 demo() 能查到 id=1 的商品"""
  async with db_engine.begin() as conn:
    await conn.run_sync(Base.metadata.create_all)

  async with session_factory() as session:
    product = await session.get(Product, 1)
    if product is None:
      session.add(Product(id=1, title="机械键盘"))
      await session.commit()

async def demo():
  # async_sessionmaker 是工厂，要先调用它拿到 AsyncSession 再进上下文管理器
  async with session_factory() as session:
     # 1. 异步查出一个商品对象
     product = await session.get(Product, 1)
     print(f"First time read {product.title}")
     # 2. 提交事务
     await session.commit()
     # 3. 再次尝试读取属性
     # expire_on_commit=True 时：底层会用异步连接重新查询数据库，获取对象最新的值
     print(f"Second time read {product.title}")

async def main():
  try:
    await prepare_table()
    await demo()
  finally:
    # dispose 要放在 session 上下文之外，且保证一定执行
    await db_engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
