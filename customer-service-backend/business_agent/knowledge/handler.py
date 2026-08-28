import logging

from business_agent.config.settings import settings
from business_agent.domain.messages import BotMessage
from business_agent.domain.state import DialogueState
from business_agent.knowledge.intents import KnowledgeIntent
from business_agent.knowledge.provider.provider import KnowledgeChunk, KnowledgeUnavailableError
from business_agent.knowledge.provider.rag import VectorKnowledgeProvider
from business_agent.knowledge.provider.register import KnowledgeRegister
from business_agent.knowledge.responder import KnowledgeResponder

logger = logging.getLogger(__name__)


class KnowledgeHandler:

  def __init__(self, 
               knowledge_intents: dict[str, KnowledgeIntent],
               knowledge_register: KnowledgeRegister,
               knowledge_responder: KnowledgeResponder):
    self.knowledge_intents = knowledge_intents
    self.knowledge_register = knowledge_register
    self.knowledge_responder = knowledge_responder

  async def handle(self,
                   state: DialogueState,
                   intents: list[str]) -> list[BotMessage]:
    # 1. Resolve provider ids from the knowledge intents
    provider_ids = self._get_provider_ids_by_intents(intents)

    # 2. Query the providers.
    #
    # The vector-backed ones are merged into a single retrieval. They differ only in their
    # source_type filter — rag.default takes "document", faq.default takes "faq" — and both run
    # the same query text through the same embed → Milvus → rerank chain. Run separately, that is
    # two rerank round trips for one question, and rerank is a remote call costing ~2.2s. Merged,
    # it is one.
    #
    # This is deliberately **not** concurrency. An earlier attempt used asyncio.gather and
    # deadlocked: retrieval stopped producing any log line at all, the request hung past 90s, and
    # the process stayed alive. The local BGE-M3 model object and the Milvus client cannot be
    # driven from two coroutines at once and neither fails loudly when you try. Merging removes
    # the second call instead of overlapping it, so nothing is shared concurrently.
    #
    # top_k is scaled by the number of merged providers so the candidate pool stays the size it
    # was — one call asking for k would otherwise return half of what two calls asking for k each
    # returned, and that is a recall change disguised as a latency fix.
    vector_providers, other_providers = [], []
    for provider_id in provider_ids:
        provider = self.knowledge_register.get_provider_by_id(provider_id)
        (vector_providers if isinstance(provider, VectorKnowledgeProvider)
         else other_providers).append((provider_id, provider))

    chunks: list[KnowledgeChunk] = []
    try:
        if len(vector_providers) > 1:
            chunks.extend(await self._retrieve_merged(state, vector_providers))
        else:
            for provider_id, provider in vector_providers:
                chunks.extend(await provider.retrival(state))

        # 3. Non-vector providers (the business APIs) are unrelated calls and stay separate.
        for provider_id, provider in other_providers:
            chunks.extend(await provider.retrival(state))
    except KnowledgeUnavailableError as error:
        # The vector store or embedding service is unavailable: degrade straight to "cannot look
        # this up, let me hand you to a human". Carrying on and letting the LLM invent an answer
        # from its own knowledge is never allowed (spec 5.1 / C.4.7).
        logger.warning("knowledge_provider_unavailable error=%s", error)
        return await self.knowledge_responder.respond_unavailable(
            state, provider_ids=provider_ids, error=str(error))

    # 4. Hand everything the providers returned to the responder, which does the sorting,
    #    Top-K, context trimming and miss fallback
    messages = await self.knowledge_responder.respond(chunks, state, provider_ids=provider_ids)
    # 5. Wrap the result and return
    return messages

  async def _retrieve_merged(self,
                             state: DialogueState,
                             vector_providers: list[tuple[str, VectorKnowledgeProvider]]
                             ) -> list[KnowledgeChunk]:
    """
    Goal: one retrieval covering every vector provider's source_type, instead of one per provider

    Chunks come back carrying the source_type they were indexed with, so attribution is restored
    by mapping that back to the provider that owns it. Getting this wrong would not break the
    answer — the responder merges everything anyway — but it would make retrieval_traces lie
    about which source a citation came from, and traces are the only record of that.
    """
    first_provider = vector_providers[0][1]
    source_types: list[str] = []
    owner_by_source_type: dict[str, str] = {}
    for provider_id, provider in vector_providers:
      for source_type in provider.source_types:
        if source_type not in owner_by_source_type:
          owner_by_source_type[source_type] = provider_id
          source_types.append(source_type)

    query_text = first_provider._build_query_text(state)
    chunks = await first_provider._retriever.retrieve(
      query_text,
      source_types=tuple(source_types),
      top_k=settings.knowledge_top_k * len(vector_providers),
      provider_id=None)

    for chunk in chunks:
      chunk.provider_id = owner_by_source_type.get(chunk.source_type, chunk.provider_id)
    return chunks

  def _get_provider_ids_by_intents(self, intents: list[str]) -> list[str]:
    """
    Resolve provider ids from knowledge intents.
    """
    provider_ids = []
    for intent_id in intents:
        knowledge_intent = self.knowledge_intents[intent_id]
        provider_ids.extend(knowledge_intent.provider_ids)

    # Sorting keeps the provider call order stable, which makes logs comparable when read back
    return sorted(set(provider_ids))
