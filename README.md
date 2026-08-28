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

Against the spec's 10 MVP acceptance criteria, the current state is **10 implemented / 0 stubbed / 0 not started**, verified by repeated independent audits that start the service, send real requests and check the database rather than reading the code.

| | |
| --- | --- |
| Multi-turn slot filling; flow switch / resume / cancel | RAG knowledge base with per-chunk provenance |
| Real-time data from the e-commerce service (orders, logistics, products) | Retrieval miss falls back without fabricating — **guaranteed by control flow, not by prompt wording** |
| Product recommendation: preference collection → card list with live stock → quick replies | Human handoff: three control-owner states, the agent stops replying under `HUMAN` |
| State persistence and session recovery across process restarts | Order creation with an idempotency key and row-locked stock decrement |

**Two retrieval chains ship in this repo.** The default needs nothing beyond an API key; the second is the rebuild.

| | Chroma + DashScope *(default)* | Milvus + BGE-M3 |
| --- | --- | --- |
| Embedding | hosted `text-embedding-v3`, dense only | local BGE-M3, dense + sparse |
| Retrieval | cosine Top-K + threshold | hybrid search + rerank, orchestrated with LangGraph |
| Gate | vector score | rerank relevance score |
| Query latency | 0.68–0.85s | ~2.1s — the rerank round-trip is 94% of it |
| Setup | none | ~2GB of dependencies, three containers, a 2.3GB model |

Switch with one idempotent command: `bash scripts/enable_milvus_rag.sh` (`--revert` to go back). It installs dependencies, starts Milvus, creates a **separate** metadata database, rewrites `.env`, rebuilds the index, and asserts `vector_chunks == metadata_chunks` before reporting success.

**Known gaps** — these are decisions, not oversights:

- `knowledge/handler.py` queries every provider an intent names and merges the survivors; hit-and-stop priority routing is tier-two work. Tier one relies on the configuration discipline in spec 3.1.1 — no volatile data in the knowledge base — to keep sources from contradicting each other.
- `Action.is_write` / `idempotency_slots` are declared and filled but **nothing consumes them yet**; the engine has no "confirm before a write" step. That is the order-flow task.
- `PENDING_HUMAN` has no user-side exit. Only an agent calling `POST /api/handoff` `release`, or the 1-hour idle expiry, clears it.
- The rebuilt chain runs rerank **once per provider**, so a two-provider knowledge turn spends ~4s waiting on the network. Merging both candidate sets into a single rerank call would halve it.

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

The knowledge stack adds another 20 keys — embedding backend and device, vector backend, chunking, Top-K, thresholds, rerank, HyDE, graph orchestration. They are all documented inline in [`.env.example`](customer-service-backend/.env.example); copy that section wholesale rather than picking keys out of it.

**A missing key fails at import time, not at startup.** `settings = Settings()` runs at module scope, so `import business_agent.api.app` dies before the server binds a port and every `-m` command dies with it. It looks like broken code; it is a missing line in `.env`. The reverse also holds — pydantic-settings defaults to `extra='forbid'`, so a key the current code does not declare is equally fatal.

**One key deserves singling out.** `KNOWLEDGE_DATABASE_URL` is empty by default, which means "use `DATABASE_URL`". Any branch that changes the embedding model or the vector store **must** point it at its own database: the vector index is a local gitignored directory while the chunk metadata lives in the shared MySQL, so sharing the metadata across two embedding models breaks the `vector_chunks == metadata_chunks` check for everyone else — for reasons unrelated to their own environment.

**`.env` is excluded by `.gitignore` — never commit it.** When adding a new setting, update `.env.example` alongside it.

## External dependencies

| Dependency | Purpose | Default address |
| --- | --- | --- |
| MySQL | Persists dialogue state in table `dialogue_states` (`sender_id` primary key + `state_json` TEXT) | `127.0.0.1:13306` |
| E-commerce service | Order detail `/orders/{id}`, logistics `/orders/{id}/logistics`, products, per-user order lists | `127.0.0.1:18081` |
| LLM | Turn planning, knowledge answering, chitchat, clarification wording | DashScope |
| Embedding | Indexing and query encoding | DashScope by default; local BGE-M3 on the rebuilt chain |
| Vector store | Chunk vectors — **a local gitignored directory, never in MySQL**, so every checkout builds its own and a fresh clone starts empty | `knowledge_store/chroma`, or Milvus on `127.0.0.1:19530` |
| Reranker *(rebuilt chain only)* | Relevance scoring; the gate moves from vector similarity to this | DashScope `gte-rerank-v2` |

