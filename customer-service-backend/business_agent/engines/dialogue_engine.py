"""
Dialogue Engine: modify attributes of dialogue state
"""


import time

from business_agent.chitchat.handler import ChitChatHandler
from business_agent.clarify.responder import ClarifyResponder
from business_agent.domain.messages import BotMessage, FocusedObject, MessageType, ProcessedResult, UserMessage
from business_agent.domain.state import DialogueState
from business_agent.knowledge.handler import KnowledgeHandler
from business_agent.plan.planner import TurnPlanner
from business_agent.plan.turn_plan import ClarifyReason, TurnPlan
from business_agent.plan.validator import TurnPlanValidator
from business_agent.task.commands.command import Command, SetSlotsCommand
from business_agent.task.flows.flows import FlowList
from business_agent.task.flows.steps import CollectionFlowStep
from business_agent.task.handler import TaskHandler


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
    self.clarify_responder = clarify_responder
    self.task_handler = task_handler
    self.knowledge_handler = knowledge_handler
    self.chitchat_handler = chitchat_handler

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
      # a) Save clicked card save to dialog state
      dialogue_state.focused_object = user_message.object
      # b) Process object message
      bot_messages = await self._handle_object_message(user_message.object, 
                                                       dialogue_state, 
                                                       self.task_handler.flow_list)

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
    turn_plan: TurnPlan = await self.turn_planner.predict(dialogue_state, flow_list=self.task_handler.flow_list, knowledge_intents=self.knowledge_handler.knowledge_intents)

    # 2. Use turn validator to evaluate planned result
    validated = self.turn_plan_validator.validate(turn_plan, 
                                                  dialogue_state, 
                                                  flow_list=self.task_handler.flow_list, 
                                                  knowledge_intents=self.knowledge_handler.knowledge_intents)

    # 3. Process validated failure
    if not validated.valid:
      return await self.clarify_responder.respond(validated.reason, dialogue_state)

    # 4. validated succeed(which path? Go to path handler to execute path logic)
    if turn_plan.task is not None:
      return await self.task_handler.handle(turn_plan.task.commands, dialogue_state)
    elif turn_plan.knowledge is not None:
      return await self.knowledge_handler.handle(dialogue_state,turn_plan.knowledge.intents)
    elif turn_plan.chitchat is not None:
      return await self.chitchat_handler.handle(turn_plan.chitchat.chat, dialogue_state)
    else:
      pass

    # 5. Directly return bot message
    return [BotMessage(text="你好，我是一个智能助手")]


  async def _handle_object_message(self, 
                                   object: FocusedObject, 
                                   dialogue_state: DialogueState, 
                                   flow_list: FlowList) -> list[BotMessage]:
    """
    Goal: Process object type, construct set slots command.
    """
    # 1. Try to construct SetSlotsCommand object
    command = self._try_build_set_slots_command(object, dialogue_state, flow_list)

    # 2. check command: 情况3：流程继续推进下一步
    if command:
      return await self.task_handler.handle(commands=[command], dialogue_state=dialogue_state)

    if dialogue_state.active_task is not None: # 情况2：流程继续执行，但是不去推进下一步，而是在执行当前这一步
      return await self.task_handler.handle(commands=[], dialogue_state=dialogue_state)

    # 情况1. 澄清
    return await self.clarify_responder.respond(reason=ClarifyReason.OBJECT_REQUIRES_INTENT, state=dialogue_state)


  def _try_build_set_slots_command(self,
                                   object: FocusedObject,
                                   dialogue_state: DialogueState,
                                   flow_list: FlowList) -> Command | None:
    """
    Goal: Two card type info (order, product info)
    Args:
        dialogue_state
    """
    if object.type == "order":
      if self._is_can_set_slots_command(slot_name="order_number", state=dialogue_state, flow_list=flow_list):
        return SetSlotsCommand(command="set_slots", slots={"order_number": object.id})
      return None
    elif object.type == "product":
      if self._is_can_set_slots_command(slot_name="product_id", state=dialogue_state, flow_list=flow_list):
        return SetSlotsCommand(command="set_slots", slots={"product_id": object.id})
      return None
    else:
      return None

  def _is_can_set_slots_command(self,
                                slot_name: str,
                                state: DialogueState,
                                flow_list: FlowList) -> bool:
    """
    Goal: Process click card
    情况1. 没有业务流程， 返回False
    情况2. 有业务流程, 但是收集步骤的时候, 并不缺少卡片信息, 返回False
    情况3. 有业务流程, 刚好手机该步骤的时候, 点击卡片信息, 返回True
    Args:
        slot_name:
        state:
        flow_list:
    """
    # 1. Retrieve current flow context
    task_context = state.active_task

    # 2. Check if current flow context exists, no exist:
    if task_context is None:
       return False

    # 3. Check if curent context exist
    flow = flow_list.get_flow_by_flow_id(task_context.flow_id)
    if flow is None:  # Protection
      return False

    # 4. check if flow step exists
    step_id = task_context.step_id
    step = flow.get_step_by_step_id(step_id)
    if step is None:  # Protection
      return False

    # 5. Retrieve current step type
    if not isinstance(step, CollectionFlowStep):
      return False

    # missing, provided
    return step.slot_name == slot_name
