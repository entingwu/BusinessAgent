"""
通过LangChain定义LLM 客户端
标准写法（PEP8规范）
# 1. sdk自带的依赖包

# 2. 第三组件的依赖包

# 3. 自己应用的依赖包
"""
import asyncio

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.output_parsers import StrOutputParser

from econ_agent.config.settings import settings

llm_client:BaseChatModel = init_chat_model(
  model_provider="openai",
  model=settings.llm_model,
  api_key=settings.llm_api_key,
  base_url=settings.llm_base_url,
)

async def main_test():
  """
  流式调用: stream: 同步的流式 astream: 异步流式
  非流失调用: invoke 同步非流式 ainvoke: 异步非流式
  Runnable provides Abstract Interface: Define common ways: 
  """

  # ai_message: AIMessage = llm_client.invoke("请你给我讲一个笑话，确保要幽默")
  chain = llm_client | StrOutputParser() # | LCEL表达式
  # output=llm_client.invoke(original_input)
  # final_output=StrOutputParser.invoke(output)
  content = chain.invoke("请你给我讲一个笑话，确保要幽默")
  print(content)


if __name__ == '__main__':
  asyncio.run(main_test())