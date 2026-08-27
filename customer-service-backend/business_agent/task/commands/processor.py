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
    Goal: 开启业务流程. 代码逻辑（激活）更新业务流程上下文以及（激活）系统流程上下文
    """
    # 1. 获取当前要开启的业务流程ID
    start_flow_id = command.flow

    # 2. 获取当前要开启的业务流程名字
    start_flow_name = flow_list.get_flow_by_flow_id(start_flow_id).name

    # 3. 获取当前正在执行业务流程上下文
    activate_task = state.active_task

    # 4. 当前正在执行的业务流程存在
    if activate_task is not None:
      # a) 当前正在执行业务流程的流程ID是等于要开启的业务流程的流程ID
      #    不用激活业务流程和系统流程
      if activate_task.flow_id == start_flow_id:
        return # 保持当前状态即可

      # b) 从挂起栈中移除要开启的业务流程的流程ID
      state.remove_paused_task(start_flow_id)
      interrupted_flow_id = state.active_task.flow_id
      interrupted_flow_name = flow_list.get_flow_by_flow_id(interrupted_flow_id).name

      # c) 中断当前正在执行的业务流程
      state.interrupt_activate_task()

      # d) 激活业务流程以及中断系统流程
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
    # 5. 当前不存在正在执行的业务流程
      # a) 从栈中移除要开启的业务流程的流程ID（有就移除，没有就不管）
      state.remove_paused_task(start_flow_id)

      # b) 激活业务流程以及中断系统流程
      state.start_task(TaskContext(
        flow_id=start_flow_id,
        step_id="start"
      ))

      # c) 激活开始流程
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
    Goal: 给业务流程缺失的槽位补全信息。 代码逻辑: 修改状态
    修改state中activated_task的slots属性[将传入过来的槽位信息[槽位名:槽位值] 放到业务流程的slots中]

    写入前先过两道关。槽位值来自 LLM 抽取，而 LLM 会把上一轮的实体顺手填进
    这一轮的槽位——实测「查订单 o30002 的物流」之后紧接着问「看看类似的商品推荐」，
    订单号 o30002 会被填进 product_id，推荐流程于是拿着一个订单号去查商品。
    提示词里写「禁止臆造」只是口头约束，拦不住，所以在写入状态这一步挡：

    1. 槽位名必须属于当前流程声明的槽位（Flow.slots 由 collect 步骤推导而来）；
    2. 槽位若配了 pattern，值必须匹配，否则丢弃。

    丢弃而不是整轮拒绝：用户的意图（start_flow）本身是对的，错的只是被顺手带上的
    槽位值。丢掉之后 collect 步骤会正常向用户要，这比让整轮走澄清体验好。
    丢弃一律留日志，不静默。
    Args:
        command: 待写入的槽位命令
        state: 当前对话状态
        flow_list: 用于取出当前流程的槽位声明
    """
    task_context = state.active_task
    flow = flow_list.get_flow_by_flow_id(task_context.flow_id) if task_context is not None else None
    accepted = accept_slots(flow, command.slots, source="set_slots 命令")
    if accepted:
      state.set_slots(accepted)


  def _resumed_flow(self, 
                    command: ResumeFlowCommand, 
                    state: DialogueState, 
                    flow_list: FlowList):
    """
    Goal: 恢复业务流程
    Args:
        command
        state
        flow_list
    """
    # 1. 获取要恢复的业务流程的流程ID（不一定有，如果在恢复的时候没有明确的恢复目标, 那么flow是None）
    resumed_flow_id = command.flow

    # 2. 获取当前正在执行的业务流程上下文
    activate_task = state.active_task

    # 3. 当前正在执行的业务流程存在
    if activate_task is not None:
      # 3.1 判断要恢复的业务流程的流程ID是否为空
      if resumed_flow_id is None:
        return # 保持当前状态
      # 3.2 判断是否和当前正在执行的业务流程一样
      if resumed_flow_id == activate_task.flow_id:
        return # 保持当前状态
      interrupt_flow_id = activate_task.flow_id
      interrupt_flow_name = flow_list.get_flow_by_flow_id(interrupt_flow_id).name
      
      # 3.3 中断当前正在执行的业务流程
      state.interrupt_activate_task()

      # 3.4 从挂起业务流程上下文的栈中恢复
      resumed = state.resume_task(resumed_flow_id)

      # 3.5 没有恢复成功
      if not resumed:
        # a) 回滚。 把刚刚压入到栈中的当前执行的业务流程上下文恢复出来
        state.resume_task()

        # b) 激活恢复失败的系统流程
        state.start_system_task(SystemTaskResumeFailedContext(
          flow_id="system_task_resume_failed",
          step_id="start",
        ))
      else:
        # c) 激活中断系统流程
        state.start_system_task(SystemTaskInterruptedContext(
          flow_id="system_task_interrupted",
          step_id="start",
          interrupted_flow_id=interrupt_flow_id,
          interrupted_flow_name=interrupt_flow_name,
          started_flow_id=state.active_task.flow_id,
          started_flow_name=flow_list.get_flow_by_flow_id(state.active_task.flow_id).name,
        ))
    else:
    # 4.当前不存在正在执行的业务流程
      # a) 恢复指定的业务流程
      resumed = state.resume_task(flow_id=resumed_flow_id)
      # b) 恢复失败
      if not resumed:
        state.start_system_task(SystemTaskResumeFailedContext(
            flow_id="system_task_resume_failed",
            step_id="start"
        ))
        return
      resumed_flow_id = state.active_task.flow_id
      resumed_flow_name = flow_list.get_flow_by_flow_id(resumed_flow_id).name
        
      # c) 恢复成功
      state.start_system_task(SystemTaskResumedContext(
          flow_id="system_task_resumed",
          step_id="start",
          resumed_flow_id=resumed_flow_id,
          resumed_flow_name=resumed_flow_name
      ))

  def _cancel_flow(self, 
                   state: DialogueState, 
                   flow_list: FlowList):
    # 1.获取当前系统中正在执行的业务流程
    activated_task = state.active_task

    # 1.1 当前没有正在办理的业务流程，没有东西可以取消。
    #     激活取消失败的系统流程，给用户一句明确回复，避免返回空消息。
    if activated_task is None:
      state.cancel_active_task()
      state.start_system_task(SystemTaskCancelFailedContext(
        flow_id="system_task_cancel_failed",
        step_id="start",
      ))
      return

    activated_flow_id = activated_task.flow_id

    # 2.修改state中的activated_task和activated_system_task [None]
    state.cancel_active_task()

    # 3.激活 system_task_canceled: 精准，为了让用户看到 “好的, xxx 业务流程，先帮你取消”开场白
    state.start_system_task(SystemTaskCanceledContext(
      flow_id="system_task_canceled",
      step_id="start",
      canceled_flow_id=activated_flow_id,
      canceled_flow_name=flow_list.get_flow_by_flow_id(activated_flow_id).name,
    ))