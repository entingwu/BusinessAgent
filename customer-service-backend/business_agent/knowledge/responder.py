"""
知识回复生成

规范 B.6 第 3 条点名要改掉的旧行为：把所有 provider 的分片无差别 "\n\n".join 塞进提示词。
现在的规则：

1. 命中多个分片按相似度排序取 Top-K（settings.knowledge_top_k）
2. 上下文分片总长 ≤ settings.knowledge_context_max_tokens，超出按相似度从低到高截断
3. 回复必须基于命中分片组织，不得超出分片信息作答
4. 未命中或全部低于阈值 → 固定兜底话术，**不调用 LLM**，从机制上杜绝编造
5. 检索链路不可用 → 降级话术「暂时查不了，帮你转人工」，不得退化为模型自身知识作答
6. 内部记录命中的分片 ID 与相似度，让回复可溯源：既打日志，也写 retrieval_traces 表
   （日志会滚掉、也没法按 turn_id 查回来，两者都要，见 knowledge/trace.py）
"""
import logging

from business_agent.chat_history.builder import ChatHistoryBuilder
from business_agent.config.settings import settings
from business_agent.domain.state import DialogueState
from business_agent.infrastructure.llm_client import llm_client
from business_agent.knowledge.ingest.tokens import estimate_tokens
from business_agent.knowledge.provider.provider import KnowledgeChunk
from business_agent.knowledge.trace import (
    DROP_CONTEXT_BUDGET,
    DROP_TOP_K,
    OUTCOME_ANSWERED,
    OUTCOME_NO_HIT,
    OUTCOME_UNAVAILABLE,
    KnowledgeTraceRecorder,
)
from business_agent.prompt.loader import load_prompt_template
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser

from business_agent.domain.messages import BotMessage

logger = logging.getLogger(__name__)

# 未命中兜底：说明需要什么信息 + 引导转人工，不做任何事实性陈述
FALLBACK_NO_HIT_TEXT = (
    "这个问题我在商家的知识库里没有查到对应的说明，不能凭印象回答你。"
    "你可以补充一下具体场景（比如涉及的订单号、商品名称，或者你想了解的是退货、退款还是配送哪一类），"
    "我再帮你找一次；也可以直接说「转人工」，我把你接给人工客服。"
)

# 降级兜底：向量库或 Embedding 服务不可用（规范 5.1 / C.4.7）
FALLBACK_UNAVAILABLE_TEXT = (
    "知识库暂时查不了，为避免给你不准确的信息，我先不猜。"
    "我帮你转人工客服跟进，或者你稍后再问我一次。"
)


