"""
Edge Data Model
- ordered edge: next: ask_order_number
- conditional edge: if "codition1": then "clarification_rejected"
- default edge: else: ask_rephrase

Base Class Idea
"""

from dataclasses import dataclass

@dataclass(slots=True)
class FlowStepLink:
  """
  Base Class for Three different edge types 
  """
  target: str           # next node id (ordered edge next, conditional edge then, default edge else)


@dataclass(slots=True)
class FlowStepStaticLink(FlowStepLink):
  pass


@dataclass(slots=True)
class FlowStepConditionLink(FlowStepLink):
  condition: str


@dataclass(slots=True)
class FlowStepFallbackLink(FlowStepLink):
  pass