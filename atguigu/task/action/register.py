"""
Provide Ability to register, to manage 5 Actions
"""

from atguigu.task.action.base import Action


class ActionRegister:

  def __init__(self):
    self.actions: dict[str, Action] = {}

  def registry_action(self, action: Action):
    self.actions[action.name] = action

  def get_action(self, action_name: str) -> Action:
    return self.actions[action_name]