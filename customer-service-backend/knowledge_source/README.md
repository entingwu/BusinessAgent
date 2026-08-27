# 知识源目录

本目录存放入库到向量库的商家知识文档，由 `business_agent/knowledge/ingest` 流水线读取。

| 子目录 | 知识源类型 | 切分方式 |
|---|---|---|
| `faq/` | `faq` | 一条一片，不做语义切分 |
| `policy/` | `document` | `RecursiveCharacterTextSplitter`，按 `\n## / \n### / \n\n / 。` 递归切分 |

## 配置纪律（规范 3.1.1）

**易变数据一律不写进本目录**：商品价格、库存数量、订单状态、物流单号只能来自业务接口。
写进来会造成「检索到的旧值」与「接口的新值」并存，这是最难排查的一类客服事故。

本目录只放稳定事实：政策条款、时效承诺、流程步骤、平台规则。

## 入库

```bash
# customer-service-backend/
uv run python -m business_agent.knowledge.ingest ingest        # 全量入库（内容未变则跳过）
uv run python -m business_agent.knowledge.ingest ingest --force
uv run python -m business_agent.knowledge.ingest list
uv run python -m business_agent.knowledge.ingest delete --source-id policy.return_policy
uv run python -m business_agent.knowledge.ingest query --text "七天无理由怎么退"
```
