"""
按偏好推荐商品。对应规范 3.3.3 与验收标准第 5 条。

与被它取代的 recommend_similar_products 的区别：那个只认一个 product_id、
返回一句占位文案；这个收集偏好维度、真调中台检索、以卡片列表返回候选。
"""

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from business_agent.domain.messages import BotMessage, FocusedObject
from business_agent.domain.state import DialogueState
from business_agent.task.action.base import Action, ActionResult, SlotSpec
from business_agent.task.action.customer.shared import search_products

logger = logging.getLogger(__name__)

# 槽位名 → 中台属性名。中台的属性名是白名单，传别的会 400，
# 所以映射写死在这里，不从槽位名直接猜
SLOT_TO_ATTR: dict[str, str] = {
  "product_use_case": "use_case",
  "product_style": "style",
  "product_size": "size",
}

# 一次最多给几张卡片。给太多等于没筛，用户还是要自己看；
# 3.3.3 要的是「两轮收敛」，每轮给少量候选才收敛得动
MAX_CARDS = 4

# 收敛按钮的候选值。给值不给维度名——见 user_flows.yml 里 wait_more 的注释。
# 值取自槽位 description 里列的可选值，规划器认得。
#
# STYLE_VALUES **刻意保持中文**，没有随界面一起英语化：这些值会被原样填进
# product_style 槽位、再送去中台做属性过滤，中台存的就是 "style": "极简"。
# 翻了这里而不改中台种子数据，点击后必然检索为空——而空结果与「确实没有匹配
# 商品」在界面上无法区分，失败是静默的。要动它得四处同步改，是单独一档的活。
# BUDGET_VALUES 可以翻，因为 _parse_budget 只抽数字、不做字符串匹配。
STYLE_VALUES = ("极简", "商务", "电竞", "北欧")
BUDGET_VALUES = ("Under 300", "Under 500", "Under 1000")


