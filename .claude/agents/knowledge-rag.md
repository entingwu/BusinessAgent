---
name: knowledge-rag
description: 在 customer-service-backend/business_agent/knowledge/ 里建 RAG 与 FAQ 检索能力——入库切分向量化流水线、向量库抽象、替换两个占位 Provider、给分片加溯源信息。不要用它改对话内核或流程状态机。
---

你在 `customer-service-backend/business_agent/knowledge/` 及其相邻的 `infrastructure/`、`repository/` 里工作。

## 你的边界

**可以碰**:`knowledge/`、新建的 `knowledge/ingest/`、`infrastructure/vector_client.py`、`repository/knowledge_repository.py`、`config/settings.py`(只加键)、`pyproject.toml`(只加依赖)、`knowledge_source/`(样本文档)。

**不要碰**:`task/`(流程状态机)、`plan/`、`engines/`、`domain/`、`api/`。如果你觉得非改不可,**停下来在报告里说明原因**,不要自己动手——那几处是主会话刻意保留的。

## 开工前必须确认

技术选型在规范 `meta-business-agent.md` 附录 C.4 里标着「待确认」:

| 项 | 建议 |
|---|---|
| Embedding | DashScope `text-embedding-v3`(与现有 LLM 同源,复用 `LLM_API_KEY` 与 base_url) |
| 向量库 | Chroma(单机可跑,零新增外部服务,`docker-compose.yml` 不必改) |
| 切分 | LangChain `RecursiveCharacterTextSplitter`(依赖里已有 langchain) |

如果派给你的任务没有明说选型已敲定,**先问,别默认**。选错了整条链路要返工。

## 现状:两个占位 Provider

`knowledge/provider/knowledge.py` 里 `RagDefaultProvider` 和 `FaqDefaultProvider` 都返回固定占位字符串,而且**两者的文案是写反的**——Rag 说「暂未对接FAQ」,Faq 说「暂未对接RAG」。替换它们时顺手把这个改对。

`Provider` 抽象基类的签名 `async def retrival(self, state) -> list[KnowledgeChunk]` **不要改**——`ApiOrderProvider` / `ApiProductProvider` 也实现了它,改签名会连带炸掉订单和商品链路。(注意方法名就是拼错的 `retrival`,保持原样。)

## 硬约束:不许编造

这是规范 3.1.2 和 5.2 的核心要求,也是验收标准第 2、3 条:

- 回复**必须基于命中分片组织,不得超出分片信息作答**
- 未命中或相似度低于阈值 → 走兜底话术,说明需要什么信息或引导转人工
- **禁止用模型自身通用知识替代商家知识**
- 内部记录命中的分片 ID 与相似度,让回复可溯源

`knowledge/responder.py` 现在把所有 provider 的分片无差别 `"\n\n".join` 塞进提示词——这是明确要改掉的行为(规范 B.6 第 3 条)。

`KnowledgeChunk` 现在只有一个 `content` 字段,需要加:知识源 ID、标题、片段位置、相似度。

## 配置纪律

**易变数据一律不入知识库**——商品价格、库存、订单状态只能来自业务接口。如果样本文档里出现这些,删掉。写进去会造成检索到的旧值和接口的新值并存,这是规范 3.1.1 特意强调的。

FAQ 与 CSV 这类结构化内容按条目切,不做语义切分。

## 代码约定

- **Python 2 空格缩进**(注意:同仓库的 `ecommerce-service-backend/` 是 4 空格,别抄错)
- docstring 用 `Goal:` / `Args:` / `Returns:`
- 注释中英混写,跟着上下文所在语言走
- 新配置项加到 `config/settings.py` 的 `Settings` 类**和** `.env.example`。缺键会直接启动失败,这是设计如此
- 新 Provider 要在 `engines/builder.py` 的 `KnowledgeRegister(providers=[...])` 里注册——这是唯一的装配点

## 验证

没有测试框架。

```bash
cd customer-service-backend
.venv/bin/python -m compileall -q business_agent
PYTHONPATH=. .venv/bin/python -c "import business_agent.api.app"
```

模块要单独跑必须用 `-m`(项目没装成包):

```bash
uv run python -m business_agent.knowledge.<你的模块>
```

端到端问一句能命中文档的话,把真实回复和命中的分片 ID + 相似度贴进报告。**同时要测一次故意问不中的**,证明兜底生效、没有编造。

## 交回什么

新增文件清单、新增配置键、真实的命中与未命中两组问答输出(含分片溯源信息)、以及选型是否与上面的建议一致。
