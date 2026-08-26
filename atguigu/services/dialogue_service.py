from atguigu.domain.messages import ChatHistoryMessage, UserMessage, ProcessedResult
from atguigu.engines.dialogue_engine import DialogueEngine
from atguigu.repository.dialogue_repository import DialogueRepository
from atguigu.chat_history.builder import ChatHistoryBuilder


class DialogueStateService:

  def __init__(self,
                 engine: DialogueEngine,
                 repository: DialogueRepository
                 ):
        self._engine = engine
        self._repository = repository

  async def process_message(self, user_message: UserMessage) -> ProcessedResult:
      """
      Goal: Dialog message processing core entrance:
      Args:
          user_message:

      Returns:
      """
      # 1. Read current user dialog state from database I/O
      dialogue_state = await self._repository.load_state(user_message.sender_id)

      # 2. Engine uses (updated attributes of dialog state) for calculation
      processed_result = await self._engine.handle_message(user_message, dialogue_state)

      # 3. Save modified dialog state to database I/O
      await self._repository.save_state(user_message.sender_id, dialogue_state)
      return processed_result


  async def get_chat_history(self, sender_id: str) -> list[ChatHistoryMessage]:
        """
        Goal: 查询该用户所有会话下的聊天内容（当前session下的历史对话）
        """
        state = await self._repository.load_state(sender_id)
        final_chat_history_messages = []

        for session in state.sessions:
            for turn in session.turns:
                user_message = turn.user_message
                user_chat_history_message = ChatHistoryBuilder.build_chat_history(
                    session.session_id,
                    "user",
                    user_message.text,
                    user_message.object,
                )
                final_chat_history_messages.append(user_chat_history_message)

                for bot_message in turn.bot_messages:
                    bot_chat_history_message = ChatHistoryBuilder.build_chat_history(
                        session.session_id,
                        "bot",
                        bot_message.text,
                        bot_message.object
                    )
                    final_chat_history_messages.append(bot_chat_history_message)

        return final_chat_history_messages
    