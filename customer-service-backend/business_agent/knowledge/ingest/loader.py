"""
知识源加载：Markdown / TXT / CSV

MVP 的知识源是自己写的政策文档与 FAQ 表，Python 直读即可，
不引 Apache Tika 这类多格式解析（规范 C.4.2 文档解析环节）。
"""
import csv
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_SUFFIXES = (".md", ".markdown", ".txt", ".csv")

# FAQ / CSV 一条一片，不做语义切分；文档走递归语义切分
SPLIT_MODE_ENTRY = "entry"
SPLIT_MODE_SEMANTIC = "semantic"

SOURCE_TYPE_FAQ = "faq"
SOURCE_TYPE_DOCUMENT = "document"

_SECTION_PATTERN = re.compile(r"^(#{2,3})\s+(.*)$", re.MULTILINE)


@dataclass(slots=True)
class SourceEntry:
  """
  Goal: 知识源里的一个自然单元
        文档 = 一个章节（`##` / `###` 之间的内容）
        FAQ  = 一条问答
  """
  title: str
  text: str


@dataclass(slots=True)
class LoadedSource:
  """
  Goal: 一份加载完成的知识源
  Attributes:
      source_id: 由相对路径推导，例如 policy/return_policy.md -> policy.return_policy
      source_type: faq / document
      split_mode: entry / semantic
      content_hash: 原文哈希，用于判断是否需要重新入库
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
  Goal: 递归找出目录下所有支持的知识源文件
  Args:
      root_dir: 知识源根目录
  Returns: list[Path] 已排序，保证入库顺序稳定
  """
  if not root_dir.exists():
    return []
  files = [
    path for path in root_dir.rglob("*")
    if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES and not path.name.startswith(".")
  ]
  # README 是给人看的说明，不入库
  files = [path for path in files if path.stem.lower() != "readme"]
  return sorted(files)


def build_source_id(root_dir: Path, file_path: Path) -> str:
  """
  Goal: 由相对路径推导稳定的知识源 ID（更新与删除都按它定位）
  Args:
      root_dir: 知识源根目录
      file_path: 文件路径
  Returns: str 例如 "faq.after_sales_faq"
  """
  relative = file_path.relative_to(root_dir)
  parts = list(relative.parts[:-1]) + [relative.stem]
  return ".".join(part.replace(".", "_") for part in parts)


def detect_source_type(root_dir: Path, file_path: Path) -> str:
  """
  Goal: 判定知识源类型。faq 目录下的文件与所有 CSV 视为 FAQ，其余为文档。
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
  Goal: 把一个文件加载成 LoadedSource
  Args:
      root_dir: 知识源根目录
      file_path: 文件路径
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
    file_path=str(file_path),
    split_mode=split_mode,
    content_hash=content_hash,
    entries=entries,
  )


def _load_csv(file_path: Path) -> tuple[str, list[SourceEntry]]:
  """
  Goal: CSV 一行一条，一条即一片
        识别 question/answer 两列；没有这两列时把所有列拼成 "列名：值"
  Args:
      file_path
  Returns: (知识源名称, 条目列表)
  """
  entries: list[SourceEntry] = []
  with file_path.open("r", encoding="utf-8", newline="") as handle:
    reader = csv.DictReader(handle)
    for row in reader:
      normalized = {(key or "").strip().lower(): (value or "").strip() for key, value in row.items()}
      question = normalized.get("question") or normalized.get("问题") or ""
      answer = normalized.get("answer") or normalized.get("答案") or ""
      category = normalized.get("category") or normalized.get("分类") or ""

      if question or answer:
        title = f"常见问题：{question}" if question else f"常见问题 条目 {len(entries) + 1}"
        lines = [f"问：{question}", f"答：{answer}"]
        if category:
          lines.append(f"分类：{category}")
        text = "\n".join(lines)
      else:
        title = f"条目 {len(entries) + 1}"
        text = "\n".join(f"{key}：{value}" for key, value in normalized.items() if value)

      entries.append(SourceEntry(title=title, text=text))

  return file_path.stem, entries


def _load_markdown(file_path: Path, raw_text: str) -> tuple[str, list[SourceEntry]]:
  """
  Goal: Markdown 先按 `##` / `###` 标题拆成章节，章节名即分片标题（溯源可读）
        章节内部是否继续切分交给 splitter 按 token 上限决定
  Args:
      file_path / raw_text
  Returns: (文档标题, 章节条目列表)
  """
  title_match = re.search(r"^#\s+(.*)$", raw_text, re.MULTILINE)
  document_title = title_match.group(1).strip() if title_match else file_path.stem

  matches = list(_SECTION_PATTERN.finditer(raw_text))
  if not matches:
    return document_title, [SourceEntry(title=document_title, text=raw_text.strip())]

  entries: list[SourceEntry] = []
  # 首个标题之前的引言部分
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
      # 标题写回正文，检索时标题里的关键词同样参与匹配
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
