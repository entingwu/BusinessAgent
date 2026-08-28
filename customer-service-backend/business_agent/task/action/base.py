from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from business_agent.domain.messages import BotMessage
from business_agent.domain.state import DialogueState

@dataclass(slots=True)
class ActionResult:
  messages: list[BotMessage]=field(default_factory=list)
  updated_slots: dict[str, Any]=field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class SlotSpec:
  """
  一个动作要读的槽位。规范 3.1.5 要求每个工具声明入参
  """
  name: str                    # 槽位名，与 flow_config 里的槽位一一对应
  required: bool = True        # 缺这个槽位时动作能不能跑
  description: str = ""        # 这个动作拿它做什么


class Action(ABC):
  """
  所有动作的基类。

  除了 run()，子类还要**声明自己是什么**——规范 3.1.5【第一档】要求每个工具声明
  名称、入参、出参、是否为写操作。这些声明不是文档，是给代码用的：

  - `reads` / `writes` 让「这个动作依赖哪些槽位、产出哪些槽位」变成可读取的事实，
    而不是散落在各 action 里 `state.active_task.slots.get(...)` 的隐式约定；
  - `is_write` 是规范 3.3.5 下单流程的前置：写操作前必须向用户确认，
    没有这个标志就无法在引擎层区分「查一下」和「真的下单」；
  - `idempotency_slots` 声明幂等键由哪些槽位构成（规范 B.4 第二档「加幂等键声明」）。
    写操作重试时靠它判断「这是同一笔」，避免用户点两次就下两单。

  只读动作把这三样留空即可，默认值就是只读、无幂等键。
  """
  name: str
  description: str = ""

  # 入参：这个动作会从槽位里读什么
  reads: tuple[SlotSpec, ...] = ()

  # 出参：这个动作会写回哪些槽位。只列槽位名——值的形态由动作自己保证
  writes: tuple[str, ...] = ()

  # 是否为写操作（会改变业务系统状态：下单、退款、催发货……）。
  # 只读查询一律 False。引擎据此决定要不要先向用户确认
  is_write: bool = False

  # 幂等键由哪些槽位构成，仅对写操作有意义。
  # 例如下单动作声明 ("order_draft_id",)，同一个草稿重试不会产生第二笔订单
  idempotency_slots: tuple[str, ...] = ()

  @abstractmethod
  async def run(self, action_kwargs: dict[str, Any], state: DialogueState) -> ActionResult:
    pass

  @classmethod
  def missing_required_slots(cls, state: DialogueState) -> list[str]:
    """
    Goal: 按 reads 声明检查当前状态缺哪些必需槽位
    Args:
        state: 当前对话状态
    Returns:
        缺失的必需槽位名列表；不缺则为空
    """
    slots = state.active_task.slots if state.active_task is not None else {}
    return [spec.name for spec in cls.reads if spec.required and not slots.get(spec.name)]

  @classmethod
  def idempotency_key(cls, state: DialogueState) -> str | None:
    """
    Goal: 由声明的槽位拼出这次写操作的幂等键
    只读动作或没声明 idempotency_slots 的动作返回 None。
    任何一个组成槽位为空都返回 None——宁可让调用方自己兜底，
    也不要拼出一个「看起来像但其实不唯一」的键，那比没有键更危险。
    Args:
        state: 当前对话状态
    Returns:
        幂等键字符串，或 None
    """
    if not cls.is_write or not cls.idempotency_slots:
      return None
    slots = state.active_task.slots if state.active_task is not None else {}
    values = [str(slots.get(name) or "") for name in cls.idempotency_slots]
    if not all(values):
      return None
    return ":".join([cls.name, *values])
