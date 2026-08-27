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
from langchain_openai import OpenAIEmbeddings

from business_agent.config.settings import settings

llm_client:BaseChatModel = init_chat_model(
  model_provider="openai",
  model=settings.llm_model,
  api_key=settings.llm_api_key,
  base_url=settings.llm_base_url,
)


class EmbeddingUnavailableError(RuntimeError):
  """
  Goal: Embedding 服务不可用。上层据此走「暂时查不了，帮你转人工」的降级路径，
        绝不允许退化成用模型自身知识作答（规范 5.1 / C.4.7）。
  """


# Embedding 与 LLM 共用 DashScope 凭据（settings.llm_api_key / settings.llm_base_url），
# 不引入第二套 SDK 与第二份 API Key（规范 C.4.1 第 2 条）。
# check_embedding_ctx_length=False: DashScope 兼容接口只接受字符串入参，
# 不接受 OpenAI 客户端默认的 token id 数组。
embedding_client: OpenAIEmbeddings = OpenAIEmbeddings(
  model=settings.embedding_model,
  api_key=settings.llm_api_key,
  base_url=settings.llm_base_url,
  dimensions=settings.embedding_dimensions,
  chunk_size=settings.embedding_batch_size,
  check_embedding_ctx_length=False,
)


def embedding_model_name() -> str:
  """
  Goal: 返回当前 Embedding 模型名。入库与检索共用同一个来源，杜绝两边配不一致。
  Returns: str
  """
  return settings.embedding_model


async def embed_query(text: str) -> list[float]:
  """
  Goal: 把一句用户提问向量化（检索侧）
  Args:
      text: 待向量化的文本
  Returns: list[float] 长度为 settings.embedding_dimensions 的向量
  Raises: EmbeddingUnavailableError 服务不可用时抛出，交由上层降级
  """
  try:
    return await embedding_client.aembed_query(text)
  except Exception as error:  # noqa: BLE001 - 任何底层异常都统一收敛成降级信号
    raise EmbeddingUnavailableError(f"embedding query failed: {error}") from error


async def embed_documents(texts: list[str]) -> list[list[float]]:
  """
  Goal: 批量把知识分片向量化（入库侧）
  Args:
      texts: 待向量化的分片文本列表
  Returns: list[list[float]] 与入参一一对应的向量列表
  Raises: EmbeddingUnavailableError
  """
  if not texts:
    return []
  try:
    return await embedding_client.aembed_documents(texts)
  except Exception as error:  # noqa: BLE001
    raise EmbeddingUnavailableError(f"embedding documents failed: {error}") from error


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

  # Embedding 自检：维度必须与 settings.embedding_dimensions 一致
  vector = await embed_query("退货政策是什么")
  print(f"embedding model={embedding_model_name()} dim={len(vector)}")


if __name__ == '__main__':
  asyncio.run(main_test())
