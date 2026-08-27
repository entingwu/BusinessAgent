from business_agent.domain.messages import BotMessage
from business_agent.domain.state import DialogueState
from business_agent.task.action.runner import ActionRunner
from business_agent.task.commands.command import Command
from business_agent.task.commands.processor import CommandProcessor
from business_agent.task.flows.executor import FlowExecutor
from business_agent.task.flows.flows import FlowList


class TaskHandler:

  def __init__(self, 
               flow_list: FlowList, 
               command_processor: CommandProcessor,
               flow_executor: FlowExecutor,
               action_runner: ActionRunner):
    self.flow_list = flow_list
    self.command_processor = command_processor
    self.flow_executor = flow_executor
    self.action_runner = action_runner

  async def handle(self,
                   commands: list[Command],
                   dialogue_state: DialogueState) -> list[BotMessage]:
    """
    Goal: Flow Processor to process business flow
    1. Use CommandProcessor to modify state and flow task related attributes.
    2. Use FlowExecutor to read task attribute from state, to push task flow and system flow.
    Args: 
        commands:
        dialogue_state:
    Returns:
    """

    # 1. 利用命令[指令]处理器处理对应的命令[指令]
    self.command_processor.process_commands(commands, dialogue_state, self.flow_list)

    # 2. 利用流程推进器推荐流程
    bot_messages = await self.flow_executor.execute_flow(
      dialogue_state, 
      action_runner=self.action_runner, 
      flow_list=self.flow_list)

    # 3. 返回机器人回复的消息
    return bot_messages