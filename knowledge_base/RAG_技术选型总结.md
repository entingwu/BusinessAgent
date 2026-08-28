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