class KnowledgeResponder:

    def __init__(self, trace_recorder: KnowledgeTraceRecorder | None = None):
        self._trace_recorder = trace_recorder or KnowledgeTraceRecorder()

    async def respond(self,
                      chunks: list[KnowledgeChunk],
                      state: DialogueState,
                      provider_ids: list[str] | None = None) -> list[BotMessage]:
        """
        Goal: 基于命中分片组织回复；未命中走兜底。两条路径都落溯源记录。
        Args:
            chunks: 各 Provider 检索到的分片
            state: 对话状态
            provider_ids: 本轮参与检索的 Provider ID，未命中时按它落「哪个源没查到」
        Returns: list[BotMessage]
        """
        # 1. 排序 + Top-K + 上下文长度截断
        selected, dropped = self._select_chunks(chunks)

        # 2. 未命中：固定兜底话术，不调用 LLM，杜绝编造（规范 5.2 / 验收标准 3）
        #    落一行 outcome=no_hit 的溯源记录，事后能回答「这一轮为什么兜底了」
        if not selected:
            logger.info("knowledge_respond fallback=no_hit candidates=%s", len(chunks))
            # 连续未命中是「Agent 答不了」的信号，累计到阈值触发转人工（规范 3.3.4）。
            # 这里是唯一知道本轮命中与否的地方，所以计数在此更新
            state.note_knowledge_miss(missed=True)
            await self._trace_recorder.record(
                state,
                outcome=OUTCOME_NO_HIT,
                provider_ids=provider_ids or [],
                dropped=dropped,
            )
            return [BotMessage(text=FALLBACK_NO_HIT_TEXT)]

        # 命中，连续未命中计数清零
        state.note_knowledge_miss(missed=False)

        # 3. 内部记录命中的分片 ID 与相似度，回复可溯源：日志 + retrieval_traces 表
        logger.info(
            "knowledge_respond hits=%s dropped=%s traces=%s",
            len(selected), len(dropped), [chunk.trace() for chunk in selected],
        )
        await self._trace_recorder.record(
            state,
            outcome=OUTCOME_ANSWERED,
            provider_ids=provider_ids or [],
            selected=selected,
            dropped=dropped,
        )

        # 4. 加载提示词模版内容
        prompt_template_str = load_prompt_template("knowledge_respond")

        # 5. 实例化提示词模版对象
        prompt_template = PromptTemplate.from_template(template=prompt_template_str, template_format="jinja2")

        # 6. 定义chain
        chain = prompt_template | llm_client | StrOutputParser()

        # 7. 调用
        result = await chain.ainvoke({
            "user_message": ChatHistoryBuilder.build_user_message_str(state.pending_turn.user_message),
            "history": ChatHistoryBuilder.build(state.current_session().turns[-10:]),
            "knowledge_content": self._build_knowledge_content(selected),
            "fallback_text": FALLBACK_NO_HIT_TEXT,
        })

        return [BotMessage(text=result)]

    async def respond_unavailable(self,
                                  state: DialogueState,
                                  provider_ids: list[str] | None = None,
                                  error: str | None = None) -> list[BotMessage]:
        """
        Goal: 检索链路不可用时的降级回复。不调用 LLM，不使用模型自身知识作答。
              同样落一行 outcome=unavailable 的溯源记录——事后要能区分
              「知识库里没有」和「知识库根本没查成」。
        Args:
            state: 对话状态
            provider_ids: 本轮参与检索的 Provider ID
            error: 失败原因摘要
        Returns: list[BotMessage]
        """
        logger.warning("knowledge_respond fallback=unavailable sender_id=%s error=%s", state.sender_id, error)
        # 降级与未命中对用户是同一件事——问了但没得到答案，同样计入转人工阈值
        state.note_knowledge_miss(missed=True)
        await self._trace_recorder.record(
            state,
            outcome=OUTCOME_UNAVAILABLE,
            provider_ids=provider_ids or [],
            note=error,
        )
        return [BotMessage(text=FALLBACK_UNAVAILABLE_TEXT)]

    def _select_chunks(self,
                       chunks: list[KnowledgeChunk]
                       ) -> tuple[list[KnowledgeChunk], list[tuple[KnowledgeChunk, str]]]:
        """
        Goal: 排序 → Top-K → 上下文总长截断
              业务接口分片（score 为 None）是权威实时数据，始终保留，不参与 Top-K 竞争；
              向量检索分片按相似度从高到低取 Top-K，超出总长预算时从低到高丢弃。
        Args:
            chunks: 候选分片
        Returns: (进了提示词的分片, [(被丢掉的分片, 丢弃原因)])
                 被丢掉的也返回出去，一起落进 retrieval_traces——
                 事后调阈值时「差一点就进上下文的是哪几片」是关键信息。
        """
        authoritative: list[KnowledgeChunk] = []
        retrieved: list[KnowledgeChunk] = []
        seen_ids: set[str] = set()

        for chunk in chunks:
            if not chunk or not (chunk.content or "").strip():
                continue
            # 多个 Provider 可能召回同一分片，按 chunk_id 去重
            if chunk.chunk_id is not None:
                if chunk.chunk_id in seen_ids:
                    continue
                seen_ids.add(chunk.chunk_id)

            if chunk.score is None:
                authoritative.append(chunk)
            else:
                retrieved.append(chunk)

        retrieved.sort(key=lambda item: item.score or 0.0, reverse=True)
        dropped: list[tuple[KnowledgeChunk, str]] = [
            (chunk, DROP_TOP_K) for chunk in retrieved[settings.knowledge_top_k:]
        ]
        retrieved = retrieved[: settings.knowledge_top_k]

        budget = settings.knowledge_context_max_tokens
        selected: list[KnowledgeChunk] = []

        for chunk in authoritative:
            selected.append(chunk)
            budget -= estimate_tokens(chunk.content)

        truncated = False
        for chunk in retrieved:
            cost = estimate_tokens(chunk.content)
            if truncated or budget - cost < 0:
                # 预算耗尽，剩下的都是相似度更低的，直接截断（规范 3.1.2 默认参数）
                if not truncated:
                    logger.info("knowledge_respond truncated_from_chunk=%s score=%s", chunk.chunk_id, chunk.score)
                truncated = True
                dropped.append((chunk, DROP_CONTEXT_BUDGET))
                continue
            selected.append(chunk)
            budget -= cost

        return selected, dropped

    def _build_knowledge_content(self, chunks: list[KnowledgeChunk]) -> str:
        """
        Goal: 把分片拼成带编号与来源标注的上下文。来源标注同时进提示词，
              便于模型自查「这句话出自哪一片」，也便于人工回读日志对照。
        Args:
            chunks: 已选中的分片
        Returns: str
        """
        blocks = []
        for index, chunk in enumerate(chunks, start=1):
            if chunk.score is None:
                header = f"[片段{index}] 来源：{chunk.citation()}（业务接口实时数据）"
            else:
                header = f"[片段{index}] 来源：{chunk.citation()}（相似度 {chunk.score:.3f}）"
            blocks.append(f"{header}\n{chunk.content.strip()}")
        return "\n\n".join(blocks)
