-- 把现有 commerce 库的商品数据升级到统一属性 key 的版本，并补齐扩充商品。
-- 内容与 docker/mysql/init/02-seed.sql 完全一致，由同一份数据生成。
--
-- 为什么不是 TRUNCATE + 重新 INSERT：order_items 有外键 fk_order_items_product
-- 指向 products.id，TRUNCATE 会被外键直接挡下；DELETE 则会连带破坏历史订单的商品关联。
-- 所以 p2001-p2006 走 UPDATE（id / product_id / 标题 / 价格 / 库存一律不动，只重写属性），
-- 新增商品走 INSERT ... ON DUPLICATE KEY UPDATE，整份脚本可以重复执行。
--
-- 用法（不要用 docker compose down -v：init 脚本从不创建 custom_service 库，
-- 清卷会连对话状态与 RAG 的表一起毁掉且无人重建）：
--   docker exec -i ecommerce-mysql mysql -uroot -proot123456 commerce \
--     < docker/mysql/migrations/2026-08-27-unify-product-attributes.sql

USE commerce;

SET NAMES utf8mb4;

-- 1. 存量商品：只重写 attributes_json，其余字段保持不动
UPDATE products SET attributes_json = '{"use_case": "办公", "style": "极简", "spec": "静音红轴 / 87 键 / 三模连接（有线+蓝牙+2.4G）/ 全键无冲", "size": "标准", "color": "黑", "brand": "简物", "warranty": "两年"}' WHERE product_id = 'p2001';
UPDATE products SET attributes_json = '{"use_case": "办公", "style": "商务", "spec": "高密度网布 / 承重 150kg / 座高·腰托·扶手可调", "size": "大号", "color": "黑", "brand": "坐望", "warranty": "三年"}' WHERE product_id = 'p2002';
UPDATE products SET attributes_json = '{"use_case": "差旅", "style": "极简", "spec": "混合式主动降噪 42dB / 8h+22h 续航 / 蓝牙 5.3 / IPX4", "size": "小号", "color": "白", "brand": "听野", "warranty": "一年"}' WHERE product_id = 'p2003';
UPDATE products SET attributes_json = '{"use_case": "办公", "style": "商务", "spec": "15.6 英寸 / 1920x1080 IPS / Type-C x2 + mini HDMI / 780g", "size": "标准", "color": "深空灰", "brand": "视界", "warranty": "两年"}' WHERE product_id = 'p2004';
UPDATE products SET attributes_json = '{"use_case": "居家", "style": "北欧", "spec": "2700K-6500K 无极调色 / Ra95 / App·语音·触控 / 12W", "size": "小号", "color": "米白", "brand": "木言", "warranty": "一年"}' WHERE product_id = 'p2005';
UPDATE products SET attributes_json = '{"use_case": "差旅", "style": "极简", "spec": "65W / 2C1A / PD3.0+QC4+ / 折叠插脚", "size": "小号", "color": "白", "brand": "简物", "warranty": "一年"}' WHERE product_id = 'p2006';

-- 2. 扩充商品：幂等写入，重复执行不会产生重复行
INSERT INTO products (id, product_id, title, description, price, stock_status, cover_url, attributes_json, created_at) VALUES
(7, 'p2007', '静音无线鼠标',
 '静音微动按键，2.4G 与蓝牙双模，一键切换三台设备，适合开放式办公区。',
 129.00, '有货', 'https://picsum.photos/seed/p2007/400/400',
 '{"use_case": "办公", "style": "极简", "spec": "静音微动 / 2.4G+蓝牙双模 / 1600DPI / 三设备切换", "size": "标准", "color": "白", "brand": "简物", "warranty": "一年"}',
 '2025-01-15 12:00:00')
ON DUPLICATE KEY UPDATE
  title = VALUES(title), description = VALUES(description), price = VALUES(price),
  stock_status = VALUES(stock_status), cover_url = VALUES(cover_url),
  attributes_json = VALUES(attributes_json), created_at = VALUES(created_at);
INSERT INTO products (id, product_id, title, description, price, stock_status, cover_url, attributes_json, created_at) VALUES
(8, 'p2008', '轻量化电竞鼠标',
 '59g 蜂巢轻量机身，26000DPI 光学引擎，1000Hz 回报率，配编织伞绳线。',
 349.00, '有货', 'https://picsum.photos/seed/p2008/400/400',
 '{"use_case": "游戏", "style": "电竞", "spec": "26000DPI / 59g 轻量 / 1000Hz 回报率", "size": "标准", "color": "黑", "brand": "雷动", "warranty": "两年"}',
 '2025-01-22 12:00:00')
ON DUPLICATE KEY UPDATE
  title = VALUES(title), description = VALUES(description), price = VALUES(price),
  stock_status = VALUES(stock_status), cover_url = VALUES(cover_url),
  attributes_json = VALUES(attributes_json), created_at = VALUES(created_at);
