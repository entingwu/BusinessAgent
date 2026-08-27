from dataclasses import asdict
import json
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

class TurnPlanner:

  # * 位置参数， 关键字参数
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
    llm_result = await self._invoke(prompt_inputs)
    print('@', llm_result)

    # 3. Return LLM result
    return llm_result

  def _build_prompt_inputs(self, 
                           state: DialogueState, 
                           *, 
                           flow_list: FlowList,
                           knowledge_intents: dict[str, KnowledgeIntent]) -> dict[str, Any]:
    # 1. 会话相关
    user_message_str = ChatHistoryBuilder.build_user_message_str(state.pending_turn.user_message)
    current_conversation_str = ChatHistoryBuilder.build(state.current_session().turns[-10:])

    # 2. 卡片相关
    focused_object_json = json.dumps(state.focused_object.to_dict(),
                                          ensure_ascii=False) if state.focused_object is not None else "null"

    # 3. 任务相关
    active_task_json = json.dumps(state.active_task.to_dict(),
                                      ensure_ascii=False) if state.active_task is not None else "null"

    interrupted_tasks_json = json.dumps([paused_task.to_dict() for paused_task in state.paused_tasks],
                                            ensure_ascii=False)

    # 4. 清单相关
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
    Goal: 把 Flow 序列化成提示词里的可用流程清单项
    去掉 steps：对路由决策没用，而且很长。
    去掉值为 None 的 pattern：槽位的格式约束进提示词是有意的——把约束摆给 LLM 看，
    能减少它生成格式不对的槽位值；但没配 pattern 的槽位会带出一串 "pattern": null，
    那是纯噪声，占提示词篇幅还容易让模型以为「null」有含义。
    Args:
        flow_object: 一个 Flow
    Returns:
        用于序列化进提示词的字典
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
  prompt_template = "{name}喜欢编程"
  prompt_template = PromptTemplate.from_template(template=prompt_template)
  print(prompt_template.invoke({"name": "entingwu"}))
  print(prompt_template.format_prompt(name="entingwu")) # str: python 格式化