Both come from [`ecommerce-service-backend/docker-compose.yml`](ecommerce-service-backend/docker-compose.yml) in this repository.

The e-commerce service exposes `/health`, `/orders/{id}`, `/orders/{id}/status`, `/orders/{id}/logistics`, `/orders/{id}/refund-applications`, `/orders/{id}/shipping-reminders`, `/products/{id}`, `/users/{id}/orders` and `/users/{id}/products`. Its MySQL schema covers users, products, orders, order items, logistics records and traces, refund requests and shipping reminders.

> The Compose file carries throwaway credentials for the local demo database in plain text. They are scoped to a container bound to localhost; do not reuse them anywhere real.

## API

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/` | Health check |
| `POST` | `/api/chat` | Main chat entry point; takes `sender_id` + `text` (or `object` when the user clicks a card) |
| `GET` | `/api/chat/history?sender_id=` | Full chat history across all of that user's sessions |
| `GET` | `/api/session/state?sender_id=` | Current flow, step, slots and control ownership (spec 4.2) |
| `POST` | `/api/handoff` | A human agent `claim`s the session or `release`s it back to the agent |
| `GET` | `/docs` | Swagger UI |

Request/response models are defined in [`business_agent/api/schemas.py`](customer-service-backend/business_agent/api/schemas.py).

`POST /api/handoff` is the other half of the gate at the top of the architecture diagram: `claim` sets `control_owner` to `HUMAN`, after which the agent stops replying while still recording what the user says. `release` hands the session back. Tier one only flips ownership — the handoff package (history, slots, tool results) is tier-two work.

## Architecture

```mermaid
flowchart TD
    A["POST /api/chat"] --> B["DialogueStateService: load state"]
    B --> C{"control_owner is HUMAN?"}
    C -->|"yes: agent stays silent"| Z["persist DialogueState"]
    C -->|no| D{"message type"}

    D -->|"card click"| F["SetSlotsCommand (no LLM call)"]
    D -->|text| E["TurnPlanner (one LLM call)"]
    E --> G{"TurnPlanValidator"}
    G -->|invalid| H["ClarifyResponder"]
    G -->|task| I["TaskHandler"]
    G -->|knowledge| J["KnowledgeHandler"]
    G -->|chitchat| K["ChitChatHandler"]
    F --> I

    I --> I1["CommandProcessor: start / set_slots / resume / cancel"]
    I1 --> I2["FlowExecutor: advance one step"]
    I2 --> I3["ActionRunner: run one action"]

    J --> J1{"retrieval hit?"}
    J1 -->|"no: fixed text, no LLM call"| Z
    J1 -->|yes| J2["KnowledgeResponder: answer from the chunks only"]

    I3 --> Z
    J2 --> Z
    K --> Z
    H --> Z
    Z --> Y["ChatResponse"]
