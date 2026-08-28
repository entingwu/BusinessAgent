import json
from typing import Any

from business_agent.domain.messages import BotMessage
from business_agent.domain.state import DialogueState
from business_agent.plan.turn_plan import TurnPlanValidatedResult, ClarifyReason
from business_agent.prompt.loader import load_prompt_template
from business_agent.infrastructure.llm_client import llm_client
from business_agent.chat_history.builder import  ChatHistoryBuilder

from langchain_core.prompts.prompt import PromptTemplate
from langchain_core.output_parsers import StrOutputParser


class ClarifyResponder:
  
  async def respond(self, 
                    reason: ClarifyReason, 
                    state: DialogueState) -> list[BotMessage]:
    # 1. 构建澄清话术需要的提示词模版变量值
    prompt_inputs = self._build_prompt_inputs(reason, state)

    # 2. 格式化模版，调用LLM
    rewritten = await self._invoke(prompt_inputs)

    # 3. 返回
    return rewritten

  def _build_prompt_inputs(self,
                           reason: ClarifyReason,
                           state: DialogueState) -> dict[str, Any]:
      user_message_str = ChatHistoryBuilder.build_user_message_str(state.pending_turn.user_message)
      history_str = ChatHistoryBuilder.build(state.current_session().turns[-10:])
      focused_object_str = json.dumps(state.focused_object.to_dict(),
                                      ensure_ascii=False) if state.focused_object is not None else "null"
      reason_str = reason.value
      clarify_message_str =self._build_base_response(reason,state)

      return {
          "user_message": user_message_str,
          "history": history_str,
          "focused_object": focused_object_str,
          "clarify_message": clarify_message_str,
          "reason": reason_str,
      }

  async def _invoke(self, prompt_inputs: dict[str, Any]) -> list[BotMessage]:
      # 1. 加载提示词模版
      prompt_template_str = load_prompt_template("clarify_respond")

      # 2. 实例化提示词模版对象
      prompt_template = PromptTemplate.from_template(template=prompt_template_str, template_format="jinja2")

      # 3. 构建chain
      chain = prompt_template | llm_client | StrOutputParser()

      # 4. 执行链
      result = await  chain.ainvoke(prompt_inputs)

      # 5. 返回结果
      return [BotMessage(text=result)]


  def _build_base_response(self,
                            reason:ClarifyReason,
                            state:DialogueState)->str:

      if reason is ClarifyReason.MULTIPLE_TRACKS:
          return "You mentioned a few different things at once. Let's take them one at a time — would you like to handle a request first, or ask a question first?"

      if reason is ClarifyReason.MISSING_FOCUSED_OBJECT:
          return "Please send me the item you'd like to ask about, and I'll take a look."

      if reason is ClarifyReason.MISSING_KNOWLEDGE_INTENT:
          return "Are you asking about product details, order details, or the after-sales and shipping rules?"

      if reason is ClarifyReason.MISSING_TRACK:
          return "Would you like to handle a request first, or ask a question first?"

      if reason is ClarifyReason.MISSING_TASK_COMMANDS:
          return "What would you like me to help with? For example checking an order, tracking a shipment, or filing a refund request."

      if reason is ClarifyReason.OBJECT_REQUIRES_INTENT:
          focused_object = state.focused_object
          if focused_object is not None and focused_object.type == "order":
              return "Got that order. Would you like to check its status, track the shipment, or file a refund request?"
          if focused_object is not None and focused_object.type == "product":
              return "Got that product. Would you like its details, its shipping status, or something about after-sales?"

      return "I want to make sure I understood you. Could you put it a bit more specifically?"
