from pathlib import Path

from econ_agent.chitchat.handler import ChitChatHandler
from econ_agent.chitchat.responder import ChitChatResponder
from econ_agent.clarify.responder import ClarifyResponder
from econ_agent.engines.dialogue_engine import DialogueEngine
from econ_agent.knowledge.handler import KnowledgeHandler
from econ_agent.knowledge.intents import KNOWLEDGE_INTENTS
from econ_agent.knowledge.provider.knowledge import ApiOrderProvider, ApiProductProvider, FaqDefaultProvider, RagDefaultProvider
from econ_agent.knowledge.provider.register import KnowledgeRegister
from econ_agent.knowledge.responder import KnowledgeResponder
from econ_agent.plan.planner import TurnPlanner
from econ_agent.plan.validator import TurnPlanValidator
from econ_agent.task.action.builder import build_action_runner
from econ_agent.task.commands.processor import CommandProcessor
from econ_agent.task.flows.executor import FlowExecutor
from econ_agent.task.flows.flows import FlowList
from econ_agent.task.flows.loader import FlowLoader
from econ_agent.task.handler import TaskHandler

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