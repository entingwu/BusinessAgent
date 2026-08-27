from dataclasses import dataclass, field
from business_agent.task.flows.steps import FlowStep

@dataclass(slots=True)
class FlowSlot:
  slot_name: str
  type: str
  label: str
  description: str
  pattern: str | None = None   # 槽位值的格式约束（正则），None 表示不约束


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

  def  get_step_by_step_id(self,step_id:str)->FlowStep | None:
    for  step in self.steps:
        if step.id == step_id:
            return  step

    return  None

@dataclass(slots=True)
class FlowList:
  """
  Goal: Carrying yaml file upper layer elements(slots: user_flows.yml/ flows: both)
  """
  flows: list[Flow]
  slots: dict[str, FlowSlot] = field(default_factory=dict)

  def get_flow_by_flow_id(self,flow_id:str) -> Flow | None:
    for flow in self.flows:
        if flow.id == flow_id:
            return  flow

    return  None

if __name__ == '__main__':
  # 1. pyyaml -> dict
  # 2. initialize FlowList(flows="", slots="") Flow() FlowSlot()
  pass