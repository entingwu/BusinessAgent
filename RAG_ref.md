# BusinessAgent 的 RAG 实施计划

> 本文档分两部分。**上半部分是本项目的实施计划**（当前状态、重做决策、施工步骤）。
> 下半部分是原始的通用选型文档，与 `knowledge_base/RAG_技术选型总结.md` 内容相同——
> 它面向「可扩展到企业级」，是选型的**依据**而非结论。规范 `meta-business-agent.md`
> 附录 C.4 引用的就是下半部分。

------

## 一、当前状态：第一版已完成并通过验收

| 项 | 值 |
|---|---|
| Embedding | DashScope `text-embedding-v3`，1024 维，托管 |
| 向量库 | Chroma，本地持久化目录，纯 dense |
| 检索 | 余弦 Top-K=5 + 阈值 0.58 + metadata 过滤 |
| 语料 | 45 片（FAQ 22 + 政策文档 23），5 个知识源 |
| 验收 | 7.1 第 1 / 2 / 3 条全部「已实现」，经四轮独立审计 |

**实测基线**（冻结在 `customer-service-backend/knowledge_eval/BASELINE_chroma_dashscope.md`）：

- 有答案用例召回 **22 / 22**
- 无答案用例正确兜底 **11 / 12**
- 唯一失败项：「支持货到付款吗」top=0.7263，与「支持海外或港澳台配送吗？」表面句式相似
- 单条检索延迟 **0.68–0.85 秒**（含一次 embedding API 调用，不含生成）

三条路径**不经过 LLM**：未命中、低于阈值、向量库不可用，都在建立 LLM chain 之前返回常量文案。编造在机制上不可能发生，而不是靠模型自觉。

------

## 二、重做决策：对齐 knowledge_base/atguigu 的完整版技术栈

**两个项目共用下半部分这份选型依据，但走了不同路线**：本项目第一版走 MVP 路线（零新增外部服务），atguigu 走完整版路线。现决定收敛到完整版。

> **如实记录：这次重做不是数据推动的。** 下半部分「检索策略」一节建议一开始就上 Top-20 + 重排，本项目第一版明确拒绝了，理由是「等评测集证明排序是瓶颈时再加」。而评测集**并未证明**——基线有答案召回 22/22，Top-K 内一次都没漏召。这是选型对齐决策，不是性能问题倒逼。不写清楚，后来人会错误推广这个决策。

### 分环节变更

| 环节 | 第一版 | 重做后 |
|---|---|---|
| Embedding | DashScope `text-embedding-v3`（托管，dense） | **BGE-M3 本地**，一次产 dense + sparse |
| 向量库 | Chroma（进程内目录） | **Milvus standalone**（etcd + minio + milvus 三容器） |
| 检索 | 余弦 Top-K + 阈值 | **混合检索**（dense+sparse，`WeightedRanker` 内建融合） |
| 多路融合 | 无 | **RRF**（融合原问题与 HyDE 两路，权重 1.0 / 0.8，k=60） |
| 重排 | 无 | **DashScope `TextReRank`**（托管，不要 GPU） |
| 查询改写 | 无 | **HyDE**：先让 LLM 生成假设性答案，再对答案检索 |
| 编排 | 普通函数调用 | **LangGraph `StateGraph`** |
| 切分 / 语料 | `RecursiveCharacterTextSplitter` / 45 片中文 | 不变 |

------

## 三、本机硬约束

| 约束 | 实测 | 后果 |
|---|---|---|
| **无 CUDA** | Apple M1 Max | atguigu 的 `pyproject.toml` 强制走 `download.pytorch.org/whl/cu128`，**本机装不上**。须改用默认 PyPI 的 arm64 轮子；`use_fp16` 必须关闭 |
| **Docker 内存 8GB** | MySQL 已占一部分 | Milvus standalone 官方建议 ≥8GB。45 片量级跑得动，但要上调 Docker Desktop 上限 |
| **磁盘剩 32GB** | BGE-M3 权重 ~2.3GB + torch + Milvus 镜像 ~2GB | 够用，装完约剩 25GB |

------

## 四、照搬 atguigu 的代码再精简，不重新发明

