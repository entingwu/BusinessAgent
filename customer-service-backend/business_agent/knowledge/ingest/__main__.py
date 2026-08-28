"""
Knowledge ingest command line.

    uv run python -m business_agent.knowledge.ingest ingest [--source-id X] [--force]
    uv run python -m business_agent.knowledge.ingest list
    uv run python -m business_agent.knowledge.ingest delete --source-id X
    uv run python -m business_agent.knowledge.ingest query --text "七天无理由怎么算"
    uv run python -m business_agent.knowledge.ingest calibrate [--file path.jsonl]
    uv run python -m business_agent.knowledge.ingest stats

query and calibrate are the "single-shot test interface" spec C.1 asks for: they are what you
use when tuning the threshold and the chunk granularity, and the debugging time they save is more
than what they cost to write.
"""
import argparse
import asyncio
import json
import logging
from pathlib import Path

from business_agent.config.settings import PROJECT_DIR, settings
from business_agent.infrastructure.vector_client import get_vector_client
from business_agent.knowledge.ingest.pipeline import IngestPipeline, run_with_repository
from business_agent.knowledge.provider.rag import KnowledgeRetriever
from business_agent.repository.knowledge_repository import KnowledgeRepository

DEFAULT_CALIBRATION_FILE = PROJECT_DIR / "knowledge_eval" / "calibration_set.jsonl"


async def _cmd_ingest(args) -> None:
  pipeline = IngestPipeline()
  source_ids = args.source_id or None

  async def handler(repository: KnowledgeRepository):
    return await pipeline.ingest(repository, source_ids=source_ids, force=args.force)

  report = await run_with_repository(handler)
  print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))


async def _cmd_delete(args) -> None:
  pipeline = IngestPipeline()

  async def handler(repository: KnowledgeRepository):
    return await pipeline.delete(repository, args.source_id)

  result = await run_with_repository(handler)
  print(f"deleted source_id={result.source_id} removed_chunks={result.chunk_count}")


async def _cmd_list(_args) -> None:
  async def handler(repository: KnowledgeRepository):
    return await repository.list_sources()

  summaries = await run_with_repository(handler)
  if not summaries:
    print("(no knowledge source ingested)")
    return
  for summary in summaries:
    print(f"{summary.source_id:32s} type={summary.source_type:9s} chunks={summary.chunk_count:3d} "
          f"model={summary.embedding_model:20s} ingested_at={summary.ingested_at.isoformat()} name={summary.name}")


async def _cmd_stats(_args) -> None:
  vector_count = await get_vector_client().count()

  async def handler(repository: KnowledgeRepository):
    return await repository.count_chunks()

  metadata_count = await run_with_repository(handler)
  print(json.dumps({
    "embedding_model": settings.embedding_model,
    "embedding_dimensions": settings.embedding_dimensions,
    "vector_backend": settings.vector_backend,
    # 存储位置随后端而异：chroma 是本地目录，milvus 是服务地址。
    # 无条件打印 Chroma 目录会让人以为用的是 Chroma。
    "vector_store": (str(settings.resolved_vector_store_dir())
                     if settings.vector_backend == "chroma" else settings.milvus_uri),
    "collection": settings.vector_collection_name,
    "vector_chunks": vector_count,
    "metadata_chunks": metadata_count,
    "top_k": settings.knowledge_top_k,
    "score_threshold": settings.knowledge_score_threshold,
  }, ensure_ascii=False, indent=2))


async def _cmd_traces(args) -> None:
  """Goal: read back the retrieval traces (retrieval_traces) for a user's recent turns."""

  async def handler(repository: KnowledgeRepository):
    return await repository.list_retrieval_traces(args.sender_id, limit=args.limit)

  rows = await run_with_repository(handler)
  if not rows:
    print(f"(no retrieval trace for sender_id={args.sender_id})")
    return
  for row in rows:
    score = f"{row['score']:.4f}" if row["score"] is not None else "  -   "
    print(f"{row['created_at']}  turn={row['turn_id'][:8]}  {row['outcome']:11s} "
          f"{row['provider_id']:12s} selected={str(row['selected']):5s} "
          f"drop={str(row['drop_reason'] or '-'):15s} score={score}  "
          f"{row['chunk_id'] or '-'}  {row['source_title'] or ''}")


async def _cmd_query(args) -> None:
  retriever = KnowledgeRetriever(top_k=args.top_k, score_threshold=args.threshold)
  source_types = tuple(args.source_type) if args.source_type else None

  # A threshold of -1 disables filtering, which is how you see what the top similarity of a
  # missing question actually is
  chunks = await retriever.retrieve(args.text, source_types=source_types)
  print(f"query={args.text!r} source_types={source_types} "
        f"top_k={args.top_k or settings.knowledge_top_k} "
        f"threshold={args.threshold if args.threshold is not None else settings.knowledge_score_threshold}")
  if not chunks:
    print("  -> miss (the responder will use the fallback text)")
    return
  for chunk in chunks:
    print(f"  score={chunk.score:.4f}  chunk_id={chunk.chunk_id}  title={chunk.source_title}")
    print(f"      {chunk.content[:70].replace(chr(10), ' ')}...")


