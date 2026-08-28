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

**`docker/mysql/init/` only runs once, on first volume init.** Pulling new code does not update an existing database. If the volume already exists, apply the incremental scripts in `docker/mysql/migrations/` by hand — they are the only way seed changes reach a running database:

```bash
# ecommerce-service-backend/
docker exec -i ecommerce-mysql mysql -uroot -proot123456 --default-character-set=utf8mb4 commerce \
  < docker/mysql/migrations/2026-08-27-unify-product-attributes.sql
```

There is **no version table** — nothing records which migrations have run. Every script is written to be idempotent (`UPDATE` for existing rows, `INSERT ... ON DUPLICATE KEY UPDATE` for new ones), so re-running one is safe on its own; that is the only defence. Check the data itself to tell whether a script has been applied.

**"Idempotent on its own" is not the same as "safe in any order", and one pair here proves it.** `2026-08-27-unify-product-attributes.sql` rewrites `products.title`, `description` and `attributes_json` back to their original Chinese values unconditionally. Re-running it — which the paragraph above actively encourages — silently reverts all three of `2026-08-28-englishify-display-fields.sql`, `2026-08-28-englishify-attribute-values.sql` and the two cover-image scripts. Nothing errors and nothing is logged; the catalogue simply turns back into Chinese with the old placeholder photos.

The repair is to re-run the three 2026-08-28 scripts, in that order, and they are written so that always works: every one of them keys on `product_id` alone rather than on the value being replaced. **If you re-run the 2026-08-27 script, re-run those three after it.**

The same applies to a **fresh volume**, and this is the case most likely to catch someone out. `docker/mysql/init/02-seed.sql` writes the original Chinese values, so a newly initialised database starts out disagreeing with the code, which now sends `office` / `minimalist` / `standard` as attribute filters. Nothing errors — product search simply returns nothing, for every query, and that is indistinguishable from an empty catalogue. Apply `2026-08-27-unify-product-attributes.sql` and then the three 2026-08-28 scripts before deciding anything is broken.

One consequence worth knowing separately: `02-seed.sql` states that the two size scales do not interoperate — that `size:大号` cannot match apparel and `size:M` cannot match hardware. That was true while the scales were Han characters and Latin letters. After englishification the commerce filter's `LOWER(...) LIKE '%value%'` makes `small` contain "m", `standard` contain "s" and `large` contain "l", so the scales now bleed into each other. No flow collects `product_size` today, so nothing reaches it — but that comment is now wrong, and whoever wires the slot up will read it.

**Not all migrations are equal — check whether one is a schema change before deploying code that needs it.**

| Script | Kind | Skipping it means |
| --- | --- | --- |
| `2026-08-27-unify-product-attributes.sql` | data only | attribute filtering matches nothing; endpoints still answer |
| `2026-08-28-stock-quantity-and-order-idempotency.sql` | **schema** | **every product and order endpoint returns 500** — the ORM selects `products.stock_quantity`, `orders.idempotency_key`, `delivery_method` and `request_fingerprint`, and MySQL raises `Unknown column` |

**`init/` and `migrations/` differ on purpose about `USE`.** The two `init/` files carry **no** `USE` statement — the entrypoint already runs them with `--database="$MYSQL_DATABASE"`, so the caller's database wins and the same files can be loaded into a throwaway schema for testing without touching the shared one. The `migrations/` scripts **keep** `USE commerce;` and hardcode `TABLE_SCHEMA = 'commerce'` in their guards, because they target that one database by definition. Do not "make them consistent" — the asymmetry is the safety property. It was learned the hard way: `01-schema.sql` opens with eight `DROP TABLE`, and one load that let the file's own `USE commerce;` override the target database emptied the shared database (tables intact, every row gone).

For a schema migration the order is **migrate first, then deploy the code** — the reverse leaves the service broken until you notice. There is no automation for this: `docker-compose.yml` mounts only `init/`, and `init/` runs only on first volume creation.

Two traps around this:

