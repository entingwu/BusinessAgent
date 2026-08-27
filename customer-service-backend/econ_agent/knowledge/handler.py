from econ_agent.domain.messages import BotMessage
from econ_agent.domain.state import DialogueState
from econ_agent.knowledge.intents import KnowledgeIntent
from econ_agent.knowledge.provider.register import KnowledgeRegister
from econ_agent.knowledge.responder import KnowledgeResponder


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
    # 1. 根据知识意图查询提供者ID
    provider_ids = self._get_provider_ids_by_intents(intents)

    chunks = []
    # 2. 根据提供者ID，查询提供这对象(Provider)
    for provider_id in provider_ids:
        provider = self.knowledge_register.get_provider_by_id(provider_id)

        # 3. 调用提供者的检索方法 获取到各个提供者提供的内容
        chunk = await provider.retrival(state)
        chunks.extend(chunk)

    # 4. 将从所有提供者查询获取到的结果给responder组件用
    messages = await self.knowledge_responder.respond(chunks, state)
    # 5. 封装数据结果返回
    return messages

  def _get_provider_ids_by_intents(self, intents: list[str]) -> list[str]:
    """
    根据知识意图查询提供者ID
    """
    provider_ids = []
    for intent_id in intents:
        knowledge_intent = self.knowledge_intents[intent_id]
        provider_ids.extend(knowledge_intent.provider_ids)

    return list(set(provider_ids))