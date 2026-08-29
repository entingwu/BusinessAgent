"""
Control-ownership states and handoff triggers for human takeover. Spec 3.3.4, tier 1.

Tier 1 only does "state and notice": mark the session as one of three states, stop the Agent from
answering automatically while a human holds it, and expose ownership to the UI. The handoff
package (full history + slots + tool results + trigger reason) and re-syncing facts after a human
hands control back are tier 2, and are not here.
"""

import re
from dataclasses import dataclass
from enum import Enum

from business_agent.config.settings import settings
from business_agent.infrastructure.text import contains_cjk


class ControlOwner(str, Enum):
  """
  Who owns the session. It subclasses str so the same value can go straight into to_dict() for
  persistence and straight into an API response, with no conversion at either end.
  """
  AGENT = "AGENT"                  # the Agent answers automatically
  PENDING_HUMAN = "PENDING_HUMAN"  # queued for a human; the Agent still covers in the meantime
  HUMAN = "HUMAN"                  # a human has taken over; the Agent stops answering

  @classmethod
  def coerce(cls, value: object) -> "ControlOwner":
    """
    Goal: narrow a value from any source down to a legal state. State rows persisted before this
          field existed do not have it, and external callers may pass junk. Both degrade to AGENT
          rather than raising — when ownership cannot be read, letting the Agent keep serving
          beats crashing the whole session.
    """
    try:
      return cls(str(value).upper())
    except (ValueError, AttributeError):
      return cls.AGENT


class HandoffTrigger(str, Enum):
  """Why the takeover fired. Persisted with the session; tier 2's handoff package needs it to
  explain "why was this escalated"."""
  USER_REQUESTED = "user_requested"          # the customer explicitly asked for a human
  RISKY_TOPIC = "risky_topic"                # a high-risk topic was mentioned
  KEYWORD = "keyword"                        # a merchant-configured keyword matched
  REPEATED_CLARIFY = "repeated_clarify"      # intent recognition failed several turns running
  KNOWLEDGE_MISS = "knowledge_miss"          # knowledge retrieval missed several turns running
  MANUAL = "manual"                          # an agent claimed the session themselves


# High-risk topics: if one of these goes badly the cost is far higher than one extra handoff.
# Spec 3.3.4 names three — complaints, haggling, and returns/exchanges.
RISKY_TOPIC_KEYWORDS: tuple[str, ...] = (
  "投诉", "举报", "曝光", "消协", "工商", "起诉", "律师",
  "议价", "便宜点", "打折", "讲价", "少点钱",
  "退货", "换货", "退换", "退款", "赔偿", "索赔",
)

# Explicit requests for a human. Note that the bare word 「人工」 is not enough — it would match
# 「人工智能」 ("artificial intelligence"). Hence the longer phrases: better to miss a request
# than to kick a normal conversation over to a live agent.
HUMAN_REQUEST_KEYWORDS: tuple[str, ...] = (
  "转人工", "转接人工", "人工客服", "真人", "找客服", "要客服",
  "人工服务", "叫个人", "换个人",
)

# ---------------------------------------------------------------------------
# English keywords. The UI is English, so real users are likely to write English, and neither
# Chinese list above matches a single English phrase. Measured asymmetry: for one and the same
# intent (「能便宜点吗」 vs "Can you give me a discount"), the Chinese wording handed off and the
# English one did not.
#
# English **cannot reuse the substring matching the Chinese lists rely on** — it needs word
# boundaries. The clearest trap: "sue" is a substring of "issue", so "I have an issue with my
# order" — about as ordinary a sentence as there is — would be read as the user threatening to
# sue. With \b it no longer matches.
#
# But word boundaries only solve half of it. "agent" and "human" stay too broad even bounded:
# "the shipping agent called me" and "is this a human or a bot" both match. So those two words
# only ever appear inside a phrase, never on their own — the same discipline as the 「人工」 note
# above, replayed in another language.
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
  # complaints and legal pressure
  "complaint", "complain", "report you", "consumer protection",
  "better business bureau", "trading standards",
  "lawyer", "attorney", "legal action", "sue", "small claims",
  "chargeback", "dispute the charge",
  # haggling
  "discount", "lower the price", "lower price", "better price",
  "price match", "negotiate", "haggle", "cheaper price", "give me a deal",
  # returns, exchanges and claims
  "refund", "return this", "return it", "exchange it",
  "compensation", "compensate", "reimburse",
)

# How many consecutive recognition failures / retrieval misses before handing off.
# Three rather than two: an LLM going off track once is common, and two would kick a lot of
# healthy conversations to a human. Three is a fair sign the Agent genuinely cannot handle it.
REPEATED_FAILURE_THRESHOLD = 3