INSERT INTO products (id, product_id, title, description, price, stock_status, cover_url, attributes_json, created_at) VALUES
(9, 'p2009', '全尺寸机械键盘 104 键',
 '铝合金一体成型外壳，热插拔轴座，RGB 背光，Gasket 结构填充。',
 899.00, '有货', 'https://picsum.photos/seed/p2009/400/400',
 '{"use_case": "游戏", "style": "电竞", "spec": "茶轴 / 104 键 / 热插拔 / RGB 背光 / Gasket 结构", "size": "大号", "color": "黑", "brand": "雷动", "warranty": "两年"}',
 '2025-02-01 12:00:00')
ON DUPLICATE KEY UPDATE
  title = VALUES(title), description = VALUES(description), price = VALUES(price),
  stock_status = VALUES(stock_status), cover_url = VALUES(cover_url),
  attributes_json = VALUES(attributes_json), created_at = VALUES(created_at);
INSERT INTO products (id, product_id, title, description, price, stock_status, cover_url, attributes_json, created_at) VALUES
(10, 'p2010', '便携蓝牙音箱',
 'IPX7 级防水，12 小时续航，支持双设备串联立体声，附挂绳。',
 299.00, '有货', 'https://picsum.photos/seed/p2010/400/400',
 '{"use_case": "运动", "style": "运动", "spec": "IPX7 防水 / 12h 续航 / 双机串联立体声", "size": "小号", "color": "深蓝", "brand": "听野", "warranty": "一年"}',
 '2025-02-10 12:00:00')
ON DUPLICATE KEY UPDATE
  title = VALUES(title), description = VALUES(description), price = VALUES(price),
  stock_status = VALUES(stock_status), cover_url = VALUES(cover_url),
  attributes_json = VALUES(attributes_json), created_at = VALUES(created_at);
INSERT INTO products (id, product_id, title, description, price, stock_status, cover_url, attributes_json, created_at) VALUES
(11, 'p2011', '4K 显示器 27 英寸',
 '27 英寸 4K IPS 面板，出厂校色 ΔE<2，Type-C 一线连接并反向供电 90W。',
 2499.00, '有货', 'https://picsum.photos/seed/p2011/400/400',
 '{"use_case": "办公", "style": "商务", "spec": "27 英寸 / 3840x2160 IPS / Type-C 90W 反向供电 / ΔE<2", "size": "大号", "color": "银", "brand": "视界", "warranty": "三年"}',
 '2025-02-18 12:00:00')
ON DUPLICATE KEY UPDATE
  title = VALUES(title), description = VALUES(description), price = VALUES(price),
  stock_status = VALUES(stock_status), cover_url = VALUES(cover_url),
  attributes_json = VALUES(attributes_json), created_at = VALUES(created_at);
INSERT INTO products (id, product_id, title, description, price, stock_status, cover_url, attributes_json, created_at) VALUES
(12, 'p2012', '带鱼屏曲面显示器 34 英寸',
 '34 英寸 21:9 曲面，165Hz 刷新率，1ms 响应，支持 HDR400。',
 3999.00, '缺货', 'https://picsum.photos/seed/p2012/400/400',
 '{"use_case": "游戏", "style": "电竞", "spec": "34 英寸 / 3440x1440 / 165Hz / 1ms / HDR400", "size": "大号", "color": "黑", "brand": "视界", "warranty": "三年"}',
 '2025-02-25 12:00:00')
ON DUPLICATE KEY UPDATE
  title = VALUES(title), description = VALUES(description), price = VALUES(price),
  stock_status = VALUES(stock_status), cover_url = VALUES(cover_url),
  attributes_json = VALUES(attributes_json), created_at = VALUES(created_at);
INSERT INTO products (id, product_id, title, description, price, stock_status, cover_url, attributes_json, created_at) VALUES
(13, 'p2013', '铝合金笔记本支架',
 '六档角度调节，承重 8kg，全铝散热机身，底部硅胶防滑垫。',
 159.00, '有货', 'https://picsum.photos/seed/p2013/400/400',
 '{"use_case": "办公", "style": "极简", "spec": "六档调节 / 承重 8kg / 全铝散热", "size": "标准", "color": "银", "brand": "简物", "warranty": "一年"}',
 '2025-03-04 12:00:00')
ON DUPLICATE KEY UPDATE
  title = VALUES(title), description = VALUES(description), price = VALUES(price),
  stock_status = VALUES(stock_status), cover_url = VALUES(cover_url),
  attributes_json = VALUES(attributes_json), created_at = VALUES(created_at);
