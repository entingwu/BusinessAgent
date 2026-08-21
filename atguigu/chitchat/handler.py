from atguigu.domain.messages import BotMessage
from atguigu.domain.state import DialogueState


class ChitChatHandler:

  async def handle(self,
                   chitchat: str,
                   state: DialogueState) -> list[BotMessage]:
    return []