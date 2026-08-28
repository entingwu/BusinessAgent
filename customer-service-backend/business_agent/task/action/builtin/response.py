from dataclasses import dataclass
from typing import Any

from jinja2 import Template
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser

from business_agent.domain.messages import BotMessage
from business_agent.domain.state import DialogueState
from business_agent.infrastructure.db_client import main_test
from business_agent.task.action.base import Action, ActionResult
from business_agent.infrastructure.llm_client import llm_client
from business_agent.chat_history.builder import ChatHistoryBuilder


class ActionResponse(Action):
  name = "action_response"
  description = ("Render the text configured in YAML as a reply, optionally having the LLM "
                 "rewrite it or generate one from scratch")
  # The text comes from the YAML args; there is no fixed slot input, since which slots the
  # template references is decided by configuration. It only produces a reply and writes back no
  # slots.
  is_write = False

  async def run(self, action_kwargs: dict[str, Any], state: DialogueState) -> ActionResult:
    """
    According to action_kwargs text content, analyze placeholder.
    Wrap the configured text into the BotMessage of an ActionResult.

    Goal: render the args of an action_response step from user_flows.yml or system_flows.yml.

    Two things to keep in mind about what gets rendered:
    1. The args may be a dict or a plain string.
    2. The string may or may not contain variables:
    2.1 With variables — Jinja2 placeholders, recognisable by the double braces:
      - e.g. "Sure, let's start with {{context.started_flow_name}}."
      - e.g. "Order {{slots.order_number}} is currently: {{slots.order_status}}. {{slots.order_summary}}"
    2.2 Without variables — e.g. "Could you tell me briefly why you want a refund?"
    Args:
        action_kwargs:
        state:
    """

    # Quick-reply buttons: available in all three modes. They are never sent through the LLM
    # rewrite, because a button label has to line up with the intent set, and letting the model
    # polish it produces wording the planner no longer recognises.
    suggestions = list(action_kwargs.get('suggestions') or [])

    # 1. Read the response mode
    mode = action_kwargs.get('mode', 'static')

    # 2. Dispatch on it
    if mode == "rephrase":
      # rephrase: render the original YAML text first, then have the LLM rewrite from it
      # a) Get the prompt
      prompt = action_kwargs['prompt']

      # b) Render the target text
      render_text = self._render_text(action_kwargs['text'], state)

      # c) Call the LLM
      rewritten = await self._call_llm(prompt, state, render_text)
      return ActionResult(messages=[BotMessage(text=rewritten, suggestions=suggestions)])

    elif mode == "generate":
      # generate: written by the LLM from scratch, independent of the YAML text — so text is
      # neither read nor rendered here, and current_response is left to _call_llm's empty default
      # a) Get the prompt
      prompt = action_kwargs['prompt']

      # b) Call the LLM
      generated = await self._call_llm(prompt, state)
      return ActionResult(messages=[BotMessage(text=generated, suggestions=suggestions)])

    else:
      # static: render the YAML text as-is
      render_text = self._render_text(action_kwargs['text'], state)
      return ActionResult(messages=[BotMessage(text=render_text, suggestions=suggestions)])

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
    Goal: interpolate the variables in the response text
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
  print(template.render(context=Context(started_flow_name="order status lookup")))