核心机制的代码量远小于目录规模：`embedding_utils.py` 52 行里真正的逻辑约 10 行，`milvus_utils.py` 108 行里约 30 行，建表 schema 约 35 行，rerank 调用 50 行。

| 直接拿来 | 删掉 | 理由 |
|---|---|---|
| `embedding_utils.py`（BGE-M3 双向量） | `node_pdf_to_md` + `node_md_img`（689 行） | 知识源是 Markdown / CSV，没有 PDF 与图片 |
| `milvus_utils.py` 混合检索与建表 | `node_item_name_recognition` + `confirm`（652 行） | 商品走中台接口，不靠向量猜商品名 |
| `reranker_http_utils.py`（50 行） | `node_web_search_mcp`（72 行） | 不引入联网检索 |
| `node_rrf._rrf_merge`（核心约 5 行） | MinIO / MongoDB | 图片存储与对话历史都已有别的落点 |
| `node_search_embedding_hyde` | `node_rerank` 的动态 TopK 与断崖阈值（243 → 50） | 先用简单 top-N，调参属于校准阶段 |

**一处明确不能照搬：`node_answer_output`（240 行）。** 本项目的 `KnowledgeResponder` 有一条它不具备的性质——未命中在建立 LLM chain 之前就返回常量。那是验收第 3 条与规范 5.2 的红线，换掉是倒退。

**一处适配成本**：atguigu 的节点是同步的（`process(state)` 直接返回），本项目全链路 async，每个节点都要改写。

------

## 五、施工步骤与工时（合计 7–10 人天）

| Phase | 内容 | 人天 | 状态 |
|---|---|---:|---|
| 0 | **固定基线**：跑 34 条校准集，冻结对照数字 | 0.5 | ✅ 完成 |
| 1 | `docker-compose` 加 Milvus standalone 三容器（独立 project name） | 0.5–1 | |
| 2 | **BGE-M3 本地化**（照搬 `embedding_utils.py`）—— 风险最高 | 0.5–1 | ⏳ 进行中 |
| 3 | 建表 + 入库改写 + 全量重建索引 | 0.5–1 | |
| 4 | 混合检索接进现有 Provider | 0.5 | |
| 5 | rerank | 0.5 | |
| 6 | HyDE + RRF 两路融合 | 1–1.5 | |
| 7 | LangGraph 编排（图设计见规范 C.4.10） | 2–3 | |
| 8 | 重新校准 + 对照基线 + spec-auditor 复审 | 1 | |
| — | 保留 Chroma 作为 `VECTOR_BACKEND` 可切换退路 | +0.5 | |

**Phase 2 先于 Phase 1 做。** 它不碰 Docker，且是整条链路风险最高的一步——万一 BGE-M3 在 M1 CPU 上慢到不可接受，能在动 Docker 之前就知道。

> **估算修正记录。** 初稿给的是 9.5–14 人天，是按 atguigu 的目录行数推断的，没读代码，虚高了一多半。两处具体错误：把 3180 行节点代码当成待移植量（其中近 1400 行是本项目用不到的功能，其余大头是 LangGraph 节点样板）；把 RRF 重复计算了一次——**dense + sparse 的融合是 Milvus 内建的**，`hybrid_search(reqs=[dense, sparse], ranker=WeightedRanker(0.8, 0.2))` 一次调用完成，atguigu 的 `node_rrf` 融合的是**多路检索器**（向量 / HyDE / 联网）。**只有上了 HyDE 才需要 RRF。**
>
> 教训：规模不等于工作量，读代码之前给的数字是猜的。

------

## 六、改造后必须对照的五项

1. **有答案召回** —— 基线 22/22，不得低于
2. **无答案正确兜底** —— 基线 11/12，目标 12/12
3. **「支持货到付款吗」那条误通过** —— 基线唯一失败项。混合检索的 sparse 分量理论上能区分「货到付款」与「海外配送」，**这是本次改造最该兑现的一条**。若仍误通过，说明 RRF 与 rerank 没解决实际问题
4. **端到端延迟** —— 基线 0.68–0.85 秒。改造后大概率变差（BGE-M3 走 CPU，多了 HyDE 一次 LLM 调用与 rerank 一次 API 调用）。**允许变差，但要量出来，不能估**
5. **「未命中不调 LLM」的机制保证** —— **LangGraph 改写不得破坏**。图里 `node_fallback` / `node_degrade` 在拓扑上不通向 `node_answer`，这条约束从「读代码才能确认」变成「看图就能确认」

