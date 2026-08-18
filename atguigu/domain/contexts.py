"""
Context object type (Abstract)
Business Process Context
System Process Context: inheritance + mapping
"""
from dataclasses import dataclass, field, asdict
from typing import Any, Self

@dataclass(slots=True)
class TaskContext:
  """
  Business Process Context:
  flow_id: Business process id
  step_id: Business step id, ensure business process steps. which steps have gone through. 
  slots: missing info in business process
  """
  flow_id: str
  step_id: str
  slots: dict[str, Any] = field(default_factory=dict)

  def to_dict(self)->dict[str, Any]:
    return {
      "flow_id": self.flow_id,
      "step_id": self.step_id,
      "slots": self.slots
    }

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> "TaskContext":
    return cls(
      flow_id=data['flow_id'],
      step_id=data["step_id"],
      slots=data['slots'],
    )

@dataclass(slots=True)
class SystemContext:
  """
  System process context base class
  flow_id: system proess id: system_task_started
  step_id: system process id: start
  flow_id/step_id must use these two names. [流程推进器]
  """
  flow_id: str
  step_id: str

  def to_dict(self) -> dict[str, Any]:
    return asdict(self)       # type: ignore

  @staticmethod
  def from_dict(data: dict[str, Any]) -> "SystemContext":
    flow_id = data['flow_id']
    clz =  SYSTEM_CONTEXT_TO_CLASS[flow_id]
    return clz(**data) # 解字典


@dataclass(slots=True)
class SystemTaskStartedContext(SystemContext):
  started_flow_id: str        # started business process flow id
  started_flow_name: str      # started business process flow name

# system_task_interrupted
@dataclass(slots=True)
class SystemTaskInterruptedContext(SystemContext):
  interrupted_flow_id: str
  interrupted_flow_name: str
  started_flow_id: str
  started_flow_name: str


# system_task_resumed
@dataclass(slots=True)
class SystemTaskResumedContext(SystemContext):
  resumed_flow_id: str
  resumed_flow_name: str


# system_task_canceled
@dataclass(slots=True)
class SystemTaskCanceledContext(SystemContext):
  canceled_flow_id: str
  canceled_flow_name: str


@dataclass(slots=True)
class SystemCollectInformationContext(SystemContext):
  response: dict[str, Any]        # Tell user what is missing in business process info 
  slot_name: str                  # missing info name [info: name, value], used for decison making.

@dataclass(slots=True)
class User:
  id: int
  name: str
  address: list[str] = field(default_factory=list)

if __name__ == '__main__':
  user = User(id=111, name="tom")
  print(asdict(user))

SYSTEM_CONTEXT_TO_CLASS: dict[str, type[SystemContext]] = {
  "system_task_started": SystemTaskStartedContext,
  "system_task_interrupted": SystemTaskInterruptedContext,
  "system_task_resumed": SystemTaskResumedContext,
  "system_task_canceled": SystemTaskCanceledContext,
  "system_collect_information": SystemCollectInformationContext,
}
