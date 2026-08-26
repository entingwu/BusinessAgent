from atguigu.chat_history.builder import ChatHistoryBuilder
from atguigu.domain.state import DialogueState
from atguigu.infrastructure import llm_client
from atguigu.prompt.loader import load_prompt_template
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser

from atguigu.domain.messages import BotMessage


class ChitChatResponder:

  async def respond(self,
                    chitchat: str,
                    state: DialogueState) -> list[BotMessage]:
      # 1. 加载提示词模版内容
      prompt_template_str = load_prompt_template("knowledge_respond")

      # 2. 实例化提示词模版对象
      prompt_template = PromptTemplate.from_template(template=prompt_template_str, template_format="jinja2")

      # 3. 定义chain
      chain = prompt_template | llm_client | StrOutputParser()

      # 4. 调用
      result = await  chain.ainvoke({
          "user_message": chitchat,
          "history": ChatHistoryBuilder.build(state.current_session().turns[-10:]),
      })

      return [BotMessage(text=result)]