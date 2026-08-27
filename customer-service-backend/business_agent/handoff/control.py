"""
人工接管的控制权状态与触发判定。对应规范 3.3.4 第一档。

第一档只做「状态与提示」：把会话标记成三态之一、HUMAN 时 Agent 停止自动应答、
把归属暴露给界面。移交包（完整历史 + 槽位 + 工具结果 + 触发原因）与人工回交后
的事实同步属于第二档，不在这里。
"""

from dataclasses import dataclass
from enum import Enum


class ControlOwner(str, Enum):
  """
  会话控制权归属。继承 str 是为了让它能直接进 to_dict 落库、也能直接进 API 响应，
  不必两头做转换。
  """
  AGENT = "AGENT"                  # Agent 自动应答
  PENDING_HUMAN = "PENDING_HUMAN"  # 已排队等人工接入，Agent 仍可继续兜着
  HUMAN = "HUMAN"                  # 人工已接管，Agent 不再自动应答

  @classmethod
  def coerce(cls, value: object) -> "ControlOwner":
    """
    Goal: 把任意来源的值收敛成合法状态。落库的旧状态没有这个字段、
          外部接口可能传脏值，一律降级为 AGENT 而不是抛异常——
          控制权读不出来时，让 Agent 继续服务比让整个会话崩掉好
    """
    try:
      return cls(str(value).upper())
    except (ValueError, AttributeError):
      return cls.AGENT


class HandoffTrigger(str, Enum):
  """触发接管的原因。会随会话落库，第二档的移交包要靠它说明「为什么转过来」。"""
  USER_REQUESTED = "user_requested"          # 消费者显式要求人工
  RISKY_TOPIC = "risky_topic"                # 命中高风险话题
  KEYWORD = "keyword"                        # 命中配置关键词
  REPEATED_CLARIFY = "repeated_clarify"      # 意图连续识别失败
  KNOWLEDGE_MISS = "knowledge_miss"          # 知识检索连续未命中
  MANUAL = "manual"                          # 坐席主动接管


# 高风险话题：这些一旦谈崩，代价远高于「多转一次人工」。
# 规范 3.3.4 点名了投诉、议价、退换货三类。
RISKY_TOPIC_KEYWORDS: tuple[str, ...] = (
  "投诉", "举报", "曝光", "消协", "工商", "起诉", "律师",
  "议价", "便宜点", "打折", "讲价", "少点钱",
  "退货", "换货", "退换", "退款", "赔偿", "索赔",
)

# 显式要人工。注意「人工」两字本身不够——「人工智能」会误命中，
# 所以这里用更长的短语，宁可漏判也不要把正常对话踢给坐席。
HUMAN_REQUEST_KEYWORDS: tuple[str, ...] = (
  "转人工", "转接人工", "人工客服", "真人", "找客服", "要客服",
  "人工服务", "叫个人", "换个人",
)

# 连续多少次识别失败 / 检索未命中就转人工。
# 取 3 而不是 2：LLM 偶发一次跑偏很常见，2 次会把大量正常会话误踢给人工；
# 3 次基本可以确定是 Agent 真的处理不了。
REPEATED_FAILURE_THRESHOLD = 3


@dataclass(slots=True)
class HandoffDecision:
  """判定结果。needed 为假时其余字段无意义。"""
  needed: bool
  trigger: HandoffTrigger | None = None
  reason: str = ""


def _hit(text: str, keywords: tuple[str, ...]) -> str | None:
  for keyword in keywords:
    if keyword in text:
      return keyword
  return None


def evaluate(text: str | None,
             consecutive_clarify: int,
             consecutive_knowledge_miss: int,
             extra_keywords: tuple[str, ...] = (),
             handled_by_flow: bool = False) -> HandoffDecision:
  """
  Goal: 判断当前这轮是否该转人工
  Args:
      text: 用户这轮说的话；卡片消息没有文本，传 None
      consecutive_clarify: 连续澄清失败次数
      consecutive_knowledge_miss: 连续知识检索未命中次数
      extra_keywords: 商家配置的额外关键词
      handled_by_flow: 本轮是否已被商家配置的能力接住（知识意图或业务流程）。
          为真时不按高风险话题转人工——高风险话题的本意是「Agent 不该自己硬答」，
          但商家专门配了 refund_request 流程或 return_policy 知识意图来接这件事，
          那就是商家选定的处理方式，再转人工既多余又互相矛盾：
          用户会同时收到「请告诉我你的订单号」和「已帮你转人工」两条指令
  Returns:
      HandoffDecision；needed 为真时带上触发原因
  """
  content = (text or "").strip()

  if content:
    if (hit := _hit(content, HUMAN_REQUEST_KEYWORDS)) is not None:
      return HandoffDecision(True, HandoffTrigger.USER_REQUESTED, f"用户明确要求人工（命中「{hit}」）")

    if (hit := _hit(content, extra_keywords)) is not None:
      return HandoffDecision(True, HandoffTrigger.KEYWORD, f"命中配置关键词「{hit}」")

    # 高风险话题只在「没有配置流程接住」时才转——闲聊轨道或澄清失败时提到投诉、
    # 议价，才是真正需要人介入的场景
    if not handled_by_flow and (hit := _hit(content, RISKY_TOPIC_KEYWORDS)) is not None:
      return HandoffDecision(True, HandoffTrigger.RISKY_TOPIC, f"命中高风险话题「{hit}」")

  # 计数类触发放在关键词之后：关键词是明确信号，计数是推断，明确的优先
  if consecutive_clarify >= REPEATED_FAILURE_THRESHOLD:
    return HandoffDecision(
      True, HandoffTrigger.REPEATED_CLARIFY,
      f"连续 {consecutive_clarify} 轮未能识别意图")

  if consecutive_knowledge_miss >= REPEATED_FAILURE_THRESHOLD:
    return HandoffDecision(
      True, HandoffTrigger.KNOWLEDGE_MISS,
      f"连续 {consecutive_knowledge_miss} 轮知识检索未命中")

  return HandoffDecision(False)


# 转入 PENDING_HUMAN 时给消费者的提示。按触发原因分开写，
# 因为「你说要人工」和「我没听懂」对用户来说是完全不同的两件事
PENDING_NOTICE: dict[HandoffTrigger, str] = {
  HandoffTrigger.USER_REQUESTED: "好的，已经帮你转接人工客服，请稍等。在坐席接入前你可以继续留言。",
  HandoffTrigger.RISKY_TOPIC: "这个问题我帮你转给人工客服跟进，请稍等。在坐席接入前你可以继续留言。",
  HandoffTrigger.KEYWORD: "这个问题我帮你转给人工客服跟进，请稍等。在坐席接入前你可以继续留言。",
  HandoffTrigger.REPEATED_CLARIFY: "抱歉，我可能没理解你的意思。已经帮你转接人工客服，请稍等。",
  HandoffTrigger.KNOWLEDGE_MISS: "这个问题我暂时查不到准确答案，已经帮你转接人工客服，请稍等。",
  HandoffTrigger.MANUAL: "人工客服已接入，接下来由客服为你服务。",
}
