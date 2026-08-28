# BusinessAgent · LLM E-commerce Customer Service

An LLM-driven multi-turn customer service system. Every user message is first run through an LLM planner that decides the intent for the turn, then routed down one of three tracks — **task flow / knowledge Q&A / chitchat**. The task track is driven by a YAML-configured state machine and handles business cases that need multi-turn slot filling: order status, logistics tracking, refund requests, and so on.

The repository holds all three services:

| Directory | What it is | Stack | Port |
| --- | --- | --- | --- |
| [`customer-service-backend/`](customer-service-backend/) | Dialogue backend — planning, flows, knowledge, persistence | Python 3.12 · FastAPI · SQLAlchemy 2.0 (async) · LangChain · Qwen | 18082 |
| [`customer-service-frontend/`](customer-service-frontend/) | Chat UI — conversation, cards, quick replies | Vue 3 · Vite | 5174 |
| [`ecommerce-service-backend/`](ecommerce-service-backend/) | Mock e-commerce service — orders, logistics, products | Python · FastAPI · SQLAlchemy (sync) · MySQL 8 | 18081 |

## Project status

This repository is being evolved toward the spec in [`meta-business-agent.md`](meta-business-agent.md) (Chinese) — a simplified Meta Business Agent, organised into three priority tiers. The MVP is tier one.

Against the spec's 10 MVP acceptance criteria, the current state is **4 implemented / 4 stubbed / 2 not started**:

| Working today | Not yet |
| --- | --- |
| Multi-turn slot filling; flow switch / resume / cancel | RAG knowledge base — no embedding or vector store exists yet |
| Real-time data from the e-commerce service (orders, logistics, products) | FAQ retrieval |
| State persistence and session recovery | Human handoff — no `PENDING_HUMAN` state, the agent never stops replying |
| End-to-end loop for the tool-calling track | Product recommendation, card lists, quick-reply buttons |

**Known stubs** — these return successfully with placeholder content, so do not mistake them for runtime failures:

- `knowledge/provider/knowledge.py` — `RagDefaultProvider` and `FaqDefaultProvider` return fixed placeholder strings (and their messages are swapped)
- `task/action/customer/recommend_similar_products.py` — replies that recommendation is not wired up yet
- `BotMessage.object` is never assigned anywhere in the project, so the frontend's card-rendering branch is unreachable
- The frontend already renders `botMsg.suggestions`, but the backend has no such field — a contract gap to close on both sides at once

`GET /orders/{id}/logistics` returns four trace nodes, but `lookup_logistics.py` reads only three summary fields and drops `traces`.


## Quick start

### 1. E-commerce service + MySQL

Both the dialogue backend and the frontend need this running first. It ships as a Docker stack:

```bash
cd ecommerce-service-backend
docker compose -p ecommerce up -d
docker compose -p ecommerce ps       # wait for mysql to report "healthy"
```

Use `-p ecommerce` consistently. The Compose project name defaults to the directory name, and the named volume is derived from it — starting the stack under a different project name creates a **fresh, empty database** instead of reusing the existing one.

This brings up MySQL on 13306 (seeded on first run from [`docker/mysql/init/`](ecommerce-service-backend/docker/mysql/init/)) and the e-commerce API on 18081. Verify with:

```bash
curl http://127.0.0.1:18081/health
curl http://127.0.0.1:18081/users/u1001/orders
```

The init scripts run **only when the volume is first created**, so pulling new code leaves an existing database untouched. Seed changes ship as incremental scripts in [`docker/mysql/migrations/`](ecommerce-service-backend/docker/mysql/migrations/) and have to be applied by hand:

```bash
docker exec -i ecommerce-mysql mysql -uroot -proot123456 --default-character-set=utf8mb4 commerce \
  < docker/mysql/migrations/2026-08-27-unify-product-attributes.sql
```

They are idempotent and there is no version table, so when in doubt, re-run. **`2026-08-28-stock-quantity-and-order-idempotency.sql` is a schema change, not just data** — the ORM depends on the columns it adds, so run it *before* starting this version of the service; skipping it makes every product and order endpoint return 500. Rebuilding the API after a code change needs `docker compose -p ecommerce up -d --build backend` — a plain `restart` keeps serving the old image. Never use `down -v`: it destroys the `custom_service` database, which no script recreates.

The dialogue backend additionally needs a `custom_service` database on that same MySQL instance for its own `dialogue_states` table.

