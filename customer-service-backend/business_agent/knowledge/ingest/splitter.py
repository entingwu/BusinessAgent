"""
切分：LangChain RecursiveCharacterTextSplitter（规范 C.4.2）

递归分隔符按 `\n## / \n### / \n\n / 。` 逐级下沉，长度函数用 tokens.estimate_tokens，
所以 KNOWLEDGE_CHUNK_SIZE / KNOWLEDGE_CHUNK_OVERLAP 两个配置项的单位是 token 而不是字符。

FAQ 与 CSV 走 SPLIT_MODE_ENTRY：一条一片，不做语义切分——条目本身即语义单元。
"""
from dataclasses import dataclass, field
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from business_agent.config.settings import settings
from business_agent.knowledge.ingest.loader import SPLIT_MODE_ENTRY, SPLIT_MODE_SEMANTIC, LoadedSource
from business_agent.knowledge.ingest.tokens import estimate_tokens

# 递归分隔符：先在标题处断，再在段落处断，最后才在句号处断
SEPARATORS = ["\n## ", "\n### ", "\n\n", "。", "\n", "，", " ", ""]


@dataclass(slots=True)
class PreparedChunk:
  """
  Goal: 切分完成、等待向量化的一个分片
  Attributes:
      chunk_id: "{source_id}#{position:04d}"，向量库与元数据库共用同一个 ID
      position: 片段在知识源内的序号，溯源用
  """
  chunk_id: str
  source_id: str
  source_type: str
  source_name: str
  title: str
  position: int
  content: str
  token_count: int = 0
  split_mode: str = SPLIT_MODE_SEMANTIC

  def metadata(self, embedding_model: str) -> dict[str, Any]:
    """
    Goal: 写进向量库的 metadata。检索时的过滤与溯源都靠它，
          值只能是 str / int / float / bool（Chroma 限制）。
    Args:
        embedding_model: 本次入库使用的 Embedding 模型名
    Returns: dict
    """
    return {
      "chunk_id": self.chunk_id,
      "source_id": self.source_id,
      "source_type": self.source_type,
      "source_name": self.source_name,
      "title": self.title,
      "position": self.position,
      "embedding_model": embedding_model,
    }


def build_text_splitter(chunk_size: int | None = None,
                        chunk_overlap: int | None = None) -> RecursiveCharacterTextSplitter:
  """
  Goal: 构造语义切分器
  Args:
      chunk_size: 分片大小（token），默认取 settings.knowledge_chunk_size
      chunk_overlap: 重叠长度（token），默认取 settings.knowledge_chunk_overlap
  Returns: RecursiveCharacterTextSplitter
  """
  return RecursiveCharacterTextSplitter(
    separators=SEPARATORS,
    chunk_size=chunk_size or settings.knowledge_chunk_size,
    chunk_overlap=chunk_overlap if chunk_overlap is not None else settings.knowledge_chunk_overlap,
    length_function=estimate_tokens,
    keep_separator=True,
  )


def split_source(source: LoadedSource,
                 chunk_size: int | None = None,
                 chunk_overlap: int | None = None) -> list[PreparedChunk]:
  """
  Goal: 把一份知识源切成分片，并给每片打上来源标识（知识源 ID、标题、片段位置）
  Args:
      source: 已加载的知识源
      chunk_size / chunk_overlap: 覆盖默认参数（阈值校准时用）
  Returns: list[PreparedChunk]
  """
  chunks: list[PreparedChunk] = []
  splitter = build_text_splitter(chunk_size, chunk_overlap)

  for entry in source.entries:
    if source.split_mode == SPLIT_MODE_ENTRY:
      pieces = [entry.text.strip()]
    else:
      pieces = [piece.strip() for piece in splitter.split_text(entry.text) if piece.strip()]

    for piece in pieces:
      position = len(chunks)
      chunks.append(PreparedChunk(
        chunk_id=f"{source.source_id}#{position:04d}",
        source_id=source.source_id,
        source_type=source.source_type,
        source_name=source.name,
        title=entry.title,
        position=position,
        content=piece,
        token_count=estimate_tokens(piece),
        split_mode=source.split_mode,
      ))

  return chunks


def embedding_text(chunk: PreparedChunk) -> str:
  """
  Goal: 送去向量化的文本。

        entry 模式（FAQ / CSV）：正文里已经包含问题原文，再拼一次标题只是重复，
        而且「常见问题：」这种共同前缀会给所有 FAQ 向量注入一个公共分量，
        抬高任意短问句与 FAQ 的基线相似度，直接影响阈值可分性（校准实测）。

        semantic 模式（文档）：正文开头已带章节名，这里再补一个知识源名称
        （「退货政策」「配送政策」），让文档主题这一层语境参与匹配。

  Args:
      chunk
  Returns: str
  """
  if chunk.split_mode == SPLIT_MODE_ENTRY:
    return chunk.content
  return f"{chunk.source_name}\n{chunk.content}"


if __name__ == '__main__':
  from business_agent.config.settings import settings as _settings
  from business_agent.knowledge.ingest.loader import discover_files, load_source

  root = _settings.resolved_knowledge_source_dir()
  total = 0
  for path in discover_files(root):
    loaded = load_source(root, path)
    prepared = split_source(loaded)
    total += len(prepared)
    token_counts = [chunk.token_count for chunk in prepared]
    print(f"{loaded.source_id:32s} chunks={len(prepared):3d} "
          f"tokens min={min(token_counts)} max={max(token_counts)} "
          f"avg={sum(token_counts) // len(token_counts)}")
  print(f"total chunks={total}")
