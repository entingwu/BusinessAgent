"""
HyDE（Hypothetical Document Embedding）—— 先让 LLM 写一段假设性答案，再拿它去检索。

## 它解决什么

用户问「吊牌剪了还能退吗」，文档写的是「吊牌与防伪标识未被剪除或损坏」。
口语提问与书面条款之间有措辞鸿沟，直接拿问题去检索，向量要跨越这个鸿沟。
HyDE 先生成一段**书面语气的假设答案**，用它去检索，等于把查询挪到了与文档
同一个语域里。

## 代价必须先说清楚

**它多一次 LLM 调用。** 本项目实测同类调用 2.0–2.7 秒，而整条检索链路
（BGE-M3 本地推理 + Milvus + rerank）总共不到 1 秒。**HyDE 是这条链路上
最贵的一步，比其余全部加起来还贵一倍以上。**

因此它默认关闭，且值不值得开要看 Phase 8 的召回对照——如果基线召回已经很高
（本项目 23/23），HyDE 能改善的空间本来就小，那这个延迟就买不到东西。
"""
import logging

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate

from business_agent.infrastructure.llm_client import llm_client
from business_agent.prompt.loader import load_prompt_template

logger = logging.getLogger(__name__)


class HydeUnavailableError(RuntimeError):
  """
  Goal: 假设性答案生成失败。上层应退回「只用原问题检索」，而不是整轮失败——
        HyDE 是提升召回的增强项，它挂了不该让用户拿不到答案。
  """


async def generate_hypothetical_answer(question: str) -> str:
  """
  Goal: 为问题生成一段假设性答案，供第二路检索使用。
  Args:
      question: 用户原问题
  Returns: str 假设性答案；生成失败时抛出，由调用方决定退回单路检索
  Raises: HydeUnavailableError
  """
  question = (question or "").strip()
  if not question:
    return ""
  try:
    template = PromptTemplate.from_template(
      template=load_prompt_template("hyde_generate"), template_format="jinja2")
    chain = template | llm_client | StrOutputParser()
    answer = (await chain.ainvoke({"question": question})).strip()
  except Exception as error:
    raise HydeUnavailableError(f"hyde generation failed: {type(error).__name__}: {error}") from error
  if not answer:
    raise HydeUnavailableError("hyde generation returned empty text")
  logger.info("hyde_generated question=%r answer=%r", question, answer[:120])
  return answer
