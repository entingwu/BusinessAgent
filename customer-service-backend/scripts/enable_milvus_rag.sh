#!/usr/bin/env bash
# Switch this checkout to the Milvus + BGE-M3 + rerank + LangGraph retrieval chain.
#
# Idempotent: safe to re-run. Every step asserts the state it was supposed to produce rather
# than trusting its own exit code — this repo has produced too many "reported success, changed
# nothing" failures for exit codes to be worth trusting (see CLAUDE.md, "Failure modes").
#
# Usage:  bash scripts/enable_milvus_rag.sh          # from customer-service-backend/
#         bash scripts/enable_milvus_rag.sh --revert # back to Chroma + DashScope
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE=.env
COMPOSE_DIR=../milvus

log()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[31mFAILED: %s\033[0m\n' "$*" >&2; exit 1; }

set_key() {  # set_key KEY VALUE — idempotent, appends if absent
  local key=$1 val=$2
  if grep -qE "^${key}=" "$ENV_FILE"; then
    python3 - "$ENV_FILE" "$key" "$val" <<'PY'
import re, sys
path, key, val = sys.argv[1:4]
text = open(path, encoding="utf-8").read()
open(path, "w", encoding="utf-8").write(re.sub(rf"^{re.escape(key)}=.*$", f"{key}={val}", text, flags=re.M))
PY
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
  fi
}

if [[ "${1:-}" == "--revert" ]]; then
  log "Reverting to Chroma + DashScope"
  set_key VECTOR_BACKEND chroma
  set_key EMBEDDING_BACKEND dashscope
  set_key EMBEDDING_MODEL text-embedding-v3
  # Vector-score gate for the Chroma + text-embedding-v3 chain. 0.58 was calibrated when the
  # corpus was Chinese; the corpus is English now and this value has NOT been re-derived for it.
  # Run `ingest --force` then `calibrate` after reverting and use what it prints.
  set_key KNOWLEDGE_SCORE_THRESHOLD 0.58
  set_key RERANK_ENABLED false
  set_key KNOWLEDGE_GRAPH_ENABLED false
  set_key KNOWLEDGE_DATABASE_URL ""
  echo "Done. Run: uv run python -m business_agent.knowledge.ingest ingest --force"
  exit 0
fi

[[ -f "$ENV_FILE" ]] || fail ".env not found — copy .env.example first and fill in LLM_API_KEY"

# ---------------------------------------------------------------- 1. dependencies
log "1/5  Installing dependencies (torch + pymilvus + FlagEmbedding, ~2GB on first run)"
uv sync
.venv/bin/python -c "import torch, pymilvus.model.hybrid" \
  || fail "torch / pymilvus[model] still missing after uv sync"
echo "    torch + pymilvus importable"

# ---------------------------------------------------------------- 2. Milvus
log "2/5  Starting Milvus (etcd + minio + standalone, ~330MB idle)"
docker compose -p milvus -f "$COMPOSE_DIR/docker-compose.yml" up -d
for _ in $(seq 1 60); do
  [[ "$(docker ps --filter name=milvus-standalone --format '{{.Status}}')" == *healthy* ]] && break
  sleep 5
done
[[ "$(docker ps --filter name=milvus-standalone --format '{{.Status}}')" == *healthy* ]] \
  || fail "milvus-standalone never became healthy — check: docker logs milvus-standalone"
curl -sf -m 5 http://127.0.0.1:9091/healthz >/dev/null || fail "Milvus healthz not answering on 9091"
echo "    milvus-standalone healthy, gRPC on 19530"

# ---------------------------------------------------------------- 3. isolated metadata database
log "3/5  Creating the isolated metadata database"
# The vector index is local while knowledge metadata lives in the shared MySQL. Sharing the
# metadata across two embedding models breaks "vector_chunks == metadata_chunks" for everyone
# still on Chroma — for reasons unrelated to their own environment. Hence a separate database.
DB_URL=$(grep -E '^DATABASE_URL=' "$ENV_FILE" | cut -d= -f2-)
KNOWLEDGE_DB=$(python3 - "$DB_URL" <<'PY'
import re, sys
print(re.sub(r"/([^/?]+)(\?|$)", r"/custom_service_ragmilvus\2", sys.argv[1]))
PY
)
DB_USER=$(python3 -c "import re,sys;print(re.search(r'://([^:]+):', sys.argv[1]).group(1))" "$DB_URL")
docker exec ecommerce-mysql mysql -uroot -proot123456 -e \
  "CREATE DATABASE IF NOT EXISTS custom_service_ragmilvus CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
   GRANT ALL PRIVILEGES ON custom_service_ragmilvus.* TO '${DB_USER}'@'%'; FLUSH PRIVILEGES;" \
  || fail "could not create custom_service_ragmilvus (is ecommerce-mysql running?)"
docker exec ecommerce-mysql mysql -uroot -proot123456 -N -e \
  "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='custom_service_ragmilvus';" \
  | grep -q custom_service_ragmilvus || fail "database was reported created but is not there"
echo "    custom_service_ragmilvus ready, granted to ${DB_USER}"

# ---------------------------------------------------------------- 4. configuration
log "4/5  Switching .env to the Milvus chain"
set_key VECTOR_BACKEND milvus
set_key MILVUS_URI http://127.0.0.1:19530
set_key EMBEDDING_BACKEND bge_m3
set_key EMBEDDING_MODEL BAAI/bge-m3
# mps on Apple Silicon, cpu elsewhere: measured 0.148s vs 0.308s per query on an M1 Max.
if .venv/bin/python -c "import torch,sys; sys.exit(0 if torch.backends.mps.is_available() else 1)" 2>/dev/null; then
  set_key EMBEDDING_DEVICE mps; echo "    Metal (mps) available — using it for queries"
else
  set_key EMBEDDING_DEVICE cpu; echo "    no Metal backend — using cpu"
fi
set_key RERANK_ENABLED true
# Rerank relevance gate — this is the one that actually fires while rerank is up.
# Calibrated on 35 zh + 22 en cases against the **Chinese** corpus (see RAG_ref.md). The corpus
# was englishified on 2026-08-28 and this value was not re-derived; it still separates on the
# current calibration set, but its stated provenance no longer matches the corpus.
set_key RERANK_SCORE_MIN 0.155
# Vector-score gate, used only when rerank is down. Re-derived 2026-08-28 against the English
# corpus with RERANK_ENABLED=false: answerable 26/29, unanswerable 8/8 correctly rejected.
# It is deliberately strict — on the degraded path, refusing to answer beats guessing, which is
# the same reason node_threshold falls back to the vector threshold rather than reusing the
# rerank one. Do not copy a rerank-scale number here; the scales differ by roughly 4x.
set_key KNOWLEDGE_SCORE_THRESHOLD 0.75
set_key KNOWLEDGE_GRAPH_ENABLED true
set_key KNOWLEDGE_DATABASE_URL "$KNOWLEDGE_DB"
echo "    .env updated"

# ---------------------------------------------------------------- 5. index
log "5/5  Building the index (downloads BGE-M3 ~2.3GB on first run, then ~1.7s to load)"
EMBEDDING_DEVICE=cpu uv run python -m business_agent.knowledge.ingest ingest --force
STATS=$(uv run python -m business_agent.knowledge.ingest stats 2>/dev/null)
VEC=$(echo "$STATS" | python3 -c "import json,sys;print(json.load(sys.stdin)['vector_chunks'])")
META=$(echo "$STATS" | python3 -c "import json,sys;print(json.load(sys.stdin)['metadata_chunks'])")
# The one acceptance check that matters: the index and its metadata must agree. Milvus reports
# row_count 0 until flush, and ingest skips on content_hash without checking the index — both
# produce "reported success, empty index", which is why this is asserted rather than assumed.
[[ "$VEC" == "$META" && "$VEC" != "0" ]] \
  || fail "vector_chunks=$VEC != metadata_chunks=$META — the index is not built"
echo "    vector_chunks == metadata_chunks == $VEC"

log "Done — this checkout is on Milvus + BGE-M3 + rerank + LangGraph"
echo "Verify:  uv run python -m business_agent.knowledge.ingest query --text \"退货运费谁承担\""
echo "Revert:  bash scripts/enable_milvus_rag.sh --revert"
