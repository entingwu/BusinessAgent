from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, text
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

# products.stock_status 目前是字符串枚举（如“有货”“缺货”），不是库存数量。
# 这里用白名单判定“有货”，未知的新状态一律视为不可售，避免把缺货商品推荐给用户。
_IN_STOCK_STATUSES = ("有货", "现货", "有库存")

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


def _is_in_stock(stock_status: str) -> bool:
    """按白名单判定“有货”。把语义收在中台，调用方不必自己猜 VARCHAR 字符串的含义。"""
    return stock_status in _IN_STOCK_STATUSES


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
                detail=f"属性过滤条件 “{raw}” 格式不正确，正确格式为 “属性名:属性值”，例如 attr=use_case:办公。",
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
        raise HTTPException(status_code=404, detail=f"用户 {user_id} 不存在。")
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
            title=order.items[0].title_snapshot if order.items else "未知商品",
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


def _get_order_or_404(db: Session, order_id: str) -> Order:
    order = (
        db.query(Order)
        .options(joinedload(Order.items).joinedload(OrderItem.product))
        .filter(Order.order_id == order_id)
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail=f"订单 {order_id} 不存在。")
    return order


def _get_product_or_404(db: Session, product_id: str) -> Product:
    product = db.query(Product).filter(Product.product_id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail=f"商品 {product_id} 不存在。")
    return product


@router.get(
    "/health",
    response_model=ApiResponse,
    tags=["系统"],
    summary="健康检查",
    description="用于检查服务和数据库连接是否正常。",
)
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return _wrap({"status": "ok"})


@router.get(
    "/users/{user_id}/orders",
    response_model=ApiResponse,
    tags=["用户"],
    summary="查询用户最近订单列表",
    description="根据用户 ID 查询最近订单，用于前端展示订单对象列表。",
)
def user_orders(user_id: str, db: Session = Depends(get_db)):
    user = _get_user_or_404(db, user_id)
    return _wrap(UserOrdersData(user_id=user.user_id, orders=_build_recent_orders(db, user)))


@router.get(
    "/users/{user_id}/products",
    response_model=ApiResponse,
    tags=["用户"],
    summary="查询用户最近商品列表",
    description="根据用户 ID 查询最近购买或关联过的商品，用于前端展示商品对象列表。",
)
def user_products(user_id: str, db: Session = Depends(get_db)):
    user = _get_user_or_404(db, user_id)
    return _wrap(UserProductsData(user_id=user.user_id, products=_build_recent_products(db, user)))


@router.get(
    "/orders/{order_id}",
    response_model=ApiResponse,
    tags=["订单"],
    summary="查询订单详情",
    description="根据订单 ID 查询订单主信息、收货信息以及订单商品明细。",
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
    tags=["订单"],
    summary="查询订单状态",
    description="返回订单当前状态及面向用户展示的状态说明。",
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
    tags=["订单"],
    summary="查询订单物流信息",
    description="返回物流公司、运单号、当前物流状态和物流轨迹。",
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
        raise HTTPException(status_code=404, detail=f"订单 {order_id} 暂无物流信息。")

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
    tags=["商品"],
    summary="按条件检索商品",
    description=(
        "按关键词、价格区间、商品属性和库存状态检索商品，返回分页后的候选商品列表与匹配总数，"
        "供上层按用户偏好做商品推荐。所有查询参数均可选；没有匹配商品时返回空列表且 total 为 0，不返回 404。\n\n"
        "attr 为可重复传入的属性过滤条件，格式 “属性名:属性值”（如 attr=use_case:办公&attr=style:极简）。"
        "可用的属性名为 use_case（用途）、style（风格）、spec（规格）、size（尺码）、color（颜色）、"
        "brand（品牌）、warranty（保修），传其他属性名返回 400 而不是空列表。"
        "属性值为忽略大小写的模糊匹配，多个条件之间是「与」的关系。\n\n"
        "库存状态每次实时读库，响应显式声明 Cache-Control: no-store，不做任何缓存。"
    ),
)
def search_products(
    response: Response,
    q: str | None = Query(
        default=None,
        description="关键词，对商品标题与描述做模糊匹配（不区分大小写）。",
    ),
    min_price: Decimal | None = Query(
        default=None,
        ge=0,
        description="价格下限，闭区间（含等于）。",
    ),
    max_price: Decimal | None = Query(
        default=None,
        ge=0,
        description="价格上限，闭区间（含等于）。",
    ),
    attr: list[str] | None = Query(
        default=None,
        description="属性过滤条件，格式 “属性名:属性值”，可重复传入，多个条件之间是「与」的关系。",
    ),
    in_stock: bool | None = Query(
        default=None,
        description="true 只返回有货商品，false 只返回非有货商品，不传则不按库存过滤。",
    ),
    limit: int = Query(default=10, ge=1, le=50, description="本次返回的最大条数，默认 10，上限 50。"),
    offset: int = Query(default=0, ge=0, description="结果偏移量，用于翻页，默认 0。"),
    db: Session = Depends(get_db),
):
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(
            status_code=400,
            detail=f"价格区间不合法：价格下限 {min_price} 大于价格上限 {max_price}。",
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
        conditions.append(Product.stock_status.in_(_IN_STOCK_STATUSES))
    elif in_stock is False:
        conditions.append(Product.stock_status.notin_(_IN_STOCK_STATUSES))

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
                    in_stock=_is_in_stock(product.stock_status),
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
    tags=["商品"],
    summary="查询商品详情",
    description="根据商品 ID 查询商品标题、描述、价格、库存状态和规格参数。",
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
            cover_url=product.cover_url,
            attributes=product.attributes_json or {},
        )
    )


@router.post(
    "/orders/{order_id}/shipping-reminders",
    response_model=ApiResponse,
    tags=["订单"],
    summary="创建发货提醒",
    description="为指定订单创建一条发货提醒请求。当前仅允许对待发货或待揽收订单发起提醒。",
)
def create_shipping_reminder(
    order_id: str,
    body: UrgeShippingRequest,
    db: Session = Depends(get_db),
):
    order = _get_order_or_404(db, order_id)
    if order.status not in {"待发货", "待揽收"}:
        raise HTTPException(
            status_code=400,
            detail=f"订单当前状态为“{order.status}”，当前不适合再次发起发货提醒。",
        )

    operation_id = f"U{datetime.now():%Y%m%d%H%M%S}{uuid4().hex[:6].upper()}"
    urge = ShippingUrgeRequest(
        urge_id=operation_id,
        order_id=order.id,
        operator=body.submitted_by,
        reason=body.note,
        status="submitted",
        status_desc="发货提醒已创建，商家会尽快处理。",
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
    tags=["订单"],
    summary="创建退款申请",
    description="为指定订单创建退款申请。如果订单已有进行中的退款申请，将返回冲突错误。",
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
            detail=f"订单 {order_id} 已存在进行中的退款申请。",
        )

    operation_id = f"R{datetime.now():%Y%m%d%H%M%S}{uuid4().hex[:6].upper()}"
    refund = RefundRequest(
        refund_id=operation_id,
        order_id=order.id,
        operator=body.submitted_by,
        reason=body.reason,
        status="submitted",
        status_desc="退款申请已提交，正在审核中。",
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
