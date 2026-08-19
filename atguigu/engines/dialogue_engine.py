"""
Dialogue Engine: modify attributes of dialogue state
"""


import time

from atguigu.chitchat.handler import ChitChatHandler
from atguigu.clarify.responder import ClarifyResponder
from atguigu.domain.contexts import TaskContext
from atguigu.domain.messages import BotMessage, MessageType, ProcessedResult, UserMessage
from atguigu.domain.state import DialogueState
from atguigu.knowledge.handler import KnowledgeHandler
from atguigu.plan.planner import TurnPlanner
from atguigu.plan.turn_plan import TurnPlan
from atguigu.plan.validator import TurnPlanValidator
from atguigu.task.handler import TaskHandler


class DialogueEngine:

  def __init__(self, 
               turn_planner: TurnPlanner, 
               turn_plan_validator: TurnPlanValidator, 
               clarify_responder: ClarifyResponder,
               task_handler: TaskHandler,
               knowledge_handler: KnowledgeHandler,
               chitchat_handler: ChitChatHandler):
    self.turn_planner = turn_planner
    self.turn_plan_validator = turn_plan_validator
    self.clairfy_responder = clarify_responder
    self._task_handler = task_handler
    self._knowledge_handler = knowledge_handler
    self._chitchat_handler = chitchat_handler

  async def handle_message(self, user_message: UserMessage, dialogue_state: DialogueState) -> ProcessedResult:
    """
    Goal: Handle message core entrance.
    Args:
        user_message:
        dialogue_state:
    Returns:
    """
    # 1. Prepare session
    self._prepare_session(dialogue_state)

    # 2. start turn
    self._start_turn(user_message, dialogue_state)

    # 3. Spliting Message (text, object)
    # 3.1 Text message
    if user_message.type is MessageType.TEXT:
      bot_messages = await self._handle_text_message(dialogue_state)

    # 3.2 object message
    else:
      bot_messages = await self._handle_object_message(dialogue_state)

    # 4. Submit
    dialogue_state.pending_turn.bot_messages = bot_messages
    dialogue_state.commit_pending_turn()

    # 5. Return bot message
    return ProcessedResult(message_id=user_message.message_id, messages=bot_messages)

  def _prepare_session(self, state: DialogueState):
    """
    Goal: Create session object
    Args:
        dialogue_state
    """
    # 1. Retrieve current session
    current_session = state.current_session()

    # 2. No current session
    if current_session is None:
      # a) create session
      state.start_session()
    else:
      # 3.1 Check if session is expired
      now = time.time()
      if now - current_session.activated_at > 60 * 60:
        # a) Close expired session
        state.close_current_session()
        # b) Reset running expired session dialogue state
        state.reset_runtime_state_for_new_session()
        # c) Create new session
        state.start_session()
      else:
        # Not expired
        current_session.activated_at = now


  def _start_turn(self, user_message: UserMessage, state: DialogueState):
    state.begin_turn(user_message)

  async def _handle_text_message(self, dialogue_state: DialogueState) -> list[BotMessage]:
    """
    Goal: Process text message (llm analyzes routing, and plan path)
    Args: 
        dialogue_state
    Returns:
    """
    # 1. Use turn planner
    turn_plan: TurnPlan = await self.turn_planner.predict(dialogue_state, flow_list=self._task_handler.flow_list)

    # 2. Use turn validator to evaluate planned result
    validated = self.turn_plan_validator.validate(turn_plan, dialogue_state)

    # 3. Process validated failure
    if not validated:
      return await self.clairfy_responder.respond(validated, dialogue_state)

    # 4. validated succeed(which path? Go to path handler to execute path logic)
    if turn_plan.task is not None:
      return self.task_handler.handle()
    elif turn_plan.knowledge is not None:
      return self.knowledge_handler.handle()
    elif turn_plan.chitchat is not None:
      return self.chitchat_handler.handle()
    else:
      pass

    # 5. Directly return bot message
    return [BotMessage(text="Hi, I am smart chatbot helper")]


  async def _handle_object_message(self, dialogue_state: DialogueState) -> list[BotMessage]:
    pass