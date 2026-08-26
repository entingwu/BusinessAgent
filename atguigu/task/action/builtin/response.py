from dataclasses import dataclass
from typing import Any

from jinja2 import Template
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser

from atguigu.domain.messages import BotMessage
from atguigu.domain.state import DialogueState
from atguigu.infrastructure.db_client import main_test
from atguigu.task.action.base import Action, ActionResult
from atguigu.infrastructure.llm_client import llm_client
from atguigu.chat_history.builder import ChatHistoryBuilder


class ActionResponse(Action):
  name = "action_response"

  async def run(self, action_kwargs: dict[str, Any], state: DialogueState) -> ActionResult:
    """
    According to action_kwargs text content, analyze placeholder.
    封装到ActionResult的BotMessage内容
    Goal: 响应YAML文件内容(user_flows以及system_flows中的action_response的args内容展示出来)
    展示的内容注意以下几点：
    1. 展示的结构是什么类型: dict/str
    2. 展示的内容（字符串）：
    2.1 有需要格式化的变量：
      - 例如 "好的，我们先处理{{context.started_flow_name}}"。（站位特点是双{{}}占位:jinja2模版)
      - 例如 "订单{{slots.order_number}}当前状态{{slots.order_status}}.{{slots.order_summary}}"
    2.2 没有需要格式化的变量：例如:"请简单说一下退款原因"
    Args:
        action_kwargs:
        state:
    """

    # 1. 获取响应的模式
    mode = action_kwargs.get('mode', 'static')

    # 2. 判断模式
    if mode == "rephrase":
      # rephrase: 先把YAML里的原始文案渲染出来，再让LLM在此基础上改写
      # a) 获取提示词
      prompt = action_kwargs['prompt']

      # b) 渲染的文本目标
      render_text = self._render_text(action_kwargs['text'], state)

      # c) 调用LLM
      rewritten = await self._call_llm(prompt, state, render_text)
      return ActionResult(messages=[BotMessage(text=rewritten)])

    elif mode == "generate":
      # generate: 从0到1由LLM生成，不依赖YAML里的原始文案，
      # 所以这里不读取也不渲染 text，current_response 交给 _call_llm 用默认空串
      # a) 获取提示词
      prompt = action_kwargs['prompt']

      # b) 调用LLM
      generated = await self._call_llm(prompt, state)
      return ActionResult(messages=[BotMessage(text=generated)])

    else:
      # static: 直接渲染YAML里的文案
      render_text = self._render_text(action_kwargs['text'], state)
      return ActionResult(messages=[BotMessage(text=render_text)])

  async def _call_llm(self, 
                      prompt_template_str: str, 
                      state: DialogueState, 
                      render_text: str = "") -> str:
    prompt_template = PromptTemplate.from_template(template=prompt_template_str)

    chain = prompt_template | llm_client | StrOutputParser()

    result = await chain.ainvoke({
      "history": ChatHistoryBuilder.build(state.current_session().turns[-5:]),
      "user_message": ChatHistoryBuilder.build_user_message_str(state.pending_turn.user_message),
      "current_response": render_text,
    })
    return result

  def _render_text(self, text: str, state: DialogueState) -> str:
    """
    Goal: 格式化响应文本中的变量
    Args:
        text:
    """
    template = Template(text)
    rendered_text = template.render(slots=state.active_task.slots if state.active_task is not None else None, 
                                    context=state.active_system_task)
    return rendered_text

@dataclass(slots=True)
class Context:
  started_flow_name: str


if __name__ == "__main__":
  template = Template("abc")
  # print(template.render(slots={"order_number": "12345"}))
  print(template.render(context=Context(started_flow_name="订单状态查询")))