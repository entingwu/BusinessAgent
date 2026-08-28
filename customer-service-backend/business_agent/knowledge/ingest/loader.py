"""
Knowledge source loading: Markdown / TXT / CSV.

The MVP's sources are policy documents and FAQ tables we wrote ourselves, which Python reads
directly — no multi-format parser such as Apache Tika (spec C.4.2, document parsing).
"""
import csv
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_SUFFIXES = (".md", ".markdown", ".txt", ".csv")

# FAQ and CSV: one entry per chunk, no semantic splitting. Documents go through recursive
# semantic splitting.
SPLIT_MODE_ENTRY = "entry"
SPLIT_MODE_SEMANTIC = "semantic"

SOURCE_TYPE_FAQ = "faq"
SOURCE_TYPE_DOCUMENT = "document"

_SECTION_PATTERN = re.compile(r"^(#{2,3})\s+(.*)$", re.MULTILINE)


@dataclass(slots=True)
class SourceEntry:
  """
  Goal: one natural unit within a knowledge source
        document = one section (the content between `##` / `###` headings)
        FAQ      = one question-and-answer entry
  """
  title: str
  text: str


@dataclass(slots=True)
class LoadedSource:
  """
  Goal: a fully loaded knowledge source
  Attributes:
      source_id: derived from the relative path, e.g. policy/return_policy.md ->
          policy.return_policy
      source_type: faq / document
      split_mode: entry / semantic
      content_hash: hash of the source text, used to decide whether a re-ingest is needed
  """
  source_id: str
  source_type: str
  name: str
  file_path: str
  split_mode: str
  content_hash: str
  entries: list[SourceEntry] = field(default_factory=list)


def discover_files(root_dir: Path) -> list[Path]:
  """
  Goal: recursively find every supported knowledge source file under a directory
  Args:
      root_dir: the knowledge source root directory
  Returns: a sorted list[Path], so ingest order stays stable
  """
  if not root_dir.exists():
    return []
  files = [
    path for path in root_dir.rglob("*")
    if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES and not path.name.startswith(".")
  ]
  # README is documentation for people, not a knowledge source
  files = [path for path in files if path.stem.lower() != "readme"]
  return sorted(files)


def build_source_id(root_dir: Path, file_path: Path) -> str:
  """
  Goal: derive a stable knowledge source id from the relative path (updates and deletes both
        locate a source by it)
  Args:
      root_dir: the knowledge source root directory
      file_path: path to the file
  Returns: e.g. "faq.after_sales_faq"
  """
  relative = file_path.relative_to(root_dir)
  parts = list(relative.parts[:-1]) + [relative.stem]
  return ".".join(part.replace(".", "_") for part in parts)


def detect_source_type(root_dir: Path, file_path: Path) -> str:
  """
  Goal: decide the source type. Files under faq/ and every CSV count as FAQ; everything else is
        a document.
  Args:
      root_dir / file_path
  Returns: str faq | document
  """
  relative_parts = {part.lower() for part in file_path.relative_to(root_dir).parts[:-1]}
  if "faq" in relative_parts or file_path.suffix.lower() == ".csv":
    return SOURCE_TYPE_FAQ
  return SOURCE_TYPE_DOCUMENT


def load_source(root_dir: Path, file_path: Path) -> LoadedSource:
  """
  Goal: load one file into a LoadedSource
  Args:
      root_dir: the knowledge source root directory
      file_path: path to the file
  Returns: LoadedSource
  """
  raw_text = file_path.read_text(encoding="utf-8")
  content_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
  source_id = build_source_id(root_dir, file_path)
  source_type = detect_source_type(root_dir, file_path)

  if file_path.suffix.lower() == ".csv":
    name, entries = _load_csv(file_path)
    split_mode = SPLIT_MODE_ENTRY
  elif file_path.suffix.lower() in (".md", ".markdown"):
    name, entries = _load_markdown(file_path, raw_text)
    split_mode = SPLIT_MODE_ENTRY if source_type == SOURCE_TYPE_FAQ else SPLIT_MODE_SEMANTIC
  else:
    name = file_path.stem
    entries = [SourceEntry(title=name, text=raw_text.strip())]
    split_mode = SPLIT_MODE_ENTRY if source_type == SOURCE_TYPE_FAQ else SPLIT_MODE_SEMANTIC

  entries = [entry for entry in entries if entry.text.strip()]

  return LoadedSource(
    source_id=source_id,
    source_type=source_type,
    name=name,
    # Store the path **relative to the knowledge source root**, not an absolute one.
    # Absolute paths bake in whichever checkout ran the ingest — and when that checkout is a
    # temporary git worktree, deleting it leaves metadata pointing at a path that no longer
    # exists, with nothing to catch it: ingest only compares content_hash and stats only counts
    # rows. A relative path is identical across every checkout, so the failure cannot happen.
    file_path=str(file_path.relative_to(root_dir)),
    split_mode=split_mode,
    content_hash=content_hash,
    entries=entries,
  )


