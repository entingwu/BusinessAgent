# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A monorepo holding three services that together form an LLM-driven multi-turn customer service agent:

| Directory | Role | Port |
| --- | --- | --- |
| `customer-service-backend/` | Dialogue backend — the actual agent. Package: `business_agent` | 18082 |
| `customer-service-frontend/` | Vue 3 + Vite chat UI | 5174 |
| `ecommerce-service-backend/` | Mock business mid-platform (orders, logistics, products). **Independent service, not part of the agent** — reached only over HTTP via `COMMERCE_API_BASE_URL` | 18081 |

`meta-business-agent.md` at the repo root is the **product spec / roadmap** the project is being built toward (Meta Business Agent 简易版). It is written in Chinese, uses a three-tier priority system (第一档 / 第二档 / 第三档), and its 附录 B 移植清单 maps spec items onto files in this repo. Read it before planning any feature work. **Decision made: evolve this repo in place** — do not create the separate `meta-business-agent/` repo the spec's B.3 describes.

## Commands

Everything below assumes you are in the directory named in the heading.

### Start the stack

The e-commerce service must be up first — the dialogue backend calls it and the frontend proxies to it.

```bash
# ecommerce-service-backend/
docker compose -p ecommerce up -d
docker compose -p ecommerce ps        # wait for mysql to report "healthy"
```

Always pass `-p ecommerce`. The Compose project name defaults to the directory name and the named volume derives from it — a different project name silently creates a **fresh empty database** instead of reusing the seeded one.

```bash
# customer-service-backend/
uv sync
uv run python business_agent/api/main.py     # http://127.0.0.1:18082/docs

# customer-service-frontend/
npm install && npm run dev                    # http://127.0.0.1:5174
```

### Running individual modules — the `-m` rule

The project is **not installed as a package**. `uv run python <file path>` only puts that file's own directory on `sys.path`, so running a module directly fails with `ModuleNotFoundError: No module named 'business_agent'`. Use `-m` from `customer-service-backend/`:

```bash
uv run python -m business_agent.config.settings          # config loads?
uv run python -m business_agent.infrastructure.db_client # database reachable?
uv run python -m business_agent.task.action.builder      # actions registered?
```

`business_agent/api/main.py` is the one exception — it inserts the project root into `sys.path` itself.

### Verifying a change

There is **no test framework in this repo** — no pytest, no test suite. `business_agent/test/test_expire_on_commit.py` is a hand-written SQLAlchemy demo script, not a test. Verify changes by:

```bash
# customer-service-backend/
.venv/bin/python -m compileall -q business_agent          # compiles?
PYTHONPATH=. .venv/bin/python -c "import business_agent.api.app"   # imports?

# end-to-end (calls the real LLM and writes a dialogue_states row)
curl -X POST http://127.0.0.1:18082/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"sender_id":"u1001","text":"我要查订单"}'
```

Use a throwaway `sender_id` for smoke tests and delete the row afterward — state is keyed by `sender_id` and persists across runs.

## Architecture

### The turn pipeline

Understanding one user message requires reading across `api/` → `services/` → `engines/` → the three handler trees. The chain:

1. **`api/chat_router.py`** converts the API model (`ChatRequest`) to the domain model (`UserMessage`). The API layer does nothing else — no business logic.
2. **`services/dialogue_service.py`** owns the **only I/O boundary**: it loads `DialogueState` from the DB, hands it to the engine, saves it back. The engine itself performs no persistence.
3. **`engines/dialogue_engine.py`** `handle_message()` prepares/expires the session (1-hour idle window, hardcoded), opens a turn, then **splits on message type**:
   - **Text** → `TurnPlanner` (LLM call) produces a `TurnPlan` → `TurnPlanValidator` checks it against the loaded flows and knowledge intents → if invalid, `ClarifyResponder`; if valid, routes down **exactly one of three tracks**: task / knowledge / chitchat.
   - **Object** (a card the user clicked) → **no LLM call**. It tries to build a `SetSlotsCommand` from the card, but only if the active flow's current step is a `CollectionFlowStep` waiting for exactly that slot. Otherwise it either re-runs the current step or asks for clarification.
4. The turn is committed into the current session and the whole `DialogueState` is serialized back to the DB.

### The three tracks

