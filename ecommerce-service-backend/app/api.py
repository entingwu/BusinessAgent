from __future__ import annotations

import hashlib
import json

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import (
    LogisticsRecord,
    Order,
    OrderItem,
    Product,
    RefundRequest,
    ShippingUrgeRequest,
    User,
)
from app.schemas import (
    ApiResponse,
    CreateOrderRequest,
    CreateOrderResultData,
    LogisticsData,
    LogisticsTraceData,
    OrderDetailData,
    OrderItemData,
    OrderSummaryData,
    OperationResultData,
    OrderStatusData,
    ProductData,
    ProductSearchData,
    ProductSearchItemData,
    ProductSummaryData,
    RefundRequestBody,
    UserOrdersData,
    UserProductsData,
    UrgeShippingRequest,
)


router = APIRouter()

# stock_quantity 才是真实库存，stock_status 只是它的派生展示值。
# 判定「有没有货」一律以数量为准；这两个常量仍保留，用于写回展示值。
# 订单状态是**匹配键，不是展示文本**，所以读侧刻意同时接受中英两套值。
#
# 数据库里的值已由 2026-08-28-englishify-status-values.sql 翻成英文，写侧（下面的
# _NEW_ORDER_STATUS、_IN_STOCK_LABEL）也只写英文。中文仍留在集合里，不是过渡期
# 的残留，而是**故意的兼容垫片**：
#   · 2026-08-27-unify-product-attributes.sql 与 stock-quantity 那个脚本都会写
#     stock_status，本仓的约定又是「拿不准就重跑迁移」；
#   · 别的开发机、别的分支上的库可能还没跑过英语化迁移。
# 任何一种情况下，只要读侧两套都认，就不会出现「值对不上」这种静默失效。
#
# 抽成具名常量是为了让这个依赖能被 grep 到。原来它是内联字面量，
# 而它的失败形态是静默的：值一旦对不上，这个端点对任何订单都返回 400，
# 不报错、不崩，只是永远拒绝。「两处独立事实、两处都不报警」是这个仓里反复出现的形态。
_SHIPPABLE_STATUSES = frozenset({
  "Awaiting shipment", "Awaiting pickup",   # 当前值
  "待发货", "待揽收",                        # 兼容未跑过英语化迁移的库
})

# 写回展示值时只写英文；读侧的判定一律以 stock_quantity 为准，不看这两个字符串。
_IN_STOCK_LABEL = "In stock"
_OUT_OF_STOCK_LABEL = "Out of stock"

# 新建订单的状态。支付不在本服务范围内，所以订单落库即为「待支付」。
_NEW_ORDER_STATUS = "Awaiting payment"

_LIKE_ESCAPE_CHAR = "\\"

# attributes_json 的 key 是一套固定集合（见 docker/mysql/init/02-seed.sql 头部注释）。
# 属性名写错时必须报错而不是返回空列表——「没有这个字段」和「没有匹配商品」是两回事，
# 混成同一个响应会让调用方把自己的参数 bug 当成业务结论告诉用户，
# 正好撞上 3.3.3 的「无匹配时如实告知，不编造商品」。
# 这里和种子数据的 key 集合是两处独立事实，是一个双向同步点，但只有一个方向会报警：
#   新增维度时漏加白名单 -> 调用方一试就 400 并被列出可用属性名，响亮且自解释；
#   反过来改名 / 删除 key，或者白名单里拼错一个字（warrenty），数据侧没有对应字段，
#   查询会退回 200 + 空列表，也就是这个白名单本来要消灭的那种静默失效。
# 所以改动属性维度时，这两处必须一起改，不要指望 400 会兜住。
_FILTERABLE_ATTR_KEYS = ("use_case", "style", "spec", "size", "color", "brand", "warranty")


def _wrap(data):
    return ApiResponse(data=data)


