from business_agent.domain.contexts import SystemTaskCancelFailedContext, SystemTaskCanceledContext, SystemTaskInterruptedContext, SystemTaskResumeFailedContext, SystemTaskResumedContext, SystemTaskStartedContext, TaskContext
from business_agent.domain.state import DialogueState
from business_agent.task.commands.command import CancelFlowCommand, Command, ResumeFlowCommand, SetSlotsCommand, StartFlowCommand
from business_agent.task.flows.flows import FlowList
from business_agent.task.flows.slot_guard import accept_slots


class CommandProcessor:

  def process_commands(self,
                       commands: list[Command],
                       dialogue_state: DialogueState,
                       flow_list: FlowList):
    """
    Goal: Handle 4 specific command
    Args:
        commands:
        dialogue_state:
        flow_list
    Returns:
    """

    for command in commands:
      if isinstance(command, StartFlowCommand):
        self._start_flow(command, dialogue_state, flow_list)
      elif isinstance(command, SetSlotsCommand):
        self._update_slots(command, dialogue_state, flow_list)
      elif isinstance(command, ResumeFlowCommand):
        self._resumed_flow(command, dialogue_state, flow_list)
      elif isinstance(command, CancelFlowCommand):
        self._cancel_flow(dialogue_state, flow_list)
      else:
         pass

  def _start_flow(self, 
                  command: StartFlowCommand, 
                  state: DialogueState, 
                  flow_list: FlowList):
    """
    Goal: start a business flow — activate/update the business task context and the system flow context
    """
    # 1. The flow id being started
    start_flow_id = command.flow

    # 2. Its display name
    start_flow_name = flow_list.get_flow_by_flow_id(start_flow_id).name

    # 3. The currently running business task context
    activate_task = state.active_task

    # 4. A flow is already running
    if activate_task is not None:
      # a) The running flow is the one being started — no need to activate anything
      if activate_task.flow_id == start_flow_id:
        return  # keep the current state as it is

      # b) Drop that flow id from the paused stack
      state.remove_paused_task(start_flow_id)
      interrupted_flow_id = state.active_task.flow_id
      interrupted_flow_name = flow_list.get_flow_by_flow_id(interrupted_flow_id).name

      # c) Interrupt the flow that is running
      state.interrupt_activate_task()

      # d) Activate the new flow and the 'interrupted' system flow
      state.start_task(TaskContext(
        flow_id=start_flow_id,
        step_id="start"
      ))

      state.start_system_task(SystemTaskInterruptedContext(
        flow_id="system_task_interrupted",
        step_id="start",
        interrupted_flow_id=interrupted_flow_id,
        interrupted_flow_name=interrupted_flow_name,
        started_flow_id=start_flow_id,
        started_flow_name=start_flow_name,
      ))
    else:
    # 5. Nothing is running right now
      # a) Drop that flow id from the stack if present; ignore it if not
      state.remove_paused_task(start_flow_id)

      # b) Activate the flow and the 'started' system flow
      state.start_task(TaskContext(
        flow_id=start_flow_id,
        step_id="start"
      ))

      # c) Activate the start step
      state.start_system_task(SystemTaskStartedContext(
        flow_id="system_task_started",
        step_id="start",
        started_flow_id=start_flow_id,
        started_flow_name=start_flow_name,
      ))


  def _update_slots(self, 
                    command: SetSlotsCommand, 
                    state: DialogueState,
                    flow_list: FlowList):
    """
    Goal: fill in the slots a business flow is missing — mutates state by writing the incoming
    {slot name: slot value} pairs into the active task's slots.

    Two guards run before the write. Slot values come from LLM extraction, and the LLM will
    happily carry an entity from the previous turn into this one — measured: right after
    「查订单 o30002 的物流」 the user asks 「看看类似的商品推荐」, and the order number o30002
    lands in product_id, so the recommendation flow goes looking for a product by order number.
    Writing "do not fabricate" into the prompt is a verbal constraint and does not stop it, so
    the block happens here, at the point of writing to state:

    1. the slot name must be one the current flow declares (Flow.slots is derived from its
       collect steps);
    2. if the slot declares a pattern, the value must match it, or it is dropped.

    Dropping rather than rejecting the whole turn: the user's intent (start_flow) was right, only
    the slot value that got swept along was wrong. Once dropped, the collect step asks the user
    for it normally, which is a better experience than sending the whole turn to clarification.
    Every drop is logged — never silent.
    Args:
        command: the slot command about to be written
        state: the current dialogue state
        flow_list: used to look up the current flow's slot declarations
    """
    task_context = state.active_task
    flow = flow_list.get_flow_by_flow_id(task_context.flow_id) if task_context is not None else None
    accepted = accept_slots(flow, command.slots, source="set_slots command")
    if accepted:
      state.set_slots(accepted)


  def _resumed_flow(self, 
                    command: ResumeFlowCommand, 
                    state: DialogueState, 
                    flow_list: FlowList):
    """
    Goal: resume a business flow
    Args:
        command
        state
        flow_list
    """
    # 1. The flow id to resume. Optional — with no explicit target, flow is None
    resumed_flow_id = command.flow

    # 2. The currently running business task context
    activate_task = state.active_task

    # 3. A flow is already running
    if activate_task is not None:
      # 3.1 No resume target given
      if resumed_flow_id is None:
        return  # keep the current state
      # 3.2 Same flow as the one already running
      if resumed_flow_id == activate_task.flow_id:
        return  # keep the current state
      interrupt_flow_id = activate_task.flow_id
      interrupt_flow_name = flow_list.get_flow_by_flow_id(interrupt_flow_id).name
      
      # 3.3 Interrupt the flow that is running
      state.interrupt_activate_task()

      # 3.4 Restore from the paused-task stack
      resumed = state.resume_task(resumed_flow_id)

      # 3.5 The resume failed
      if not resumed:
        # a) Roll back — pull the context we just pushed back out of the stack
        state.resume_task()

        # b) Activate the 'resume failed' system flow
        state.start_system_task(SystemTaskResumeFailedContext(
          flow_id="system_task_resume_failed",
          step_id="start",
        ))
      else:
        # c) Activate the 'interrupted' system flow
        state.start_system_task(SystemTaskInterruptedContext(
          flow_id="system_task_interrupted",
          step_id="start",
          interrupted_flow_id=interrupt_flow_id,
          interrupted_flow_name=interrupt_flow_name,
          started_flow_id=state.active_task.flow_id,
          started_flow_name=flow_list.get_flow_by_flow_id(state.active_task.flow_id).name,
        ))
    else:
    # 4. Nothing is running right now
      # a) Resume the requested flow
      resumed = state.resume_task(flow_id=resumed_flow_id)
      # b) The resume failed
      if not resumed:
        state.start_system_task(SystemTaskResumeFailedContext(
            flow_id="system_task_resume_failed",
            step_id="start"
        ))
        return
      resumed_flow_id = state.active_task.flow_id
      resumed_flow_name = flow_list.get_flow_by_flow_id(resumed_flow_id).name
        
      # c) The resume succeeded
      state.start_system_task(SystemTaskResumedContext(
          flow_id="system_task_resumed",
          step_id="start",
          resumed_flow_id=resumed_flow_id,
          resumed_flow_name=resumed_flow_name
      ))

  def _cancel_flow(self, 
                   state: DialogueState, 
                   flow_list: FlowList):
    # 1. The business flow currently running
    activated_task = state.active_task

    # 1.1 Nothing in progress, so there is nothing to cancel. Activate the 'cancel failed'
    #     system flow so the user gets a clear reply instead of an empty message.
    if activated_task is None:
      state.cancel_active_task()
      state.start_system_task(SystemTaskCancelFailedContext(
        flow_id="system_task_cancel_failed",
        step_id="start",
      ))
      return

    activated_flow_id = activated_task.flow_id

    # 2. Clear activated_task and activated_system_task on state
    state.cancel_active_task()

    # 3. Activate system_task_canceled so the user gets the explicit "Done — I have cancelled X"
    #    opening line
    state.start_system_task(SystemTaskCanceledContext(
      flow_id="system_task_canceled",
      step_id="start",
      canceled_flow_id=activated_flow_id,
      canceled_flow_name=flow_list.get_flow_by_flow_id(activated_flow_id).name,
    ))