```

Responsibilities are split across three layers:

- **API layer** (`business_agent/api/`) only converts between API models and domain models
- **Service layer** (`business_agent/services/`) owns the state load/save boundary
- **Engine layer** (`business_agent/engines/`) is pure state computation and touches no I/O

Three properties in the diagram are load-bearing rather than incidental, and each one is a decision:

- **The handoff gate sits ahead of planning** (`engines/dialogue_engine.py`). Once a human owns the session the message is still persisted — the agent on the other side needs to see what the user said — but it never reaches the planner, so not even intent recognition runs.
- **A card click never calls the LLM.** A tapped card carries an id, and the only question is whether the step the flow is waiting on can accept it; that is a lookup, not a judgement.
- **A retrieval miss returns before the LLM chain is built.** The fallback wording is a constant in `knowledge/responder.py`, so a miss cannot fabricate — the guarantee is in the control flow, not in prompt wording.

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
| `refund_policy` / `return_policy` / `shipping_policy` | `faq.default` + `rag.default` |
| `platform_rule` | `rag.default` |
| `general_ecommerce_info` | `faq.default` + `rag.default` |

`rag.default` and `faq.default` are the same vector-backed provider filtered on `source_type` — documents versus FAQ entries. The corpus lives in [`knowledge_source/`](customer-service-backend/knowledge_source/) and is indexed by `python -m business_agent.knowledge.ingest`.

**Three paths never reach the LLM at all**: a retrieval miss, a result below the threshold, and a vector-store or embedding outage each return constant text. Fabrication is therefore impossible by control flow rather than discouraged by prompt wording — the distinction matters, because an audit can verify the former by reading one branch.

Every retrieval writes to `retrieval_traces` — one row per candidate chunk per turn, recording its similarity, whether it survived Top-K and the token budget, and why it was dropped. Read a past turn back with `python -m business_agent.knowledge.ingest traces --sender-id <id>`.

> The corpus is written in Chinese on purpose. The similarity threshold was calibrated against it, and the local BGE-M3 backend retrieves it correctly from English questions — `Who pays the return shipping fee?` matches the Chinese FAQ entry. Translating the corpus would invalidate the threshold and buy nothing.

## Project layout

```
customer-service-backend/
├── business_agent/
│   ├── api/            FastAPI app, routes, request/response models, DI
│   ├── services/       Dialogue service — the state read/write boundary
│   ├── engines/        Dialogue engine + dependency wiring (builder.py)
│   ├── plan/           LLM turn planning and validation
│   ├── task/           Task track: flows / commands / action
│   ├── knowledge/      Knowledge track: intents, providers, ingest pipeline, traces
│   ├── chitchat/       Chitchat track
│   ├── clarify/        Clarification prompts
│   ├── handoff/        Control ownership and the handoff trigger rules
│   ├── chat_history/   Renders past turns into the planner's context window
│   ├── domain/         Domain models: messages, contexts, dialogue state
│   ├── repository/     SQLAlchemy persistence
│   ├── infrastructure/ DB / HTTP / LLM / vector clients
│   ├── prompt/         Jinja2 prompt templates
│   ├── config/         Settings
│   ├── observability.py  Log configuration and the one-line argument formatter
│   └── test/           One hand-written SQLAlchemy demo script — not a test suite
├── flow_config/        Flow YAML
├── knowledge_source/   The retrieval corpus: policy documents and the FAQ
├── knowledge_eval/     Calibration set and the recorded baseline for the threshold
├── knowledge_store/    Local vector index — gitignored, so each checkout builds its own
└── scripts/            enable_milvus_rag.sh, the one-command retrieval-chain switch

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
| `frontend` | `customer-service-frontend/` — protocol rendering, interaction, visuals |
| `spec-auditor` | Read-only; audits code against the spec's acceptance criteria |

There is deliberately no agent for `domain/` + `api/` (the message-protocol spine) or `task/flows/` (the flow state machine). Both are easy to break by redesign and are kept in the main session.

**Run an audit against a pinned commit, in its own worktree.** `git worktree add --detach <commit>` and tell the auditor to read only that path. Not because an audit writes anything — it does not — but because it needs its subject to hold still: a `git pull` in the shared workspace halfway through one produced a report that looked entirely normal and was measured against two different versions of the code. Development that gets interrupted is redone; an audit that gets interrupted yields a wrong report with no symptom.

**Two things bite when several sessions run at once**, and both have already happened here:

- **`.env` is shared on disk while branches are not.** A branch behind `main` starts failing against it the moment another branch adds a setting — `extra='forbid'` turns the new key into an import-time crash that reads like broken code. The fix is to rebase, not to edit `.env`.
- **Kill by port, never by pattern.** `pkill -f business_agent/api/main.py` matches every session's backend; `kill $(lsof -ti:<port>)` matches one. For the same reason, give a worktree its own `APP_PORT` — copying `.env` from the main checkout hands it a port a colleague is already holding.

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
