from atguigu.domain.messages import UserMessage, ProcessedResult
from atguigu.engines.dialogue_engine import DialogueEngine
from atguigu.repository.dialogue_repository import DialogueRepository


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
      processed_result = await self._engine.handle_message(dialogue_state)

      # 3. Save modified dialog state to database I/O
      await self._repository.save_state(user_message.sender_id, dialogue_state)
      return processed_result