- **Never `docker compose down -v`.** The init scripts only `USE commerce` — they have never created `custom_service`, which holds `dialogue_states` plus the RAG tables. Wiping the volume rebuilds `commerce` and destroys `custom_service` with nothing to recreate it.
- **`docker compose restart backend` loads the old code.** `Dockerfile.local` is `COPY . .` with no bind mount, so code changes need `docker compose -p ecommerce up -d --build backend`. This fails quietly in a nasty way: FastAPI silently ignores query parameters it does not declare, so an old image answers `GET /products?attr=use_case:office` with `200` and **every** product rather than an error — the filter looks like it worked.

```bash
# customer-service-backend/
uv sync
uv run python business_agent/api/main.py     # http://127.0.0.1:18082/docs

# customer-service-frontend/
npm install && npm run dev                    # http://127.0.0.1:5174
```

### After pulling — four things a fresh checkout needs

`uv sync` is no longer enough now that the knowledge stack is in. Each of these fails in a way that looks like a different problem:

1. **Copy the 13 RAG keys from `.env.example` into your `.env`.** `config/settings.py` declares them with no defaults and `settings = Settings()` runs at module scope, so a missing key raises `ValidationError` at **import** time — `import business_agent.api.app` dies before the server ever binds a port, and every `-m` command dies with it. (`Settings(_env_file=None)` reports all 20 required keys: the 7 original plus these 13.) Embedding reuses `LLM_API_KEY` / `LLM_BASE_URL` — there is no second credential to obtain.

2. **Budget real time for `uv sync`.** The RAG work added `chromadb`, `langchain-text-splitters` and the `[asyncio]` extra on `sqlalchemy`. The lock went from 63 packages to 115: Chroma drags in `onnxruntime`, `grpcio`, `tokenizers`, `numpy` and the whole `opentelemetry-*` set. That is a few hundred MB on first sync, not a few seconds.

3. **Ingest with `--force`, and check the count.** Plain `ingest` will silently do nothing on a fresh checkout. The chunk metadata lives in **MySQL**, which everyone shares through the one container, but the Chroma index is a **local gitignored directory** that a fresh checkout does not have. `ingest` decides what to skip by comparing `content_hash` against the MySQL rows (`knowledge/ingest/pipeline.py:86-95`) and never checks whether the local vector store actually holds those vectors — so it reports every source `skipped`, exits successfully, and leaves you with an empty index. Every knowledge question then hits the miss fallback, which reads exactly like "the RAG work was never done".

   ```bash
   uv run python -m business_agent.knowledge.ingest ingest --force
   uv run python -m business_agent.knowledge.ingest stats
   ```

   One acceptance check: **`vector_chunks` must equal `metadata_chunks`** (45 / 45 today). This check exists because the metadata lives in the shared MySQL container while the index is local — it has already caught one case where another branch's ingest rewrote the shared table with a different embedding model, leaving everyone else's index silently mismatched. If they differ, the index is not built, whatever the ingest output said. `--force` re-embeds every chunk through DashScope, so it needs network and a valid `LLM_API_KEY`.

4. **Query them with `--default-character-set=utf8mb4`.** Without it the MySQL client renders `source_title` and the Chinese corpus as `?????`. The data is fine — the columns are `utf8mb4` — but the symptom looks exactly like a broken ingest, and it is easy to spend a round debugging the embedding pipeline over a client setting:

   ```bash
   docker exec -i ecommerce-mysql mysql -uroot -proot123456 --default-character-set=utf8mb4 custom_service \
     -e "SELECT chunk_id, source_title, score, selected, drop_reason FROM retrieval_traces ORDER BY id DESC LIMIT 10"
   ```

5. **The three knowledge tables create themselves — do not write a migration.** `knowledge_sources`, `knowledge_chunks` and `retrieval_traces` are created by `ensure_tables()` (`repository/knowledge_repository.py:146-158`), which the ingest CLI runs and the server runs once per process on its first retrieval. It is a whitelisted `create_all` and never touches `dialogue_states`. Two consequences: `create_all` only CREATEs — **adding a column to one of those models will not alter an existing table**, you have to `ALTER` by hand; and these tables are exactly what `docker compose down -v` destroys with nothing in the repo able to recreate them.

### Switching the retrieval chain

Two retrieval chains ship in this repo. `.env.example` defaults to the first one because it needs
nothing beyond an API key; the second is what the RAG rebuild delivered.

