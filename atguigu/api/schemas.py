"""
Define API data model: interact with frontend
Inherit BaseModel: Complete Type evaludation and conversion during run time
"""
from typing import Any
from pydantic import BaseModel, model_validator

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

  @model_validator(mode="after")
  def check_text_or_object(self):
    # text 和 object 至少要有一个，否则后续没有任何内容可以处理
    if self.text is None and self.object is None:
      raise ValueError("text 和 object 至少需要提供一个")
    return self


class ChatResponse(BaseModel):
  """
  Chat responsive API data model
  """
  message_id: str
  messages: list[ChatBotMessage]


class ChatHistoryResponse(BaseModel):
  sender_id: str
  messages: list[ChatHistoryMessage]