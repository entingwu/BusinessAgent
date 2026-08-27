from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from business_agent.domain.state import DialogueState


class KnowledgeUnavailableError(RuntimeError):
  """
  Goal: 知识检索链路不可用（向量库或 Embedding 服务挂了）。
        上层据此回「暂时查不了，帮你转人工」，不得退化为用模型自身知识作答
        （规范 5.1 / C.4.7，第一档就要实现的降级路径）。
  """


@dataclass(slots=True)
class KnowledgeChunk:
  """
  Goal: 一条检索结果。除正文外带来源标识与相似度，让回复可溯源（规范 5.2）。
  Attributes:
      chunk_id: 分片 ID，内部日志按它回溯
      source_id: 知识源 ID
      source_type: faq / document / api
      source_title: 知识源名称 + 章节名，给人看的来源
      position: 片段在知识源内的序号
      score: 余弦相似度。业务接口类分片没有相似度，为 None，视为权威结果不参与阈值过滤
      provider_id: 哪个 Provider 召回的，溯源落库时要按它区分来路
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
    Goal: 拼一个人可读的来源串，用于内部日志与提示词里的分片标注
    Returns: str
    """
    parts = [part for part in (self.source_title, self.chunk_id) if part]
    return " | ".join(parts) if parts else (self.source_id or "unknown")

  def trace(self) -> dict[str, Any]:
    """
    Goal: 溯源信息。内部记录命中的分片 ID 与相似度（规范 3.1.2 / 5.2）。
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