------

## 七、阈值会作废

BGE-M3 的分数分布与 `text-embedding-v3` 不同；加 rerank 之后「分数」的语义再变一次（rerank 分是相关性打分，不是余弦）。

**34 条校准集要重跑，而且要先决定阈值卡在三段中的哪一层**：向量分、RRF 融合分、还是 rerank 分。这是 Phase 8 的主要内容，也是最容易被低估的一步——前面全做对了，阈值卡错位置一样会同时制造「该兜底的编造了」和「该回答的说不知道」。

------
------

> 以下为原始通用选型文档，内容与 `knowledge_base/RAG_技术选型总结.md` 相同。
> 它是选型的**依据**，不是本项目的结论；本项目的结论在上半部分与规范附录 C.4。

# RAG 项目 —— 技术选型总结

本文档总结了本次检索增强生成（RAG, Retrieval-Augmented Generation）项目的关键技术选型、推荐方案、各选项的优缺点与实施建议，便于团队一致决策和后续落地。

## 一、项目目标（简要）
- 为业务提供高质量、可追溯的长文档问答/知识库查询能力。
- 支持文档更新、快速检索、低延迟响应与对外可扩展部署。
- 可插拔的组件（Embedding、向量库、LLM）以便在成本/性能/隐私间平衡。

## 二、核心组件（RAG 管道）
1. 文档获取与预处理（抓取、OCR、清洗、分段）
2. 文档分片与向量化（chunking + embeddings）
3. 向量索引与检索（ANN 或精确检索）
4. 召回排序与提示工程（reranking、prompt构造、温度控制）
5. LLM 生成（融合检索片段并生成可追溯答案）
6. 缓存/会话管理与监控

## 三、各环节技术选型与推荐

### 1) 文档预处理与分段（Chunking）
- 推荐：基于语义边界的分段（句子/段落 + overlap），默认大小 500–1000 tokens，重叠 50–200 tokens。
- 原因：保持上下文完整性，减少语义断裂，同时控制 embedding 计算与索引成本。
- 方案：先按段落/句子分割，再按 token 计数合并到目标大小；对代码/表格/长公式使用特殊策略（保留为单独片段）。

### 2) Embeddings（向量化）
- 推荐：先使用开源/托管的中等规模 embedding（例如 OpenAI Ada/通用向量模型 或 兼容的开源替代如 sentence-transformers/MiniLM / Mistral embedding），根据隐私要求决定是否自托管。
- 权衡：
  - 托管（OpenAI/Anthropic 等）：易用、质量稳定，但有数据外泄与成本考量。
  - 自托管（sentence-transformers、LLM+embedding pipeline）：降低外泄风险，可在本地 GPU 上运行，但需要维护与调参。

### 3) 向量数据库（Vector DB）
- 推荐优选：Milvus（向量搜索 + 企业级功能）、Weaviate（内建向量 + schema + semantic search）、Qdrant（轻量、Rust 性能）、或 Faiss（作为库嵌入在服务内）。
- 选择要点：持久化、复制/备份、近似最近邻（ANN）算法支持（HNSW/IVF/PQ）、过滤（metadata）能力、查询延迟、并发吞吐。
- 快速建议：开发/小规模：Qdrant 或 Faiss（嵌入）；生产/企业：Milvus 或 Weaviate（带治理与扩展）。

### 4) 检索策略（召回 + 排序）
- 推荐：先用 ANN 检索 top-k（如 k=20），再用语义或交叉嵌入 reranking 或 BM25 混合排序。
- 重要：支持 metadata 过滤（来源、文档类型、时间）以实现精准召回与可审计性。

### 5) LLM 模型
- 推荐策略：使用小/中型开源模型 + 业务敏感或高质量场景下调用更大模型作为后备（混合调用）。
- 具体选项：
  - 托管商用模型（OpenAI GPT-4o/4, Anthropic Claude）——质量最高，成本较高且有隐私考量。
  - 自托管开源模型（Llama2-family, Mistral, Falcon）：可控、成本可预测，但需要 GPU 与运维。
