"""
Manage service
"""

from typing import Annotated
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from business_agent.engines.builder import build_dialogue_engine
from business_agent.services.dialogue_service import DialogueStateService
from business_agent.engines.dialogue_engine import DialogueEngine
from business_agent.repository.dialogue_repository import DialogueRepository
# from business_agent.infrastructure.db_client import session_factory  # a trap: this binds the member
from business_agent.infrastructure import db_client      # import the module, not the member


def get_dialogue_engine():
  return build_dialogue_engine()

DialogueEngineDep = Annotated[DialogueEngine, Depends(get_dialogue_engine)]

async def get_session():
  # session_factory is assigned by init_db_engine() inside lifespan, so it has to be read as a
  # module attribute to get the current value — `from ... import session_factory` captures the
  # None that was there at import time.
  async with db_client.session_factory() as session:
    yield session     # Must yield, once return code block is completed, session object is released. Release after used.

DialogueSessionDep = Annotated[AsyncSession, Depends(get_session)]

def get_dialogue_repository(session: DialogueSessionDep):
  return DialogueRepository(session)

DialogueRepositoryDep = Annotated[DialogueRepository, Depends(get_dialogue_repository)]

def get_dialogue_service(engine: DialogueEngineDep, repository: DialogueRepositoryDep):
  return DialogueStateService(engine, repository)

DialogueStateServiceDep = Annotated[DialogueStateService, Depends(get_dialogue_service)]
