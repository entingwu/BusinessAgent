---
name: commerce-api
description: 在 ecommerce-service-backend/ 里增删改业务中台的接口与数据表。用于商品检索接口、创建订单接口、库存字段改造、优惠券等一切「中台缺口」类任务。不要用它改对话后端。
---

你在 `ecommerce-service-backend/` 这个**独立服务**里工作。

## 最重要的一条

这个服务**不是 Agent 的一部分**。它是被 HTTP 调用的外部业务系统。

- 绝对不要 import `business_agent` 的任何东西
- 绝对不要读 `customer-service-backend/` 的代码来「保持一致」——两边的约定是**相反的**
- 你的产出是 HTTP 契约。调用方怎么用,不归你管

## 这个服务的约定(和对话后端不一样,别搞混)

| | 中台(你在的地方) | 对话后端(别碰) |
|---|---|---|
| 缩进 | **4 空格** | 2 空格 |
| SQLAlchemy | **同步** `Session` | async |
| 响应模型 | Pydantic + `ApiResponse` 信封 | dataclass |

其他约定,照 `app/api.py` 现有代码抄:

- 每个文件开头 `from __future__ import annotations`
- 所有响应过 `_wrap(data)`,包成 `ApiResponse{code, message, data}`
- 依赖注入用 `db: Session = Depends(get_db)`
- 路由装饰器写全 `response_model` / `tags` / `summary` / `description`,`summary` 和 `description` 用中文
- 404/409 用 `raise HTTPException(...)`,`detail` 用中文,带上业务 ID
- 取实体统一走 `_get_order_or_404` / `_get_product_or_404` / `_get_user_or_404`

## 写接口照这个模式抄

`create_refund_application`(`app/api.py`)是这个服务里写操作的范本:生成业务 ID → 落库 → `db.commit()` → 返回 `OperationResultData`。业务 ID 的格式是:

```python
operation_id = f"R{datetime.now():%Y%m%d%H%M%S}{uuid4().hex[:6].upper()}"
```

前缀字母按业务类型换。冲突检测(「已存在进行中的申请」)也照它写。

## 改表的话

Schema 在 `docker/mysql/init/01-schema.sql`,种子数据在 `02-seed.sql`。这两个 `.sql` **只在数据卷首次初始化时执行一次**——改了它们,已经起来的库不会变。要让改动生效必须:

```bash
docker compose -p ecommerce down -v    # -v 删数据卷
docker compose -p ecommerce up -d
```

`-v` 会清空数据。动手前先告诉用户这一步会清库,别自己决定。始终带 `-p ecommerce`,项目名不一致会另建一个空库。

同时改 `app/models.py` 的 ORM 定义,两边要对得上。

## 验证

```bash
docker compose -p ecommerce up -d
curl http://127.0.0.1:18081/health
curl 'http://127.0.0.1:18081/<你新加的路径>'    # 真调一次,别只看代码
```

没有测试框架。你的验证就是真发一次请求,把响应贴进报告。

## 交回什么

报告里给出:新增/修改的端点路径、完整的请求与响应 JSON 示例(真实调用的输出,不是你编的)、以及是否动过 `.sql`(动过就明说需要重建数据卷)。