### 2. Dialogue backend

```bash
cd customer-service-backend

# 1. Install dependencies (via uv)
uv sync

# 2. Configure environment variables
cp .env.example .env
# Edit .env — at minimum set LLM_API_KEY and DATABASE_URL

# 3. Run the service
uv run python business_agent/api/main.py
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

### 3. Frontend

```bash
cd customer-service-frontend
npm install
npm run dev
```

Then open <http://127.0.0.1:5174>. The dev server proxies `/api` and `/health` to the backend on 18082 and `/commerce` to the e-commerce service on 18081, so no CORS setup is needed — see [`vite.config.js`](customer-service-frontend/vite.config.js). Enter a `sender_id` (for example `u1001`) in the UI to start a conversation.

## Configuration

All backend configuration is environment-driven. [`business_agent/config/settings.py`](customer-service-backend/business_agent/config/settings.py) loads it from `customer-service-backend/.env` via pydantic-settings — a missing key fails startup immediately.

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

Both come from [`ecommerce-service-backend/docker-compose.yml`](ecommerce-service-backend/docker-compose.yml) in this repository.

The e-commerce service exposes `/health`, `/orders/{id}`, `/orders/{id}/status`, `/orders/{id}/logistics`, `/orders/{id}/refund-applications`, `/orders/{id}/shipping-reminders`, `/products/{id}`, `/users/{id}/orders` and `/users/{id}/products`. Its MySQL schema covers users, products, orders, order items, logistics records and traces, refund requests and shipping reminders.

> The Compose file carries throwaway credentials for the local demo database in plain text. They are scoped to a container bound to localhost; do not reuse them anywhere real.

## API

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/` | Health check |
| `POST` | `/api/chat` | Main chat entry point; takes `sender_id` + `text` (or `object` when the user clicks a card) |
| `GET` | `/api/chat/history?sender_id=` | Full chat history across all of that user's sessions |
| `GET` | `/docs` | Swagger UI |

Request/response models for `POST /api/chat` are defined in [`business_agent/api/schemas.py`](customer-service-backend/business_agent/api/schemas.py).

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

- **API layer** (`business_agent/api/`) only converts between API models and domain models
- **Service layer** (`business_agent/services/`) owns the state load/save boundary
- **Engine layer** (`business_agent/engines/`) is pure state computation and touches no I/O

Sessions expire after 1 hour of inactivity: the old session is closed, runtime state is reset, and a new session starts (history is preserved).

## Task flows

Flows are configured as YAML under [`flow_config/`](customer-service-backend/flow_config/) and loaded by `FlowLoader`, so changing a flow needs no code change.

**Business flows** (`user_flows.yml`): `onboarding`, `order_status_query`, `logistics_tracking`, `refund_request`, `similar_product_recommendation`, `human_handoff`

**System flows** (`system_flows.yml`): task started, collect information, task resumed / resume failed, task interrupted / canceled / cancel failed, cannot handle

Step types are `start` / `action` / `collect` / `end`, chained together via `next`. A `collect` step suspends the flow to ask the user for a slot value, then resumes once it is filled — if the user changes the subject mid-flow, the original flow is interrupted and an attempt is made to resume it later.

**Commands** (`business_agent/task/commands/`): `start_flow`, `set_slots`, `resume_flow`, `cancel_flow`. These are exactly what the LLM planner emits.

**Actions** (`business_agent/task/action/`): built-in `action_response` (reply) and `action_listen` (wait for user input); business actions cover order status lookup, logistics lookup, and similar-product recommendation. Action classes under `business_agent/task/action/customer/` are **discovered and registered automatically** by `builder.py` via `pkgutil` — to add one, just drop a class extending `Action` into that directory; no manual registration needed.

## Knowledge Q&A

Seven knowledge intents are defined in [`business_agent/knowledge/intents.py`](customer-service-backend/business_agent/knowledge/intents.py), each wired to one or more providers:

| Intent | Providers |
| --- | --- |
| `product_info` | `api.product` (requires product card context) |
| `order_info` | `api.order` (requires order card context) |
| `refund_policy` / `return_policy` / `shipping_policy` | `faq.default` + `rag.default` *(both stubs)* |
| `platform_rule` | `rag.default` *(stub)* |
| `general_ecommerce_info` | `faq.default` + `rag.default` *(both stubs)* |

## Project layout

