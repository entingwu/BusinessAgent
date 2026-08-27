# EconChatbot · E-commerce Customer Service Backend

An LLM-driven multi-turn customer service backend. Every user message is first run through an LLM planner that decides the intent for the turn, then routed down one of three tracks — **task flow / knowledge Q&A / chitchat**. The task track is driven by a YAML-configured state machine and handles business cases that need multi-turn slot filling: order status, logistics tracking, refund requests, and so on.

Stack: Python 3.12 · FastAPI · SQLAlchemy 2.0 (async) · aiomysql · LangChain · Qwen (DashScope OpenAI-compatible API)

> This repository contains the **customer service backend only**. At runtime it also depends on two components that live outside this repo: an e-commerce backend service that serves order/logistics/product data (default `http://127.0.0.1:18081`), and a chat frontend. See [External dependencies](#external-dependencies).

## Quick start

```bash
# 1. Install dependencies (via uv)
uv sync

# 2. Configure environment variables
cp .env.example .env
# Edit .env — at minimum set LLM_API_KEY and DATABASE_URL

# 3. Run the service
uv run python econ_agent/api/main.py
```

Once running:

- API docs: <http://127.0.0.1:18082/docs>
- Health check: `curl http://127.0.0.1:18082/`

Try a turn of conversation:

```bash
curl -X POST http://127.0.0.1:18082/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"sender_id":"u1001","text":"I want to check my order"}'
```

## Configuration

All configuration is environment-driven. [`econ_agent/config/settings.py`](econ_agent/config/settings.py) loads it from `.env` at the project root via pydantic-settings — a missing key fails startup immediately.

| Variable | Description | Example |
| --- | --- | --- |
| `LLM_MODEL` | Model name | `qwen-plus` |
| `LLM_BASE_URL` | OpenAI-compatible endpoint | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `LLM_API_KEY` | Model API key | `sk-...` |
| `COMMERCE_API_BASE_URL` | E-commerce backend service | `http://127.0.0.1:18081` |
| `DATABASE_URL` | Async MySQL connection string | `mysql+aiomysql://user:pass@127.0.0.1:13306/custom_service?charset=utf8mb4` |
| `APP_HOST` / `APP_PORT` | Listen address | `0.0.0.0` / `18082` |

**`.env` is excluded by `.gitignore` — never commit it.** When adding a new setting, update `.env.example` alongside it.

## External dependencies

| Dependency | Purpose | Default address |
| --- | --- | --- |
| MySQL | Persists dialogue state in table `dialogue_states` (`sender_id` primary key + `state_json` TEXT) | `127.0.0.1:13306` |
| E-commerce service | Order detail `/orders/{id}`, logistics `/orders/{id}/logistics`, products, per-user order lists | `127.0.0.1:18081` |
| LLM | Turn planning, knowledge answering, chitchat, clarification wording | DashScope |

The e-commerce service and MySQL are provided by the sibling `ecommerce-service-backend` project's `docker-compose.yml` (not in this repo).

## API

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/` | Health check |
| `POST` | `/api/chat` | Main chat entry point; takes `sender_id` + `text` (or `object` when the user clicks a card) |
| `GET` | `/api/chat/history?sender_id=` | Full chat history across all of that user's sessions |
| `GET` | `/docs` | Swagger UI |

Request/response models for `POST /api/chat` are defined in [`econ_agent/api/schemas.py`](econ_agent/api/schemas.py).

## Architecture

```mermaid
flowchart TD
    A["POST /api/chat"] --> B["DialogueStateService<br/>load DialogueState from MySQL"]
    B --> C["DialogueEngine<br/>open session / begin turn"]
    C --> D{"Message type"}
    D -->|text| E["TurnPlanner<br/>LLM plans the turn"]
    D -->|card click| F["build SetSlotsCommand"]
    E --> G["TurnPlanValidator<br/>validate the plan"]
    G -->|invalid| H["ClarifyResponder<br/>ask for clarification"]
    G -->|task| I["TaskHandler"]
    G -->|knowledge| J["KnowledgeHandler"]
    G -->|chitchat| K["ChitChatHandler"]
    F --> I
    I --> L["CommandProcessor mutates state<br/>→ FlowExecutor advances the flow<br/>→ ActionRunner executes actions"]
    J --> M["KnowledgeRegister<br/>api.order / api.product / faq / rag"]
    L --> N["persist DialogueState back to MySQL"]
    M --> N
    K --> N
    H --> N
    N --> O["ChatResponse"]
```

Responsibilities are split across three layers:

- **API layer** (`econ_agent/api/`) only converts between API models and domain models
- **Service layer** (`econ_agent/services/`) owns the state load/save boundary
- **Engine layer** (`econ_agent/engines/`) is pure state computation and touches no I/O

Sessions expire after 1 hour of inactivity: the old session is closed, runtime state is reset, and a new session starts (history is preserved).

## Task flows

Flows are configured as YAML under [`flow_config/`](flow_config/) and loaded by `FlowLoader`, so changing a flow needs no code change.

**Business flows** (`user_flows.yml`): `onboarding`, `order_status_query`, `logistics_tracking`, `refund_request`, `similar_product_recommendation`, `human_handoff`

**System flows** (`system_flows.yml`): task started, collect information, task resumed / resume failed, task interrupted / canceled / cancel failed, cannot handle

Step types are `start` / `action` / `collect` / `end`, chained together via `next`. A `collect` step suspends the flow to ask the user for a slot value, then resumes once it is filled — if the user changes the subject mid-flow, the original flow is interrupted and an attempt is made to resume it later.

**Commands** (`econ_agent/task/commands/`): `start_flow`, `set_slots`, `resume_flow`, `cancel_flow`. These are exactly what the LLM planner emits.

**Actions** (`econ_agent/task/action/`): built-in `action_response` (reply) and `action_listen` (wait for user input); business actions cover order status lookup, logistics lookup, and similar-product recommendation. Action classes under `econ_agent/task/action/customer/` are **discovered and registered automatically** by `builder.py` via `pkgutil` — to add one, just drop a class extending `Action` into that directory; no manual registration needed.

## Knowledge Q&A

Seven knowledge intents are defined in [`econ_agent/knowledge/intents.py`](econ_agent/knowledge/intents.py), each wired to one or more providers:

| Intent | Providers |
| --- | --- |
| `product_info` | `api.product` (requires product card context) |
| `order_info` | `api.order` (requires order card context) |
| `refund_policy` / `return_policy` / `shipping_policy` | `faq.default` + `rag.default` |
| `platform_rule` | `rag.default` |
| `general_ecommerce_info` | `faq.default` + `rag.default` |

## Project layout

```
econ_agent/
├── api/            FastAPI app, routes, request/response models, DI
├── services/       Dialogue service — the state read/write boundary
├── engines/        Dialogue engine + dependency wiring (builder.py)
├── plan/           LLM turn planning and validation
├── task/           Task track: flows / commands / action
├── knowledge/      Knowledge track: intents + providers
├── chitchat/       Chitchat track
├── clarify/        Clarification prompts
├── domain/         Domain models: messages, contexts, dialogue state
├── repository/     SQLAlchemy persistence
├── infrastructure/ DB / HTTP / LLM clients
├── prompt/         Jinja2 prompt templates
└── config/         Settings
flow_config/        Flow YAML
```

Prompt templates live in `econ_agent/prompt/jinja2/` (`turn_plan` / `knowledge_respond` / `chitchat_respond` / `clarify_respond`) — edit these to change the assistant's wording.

## Debugging

Use this VS Code configuration to launch the service; breakpoints anywhere will be hit, since `main.py` does not enable `reload` and therefore runs single-process:

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

Do not use "debug current file" on a module like `handler.py` that has no `__main__` — it will just import once and exit, and no breakpoint will be hit. If `reload=True` or `workers=` is ever added to `uvicorn.run`, uvicorn forks a subprocess and breakpoints stop working; add `"subProcess": true` in that case.

The engine runs with `echo=True`, so every SQL statement is printed to the console. Turn it off in [`econ_agent/infrastructure/db_client.py`](econ_agent/infrastructure/db_client.py) if it gets noisy.

A few modules can be run standalone as a sanity check:

```bash
uv run python -m econ_agent.config.settings          # config loads?
uv run python -m econ_agent.infrastructure.db_client # database reachable?
uv run python -m econ_agent.task.action.builder      # actions registered?
```

`-m` is required here: the project is not installed as a package, so `uv run python <file path>` only puts that file's own directory on `sys.path` and running it directly fails with `ModuleNotFoundError: No module named 'econ_agent'`. `econ_agent/api/main.py` is the exception — it inserts the project root into `sys.path` itself at the top of the file.
