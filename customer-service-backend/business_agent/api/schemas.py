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

  The two differ whenever the tap has to carry something the user should not have to read — a
  button saying "Track this order" that sends the order id along with it. See Suggestion in
  domain/messages.py.
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


class KnowledgeStatsResponse(BaseModel):
  """What the retrieval chain is configured with and how much it has indexed."""
  vector_backend: str
  embedding_backend: str
  embedding_model: str
  rerank_enabled: bool
  graph_enabled: bool
  top_k: int
  # The gate and its scale travel together on purpose: rerank relevance and vector cosine differ
  # by roughly 4x, so a bare number invites the reader to compare two incomparable things.
  score_gate: float
  score_gate_scale: str
  vector_chunks: int
  metadata_chunks: int
  sources: list[dict[str, Any]] = Field(default_factory=list)


class KnowledgeProbeRequest(BaseModel):
  text: str


class KnowledgeProbeResponse(BaseModel):
  """One retrieval, no generation — separates a retrieval miss from a bad answer."""
  query: str
  hit: bool
  gate: float
  chunks: list[dict[str, Any]] = Field(default_factory=list)
