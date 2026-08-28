-- 建表语句由 app/models.py 的 SQLAlchemy 模型推导而来
USE commerce;

SET NAMES utf8mb4;

DROP TABLE IF EXISTS shipping_urge_requests;
DROP TABLE IF EXISTS refund_requests;
DROP TABLE IF EXISTS logistics_traces;
DROP TABLE IF EXISTS logistics_records;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS users;

CREATE TABLE users (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    user_id       VARCHAR(64)  NOT NULL,
    nickname      VARCHAR(100) NOT NULL,
    level         VARCHAR(32)  NOT NULL,
    mobile_masked VARCHAR(32)  NOT NULL,
    created_at    DATETIME     NOT NULL,
    UNIQUE KEY uq_users_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE products (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    product_id      VARCHAR(64)    NOT NULL,
    title           VARCHAR(255)   NOT NULL,
    description     TEXT           NOT NULL,
    price           DECIMAL(10, 2) NOT NULL,
    -- stock_status 是面向展示的字符串（有货 / 缺货），stock_quantity 才是真实库存数量。
    -- 两者并存而不是替换：下单要扣减数量，而现有读接口与前端仍在用 stock_status 展示。
    -- 写入方必须同时维护两者，判定「有没有货」一律以 stock_quantity > 0 为准。
    stock_status    VARCHAR(32)    NOT NULL,
    stock_quantity  INT            NOT NULL DEFAULT 0,
    cover_url       VARCHAR(500)   NULL,
    attributes_json JSON           NOT NULL,
    created_at      DATETIME       NOT NULL,
    UNIQUE KEY uq_products_product_id (product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE orders (
    id                    INT AUTO_INCREMENT PRIMARY KEY,
    order_id              VARCHAR(64)    NOT NULL,
    user_id               INT            NOT NULL,
    status                VARCHAR(32)    NOT NULL,
    status_desc           VARCHAR(255)   NOT NULL,
    amount                DECIMAL(10, 2) NOT NULL,
    created_at            DATETIME       NOT NULL,
    receiver_name         VARCHAR(64)    NOT NULL,
    receiver_phone_masked VARCHAR(32)    NOT NULL,
    receiver_address      VARCHAR(255)   NOT NULL,
    delivery_method       VARCHAR(32)    NOT NULL DEFAULT '标准配送',
    -- 幂等键：同一个 idempotency_key 重复下单只会产生一笔订单。
    -- 允许 NULL 是为了兼容既有订单与非幂等来源；MySQL 的唯一索引允许多个 NULL。
    -- collation 必须是 utf8mb4_0900_bin：表默认的 utf8mb4_unicode_ci 不区分大小写、
    -- 又是 PAD SPACE，两个真正不同的键会互相吞掉，调用方会拿到别人的订单。
    -- 注意不能用 utf8mb4_bin——它区分大小写但**仍是 PAD SPACE**，尾空格照样撞车；
    -- 只有 UCA 9.0 那套（_0900_）是 NO PAD。
    idempotency_key       VARCHAR(64)    COLLATE utf8mb4_0900_bin NULL,
    -- 请求指纹：同一个幂等键换了购物车内容时用来识别，返回 409 而不是静默给旧单
    request_fingerprint   VARCHAR(64)    COLLATE utf8mb4_0900_bin NULL,
    UNIQUE KEY uq_orders_order_id (order_id),
    UNIQUE KEY uq_orders_idempotency_key (idempotency_key),
    KEY ix_orders_user_id (user_id),
    CONSTRAINT fk_orders_user FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE order_items (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    order_id       INT            NOT NULL,
    product_id     INT            NOT NULL,
    title_snapshot VARCHAR(255)   NOT NULL,
    quantity       INT            NOT NULL,
    price          DECIMAL(10, 2) NOT NULL,
    KEY ix_order_items_order_id (order_id),
    KEY ix_order_items_product_id (product_id),
    CONSTRAINT fk_order_items_order FOREIGN KEY (order_id) REFERENCES orders (id),
    CONSTRAINT fk_order_items_product FOREIGN KEY (product_id) REFERENCES products (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE logistics_records (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    order_id          INT          NOT NULL,
    logistics_company VARCHAR(64)  NOT NULL,
    tracking_number   VARCHAR(64)  NOT NULL,
    status            VARCHAR(32)  NOT NULL,
    status_desc       VARCHAR(255) NOT NULL,
    updated_at        DATETIME     NOT NULL,
    KEY ix_logistics_records_order_id (order_id),
    CONSTRAINT fk_logistics_records_order FOREIGN KEY (order_id) REFERENCES orders (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE logistics_traces (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    logistics_record_id INT          NOT NULL,
    trace_time          DATETIME     NOT NULL,
    trace_desc          VARCHAR(255) NOT NULL,
    KEY ix_logistics_traces_record_id (logistics_record_id),
    CONSTRAINT fk_logistics_traces_record FOREIGN KEY (logistics_record_id) REFERENCES logistics_records (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE refund_requests (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    refund_id   VARCHAR(64)  NOT NULL,
    order_id    INT          NOT NULL,
    operator    VARCHAR(64)  NOT NULL,
    reason      VARCHAR(255) NOT NULL,
    status      VARCHAR(32)  NOT NULL,
    status_desc VARCHAR(255) NOT NULL,
    created_at  DATETIME     NOT NULL,
    UNIQUE KEY uq_refund_requests_refund_id (refund_id),
    KEY ix_refund_requests_order_id (order_id),
    CONSTRAINT fk_refund_requests_order FOREIGN KEY (order_id) REFERENCES orders (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE shipping_urge_requests (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    urge_id     VARCHAR(64)  NOT NULL,
    order_id    INT          NOT NULL,
    operator    VARCHAR(64)  NOT NULL,
    reason      VARCHAR(255) NOT NULL,
    status      VARCHAR(32)  NOT NULL,
    status_desc VARCHAR(255) NOT NULL,
    created_at  DATETIME     NOT NULL,
    UNIQUE KEY uq_shipping_urge_requests_urge_id (urge_id),
    KEY ix_shipping_urge_requests_order_id (order_id),
    CONSTRAINT fk_shipping_urge_requests_order FOREIGN KEY (order_id) REFERENCES orders (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
