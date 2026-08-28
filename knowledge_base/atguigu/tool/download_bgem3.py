from atguigu.tool.logger import logger

from modelscope.hub.snapshot_download import snapshot_download

snapshot_download("BAAI/bge-m3", cache_dir="D:/ai_models/modelscope_cache/models")

logger.info("模型已下载")