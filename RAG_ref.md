# BusinessAgent 的 RAG 实施计划

> 本项目 RAG 的实施计划：当前状态与实测基线、重做决策、施工步骤与工时。
> 与规范 `meta-business-agent.md` 附录 C.4 的分工是：规范记录**决策与验收口径**，本文档记录**怎么做**。
>
> 通用的 RAG 选型依据（面向「可扩展到企业级」的那份）见 `knowledge_base/RAG_技术选型总结.md`，
> 本仓库不再另存一份副本。

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
| **无 CUDA，但有 MPS** | Apple M1 Max，`torch.backends.mps.is_available() == True` | atguigu 的 `pyproject.toml` 强制走 `download.pytorch.org/whl/cu128`，**本机装不上**，须用默认 PyPI 的 arm64 轮子；`use_fp16` 关闭（需 CUDA）。**但 Metal 后端可用，不是只能纯 CPU** —— 实测见下 |
| **Docker 内存 8GB** | MySQL 已占一部分 | Milvus standalone 官方建议 ≥8GB。45 片量级跑得动，但要上调 Docker Desktop 上限 |
| **磁盘剩 32GB** | BGE-M3 权重 ~2.3GB + torch + Milvus 镜像 ~2GB | 够用，装完约剩 25GB |

**Phase 2 实测（2026-08-28，BGE-M3 本地）：**

| | 模型加载（缓存后） | 单条查询延迟（中位） | 批量 34 条 |
|---|---|---|---|
| 基线：DashScope `text-embedding-v3` | — | **0.68–0.85s**（API 往返） | — |
| BGE-M3 **CPU** | 1.7s | **0.308s** | 0.75s（0.022s/条） |
| BGE-M3 **MPS** | 1.6s | **0.148s** | 1.28s（0.038s/条） |

**「本地推理会更慢」这个预期是错的，反了。** 本地 MPS 比托管 API 快 4.5 倍——托管方案的延迟主要花在网络往返上，不是计算。首次加载 710 秒是下载 2.3GB 权重，缓存之后 1.7 秒。

**MPS 与 CPU 各有胜场**：MPS 单条快一倍（交互路径），CPU 批量反而更快（入库路径）——MPS 每次调用有固定开销，批量时摊薄不掉。因此 `EMBEDDING_DEVICE` 应当可配置，查询侧用 `mps`、入库脚本用 `cpu`。

dense 1024 维（与 `text-embedding-v3` 相同，元数据表的 `embedding_dimensions` 不用改）；sparse 正常产出，实测首条 10 个非零项。

**Phase 2 踩到的一个坑**：`pymilvus[model]` 没有把 `datasets` 与 `FlagEmbedding` 声明为硬依赖，而是在首次构造 `BGEM3EmbeddingFunction` 时**自己去 `pip install`**——uv 管理的 venv 里没有 pip，直接失败。必须在 `pyproject.toml` 里显式列出。atguigu 的依赖清单里写了 `flagembedding>=1.3.5`，原因就在这里。


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
| 2 | **BGE-M3 本地化**（照搬 `embedding_utils.py`） | 0.5–1 | ✅ 完成，延迟反优于基线 |
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
4. **端到端延迟** —— 基线 0.68–0.85 秒。**Embedding 这一段实测变快了**（MPS 0.148s，快 4.5 倍），但链路新增 HyDE 一次 LLM 调用（最贵）与 rerank 一次 API 调用。净效果未知，**必须量，不能估**——「本地推理会更慢」这个预期已经被证伪一次了
5. **「未命中不调 LLM」的机制保证** —— **LangGraph 改写不得破坏**。图里 `node_fallback` / `node_degrade` 在拓扑上不通向 `node_answer`，这条约束从「读代码才能确认」变成「看图就能确认」

------

## 七、阈值会作废

BGE-M3 的分数分布与 `text-embedding-v3` 不同；加 rerank 之后「分数」的语义再变一次（rerank 分是相关性打分，不是余弦）。

**34 条校准集要重跑，而且要先决定阈值卡在三段中的哪一层**：向量分、RRF 融合分、还是 rerank 分。这是 Phase 8 的主要内容，也是最容易被低估的一步——前面全做对了，阈值卡错位置一样会同时制造「该兜底的编造了」和「该回答的说不知道」。
