"""
LLM client, defined through LangChain.
Import order follows PEP 8:
# 1. standard library

# 2. third-party packages

# 3. this application
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
  Goal: the embedding service is unavailable. Callers take the degraded path ("cannot look this
        up right now, let me hand you to a human") and must never fall back to answering from the
        model's own knowledge (spec 5.1 / C.4.7).
  """


# Embedding shares the LLM's DashScope credentials (settings.llm_api_key /
# settings.llm_base_url); no second SDK and no second API key are introduced (spec C.4.1, rule 2).
# check_embedding_ctx_length=False: the DashScope-compatible API only accepts strings, not the
# array of token ids the OpenAI client sends by default.
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
  Goal: the current embedding model name. Ingest and retrieval read the same source, so the two
        cannot drift apart.
  Returns: str
  """
  return settings.embedding_model


async def embed_query(text: str) -> list[float]:
  """
  Goal: embed one user question (the retrieval side)
  Args:
      text: the text to embed
  Returns: a list[float] of length settings.embedding_dimensions
  Raises: EmbeddingUnavailableError when the service is unavailable, for the caller to degrade on
  """
  try:
    return await embedding_client.aembed_query(text)
  except Exception as error:  # noqa: BLE001 - every underlying error collapses into one degrade signal
    raise EmbeddingUnavailableError(f"embedding query failed: {error}") from error


async def embed_documents(texts: list[str]) -> list[list[float]]:
  """
  Goal: embed a batch of knowledge chunks (the ingest side)
  Args:
      texts: the chunk texts to embed
  Returns: a list[list[float]] matching the inputs one-to-one
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
  Streaming: stream (sync), astream (async)
  Non-streaming: invoke (sync), ainvoke (async)
  Runnable provides Abstract Interface: Define common ways: 
  """

  # ai_message: AIMessage = llm_client.invoke("Tell me a joke, and make it funny")
  chain = llm_client | StrOutputParser()  # | is the LCEL composition operator
  # output=llm_client.invoke(original_input)
  # final_output=StrOutputParser.invoke(output)
  content = chain.invoke("Tell me a joke, and make it funny")
  print(content)

  # Embedding self-check: the dimensions must match settings.embedding_dimensions
  vector = await embed_query("What is the return policy")
  print(f"embedding model={embedding_model_name()} dim={len(vector)}")


if __name__ == '__main__':
  asyncio.run(main_test())
