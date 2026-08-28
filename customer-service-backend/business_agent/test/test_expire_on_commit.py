import asyncio

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import String
from business_agent.config.settings import settings


class Base(DeclarativeBase):
  pass

# Product List Data Model
class Product(Base):
  __tablename__ = "products"
  id: Mapped[int] = mapped_column(primary_key=True)
  title: Mapped[str] = mapped_column(String(255))

db_engine = create_async_engine(url=settings.database_url, echo=True)
# Keep already-loaded objects usable in memory after the transaction commits.
# Flip this to True and run again: step 3 issues a fresh SELECT, triggering a lazy load inside a
# transaction that has already closed.
session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

async def prepare_table():
  """Create the table and one row, so demo() has a product with id=1 to find."""
  async with db_engine.begin() as conn:
    await conn.run_sync(Base.metadata.create_all)

  async with session_factory() as session:
    product = await session.get(Product, 1)
    if product is None:
      session.add(Product(id=1, title="Mechanical keyboard"))
      await session.commit()

async def demo():
  # async_sessionmaker is a factory: call it to get an AsyncSession before entering the context
  # manager
  async with session_factory() as session:
     # 1. Load one product asynchronously
     product = await session.get(Product, 1)
     print(f"First time read {product.title}")
     # 2. Commit the transaction
     await session.commit()
     # 3. Read an attribute again
     # With expire_on_commit=True the driver re-queries the database over the async connection
     # to get the object's current values
     print(f"Second time read {product.title}")

async def main():
  try:
    await prepare_table()
    await demo()
  finally:
    # dispose() must sit outside the session context and must always run
    await db_engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
