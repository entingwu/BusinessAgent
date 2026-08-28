"""
Knowledge answer generation.

Spec B.6 item 3 names the old behaviour this replaces: "\n\n".join every provider's chunks
indiscriminately into the prompt. The rules now:

1. Multiple hits are sorted by similarity and cut to Top-K (settings.knowledge_top_k).
2. Total context length stays within settings.knowledge_context_max_tokens; anything over budget
   is dropped lowest-similarity first.
3. The answer must be built from the retrieved chunks and may not go beyond what they say.
4. A miss, or everything below threshold, returns fixed fallback text and **never calls the
   LLM** — fabrication is ruled out mechanically rather than by instruction.
5. When the retrieval stack is unavailable, degraded text ("cannot look this up, let me hand you
   to a human") is returned; it must never fall back to the model's own knowledge.
6. Hit chunk ids and similarities are recorded internally so answers stay traceable: both to the
   log and to the retrieval_traces table. (Logs roll over and cannot be queried by turn_id, so
   both are needed — see knowledge/trace.py.)
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

# Miss fallback: say what is needed and offer a human, without stating any fact.
#
# "talk to a human" is not casual wording — it must be a phrase that actually exists in
# HUMAN_REQUEST_PATTERNS_EN in handoff/control.py. This text teaches the user a sentence whose
# effect is decided in another file: if the two drift apart, we are teaching them an incantation
# that does nothing, and the failure is silent (the user says it, and nothing happens).
# Changing either side means checking the other.
FALLBACK_NO_HIT_TEXT = (
    "I could not find anything in the merchant's knowledge base that covers this, "
    "and I will not answer from impression. "
    "You can add some context — an order number, a product name, or whether this is about "
    "returns, refunds or shipping — and I will look again. "
    "You can also just say \"talk to a human\" and I will hand you over to a human agent."
)

# Degraded fallback: the vector store or the embedding service is unavailable (spec 5.1 / C.4.7)
FALLBACK_UNAVAILABLE_TEXT = (
    "I cannot reach the knowledge base right now, and I would rather not guess than give you "
    "something inaccurate. I can hand you over to a human agent, or you can ask me again shortly."
)


class KnowledgeResponder:

    def __init__(self, trace_recorder: KnowledgeTraceRecorder | None = None):
        self._trace_recorder = trace_recorder or KnowledgeTraceRecorder()

    async def respond(self,
                      chunks: list[KnowledgeChunk],
                      state: DialogueState,
                      provider_ids: list[str] | None = None) -> list[BotMessage]:
        """
        Goal: build the answer from the retrieved chunks; a miss takes the fallback. Both paths
              write a trace record.
        Args:
            chunks: the chunks each provider retrieved
            state: the dialogue state
            provider_ids: the providers queried this turn; on a miss they record which source
                came up empty
        Returns: list[BotMessage]
        """
        # 1. Sort, cut to Top-K, trim to the context budget
        selected, dropped = self._select_chunks(chunks)

        # 2. Miss: fixed fallback text, no LLM call, fabrication ruled out (spec 5.2 /
        #    acceptance criterion 3). Write one outcome=no_hit trace row so "why did this turn
        #    fall back?" can be answered afterwards.
        if not selected:
            logger.info("knowledge_respond fallback=no_hit candidates=%s", len(chunks))
            # Consecutive misses signal "the Agent cannot answer this"; at the threshold they
            # trigger a handoff (spec 3.3.4). This is the only place that knows whether this turn
            # hit, so the counter is updated here.
            state.note_knowledge_miss(missed=True)
            await self._trace_recorder.record(
                state,
                outcome=OUTCOME_NO_HIT,
                provider_ids=provider_ids or [],
                dropped=dropped,
            )
            return [BotMessage(text=FALLBACK_NO_HIT_TEXT)]

        # A hit — reset the consecutive-miss counter
        state.note_knowledge_miss(missed=False)

        # 3. Record hit chunk ids and similarities so the answer stays traceable: log +
        #    retrieval_traces table
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

        # 4. Load the prompt template
        prompt_template_str = load_prompt_template("knowledge_respond")

        # 5. Instantiate the template object
        prompt_template = PromptTemplate.from_template(template=prompt_template_str, template_format="jinja2")

        # 6. Define the chain
        chain = prompt_template | llm_client | StrOutputParser()

        # 7. Invoke
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
        Goal: the degraded reply for when the retrieval stack is unavailable. No LLM call, and
              never an answer from the model's own knowledge.
              It writes an outcome=unavailable trace row too — afterwards it must be possible to
              tell "the knowledge base does not contain this" from "the knowledge base could not
              be queried at all".
        Args:
            state: the dialogue state
            provider_ids: the providers queried this turn
            error: a summary of what failed
        Returns: list[BotMessage]
        """
        logger.warning("knowledge_respond fallback=unavailable sender_id=%s error=%s", state.sender_id, error)
        # To the user, degraded and miss are the same thing — they asked and got no answer — so
        # it counts towards the handoff threshold too
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
        Goal: sort -> Top-K -> trim to the total context budget.
              Chunks from business APIs (score is None) are authoritative live data: they are
              always kept and never compete for a Top-K slot. Vector hits are taken highest
              similarity first, and dropped lowest first when the budget runs out.
        Args:
            chunks: the candidate chunks
        Returns: (chunks that made it into the prompt, [(dropped chunk, reason)])
                 The dropped ones are returned too and written to retrieval_traces — when
                 tuning the threshold later, "which chunks just barely missed the context" is
                 exactly the information you need.
        """
        authoritative: list[KnowledgeChunk] = []
        retrieved: list[KnowledgeChunk] = []
        seen_ids: set[str] = set()

        for chunk in chunks:
            if not chunk or not (chunk.content or "").strip():
                continue
            # Several providers can return the same chunk — de-duplicate by chunk_id
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
                # Budget exhausted; everything left scores lower, so cut here (spec 3.1.2
                # default parameters)
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
        Goal: assemble the chunks into a numbered, source-labelled context. The source labels go
              into the prompt as well, so the model can check which chunk a sentence came from
              and so a human reading the log can cross-reference it.
        Args:
            chunks: the selected chunks
        Returns: str
        """
        blocks = []
        for index, chunk in enumerate(chunks, start=1):
            if chunk.score is None:
                header = f"[snippet {index}] source: {chunk.citation()} (live business API data)"
            else:
                header = f"[snippet {index}] source: {chunk.citation()} (similarity {chunk.score:.3f})"
            blocks.append(f"{header}\n{chunk.content.strip()}")
        return "\n\n".join(blocks)
