"""
人工接管的控制权状态与触发判定。对应规范 3.3.4 第一档。

第一档只做「状态与提示」：把会话标记成三态之一、HUMAN 时 Agent 停止自动应答、
把归属暴露给界面。移交包（完整历史 + 槽位 + 工具结果 + 触发原因）与人工回交后
的事实同步属于第二档，不在这里。
"""

import re
from dataclasses import dataclass
from enum import Enum

from business_agent.config.settings import settings


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

# ---------------------------------------------------------------------------
# 英文关键词。界面是英文的，真实用户大概率说英文，而上面两组中文词对英文
# 一条都不命中——同一个意图「能便宜点吗 / Can you give me a discount」，
# 中文会转人工、英文不会，这是实测出来的不对称。
#
# 英文**不能沿用中文那套子串匹配**，必须带词边界。最典型的一个坑：
# "sue" 是 "issue" 的子串，于是「I have an issue with my order」——一句
# 再普通不过的话——会被判成用户要起诉。加 \b 之后就不命中了。
#
# 但词边界只解决一半。"agent" 与 "human" 即便带边界仍然太宽：
# 「the shipping agent called me」「is this a human or a bot」都会误命中。
# 这两个词只以短语形式出现，绝不单独列——这与中文那条「『人工』两字本身
# 不够」是同一条纪律，换了个语言重演一遍。
# ---------------------------------------------------------------------------

HUMAN_REQUEST_PATTERNS_EN: tuple[str, ...] = (
  "human agent", "live agent", "real agent", "human rep", "human representative",
  "real person", "live person", "real human",
  "talk to a human", "speak to a human", "chat with a human",
  "talk to someone", "speak to someone",
  "talk to an agent", "speak to an agent", "connect me to an agent",
  "customer service rep", "service representative",
  "human support", "live support",
  "transfer me", "escalate this", "escalate to",
  "your manager", "a manager", "supervisor",
)

RISKY_TOPIC_PATTERNS_EN: tuple[str, ...] = (
  # 投诉与法律施压
  "complaint", "complain", "report you", "consumer protection",
  "better business bureau", "trading standards",
  "lawyer", "attorney", "legal action", "sue", "small claims",
  "chargeback", "dispute the charge",
  # 议价
  "discount", "lower the price", "lower price", "better price",
  "price match", "negotiate", "haggle", "cheaper price", "give me a deal",
  # 退换与索赔
  "refund", "return this", "return it", "exchange it",
  "compensation", "compensate", "reimburse",
)

# 连续多少次识别失败 / 检索未命中就转人工。
# 取 3 而不是 2：LLM 偶发一次跑偏很常见，2 次会把大量正常会话误踢给人工；
# 3 次基本可以确定是 Agent 真的处理不了。
REPEATED_FAILURE_THRESHOLD = 3


def configured_keywords() -> tuple[str, ...]:
  """
  Goal: 读商家配置的转人工关键词（规范 3.3.4 五种触发里的「命中配置关键词」）

  此前 evaluate 的 extra_keywords 参数有定义、有自用，但**零调用方传值**——
  这条触发在代码里假装支持、实际永不可达。现在由 HANDOFF_KEYWORDS 配置项供给。
  """
  raw = (settings.handoff_keywords or "").strip()
  if not raw:
    return ()
  return tuple(keyword.strip() for keyword in raw.split(",") if keyword.strip())


@dataclass(slots=True)
class HandoffDecision:
  """判定结果。needed 为假时其余字段无意义。"""
  needed: bool
  trigger: HandoffTrigger | None = None
  reason: str = ""


def _has_cjk(text: str) -> bool:
  return any("\u4e00" <= char <= "\u9fff" for char in text)


def _hit(text: str, keywords: tuple[str, ...]) -> str | None:
  """
  Goal: 判断文本命中了哪个关键词，中英文各按各自正确的方式匹配

  中文按子串：中文没有词间空格，子串就是正确的匹配单位。
  英文按词边界：子串匹配在英文上是错的，"issue" 会命中 "sue"。

  按关键词本身是否含汉字来分派，而不是按用户输入的语言——
  这样商家在 HANDOFF_KEYWORDS 里混着配中英文词也能各自正确工作，
  不需要先判断用户说的是哪种语言（混合语句「我的 order 怎么查」本来就无解）。
  """
  lowered = text.lower()
  for keyword in keywords:
    if _has_cjk(keyword):
      if keyword in text:
        return keyword
    elif re.search(rf"\b{re.escape(keyword.lower())}\b", lowered):
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
    if (hit := _hit(content, HUMAN_REQUEST_KEYWORDS + HUMAN_REQUEST_PATTERNS_EN)) is not None:
      return HandoffDecision(True, HandoffTrigger.USER_REQUESTED, f"用户明确要求人工（命中「{hit}」）")

    if (hit := _hit(content, extra_keywords)) is not None:
      return HandoffDecision(True, HandoffTrigger.KEYWORD, f"命中配置关键词「{hit}」")

    # 高风险话题只在「没有配置流程接住」时才转——闲聊轨道或澄清失败时提到投诉、
    # 议价，才是真正需要人介入的场景
    if not handled_by_flow and (
        hit := _hit(content, RISKY_TOPIC_KEYWORDS + RISKY_TOPIC_PATTERNS_EN)) is not None:
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
