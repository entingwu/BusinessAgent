"""
HyDE (Hypothetical Document Embedding) — have the LLM write a hypothetical answer first, then
retrieve with that instead of the question.

## What it is for

A user asks "can I still return it if the tag is cut off"; the document says "the price tag and
anti-counterfeit label have not been cut off or damaged". There is a wording gap between a spoken
question and a written clause, and retrieving with the question directly asks the vector to cross
that gap. HyDE generates a **hypothetical answer in the register of the documents** and retrieves
with that, which moves the query into the same register as the corpus.

## The cost, stated first

**It adds one LLM call.** Comparable calls measure 2.0-2.7s here, while the whole retrieval chain
(local BGE-M3 inference + Milvus + rerank) is under a second. **HyDE is the most expensive step on
this path — more than everything else put together, twice over.**

So it is off by default, and whether it earns its place depends on a recall comparison: if
baseline recall is already high, there is little room for HyDE to improve and the latency buys
nothing.
"""
import logging

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate

from business_agent.infrastructure.llm_client import llm_client
from business_agent.prompt.loader import load_prompt_template

logger = logging.getLogger(__name__)


class HydeUnavailableError(RuntimeError):
  """
  Goal: generating the hypothetical answer failed. Callers should fall back to retrieving with the
        original question alone rather than failing the turn — HyDE improves recall, and losing it
        should not leave the user with no answer.
  """


async def generate_hypothetical_answer(question: str) -> str:
  """
  Goal: generate a hypothetical answer for a question, for the second retrieval route to use.
  Args:
      question: the user's original question
  Returns: the hypothetical answer; raises on failure, leaving the caller to fall back to a single
           route
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
