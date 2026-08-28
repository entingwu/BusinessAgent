# Knowledge sources

Merchant knowledge documents that get indexed into the vector store. Read by the
`business_agent/knowledge/ingest` pipeline.

| Directory | Source type | Splitting |
|---|---|---|
| `faq/` | `faq` | One chunk per entry — no semantic splitting |
| `policy/` | `document` | `RecursiveCharacterTextSplitter`, recursive on `\n## / \n### / \n\n / 。` |

## The content is English, and changing it invalidates the thresholds

The files under `faq/` and `policy/` are the retrieval corpus, not documentation.
They were Chinese until 2026-08-28 and are now English, matching the UI and the
agent's replies.

**Editing this corpus — translating it, restructuring it, or even re-titling a
section — shifts the whole score distribution and voids the calibrated
thresholds.** After any change here:

```bash
uv run python -m business_agent.knowledge.ingest ingest --force
uv run python -m business_agent.knowledge.ingest calibrate
```

and set the value `calibrate` prints. Nothing errors if you skip this; retrieval
simply mis-gates, which looks like an empty knowledge base or like the assistant
answering things it should not.

Two thresholds exist and they are on different scales — do not copy one into the
other:

| Key | Scale | When it gates |
|---|---|---|
| `RERANK_SCORE_MIN` | rerank relevance, ~0.0–0.9 | normal operation, when rerank is up |
| `KNOWLEDGE_SCORE_THRESHOLD` | vector cosine, ~0.5–0.8 | only on the degraded path, when rerank is down |

`calibrate` reports against whichever scoring is currently active, so read
`RERANK_ENABLED` before believing its number applies to the key you are about to
edit. Deriving the vector-scale value means running it with `RERANK_ENABLED=false`.

This README is developer-facing, so it is in English.

## Configuration discipline (spec 3.1.1)

**Never put volatile data in this directory.** Product prices, stock levels,
order status and tracking numbers must come from the business API instead.

Writing them here makes a stale retrieved value coexist with a fresh API value —
the hardest class of customer-service defect to trace, because both answers look
authoritative and neither is obviously wrong.

This directory holds stable facts only: policy clauses, timing commitments,
process steps, platform rules.

## Ingesting

```bash
# customer-service-backend/
uv run python -m business_agent.knowledge.ingest ingest          # skips sources whose content_hash is unchanged
uv run python -m business_agent.knowledge.ingest ingest --force  # re-embeds everything
uv run python -m business_agent.knowledge.ingest stats           # vector_chunks must equal metadata_chunks
uv run python -m business_agent.knowledge.ingest list
uv run python -m business_agent.knowledge.ingest delete --source-id policy.return_policy
uv run python -m business_agent.knowledge.ingest query --text "七天无理由怎么退"
```

> On a fresh checkout, use `--force`. Chunk metadata lives in the shared MySQL
> instance while the Chroma index is a local gitignored directory, and the skip
> logic only compares `content_hash` against MySQL — it never checks whether the
> local vector store actually holds those vectors. Plain `ingest` will then report
> every source `skipped`, exit 0, and leave you with an empty index. The only
> reliable check is `stats`: `vector_chunks` must equal `metadata_chunks`.
