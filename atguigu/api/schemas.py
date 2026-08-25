"""
Define API data model: interact with frontend
Inherit BaseModel: Complete Type evaludation and conversion during run time
"""
from typing import Any
from pydantic import BaseModel

from atguigu.domain.messages import ChatHistoryMessage

class ChatObject(BaseModel):
  id: str
  title: str
  type: str
  attributes: dict[str, Any]


class ChatBotMessage(BaseModel):
  text: str 
  object: ChatObject | None = None


class ChatRequest(BaseModel):
  """
  Chat request API data model
  """
  sender_id: str
  text: str | None = None
  object: ChatObject | None = None


class ChatResponse(BaseModel):
  """
  Chat responsive API data model
  """
  message_id: str
  messages: list[ChatBotMessage]


class ChatHistoryResponse(BaseModel):
  sender_id: str
  messages: list[ChatHistoryMessage]