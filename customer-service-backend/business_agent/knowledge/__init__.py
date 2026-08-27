"""
knowledge 包初始化：把检索溯源日志真正打开。

背景：仓库里唯一的 logging 配置在几个 `__main__` 调试入口里，服务进程（uvicorn）跑起来时
root logger 停在 WARNING，`knowledge/responder.py` 与 `knowledge/provider/rag.py` 里那两行
记录「命中分片 ID + 相似度」的 logger.info 一行都不会输出——规范 3.1.2「内部记录命中的
分片 ID 与相似度」等于空转。

做法：本包自己给 `business_agent.knowledge` 这个 logger 挂一个 StreamHandler 并设级别，
`propagate = False`，不依赖也不改动 root / uvicorn 的日志配置（`api/` 不属于本模块的范围）。
`knowledge` 包下任何模块被导入前都会先执行本文件，因此服务进程与命令行入口都覆盖得到。

级别由 KNOWLEDGE_LOG_LEVEL 控制，设成 WARNING 即可关掉检索日志。
"""
import logging
import sys

from business_agent.config.settings import settings

_HANDLER_TAG = "business_agent.knowledge.trace_handler"


def _configure_logger() -> None:
  """
  Goal: 给 business_agent.knowledge 挂一个自有 handler，幂等，不影响其他 logger
  """
  logger = logging.getLogger("business_agent.knowledge")

  level_name = (settings.knowledge_log_level or "INFO").upper()
  logger.setLevel(getattr(logging, level_name, logging.INFO))

  # 重复导入（例如 uvicorn --reload）时不要挂第二个 handler，否则日志会翻倍
  if any(getattr(handler, "_tag", None) == _HANDLER_TAG for handler in logger.handlers):
    return

  handler = logging.StreamHandler(stream=sys.stdout)
  handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
  handler._tag = _HANDLER_TAG  # type: ignore[attr-defined]
  logger.addHandler(handler)

  # 自带 handler，就不要再往上冒泡，避免 root 也配了 handler 时打两遍
  logger.propagate = False


_configure_logger()
