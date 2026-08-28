-- 给 commerce 库加两样东西，支撑规范 3.3.5 下单成交：
--   1. products.stock_quantity —— 真实库存数量。stock_status 保留为派生的展示值。
--   2. orders.idempotency_key  —— 创建订单的幂等键，同一个 key 只产生一笔订单。
-- 内容与 docker/mysql/init/01-schema.sql、02-seed.sql 一致，那两份是全新环境的事实来源。
--
-- 为什么是加列而不是把 stock_status 改成数量：加列可回滚、可先只读新列比对确认一致再切，
-- 替换是一次不可逆的破坏性变更，派生规则一旦写错（比如 0 到底算不算有货），旧值已经没了。
--
-- 幂等：整份脚本可重复执行。加列与建索引都先查 information_schema 再决定做不做
-- （MySQL 8 没有 ADD COLUMN IF NOT EXISTS），库存回填只在「本次刚新增该列」时执行一次，
-- 否则重跑会把卖掉的库存又填回去。
--
-- 用法（不要用 docker compose down -v：init 脚本从不创建 custom_service 库，
-- 清卷会连对话状态与 RAG 的表一起毁掉且无人重建）：
--   docker exec -i ecommerce-mysql mysql -uroot -proot123456 --default-character-set=utf8mb4 commerce \
--     < docker/mysql/migrations/2026-08-28-stock-quantity-and-order-idempotency.sql

USE commerce;

SET NAMES utf8mb4;

-- 1. products.stock_quantity
SET @has_stock_quantity := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = 'commerce' AND TABLE_NAME = 'products' AND COLUMN_NAME = 'stock_quantity'
);
SET @sql := IF(@has_stock_quantity = 0,
  'ALTER TABLE products ADD COLUMN stock_quantity INT NOT NULL DEFAULT 0 AFTER stock_status',
  'SELECT "products.stock_quantity 已存在，跳过" AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 2. 回填库存数量。只在刚刚新增该列时执行——重复执行会把已经卖掉的库存又填回去。
--    数量与 02-seed.sql 一致：缺货的四件为 0，其余按商品给不同量级
--    （既有充足的，也有个位数的，好让「仅剩 N 件」这类话术有素材）。
SET @sql := IF(@has_stock_quantity = 0,
  'UPDATE products SET stock_quantity = CASE product_id
     WHEN "p2001" THEN 42  WHEN "p2002" THEN 15  WHEN "p2003" THEN 30  WHEN "p2004" THEN 0
     WHEN "p2005" THEN 60  WHEN "p2006" THEN 88  WHEN "p2007" THEN 55  WHEN "p2008" THEN 24
     WHEN "p2009" THEN 9   WHEN "p2010" THEN 33  WHEN "p2011" THEN 12  WHEN "p2012" THEN 0
     WHEN "p2013" THEN 47  WHEN "p2014" THEN 26  WHEN "p2015" THEN 6   WHEN "p2016" THEN 120
     WHEN "p2017" THEN 38  WHEN "p2018" THEN 71  WHEN "p2019" THEN 0   WHEN "p2020" THEN 95
     WHEN "p2021" THEN 150 WHEN "p2022" THEN 4   WHEN "p2023" THEN 18  WHEN "p2024" THEN 0
     ELSE stock_quantity END',
  'SELECT "库存已回填过，跳过" AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 3. 让 stock_status 与数量对齐。这一步每次都跑：它是幂等的（同一份数量算出同一个标签），
--    而且能把此前手工改过、与数量对不上的展示值纠正回来。
UPDATE products SET stock_status = IF(stock_quantity > 0, '有货', '缺货');

-- 4. orders.idempotency_key
SET @has_idempotency_key := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = 'commerce' AND TABLE_NAME = 'orders' AND COLUMN_NAME = 'idempotency_key'
);
SET @sql := IF(@has_idempotency_key = 0,
  'ALTER TABLE orders ADD COLUMN idempotency_key VARCHAR(64) NULL AFTER receiver_address',
  'SELECT "orders.idempotency_key 已存在，跳过" AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 5. 幂等键的唯一索引。允许 NULL 是为了兼容既有订单（MySQL 唯一索引允许多个 NULL），
--    唯一性只约束真正带 key 的那些。没有这个索引，并发的重复提交会各自查不到再各自插入。
SET @has_idempotency_index := (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = 'commerce' AND TABLE_NAME = 'orders' AND INDEX_NAME = 'uq_orders_idempotency_key'
);
SET @sql := IF(@has_idempotency_index = 0,
  'ALTER TABLE orders ADD UNIQUE KEY uq_orders_idempotency_key (idempotency_key)',
  'SELECT "uq_orders_idempotency_key 已存在，跳过" AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
