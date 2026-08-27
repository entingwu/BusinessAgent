"""
Token 估算

项目不依赖在线 BPE 文件（tiktoken 首次使用要联网下载），这里用一个确定性的
纯 Python 估算函数。规范要求「分片宁可偏小不可跨节」，所以估算方向刻意偏保守：
中日韩字符按 1 token 计（实际 qwen 分词通常低于 1），拉丁字符按 4 字符 1 token 计。

入库切分与提示词上下文预算共用这一个函数，两处口径必须一致。
"""

_CJK_RANGES = (
  (0x3400, 0x4DBF),    # CJK 扩展 A
  (0x4E00, 0x9FFF),    # CJK 基本区
  (0xF900, 0xFAFF),    # CJK 兼容表意
  (0x3000, 0x303F),    # 中文标点
  (0xFF00, 0xFFEF),    # 全角字符
)


def _is_cjk(char: str) -> bool:
  code = ord(char)
  return any(start <= code <= end for start, end in _CJK_RANGES)


def estimate_tokens(text: str) -> int:
  """
  Goal: 估算一段文本的 token 数
  Args:
      text: 待估算文本
  Returns: int 估算的 token 数（下限 0）
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
  print(estimate_tokens("七天无理由退货的往返运费由消费者承担。"))
  print(estimate_tokens("RecursiveCharacterTextSplitter"))
