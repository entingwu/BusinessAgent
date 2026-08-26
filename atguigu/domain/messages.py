"""
Message category:
1. User role message
2. AI role message

No matter transfer via network or read/write via IO, could not directly operate on object. object is in memory.
"""

from enum import Enum
from typing import Any, Literal, Self
from dataclasses import dataclass

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
class BotMessage:
  text: str  # 当下承载机器人的回复（文本内容） 一定会有值
  object: FocusedObject | None = None  # 承载机器人的回复对象（后续内容扩展） TODO

  def to_dict(self) -> dict[str, Any]:
      return {
          "text": self.text,
          "object": FocusedObject.to_dict(self.object) if self.object is not None else None
      }

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> "BotMessage":
      object_data = data['object']
      return cls(
          text=data['text'],
          object=FocusedObject.from_dict(object_data) if object_data is not None else None
      )


@dataclass(slots=True)
class ProcessedResult:
  message_id: str
  messages: list[BotMessage]

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