async def _cmd_calibrate(args) -> None:
  """
  Goal: threshold calibration (spec C.4.6).
        Take the lowest top-similarity among the expect=hit cases and the highest top-similarity
        among the expect=miss cases; the threshold goes between them. If the two ranges overlap,
        the splitting is what is wrong — fix that before touching the threshold.
  """
  path = Path(args.file) if args.file else DEFAULT_CALIBRATION_FILE
  cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

  # Calibration turns threshold filtering off so the raw similarity distribution is visible
  retriever = KnowledgeRetriever(top_k=args.top_k, score_threshold=-1.0)

  hit_tops: list[tuple[float, str]] = []
  miss_tops: list[tuple[float, str]] = []

  for case in cases:
    question = case["question"]
    expect = case["expect"]
    chunks = await retriever.retrieve(question)
    top_score = chunks[0].score if chunks else 0.0
    top_chunk = chunks[0].chunk_id if chunks else "-"
    bucket = hit_tops if expect == "hit" else miss_tops
    bucket.append((top_score, question))
    print(f"{expect:5s} top={top_score:.4f} chunk={top_chunk:28s} {question}")

  print("\n---- calibration result ----")
  if hit_tops:
    lowest_hit = min(hit_tops)
    print(f"{len(hit_tops)} answerable cases, lowest top-similarity = {lowest_hit[0]:.4f}  ({lowest_hit[1]})")
  if miss_tops:
    highest_miss = max(miss_tops)
    print(f"{len(miss_tops)} unanswerable cases, highest top-similarity = {highest_miss[0]:.4f}  ({highest_miss[1]})")

  if hit_tops and miss_tops:
    low = min(hit_tops)[0]
    high = max(miss_tops)[0]
    if low > high:
      print(f"the ranges do not overlap; any threshold in ({high:.4f}, {low:.4f}) works, "
            f"midpoint {(low + high) / 2:.4f} recommended")
    else:
      print("the ranges overlap. Per spec C.4.6, check chunk granularity first; if only a few")
      print("outliers remain after trying another splitting scheme, that is the embedding model")
      print("reading short questions as superficially similar. In that case take the separable")
      print("range with the outliers removed, and leave the remaining false passes to the")
      print("prompt's second line of defence (chunks that do not match the question fall back).")
      sorted_miss = sorted(miss_tops, reverse=True)
      # Strip the highest miss one at a time, looking for the first point that separates cleanly
      # from the hit range
      for outlier_count in range(1, len(sorted_miss)):
        remaining_high = sorted_miss[outlier_count][0]
        if low > remaining_high:
          print(f"after removing {outlier_count} outlier miss case(s): separable range "
                f"({remaining_high:.4f}, {low:.4f}), midpoint {(low + remaining_high) / 2:.4f}")
          print(f"  outliers: {[(round(score, 4), question) for score, question in sorted_miss[:outlier_count]]}")
          break

  # Re-run the verdicts with the configured threshold — this is how the shipped value actually
  # behaves
  threshold = settings.knowledge_score_threshold
  hit_pass = [item for item in hit_tops if item[0] >= threshold]
  miss_pass = [item for item in miss_tops if item[0] >= threshold]
  print(f"\nconfigured KNOWLEDGE_SCORE_THRESHOLD={threshold}")
  print(f"  answerable cases recalled: {len(hit_pass)}/{len(hit_tops)}")
  print(f"  unanswerable cases correctly falling back: {len(miss_tops) - len(miss_pass)}/{len(miss_tops)}")
  if miss_pass:
    print(f"  false passes (left to the prompt fallback): {[(round(score, 4), question) for score, question in miss_pass]}")
  if len(hit_pass) < len(hit_tops):
    missed = [item for item in hit_tops if item[0] < threshold]
    print(f"  answerable cases blocked by the threshold: {[(round(score, 4), question) for score, question in missed]}")


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(prog="python -m business_agent.knowledge.ingest", description="Knowledge ingest and retrieval debugging")
  parser.add_argument("--verbose", action="store_true", help="print retrieval logs")
  subparsers = parser.add_subparsers(dest="command", required=True)

  ingest_parser = subparsers.add_parser("ingest", help="load -> split -> embed -> index")
  ingest_parser.add_argument("--source-id", action="append", help="ingest only these sources; repeatable")
  ingest_parser.add_argument("--force", action="store_true", help="re-ingest even when the content has not changed")
  ingest_parser.set_defaults(func=_cmd_ingest)

  delete_parser = subparsers.add_parser("delete", help="delete a knowledge source (index and metadata together)")
  delete_parser.add_argument("--source-id", required=True)
  delete_parser.set_defaults(func=_cmd_delete)

  list_parser = subparsers.add_parser("list", help="list the ingested knowledge sources")
  list_parser.set_defaults(func=_cmd_list)

  stats_parser = subparsers.add_parser("stats", help="index and configuration overview")
  stats_parser.set_defaults(func=_cmd_stats)

  query_parser = subparsers.add_parser("query", help="single-shot retrieval test")
  query_parser.add_argument("--text", required=True)
  query_parser.add_argument("--top-k", type=int, default=None)
  query_parser.add_argument("--threshold", type=float, default=None)
  query_parser.add_argument("--source-type", action="append", help="metadata filter: faq / document; repeatable")
  query_parser.set_defaults(func=_cmd_query)

  traces_parser = subparsers.add_parser("traces", help="read back retrieval traces (retrieval_traces)")
  traces_parser.add_argument("--sender-id", required=True)
  traces_parser.add_argument("--limit", type=int, default=50)
  traces_parser.set_defaults(func=_cmd_traces)

  calibrate_parser = subparsers.add_parser("calibrate", help="calibrate the similarity threshold against a sample set")
  calibrate_parser.add_argument("--file", default=None, help=f"case-set JSONL; defaults to {DEFAULT_CALIBRATION_FILE}")
  calibrate_parser.add_argument("--top-k", type=int, default=5)
  calibrate_parser.set_defaults(func=_cmd_calibrate)

  return parser


def main() -> None:
  args = build_parser().parse_args()
  logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                      format="%(levelname)s %(name)s %(message)s")
  asyncio.run(args.func(args))


if __name__ == '__main__':
  main()
