import hashlib

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
    # 用「是不是没有值」判断，而不是 falsy：数量 0、空字符串、False 都是合法的槽位值，
    # 按 falsy 判会把它们当成没填，让流程反复追问一个用户已经回答过的问题
    return [
      spec.name for spec in cls.reads
      if spec.required and cls._is_blank(slots.get(spec.name))
    ]

  @staticmethod
  def _is_blank(value: Any) -> bool:
    """槽位算不算「没填」：只有 None 与纯空白字符串算，0 / False 都算填了"""
    return value is None or (isinstance(value, str) and not value.strip())

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
    values = [slots.get(name) for name in cls.idempotency_slots]
    if any(cls._is_blank(value) for value in values):
      return None

    # 对值取哈希而不是直接拼接，解决三件事：
    # 1. 拼接不转义时，含冒号的槽位值会让不同组合拼出同一个键，而这层的全部意义就是唯一性；
    # 2. 地址这类长槽位拼出来会超过中台 idempotency_key 的 64 字符上限；
    # 3. 幂等键会进日志与错误信息，哈希顺带避免把收货地址原文带出去。
    # 前缀保留动作名，排查时还能一眼看出这是哪个动作的键。
    digest = hashlib.sha256(
      "\u0000".join(str(value) for value in values).encode("utf-8")
    ).hexdigest()[:32]
    return f"{cls.name}:{digest}"
