import logging

from business_agent.domain.state import DialogueState
from business_agent.knowledge.intents import KnowledgeIntent
from business_agent.plan.turn_plan import ClarifyReason, KnowledgeTurnPlan, TaskTurnPlan, TurnPlan, TurnPlanValidatedResult
from business_agent.task.commands.command import CancelFlowCommand, Command, ResumeFlowCommand, SetSlotsCommand, StartFlowCommand
from business_agent.task.flows.flows import FlowList


logger = logging.getLogger(__name__)


class TurnPlanValidator:
  def validate(self,
              turn_plan: TurnPlan, 
              dialogue_state: DialogueState,
              flow_list: FlowList,
              knowledge_intents: KnowledgeIntent):
    """
    Goal: Evaluate the Turn Plan result.
    1. Evaluate numbers of paths. (outside)
    2. Evaluate path inside
    Args:
        turn_plan:
        dialogue_state:
        flow_list:
        knowledge_intents:
    """

    # 1. 获取路由后的轨道情况
    activate_tracks = turn_plan.activated_tracks()

    # 2. 是否未命中轨道
    if not activate_tracks:
      return self._reject(ClarifyReason.MISSING_TRACK)

    # 3. 是否命中多条轨道
    if len(activate_tracks) > 1:
      return self._reject(ClarifyReason.MULTIPLE_TRACKS)

    # 4. 命中唯一的轨道
    selectd_tracks = activate_tracks[0]

    # 4.1 进入到task轨道校验
    if selectd_tracks == "task":
      return self._validate_task_track(turn_plan.task, flow_list)

    # 4.2 进入到knowledge轨道校验
    if selectd_tracks == "knowledge":
      return self._validate_knowldge_track(turn_plan.knowledge, dialogue_state, knowledge_intents)

    # 4.3 闲聊轨道不校验
    return TurnPlanValidatedResult(valid=True)


  def _reject(self, reason: ClarifyReason) -> TurnPlanValidatedResult:
    # 规划被判非法这条路径此前一行日志都没有：规划成功记了，被拒反而静默。
    # 而「静默失效」正是上面那类「加了新命令忘了加白名单」的错误唯一会留下的痕迹
    logger.warning("turn_plan_rejected reason=%s", reason.value)
    return TurnPlanValidatedResult(valid=False, reason=reason)

  def _validate_task_track(self, 
                           task: TaskTurnPlan, 
                           flow_list: FlowList) -> TurnPlanValidatedResult:
    """
    Goal: Evaluate task track
    1. whether task track has corresponding Command
    2. whether command is legit
    3. whether has multiple start commands
    4. whether has flow process
    Args:
        task:
        dialogue_state:
        flow_list:
    Returns
    """
    # 1. task track has corresponding commands
    if not task.commands:
      return self._reject(ClarifyReason.MISSING_TASK_COMMANDS)

    # 2. whether command is legit
    #
    # 这一条当前**永远不会命中**：allowed_commands 与 COMMAND_TO_CLASS 是同一批类，
    # 合法命令必然是四者之一，非法的在 Command.from_dict 就 KeyError 炸了、到不了这里。
    #
    # 保留它是因为它是唯一的类型闸门。但要说清楚它**不是报警**：真有人给
    # COMMAND_TO_CLASS 加了第五种命令却忘了加进这里，走的是
    # reject → 澄清兜底话术 → 连续失败攒够转人工，全程不抛错。
    # 表现是「机器人忽然变笨然后转人工」，而不是任何一处显式失败。
    # 下面 _reject 里那行 warning 是这条路径目前唯一的信号，别删。
    allowed_commands = (StartFlowCommand, SetSlotsCommand, CancelFlowCommand, ResumeFlowCommand)
    if not all(isinstance(command, allowed_commands) for command in task.commands):
      return self._reject(ClarifyReason.INVALID_TASK_COMMANDS)

    # 3. whether has multiple start commands
    start_command = [command for command in task.commands if isinstance(command, StartFlowCommand)]
    if len(start_command) > 1:
      return self._reject(ClarifyReason.MULTIPLE_TASK_FLOWS)

    if start_command:
      flow_id = start_command[0].flow
      flow = flow_list.get_flow_by_flow_id(flow_id)
      if flow is None:
        return self._reject(ClarifyReason.UNKNOWN_TASK_FLOW)

    # 4. Pass (set slots, resume, cancel)
    return TurnPlanValidatedResult(valid=True)


  def _validate_knowldge_track(self, 
                               knowledge: KnowledgeTurnPlan, 
                               dialogue_state: DialogueState, 
                               knowledge_intents: dict[str, KnowledgeIntent]) -> TurnPlanValidatedResult:
    """
    Goal: Evaluate Knowledge Track
    Evaluate api.order/api.product provider intent object has filled in card object
    Restriction: Only retrieve label through click card, do not support extract object in input using natural language.[llm extract]
    Args:
        knowledge:
        dialogue_state:
        knowledge_intents:
    Returns:
    """
    # Can analyze knowledge intent
    if not knowledge.intents:
      return self._reject(ClarifyReason.MISSING_KNOWLEDGE_INTENT)

    # 只校验本轮LLM路由出来的知识意图，不能遍历全量注册表，
    # 否则任何知识提问都会被 product_info/order_info 的卡片要求拦下来
    for llm_intent in knowledge.intents:
      knowledge_object = knowledge_intents.get(llm_intent)

      # LLM 可能给出注册表里不存在的意图ID，此时按"识别不出意图"处理
      if knowledge_object is None:
        return self._reject(ClarifyReason.MISSING_KNOWLEDGE_INTENT)

      require_type = knowledge_object.requires_object_type

      focused_object = dialogue_state.focused_object
      if require_type is not None:
        if focused_object is None or focused_object.type != require_type:
          return self._reject(ClarifyReason.MISSING_FOCUSED_OBJECT)

    return TurnPlanValidatedResult(valid=True)


if __name__ == '__main__':
    allowed_commands = (StartFlowCommand, SetSlotsCommand, CancelFlowCommand, ResumeFlowCommand)