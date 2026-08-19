"""
Manage service
"""

from typing import Annotated
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from atguigu.engines.builder import build_dialogue_engine
from atguigu.services.dialogue_service import DialogueStateService
from atguigu.engines.dialogue_engine import DialogueEngine
from atguigu.repository.dialogue_repository import DialogueRepository
# from atguigu.infrastructure.db_client import session_factory # 有坑，模块下成员
from atguigu.infrastructure import db_client      # 包下面的模块


def get_dialogue_engine():
  return build_dialogue_engine()

DialogueEngineDep = Annotated[DialogueEngine, Depends(get_dialogue_engine)]

async def get_session():
  # session_factory 由 lifespan 中的 init_db_engine() 赋值，必须通过模块属性读取最新值，
  # 直接 from ... import session_factory 拿到的是导入时刻的 None
  async with db_client.session_factory() as session:
    yield session     # Must yield, once return code block is completed, session object is released. Release after used.

DialogueSessionDep = Annotated[AsyncSession, Depends(get_session)]

def get_dialogue_repository(session: DialogueSessionDep):
  return DialogueRepository(session)

DialogueRepositoryDep = Annotated[DialogueRepository, Depends(get_dialogue_repository)]

def get_dialogue_service(engine: DialogueEngineDep, repository: DialogueRepositoryDep):
  return DialogueStateService(engine, repository)

DialogueStateServiceDep = Annotated[DialogueStateService, Depends(get_dialogue_service)]
