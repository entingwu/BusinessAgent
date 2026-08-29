"""
Token estimation.

The project does not depend on an online BPE file (tiktoken downloads one on first use), so this
is a deterministic pure-Python estimate. The spec asks that "a chunk should err on the small side
rather than span sections", so the estimate deliberately leans conservative: CJK characters count
as 1 token each (qwen's tokeniser usually gives less), Latin text as 1 token per 4 characters.

Ingest-time splitting and the prompt context budget share this one function — the two must measure
the same way.
"""

_CJK_RANGES = (
  (0x3400, 0x4DBF),    # CJK Extension A
  (0x4E00, 0x9FFF),    # CJK Unified Ideographs
  (0xF900, 0xFAFF),    # CJK Compatibility Ideographs
  (0x3000, 0x303F),    # CJK punctuation
  (0xFF00, 0xFFEF),    # fullwidth forms
)


def _is_cjk(char: str) -> bool:
  code = ord(char)
  return any(start <= code <= end for start, end in _CJK_RANGES)


def estimate_tokens(text: str) -> int:
  """
  Goal: estimate the token count of a piece of text
  Args:
      text: the text to measure
  Returns: the estimated token count (never below 0)
  """
  if not text:
    return 0

  cjk_count = 0
  other_count = 0
  for char in text:
    if _is_cjk(char):
      cjk_count += 1
    else:
      other_count += 1

  return cjk_count + (other_count + 3) // 4


if __name__ == '__main__':
  # The first string stays Chinese deliberately: it is the only thing here that exercises the CJK
  # branch of the estimate. Englishifying it would leave both prints on the latin path and the
  # 1-token-per-character rule untested.
  print(estimate_tokens("七天无理由退货的往返运费由消费者承担。"))
  print(estimate_tokens("RecursiveCharacterTextSplitter"))
