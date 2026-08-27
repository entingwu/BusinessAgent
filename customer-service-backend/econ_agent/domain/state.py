"""
Mainly used to manage user(sender_id) complete dialog state
1. Task related Info [TaskContext/SystemContext] 
2. Dialog related info
3. Turn related info
4. User click card info [FocusedObject]
"""

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from econ_agent.domain.contexts import SystemContext, TaskContext
from econ_agent.domain.messages import FocusedObject, UserMessage, BotMessage

@dataclass(slots=True)
class Turn:
  turn_id: str
  user_message: UserMessage
  bot_messages: list[BotMessage]

  def to_dict(self) -> dict[str, Any]:
    return {
      "turn_id": self.turn_id,
      "user_message": UserMessage.to_dict(self.user_message),
      "bot_messages": [BotMessage.to_dict(bot_message) for bot_message in self.bot_messages],
    }

  @classmethod
  def from_dict(cls, data:dict[str, Any]) -> "Turn":
    return cls(
      turn_id=data['turn_id'],
      user_message=UserMessage.from_dict(data['user_message']),
      bot_messages=[BotMessage.from_dict(bot_msg_dict) for bot_msg_dict in data['bot_messages']],
    )
  
@dataclass(slots=True)
class Session:
  session_id: str
  started_at: float                   # session created time
  activated_at: float                 # session activated time: check if it is expired. Created a new session if expired, else reuse current session. Updated activated_at.
  closed_at: float | None = None      # session closed time
  turns: list[Turn]=field(default_factory=list)

  def to_dict(self)->dict[str, Any]:
    return {
      "session_id": self.session_id,
      "started_at": self.started_at,
      "activated_at": self.activated_at,
      "closed_at": self.closed_at,
      "turns": [Turn.to_dict(turn) for turn in self.turns]
    }

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> "Session":
    return cls(
      session_id=data["session_id"],
      started_at=data["started_at"],
      activated_at=data["activated_at"],
      closed_at=data["closed_at"],
      turns=[Turn.from_dict(turn_dict) for turn_dict in data["turns"]]
    )

@dataclass(slots=True)
class DialogueState:
  """
  Large warehouse:
  Put item in large warehouse. [Put at different step]
  Get item from large warehouse. [The info needed to operate engine should retrieve from DialogState]
  """

  sender_id: str
  active_task: TaskContext | None = None                               # Current processing active business task
  paused_tasks: list[TaskContext] = field(default_factory=list)        # Paused task
  active_system_task: SystemContext | None = None                      # Current processing active system task
  sessions: list[Session] = field(default_factory=list)                # Dialog info
  current_session_id:str | None = None                                 # Current session dialog id, in order to retrieve current created session object
  focused_object:FocusedObject | None = None                           # Card info
  pending_turn: Turn | None = None

  def to_dict(self) -> dict[str, Any]:
    return {
      "sender_id": self.sender_id,
      "active_task": TaskContext.to_dict(self.active_task) if self.active_task is not None else None,
      "paused_tasks": [TaskContext.to_dict(paused_task) for paused_task in self.paused_tasks],
      "active_system_task": SystemContext.to_dict(
                self.active_system_task) if self.active_system_task is not None else None,
      "focused_object": FocusedObject.to_dict(self.focused_object) if self.focused_object is not None else None,
      "sessions": [Session.to_dict(session) for session in self.sessions],
      "current_session_id": self.current_session_id,
      "pending_turn": Turn.to_dict(self.pending_turn) if self.pending_turn is not None else None
    }

  @classmethod
  def from_dict(cls, data:dict[str, Any])->"DialogueState":
    return cls(
      sender_id=data["sender_id"],
      active_task=TaskContext.from_dict(data['active_task']) if data['active_task'] is not None else None,
      paused_tasks=[TaskContext.from_dict(paused_task_dict) for paused_task_dict in data['paused_tasks']],
      active_system_task=SystemContext.from_dict(data['active_system_task']) if data['active_system_task'] is not None else None,
      focused_object=FocusedObject.from_dict(data['focused_object']) if data['focused_object'] is not None else None,
      sessions=[Session.from_dict(session_dict) for session_dict in data['sessions']],
      current_session_id=data['current_session_id'],
      pending_turn=Turn.from_dict(data['pending_turn']) if data['pending_turn'] is not None else None
    )

