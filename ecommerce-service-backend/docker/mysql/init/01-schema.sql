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
    stock_status    VARCHAR(32)    NOT NULL,
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
    UNIQUE KEY uq_orders_order_id (order_id),
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
