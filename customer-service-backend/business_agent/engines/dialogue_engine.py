"""
Dialogue Engine: modify attributes of dialogue state
"""


import time

from business_agent.chitchat.handler import ChitChatHandler
from business_agent.clarify.responder import ClarifyResponder
from business_agent.domain.messages import BotMessage, FocusedObject, MessageType, ProcessedResult, UserMessage
from business_agent.domain.contexts import SystemCannotHandleContext
from business_agent.domain.state import DialogueState
from business_agent.handoff import control as handoff_control
from business_agent.knowledge.handler import KnowledgeHandler
from business_agent.plan.planner import TurnPlanner
from business_agent.plan.turn_plan import ClarifyReason, TurnPlan
from business_agent.plan.validator import TurnPlanValidator
from business_agent.task.commands.command import Command, SetSlotsCommand, StartFlowCommand
from business_agent.task.flows.flows import FlowList
from business_agent.task.flows.steps import CollectionFlowStep
from business_agent.task.handler import TaskHandler


# The handoff flow that already exists in user_flows.yml. It emits its own transfer notice, so
# when the handoff policy fires on the same turn it does not add a second one.
HUMAN_HANDOFF_FLOW_ID = "human_handoff"

# The "cannot handle this" flow in system_flows.yml
CANNOT_HANDLE_FLOW_ID = "system_cannot_handle"