########################################### Task related methods ###########################################

  def start_task(self, task_context: TaskContext):
    """
    Goal: Start a new business flow task
    :return 
    """
    self.active_task = task_context

  def end_active_task(self):
    """
    Goal: Complete the business flow task
    :return 
    """
    self.active_task = None

  def cancel_active_task(self):
    """
    Goal: Cancel the ongoing business task and system flow task.
    :return 
    """
    self.active_task = None
    self.active_system_task = None

  def remove_paused_task(self, flow_id: str):
    """
    Goal: Stop canceling the paused business task in the task stack.
    flow_id: The business flow id to cancel
    :return 
    paused_tasks=[
        TaskContext(flow_id="order_status_query", step_id="start"), 
        TaskContext(flow_id="logistics_tracking", step_id="start")]
    """
    self.paused_tasks = [paused_task for paused_task in self.paused_tasks if paused_task.flow_id != flow_id]

  def interrupt_activate_task(self):
    """
    Goal: Stop current ongoing business task.
    :return 
    """  
    # 1. Put the current processing business task into the stack for stop business task.
    self.paused_tasks.append(self.active_task)

    # 2. Clear current processing business flow.
    self.active_task = None

  def resume_task(self, flow_id: str | None = None):
    """
    Goal: Resume stop business task from the business stack
    Args:
        flow_id: The business flow id to resume
    """
    # 1. Check if stack is empty
    if not self.paused_tasks:
      return False

    # 2. check flow_id
    # 2.1 No assigned business flow id, resume the most recent business flow from the top of stack
    if flow_id is None:
      paused_task = self.paused_tasks.pop()
      self.active_task = paused_task
      return True

    # 2.2 Have assigned business flow id, select from the task based on the assigned business task flow id
    for index, paused_task in enumerate(self.paused_tasks):
      if paused_task.flow_id == flow_id:
        self.active_task = paused_task
        del self.paused_tasks[index]
        return True

    return False

  def start_system_task(self, system_task: SystemContext):
    self.active_system_task = system_task

  def end_system_task(self):
    self.active_system_task = None

  def current_task(self):
    """
    Caller: 流程推进器使用
    Goal: Return the task context, can be system context, or business task context. Can be None.
    1. business flow task context
    2. system flow task context
    case1: Have both 1, 2. return 2.
    case2: Neither 1 nor 2. return None.
    case3: Have 1, return 1
    case4: Have 2, return 2
    Conclusion: return the one who has, if have both, then return system.
    Returns
    """
    return self.active_system_task or self.active_task

########################################### Slots related methods ###########################################

  def set_slots(self, slot_info: dict[str, Any]):
    if self.active_task is not None:
      self.active_task.slots.update(slot_info)

  def remove_slot(self, slot_name: str):
    if self.active_task is not None:
      # 槽位可能已经不存在（例如重复清理），用默认值避免 KeyError
      self.active_task.slots.pop(slot_name, None)

########################################### Dialog related methods ###########################################

  def start_session(self):
    """
    Goal: create sesson object, assign value to session attributes
    Returns:
    """
    now = time.time()
    # 1. Initialize session object
    session = Session(session_id=str(uuid4().hex), started_at=now, activated_at=now)

    # Update the session id at state
    self.current_session_id = session.session_id

    # 3. Append session to sessions
    self.sessions.append(session)

  def current_session(self) -> Session | None:
    """
    Goal: Retrieve current session
    Returns:
    """
    for session in self.sessions:
      if session.session_id == self.current_session_id:
        return session
    return None

  def close_current_session(self):
    """
    Goal: Update closed_at of the current session object
    """
    self.current_session().closed_at = time.time()
    self.current_session_id = None

  def reset_runtime_state_for_new_session(self):
    """
    Goal: If current session expired, it clears the dialog state of this session.
    Check if session is expired.
    Returns:
    """
    # 1. Task related
    self.active_task = None
    self.active_system_task = None
    self.paused_tasks = []

    # 2. card related
    self.focused_object = None

    # 3. buffer 
    self.pending_turn = None

########################################### Turn related methods ###########################################
  
  def begin_turn(self, user_message: UserMessage):
    """
    Goal: Initialize Turn Object.
    Args:
      user_message:
    Returns:
    """
    # 1. Initialize New Turn Object
    turn = Turn(turn_id=str(uuid4().hex), user_message=user_message, bot_messages=[])

    # 2. Assign turn object to buffer
    self.pending_turn = turn

  def commit_pending_turn(self):
    """
    Goals: Update current session with the buffer content, then clear buffer.
    """
    # 1. Update current session with the buffer content
    self.current_session().turns.append(self.pending_turn)
    # 2. clear buffer
    self.pending_turn = None

########################################### Object related methods ###########################################

  def set_focused_object(self, object: FocusedObject):
        self.focused_object = object


if __name__ == '__main__':
  list_container = ["1", "2", "3"]
  print(list_container.pop())