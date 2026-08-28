"""
Splitting, via LangChain's RecursiveCharacterTextSplitter (spec C.4.2).

The recursive separators step down through `\n## / \n### / \n\n / 。`, and the length function is
tokens.estimate_tokens — so KNOWLEDGE_CHUNK_SIZE and KNOWLEDGE_CHUNK_OVERLAP are measured in
tokens, not characters.

FAQ and CSV sources use SPLIT_MODE_ENTRY: one entry per chunk, no semantic splitting, because an
entry already is the semantic unit.

The text that goes to the embedding model is not the same as the chunk body — see
embedding_text(), whose comment explains why an FAQ must not have its title prepended, and how
that is bound to KNOWLEDGE_SCORE_THRESHOLD.
"""
from dataclasses import dataclass, field
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from business_agent.config.settings import settings
from business_agent.knowledge.ingest.loader import SPLIT_MODE_ENTRY, SPLIT_MODE_SEMANTIC, LoadedSource
from business_agent.knowledge.ingest.tokens import estimate_tokens

# Recursive separators: break at headings first, then paragraphs, and only then at sentence ends
SEPARATORS = ["\n## ", "\n### ", "\n\n", "。", "\n", "，", " ", ""]


@dataclass(slots=True)
class PreparedChunk:
  """
  Goal: one split chunk, awaiting embedding
  Attributes:
      chunk_id: "{source_id}#{position:04d}" — the vector store and the metadata table share it
      position: the chunk's index within its source, used for tracing
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
    Goal: the metadata written into the vector store. Retrieval-time filtering and tracing both
          rely on it, and values may only be str / int / float / bool (a Chroma restriction).
    Args:
        embedding_model: the name of the embedding model used for this ingest
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
  Goal: build the semantic splitter
  Args:
      chunk_size: chunk size in tokens; defaults to settings.knowledge_chunk_size
      chunk_overlap: overlap in tokens; defaults to settings.knowledge_chunk_overlap
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
  Goal: split one knowledge source into chunks, stamping each with its provenance (source id,
        title, position)
  Args:
      source: the loaded knowledge source
      chunk_size / chunk_overlap: override the defaults (used during threshold calibration)
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
  Goal: the text that gets embedded.

  ⚠️ Read this whole note before changing this function — it decides directly whether
  KNOWLEDGE_SCORE_THRESHOLD is still usable.

  entry mode (FAQ / CSV): **embed the body only, and never prepend chunk.title.**
    A title looks like 「常见问题：退货运费谁出？」 and the question itself already appears in the
    body, so prepending it duplicates the text. The real damage is that the shared prefix
    「常见问题：」 injects a common component into every FAQ vector, raising the baseline
    similarity between any short question and every FAQ, which collapses the separable range on
    the spot.

  semantic mode (documents): the body already opens with its section name, so the source name is
    added here (「退货政策」, 「配送政策」) to bring the document-topic layer of context into the
    match.

  Measured during calibration (34 samples, see knowledge_eval/calibration_set.jsonl; reproduce
  with `python -m business_agent.knowledge.ingest calibrate`):

    | scheme                          | lowest score with answer | highest without (outliers removed) |
    |----------------------------|-------------|------------------------------|
    | title + body (the old way)      | 0.5858                   | 0.5518                             |
    | body only                       | 0.5773                   | 0.5605                             |
    | source name + body (current)    | 0.6030                   | 0.5605                             |

  The current scheme's separable range is (0.5605, 0.6030), whose midpoint gives
  KNOWLEDGE_SCORE_THRESHOLD=0.58.
  Reverting to "title + body" squeezes the range to (0.5518, 0.5858), which puts 0.58 inside the
  answerable set — a batch of questions that should be answered would start falling back instead.

  So: **changing this function means re-running calibrate and re-deriving the threshold.** The two
  are bound together.

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
