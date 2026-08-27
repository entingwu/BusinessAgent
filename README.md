# EconChatbot · 电商智能客服后端

基于 LLM 的多轮对话客服后端。用户的每句话先由 LLM 规划出本轮意图，再分流到**任务流程 / 知识问答 / 闲聊**三条轨道；任务轨道由 YAML 配置的状态机驱动，负责查订单、查物流、提交退款等需要多轮收集信息的业务。

技术栈：Python 3.12 · FastAPI · SQLAlchemy 2.0 (async) · aiomysql · LangChain · 通义千问（DashScope OpenAI 兼容接口）

> 本仓库只包含**客服后端**。它运行时还依赖两个不在本仓库的组件：提供订单/物流/商品数据的电商中台服务（默认 `http://127.0.0.1:18081`），以及聊天前端。见下方「外部依赖」。

## 快速开始

```bash
# 1. 装依赖（用 uv）
uv sync

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，至少填入 LLM_API_KEY 和 DATABASE_URL

# 3. 起服务
uv run python econ_agent/api/main.py
```

启动后：

- 接口文档 <http://127.0.0.1:18082/docs>
- 健康检查 `curl http://127.0.0.1:18082/`

试一轮对话：

```bash
curl -X POST http://127.0.0.1:18082/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"sender_id":"u1001","text":"我想查一下我的订单"}'
```

## 配置

所有配置走环境变量，由 [`econ_agent/config/settings.py`](econ_agent/config/settings.py) 从项目根目录的 `.env` 读入（pydantic-settings，缺项直接启动失败）。

| 变量 | 说明 | 示例 |
| --- | --- | --- |
| `LLM_MODEL` | 模型名 | `qwen-plus` |
| `LLM_BASE_URL` | OpenAI 兼容接口地址 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `LLM_API_KEY` | 模型密钥 | `sk-...` |
| `COMMERCE_API_BASE_URL` | 电商中台服务地址 | `http://127.0.0.1:18081` |
| `DATABASE_URL` | MySQL 异步连接串 | `mysql+aiomysql://user:pass@127.0.0.1:13306/custom_service?charset=utf8mb4` |
| `APP_HOST` / `APP_PORT` | 服务监听地址 | `0.0.0.0` / `18082` |

**`.env` 已被 `.gitignore` 排除，不要提交。** 新增配置项时请同步更新 `.env.example`。

## 外部依赖

| 依赖 | 用途 | 默认地址 |
| --- | --- | --- |
| MySQL | 持久化对话状态，表 `dialogue_states`（`sender_id` 主键 + `state_json` TEXT） | `127.0.0.1:13306` |
| 电商中台服务 | 订单详情 `/orders/{id}`、物流 `/orders/{id}/logistics`、商品、用户订单列表 | `127.0.0.1:18081` |
| LLM | 轮次规划、知识作答、闲聊、澄清话术 | DashScope |

中台服务和 MySQL 由同级项目 `ecommerce-service-backend` 的 `docker-compose.yml` 提供（不在本仓库）。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/` | 健康检查 |
| `POST` | `/api/chat` | 对话主入口，收 `sender_id` + `text`（或点击卡片时的 `object`） |
| `GET` | `/api/chat/history?sender_id=` | 该用户全部会话的聊天记录 |
| `GET` | `/docs` | Swagger UI |

`POST /api/chat` 的请求/响应模型见 [`econ_agent/api/schemas.py`](econ_agent/api/schemas.py)。

## 架构

```mermaid
flowchart TD
    A["POST /api/chat"] --> B["DialogueStateService<br/>从 MySQL 读出 DialogueState"]
    B --> C["DialogueEngine<br/>建会话 / 开新轮次"]
    C --> D{"消息类型"}
    D -->|文本| E["TurnPlanner<br/>LLM 规划本轮意图"]
    D -->|点击卡片| F["构造 SetSlotsCommand"]
    E --> G["TurnPlanValidator<br/>校验规划结果"]
    G -->|不合法| H["ClarifyResponder<br/>澄清追问"]
    G -->|task| I["TaskHandler"]
    G -->|knowledge| J["KnowledgeHandler"]
    G -->|chitchat| K["ChitChatHandler"]
    F --> I
    I --> L["CommandProcessor 改状态<br/>→ FlowExecutor 推进流程<br/>→ ActionRunner 执行动作"]
    J --> M["KnowledgeRegister<br/>api.order / api.product / faq / rag"]
    L --> N["回写 DialogueState 到 MySQL"]
    M --> N
    K --> N
    H --> N
    N --> O["ChatResponse"]