def _load_csv(file_path: Path) -> tuple[str, list[SourceEntry]]:
  """
  Goal: one CSV row per entry, one entry per chunk.
        Recognises question/answer columns; without them, every column is joined as
        "column name: value"
  Args:
      file_path
  Returns: (source name, list of entries)
  """
  entries: list[SourceEntry] = []
  with file_path.open("r", encoding="utf-8", newline="") as handle:
    reader = csv.DictReader(handle)
    for row in reader:
      normalized = {(key or "").strip().lower(): (value or "").strip() for key, value in row.items()}
      # The wording below is not display text: it decides what the chunk body looks like, and the
      # chunk body is what gets embedded. The similarity threshold is calibrated against exactly
      # this wording (see the note on embedding_text() in splitter.py), so changing it silently
      # invalidates the threshold — retrieval keeps working, the scores just shift, and questions
      # that used to be answered start hitting the fallback.
      # **Changing any of this means re-running `ingest --force` and `calibrate`.**
      #
      # The Chinese column names are kept as fallbacks so a Chinese-authored CSV still loads; the
      # shipped corpus uses the English headers.
      question = normalized.get("question") or normalized.get("问题") or ""
      answer = normalized.get("answer") or normalized.get("答案") or ""
      category = normalized.get("category") or normalized.get("分类") or ""

      if question or answer:
        title = f"FAQ: {question}" if question else f"FAQ entry {len(entries) + 1}"
        lines = [f"Q: {question}", f"A: {answer}"]
        if category:
          lines.append(f"Category: {category}")
        text = "\n".join(lines)
      else:
        title = f"Entry {len(entries) + 1}"
        text = "\n".join(f"{key}：{value}" for key, value in normalized.items() if value)

      entries.append(SourceEntry(title=title, text=text))

  return file_path.stem, entries


def _load_markdown(file_path: Path, raw_text: str) -> tuple[str, list[SourceEntry]]:
  """
  Goal: split Markdown into sections at `##` / `###` headings, using the heading as the chunk
        title so traces stay readable. Whether a section is split further is left to the splitter
        and its token limit.
  Args:
      file_path / raw_text
  Returns: (document title, list of section entries)
  """
  title_match = re.search(r"^#\s+(.*)$", raw_text, re.MULTILINE)
  document_title = title_match.group(1).strip() if title_match else file_path.stem

  matches = list(_SECTION_PATTERN.finditer(raw_text))
  if not matches:
    return document_title, [SourceEntry(title=document_title, text=raw_text.strip())]

  entries: list[SourceEntry] = []
  # The preamble before the first heading
  preamble = raw_text[: matches[0].start()]
  preamble = _strip_document_title(preamble).strip()
  if preamble:
    entries.append(SourceEntry(title=document_title, text=preamble))

  for index, match in enumerate(matches):
    section_title = match.group(2).strip()
    start = match.end()
    end = matches[index + 1].start() if index + 1 < len(matches) else len(raw_text)
    body = raw_text[start:end].strip()
    if not body:
      continue
    entries.append(SourceEntry(
      title=f"{document_title} / {section_title}",
      # Write the heading back into the body so its keywords take part in matching at retrieval
      # time
      text=f"{section_title}\n{body}",
    ))

  return document_title, entries


def _strip_document_title(text: str) -> str:
  return re.sub(r"^#\s+.*$", "", text, count=1, flags=re.MULTILINE)


if __name__ == '__main__':
  from business_agent.config.settings import settings

  root = settings.resolved_knowledge_source_dir()
  for path in discover_files(root):
    source = load_source(root, path)
    print(f"{source.source_id:32s} type={source.source_type:9s} mode={source.split_mode:9s} entries={len(source.entries)}")
