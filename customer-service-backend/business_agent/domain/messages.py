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
class BotMessage:
  """
  一条 bot 回复。协议见 meta-business-agent.md 附录 E。

  关键点：text / cards / suggestions 三者可以同时有值。
  旧结构是 text 与 object 二选一，带卡片时快捷回复会被丢掉，这正是要改掉的。
  object 保留是为了兼容旧路径，与 cards 不并存——要么发 object（单个），要么发 cards（列表）。
  """
  text: str  # 当下承载机器人的回复（文本内容） 一定会有值
  object: FocusedObject | None = None  # 旧路径的单个业务对象，等价于 cards 只有一项
  cards: list[FocusedObject] = field(default_factory=list)  # 新路径：业务对象列表
  suggestions: list[str] = field(default_factory=list)      # 快捷回复按钮文案

  def to_dict(self) -> dict[str, Any]:
      return {
          "text": self.text,
          "object": FocusedObject.to_dict(self.object) if self.object is not None else None,
          "cards": [FocusedObject.to_dict(card) for card in self.cards],
          "suggestions": list(self.suggestions),
      }

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> "BotMessage":
      object_data = data['object']
      return cls(
          text=data['text'],
          object=FocusedObject.from_dict(object_data) if object_data is not None else None,
          # 历史落库的状态没有这两个键，用 .get 兜住，否则老会话读回来直接 KeyError
          cards=[FocusedObject.from_dict(card) for card in (data.get('cards') or [])],
          suggestions=list(data.get('suggestions') or []),
      )


@dataclass(slots=True)
class ProcessedResult:
  message_id: str
  messages: list[BotMessage]
  # 会话控制权。附录 E.2 第 3 条：会话级而非消息级。
  # 目前恒为 AGENT——真正的状态机是验收第 8 条（人工接管）的事，
  # 这里先把字段留出来，届时由引擎写入，router 不用再动。
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