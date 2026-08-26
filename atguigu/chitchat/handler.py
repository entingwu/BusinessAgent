from atguigu.chitchat.responder import ChitChatResponder
from atguigu.domain.messages import BotMessage
from atguigu.domain.state import DialogueState


class ChitChatHandler:

    def __init__(self, chat_responder: ChitChatResponder):
        self.chat_responder = chat_responder

    async def handle(self,
                     chitchat: str,
                     state: DialogueState) -> list[BotMessage]:
        bot_messages = await self.chat_responder.respond(chitchat, state)
        return bot_messages