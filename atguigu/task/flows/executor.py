from argparse import Action
from dataclasses import asdict

from atguigu.domain.contexts import SystemCollectInformationContext
from atguigu.domain.messages import BotMessage
from atguigu.domain.state import DialogueState
from atguigu.task.action.runner import ActionCall
from atguigu.task.flows.flows import FlowList
from atguigu.task.flows.links import FlowStepConditionLink, FlowStepFallbackLink, FlowStepStaticLink
from atguigu.task.flows.steps import ActionFlowStep, CollectionFlowStep, EndFlowStep, FlowStep, StartFlowStep


class FlowExecutor:

  async def execute_flow(self, 
                          state: DialogueState, 
                          *, 
                          action_runner, 
                          flows_list) -> list[BotMessage]:
    """
    Goal: 推进两份YAML中流程。目标: 推进业务流程[顺便推进系统流程]
    两层循环：
    外层循环: execute找到的action
    内存循环: find执行action
    特点：
    1. 两个YAML中的流程在推进期间可能出现交替
    2. 推进业务、系统流程的分界线是步骤类型为Action
    3. 遇到步骤类型是Action,都需要先停止。
    4. 步骤类型是Action, 且名字是action_response或者action_xxx的时候, 
    都需要通过action_runner找到action，执行action. 获取槽位的更新值或者
    回复响应之后，再推进流程的后续步骤.
    Args:
        dialogue_state:
        action_runner:
        flow_list:
    Returns:
    """
    final_response_messages: list[BotMessage] = []
    while True:
      # 1. 找流程步骤是Action
      action_call: ActionCall = self._advance_flow_util_action(state, flows_list)

      # 2. action名字是listen
      if action_call.action_name == "action_listen":
        break

      # 3. action名字是action_response或者action_xxx
      action_result=await action_runner.run(action_call, state)
      final_response_messages.extend(action_result.messages)
      state.set_slots(action_result.update_slots)

    return final_response_messages

  def _advance_flow_util_action(self,
                                state: DialogueState,
                                flow_list: FlowList) -> ActionCall:
    """
    Goal: 推进流程并且在推进流程期间找步骤类型是action
    如果执行流程期间步骤类型不是action，继续执行下一步流程（继续推进流程）
    如果执行流程期间步骤类型是action，不能继续推，要构建action_call, 并且返回。
    Args:
        state:
        flow_list:
    Returns:
    """
    while True:
      # 1. 
      current_task = state.current_task()

      # 2. 从上下文中流程ID（一个属性 双重身份）
      flow_id = current_task.flow_id

      # 3. 获取流程对象
      flow = flow_list.get_flow_by_flow_id(flow_id)

      # 4. 获取步骤ID
      step_id = current_task.step_id

      # 5. 获取步骤对象
      step = flow.get_step_by_step_id(step_id)

      # 6. 运行步骤
      action_call = self._run_step(step, state, flow_list)

      if action_call is not None:
        return action_call


  def _run_step(self, 
                step: FlowStep,
                state: DialogueState,
                flow_list: FlowList) -> ActionCall | None:
    """
    Goal: 运行步骤
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
    elif isinstance(self, CollectionFlowStep):
      return self._run_collection_step(step, state)
    else:
      return None

  def _run_start_step(self,
                      step: StartFlowStep,
                      state: DialogueState,
                      flow_list: FlowList) -> None:
    """
    Goal: 运行步骤类型是start, 什么都不用干, 找到下一个步骤ID, 更新到state中的流程上下文。
    Args:
        step:
        state:
        flow_list:
    Returns:
    """
    # 1. 推进下一步
    self._advance_next_step(step, state, flow_list)

    # 2. 返回None
    return None

  def _advance_next_step(self,
                         step: FlowStep,
                         state: DialogueState,
                         flow_list: FlowList):
    # 1. 找step_id
    next_step_id = self._find_next_step_id(step, state)

    # 2. 更新step_id
    state.current_task().step_id = next_step_id

  def _find_next_step_id(self,
                         step: FlowStep,
                         state: DialogueState) -> str:
    for link in step.next:
      if isinstance(link, FlowStepStaticLink):
        return link.target    # step_id
      elif isinstance(link, FlowStepConditionLink):
        # 1. 计算条件表的条件
        if self._eval_condition(link, state):
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

    return eval(condition_expr, {}, data)


  def _run_end_step(self, 
                    state: DialogueState) -> None:
    """
    Goal: 清空对应的流程上下文
    特点：不需要调用_advance_next_step方法
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
    Goal: 构建ActionCall对象返回
    特点: 需要调用_advance_next_step方法
    """
    # 1. 推进下一步
    self._advance_next_step(step, state)

    # 2. 构建ActionCall返回
    action_kwargs = step.args # dict or str
    if isinstance(action_kwargs, str):  
      # system_collect_information系统流程 args: context.response 转成字典dict
      action_kwargs = asdict(state.active_system_task)['response']

    return ActionCall(action_name=step.action, action_kwargs=action_kwargs)


  def _run_collection_step(self, 
                           step: FlowStep, 
                           state: DialogueState):
    """
    Goal: 
    """
    self._try_set_slots_from_object(step, state)

    # 第二次： 校验用户填写的槽位信息
    if state.active_task.slots.get(step.slot_name):
      if step.validated:
        if self._eval_condition(condition_expr=step.validated.condition, state=state):
          self._advance_next_step(step, state) # 推进下一步
          return None 
        else:
          # a) 清空填错的槽位信息
          state.remove_slot(step.slot_name)

          # b) 给错误响应
          if step.validated.failure_response:
            return ActionCall(action_name="action_response",
                              action_kwargs=asdict(step.validated.failure_response))
          else:
            return ActionCall(action_name="action_response",
                              action_kwargs={"text": "你填写的槽位信息有误不合法，请重新填写"})
      else:
        self._advance_next_step(step, state) # 推进下一步
        return None
    else:
      # 第一次： 让用户填写槽位信息，激活
      state.start_system_task(SystemCollectInformationContext(
          flow_id="system_collect_information",
          step_id="start",
          response=asdict(step.response),
          slot_name=step.slot_name
      ))
      return None

  def _try_set_slots_from_object(self, 
                                 step: CollectionFlowStep, 
                                 state: DialogueState):
    # 1. 判断当前业务流程以及卡片对象是否有
    if state.active_task is None or state.focused_object is None:
      return

    # 2. 卡片类型和槽位的映射
    expected_slots_mapping = {
      "order": "order_number",
      "product": "product_id",
    }
    # 3. 获取期望的槽位
    expected_slots = expected_slots_mapping.get(state.focused_object.type)

    # 4. 判断当前这一步缺失的槽位是否等于期望的槽位, 且当前业务流程上下文中槽位还没有，才利用前面点击过的卡片。
    if step.slot_name == expected_slots and not state.active_task.slots.get(step.slot_name):
      state.set_slots({step.slot_name: state.focused_object.id})


if __name__ == '__main__':
  condition_str="context.get('reason') == 'clarification_rejected'"
  data = {
    "context": {
      "reason": "clarification_rejected"
    },
    "slots": {}
  }
  print(eval(condition_str, {}, data))