- Prompt 设计：使用检索片段 + system prompt + user query 模式，限制 token 上下文以降低生成偏差。
- 安全：强制去引用与可追溯性要求时，在生成文本里添加来源引用与相似度阈值检查。

### 6) 缓存与会话管理
- 推荐：对最近查询与常见问答做短期缓存（Redis）以降低成本与延迟。会话上下文应轻量保存，必要时再检索历史以避免上下文膨胀。

### 7) 指标与评估
- 核心指标：召回率/精确率（检索层）、生成质量（ROUGE/BLEU/EM + 人工打分）、用户满意度、响应延迟、成本/每次查询。
- 建议建立小规模标注集用于 A/B 测试与离线评估，长期收集用户反馈用于监控漂移。

### 8) 部署与基础设施
- 推荐：容器化（Docker）+ Kubernetes（生产）以支持弹性扩缩。向量 DB 可采用托管服务或自建集群。
- GPU 需求：LLM 推理与大型 embedding 需要 GPU；小模型可用 CPU 推理以降低成本。
- Observability：日志、链路追踪、prompt/检索审计（保存检索片段 id 与相似度）

### 9) 安全、合规与隐私
- 数据风险评估：敏感数据需脱敏或不发送第三方托管；优先使用自托管 embedding/LLM 或加密传输及合同保障。
- 可追溯性：每次生成都返回源文档 id + 匹配相似度与片段原文（或片段摘要）。
- 访问控制：细粒度权限、审计日志、数据保留策略。

### 10) 成本控制策略
- 混合模型调用（小模型首尝试，大模型按需调用）
- 缓存、批量 embedding 处理、离线索引重建窗口化调度
- 向量库选择上权衡存储成本（精度 vs 压缩）

## 四、简单比较（要点）
- 托管 LLM（OpenAI）: + 高质量、低运维； - 成本高、隐私风险
- 自托管开源模型: + 成本可控、可私有化； - 运维/硬件成本、需要调优
- Qdrant: 轻量、快速上手，适合中小团队
- Milvus: 企业级扩展性好，适合大数据量场景
- Embedding 服务（托管 vs 自托管）取决于隐私与成本要求

## 五、推荐的默认技术栈（初始版本）
- 文档预处理：Python（langchain-like pipeline）、Apache Tika（多格式解析）
- Embeddings：sentence-transformers（若自托管）或 OpenAI embeddings（若接受托管）
- 向量数据库：Qdrant（MVP）→ Milvus/Weaviate（Scale-up）
- 检索框架：FAISS（本地嵌入）+ metadata 过滤 或 使用 DB 原生检索
- LLM：OpenAI GPT（商业验证期）→ 自托管 Llama2/Mistral（生产私有化）
- 缓存/队列：Redis（缓存）、RabbitMQ/kafka（异步任务）
- 部署：Docker + k8s，CI/CD 用 GitHub Actions / Jenkins

## 六、落地建议与分阶段计划
1. MVP（2–4 周）
   - 使用托管 embedding + Qdrant + OpenAI 小模型验证端到端流程
   - 构建文档入库脚本与基本前端问答界面
   - 收集用户查询样本与质量反馈
2. 迭代（1–3 个月）
   - 根据负载与隐私需求，评估迁移到自托管 embedding 或向量库
   - 加入 reranker（小模型或 cross-encoder）提升精度
   - 增加监控、审计与自动重训练管道
3. 规模化/企业化（3–6 个月）
   - 启用 k8s + GPU 集群，自托管中/大型模型
   - 多租户隔离、数据治理、合规审计

## 七、附：提示工程与可追溯性实践
- Prompt 模板中显式要求“列出每条引用来源（文档 id + 段落）”。
- 对返回内容使用置信度阈值：当相似度低于阈值时提示用户“未找到高置信度答案”。
- 保存每次检索的 top-k 文档 id 与相似度于对话记录，便于回溯与纠正。

## 八、下一步建议（行动项）
- 快速搭建 MVP 并在内部做 1 周的可用性测试。
- 同步成立一个小组负责：监控/评估指标、隐私合规评估、成本估算。
- 根据 MVP 反馈决定是否优先自托管 embedding 或向量库迁移。

---
若需要，可把本文档转成 README、slides，或把每一部分拆成具体实现任务并生成开发 TODO 清单。
