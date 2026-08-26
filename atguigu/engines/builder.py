from pathlib import Path

from atguigu.chitchat.handler import ChitChatHandler
from atguigu.chitchat.responder import ChitChatResponder
from atguigu.clarify.responder import ClarifyResponder
from atguigu.engines.dialogue_engine import DialogueEngine
from atguigu.knowledge.handler import KnowledgeHandler
from atguigu.knowledge.intents import KNOWLEDGE_INTENTS
from atguigu.knowledge.provider.knowledge import ApiOrderProvider, ApiProductProvider, FaqDefaultProvider, RagDefaultProvider
from atguigu.knowledge.provider.register import KnowledgeRegister
from atguigu.knowledge.responder import KnowledgeResponder
from atguigu.plan.planner import TurnPlanner
from atguigu.plan.validator import TurnPlanValidator
from atguigu.task.action.builder import build_action_runner
from atguigu.task.commands.processor import CommandProcessor
from atguigu.task.flows.executor import FlowExecutor
from atguigu.task.flows.flows import FlowList
from atguigu.task.flows.loader import FlowLoader
from atguigu.task.handler import TaskHandler

PROJECT_ROOT_DIR = Path(__file__).resolve().parents[2]

FLOW_CONFIG_DIR = PROJECT_ROOT_DIR / "flow_config"


def build_dialogue_engine():
  # 1. Load Flow
  flow_list = FlowLoader().load_multi_yamls([FLOW_CONFIG_DIR / yaml for yaml in ("system_flows.yml", "user_flows.yml")])

  return DialogueEngine(
    turn_planner=TurnPlanner(),
    turn_plan_validator=TurnPlanValidator(),
    clarify_responder=ClarifyResponder(),
    task_handler=TaskHandler(
      flow_list=flow_list,
      command_processor=CommandProcessor(),
      flow_executor=FlowExecutor(),
      action_runner=build_action_runner()
    ),
    knowledge_handler=KnowledgeHandler(
      knowledge_intents=KNOWLEDGE_INTENTS,
      knowledge_register=KnowledgeRegister(providers=[
        ApiOrderProvider(),
        ApiProductProvider(),
        RagDefaultProvider(),
        FaqDefaultProvider(),
      ]),
      knowledge_responder=KnowledgeResponder()
    ),
    chitchat_handler=ChitChatHandler(
      chat_responder=ChitChatResponder()),
  )