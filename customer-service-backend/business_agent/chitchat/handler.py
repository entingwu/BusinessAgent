from business_agent.chitchat.responder import ChitChatResponder
from business_agent.domain.messages import BotMessage
from business_agent.domain.state import DialogueState


class ChitChatHandler:

    def __init__(self, chat_responder: ChitChatResponder):
        self.chat_responder = chat_responder

    async def handle(self,
                     chitchat: str,
                     state: DialogueState) -> list[BotMessage]:
        bot_messages = await self.chat_responder.respond(chitchat, state)
        return bot_messages