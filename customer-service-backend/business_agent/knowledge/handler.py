import logging

from business_agent.domain.messages import BotMessage
from business_agent.domain.state import DialogueState
from business_agent.knowledge.intents import KnowledgeIntent
from business_agent.knowledge.provider.provider import KnowledgeChunk, KnowledgeUnavailableError
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

    chunks: list[KnowledgeChunk] = []
    # 2. Resolve the Provider objects from those ids
    for provider_id in provider_ids:
        provider = self.knowledge_register.get_provider_by_id(provider_id)

        # 3. Call each provider's retrieval method and collect what it returns
        try:
            chunk = await provider.retrival(state)
        except KnowledgeUnavailableError as error:
            # The vector store or embedding service is unavailable: degrade straight to
            # "cannot look this up, let me hand you to a human". Carrying on and letting the LLM
            # invent an answer from its own knowledge is never allowed (spec 5.1 / C.4.7).
            logger.warning("knowledge_provider_unavailable provider_id=%s error=%s", provider_id, error)
            return await self.knowledge_responder.respond_unavailable(
                state, provider_ids=provider_ids, error=f"{provider_id}: {error}")

        chunks.extend(chunk)

    # 4. Hand everything the providers returned to the responder, which does the sorting,
    #    Top-K, context trimming and miss fallback
    messages = await self.knowledge_responder.respond(chunks, state, provider_ids=provider_ids)
    # 5. Wrap the result and return
    return messages

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
