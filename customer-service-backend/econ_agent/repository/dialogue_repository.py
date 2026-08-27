import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.mysql import insert # mysql package

from econ_agent.domain.state import DialogueState
from econ_agent.repository.dialogue_record import DialogueRecord


class DialogueRepository:

  def __init__(self, session: AsyncSession):
    self._session = session

  async def load_state(self, sender_id: str) -> DialogueState:
    """
    Goal: Read complete Dialogue state based on user id
    Args: sender_id
    """
    # 1. Define SQL
    stmt = select(DialogueRecord).where(DialogueRecord.sender_id == sender_id)

    # 2. Execute SQL
    cursor_result = await self._session.execute(stmt)

    # 3. Fetch result object
    dialogue_record = cursor_result.scalar_one_or_none()
    # 3.1 User does not exist
    if dialogue_record is None:
      return DialogueState(sender_id=sender_id)
    # 3.2 User exists
    dialogue_record_dict = json.loads(dialogue_record.state_json)

    return DialogueState.from_dict(dialogue_record_dict)

  async def save_state(self, sender_id: str, dialogue_state: DialogueState):
      """
      Goal: Save engine updated Dialogue State to database.
      If user does not exist, call save_state, insert a record to database.
      If user exists, call save_state, update current user state_json str.
      
      Traditional idea: before inserting record, and select based on sender id.
      Save if exists, else updates.
      We can do this at SQL statement.
      MySQL: advanced SQL for insert and update (唯一值： 主键索引、 唯一索引)
      Args: 
          sender_id, 
          dialogue_state
      Returns:
      """
      # 1. Convert dialogue state
      dialogue_state_dict = dialogue_state.to_dict()
      dialogue_state_str = json.dumps(dialogue_state_dict, ensure_ascii=False)

      # 2. Define SQL
      # 2.1 Define INSERT SQL statement
      insert_stmt = insert(DialogueRecord).values(sender_id=sender_id, state_json=dialogue_state_str)
      # 2.2 Define UPDATE SQL statement
      update_stmt=insert_stmt.on_duplicate_key_update(state_json=insert_stmt.inserted.state_json)

      # 3. Execute SQL
      await self._session.execute(update_stmt)

      # 4. Submit result
      await self._session.commit()


if __name__ == '__main__':
  dict_container = {"name": "tom", "age": 18}
  print(json.dumps(dict_container, indent=2, ensure_ascii=False))