def configured_keywords() -> tuple[str, ...]:
  """
  Goal: read the merchant's own handoff keywords (the "configured keyword matched" case among the
        five triggers in spec 3.3.4)

  evaluate()'s extra_keywords parameter used to be defined and used inside the function body, but
  **no caller ever passed a value** — the trigger pretended to be supported while being
  unreachable. HANDOFF_KEYWORDS now supplies it.
  """
  raw = (settings.handoff_keywords or "").strip()
  if not raw:
    return ()
  return tuple(keyword.strip() for keyword in raw.split(",") if keyword.strip())


@dataclass(slots=True)
class HandoffDecision:
  """The verdict. When needed is false the other fields carry no meaning."""
  needed: bool
  trigger: HandoffTrigger | None = None
  reason: str = ""


def _hit(text: str, keywords: tuple[str, ...]) -> str | None:
  """
  Goal: report which keyword the text matched, matching Chinese and English each the right way

  Chinese matches on substring: it has no spaces between words, so a substring is the correct unit.
  English matches on word boundary: substring matching is simply wrong there — "issue" would match
  "sue".

  The choice is made from **the keyword itself** containing Han characters, not from the language
  of the user's input. That way a merchant can mix Chinese and English in HANDOFF_KEYWORDS and
  both work, and no language detection is needed (a mixed sentence like 「我的 order 怎么查」 has
  no right answer anyway).
  """
  lowered = text.lower()
  for keyword in keywords:
    if contains_cjk(keyword):
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
  Goal: decide whether this turn should hand off to a human
  Args:
      text: what the user said this turn; card messages carry no text, pass None
      consecutive_clarify: how many clarification attempts have failed in a row
      consecutive_knowledge_miss: how many retrievals have missed in a row
      extra_keywords: the merchant's own configured keywords
      handled_by_flow: whether a configured capability (a knowledge intent or a business flow)
          already caught this turn. When it did, do not hand off on a risky topic — the point of
          the risky-topic rule is "the Agent should not answer this on its own", but if the
          merchant configured a refund_request flow or a return_policy intent precisely to handle
          it, that is the handling the merchant chose, and handing off is both redundant and
          self-contradictory: the user would receive "what is your order number?" and "you have
          been transferred to a human" in the same breath
  Returns:
      HandoffDecision; when needed is true it carries the trigger and reason
  """
  content = (text or "").strip()

  if content:
    if (hit := _hit(content, HUMAN_REQUEST_KEYWORDS + HUMAN_REQUEST_PATTERNS_EN)) is not None:
      return HandoffDecision(True, HandoffTrigger.USER_REQUESTED,
                             f"user explicitly asked for a human (matched \"{hit}\")")

    if (hit := _hit(content, extra_keywords)) is not None:
      return HandoffDecision(True, HandoffTrigger.KEYWORD, f"configured keyword matched \"{hit}\"")

    # A risky topic only hands off when no configured flow caught the turn — mentioning a
    # complaint or haggling from the chitchat track, or after clarification failed, is the case
    # that genuinely needs a person
    if not handled_by_flow and (
        hit := _hit(content, RISKY_TOPIC_KEYWORDS + RISKY_TOPIC_PATTERNS_EN)) is not None:
      return HandoffDecision(True, HandoffTrigger.RISKY_TOPIC, f"high-risk topic matched \"{hit}\"")

  # Counter-based triggers come after keywords: a keyword is an explicit signal, a counter is an
  # inference, and the explicit one wins
  if consecutive_clarify >= REPEATED_FAILURE_THRESHOLD:
    return HandoffDecision(
      True, HandoffTrigger.REPEATED_CLARIFY,
      f"intent not recognised for {consecutive_clarify} turns running")

  if consecutive_knowledge_miss >= REPEATED_FAILURE_THRESHOLD:
    return HandoffDecision(
      True, HandoffTrigger.KNOWLEDGE_MISS,
      f"knowledge retrieval missed for {consecutive_knowledge_miss} turns running")

  return HandoffDecision(False)


# Notice shown to the customer when the session moves to PENDING_HUMAN. Written per trigger,
# because "you asked for a person" and "I did not understand you" are entirely different things
# from the user's side.
PENDING_NOTICE: dict[HandoffTrigger, str] = {
  HandoffTrigger.USER_REQUESTED: "Sure — I am transferring you to a human agent. "
                                 "You can keep writing while you wait for them to join.",
  HandoffTrigger.RISKY_TOPIC: "I am passing this to a human agent to follow up. "
                              "You can keep writing while you wait for them to join.",
  HandoffTrigger.KEYWORD: "I am passing this to a human agent to follow up. "
                          "You can keep writing while you wait for them to join.",
  HandoffTrigger.REPEATED_CLARIFY: "Sorry — I do not think I understood you. "
                                   "I have transferred you to a human agent, one moment.",
  HandoffTrigger.KNOWLEDGE_MISS: "I cannot find a reliable answer to this. "
                                 "I have transferred you to a human agent, one moment.",
  HandoffTrigger.MANUAL: "A human agent has joined and will take it from here.",
}
