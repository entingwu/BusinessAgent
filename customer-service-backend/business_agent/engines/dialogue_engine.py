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


# user_flows.yml 里既有的转人工流程。它自己会回一句转接提示，
# 所以接管策略命中同一轮时不再重复补话
HUMAN_HANDOFF_FLOW_ID = "human_handoff"

# system_flows.yml 里的「办不了」流程
CANNOT_HANDLE_FLOW_ID = "system_cannot_handle"

# 连续澄清到第几轮改口说「我理解偏了」。取 2：第 1 次换个说法是正常的，
# 第 2 次还不行说明问题出在我这边而不是用户表述上。
# 第 3 次由接管策略转人工（handoff/control.py 的阈值），三级递进
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

    # 3. 停答门闸（规范 3.3.4 第一档：HUMAN 状态下 Agent 不再自动应答）。
    #    消息照常入库——坐席要看到用户在这期间说了什么——但不进规划、不调 LLM。
    #    放在最前面是有意的：一旦人工接管，连意图识别都不该跑
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

    # 5. 接管判定：本轮处理完再判，这样「连续失败」的计数已经反映了这一轮
    bot_messages = self._apply_handoff_policy(user_message, dialogue_state, bot_messages,
                                             handoff_flow_ran, handled_by_flow)

    # 6. Submit
    return self._commit(user_message, dialogue_state, bot_messages)

  def _commit(self,
              user_message: UserMessage,
              state: DialogueState,
              bot_messages: list[BotMessage]) -> ProcessedResult:
    """
    Goal: 落 turn 并封装返回。control_owner 是会话级的，从 state 取，
          不是每条消息各自带一份（附录 E.2 第 3 条）
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
    Goal: 判断本轮是否触发转人工；触发则切 PENDING_HUMAN 并按需追加一句提示
    Args:
        user_message: 本轮用户消息，卡片消息没有文本
        state:
        bot_messages: 本轮已经产生的回复
        handoff_flow_ran: 本轮是否已经跑过 human_handoff 流程。跑过就直接移交控制权、
                          且不再补提示——那个流程自己会说「我来为你转接人工客服」，
                          否则一次转接会连着播三句意思相同的话
    Returns:
        可能追加了提示的回复列表
    """
    # 规划器启动 human_handoff 本身就是一次移交决定，不需要再让关键词表复核一遍。
    # 这里必须先于 evaluate：evaluate 在 handled_by_flow=True 时会压掉高风险话题触发
    # （那条压制是对的，它挡住了「你们退货政策几天」被 RISKY_TOPIC 劫持成转人工），
    # 但 human_handoff 恰恰是唯一一个该移交控制权的流程。
    #
    # 不修的后果实测过：「我要投诉你们，我要找人工」——「找人工」不在关键词表里
    # （表里是「找客服」），planner 却正确地启动了 human_handoff，于是用户听到
    # 「我来为你转接人工客服」，而 control_owner 停在 AGENT：输入框不锁、没人被叫、
    # 用户在等一个从未被召唤的坐席。界面宣称成功而系统什么都没做，比直接报错更糟。
    if handoff_flow_ran:
      if state.control_owner is not handoff_control.ControlOwner.PENDING_HUMAN:
        state.request_handoff(handoff_control.HandoffTrigger.USER_REQUESTED,
                              "规划器启动了 human_handoff 流程")
      # 流程自己已经说了「我来为你转接人工客服」，不再追加提示
      return bot_messages

    decision = handoff_control.evaluate(
      text=user_message.text,
      consecutive_clarify=state.consecutive_clarify,
      consecutive_knowledge_miss=state.consecutive_knowledge_miss,
      handled_by_flow=handled_by_flow,
      # 规范 3.3.4「命中配置关键词」：商家可在 HANDOFF_KEYWORDS 里加自己的高危词
      extra_keywords=handoff_control.configured_keywords(),
    )
    if not decision.needed:
      return bot_messages

    # 已经在排队了就不再重复提示，否则用户每说一句都收到一遍「正在转接」
    already_pending = state.control_owner is handoff_control.ControlOwner.PENDING_HUMAN
    state.request_handoff(decision.trigger, decision.reason)
    if already_pending:
      return bot_messages

    notice = handoff_control.PENDING_NOTICE.get(decision.trigger)
    return [*bot_messages, BotMessage(text=notice)] if notice else bot_messages

  def _cannot_handle_reason(self, reason: ClarifyReason, state: DialogueState) -> str | None:
    """
    Goal: 判断这轮该走「办不了」而不是「再澄清一次」
    Returns:
        system_flows.yml 里的分支名；None 表示照常澄清
    """
    # 规划器点名了一个不存在的业务流程 = 这个能力我们没有。
    # 让用户「换个更具体的说法」是误导，他再具体也变不出这个能力
    if reason is ClarifyReason.UNKNOWN_TASK_FLOW:
      return "not_supported"

    # 连续澄清还不行，问题在我这边，换个说法承认理解偏了
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
        (回复列表, 本轮是否跑过 human_handoff 流程, 本轮是否被配置的流程或知识意图接住)
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
      # 连续澄清失败是「Agent 处理不了」的信号，累计到阈值触发转人工
      dialogue_state.note_clarify(happened=True)

      # 「听不懂」与「办不了」是两回事：前者让用户换个说法有用，
      # 后者换多少遍说法都没用，该直说这个能力没有。
      # system_cannot_handle 流程本来就是为后者写的，此前从未被启动过
      cannot_handle_reason = self._cannot_handle_reason(validated.reason, dialogue_state)
      if cannot_handle_reason is not None:
        dialogue_state.start_system_task(SystemCannotHandleContext(
          flow_id=CANNOT_HANDLE_FLOW_ID, step_id="start", reason=cannot_handle_reason))
        return await self.task_handler.handle([], dialogue_state), False, False

      return await self.clarify_responder.respond(validated.reason, dialogue_state), False, False

    # 识别成功，连续失败计数清零
    dialogue_state.note_clarify(happened=False)

    # 4. validated succeed(which path? Go to path handler to execute path logic)
    if turn_plan.task is not None:
      # 必须同时判类型：ResumeFlowCommand / CancelFlowCommand 也有 flow 字段，
      # 光用 getattr(command, "flow") 会把它们一起算进来。
      #
      # 这个判据从前只用来抑制一句重复提示，误判无代价；现在它决定控制权归属，
      # 误判就变成会话状态被悄悄改写。实测过的漏洞：用户说「继续刚才的转人工」，
      # 规划器给出 ResumeFlowCommand(flow='human_handoff')——而 human_handoff
      # 一轮内 start→respond→end 走完，永远不会进 paused_tasks，恢复必然失败。
      # 于是机器人回「当前没有找到可以继续的业务流程」，同时 control_owner
      # 被标成 PENDING_HUMAN 且一句提示都没有：系统状态和它自己说的话直接矛盾。
      handoff_flow_ran = any(
        isinstance(command, StartFlowCommand) and command.flow == HUMAN_HANDOFF_FLOW_ID
        for command in turn_plan.task.commands)
      return await self.task_handler.handle(turn_plan.task.commands, dialogue_state), handoff_flow_ran, True
    elif turn_plan.knowledge is not None:
      return await self.knowledge_handler.handle(dialogue_state,turn_plan.knowledge.intents), False, True
    elif turn_plan.chitchat is not None:
      return await self.chitchat_handler.handle(turn_plan.chitchat.chat, dialogue_state), False, False

    # 5. Directly return bot message
    return [BotMessage(text="你好，我是一个智能助手")], False, False


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
