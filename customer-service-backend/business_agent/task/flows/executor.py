import logging
from dataclasses import asdict

from business_agent.domain.contexts import SystemCollectInformationContext
from business_agent.domain.messages import BotMessage
from business_agent.domain.state import DialogueState
from business_agent.task.action.runner import ActionCall
from business_agent.task.flows.flows import FlowList
from business_agent.task.flows.links import FlowStepConditionLink, FlowStepFallbackLink, FlowStepStaticLink
from business_agent.task.flows.slot_guard import accept_slots
from business_agent.task.flows.steps import ActionFlowStep, CollectionFlowStep, EndFlowStep, FlowStep, StartFlowStep

logger = logging.getLogger(__name__)


class FlowExecutor:

  async def execute_flow(self, 
                          state: DialogueState, 
                          *, 
                          action_runner, 
                          flow_list) -> list[BotMessage]:
    """
    Goal: advance the flows defined across both YAML files — the business flow, and the system
    flow alongside it.

    Two nested loops: the outer one executes the action that was found, the inner one walks the
    flow to find the next action.

    Notes:
    1. The business flow and the system flow may alternate while advancing.
    2. The boundary between advancing them is a step of type Action.
    3. Every Action step forces a stop first.
    4. For an Action step named action_response or action_xxx, action_runner resolves and runs
       the action; only after collecting its slot updates or its reply does the flow advance to
       the following steps.
    Args:
        dialogue_state:
        action_runner:
        flow_list:
    Returns:
    """
    final_response_messages: list[BotMessage] = []
    while True:
      # 1. Walk to the next Action step
      action_call: ActionCall = self._advance_flow_util_action(state, flow_list)

      # 2. The action is action_listen
      if action_call.action_name == "action_listen":
        break

      # 3. The action is action_response or action_xxx
      action_result=await action_runner.run(action_call, state)
      final_response_messages.extend(action_result.messages)
      state.set_slots(action_result.updated_slots)

    return final_response_messages

  def _advance_flow_util_action(self,
                                state: DialogueState,
                                flow_list: FlowList) -> ActionCall:
    """
    Goal: advance the flow, stopping at the next step of type action.
    A non-action step is executed and the walk continues to the following step.
    An action step stops the walk: build an ActionCall and return it.
    Args:
        state:
        flow_list:
    Returns:
    """
    while True:
      # 1. The context of the flow being advanced
      current_task = state.current_task()

      # 1.1 Both the business flow and the system flow have ended — stop and wait for the
      #     user's next message
      if current_task is None:
        return ActionCall(action_name="action_listen")

      # 2. The flow id from the context (one attribute, serving both flow kinds)
      flow_id = current_task.flow_id

      # 3. The flow object
      flow = flow_list.get_flow_by_flow_id(flow_id)

      # 4. The step id
      step_id = current_task.step_id

      # 5. The step object
      step = flow.get_step_by_step_id(step_id)

      # 6. Run the step
      action_call = self._run_step(step, state, flow_list)

      if action_call is not None:
        return action_call


  def _run_step(self, 
                step: FlowStep,
                state: DialogueState,
                flow_list: FlowList) -> ActionCall | None:
    """
    Goal: run one step
    Args:
        step:
        state:
        flow_list:
    Returns:
    """
    if isinstance(step, StartFlowStep):
      return self._run_start_step(step, state, flow_list)
    elif isinstance(step, EndFlowStep):
      return self._run_end_step(state)
    elif isinstance(step, ActionFlowStep):
      return self._run_action_step(step, state)
    elif isinstance(step, CollectionFlowStep):
      return self._run_collection_step(step, state, flow_list)
    else:
      return None

  def _run_start_step(self,
                      step: StartFlowStep,
                      state: DialogueState,
                      flow_list: FlowList) -> None:
    """
    Goal: run a start step — nothing to do but resolve the next step id and write it into the
          flow context on state.
    Args:
        step:
        state:
        flow_list:
    Returns:
    """
    # 1. Advance to the next step
    self._advance_next_step(step, state)

    # 2. Return None
    return None

  def _advance_next_step(self,
                         step: FlowStep,
                         state: DialogueState):
    # 1. Resolve the next step_id
    next_step_id = self._find_next_step_id(step, state)

    # 2. Write the step_id back
    state.current_task().step_id = next_step_id

  def _find_next_step_id(self,
                         step: FlowStep,
                         state: DialogueState) -> str:
    for link in step.next:
      if isinstance(link, FlowStepStaticLink):
        return link.target    # step_id
      elif isinstance(link, FlowStepConditionLink):
        # 1. Evaluate the edge's condition
        if self._eval_condition(link.condition, state):
          return link.target  # step_id
      elif isinstance(link, FlowStepFallbackLink):
        return link.target    # step_id

    return ""

  def _eval_condition(self,
                      condition_expr: str, 
                      state: DialogueState) -> bool:
    """
    condition_expr="context.get('reason) == 'clarification_rejected'"
    Args:
        condition:
        state:
    Returns:
    """
    data = {
      "context": asdict(state.active_system_task) if state.active_system_task is not None else None,
      "slots": state.active_task.slots if state.active_task is not None else {},
    }

    # Condition expressions come from YAML: they are configuration, not code, and a typo in one
    # should not hand the user a 500. This guard went in together with product_recommendation's
    # `int(slots.get('product_round'))` — the first condition in the repo that coerces a type, so
    # a slot value that is not a digit string raises ValueError. Today the only writer is
    # _next_round (which only ever produces digit strings) and the slot whitelist stops the LLM
    # from writing that slot, so it cannot fire; but that dependency is invisible from the YAML
    # and should not be what holds this up.
    #
    # A failed evaluation is treated as "condition not met", i.e. the else branch. That is the
    # conservative direction: taking the wrong branch costs the flow one extra round, whereas
    # raising cuts the whole conversation off.
    # The WARNING is mandatory — a silent False would let a broken condition go unnoticed forever.
    try:
      return bool(eval(condition_expr, {}, data))
    except Exception as error:
      logger.warning("flow_condition_eval_failed expr=%s error=%r", condition_expr, error)
      return False


  def _run_end_step(self, 
                    state: DialogueState) -> None:
    """
    Goal: clear the corresponding flow context. Note that _advance_next_step is not called here.
    """
    if state.active_system_task is not None:
      state.end_system_task()
    elif state.active_task is not None:
      state.end_active_task()
    else: 
      pass
    return None

  def _run_action_step(self,
                       step: ActionFlowStep,  
                       state: ActionFlowStep) -> ActionCall:
    """
    Goal: build and return an ActionCall. Note that _advance_next_step **is** called here.
    """
    # 1. Advance to the next step
    self._advance_next_step(step, state)

    # 2. Build the ActionCall and return it
    action_kwargs = step.args # dict or str
    if isinstance(action_kwargs, str):  
      # For the system_collect_information flow, args is `context.response` — turn it into a dict
      action_kwargs = asdict(state.active_system_task)['response']

    return ActionCall(action_name=step.action, action_kwargs=action_kwargs)


  def _run_collection_step(self, 
                           step: CollectionFlowStep, 
                           state: DialogueState,
                           flow_list: FlowList) -> ActionCall | None:
    """
    Goal: ask the user for the slot values a business flow is missing.

    Note 1: collect steps only ever appear in user_flows.yml, never in system_flows.yml —
    collecting slots belongs to the business side by definition.

    Note 2: this method runs twice per slot, so that what the user typed can be validated. That
    is what the `validated` switch in the config file is for.
    1. Ask the user for the value (first call) — returns None; the inner loop keeps running the
       current task but must not advance (_advance_next_step is not called).
    2. Validate what the user supplied (second call), which either passes or fails.
       Passes: advance via _advance_next_step and return None.
       Fails: ask again — drop the bad slot value, build the error response, return an ActionCall.
    Args:
        steps:
        state:
    Returns
    """
    self._try_set_slots_from_object(step, state, flow_list)

    if state.active_task.slots.get(step.slot_name):
      # Second call: validate what the user supplied
      if step.validated:
        # A validation rule is configured
        if self._eval_condition(condition_expr=step.validated.condition, state=state):
          self._advance_next_step(step, state)  # advance
          return None 
        else:
          # a) Drop the invalid slot value
          state.remove_slot(step.slot_name)

          # b) Return the error response
          if step.validated.failure_response:
            return ActionCall(action_name="action_response",
                              action_kwargs=asdict(step.validated.failure_response))
          else:
            return ActionCall(action_name="action_response",
                              action_kwargs={"text": "That does not look like a valid value — could you enter it again?"})
      else:
        self._advance_next_step(step, state)  # advance
        return None
    else:
      # First call: ask the user for the value by activating system_collect_information
      state.start_system_task(SystemCollectInformationContext(
          flow_id="system_collect_information",
          step_id="start",
          response=asdict(step.response),
          slot_name=step.slot_name
      ))
      return None

  def _try_set_slots_from_object(self, 
                                 step: CollectionFlowStep, 
                                 state: DialogueState,
                                 flow_list: FlowList):
    # 1. Is there both a running flow and a focused card?
    if state.active_task is None or state.focused_object is None:
      return

    # 2. Card type -> slot mapping
    expected_slots_mapping = {
      "order": "order_number",
      "product": "product_id",
    }
    # 3. The slot this card type can fill
    expected_slots = expected_slots_mapping.get(state.focused_object.type)

    # 4. Only reuse the previously clicked card when the slot this step is waiting on is exactly
    #    the one that card can fill, and the flow context does not already hold a value for it.
    if step.slot_name == expected_slots and not state.active_task.slots.get(step.slot_name):
      # An id arriving from a card goes through the slot guard as well: a card can carry an id
      # that does not match its own type, and this path bypasses CommandProcessor entirely — it
      # is the second entry point for slot writes, so skipping it here would leave the hole open.
      flow = flow_list.get_flow_by_flow_id(state.active_task.flow_id)
      accepted = accept_slots(flow, {step.slot_name: state.focused_object.id}, source="card backfill")
      if accepted:
        state.set_slots(accepted)


if __name__ == '__main__':
  condition_str="context.get('reason') == 'clarification_rejected'"
  data = {
    "context": {
      "reason": "clarification_rejected"
    },
    "slots": {}
  }
  print(eval(condition_str, {}, data))
