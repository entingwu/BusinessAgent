from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from business_agent.domain.state import DialogueState


class KnowledgeUnavailableError(RuntimeError):
  """
  Goal: the retrieval stack is unavailable (the vector store or the embedding service is down).
        Callers answer "cannot look this up right now, let me hand you to a human" and must never
        fall back to the model's own knowledge (spec 5.1 / C.4.7 — a degraded path required
        already in tier 1).
  """


@dataclass(slots=True)
class KnowledgeChunk:
  """
  Goal: one retrieval result. Besides the text it carries provenance and similarity, which is
        what makes an answer traceable (spec 5.2).
  Attributes:
      chunk_id: the chunk id; internal logs trace back by it
      source_id: the knowledge source id
      source_type: faq / document / api
      source_title: source name + section name — the human-readable provenance
      position: the chunk's index within its source
      score: cosine similarity. Chunks from business APIs have none, so it is None; they count
          as authoritative and skip threshold filtering
      provider_id: which provider returned it; traces are written per provider to keep the
          origins apart
  """
  content: str
  chunk_id: str | None = None
  source_id: str | None = None
  source_type: str | None = None
  source_title: str | None = None
  position: int | None = None
  score: float | None = None
  provider_id: str | None = None

  def citation(self) -> str:
    """
    Goal: build a human-readable provenance string for internal logs and for the chunk labels in
          the prompt
    Returns: str
    """
    parts = [part for part in (self.source_title, self.chunk_id) if part]
    return " | ".join(parts) if parts else (self.source_id or "unknown")

  def trace(self) -> dict[str, Any]:
    """
    Goal: trace information — the internal record of hit chunk ids and similarities
          (spec 3.1.2 / 5.2).
    Returns: dict
    """
    return {
      "chunk_id": self.chunk_id,
      "source_id": self.source_id,
      "source_type": self.source_type,
      "source_title": self.source_title,
      "position": self.position,
      "score": round(self.score, 4) if self.score is not None else None,
      "provider_id": self.provider_id,
    }


class Provider(ABC):
  provider_id: str

  @abstractmethod
  async def retrival(self, state: DialogueState) -> list[KnowledgeChunk]:
    pass