def _build_like_pattern(keyword: str) -> str:
    """把用户关键词转成 LIKE 模式，转义 % 和 _ 等通配符，避免被当作通配符使用。"""
    escaped = (
        keyword.replace(_LIKE_ESCAPE_CHAR, _LIKE_ESCAPE_CHAR * 2)
        .replace("%", _LIKE_ESCAPE_CHAR + "%")
        .replace("_", _LIKE_ESCAPE_CHAR + "_")
    )
    return f"%{escaped}%"


def _is_in_stock(product: Product) -> bool:
    """判定有没有货。以 stock_quantity 为准——stock_status 是给人看的，数量是给系统用的。"""
    return product.stock_quantity > 0


def _stock_label(quantity: int) -> str:
    """由库存数量派生展示用的 stock_status，保证两者不会说两套话。"""
    return _IN_STOCK_LABEL if quantity > 0 else _OUT_OF_STOCK_LABEL


def _build_json_path(key: str) -> str:
    """
    把属性名转成 MySQL 的 JSON 路径表达式，形如 $."use_case"。
    引号与反斜杠必须转义——这个串会作为绑定参数传给 JSON_EXTRACT，不转义就是一处路径注入面。
    """
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    return f'$."{escaped}"'


def _parse_attr_filters(raw_filters: list[str]) -> list[tuple[str, str]]:
    """
    解析可重复的 attr 查询参数，形如 attr=use_case:办公。半角与全角冒号都接受。
    格式不合法、属性名不存在都直接 400，一律不静默忽略——把过滤条件悄悄丢掉
    会让上层以为“筛过了”，把属性名打错的空结果当成“没有这样的商品”。
    """
    parsed: list[tuple[str, str]] = []
    for raw in raw_filters:
        item = raw.strip()
        positions = [index for index in (item.find(":"), item.find("：")) if index >= 0]
        separator_index = min(positions) if positions else -1
        key = item[:separator_index].strip() if separator_index > 0 else ""
        value = item[separator_index + 1 :].strip() if separator_index > 0 else ""
        if not key or not value:
            raise HTTPException(
                status_code=400,
                detail=f"Attribute filter \u201c{raw}\u201d is malformed. Use \u201cname:value\u201d, for example attr=use_case:office.",
            )
        if key not in _FILTERABLE_ATTR_KEYS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"不支持按属性 “{key}” 过滤。可用的属性名为："
                    f"{'、'.join(_FILTERABLE_ATTR_KEYS)}。"
                ),
            )
        parsed.append((key, value))
    return parsed


def _get_user_or_404(db: Session, user_id: str) -> User:
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id} does not exist.")
    return user