class ActionRecommendProducts(Action):
  name = "action_recommend_products"
  description = "按用途、预算、风格三个偏好维度检索中台商品，返回商品卡片与收敛用的快捷回复"
  # 三个偏好槽位都不是必需的：缺哪个就少一个过滤条件，仍然能给出候选。
  # 标成必需会让用户说「随便推荐点」时流程卡住
  reads = (
    SlotSpec(name="product_use_case", required=False, description="使用场景，映射到中台的 use_case 属性过滤"),
    SlotSpec(name="product_style", required=False, description="风格偏好，映射到中台的 style 属性过滤"),
    # 这里**刻意不列 product_size**，尽管 SLOT_TO_ATTR 里有它的映射：
    # 那条映射的代码是活的，但它的输入是死的——product_size 既不在 user_flows.yml
    # 的槽位声明里，也没有任何 collect 步骤收集它，于是 slot_guard 会在写入前丢掉它
    # （实测日志：「丢弃槽位 product_size='大号'：流程 product_recommendation 只声明了 …」），
    # 运行时 slots.get("product_size") 恒为 None。
    # 我一度把它加进 reads，依据是「直接给 action 传这个槽位确实会改变检索结果」——
    # 但那个实测绕过了守卫，不是真实链路。多声明一个永不生效的槽位，
    # 和漏掉一个正在生效的槽位一样，都让 reads 这个「事实」变成假的。
    # 要让它真活，得先在 user_flows.yml 里把它补成可收集的槽位。
    SlotSpec(name="product_budget", required=False, description="预算上限，解析出数字后作为 max_price"),
    SlotSpec(name="product_round", required=False, description="第几轮收敛，用于生成不重复的快捷回复"),
  )
  writes = ("product_round",)
  # 只读检索中台，不改变任何业务状态
  is_write = False

  async def run(self, action_kwargs: dict[str, Any], state: DialogueState) -> ActionResult:
    """
    Goal: 按已收集的偏好槽位检索商品，以卡片列表 + 快捷回复返回
    """
    slots = state.active_task.slots if state.active_task is not None else {}

    attrs = {attr: slots.get(slot) for slot, attr in SLOT_TO_ATTR.items() if slots.get(slot)}
    max_price = self._parse_budget(slots.get("product_budget"))

    data = await search_products(
      attrs=attrs,
      max_price=max_price,
      # 3.3.3「库存实时查询不缓存」：只推有货的，缺货款留给「换一批」时的替代逻辑
      in_stock=True,
      limit=MAX_CARDS,
    )

    # 接口失败与「确实没有匹配商品」必须区分开——前者不能说成「没有找到」，
    # 那是把自己的故障当成业务结论告诉用户
    if data is None:
      logger.warning("recommend_products search_failed attrs=%s max_price=%s", attrs, max_price)
      # 计数照写。三条返回路径里漏掉任何一条，wait_more 的出口条件就永远不成立——
      # 中台持续故障时每一轮都回到 recommend，流程再也退不出去，
      # 正是 user_flows.yml 那条「回路要有出口」想防的情况
      return ActionResult(
        messages=[BotMessage(
          text="The product service is not responding right now, so I cannot look this up. "
               "You can ask me again shortly, or I can hand you to a human agent.",
          suggestions=["Try again later", "Talk to a human"],
        )],
        updated_slots={"product_round": self._next_round(slots)},
      )

    items = data.get("items") or []
    if not items:
      # 无结果时更需要出路：没有按钮用户就只能自己想怎么改条件
      return ActionResult(
        messages=[BotMessage(
          text=self._no_match_text(attrs, max_price),
          suggestions=self._refine_suggestions(slots),
        )],
        updated_slots={"product_round": self._next_round(slots)},
      )

    cards = [self._to_card(item) for item in items]
    total = data.get("total") or len(items)
    return ActionResult(
      messages=[BotMessage(
        text=self._headline(attrs, max_price, shown=len(cards), total=total),
        cards=cards,
        suggestions=self._refine_suggestions(slots),
      )],
      updated_slots={"product_round": self._next_round(slots)},
    )

  def _next_round(self, slots: dict[str, Any]) -> str:
    """收敛轮次。wait_more 用它决定还回不回 recommend——回路必须有出口"""
    try:
      return str(int(slots.get("product_round") or 0) + 1)
    except (TypeError, ValueError):
      return "1"

  def _refine_suggestions(self, slots: dict[str, Any]) -> list[str]:
    """
    Goal: 给出能真正改变下一次检索的收敛选项

    只给还没选过的值——把当前已选的风格再列一遍等于让用户点了没变化，
    那正是这个按钮此前被判为「死的」的原因
    """
    current_style = str(slots.get("product_style") or "")
    suggestions = [style for style in STYLE_VALUES if style != current_style][:2]

    current_budget = str(slots.get("product_budget") or "")
    tighter = next((budget for budget in BUDGET_VALUES if budget != current_budget), None)
    if tighter:
      suggestions.append(tighter)

    suggestions.append("No thanks")
    return suggestions

  def _to_card(self, item: dict[str, Any]) -> FocusedObject:
    """
    Goal: 中台商品项 → 附录 E.1 的业务对象卡片

    attributes 里放什么由前端卡片决定：它读 price / cover_url / description
    三个键，description 没有就退回展示价格。所以把库存与关键规格拼进
    description，让「含实时库存」这条在界面上真的看得见
    """
    attributes = item.get("attributes") or {}
    return FocusedObject(
      id=str(item.get("product_id") or ""),
      title=str(item.get("title") or ""),
      type="product",
      attributes={
        "price": item.get("price"),
        "cover_url": item.get("cover_url"),
        "description": self._describe(item, attributes),
        "stock_status": item.get("stock_status"),
        **{key: value for key, value in attributes.items() if key in ("use_case", "style", "color", "size")},
      },
    )

  def _describe(self, item: dict[str, Any], attributes: dict[str, Any]) -> str:
    parts = [str(item.get("stock_status") or "").strip()]
    spec = str(attributes.get("spec") or "").strip()
    if spec:
      # spec 是自由文本，可能很长；卡片上放不下，取第一段
      parts.append(spec.split("/")[0].strip())
    return " · ".join(part for part in parts if part)

  def _parse_budget(self, raw: Any) -> float | None:
    """
    Goal: 把「500」「500元」「五百以内」这类输入解析成价格上限。
          解析不了就返回 None——宁可不过滤，也不要因为解析错把候选全筛掉
    """
    if raw is None:
      return None
    digits = "".join(char for char in str(raw) if char.isdigit() or char == ".")
    if not digits:
      return None
    try:
      return float(Decimal(digits))
    except (InvalidOperation, ValueError):
      return None

  def _headline(self, attrs: dict[str, str], max_price: float | None, *, shown: int, total: int) -> str:
    condition = self._condition_text(attrs, max_price)
    more = f" ({total} in total, showing {shown})" if total > shown else ""
    return f"Here is what I found {condition}{more}:"

  def _no_match_text(self, attrs: dict[str, str], max_price: float | None) -> str:
    # 3.3.3「无匹配时如实告知，不编造商品」
    return (f"I could not find any products {self._condition_text(attrs, max_price)}. "
            "Want to raise the budget, or try a different style?")

  def _condition_text(self, attrs: dict[str, str], max_price: float | None) -> str:
    # 标签用英文，值仍是中台存的中文（见 STYLE_VALUES 上方注释）
    labels = {"use_case": "use case", "style": "style", "size": "size"}
    parts = [f"{labels.get(key, key)} {value}" for key, value in attrs.items()]
    if max_price is not None:
      parts.append(f"under {max_price:.0f}")
    return "for " + ", ".join(parts) if parts else "matching your request"
