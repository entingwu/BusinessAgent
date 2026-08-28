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

    # 1. Which tracks the routing produced
    activate_tracks = turn_plan.activated_tracks()

    # 2. No track matched
    if not activate_tracks:
      return self._reject(ClarifyReason.MISSING_TRACK)

    # 3. More than one track matched
    if len(activate_tracks) > 1:
      return self._reject(ClarifyReason.MULTIPLE_TRACKS)

    # 4. Exactly one track matched
    selectd_tracks = activate_tracks[0]

    # 4.1 Validate the task track
    if selectd_tracks == "task":
      return self._validate_task_track(turn_plan.task, flow_list)

    # 4.2 Validate the knowledge track
    if selectd_tracks == "knowledge":
      return self._validate_knowldge_track(turn_plan.knowledge, dialogue_state, knowledge_intents)

    # 4.3 The chitchat track is not validated
    return TurnPlanValidatedResult(valid=True)


  def _reject(self, reason: ClarifyReason) -> TurnPlanValidatedResult:
    # The rejection path used to produce no log line at all: a successful plan was recorded and
    # a rejected one was silent. Yet silent failure is the only trace that the class of mistake
    # described above — adding a command and forgetting the whitelist — ever leaves.
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
    # This branch **can never fire today**: allowed_commands and COMMAND_TO_CLASS hold the same
    # set of classes, so a legal command is necessarily one of the four, and an illegal one blows
    # up with KeyError inside Command.from_dict long before reaching here.
    #
    # It is kept because it is the only type gate there is. But be clear that it **is not an
    # alarm**: if someone adds a fifth command to COMMAND_TO_CLASS and forgets to add it here,
    # the path taken is reject -> clarification fallback -> enough consecutive failures to hand
    # off to a human, and nothing raises anywhere along it.
    # What it looks like from outside is "the bot suddenly got dumb and escalated", not an
    # explicit failure at any one point.
    # The warning in _reject below is currently this path's only signal — do not remove it.
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

    # Only the knowledge intents this turn's routing produced are validated; iterating the whole
    # registry would let product_info / order_info's card requirement block every knowledge
    # question.
    for llm_intent in knowledge.intents:
      knowledge_object = knowledge_intents.get(llm_intent)

      # The LLM can name an intent id that is not in the registry; treat that as "intent not
      # recognised"
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