"""
知识入库流水线：加载 → 切分 → 向量化 → 写索引（规范 C.4.8）

命令行入口在 business_agent/knowledge/ingest/__main__.py：

    uv run python -m business_agent.knowledge.ingest ingest
"""
