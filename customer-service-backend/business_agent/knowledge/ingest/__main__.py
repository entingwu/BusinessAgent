"""
知识入库命令行

    uv run python -m business_agent.knowledge.ingest ingest [--source-id X] [--force]
    uv run python -m business_agent.knowledge.ingest list
    uv run python -m business_agent.knowledge.ingest delete --source-id X
    uv run python -m business_agent.knowledge.ingest query --text "七天无理由怎么算"
    uv run python -m business_agent.knowledge.ingest calibrate [--file path.jsonl]
    uv run python -m business_agent.knowledge.ingest stats

query 与 calibrate 是规范 C.1 说的「单条测试接口」：调阈值与切分粒度时用它，
省下的调试时间超过写它的成本。
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
    "vector_store_dir": str(settings.resolved_vector_store_dir()),
    "collection": settings.vector_collection_name,
    "vector_chunks": vector_count,
    "metadata_chunks": metadata_count,
    "top_k": settings.knowledge_top_k,
    "score_threshold": settings.knowledge_score_threshold,
  }, ensure_ascii=False, indent=2))


async def _cmd_traces(args) -> None:
  """Goal: 回读某个用户最近几轮的检索溯源记录（retrieval_traces）"""

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

  # 阈值设为 -1 时不过滤，用来观察「未命中问题的最高相似度到底是多少」
  chunks = await retriever.retrieve(args.text, source_types=source_types)
  print(f"query={args.text!r} source_types={source_types} "
        f"top_k={args.top_k or settings.knowledge_top_k} "
        f"threshold={args.threshold if args.threshold is not None else settings.knowledge_score_threshold}")
  if not chunks:
    print("  -> 未命中（responder 会走兜底话术）")
    return
  for chunk in chunks:
    print(f"  score={chunk.score:.4f}  chunk_id={chunk.chunk_id}  title={chunk.source_title}")
    print(f"      {chunk.content[:70].replace(chr(10), ' ')}...")


async def _cmd_calibrate(args) -> None:
  """
  Goal: 阈值校准（规范 C.4.6）
        用例集里 expect=hit 的那批取最低的最高相似度，expect=miss 的那批取最高的最高相似度，
        阈值落在两者之间。两个区间重叠说明切分有问题，应先改切分而不是改阈值。
  """
  path = Path(args.file) if args.file else DEFAULT_CALIBRATION_FILE
  cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

  # 校准阶段关掉阈值过滤，直接看原始相似度分布
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

  print("\n---- 校准结论 ----")
  if hit_tops:
    lowest_hit = min(hit_tops)
    print(f"有答案用例 {len(hit_tops)} 条，最高相似度的最小值 = {lowest_hit[0]:.4f}  ({lowest_hit[1]})")
  if miss_tops:
    highest_miss = max(miss_tops)
    print(f"无答案用例 {len(miss_tops)} 条，最高相似度的最大值 = {highest_miss[0]:.4f}  ({highest_miss[1]})")

  if hit_tops and miss_tops:
    low = min(hit_tops)[0]
    high = max(miss_tops)[0]
    if low > high:
      print(f"两个区间不重叠，阈值可取 ({high:.4f}, {low:.4f}) 之间，建议取中点 {(low + high) / 2:.4f}")
    else:
      print("两个区间重叠。按规范 C.4.6 应先查切分粒度；若换过切分方案仍只剩少数离群点，")
      print("说明是 Embedding 对短问句的表面相似，此时取「去掉离群点后的可分区间」，")
      print("剩余误通过交给提示词的第二道防线（片段与问题对不上就走兜底话术）。")
      sorted_miss = sorted(miss_tops, reverse=True)
      # 逐个剥离最高的 miss，找出第一个能与 hit 区间分开的位置
      for outlier_count in range(1, len(sorted_miss)):
        remaining_high = sorted_miss[outlier_count][0]
        if low > remaining_high:
          print(f"剥离 {outlier_count} 个离群 miss 后：可分区间 ({remaining_high:.4f}, {low:.4f})，"
                f"中点 {(low + remaining_high) / 2:.4f}")
          print(f"  离群点：{[(round(score, 4), question) for score, question in sorted_miss[:outlier_count]]}")
          break

  # 用当前配置的阈值算一遍实际判定，这才是上线值的真实表现
  threshold = settings.knowledge_score_threshold
  hit_pass = [item for item in hit_tops if item[0] >= threshold]
  miss_pass = [item for item in miss_tops if item[0] >= threshold]
  print(f"\n当前配置 KNOWLEDGE_SCORE_THRESHOLD={threshold}")
  print(f"  有答案用例召回：{len(hit_pass)}/{len(hit_tops)}")
  print(f"  无答案用例正确兜底：{len(miss_tops) - len(miss_pass)}/{len(miss_tops)}")
  if miss_pass:
    print(f"  误通过（依赖提示词兜底）：{[(round(score, 4), question) for score, question in miss_pass]}")
  if len(hit_pass) < len(hit_tops):
    missed = [item for item in hit_tops if item[0] < threshold]
    print(f"  被阈值挡掉的有答案用例：{[(round(score, 4), question) for score, question in missed]}")


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(prog="python -m business_agent.knowledge.ingest", description="知识入库与检索调试")
  parser.add_argument("--verbose", action="store_true", help="打印检索日志")
  subparsers = parser.add_subparsers(dest="command", required=True)

  ingest_parser = subparsers.add_parser("ingest", help="加载 → 切分 → 向量化 → 写索引")
  ingest_parser.add_argument("--source-id", action="append", help="只入库指定知识源，可重复")
  ingest_parser.add_argument("--force", action="store_true", help="内容未变化也重新入库")
  ingest_parser.set_defaults(func=_cmd_ingest)

  delete_parser = subparsers.add_parser("delete", help="删除一个知识源（索引与元数据一起删）")
  delete_parser.add_argument("--source-id", required=True)
  delete_parser.set_defaults(func=_cmd_delete)

  list_parser = subparsers.add_parser("list", help="列出已入库的知识源")
  list_parser.set_defaults(func=_cmd_list)

  stats_parser = subparsers.add_parser("stats", help="索引与配置概览")
  stats_parser.set_defaults(func=_cmd_stats)

  query_parser = subparsers.add_parser("query", help="单条检索测试")
  query_parser.add_argument("--text", required=True)
  query_parser.add_argument("--top-k", type=int, default=None)
  query_parser.add_argument("--threshold", type=float, default=None)
  query_parser.add_argument("--source-type", action="append", help="metadata 过滤：faq / document，可重复")
  query_parser.set_defaults(func=_cmd_query)

  traces_parser = subparsers.add_parser("traces", help="回读检索溯源记录（retrieval_traces）")
  traces_parser.add_argument("--sender-id", required=True)
  traces_parser.add_argument("--limit", type=int, default=50)
  traces_parser.set_defaults(func=_cmd_traces)

  calibrate_parser = subparsers.add_parser("calibrate", help="用样本集校准相似度阈值")
  calibrate_parser.add_argument("--file", default=None, help=f"用例集 JSONL，默认 {DEFAULT_CALIBRATION_FILE}")
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
