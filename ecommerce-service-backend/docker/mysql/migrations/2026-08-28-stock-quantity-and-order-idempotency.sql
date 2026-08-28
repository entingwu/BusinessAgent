-- 给 commerce 库加两样东西，支撑规范 3.3.5 下单成交：
--   1. products.stock_quantity —— 真实库存数量。stock_status 保留为派生的展示值。
--   2. orders.idempotency_key  —— 创建订单的幂等键，同一个 key 只产生一笔订单。
-- 内容与 docker/mysql/init/01-schema.sql、02-seed.sql 一致，那两份是全新环境的事实来源。
--
-- 为什么是加列而不是把 stock_status 改成数量：加列可回滚、可先只读新列比对确认一致再切，
-- 替换是一次不可逆的破坏性变更，派生规则一旦写错（比如 0 到底算不算有货），旧值已经没了。
--
-- 幂等且可中断重跑：加列与建索引先查 information_schema 再决定做不做
-- （MySQL 8 没有 ADD COLUMN IF NOT EXISTS）；库存回填按**数据状态**判断而不是
-- 「本次是否刚建列」——后者在「列已加、回填未跑」时中断会导致重跑永久跳过回填，
-- 而对齐展示值那步照跑，把唯一的事实源一起抹掉。详见第 2 步的注释。
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

-- 2. 回填库存数量。**守卫按数据状态判断，不按「本次是否刚建列」判断。**
--    前一版用 @has_stock_quantity = 0 作条件，有个会摧毁数据的中间态：
--    脚本若在 ALTER 之后、回填之前中断，重跑时回填被永久跳过，而第 3 步照跑，
--    把仅存的事实源 stock_status 一起覆盖成「缺货」——24 件商品全部归零且不可恢复。
--    改成逐行判断「数量为 0 但展示值仍标着有货」才回填，中断后重跑能自愈：
--      · 刚建列（全 0，stock_status 仍是原值）→ 有货的那些被回填
--      · 真的卖光（数量 0，stock_status 已同步为缺货）→ 不回填，不会把卖掉的货变回来
--    数量与 02-seed.sql 一致：缺货的四件为 0，其余按商品给不同量级
--    （既有充足的，也有个位数的，好让「仅剩 N 件」这类话术有素材）。
UPDATE products SET stock_quantity = CASE product_id
    WHEN 'p2001' THEN 42  WHEN 'p2002' THEN 15  WHEN 'p2003' THEN 30  WHEN 'p2004' THEN 0
    WHEN 'p2005' THEN 60  WHEN 'p2006' THEN 88  WHEN 'p2007' THEN 55  WHEN 'p2008' THEN 24
    WHEN 'p2009' THEN 9   WHEN 'p2010' THEN 33  WHEN 'p2011' THEN 12  WHEN 'p2012' THEN 0
    WHEN 'p2013' THEN 47  WHEN 'p2014' THEN 26  WHEN 'p2015' THEN 6   WHEN 'p2016' THEN 120
    WHEN 'p2017' THEN 38  WHEN 'p2018' THEN 71  WHEN 'p2019' THEN 0   WHEN 'p2020' THEN 95
    WHEN 'p2021' THEN 150 WHEN 'p2022' THEN 4   WHEN 'p2023' THEN 18  WHEN 'p2024' THEN 0
    ELSE stock_quantity END
WHERE stock_quantity = 0 AND stock_status IN ('有货', '现货', '有库存');

-- 3. 让 stock_status 与数量对齐。放在回填之后，且回填是自愈的，
--    所以这一步不会再在中断后覆盖掉唯一的事实源。
--
--    这行写死了中文字面量，且是无条件 UPDATE：任何把 stock_status 翻成英文的改动，
--    只要之后有人重跑这个脚本（脚本被设计成可重跑，没有版本表拦着），就会被这行改回中文。
--    展示值一律走前端映射、数据库保持中文，是有意为之——见 app/api.py 的 _SHIPPABLE_STATUSES。
UPDATE products SET stock_status = IF(stock_quantity > 0, '有货', '缺货');