INSERT INTO products (id, product_id, title, description, price, stock_status, cover_url, attributes_json, created_at) VALUES
(14, 'p2014', '通勤双肩背包 25L',
 '15.6 英寸笔记本独立隔层，防泼水面料，侧开速取口袋，可套拉杆箱。',
 399.00, '有货', 'https://picsum.photos/seed/p2014/400/400',
 '{"use_case": "差旅", "style": "商务", "spec": "25L / 15.6 英寸隔层 / 防泼水 / 拉杆带", "size": "大号", "color": "深灰", "brand": "山径", "warranty": "两年"}',
 '2025-03-12 12:00:00')
ON DUPLICATE KEY UPDATE
  title = VALUES(title), description = VALUES(description), price = VALUES(price),
  stock_status = VALUES(stock_status), cover_url = VALUES(cover_url),
  attributes_json = VALUES(attributes_json), created_at = VALUES(created_at);
INSERT INTO products (id, product_id, title, description, price, stock_status, cover_url, attributes_json, created_at) VALUES
(15, 'p2015', '头层牛皮公文包',
 '植鞣头层牛皮，14 英寸笔记本隔层，可拆卸肩带，黄铜五金。',
 1299.00, '有货', 'https://picsum.photos/seed/p2015/400/400',
 '{"use_case": "差旅", "style": "商务", "spec": "头层牛皮 / 14 英寸隔层 / 可拆卸肩带", "size": "标准", "color": "棕", "brand": "山径", "warranty": "两年"}',
 '2025-03-20 12:00:00')
ON DUPLICATE KEY UPDATE
  title = VALUES(title), description = VALUES(description), price = VALUES(price),
  stock_status = VALUES(stock_status), cover_url = VALUES(cover_url),
  attributes_json = VALUES(attributes_json), created_at = VALUES(created_at);
INSERT INTO products (id, product_id, title, description, price, stock_status, cover_url, attributes_json, created_at) VALUES
(16, 'p2016', '纯棉圆领 T 恤',
 '260g 精梳棉，双纱领口不易变形，肩线落肩 1cm，四季可穿。',
 99.00, '有货', 'https://picsum.photos/seed/p2016/400/400',
 '{"use_case": "居家", "style": "极简", "spec": "260g 精梳棉 / 圆领 / 常规版型", "size": "M", "color": "白", "brand": "素野", "warranty": "无"}',
 '2025-03-28 12:00:00')
ON DUPLICATE KEY UPDATE
  title = VALUES(title), description = VALUES(description), price = VALUES(price),
  stock_status = VALUES(stock_status), cover_url = VALUES(cover_url),
  attributes_json = VALUES(attributes_json), created_at = VALUES(created_at);
INSERT INTO products (id, product_id, title, description, price, stock_status, cover_url, attributes_json, created_at) VALUES
(17, 'p2017', '双面抓绒卫衣',
 '双面抓绒面料，落肩宽松版型，罗纹下摆，内里起绒保暖。',
 259.00, '有货', 'https://picsum.photos/seed/p2017/400/400',
 '{"use_case": "居家", "style": "复古", "spec": "双面抓绒 / 落肩宽松 / 罗纹下摆", "size": "L", "color": "藏青", "brand": "素野", "warranty": "无"}',
 '2025-04-06 12:00:00')
ON DUPLICATE KEY UPDATE
  title = VALUES(title), description = VALUES(description), price = VALUES(price),
  stock_status = VALUES(stock_status), cover_url = VALUES(cover_url),
  attributes_json = VALUES(attributes_json), created_at = VALUES(created_at);
INSERT INTO products (id, product_id, title, description, price, stock_status, cover_url, attributes_json, created_at) VALUES
(18, 'p2018', '跑步运动短裤',
 '弹力速干面料，内置压缩衬里，后腰拉链口袋，反光条夜跑可见。',
 139.00, '有货', 'https://picsum.photos/seed/p2018/400/400',
 '{"use_case": "运动", "style": "运动", "spec": "弹力速干 / 内置压缩衬里 / 反光条", "size": "M", "color": "黑", "brand": "素野", "warranty": "无"}',
 '2025-04-14 12:00:00')
ON DUPLICATE KEY UPDATE
  title = VALUES(title), description = VALUES(description), price = VALUES(price),
  stock_status = VALUES(stock_status), cover_url = VALUES(cover_url),
  attributes_json = VALUES(attributes_json), created_at = VALUES(created_at);
INSERT INTO products (id, product_id, title, description, price, stock_status, cover_url, attributes_json, created_at) VALUES
(19, 'p2019', '冰感速干运动 T 恤',
 '接触冰凉面料，UPF50+ 防晒，腋下网眼透气拼接。',
 119.00, '缺货', 'https://picsum.photos/seed/p2019/400/400',
 '{"use_case": "运动", "style": "运动", "spec": "冰感速干 / UPF50+ / 网眼透气", "size": "L", "color": "荧光绿", "brand": "素野", "warranty": "无"}',
 '2025-04-22 12:00:00')