```
customer-service-backend/
├── business_agent/
│   ├── api/            FastAPI app, routes, request/response models, DI
│   ├── services/       Dialogue service — the state read/write boundary
│   ├── engines/        Dialogue engine + dependency wiring (builder.py)
│   ├── plan/           LLM turn planning and validation
│   ├── task/           Task track: flows / commands / action
│   ├── knowledge/      Knowledge track: intents + providers
│   ├── chitchat/       Chitchat track
│   ├── clarify/        Clarification prompts
│   ├── domain/         Domain models: messages, contexts, dialogue state
│   ├── repository/     SQLAlchemy persistence
│   ├── infrastructure/ DB / HTTP / LLM clients
│   ├── prompt/         Jinja2 prompt templates
│   └── config/         Settings
└── flow_config/        Flow YAML

customer-service-frontend/
├── src/App.vue         Chat UI, history, order/product sidebar
├── vite.config.js      Dev server + proxy to backend and e-commerce service
└── vue-demo/           Standalone Vue scratch demo, not part of the app

ecommerce-service-backend/
├── app/
│   ├── api.py          All REST routes
│   ├── models.py       SQLAlchemy ORM models
│   ├── schemas.py      Pydantic response models
│   └── database.py     Sync engine + session factory
├── docker/mysql/init/        Schema + seed SQL, run once on first volume init
├── docker/mysql/migrations/  Incremental SQL for databases that already exist — apply by hand
└── docker-compose.yml        MySQL 8 + the service itself
```

Prompt templates live in `business_agent/prompt/jinja2/` (`turn_plan` / `knowledge_respond` / `chitchat_respond` / `clarify_respond`) — edit these to change the assistant's wording.

## Working with Claude Code

[`CLAUDE.md`](CLAUDE.md) carries the architecture notes, commands and conventions that are not obvious from the file tree — read it before making changes.

[`.claude/agents/`](.claude/agents/) defines four subagents for parallel work, split by directory ownership so they never edit the same files:

| Agent | Scope |
| --- | --- |
| `commerce-api` | `ecommerce-service-backend/` — endpoints and schema |
| `knowledge-rag` | `knowledge/` — the RAG and FAQ chain |
| `frontend-cleanup` | `customer-service-frontend/` — deletion passes |
| `spec-auditor` | Read-only; audits code against the spec's acceptance criteria |

There is deliberately no agent for `domain/` + `api/` (the message-protocol spine) or `task/flows/` (the flow state machine). Both are easy to break by redesign and are kept in the main session.

Two conventions worth knowing before editing: **Python here uses 2-space indentation** in `customer-service-backend/` but 4-space in `ecommerce-service-backend/`, and domain models carry hand-written `to_dict()` / `from_dict()` pairs — add a field to one without the other and it vanishes silently on the next state load.


## Debugging

Use this VS Code configuration to launch the backend; breakpoints anywhere will be hit, since `main.py` does not enable `reload` and therefore runs single-process:

```jsonc
{
  "name": "FastAPI: BusinessAgent",
  "type": "debugpy",
  "request": "launch",
  "program": "${workspaceFolder}/customer-service-backend/business_agent/api/main.py",
  "console": "integratedTerminal",
  "cwd": "${workspaceFolder}/customer-service-backend",
  "env": { "PYTHONPATH": "${workspaceFolder}/customer-service-backend" }
}
```

Do not use "debug current file" on a module like `handler.py` that has no `__main__` — it will just import once and exit, and no breakpoint will be hit. If `reload=True` or `workers=` is ever added to `uvicorn.run`, uvicorn forks a subprocess and breakpoints stop working; add `"subProcess": true` in that case.

The engine runs with `echo=True`, so every SQL statement is printed to the console. Turn it off in [`business_agent/infrastructure/db_client.py`](customer-service-backend/business_agent/infrastructure/db_client.py) if it gets noisy.

A few modules can be run standalone as a sanity check (from `customer-service-backend/`):

```bash
uv run python -m business_agent.config.settings          # config loads?
uv run python -m business_agent.infrastructure.db_client # database reachable?
uv run python -m business_agent.task.action.builder      # actions registered?
```

`-m` is required here: the project is not installed as a package, so `uv run python <file path>` only puts that file's own directory on `sys.path` and running it directly fails with `ModuleNotFoundError: No module named 'business_agent'`. `business_agent/api/main.py` is the exception — it inserts the project root into `sys.path` itself at the top of the file.
