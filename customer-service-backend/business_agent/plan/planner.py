from dataclasses import asdict
import json
import logging
import time
from typing import Any
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from sqlalchemy import values
from business_agent.domain.state import DialogueState
from business_agent.knowledge.intents import KnowledgeIntent
from business_agent.plan.turn_plan import TurnPlan
from business_agent.prompt.loader import load_prompt_template
from business_agent.infrastructure.llm_client import llm_client
from business_agent.chat_history.builder import ChatHistoryBuilder
from business_agent.task.flows.flows import FlowList
from business_agent.observability import brief

logger = logging.getLogger(__name__)

class TurnPlanner:

  # * separates positional from keyword-only arguments
  async def predict(self, 
                    dialogue_state: DialogueState, 
                    *, 
                    flow_list: FlowList, 
                    knowledge_intents: dict[str, KnowledgeIntent]):
    """
    Goal: Use Turn Planner to analyze routing
    Args:
        dialogue_state
    Returns:
    """

    # 1. Construct prompt params for LLM routing analysis
    prompt_inputs: dict[str, Any] = self._build_prompt_inputs(dialogue_state, flow_list=flow_list, knowledge_intents=knowledge_intents)

    # 2. Call LLM
    started_at = time.perf_counter()
    llm_result = await self._invoke(prompt_inputs)
    elapsed_ms = (time.perf_counter() - started_at) * 1000

    # Spec 5.3, tier 1: record the intent-recognition result. This used to be a bare print —
    # no level, no timestamp, reaching no handler, so in a server process the only way to see it
    # was to watch the terminal.
    logger.info(
      "turn_plan sender_id=%s track=%s detail=%s elapsed_ms=%.0f",
      dialogue_state.sender_id, self._track_of(llm_result), brief(llm_result), elapsed_ms,
    )

    # 3. Return LLM result
    return llm_result

  def _track_of(self, turn_plan: TurnPlan) -> str:
    """Which of the three tracks was chosen. Pulled out on its own because it is the first field
    anyone looks at when debugging."""
    if turn_plan.task is not None:
      return "task"
    if turn_plan.knowledge is not None:
      return "knowledge"
    if turn_plan.chitchat is not None:
      return "chitchat"
    return "none"

  def _build_prompt_inputs(self, 
                           state: DialogueState, 
                           *, 
                           flow_list: FlowList,
                           knowledge_intents: dict[str, KnowledgeIntent]) -> dict[str, Any]:
    # 1. Session
    user_message_str = ChatHistoryBuilder.build_user_message_str(state.pending_turn.user_message)
    current_conversation_str = ChatHistoryBuilder.build(state.current_session().turns[-10:])

    # 2. Focused card
    focused_object_json = json.dumps(state.focused_object.to_dict(),
                                          ensure_ascii=False) if state.focused_object is not None else "null"

    # 3. Task
    active_task_json = json.dumps(state.active_task.to_dict(),
                                      ensure_ascii=False) if state.active_task is not None else "null"

    interrupted_tasks_json = json.dumps([paused_task.to_dict() for paused_task in state.paused_tasks],
                                            ensure_ascii=False)

    # 4. Catalogues
    available_flows = [self._serialize_flow(flow_object)
                       for flow_object in flow_list.flows
                       if not flow_object.id.startswith("system_")]

    available_flows_json = json.dumps({"flows" : available_flows}, ensure_ascii=False)
    knowledge_intents = [{"id": id, "description": intent.description} for id, intent in knowledge_intents.items()]
    knowledge_intents_json = json.dumps(knowledge_intents, ensure_ascii=False)
    
    return {
      "user_message": user_message_str,
      "current_conversation": current_conversation_str,
      "focused_object_json": focused_object_json,
      "interrupted_tasks_json": interrupted_tasks_json,
      "active_task_json": active_task_json,
      "knowledge_intents_json": knowledge_intents_json,
      "available_flows_json": available_flows_json,
    }

  def _serialize_flow(self, flow_object) -> dict[str, Any]:
    """
    Goal: serialise a Flow into an entry of the available-flows list in the prompt.
    steps are dropped: they are useless for a routing decision and very long.
    A pattern whose value is None is dropped too. Putting slot format constraints into the prompt
    is deliberate — showing the LLM the constraint reduces how often it produces a badly formatted
    slot value — but slots without a pattern would emit a run of "pattern": null, which is pure
    noise: it takes up prompt space and invites the model to read meaning into "null".
    Args:
        flow_object: one Flow
    Returns:
        the dict to serialise into the prompt
    """
    flow_dict = {k: v for k, v in asdict(flow_object).items() if k != "steps"}
    flow_dict["slots"] = {
      slot_name: {k: v for k, v in slot_dict.items() if not (k == "pattern" and v is None)}
      for slot_name, slot_dict in (flow_dict.get("slots") or {}).items()
    }
    return flow_dict

  async def _invoke(self, prompt_inputs: dict[str, Any]) -> TurnPlan:
    """
    Goal: Use LLM to analyze routing
    Args:
        prompt_inputs
    """
    # 1. Load prompt template (contain params)
    prompt_template_str = load_prompt_template("turn_plan")

    # 2. prompt template object
    prompt_template = PromptTemplate.from_template(template=prompt_template_str, template_format="jinja2")
    # invoke: llm_client, output

    # 3. construct chain
    chain = prompt_template | llm_client | JsonOutputParser() # json->dict

    # 4. execute chain
    # prompt_template.invoke(prompt_inputs) |> llm_client.invoke()
    llm_result_dict = await chain.ainvoke(prompt_inputs)

    # 5. return chain executed result
    return TurnPlan.from_dict(llm_result_dict)

if __name__ == '__main__':
  prompt_template = "{name} likes programming"
  prompt_template = PromptTemplate.from_template(template=prompt_template)
  print(prompt_template.invoke({"name": "entingwu"}))
  print(prompt_template.format_prompt(name="entingwu"))  # str: python formatting