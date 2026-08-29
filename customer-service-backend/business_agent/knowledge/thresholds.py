"""
The degraded path's vector threshold, and the one place that decides which of the two applies.

This module exists so the decision cannot be made twice. Both retrieval paths — the functional one
in `provider/rag.py` and the LangGraph one in `graph.py` — need it, and a private copy in each is
precisely the shape this repo keeps getting bitten by: the two agree until one is edited.
"""
from business_agent.config.settings import settings
from business_agent.infrastructure.text import contains_cjk


def resolve_vector_threshold(question: str) -> float:
  """
  Goal: the cosine threshold to gate this question with on the degraded path.

  Two calibrated values, picked by the script the question is written in. The corpus is English and
  BGE-M3 is multilingual, so a Chinese question is a *cross-lingual* retrieval and scores about
  0.10-0.13 lower than the same question asked in English. Measured on 2026-08-29 against
  knowledge_eval/calibration_set.jsonl and calibration_set_zh.jsonl, with RERANK_ENABLED=false:

  | band    | hits          | misses        | threshold | recall | correct fallback |
  |---------|---------------|---------------|-----------|--------|------------------|
  | English | 0.7345-0.8230 | 0.5702-0.7485 | 0.75      | 26/29  | 8/8              |
  | Chinese | 0.6172-0.6966 | 0.5446-0.6246 | 0.625     | 26/27  | 8/8              |

  **The two bands interleave.** The top English miss (0.7485, "what do I need to open a shop on
  your platform") outscores *every* Chinese hit. That is the whole reason this is conditioned on the
  question rather than being one number: a scalar set high enough to block that miss blocks all of
  Chinese, and one set low enough to admit Chinese admits it.

  Neither value has a comfortable margin — English clears its top miss by 0.0015 — because on this
  corpus the raw cosine hit and miss ranges genuinely overlap in both languages. Both values are
  therefore chosen to favour the fallback over recall, which is the correct bias for a path that
  only runs while rerank is down. Re-derive both after any corpus, chunking or embedding-model
  change; `calibrate` takes `--file` for the Chinese set.

  Args:
      question: the user's question, in whatever language they wrote it
  Returns: the cosine threshold below which a match counts as a miss
  """
  if contains_cjk(question):
    return settings.knowledge_score_threshold_cjk
  return settings.knowledge_score_threshold
