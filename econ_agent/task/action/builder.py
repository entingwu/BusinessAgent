import importlib
import inspect
import pkgutil

from econ_agent.task.action.base import Action
from econ_agent.task.action.builtin.listener import ActionListener
from econ_agent.task.action.builtin.response import ActionResponse
from econ_agent.task.action.register import ActionRegister
from econ_agent.task.action.runner import ActionRunner

def register_builtin_action(action_runner: ActionRunner):
  """
  Goal: 注册到runner中的注册中心
  """
  action_runner.action_register.registry_action(ActionResponse())
  action_runner.action_register.registry_action(ActionListener())

def register_customer_action(action_runner: ActionRunner):
  """
  Goal: 将自定义的三个action注册到runner中的注册中心
  """
  package = importlib.import_module("econ_agent.task.action.customer")
  for _, module_name, is_pkg in pkgutil.iter_modules(path=package.__path__, 
                                                    prefix=f"{package.__name__}."):
    if is_pkg:
      continue
    module = importlib.import_module(module_name)

    for _name, obj in inspect.getmembers(module, inspect.isclass):
      if not issubclass(obj, Action) or obj is Action:
        continue

      action_runner.action_register.registry_action(obj())

def build_action_runner() -> ActionRunner:
  action_runner = ActionRunner(ActionRegister())
  register_builtin_action(action_runner)
  register_customer_action(action_runner)
  return action_runner

if __name__ == '__main__':
  action_runner = build_action_runner()
  print(action_runner)