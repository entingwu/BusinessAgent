"""
Small text predicates shared across layers.

`contains_cjk` lives here rather than in either of its two callers because they sit in unrelated
packages (`handoff/` and `knowledge/`) and neither may import the other. Two private copies of the
same predicate is the shape this repo keeps getting bitten by: they agree until one of them is
edited, and nothing warns when they stop agreeing.
"""

# CJK Unified Ideographs, U+4E00-U+9FFF. Deliberately not the extension blocks: this is used to
# decide which of two calibrated thresholds applies, and every character the calibration set was
# measured on is in this range. Widening it would change a gate without re-measuring it.
_CJK_START = "一"
_CJK_END = "鿿"


def contains_cjk(text: str) -> bool:
  """
  Goal: does the text contain at least one Han character?
  Args:
      text: any string
  Returns: True if at least one character is in CJK Unified Ideographs
  """
  return any(_CJK_START <= char <= _CJK_END for char in text)
