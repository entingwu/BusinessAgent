"""
Message category:
1. User role message
2. AI role message

No matter transfer via network or read/write via IO, could not directly operate on object. object is in memory.
"""

from enum import Enum
from typing import Any, Literal, Self
from dataclasses import dataclass, field

class MessageType(Enum):
  TEXT = "text"
  OBJECT = "object"

@dataclass(slots=True)          # less occupied memory, fast access, fixed object attributes
class FocusedObject:
  """
  Message Type is Object
  """
  id: str                       # product number or order number 
  title: str                    # product title or order title
  type: str                     # click product card "product", click order card "order"
  attributes: dict[str, Any]    # extra infor for product or order

  def to_dict(self) -> dict[str, Any]:
    """
    Convert self object to dict:
    object: used by business code
    dict: json type, used for database. json.dump: str
    """
    # return asdict(self)
    return {
      "id": self.id,
      "title": self.title,
      "type": self.type,
      "attributes": self.attributes,
    }

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> "FocusedObject":
    """
    Convert dict to object
    """
    return cls(
      id=data['id'],
      title=data['title'],
      type=data['type'],
      attributes=data['attributes'],
    )


@dataclass(slots=True)
class UserMessage:
  """
  The Domain data structure for User role message (code directly operates, not including API, Routing layer)
  """
  sender_id: str                # user id: carried from frontend
  message_id: str               # message_id: generate by itself
  type:  MessageType            # message_type: text message type, object message type
  text: str | None = None       # content for text message
  object: FocusedObject | None = None  # content for object message

  def to_dict(self) -> dict[str, Any]:
    """
    Convert self object to dict:
    object: used by business code
    dict: json type, used for database. json.dump: str
    """
    # return asdict(self)
    return {
      "sender_id": self.sender_id,
      "message_id": self.message_id,
      "type": self.type.value,
      "text": self.text,
      "object": FocusedObject.to_dict(self.object) if self.object is not None else None
    }

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> "UserMessage":
    """
    Convert dict to object
    """
    return cls(
      sender_id=data['sender_id'],
      message_id=data['message_id'],
      type=MessageType(data['type']),
      text=data['text'],
      object=FocusedObject.from_dict(data['object']) if data['object'] is not None else None,
    )

@dataclass(slots=True)
class Suggestion:
  """
  One quick-reply button: what the user sees, and what gets sent when they tap it.

  The split exists because a button's text *is* the message sent back to the planner, which writes
  it verbatim into a slot. Without two fields, what the user reads and what the planner receives
  are forced to be the same string, and they are not always the same thing.

  The clearest case is a button that acts on something the user should not have to read:

      label: "Track this order"
      value: "Track order O20260828171018C32E52"

  Nobody wants an order id printed on a button, but the tap has to carry it, or the shipping flow
  turns around and asks for the order number the bot just finished saying.

  Current use in this repo is smaller: the preference buttons read "Office" while sending
  `office`. The catalogue stores its attribute values in lower case, so the key follows suit and
  only the label is capitalised. (That one is convention rather than necessity — the commerce
  filter is `LOWER(...) LIKE '%value%'`, so "Office" would match too. The order-id case above is
  the reason this type has to exist; this one is the reason it currently earns its keep.)

  One piece of history, because this type's stated rationale has already had to be replaced once.
  It was introduced when the catalogue still stored 「"use_case": "办公"」 and a button had to read
  "Office" while sending 办公 — an argument that englishifying the catalogue
  (2026-08-28-englishify-attribute-values.sql) then destroyed. If a change ever collapses label
  and value again, rewrite this note rather than leaving an argument standing that no longer
  holds. An example that can expire is a weak reason to keep a type; one that cannot is worth
  writing down.

  A bare string still works everywhere — in YAML, in action arguments, and in state persisted
  before this type existed — and means label == value. `coerce` is what makes that true; do not
  bypass it.
  """
  label: str
  value: str

  @classmethod
  def coerce(cls, raw: Any) -> "Suggestion":
    """
    Goal: accept a bare string, a {label, value} mapping, or an existing Suggestion

    A bare string is the common case and means label == value. A mapping missing either key falls
    back to the other, so a half-written config degrades to a working button rather than an
    empty one.
    """
    if isinstance(raw, Suggestion):
      return raw
    if isinstance(raw, dict):
      label = str(raw.get("label") or raw.get("value") or "")
      value = str(raw.get("value") or raw.get("label") or "")
      return cls(label=label, value=value)
    text = str(raw)
    return cls(label=text, value=text)

  def to_dict(self) -> dict[str, Any]:
    return {"label": self.label, "value": self.value}

  @classmethod
  def from_dict(cls, data: Any) -> "Suggestion":
    return cls.coerce(data)


@dataclass(slots=True)
class BotMessage:
  """
  One bot reply. The protocol is appendix E of meta-business-agent.md.

  The key point: text, cards and suggestions may all carry values at once.
  The old shape made text and object mutually exclusive, so quick replies were dropped whenever a
  card was attached — which is exactly what this replaces.
  `object` is kept for the legacy path and never coexists with `cards`: send either one object or
  a list of cards, never both.
  """
  text: str  # the bot's reply text; always present
  object: FocusedObject | None = None  # legacy single business object; equivalent to a one-item cards list
  cards: list[FocusedObject] = field(default_factory=list)  # current path: a list of business objects
  suggestions: list[Suggestion] = field(default_factory=list)  # quick-reply buttons

  def to_dict(self) -> dict[str, Any]:
      return {
          "text": self.text,
          "object": FocusedObject.to_dict(self.object) if self.object is not None else None,
          "cards": [FocusedObject.to_dict(card) for card in self.cards],
          "suggestions": [suggestion.to_dict() for suggestion in self.suggestions],
      }

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> "BotMessage":
      object_data = data['object']
      return cls(
          text=data['text'],
          object=FocusedObject.from_dict(object_data) if object_data is not None else None,
          # State persisted before these keys existed does not have them, so .get() guards the
          # read — otherwise loading an old session raises KeyError
          cards=[FocusedObject.from_dict(card) for card in (data.get('cards') or [])],
          # Persisted state written before Suggestion existed holds bare strings; coerce
          # rather than failing, or every session from before this change becomes unreadable
          suggestions=[Suggestion.coerce(item) for item in (data.get('suggestions') or [])],
      )


@dataclass(slots=True)
class ProcessedResult:
  message_id: str
  messages: list[BotMessage]
  # Session control ownership. Appendix E.2, rule 3: session-level, not per message.
  # It is written by the engine's handoff policy; the router only passes it through.
  control_owner: str = "AGENT"

@dataclass(slots=False)
class ChatHistoryMessage:
  session_id: str
  role: Literal["user", "bot"]
  text: str | None = None
  object: FocusedObject | None = None

# slots is False, can add attribute to User
@dataclass(slots=False)
class User:
  id: str
  name: str

if __name__ == '__main__':
  user = User(id="111", name="zs")
  user.age = 20
  print(user.name)
  print(user.id)
  print(user.age)