# How many consecutive clarifications before switching to "I misread that". Two: asking once
# more is normal, but a second failure means the problem is on our side rather than in how the
# user phrased it. The third goes to a human via the handoff policy (the threshold in
# handoff/control.py) — three escalating steps.
CLARIFY_REPHRASE_THRESHOLD = 2


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

    # 3. The stop-answering gate (spec 3.3.4 tier 1: the Agent stops answering automatically
    #    while a human holds the session). Messages are still persisted — the human agent needs
    #    to see what the user said meanwhile — but they never reach planning and never call the
    #    LLM. Placing this first is deliberate: once a human takes over, not even intent
    #    recognition should run.
    if dialogue_state.is_human_controlled():
      return self._commit(user_message, dialogue_state, bot_messages=[])

    # 4. Spliting Message (text, object)
    # 4.1 Text message
    if user_message.type is MessageType.TEXT:
      bot_messages, handoff_flow_ran, handled_by_flow = await self._handle_text_message(dialogue_state)

    # 4.2 object message
    else:
      handoff_flow_ran = handled_by_flow = False
      # a) Save clicked card save to dialog state
      dialogue_state.focused_object = user_message.object
      # b) Process object message
      bot_messages = await self._handle_object_message(user_message.object,
                                                       dialogue_state,
                                                       self.task_handler.flow_list)

    # 5. Handoff decision, made after the turn is handled so the consecutive-failure counters
    #    already reflect this turn
    bot_messages = self._apply_handoff_policy(user_message, dialogue_state, bot_messages,
                                             handoff_flow_ran, handled_by_flow)

    # 6. Submit
    return self._commit(user_message, dialogue_state, bot_messages)

  def _commit(self,
              user_message: UserMessage,
              state: DialogueState,
              bot_messages: list[BotMessage]) -> ProcessedResult:
    """
    Goal: commit the turn and wrap the result. control_owner is session-level and read from
          state — it is not carried per message (appendix E.2, rule 3)
    """
    state.pending_turn.bot_messages = bot_messages
    state.commit_pending_turn()
    return ProcessedResult(
      message_id=user_message.message_id,
      messages=bot_messages,
      control_owner=state.control_owner.value,
    )

  def _apply_handoff_policy(self,
                            user_message: UserMessage,
                            state: DialogueState,
                            bot_messages: list[BotMessage],
                            handoff_flow_ran: bool,
                            handled_by_flow: bool) -> list[BotMessage]:
    """
    Goal: decide whether this turn triggers a handoff; if so switch to PENDING_HUMAN and append
          a notice when one is needed
    Args:
        user_message: the user message for this turn; card messages carry no text
        state:
        bot_messages: the replies already produced this turn
        handoff_flow_ran: whether the human_handoff flow already ran this turn. If it did,
                          transfer control straight away and add no notice — that flow already
                          says "I am transferring you to a human agent", and without this a
                          single transfer would say the same thing three times over
    Returns:
        the reply list, possibly with a notice appended
    """
    # The planner starting human_handoff is itself a decision to transfer; the keyword table
    # does not need to confirm it. This must come before evaluate(): with handled_by_flow=True,
    # evaluate suppresses the risky-topic trigger (correctly so — that suppression is what stops
    # 「你们退货政策几天」 from being hijacked into a handoff), but human_handoff is precisely
    # the one flow that should transfer control.
    #
    # The consequence of not doing this was measured: 「我要投诉你们，我要找人工」 — 「找人工」
    # is not in the keyword table (「找客服」 is), yet the planner correctly started
    # human_handoff. So the user heard "I am transferring you to a human agent" while
    # control_owner stayed AGENT: the composer never locked, nobody was paged, and the user waited
    # for an agent who was never summoned. A UI that claims success while the system did nothing
    # is worse than an outright error.
    if handoff_flow_ran:
      if state.control_owner is not handoff_control.ControlOwner.PENDING_HUMAN:
        state.request_handoff(handoff_control.HandoffTrigger.USER_REQUESTED,
                              "planner started the human_handoff flow")
      # The flow already said "I am transferring you to a human agent" — add nothing
      return bot_messages

    decision = handoff_control.evaluate(
      text=user_message.text,
      consecutive_clarify=state.consecutive_clarify,
      consecutive_knowledge_miss=state.consecutive_knowledge_miss,
      handled_by_flow=handled_by_flow,
      # Spec 3.3.4's "configured keyword matched": merchants add their own high-risk words
      # through HANDOFF_KEYWORDS
      extra_keywords=handoff_control.configured_keywords(),
    )
    if not decision.needed:
      return bot_messages

    # Already queued means no repeat notice, otherwise every further message would be answered
    # with another "transferring you now"
    already_pending = state.control_owner is handoff_control.ControlOwner.PENDING_HUMAN
    state.request_handoff(decision.trigger, decision.reason)
    if already_pending:
      return bot_messages

    notice = handoff_control.PENDING_NOTICE.get(decision.trigger)
    return [*bot_messages, BotMessage(text=notice)] if notice else bot_messages

  def _cannot_handle_reason(self, reason: ClarifyReason, state: DialogueState) -> str | None:
    """
    Goal: decide whether this turn should say "cannot handle" rather than clarify once more
    Returns:
        the branch name in system_flows.yml; None means clarify as usual
    """
    # The planner named a business flow that does not exist = we do not have that capability.
    # Asking the user to "be more specific" misleads them; no amount of precision conjures a
    # capability that is not there.
    if reason is ClarifyReason.UNKNOWN_TASK_FLOW:
      return "not_supported"

    # Clarification keeps failing, so the problem is on our side — say we misread it instead
    if state.consecutive_clarify >= CLARIFY_REPHRASE_THRESHOLD:
      return "clarification_rejected"

    return None

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


  async def _handle_text_message(self, dialogue_state: DialogueState) -> tuple[list[BotMessage], bool, bool]:
    """
    Goal: Process text message (llm analyzes routing, and plan path)
    Args: 
        dialogue_state
    Returns:
        (replies, whether human_handoff ran this turn, whether a configured flow or knowledge
         intent caught this turn)
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
      # Consecutive clarification failures signal "the Agent cannot handle this"; once they
      # reach the threshold they trigger a handoff
      dialogue_state.note_clarify(happened=True)

      # "I did not understand" and "we cannot do that" are different things: rephrasing helps
      # with the first and never helps with the second, where the honest answer is that the
      # capability does not exist. The system_cannot_handle flow was written for the second case
      # and had never once been started.
      cannot_handle_reason = self._cannot_handle_reason(validated.reason, dialogue_state)
      if cannot_handle_reason is not None:
        dialogue_state.start_system_task(SystemCannotHandleContext(
          flow_id=CANNOT_HANDLE_FLOW_ID, step_id="start", reason=cannot_handle_reason))
        return await self.task_handler.handle([], dialogue_state), False, False

      return await self.clarify_responder.respond(validated.reason, dialogue_state), False, False

    # Recognised successfully — reset the consecutive-failure counter
    dialogue_state.note_clarify(happened=False)

    # 4. validated succeed(which path? Go to path handler to execute path logic)
    if turn_plan.task is not None:
      # The type must be checked too: ResumeFlowCommand and CancelFlowCommand also carry a
      # `flow` field, so a bare getattr(command, "flow") sweeps them in as well.
      #
      # This test used to only suppress one duplicate notice, where a false positive cost
      # nothing; now it decides who owns the session, and a false positive silently rewrites
      # session state. The measured hole: the user says 「继续刚才的转人工」, the planner emits
      # ResumeFlowCommand(flow='human_handoff') — and human_handoff runs start→respond→end
      # within one turn, so it never enters paused_tasks and the resume necessarily fails. The
      # bot then replies "there is nothing in progress to pick up" while control_owner is set to
      # PENDING_HUMAN with no notice at all: the system's state directly contradicts what it
      # just said.
      handoff_flow_ran = any(
        isinstance(command, StartFlowCommand) and command.flow == HUMAN_HANDOFF_FLOW_ID
        for command in turn_plan.task.commands)
      return await self.task_handler.handle(turn_plan.task.commands, dialogue_state), handoff_flow_ran, True
    elif turn_plan.knowledge is not None:
      return await self.knowledge_handler.handle(dialogue_state,turn_plan.knowledge.intents), False, True
    elif turn_plan.chitchat is not None:
      return await self.chitchat_handler.handle(turn_plan.chitchat.chat, dialogue_state), False, False

    # 5. Directly return bot message
    return [BotMessage(text="Hi, I am your shopping assistant.")], False, False


  async def _handle_object_message(self, 
                                   object: FocusedObject, 
                                   dialogue_state: DialogueState, 
                                   flow_list: FlowList) -> list[BotMessage]:
    """
    Goal: Process object type, construct set slots command.
    """
    # 1. Try to construct SetSlotsCommand object
    command = self._try_build_set_slots_command(object, dialogue_state, flow_list)

    # 2. check command — case 3: the flow advances to its next step
    if command:
      return await self.task_handler.handle(commands=[command], dialogue_state=dialogue_state)

    if dialogue_state.active_task is not None:  # case 2: the flow runs on, re-executing the current step rather than advancing
      return await self.task_handler.handle(commands=[], dialogue_state=dialogue_state)

    # case 1: clarify
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
    Case 1: no business flow in progress -> False
    Case 2: a flow is running, but the collect step is not waiting on the card's slot -> False
    Case 3: a flow is running and is collecting exactly that slot when the card is clicked -> True
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
