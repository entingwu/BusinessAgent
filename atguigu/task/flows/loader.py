from pathlib import Path
from typing import Any
from atguigu.task.flows.flows import Flow, FlowList, FlowSlot
from atguigu.task.flows.steps import CollectionFlowStep, FlowStep
import yaml

class FlowLoader:

  def load_multi_yamls(self, paths: list[Path]) -> FlowList:
    """
    FlowList: Both user_flows and system flows
    Args:
        path:
    """
    final_flows: list[Flow] = []
    final_slots: dict[str, FlowSlot] = {}
    for path in paths:
       single_flow_list = self.load_single_yaml(path)
       final_flows.extend(single_flow_list.flows)
       final_slots.update(single_flow_list.slots)
    return FlowList(flows=final_flows, slots=final_slots)
     

  def load_single_yaml(self, path: Path) -> FlowList:
    """
    Goal: Load single YAML file
    Args:
        path:
    Returns:
    """

    # 1. Use pyyaml to load yaml file to convert to dict object
    with open(path, 'r', encoding='utf-8') as f:
      yaml_dict: dict[str, Any] = yaml.safe_load(f.read())

    # 2. 加载slots
    loaded_slots = self._load_slots(yaml_dict.get('slots', {}))

    # 3. 加载flows
    loaded_flows = self._load_flows(yaml_dict['flows'], loaded_slots)

    # 4. 构建FlowsList

    return FlowList(slots=loaded_slots, flows=loaded_flows)

  def _load_slots(self, slots_dict: dict[str, Any]) -> dict[str, FlowSlot]:
    loaded_slots: dict[str, FlowSlot] = {}
    for slot_name, slot_dict in slots_dict.items():
        loaded_slots[slot_name] = FlowSlot(
            slot_name=slot_name,
            type=slot_dict['type'],
            label=slot_dict['label'],
            description=slot_dict['description']
        )
    return loaded_slots

  def _load_flows(self, flows_dict: dict[str, Any], loaded_slots: dict[str, FlowSlot]) -> list[Flow]:
    loaded_flows: list[Flow] = []
    for flow_id, flow_dict in flows_dict.items():
        steps = [FlowStep.from_dict(step_dict) for step_dict in flow_dict['steps']]
        flow = Flow(
            id=flow_id,
            name=flow_dict['name'],
            description=flow_dict['description'],
            steps=steps,
            slots=self._build_flow_slots(steps, loaded_slots)
        )
        loaded_flows.append(flow)

    return loaded_flows

  def _build_flow_slots(self, steps: list[FlowStep], loaded_slots: dict[str, FlowSlot]) -> dict[str, FlowSlot]:
    """
    Goal: Get the missing slot info from current task, in order to get Flow directly, then we can get missing slot info.
    """
    final_flow_slots: dict[str, FlowSlot] = {}
    for step in steps:
      if not isinstance(step, CollectionFlowStep):
          continue

      slot_name = step.slot_name
      slot_definition = loaded_slots[slot_name]
      final_flow_slots[slot_name] = slot_definition
    return final_flow_slots


if __name__ == "__main__":
  flow_loader = FlowLoader()
  config_dir = Path(__file__).resolve().parents[3] / "flow_config"
  flow_list = flow_loader.load_multi_yamls([
      config_dir / "user_flows.yml",
      config_dir / "system_flows.yml",
  ])
  print(flow_list)