def _build_recent_orders(db: Session, user: User, limit: int = 5) -> list[OrderSummaryData]:
    recent_orders = (
        db.query(Order)
        .options(joinedload(Order.items).joinedload(OrderItem.product))
        .filter(Order.user_id == user.id)
        .order_by(Order.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        OrderSummaryData(
            order_id=order.order_id,
            title=order.items[0].title_snapshot if order.items else "Unknown product",
            status=order.status,
            amount=order.amount,
            created_at=order.created_at,
            cover_url=order.items[0].product.cover_url if order.items and order.items[0].product else None,
        )
        for order in recent_orders
    ]


def _build_recent_products(db: Session, user: User, limit: int = 5) -> list[ProductSummaryData]:
    recent_items = (
        db.query(OrderItem)
        .join(Order, Order.id == OrderItem.order_id)
        .join(Product, Product.id == OrderItem.product_id)
        .filter(Order.user_id == user.id)
        .options(joinedload(OrderItem.product))
        .order_by(Order.created_at.desc())
        .limit(limit)
        .all()
    )

    seen_product_ids: set[str] = set()
    products: list[ProductSummaryData] = []
    for item in recent_items:
        product = item.product
        if not product or product.product_id in seen_product_ids:
            continue
        seen_product_ids.add(product.product_id)
        products.append(
            ProductSummaryData(
                product_id=product.product_id,
                title=product.title,
                price=product.price,
                cover_url=product.cover_url,
            )
        )
    return products


def _mask_phone(phone: str) -> str:
    """
    手机号脱敏后落库。中台不保存完整手机号——现有数据列名就叫 receiver_phone_masked，
    保持一致，不因为新增了写接口就把明文存进去。
    """
    digits = "".join(character for character in phone if character.isdigit())
    if len(digits) < 7:
        return "*" * len(digits) if digits else "***"
    return f"{digits[:3]}****{digits[-4:]}"


def _request_fingerprint(body: CreateOrderRequest) -> str:
    """
    Goal: 为下单请求算一个内容指纹，用来识别「同一个幂等键换了内容」
    覆盖决定这笔订单是什么的全部字段：**谁下的**、买了什么、买多少、寄给谁、怎么配送。
    同一 SKU 拆多行与调换顺序会算出同一个指纹——那本来就是同一笔订单。

    user_id 必须在里面。幂等键的唯一索引是全局的、不按用户隔离，
    所以漏掉下单人会开出一条跨用户串单的路径：另一个用户用同样的 key 和同样的购物车
    下单，会拿到别人的订单号并被告知这是自己的「重复提交」，
    再用那个订单号查详情就能看到别人的收件人、手机与完整地址。
    """
    merged: dict[str, int] = {}
    for item in body.items:
        merged[item.product_id] = merged.get(item.product_id, 0) + item.quantity
    payload = json.dumps(
        {
            "user_id": body.user_id,
            "items": sorted(merged.items()),
            "receiver_name": body.receiver_name,
            "receiver_phone": body.receiver_phone,
            "receiver_address": body.receiver_address,
            "delivery_method": body.delivery_method,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_create_order_result(order: Order, *, replay: bool) -> CreateOrderResultData:
    """
    Goal: 把订单实体转成创建订单的返回结构。首次创建与幂等重放共用，保证两者返回完全一致
    """
    delivery_method = order.delivery_method
    return CreateOrderResultData(
        order_id=order.order_id,
        status=order.status,
        status_desc=order.status_desc,
        amount=order.amount,
        delivery_method=delivery_method,
        created_at=order.created_at,
        items=[
            OrderItemData(
                product_id=item.product.product_id if item.product else "",
                title=item.title_snapshot,
                quantity=item.quantity,
                price=item.price,
            )
            for item in order.items
        ],
        idempotent_replay=replay,
    )


def _get_order_or_404(db: Session, order_id: str) -> Order:
    order = (
        db.query(Order)
        .options(joinedload(Order.items).joinedload(OrderItem.product))
        .filter(Order.order_id == order_id)
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {order_id} does not exist.")
    return order


def _get_product_or_404(db: Session, product_id: str) -> Product:
    product = db.query(Product).filter(Product.product_id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail=f"Product {product_id} does not exist.")
    return product


@router.get(
    "/health",
    response_model=ApiResponse,
    tags=["System"],
    summary="Health check",
    description="Checks that the service and its database connection are healthy.",
)
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return _wrap({"status": "ok"})


@router.get(
    "/users/{user_id}/orders",
    response_model=ApiResponse,
    tags=["Users"],
    summary="List a user's recent orders",
    description="Recent orders for a user id, used to render the order object list in the UI.",
)
def user_orders(user_id: str, db: Session = Depends(get_db)):
    user = _get_user_or_404(db, user_id)
    return _wrap(UserOrdersData(user_id=user.user_id, orders=_build_recent_orders(db, user)))


@router.get(
    "/users/{user_id}/products",
    response_model=ApiResponse,
    tags=["Users"],
    summary="List a user's recent products",
    description="Products a user recently bought or interacted with, used to render the product object list in the UI.",
)
def user_products(user_id: str, db: Session = Depends(get_db)):
    user = _get_user_or_404(db, user_id)
    return _wrap(UserProductsData(user_id=user.user_id, products=_build_recent_products(db, user)))


@router.get(
    "/orders/{order_id}",
    response_model=ApiResponse,
    tags=["Orders"],
    summary="Get order detail",
    description="Order header, delivery details and line items for an order id.",
)
def order_detail(order_id: str, db: Session = Depends(get_db)):
    order = _get_order_or_404(db, order_id)
    return _wrap(
        OrderDetailData(
            order_id=order.order_id,
            status=order.status,
            status_desc=order.status_desc,
            amount=order.amount,
            created_at=order.created_at,
            receiver_name=order.receiver_name,
            receiver_phone_masked=order.receiver_phone_masked,
            receiver_address=order.receiver_address,
            items=[
                OrderItemData(
                    product_id=item.product.product_id if item.product else "",
                    title=item.title_snapshot,
                    quantity=item.quantity,
                    price=item.price,
                )
                for item in order.items
            ],
        )
    )


@router.get(
    "/orders/{order_id}/status",
    response_model=ApiResponse,
    tags=["Orders"],
    summary="Get order status",
    description="The order's current status plus the customer-facing description of it.",
)
def order_status(order_id: str, db: Session = Depends(get_db)):
    order = _get_order_or_404(db, order_id)
    return _wrap(
        OrderStatusData(
            order_id=order.order_id,
            status=order.status,
            status_desc=order.status_desc,
        )
    )


@router.get(
    "/orders/{order_id}/logistics",
    response_model=ApiResponse,
    tags=["Orders"],
    summary="Get shipment tracking",
    description="Carrier, tracking number, current shipping status and the tracking events.",
)
def order_logistics(order_id: str, db: Session = Depends(get_db)):
    order = _get_order_or_404(db, order_id)
    record = (
        db.query(LogisticsRecord)
        .options(joinedload(LogisticsRecord.traces))
        .filter(LogisticsRecord.order_id == order.id)
        .order_by(LogisticsRecord.updated_at.desc())
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail=f"No shipping information is available for order {order_id} yet.")

    traces = sorted(record.traces, key=lambda item: item.trace_time, reverse=True)
    return _wrap(
        LogisticsData(
            order_id=order.order_id,
            logistics_company=record.logistics_company,
            tracking_number=record.tracking_number,
            status=record.status,
            status_desc=record.status_desc,
            traces=[
                LogisticsTraceData(time=trace.trace_time, desc=trace.trace_desc)
                for trace in traces
            ],
        )
    )


@router.get(
    "/products",
    response_model=ApiResponse,
    tags=["Products"],
    summary="Search products",
    description=(
        "Search products by keyword, price range, attributes and stock status. Returns a paged "
        "list of candidates plus the total match count, for callers building recommendations. "
        "Every query parameter is optional; no match returns an empty list with total 0, not a 404.\n\n"
        "attr is a repeatable attribute filter in the form \u201cname:value\u201d, "
        "e.g. attr=use_case:office&attr=style:minimalist. Valid names are use_case, style, spec, "
        "size, color, brand and warranty; any other name returns 400 rather than an empty list. "
        "Values match case-insensitively as substrings, and several filters are ANDed together.\n\n"
        "Stock status is read live on every request and the response sets Cache-Control: no-store; "
        "nothing here is cached."
    ),
)
def search_products(
    response: Response,
    q: str | None = Query(
        default=None,
        description="Keyword, matched case-insensitively against product title and description.",
    ),
    min_price: Decimal | None = Query(
        default=None,
        ge=0,
        description="Minimum price, inclusive.",
    ),
    max_price: Decimal | None = Query(
        default=None,
        ge=0,
        description="Maximum price, inclusive.",
    ),
    attr: list[str] | None = Query(
        default=None,
        description="Attribute filter in the form \u201cname:value\u201d. Repeatable; several filters are ANDed together.",
    ),
    in_stock: bool | None = Query(
        default=None,
        description="true returns only in-stock products, false only out-of-stock ones; omit to skip the stock filter.",
    ),
    limit: int = Query(default=10, ge=1, le=50, description="Maximum number of results; defaults to 10, capped at 50."),
    offset: int = Query(default=0, ge=0, description="Result offset for paging; defaults to 0."),
    db: Session = Depends(get_db),
):
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid price range: the minimum {min_price} is greater than the maximum {max_price}.",
        )

    attr_filters = _parse_attr_filters(attr or [])

    conditions = []

    keyword = q.strip() if q else ""
    if keyword:
        pattern = _build_like_pattern(keyword)
        conditions.append(
            or_(
                Product.title.ilike(pattern, escape=_LIKE_ESCAPE_CHAR),
                Product.description.ilike(pattern, escape=_LIKE_ESCAPE_CHAR),
            )
        )
    if min_price is not None:
        conditions.append(Product.price >= min_price)
    if max_price is not None:
        conditions.append(Product.price <= max_price)
    for key, value in attr_filters:
        # JSON_UNQUOTE 取出来的值是 utf8mb4_bin，大小写敏感（LIKE '%type-c%' 匹配不到 Type-C），
        # 所以两边都套 LOWER() 才能做到忽略大小写。title/description 是 ai_ci 列，本身就不敏感，不需要。
        attribute_value = func.lower(
            func.json_unquote(func.json_extract(Product.attributes_json, _build_json_path(key)))
        )
        conditions.append(
            attribute_value.like(_build_like_pattern(value.lower()), escape=_LIKE_ESCAPE_CHAR)
        )

    if in_stock is True:
        conditions.append(Product.stock_quantity > 0)
    elif in_stock is False:
        conditions.append(Product.stock_quantity <= 0)

    matched = db.query(Product).filter(*conditions)
    total = matched.count()
    products = (
        matched.order_by(Product.price.asc(), Product.product_id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    # 3.3.3 要求库存实时查询不缓存，这里显式声明，避免中间层或浏览器把库存缓存住。
    response.headers["Cache-Control"] = "no-store"

    return _wrap(
        ProductSearchData(
            items=[
                ProductSearchItemData(
                    product_id=product.product_id,
                    title=product.title,
                    price=product.price,
                    cover_url=product.cover_url,
                    stock_status=product.stock_status,
                    stock_quantity=product.stock_quantity,
                    in_stock=_is_in_stock(product),
                    attributes=product.attributes_json or {},
                )
                for product in products
            ],
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(products) < total,
        )
    )


@router.get(
    "/products/{product_id}",
    response_model=ApiResponse,
    tags=["Products"],
    summary="Get product detail",
    description="Title, description, price, stock status and specifications for a product id.",
)
def product_detail(product_id: str, db: Session = Depends(get_db)):
    product = _get_product_or_404(db, product_id)
    return _wrap(
        ProductData(
            product_id=product.product_id,
            title=product.title,
            description=product.description,
            price=product.price,
            stock_status=product.stock_status,
            stock_quantity=product.stock_quantity,
            cover_url=product.cover_url,
            attributes=product.attributes_json or {},
        )
    )


@router.post(
    "/orders",
    response_model=ApiResponse,
    tags=["Orders"],
    summary="Create an order",
    description=(
        "Create an order and decrement stock. A new order starts as Awaiting payment \u2014 payment "
        "is outside this service, and its status is written back later by the business platform.\n\n"
        "**Idempotent**: the body must carry an idempotency_key. Repeating the same key produces one "
        "order only; the repeat returns the first result unchanged with data.idempotent_replay set "
        "to true, so callers can tell a new order from a duplicate submission.\n\n"
        "Insufficient stock, a missing product and a missing user all return 4xx and create no "
        "order \u2014 there is no in-between state where stock was taken but no order exists."
    ),
)
def create_order(body: CreateOrderRequest, db: Session = Depends(get_db)):
    # 1. 幂等：先看这个 key 是不是已经建过单。
    #    放在最前面，重复请求连库存都不碰。
    existing = (
        db.query(Order)
        .options(joinedload(Order.items).joinedload(OrderItem.product))
        .filter(Order.idempotency_key == body.idempotency_key)
        .first()
    )
    fingerprint = _request_fingerprint(body)
    if existing:
        # 键相同但内容不同：不能静默返回旧单，那等于告诉用户「下单成功」却下的是别的东西。
        # 报 409 让调用方换一个幂等键——购物车变了就是另一笔订单
        if existing.request_fingerprint and existing.request_fingerprint != fingerprint:
            raise HTTPException(
                status_code=409,
                # 不回显既有订单号：指纹不匹配意味着这个 key 可能属于另一个用户，
                # 把订单号写进错误信息等于把别人的订单号交出去
                detail=(
                    f"Idempotency key {body.idempotency_key} is already held by an order with "
                    "different contents. Use a new key whenever the buyer, the items or the "
                    "delivery details change."
                ),
            )
        return _wrap(_build_create_order_result(existing, replay=True))

    user = _get_user_or_404(db, body.user_id)

    # 2. 合并同一商品的多行，避免同一 SKU 分两行绕过库存校验
    wanted: dict[str, int] = {}
    for item in body.items:
        wanted[item.product_id] = wanted.get(item.product_id, 0) + item.quantity

    # 3. 锁行读取。with_for_update 是这里的关键：并发下单必须串行化到同一行上，
    #    否则两笔请求会各自读到「还有 1 件」然后各扣一件，把库存扣成负数。
    products = (
        db.query(Product)
        .filter(Product.product_id.in_(wanted.keys()))
        .with_for_update()
        .all()
    )
    found = {product.product_id: product for product in products}

    over_limit = sorted(pid for pid, qty in wanted.items() if qty > 99)
    if over_limit:
        raise HTTPException(
            status_code=422,
            detail=f"A single product is limited to 99 units; {', '.join(over_limit)} exceeds that.",
        )

    missing = sorted(set(wanted) - set(found))
    if missing:
        raise HTTPException(status_code=404, detail=f"Product {', '.join(missing)} does not exist.")

    # 4. 先全量校验库存，再统一扣减——避免扣到一半发现不够，留下一堆被扣的库存
    shortages = [
        f"{found[pid].title}（需要 {qty} 件，仅剩 {found[pid].stock_quantity} 件）"
        for pid, qty in wanted.items()
        if found[pid].stock_quantity < qty
    ]
    if shortages:
        raise HTTPException(status_code=409, detail=f"Insufficient stock: {'; '.join(shortages)}.")

    order_id = f"O{datetime.now():%Y%m%d%H%M%S}{uuid4().hex[:6].upper()}"
    created_at = datetime.now()
    order = Order(
        order_id=order_id,
        user_id=user.id,
        status=_NEW_ORDER_STATUS,
        status_desc="Order created, awaiting payment.",
        amount=Decimal("0.00"),
        created_at=created_at,
        receiver_name=body.receiver_name,
        receiver_phone_masked=_mask_phone(body.receiver_phone),
        receiver_address=body.receiver_address,
        delivery_method=body.delivery_method,
        idempotency_key=body.idempotency_key,
        request_fingerprint=fingerprint,
    )
    db.add(order)
    try:
        # INSERT 在 flush 时就发出，所以幂等键的唯一冲突会抛在这里而不是 commit。
        # 整段写入都要包进来，只包 commit 挡不住并发的重复提交
        db.flush()  # 拿到自增主键，供 order_items 的外键引用

        amount = Decimal("0.00")
        for item in body.items:
            product = found[item.product_id]
            amount += product.price * item.quantity
            db.add(
                OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    title_snapshot=product.title,  # 快照：商品改名后历史订单仍显示下单时的标题
                    quantity=item.quantity,
                    price=product.price,
                )
            )

        # 5. 扣减库存，并同步派生的展示值，不让两者说两套话
        for pid, qty in wanted.items():
            product = found[pid]
            product.stock_quantity -= qty
            product.stock_status = _stock_label(product.stock_quantity)

        order.amount = amount
        db.commit()
    except IntegrityError:
        # 并发下的同一幂等键：两个请求都没查到既有订单，于是都走到这里插入，
        # 唯一索引 uq_orders_idempotency_key 挡下了第二个。
        # 这不是错误——调用方要的那笔订单确实已经存在了，回滚后按幂等重放返回，
        # 不能把它变成 500 让调用方以为下单失败而重试（那才会真的下出第二笔）。
        db.rollback()
        winner = (
            db.query(Order)
            .options(joinedload(Order.items).joinedload(OrderItem.product))
            .filter(Order.idempotency_key == body.idempotency_key)
            .first()
        )
        if winner is None:
            # 唯一冲突却查不到那笔订单，说明冲突来自别的约束，不能当幂等重放吞掉
            raise
        if winner.request_fingerprint and winner.request_fingerprint != fingerprint:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Idempotency key {body.idempotency_key} is already held by an order with "
                    "different contents. Use a new key whenever the buyer, the items or the "
                    "delivery details change."
                ),
            )
        return _wrap(_build_create_order_result(winner, replay=True))

    db.refresh(order)

    return _wrap(_build_create_order_result(order, replay=False))


@router.post(
    "/orders/{order_id}/shipping-reminders",
    response_model=ApiResponse,
    tags=["Orders"],
    summary="Create a shipping reminder",
    description="Create a shipping reminder for an order. Only orders awaiting shipment or awaiting pickup may be reminded.",
)
def create_shipping_reminder(
    order_id: str,
    body: UrgeShippingRequest,
    db: Session = Depends(get_db),
):
    order = _get_order_or_404(db, order_id)
    if order.status not in _SHIPPABLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"The order is currently \u201c{order.status}\u201d, which is not a state a shipping reminder applies to.",
        )

    operation_id = f"U{datetime.now():%Y%m%d%H%M%S}{uuid4().hex[:6].upper()}"
    urge = ShippingUrgeRequest(
        urge_id=operation_id,
        order_id=order.id,
        operator=body.submitted_by,
        reason=body.note,
        status="submitted",
        status_desc="Shipping reminder created; the merchant will handle it shortly.",
        created_at=datetime.now(),
    )
    db.add(urge)
    db.commit()

    return _wrap(
        OperationResultData(
            request_type="shipping_reminder",
            request_id=operation_id,
            order_id=order.order_id,
            status="submitted",
            status_desc=urge.status_desc,
        )
    )


@router.post(
    "/orders/{order_id}/refund-applications",
    response_model=ApiResponse,
    tags=["Orders"],
    summary="Create a refund request",
    description="Create a refund request for an order. Returns a conflict if one is already in progress.",
)
def create_refund_application(
    order_id: str,
    body: RefundRequestBody,
    db: Session = Depends(get_db),
):
    order = _get_order_or_404(db, order_id)

    existing = (
        db.query(RefundRequest)
        .filter(RefundRequest.order_id == order.id)
        .order_by(RefundRequest.created_at.desc())
        .first()
    )
    if existing and existing.status in {"submitted", "processing"}:
        raise HTTPException(
            status_code=409,
            detail=f"Order {order_id} already has a refund request in progress.",
        )

    operation_id = f"R{datetime.now():%Y%m%d%H%M%S}{uuid4().hex[:6].upper()}"
    refund = RefundRequest(
        refund_id=operation_id,
        order_id=order.id,
        operator=body.submitted_by,
        reason=body.reason,
        status="submitted",
        status_desc="Refund request submitted and under review.",
        created_at=datetime.now(),
    )
    db.add(refund)
    db.commit()

    return _wrap(
        OperationResultData(
            request_type="refund_application",
            request_id=operation_id,
            order_id=order.order_id,
            status="submitted",
            status_desc=refund.status_desc,
        )
    )
