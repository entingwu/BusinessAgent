"""
Define API data model: interact with frontend
Inherit BaseModel: Complete Type evaludation and conversion during run time
"""
from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator

from business_agent.domain.messages import ChatHistoryMessage

class ChatObject(BaseModel):
  id: str
  title: str
  type: str
  attributes: dict[str, Any]


# 会话控制权归属。协议见 meta-business-agent.md 附录 E.2 第 3 条：
# 它是会话级而非消息级，所以挂在响应顶层而不是每条消息上。
ControlOwner = Literal["AGENT", "PENDING_HUMAN", "HUMAN"]


class ChatBotMessage(BaseModel):
  """
  一条 bot 回复。text / cards / suggestions 可以同时有值。
  object 与 cards 不并存；前端归一化：cards?.length ? cards : (object ? [object] : [])
  """
  text: str
  object: ChatObject | None = None
  cards: list[ChatObject] = Field(default_factory=list)
  suggestions: list[str] = Field(default_factory=list)


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
  control_owner: ControlOwner = "AGENT"
  messages: list[ChatBotMessage]


class ChatHistoryResponse(BaseModel):
  sender_id: str
  control_owner: ControlOwner = "AGENT"
  messages: list[ChatHistoryMessage]

class SessionStateResponse(BaseModel):
  """
  会话状态查询（规范 4.2）。给前端显示控制权归属，也给第二档的商家接管台用。
  """
  sender_id: str
  control_owner: ControlOwner = "AGENT"
  handoff_trigger: str | None = None
  handoff_reason: str = ""
  active_flow: str | None = None
  active_step: str | None = None
  slots: dict[str, Any] = Field(default_factory=dict)


class HandoffRequest(BaseModel):
  """
  坐席接管 / 回交。第一档只做控制权翻转；
  移交包（完整历史 + 已调用工具及返回结果）是第二档。
  """
  sender_id: str
  action: Literal["claim", "release"]
  reason: str = ""