ON DUPLICATE KEY UPDATE
  title = VALUES(title), description = VALUES(description), price = VALUES(price),
  stock_status = VALUES(stock_status), cover_url = VALUES(cover_url),
  attributes_json = VALUES(attributes_json), created_at = VALUES(created_at);
INSERT INTO products (id, product_id, title, description, price, stock_status, cover_url, attributes_json, created_at) VALUES
(20, 'p2020', '316 不锈钢保温杯 500ml',
 '316 医用级不锈钢内胆，六小时保温 60℃ 以上，一键弹盖，杯口不烫嘴。',
 89.00, '有货', 'https://picsum.photos/seed/p2020/400/400',
 '{"use_case": "差旅", "style": "北欧", "spec": "500ml / 316 不锈钢 / 保温 12 小时 / 一键弹盖", "size": "小号", "color": "米白", "brand": "木言", "warranty": "一年"}',
 '2025-05-06 12:00:00')
ON DUPLICATE KEY UPDATE
  title = VALUES(title), description = VALUES(description), price = VALUES(price),
  stock_status = VALUES(stock_status), cover_url = VALUES(cover_url),
  attributes_json = VALUES(attributes_json), created_at = VALUES(created_at);
INSERT INTO products (id, product_id, title, description, price, stock_status, cover_url, attributes_json, created_at) VALUES
(21, 'p2021', '陶瓷马克杯 350ml',
 '高白泥胎体，釉下彩不脱色，可微波炉与洗碗机，杯柄贴合手型。',
 39.00, '有货', 'https://picsum.photos/seed/p2021/400/400',
 '{"use_case": "居家", "style": "北欧", "spec": "350ml / 釉下彩 / 可微波", "size": "小号", "color": "奶油白", "brand": "木言", "warranty": "无"}',
 '2025-05-14 12:00:00')
ON DUPLICATE KEY UPDATE
  title = VALUES(title), description = VALUES(description), price = VALUES(price),
  stock_status = VALUES(stock_status), cover_url = VALUES(cover_url),
  attributes_json = VALUES(attributes_json), created_at = VALUES(created_at);
INSERT INTO products (id, product_id, title, description, price, stock_status, cover_url, attributes_json, created_at) VALUES
(22, 'p2022', '实木书桌 1.2 米',
 '橡木实木桌面，可穿线理线孔，横梁加固，承重 100kg，需自行安装。',
 1599.00, '有货', 'https://picsum.photos/seed/p2022/400/400',
 '{"use_case": "办公", "style": "北欧", "spec": "1.2m x 0.6m / 橡木实木 / 承重 100kg / 理线孔", "size": "大号", "color": "原木", "brand": "木言", "warranty": "五年"}',
 '2025-05-22 12:00:00')
ON DUPLICATE KEY UPDATE
  title = VALUES(title), description = VALUES(description), price = VALUES(price),
  stock_status = VALUES(stock_status), cover_url = VALUES(cover_url),
  attributes_json = VALUES(attributes_json), created_at = VALUES(created_at);
INSERT INTO products (id, product_id, title, description, price, stock_status, cover_url, attributes_json, created_at) VALUES
(23, 'p2023', '落地阅读灯',
 'Ra95 高显色灯珠，无极调光调色，护眼无频闪，落地式可移动。',
 459.00, '有货', 'https://picsum.photos/seed/p2023/400/400',
 '{"use_case": "居家", "style": "北欧", "spec": "Ra95 / 无极调光调色 / 无频闪 / 落地式", "size": "大号", "color": "米白", "brand": "木言", "warranty": "两年"}',
 '2025-06-03 12:00:00')
ON DUPLICATE KEY UPDATE
  title = VALUES(title), description = VALUES(description), price = VALUES(price),
  stock_status = VALUES(stock_status), cover_url = VALUES(cover_url),
  attributes_json = VALUES(attributes_json), created_at = VALUES(created_at);
INSERT INTO products (id, product_id, title, description, price, stock_status, cover_url, attributes_json, created_at) VALUES
(24, 'p2024', '静音香薰加湿器',
 '300ml 容量，超声波静音雾化，缺水自动断电，可加精油。',
 219.00, '缺货', 'https://picsum.photos/seed/p2024/400/400',
 '{"use_case": "居家", "style": "极简", "spec": "300ml / 超声波静音雾化 / 缺水断电", "size": "小号", "color": "白", "brand": "木言", "warranty": "一年"}',
 '2025-06-11 12:00:00')
ON DUPLICATE KEY UPDATE
  title = VALUES(title), description = VALUES(description), price = VALUES(price),
  stock_status = VALUES(stock_status), cover_url = VALUES(cover_url),
  attributes_json = VALUES(attributes_json), created_at = VALUES(created_at);