- **Task** (`task/`) — the YAML-configured state machine, and the most intricate part of the codebase. `TaskHandler` runs commands through `CommandProcessor` (mutating state: start / set_slots / resume / cancel), then `FlowExecutor` advances the flow and invokes actions via `ActionRunner`. Supports a task stack: interrupting a flow pushes it to `paused_tasks`, and it can be resumed or cancelled later. **Do not redesign this when touching it** — the spec's 附录 B.6 calls this out explicitly: reworking the flow switch/resume/cancel semantics turns a 3–4 day job into 10+.
- **Knowledge** (`knowledge/`) — a knowledge intent (`knowledge/intents.py`) maps to one or more provider IDs; `KnowledgeHandler` retrieves from each and `KnowledgeResponder` feeds the chunks to the LLM.
- **Chitchat** (`chitchat/`) — LLM free-form reply.

### Flows are configuration, not code

`flow_config/system_flows.yml` (fallback/system branches) and `flow_config/user_flows.yml` (business flows) are loaded by `FlowLoader` at engine construction time in `engines/builder.py`. Adding a business flow means editing YAML, not Python.

`engines/builder.py` is the single wiring point — every handler, provider and action runner is constructed there. To add a dependency to the engine, that is the file to edit.

### Actions auto-register

`task/action/builder.py` discovers action classes under `task/action/customer/` via `pkgutil`. To add one, drop a class extending `Action` into that directory — no manual registration. Built-ins (`action_response`, `action_listen`) live in `task/action/builtin/`.

`action_response` supports three modes set from YAML: `static` (render the Jinja2 text as-is), `rephrase` (render, then have the LLM rewrite it), `generate` (LLM writes from scratch). Prompt templates live in `prompt/jinja2/`.

### Persistence shape

The **entire** `DialogueState` — sessions, turns, user and bot messages, task stack, slots — is serialized to a single `state_json` TEXT column in `dialogue_states`, keyed by `sender_id` (`repository/dialogue_record.py`). There is no message table. The spec's 第一档 calls for splitting messages out and adding a `control_owner` field for human handoff; expect this shape to change.

Two databases live on the one MySQL container: `commerce` (e-commerce service, seeded from `docker/mysql/init/`) and `custom_service` (dialogue backend's `dialogue_states`).

## Known stubs and gaps

These return successfully with placeholder content — they are not bugs to fix incidentally, they are scheduled 第一档 work. Do not treat their output as a runtime failure:

- `knowledge/provider/knowledge.py` — `RagDefaultProvider` and `FaqDefaultProvider` both return fixed placeholder strings. **Their messages are swapped**: `RagDefaultProvider` says "暂未对接FAQ" and `FaqDefaultProvider` says "暂未对接RAG". There is no vector store or embedding dependency in the project at all.
- `task/action/customer/recommend_similar_products.py` — 28-line stub that replies "还没有接入正式的推荐系统".
- `KnowledgeChunk` (`knowledge/provider/provider.py`) carries only `content` — no source ID, title, position or similarity, so knowledge answers cannot be traced to a source.
- `KnowledgeResponder` concatenates every provider's chunks indiscriminately into the prompt. The spec requires hit-and-stop priority routing instead.
- **Contract gap**: the frontend already renders `botMsg.suggestions`, but the backend's `BotMessage` / `ChatBotMessage` have no such field, and no `cards[]` list either — only a single `object`. Fixing this means touching `domain/messages.py`, `api/schemas.py` and `chat_router.py`'s `_build_chat_response` together.
- The e-commerce service has **no product search endpoint** (only fetch-by-ID and a "recently viewed" list), no create-order endpoint, and `products.stock_status` is a `VARCHAR` string rather than a quantity.

Leftover from earlier work, safe to remove when touching those files: the `/test` endpoint and inline `User` model in `api/chat_router.py`; the frontend's digital-human integration (`lm-avatar-chat-sdk`, `/api/avatar/session`, `/ws/avatar/chat`) which calls backend endpoints **that were never implemented**; and the frontend `package.json` name still reading `atguigu-frontend`.

## Code conventions

- **Python uses 2-space indentation**, not 4. Match it.
- Docstrings follow a `Goal:` / `Args:` / `Returns:` convention.
- Comments and docstrings are mixed Chinese and English; keep writing in whichever the surrounding block uses.
- Domain models are `@dataclass(slots=True)` with hand-written `to_dict()` / `from_dict()` pairs (not `asdict`) — when adding a field to a domain model, update **both** methods or it silently vanishes on the next state load.
- API models are Pydantic; domain models are dataclasses. The two are converted explicitly in `api/chat_router.py` — never leak a Pydantic model past the router.
- Configuration is environment-driven through `config/settings.py`; a missing key fails startup immediately by design. Add new settings there, and to `.env.example`.