| | Chroma + DashScope | Milvus + BGE-M3 |
|---|---|---|
| Embedding | hosted `text-embedding-v3`, dense only | local BGE-M3, dense + sparse |
| Retrieval | cosine Top-K + threshold | hybrid search + rerank + LangGraph |
| Gate | vector score, 0.58 | rerank relevance score, 0.155 |
| Query latency | 0.68–0.85s | ~2.1s (rerank round-trip is 94% of it) |
| Extra setup | none | ~2GB of deps, three containers, a 2.3GB model |

Switching is one command, and it is idempotent:

```bash
# customer-service-backend/
bash scripts/enable_milvus_rag.sh            # switch to Milvus + BGE-M3 + rerank + LangGraph
bash scripts/enable_milvus_rag.sh --revert   # back to Chroma + DashScope
```

It installs the dependencies, starts Milvus, **creates a separate metadata database**, rewrites
`.env`, rebuilds the index, and asserts `vector_chunks == metadata_chunks` at the end. Every step
checks the state it was supposed to produce rather than trusting its own exit code.

**The separate metadata database is not optional.** The vector index is a local gitignored
directory while `knowledge_sources` / `knowledge_chunks` live in the shared MySQL. Point two
different embedding models at the same metadata and the `vector_chunks == metadata_chunks` check
fails for everyone still on Chroma — for reasons unrelated to their own environment. The script
derives `KNOWLEDGE_DATABASE_URL` from `DATABASE_URL` and creates `custom_service_ragmilvus`.

**There is deliberately no automatic fallback.** If `VECTOR_BACKEND=milvus` and Milvus is
unreachable, retrieval fails loudly into the "can't check right now, let me get you a human"
path — it does not quietly drop back to Chroma. Silent fallback would let two people run the same
commit and get different retrieval quality with no signal, which is the failure shape this repo
keeps producing (see below).

### Running individual modules — the `-m` rule

The project is **not installed as a package**. `uv run python <file path>` only puts that file's own directory on `sys.path`, so running a module directly fails with `ModuleNotFoundError: No module named 'business_agent'`. Use `-m` from `customer-service-backend/`:

```bash
uv run python -m business_agent.config.settings          # config loads?
uv run python -m business_agent.infrastructure.db_client # database reachable?
uv run python -m business_agent.task.action.builder      # actions registered?
```

The knowledge base has its own CLI. It is **not** populated by starting the server — a fresh checkout retrieves nothing until you ingest:

