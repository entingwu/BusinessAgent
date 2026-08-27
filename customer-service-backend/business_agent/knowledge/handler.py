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
    # 1. 根据知识意图查询提供者ID
    provider_ids = self._get_provider_ids_by_intents(intents)

    chunks: list[KnowledgeChunk] = []
    # 2. 根据提供者ID，查询提供这对象(Provider)
    for provider_id in provider_ids:
        provider = self.knowledge_register.get_provider_by_id(provider_id)

        # 3. 调用提供者的检索方法 获取到各个提供者提供的内容
        try:
            chunk = await provider.retrival(state)
        except KnowledgeUnavailableError as error:
            # 向量库或 Embedding 服务不可用：直接降级为「暂时查不了，帮你转人工」，
            # 绝不允许继续往下走、让 LLM 用自身知识把答案编出来（规范 5.1 / C.4.7）
            logger.warning("knowledge_provider_unavailable provider_id=%s error=%s", provider_id, error)
            return await self.knowledge_responder.respond_unavailable(state)

        chunks.extend(chunk)

    # 4. 将从所有提供者查询获取到的结果给responder组件用
    #    responder 负责排序、Top-K、上下文截断与未命中兜底
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

    # 排序保证 Provider 调用顺序稳定，便于回读日志比对
    return sorted(set(provider_ids))
