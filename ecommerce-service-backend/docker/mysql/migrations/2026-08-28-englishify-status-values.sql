-- 2026-08-28-englishify-status-values.sql
--
-- Englishify the last three Chinese columns: products.stock_status, orders.status and
-- logistics_records.status.
--
-- These are matching keys, not display text, which is why they were left Chinese by the earlier
-- englishification passes. Translating them is only safe because every place that compares
-- against them now accepts **both** spellings:
--
--   app/api.py            _SHIPPABLE_STATUSES holds the English and the Chinese values
--   App.vue               ORDER_STATUS_LABEL / ORDER_STATUS_CLASS key on both
--   recommend_products.py STOCK_STATUS_LABELS maps both
--
-- Accepting both is not transitional debris to be cleaned up later. Two other migrations write
-- stock_status (2026-08-27-unify-product-attributes.sql and the stock-quantity script), the
-- convention in this repo is to re-run a migration when in doubt, and other machines may hold a
-- database that never saw this file. A reader that understands only one spelling turns any of
-- those into a silent failure — the shipping-reminder endpoint returning 400 for every order
-- without raising anything is the concrete example.
--
-- The English values are not invented here: they are exactly the labels App.vue was already
-- showing for each Chinese key, so the UI is unchanged by this migration.
--
-- Idempotent: each UPDATE matches the Chinese value, so a second run touches zero rows. Re-running
-- it is also the repair after any migration writes the old values back.

USE commerce;
SET NAMES utf8mb4;

-- ---------- products.stock_status ----------
UPDATE products SET stock_status = 'In stock'     WHERE stock_status IN ('有货', '现货', '有库存');
UPDATE products SET stock_status = 'Out of stock' WHERE stock_status = '缺货';

-- ---------- orders.status ----------
UPDATE orders SET status = 'Awaiting payment'  WHERE status = '待支付';
UPDATE orders SET status = 'Awaiting shipment' WHERE status = '待发货';
UPDATE orders SET status = 'Awaiting pickup'   WHERE status = '待揽收';
UPDATE orders SET status = 'In transit'        WHERE status = '运输中';
UPDATE orders SET status = 'Out for delivery'  WHERE status = '派送中';
UPDATE orders SET status = 'Delivered'         WHERE status = '已签收';
UPDATE orders SET status = 'Completed'         WHERE status = '已完成';
UPDATE orders SET status = 'Cancelled'         WHERE status = '已取消';
UPDATE orders SET status = 'Refunding'         WHERE status = '退款中';
UPDATE orders SET status = 'Refunded'          WHERE status = '已退款';

-- ---------- logistics_records.status ----------
UPDATE logistics_records SET status = 'Awaiting pickup'  WHERE status = '待揽收';
UPDATE logistics_records SET status = 'In transit'       WHERE status = '运输中';
UPDATE logistics_records SET status = 'Out for delivery' WHERE status = '派送中';
UPDATE logistics_records SET status = 'Delivered'        WHERE status = '已签收';

-- ---------- orders.delivery_method ----------
UPDATE orders SET delivery_method = 'Standard shipping' WHERE delivery_method = '标准配送';