-- 4. orders.idempotency_key
SET @has_idempotency_key := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = 'commerce' AND TABLE_NAME = 'orders' AND COLUMN_NAME = 'idempotency_key'
);
SET @sql := IF(@has_idempotency_key = 0,
  'ALTER TABLE orders ADD COLUMN idempotency_key VARCHAR(64) COLLATE utf8mb4_0900_bin NULL AFTER receiver_address',
  'SELECT "orders.idempotency_key 已存在，跳过" AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 5. 幂等键的唯一索引。允许 NULL 是为了兼容既有订单（MySQL 唯一索引允许多个 NULL），
--    唯一性只约束真正带 key 的那些。没有这个索引，并发的重复提交会各自查不到再各自插入。
SET @has_idempotency_index := (
  SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = 'commerce' AND TABLE_NAME = 'orders' AND INDEX_NAME = 'uq_orders_idempotency_key'
);
-- 幂等键必须区分大小写与尾空格。表默认的 utf8mb4_unicode_ci 既不区分大小写、
-- 又是 PAD SPACE，两个真正不同的键会互相吞掉，调用方拿到别人的订单还被标成重放。
-- 必须用 utf8mb4_0900_bin 而不是 utf8mb4_bin：后者区分大小写但仍是 PAD SPACE，
-- 尾空格照样撞车（实测「CaseKey-Aa01 」会命中「CaseKey-Aa01」的订单）。
-- 这一步把既有列（如果是早先版本建的）纠正过来，本身幂等。
SET @key_collation := (
  SELECT COLLATION_NAME FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = 'commerce' AND TABLE_NAME = 'orders' AND COLUMN_NAME = 'idempotency_key'
);
SET @sql := IF(@key_collation IS NOT NULL AND @key_collation <> 'utf8mb4_0900_bin',
  'ALTER TABLE orders MODIFY COLUMN idempotency_key VARCHAR(64) COLLATE utf8mb4_0900_bin NULL',
  'SELECT "idempotency_key 已是 utf8mb4_0900_bin，跳过" AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := IF(@has_idempotency_index = 0,
  'ALTER TABLE orders ADD UNIQUE KEY uq_orders_idempotency_key (idempotency_key)',
  'SELECT "uq_orders_idempotency_key 已存在，跳过" AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 6. orders.delivery_method
--    配送方式此前是塞进 status_desc 的中文句子里再反解出来的。那不成立：
--    中台回写支付状态会重写 status_desc，配送方式随之丢失，幂等重放拿到的就是错的。
--    存成独立列。
SET @has_delivery_method := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = 'commerce' AND TABLE_NAME = 'orders' AND COLUMN_NAME = 'delivery_method'
);
SET @sql := IF(@has_delivery_method = 0,
  'ALTER TABLE orders ADD COLUMN delivery_method VARCHAR(32) NOT NULL DEFAULT "标准配送" AFTER receiver_address',
  'SELECT "orders.delivery_method 已存在，跳过" AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 7. orders.request_fingerprint
--    幂等键之外还要记请求指纹。只认键不认内容的话，用户在会话中途改了购物车、
--    而幂等键没变，就会静默拿到旧订单还被告知「下单成功」。
--    有了指纹就能区分「重复提交同一笔」（返回旧单）和「同一个键换了内容」（报 409）。
SET @has_fingerprint := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = 'commerce' AND TABLE_NAME = 'orders' AND COLUMN_NAME = 'request_fingerprint'
);
SET @sql := IF(@has_fingerprint = 0,
  'ALTER TABLE orders ADD COLUMN request_fingerprint VARCHAR(64) COLLATE utf8mb4_0900_bin NULL AFTER idempotency_key',
  'SELECT "orders.request_fingerprint 已存在，跳过" AS note');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
