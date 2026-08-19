from dataclasses import dataclass, field
from atguigu.task.flows.steps import FlowStep

@dataclass(slots=True)
class FlowSlot:
  slot_name: str
  type: str
  label: str
  description: str


@dataclass(slots=True)
class Flow:
  """
  Flow object (Do not differentiate system flow, business flow)
  """
  id: str                # flow id
  name: str              # flow name
  description: str            # flow description
  steps: list[FlowStep]       # flow step
  slots: dict[str, FlowSlot] = field(default_factory=dict)   # slots this flow needs to collect


@dataclass(slots=True)
class FlowList:
  """
  Goal: Carrying yaml file upper layer elements(slots: user_flows.yml/ flows: both)
  """
  flows: list[Flow]
  slots: dict[str, FlowSlot] = field(default_factory=dict)


if __name__ == '__main__':
  # 1. pyyaml -> dict
  # 2. initialize FlowList(flows="", slots="") Flow() FlowSlot()
  pass