```bash
uv run python -m business_agent.knowledge.ingest ingest --force   # load → split → embed → index
uv run python -m business_agent.knowledge.ingest stats     # vector_chunks must equal metadata_chunks
uv run python -m business_agent.knowledge.ingest query --text "退货运费谁承担"   # retrieval only, no LLM
uv run python -m business_agent.knowledge.ingest calibrate  # re-derive the similarity threshold
uv run python -m business_agent.knowledge.ingest traces --sender-id <id>  # what a past turn retrieved
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
- **Knowledge** (`knowledge/`) — a knowledge intent (`knowledge/intents.py`) maps to one or more provider IDs; `KnowledgeHandler` retrieves from each and `KnowledgeResponder` feeds the chunks to the LLM. Two kinds of provider live behind the same `Provider.retrival(state) -> list[KnowledgeChunk]` signature (note the spelling — it is `retrival`, keep it): `ApiOrderProvider` / `ApiProductProvider` call the commerce service for volatile data, while `rag.default` / `faq.default` are vector-backed (`knowledge/provider/rag.py`) and filter on `source_type` metadata.

  The RAG path: `knowledge/ingest/` loads Markdown / TXT / CSV from `knowledge_source/`, splits (recursive separators for prose, one-chunk-per-entry for FAQ and CSV), embeds via DashScope `text-embedding-v3` and writes both the vectors (Chroma, through the four methods on `infrastructure/vector_client.py`) and the metadata (`repository/knowledge_repository.py`). At query time `KnowledgeRetriever` applies Top-K plus a cosine threshold and **returns an empty list on a miss** — the fallback wording is the responder's decision, not the provider's. `KnowledgeResponder` then sorts by score, trims to the token budget, and labels each chunk with its source in the prompt. Three paths never reach the LLM at all: a miss, a low-similarity result, and a vector-store/embedding outage all return constant text, so they cannot fabricate (spec 5.2, 验收 3). Every retrieval — hit, miss or outage — is written to `retrieval_traces` keyed by turn, and logged under the `business_agent.knowledge` logger.

  **The similarity threshold is calibrated, not arbitrary.** `KNOWLEDGE_SCORE_THRESHOLD` is 0.58, not the 0.35 in the spec's table: `text-embedding-v3` scores run high enough that 0.35 filters almost nothing. Re-calibrate with `-m business_agent.knowledge.ingest calibrate` against `knowledge_eval/calibration_set.jsonl` after changing the embedding model **or the chunking** — `knowledge/ingest/splitter.py` documents how the FAQ prefix once collapsed the separable range.
- **Chitchat** (`chitchat/`) — LLM free-form reply.

### Flows are configuration, not code

`flow_config/system_flows.yml` (fallback/system branches) and `flow_config/user_flows.yml` (business flows) are loaded by `FlowLoader` at engine construction time in `engines/builder.py`. Adding a business flow means editing YAML, not Python.

`engines/builder.py` is the single wiring point — every handler, provider and action runner is constructed there. To add a dependency to the engine, that is the file to edit.

### Actions auto-register

`task/action/builder.py` discovers action classes under `task/action/customer/` via `pkgutil`. To add one, drop a class extending `Action` into that directory — no manual registration. Built-ins (`action_response`, `action_listen`) live in `task/action/builtin/`.

`action_response` supports three modes set from YAML: `static` (render the Jinja2 text as-is), `rephrase` (render, then have the LLM rewrite it), `generate` (LLM writes from scratch). Prompt templates live in `prompt/jinja2/`.

### Persistence shape

The **entire** `DialogueState` — sessions, turns, user and bot messages, task stack, slots — is serialized to a single `state_json` TEXT column in `dialogue_states`, keyed by `sender_id` (`repository/dialogue_record.py`). There is no message table. The spec's 第一档 calls for splitting messages out and adding a `control_owner` field for human handoff; expect this shape to change.

Two databases live on the one MySQL container: `commerce` (e-commerce service, seeded from `docker/mysql/init/`, updated afterwards only through `docker/mysql/migrations/`) and `custom_service` (dialogue backend) — the latter is **not** created by any script in the repo.

`custom_service` holds four tables. `dialogue_states` is the one above. The other three belong to the knowledge side and are created on demand — the ingest CLI creates them, and the server creates them once per process on its first retrieval, via an explicit table whitelist that never touches `dialogue_states`: `knowledge_sources` and `knowledge_chunks` (ingest metadata, including the embedding model name so a model swap is visible) and `retrieval_traces` (one row per retrieved chunk per turn — chunk ID, similarity, whether it survived Top-K and the token budget, and why it was dropped). Note that `create_all` only CREATEs; adding a column to one of these models will **not** alter an existing table.

The Chroma index itself is **not** in MySQL — it is a local directory (`VECTOR_STORE_DIR`, gitignored), so each checkout builds its own and a fresh clone starts empty.

## Known stubs and gaps

These return successfully with placeholder content — they are not bugs to fix incidentally, they are scheduled 第一档 work. Do not treat their output as a runtime failure:

- `task/action/customer/recommend_similar_products.py` — 28-line stub that replies "还没有接入正式的推荐系统".
- **Knowledge-source priority routing (hit-and-stop) is not implemented** — it is 第二档 work (spec 3.1.2 附注, C.4.5). `KnowledgeResponder` no longer concatenates indiscriminately (it sorts by score, cuts to Top-K and to a token budget), but every provider named by the intent is still queried and their surviving chunks are merged. 第一档 relies on the 3.1.1 配置纪律 — no volatile data in the knowledge base — to keep sources from contradicting each other.
- **The protocol is wired but nobody fills it.** `cards[]`, `suggestions[]` and `control_owner` now exist end to end (`domain/messages.py` → `api/schemas.py` → `chat_router.py` → the Vue frontend renders all three, per 附录 E). But **no producer sets them**: no action or responder ever constructs a `BotMessage` with `cards` or `suggestions`, and `ProcessedResult.control_owner` is hardcoded to `"AGENT"` — there is no handoff logic to move it to `PENDING_HUMAN` / `HUMAN`. The frontend's multi-card list and the three control-ownership states are therefore unreachable at runtime. Filling them is 第一档 work (3.3.3 商品推荐 and 3.3.4 人工接管), not a plumbing bug.
- The e-commerce service **now has** `POST /orders` (idempotent, decrements stock under a row lock) and `products.stock_quantity`. `stock_status` is still a `VARCHAR`, but it is now a **derived display value** — the single place that derives it is `_stock_label()` in `app/api.py`, and every "is it in stock" judgement reads the quantity. Both columns are kept in sync only by code that goes through `create_order`; anything that writes `stock_quantity` by hand must update the label too. Applying `docker/mysql/migrations/2026-08-28-stock-quantity-and-order-idempotency.sql` is **mandatory before running this code** (see the migrations table above).
- **The UI is English, the Agent is Chinese.** `customer-service-frontend` was localized to English, but every prompt template in `prompt/jinja2/`, the YAML flows and the knowledge content are Chinese, so `qwen-plus` replies in Chinese inside an English shell. The four welcome quick-replies now send English text (`Request a refund`, …) into a Chinese intent set — intent matching is done by the LLM rather than string comparison, so it should hold, but it is unverified.
- One display-layer coupling worth knowing: order status values arrive from the commerce service **in Chinese** (`待发货`, `运输中`, …) and are used as lookup keys in `ORDER_STATUS_CLASS` / `ORDER_STATUS_LABEL` in `App.vue`. Translate the values, never the keys.

- **`no_relevant_answer` is dead configuration.** `flow_config/system_flows.yml:158,196` defines a fallback branch keyed on `context['reason'] == 'no_relevant_answer'`, but nothing in Python ever writes that reason, and `ClarifyReason` (`plan/turn_plan.py:62`) has no such member. It has never run. **The retrieval-miss fallback does not go through it** — it lives in `KnowledgeResponder` as two constants and returns without calling the LLM (see the knowledge track above). Either delete the branch or wire it deliberately; do not assume it is the fallback path.

Leftover from earlier work, safe to remove when touching that file: the `/test` endpoint and inline `User` model in `api/chat_router.py`. (The digital-human integration and the `atguigu-frontend` package name are **gone** — removed in `fb4d7d4`.)

## Failure modes this repo keeps producing

Every one of these cost real debugging time here. They share a shape: **the failing path and the
succeeding path return the same thing**, so nothing tells you anything went wrong.

- `open -a "Docker Desktop"` — wrong app name (it is `Docker.app`). Reports success, launches
  nothing. 400 seconds spent waiting for a daemon that was never starting.
- Milvus `get_collection_stats` returns `row_count: 0` until you `flush()`. Ingest reports 45
  chunks written, stats shows 0.
- BSD `sed` (macOS) does not support `\b`. `s/\bcommerce\b/.../` silently matches nothing and
  exits 0 — which is how `DROP DATABASE` once landed on the shared `commerce` database.
- `ingest` skips a source when `content_hash` is unchanged, without checking whether the local
  vector store actually holds those vectors. On a fresh checkout it reports every source
  `skipped`, exits 0, and leaves an empty index.
- FastAPI silently ignores query parameters it does not declare, so an old container answers
  `GET /products?attr=...` with 200 and every row — the filter looks like it worked.

**Detection rule: never trust the return value of an operation; assert the state it was supposed
to produce.** `ps` that the process exists, `count(*)` rather than a cached statistic, `grep -c`
the substitution actually happened.

**Two-sided facts warn on one side only.** `SYSTEM_CONTEXT_TO_CLASS` missing a class raises
`KeyError` (loud, safe); a YAML flow nobody starts is silent (dangerous). The commerce attribute
allow-list 400s on an unknown name but returns 200 + empty on a renamed key. Whenever two
independent places must agree, ask **which direction warns** — the other one is where the bugs live.

> Why every surviving bug is on the silent side: the loud half gets fixed within minutes of being
> written, so it never reaches anyone's field of view. It is survivorship bias in code. An audit
> that only finds errors that raise has not looked deep enough.

**Half-migrations.** Changing A without the B that was calibrated against it — and B does not
complain. Real cases here: the vector index was isolated per checkout but its metadata stayed in
shared MySQL; `score` switched to rerank values while the adjacent `threshold` column kept the
vector threshold; a rerank outage fell back to vector scores but kept the rerank threshold.

**Prefer impossible over remembered.** Deleting the redundant `USE commerce;` beats asserting on
it; storing paths relative to the source root beats documenting "run `--force` after merging";
`git worktree add --detach <commit>` for an audit beats asking people not to touch main while it
runs. A good migration check is self-limiting — "the stored path is absolute" stops matching once
the migration is done, so it needs no version column and no cleanup.

## Code conventions

- **Python uses 2-space indentation**, not 4. Match it.
- Docstrings follow a `Goal:` / `Args:` / `Returns:` convention.
- **Comments and docstrings are English.** They used to be mixed Chinese and English with the rule "match the surrounding block"; that rule is gone, and writing new Chinese comments re-opens the mix. The migration is tracked as tier A of the englishification work — until it finishes you will still meet Chinese comments in untouched files, so translate the ones you edit rather than matching them.
- **Two columns are load-bearing Chinese and must not be translated.** They are matching keys, not display text:
  - `products.stock_status` (`有货` / `缺货`) — the stock decrement writes `_IN_STOCK_LABEL = "有货"` back on every order, and `2026-08-28-stock-quantity-and-order-idempotency.sql` carries an unconditional `UPDATE ... SET stock_status = IF(...)`.
  - `orders.status` (`待发货`, `运输中`, …) — `app/api.py` gates the shipping-reminder endpoint on `order.status not in {"待发货", "待揽收"}`. Translate the column and that endpoint returns 400 for **every** order, without raising anything.
  Both are englishified **at display time instead**: `ORDER_STATUS_LABEL` in `App.vue` and `STOCK_STATUS_LABELS` in `recommend_products.py`. The stored value never moves, so everything that matches on it keeps working, including any matching site nobody grepped for.

- **Product attribute values are English and are matching keys** (`office`, `minimalist`, `standard`). Four places produce that same set and must change together, in one commit — `2026-08-28-englishify-attribute-values.sql` (what the catalogue stores), `STYLE_VALUES` in `recommend_products.py`, the slot descriptions in `user_flows.yml`, and that file's collect-step quick replies. Drift in any one of them makes a tap return an empty result set, which is indistinguishable from "there really is no matching product". **If searches start coming back empty, check those four first**; `recommend_products` logs `empty_result attrs=…` to make that attributable.

  These were Chinese until the D3 migration, kept that way deliberately so that drift showed up as visible Chinese text on an English button rather than as a silent empty result. Translating them traded a visible failure mode for a silent one — knowingly, and the four-way lockstep plus that log line is what pays for it.

- **Quick replies are `{label, value}`, not strings** (`Suggestion` in `domain/messages.py`). `label` is displayed; `value` is what gets sent when the button is tapped, and a button's text *is* the message the planner receives. The two differ whenever the tap must carry something the user should not have to read — a button saying "Track this order" that sends the order id along with it. A bare string still works everywhere and means `label == value` — `Suggestion.coerce` handles YAML config, action arguments, and sessions persisted before the type existed. Do not bypass it.

- **Catalogue display columns are English** (`2026-08-28-englishify-display-fields.sql`): product titles and descriptions, `attributes_json.spec` / `.brand`, order and shipping status descriptions, tracking events, carrier names, receiver names and addresses. **That migration must run after `2026-08-27-unify-product-attributes.sql`**, which writes Chinese values back unconditionally; re-running the englishify script is the repair, and its product UPDATEs match on `product_id` alone so the repair always works.
- Domain models are `@dataclass(slots=True)` with hand-written `to_dict()` / `from_dict()` pairs (not `asdict`) — when adding a field to a domain model, update **both** methods or it silently vanishes on the next state load.
- API models are Pydantic; domain models are dataclasses. The two are converted explicitly in `api/chat_router.py` — never leak a Pydantic model past the router.
- Configuration is environment-driven through `config/settings.py`; a missing key fails startup immediately by design. Add new settings there, and to `.env.example`.
