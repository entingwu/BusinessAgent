"""
知识库元数据的独立连接。

## 为什么不复用 db_client 的那一份

`knowledge_sources` / `knowledge_chunks` / `retrieval_traces` 记的是「当前索引由哪个
Embedding 模型、从哪份语料建出来的」。而**向量索引本身是各人本地的 gitignored 目录**
（Chroma 目录 / Milvus 容器），元数据却在共享的那一个 MySQL 里。

两者的共享程度不一致，就会出事：2026-08-28 本分支用 BGE-M3 重建索引（45→47 片），
共享元数据表被整体改写成 `BAAI/bge-m3`，而 main 分支上每个人的 Chroma 仍是
`text-embedding-v3` 的 45 片。于是 CLAUDE.md 那条验收检查
「`vector_chunks` 必须等于 `metadata_chunks`」对所有人失败——**而失败的原因与他们
自己的环境无关**。

更糟的是它会静默误导：`ingest` 靠比对元数据里的 `content_hash` 决定跳过什么，
从不检查本地向量库有没有那些向量。main 上的人跑一次 `ingest`（不带 `--force`）
会得到「全部 skipped、执行成功」，然后带着模型对不上的索引继续跑。

## 结论：索引在哪隔离，元数据就该在哪隔离

`KNOWLEDGE_DATABASE_URL` 缺省等于 `DATABASE_URL`（main 的行为不变）；
换了 Embedding 或向量库的分支把它指向独立的库，与 main 物理隔开。

这条是本项目反复得出的同一个判据：**共享的东西要么真共享，要么物理隔离，
最糟的是「看起来共享其实语义不同」。**
"""
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from business_agent.config.settings import settings

_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def get_knowledge_session_factory() -> async_sessionmaker[AsyncSession]:
  """
  Goal: 取知识元数据的 session 工厂（进程内单例）。
        KNOWLEDGE_DATABASE_URL 未配时退回 DATABASE_URL，与改造前行为一致。
  Returns: async_sessionmaker[AsyncSession]
  """
  global _engine, _factory
  if _factory is None:
    url = settings.knowledge_database_url or settings.database_url
    _engine = create_async_engine(url=url, echo=False)
    _factory = async_sessionmaker(_engine, expire_on_commit=False)
  return _factory


def get_knowledge_engine() -> AsyncEngine:
  """Goal: 取知识元数据的 engine，建表时用。"""
  get_knowledge_session_factory()
  assert _engine is not None
  return _engine


async def dispose() -> None:
  if _engine is not None:
    await _engine.dispose()