```

三层职责划分：

- **API 层**（`econ_agent/api/`）只做 API 模型 ↔ 领域模型的转换
- **Service 层**（`econ_agent/services/`）负责状态的读取与持久化边界
- **Engine 层**（`econ_agent/engines/`）是纯粹的状态计算，不碰 I/O

会话有 1 小时闲置过期机制：超时后关闭旧 session、重置运行时状态、开新 session（历史仍保留）。

## 任务流程（Flow）

流程用 YAML 配置在 [`flow_config/`](flow_config/)，由 `FlowLoader` 载入，改流程不用改代码。

**业务流程**（`user_flows.yml`）：`onboarding` 欢迎引导、`order_status_query` 订单状态查询、`logistics_tracking` 物流查询、`refund_request` 退款申请、`similar_product_recommendation` 相似商品推荐、`human_handoff` 人工客服

**系统流程**（`system_flows.yml`）：任务启动确认、信息收集、任务恢复/恢复失败、任务中断/取消/取消失败、无法处理

流程步骤类型有 `start` / `action` / `collect` / `end`，通过 `next` 串联。`collect` 步骤会挂起流程去追问用户填槽（slot），拿到后继续推进 —— 用户中途插话问别的，原流程会被中断并在之后尝试恢复。

**指令**（`econ_agent/task/commands/`）：`start_flow`、`set_slots`、`resume_flow`、`cancel_flow`。LLM 规划出的就是这些指令。

**动作**（`econ_agent/task/action/`）：内置 `action_response`（回复）、`action_listen`（等待用户输入）；业务动作有查订单状态、查物流、推荐相似商品。`econ_agent/task/action/customer/` 下的动作类由 `builder.py` 用 `pkgutil` **自动扫描注册**，新增动作只要在该目录放一个继承 `Action` 的类即可，不用手动登记。

## 知识问答

7 个知识意图定义在 [`econ_agent/knowledge/intents.py`](econ_agent/knowledge/intents.py)，每个意图挂载若干 provider：

| 意图 | Provider |
| --- | --- |
| `product_info` 商品信息 | `api.product`（需要商品卡片上下文） |
| `order_info` 订单信息 | `api.order`（需要订单卡片上下文） |
| `refund_policy` / `return_policy` / `shipping_policy` | `faq.default` + `rag.default` |
| `platform_rule` 平台规则 | `rag.default` |
| `general_ecommerce_info` 电商通用 | `faq.default` + `rag.default` |

## 目录结构

```
econ_agent/
├── api/            FastAPI 应用、路由、请求响应模型、依赖注入
├── services/       对话服务，状态读写边界
├── engines/        对话引擎 + 依赖装配（builder.py）
├── plan/           LLM 轮次规划与校验
├── task/           任务轨道：flows / commands / action
├── knowledge/      知识轨道：intents + provider
├── chitchat/       闲聊轨道
├── clarify/        澄清追问
├── domain/         领域模型：消息、上下文、对话状态
├── repository/     SQLAlchemy 持久化
├── infrastructure/ DB / HTTP / LLM 客户端
├── prompt/         Jinja2 提示词模板
└── config/         配置
flow_config/        流程 YAML
```

提示词模板在 `econ_agent/prompt/jinja2/`（`turn_plan` / `knowledge_respond` / `chitchat_respond` / `clarify_respond`），调整话术改这里。

## 调试

VS Code 用下面这个配置起服务，在任意文件打断点都能命中（`main.py` 没开 `reload`，是单进程）：

```jsonc
{
  "name": "FastAPI: EconChatbot",
  "type": "debugpy",
  "request": "launch",
  "program": "${workspaceFolder}/econ_agent/api/main.py",
  "console": "integratedTerminal",
  "cwd": "${workspaceFolder}",
  "env": { "PYTHONPATH": "${workspaceFolder}" }
}
```

注意：不要用「调试当前文件」去跑 `handler.py` 这类没有 `__main__` 的模块，那样只会导入一遍就退出，断点不会命中。若日后给 `uvicorn.run` 加了 `reload=True` 或 `workers=`，会 fork 子进程导致断点失效，需要额外加 `"subProcess": true`。

引擎默认开了 `echo=True`，控制台会打印全部 SQL，嫌吵可以在 [`econ_agent/infrastructure/db_client.py`](econ_agent/infrastructure/db_client.py) 关掉。

几个模块可以单独跑起来验证：

```bash
uv run python -m econ_agent.config.settings          # 配置是否读到
uv run python -m econ_agent.infrastructure.db_client # 数据库能否连通
uv run python -m econ_agent.task.action.builder      # 动作是否注册成功
```

这里必须用 `-m`：本项目没有以包的形式安装，`uv run python <文件路径>` 只会把该文件所在目录放进 `sys.path`，直接跑会报 `ModuleNotFoundError: No module named 'econ_agent'`。`econ_agent/api/main.py` 是例外，它自己在开头把项目根目录插进了 `sys.path`。
