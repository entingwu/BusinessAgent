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


# Session control ownership. The protocol is appendix E.2, rule 3 of meta-business-agent.md:
# it is session-level, not message-level, so it hangs off the top of the response rather than off
# each message.
ControlOwner = Literal["AGENT", "PENDING_HUMAN", "HUMAN"]


class ChatSuggestion(BaseModel):
  """
  One quick-reply button. `label` is what the user sees; `value` is what is sent back when they
  tap it.

  The two differ where a button's text doubles as a matching key — a button reading "Office" has
  to send 办公, because that is what the commerce catalogue stores and what the attribute filter
  compares against. See Suggestion in domain/messages.py for the full reasoning.
  """
  label: str
  value: str

class ChatBotMessage(BaseModel):
  """
  One bot reply. text, cards and suggestions may all carry values at once.
  object and cards never coexist; the front end normalises with
  cards?.length ? cards : (object ? [object] : [])
  """
  text: str
  object: ChatObject | None = None
  cards: list[ChatObject] = Field(default_factory=list)
  suggestions: list[ChatSuggestion] = Field(default_factory=list)


class ChatRequest(BaseModel):
  """
  Chat request API data model
  """
  sender_id: str
  text: str | None = None
  object: ChatObject | None = None

  @model_validator(mode="after")
  def check_text_or_object(self):
    # At least one of text and object must be present, or there is nothing downstream to handle
    if self.text is None and self.object is None:
      raise ValueError("at least one of text and object must be provided")
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
  Session state query (spec 4.2). Used by the front end to display control ownership, and by
  tier 2's merchant takeover console.
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
  A human agent claims or releases the session. Tier 1 only flips ownership;
  the handoff package (full history plus the tools called and what they returned) is tier 2.
  """
  sender_id: str
  action: Literal["claim", "release"]
  reason: str = ""
