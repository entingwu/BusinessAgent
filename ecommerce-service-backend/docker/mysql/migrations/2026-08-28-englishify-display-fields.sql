-- 2026-08-28-englishify-display-fields.sql
--
-- Englishify the *display-only* columns of the demo catalogue (englishification tier D1).
--
-- SCOPE IS DELIBERATELY NARROW. Only display columns are touched:
--   products          : title, description, attributes_json.spec, attributes_json.brand
--   order_items       : title_snapshot
--   orders            : status_desc, receiver_name, receiver_address
--   logistics_records : logistics_company, status_desc
--   logistics_traces  : trace_desc
--   users             : nickname, level
--
-- **Deliberately NOT touched: products.stock_status, orders.status, logistics_records.status.**
-- Those three are used as matching keys, and translating them breaks four separate places,
-- every one of them silently:
--   1. app/api.py -- `if order.status not in {"待发货", "待揽收"}` gates the shipping-reminder
--      endpoint. Translate orders.status and that endpoint returns 400 for every order. It does
--      not raise and it does not crash; it just refuses, forever.
--   2. app/api.py -- `_IN_STOCK_LABEL = "有货"` is written back to stock_status on every stock
--      decrement, so the first order placed after a translation re-pollutes the catalogue.
--   3. app/api.py -- new orders are created with status="待支付".
--   4. 2026-08-28-stock-quantity-and-order-idempotency.sql carries an unconditional
--      `UPDATE products SET stock_status = IF(stock_quantity > 0, '有货', '缺货')`. Migrations here
--      are re-run on purpose when in doubt, and one re-run would undo the translation.
--
-- Those three columns are englishified at the DISPLAY layer instead: App.vue already maps
-- ORDER_STATUS_LABEL from the Chinese value to an English label, which is why an order whose
-- stored status is 运输中 already reads "In transit" in the UI. Extending that map
-- costs two lines and is immune to all four traps above, because the stored value never moves.
--
-- ONE COLUMN IS NOT PURELY DISPLAY, AND IT IS WORTH NAMING. `products.title` and
-- `products.description` are matched by `GET /products?q=` (app/api.py, `Product.title.ilike(...)`
-- / `Product.description.ilike(...)`). Nothing breaks today because the only caller in the
-- dialogue backend never sends `q` -- it filters by attributes and max_price. But a Chinese
-- keyword search stops matching after this migration, so "no code matches on these" would be too
-- strong a claim to build on. Anyone adding keyword search should know the catalogue is English.
--
-- ORDERING: THIS MIGRATION MUST RUN AFTER 2026-08-27-unify-product-attributes.sql.
-- That script is not idempotent in the direction that matters here -- it writes Chinese values
-- back unconditionally. Section 1 overwrites attributes_json for p2001-p2006, and the
-- `INSERT ... ON DUPLICATE KEY UPDATE title = VALUES(title), ...` for p2007-p2024 rewrites their
-- title, description and attributes. Since the convention in this repo is "re-run a migration
-- when in doubt", a re-run of that script silently reverts this one.
--
-- That is the same trap this file documents above for stock_status, and it was originally missed
-- for the very columns this file translates. Re-running THIS file is the fix, which is why the
-- product UPDATEs below match on `product_id` alone rather than on the original Chinese title:
-- an earlier version guarded on the Chinese title, and after a unify re-run p2001-p2006 would
-- have been left with an English title and Chinese spec/brand that no re-run could repair,
-- because unify section 1 does not touch title.
--
-- IDEMPOTENCY: re-running this file is safe and is the intended repair. The product UPDATEs are
-- unconditional writes of the same English values (second run: rows matched, zero changed); every
-- other UPDATE matches on the original Chinese value and so matches nothing on a second run.
-- There is no version table in this repo -- that is the only defence.

USE commerce;
SET NAMES utf8mb4;

-- ---------- products ----------
UPDATE products SET
  title = 'Silent Mechanical Keyboard, 87 Keys',
  description = 'Compact 87-key layout with silenced red switches, tri-mode connectivity and full n-key rollover. Keycap puller included.',
  attributes_json = JSON_SET(attributes_json, '$.spec', 'Silent red switches / 87 keys / tri-mode (wired + Bluetooth + 2.4G) / full n-key rollover', '$.brand', 'Plainly')
WHERE product_id = 'p2001';
UPDATE products SET
  title = 'Ergonomic Office Chair',
  description = 'Four-stage adjustable lumbar support, 3D armrests, breathable mesh back and quiet PU castors.',
  attributes_json = JSON_SET(attributes_json, '$.spec', 'High-density mesh / 150kg load rating / adjustable seat height, lumbar and armrests', '$.brand', 'Sitwell')
WHERE product_id = 'p2002';
UPDATE products SET
  title = 'Active Noise Cancelling Earbuds',
  description = 'Hybrid active noise cancelling. 8 hours per charge, 30 hours with the case, wireless charging supported.',
  attributes_json = JSON_SET(attributes_json, '$.spec', 'Hybrid ANC 42dB / 8h + 22h battery / Bluetooth 5.3 / IPX4', '$.brand', 'Audiowild')
WHERE product_id = 'p2003';
UPDATE products SET
  title = 'Portable Monitor, 15.6 inch',
  description = '1080P laminated IPS panel, single-cable USB-C connection, magnetic stand cover included.',
  attributes_json = JSON_SET(attributes_json, '$.spec', '15.6 inch / 1920x1080 IPS / USB-C x2 + mini HDMI / 780g', '$.brand', 'Vistafield')
WHERE product_id = 'p2004';
UPDATE products SET
  title = 'Desktop Ambient Lamp',
  description = 'Stepless 2700K-6500K colour temperature, Ra95 colour rendering, app and voice control.',
  attributes_json = JSON_SET(attributes_json, '$.spec', '2700K-6500K stepless tuning / Ra95 / app, voice and touch / 12W', '$.brand', 'Woodnote')
WHERE product_id = 'p2005';
UPDATE products SET
  title = '65W GaN Charger',
  description = 'Two USB-C ports plus one USB-A, 65W smart power sharing and folding prongs. Works with laptops and phones.',
  attributes_json = JSON_SET(attributes_json, '$.spec', '65W / 2C1A / PD3.0 + QC4+ / folding prongs', '$.brand', 'Plainly')
WHERE product_id = 'p2006';
UPDATE products SET
  title = 'Silent Wireless Mouse',
  description = 'Silent micro switches, 2.4G and Bluetooth dual mode, one-tap switching across three devices. Suited to open-plan offices.',
  attributes_json = JSON_SET(attributes_json, '$.spec', 'Silent micro switches / 2.4G + Bluetooth / 1600DPI / three-device switching', '$.brand', 'Plainly')
WHERE product_id = 'p2007';
UPDATE products SET
  title = 'Lightweight Gaming Mouse',
  description = '59g honeycomb shell, 26000DPI optical sensor, 1000Hz polling rate, braided paracord cable.',
  attributes_json = JSON_SET(attributes_json, '$.spec', '26000DPI / 59g lightweight / 1000Hz polling rate', '$.brand', 'Voltrix')
WHERE product_id = 'p2008';
UPDATE products SET
  title = 'Full-size Mechanical Keyboard, 104 Keys',
  description = 'Unibody aluminium case, hot-swappable sockets, RGB backlighting and gasket-mounted construction.',
  attributes_json = JSON_SET(attributes_json, '$.spec', 'Brown switches / 104 keys / hot-swappable / RGB backlight / gasket mount', '$.brand', 'Voltrix')
WHERE product_id = 'p2009';
UPDATE products SET
  title = 'Portable Bluetooth Speaker',
  description = 'IPX7 waterproof, 12-hour battery, pair two units for stereo. Lanyard included.',
  attributes_json = JSON_SET(attributes_json, '$.spec', 'IPX7 waterproof / 12h battery / stereo pairing', '$.brand', 'Audiowild')
WHERE product_id = 'p2010';
UPDATE products SET
  title = '4K Monitor, 27 inch',
  description = '27-inch 4K IPS panel, factory calibrated to ΔE<2, single USB-C cable with 90W power delivery.',
  attributes_json = JSON_SET(attributes_json, '$.spec', '27 inch / 3840x2160 IPS / USB-C 90W power delivery / ΔE<2', '$.brand', 'Vistafield')
WHERE product_id = 'p2011';
UPDATE products SET
  title = 'Ultrawide Curved Monitor, 34 inch',
  description = '34-inch 21:9 curved panel, 165Hz refresh rate, 1ms response, HDR400.',
  attributes_json = JSON_SET(attributes_json, '$.spec', '34 inch / 3440x1440 / 165Hz / 1ms / HDR400', '$.brand', 'Vistafield')
WHERE product_id = 'p2012';
UPDATE products SET
  title = 'Aluminium Laptop Stand',
  description = 'Six angle settings, 8kg load rating, all-aluminium heat-dissipating body, silicone anti-slip base.',
  attributes_json = JSON_SET(attributes_json, '$.spec', 'Six angle settings / 8kg load rating / all-aluminium cooling', '$.brand', 'Plainly')
WHERE product_id = 'p2013';
UPDATE products SET
  title = 'Commuter Backpack, 25L',
  description = 'Dedicated 15.6-inch laptop compartment, water-repellent fabric, side quick-access pocket, luggage strap.',
  attributes_json = JSON_SET(attributes_json, '$.spec', '25L / 15.6-inch compartment / water-repellent / luggage strap', '$.brand', 'Trailpath')
WHERE product_id = 'p2014';
UPDATE products SET
  title = 'Full-grain Leather Briefcase',
  description = 'Vegetable-tanned full-grain leather, 14-inch laptop compartment, detachable strap, brass hardware.',
  attributes_json = JSON_SET(attributes_json, '$.spec', 'Full-grain leather / 14-inch compartment / detachable strap', '$.brand', 'Trailpath')
WHERE product_id = 'p2015';
UPDATE products SET
  title = 'Cotton Crew-neck T-shirt',
  description = '260g combed cotton, double-yarn collar that keeps its shape, 1cm drop shoulder. Wearable year-round.',
  attributes_json = JSON_SET(attributes_json, '$.spec', '260g combed cotton / crew neck / regular fit', '$.brand', 'Plainwear')
WHERE product_id = 'p2016';
UPDATE products SET
  title = 'Double-sided Fleece Hoodie',
  description = 'Double-sided fleece, relaxed drop-shoulder fit, ribbed hem, brushed warm lining.',
  attributes_json = JSON_SET(attributes_json, '$.spec', 'Double-sided fleece / relaxed drop shoulder / ribbed hem', '$.brand', 'Plainwear')
WHERE product_id = 'p2017';
UPDATE products SET
  title = 'Running Shorts',
  description = 'Stretch quick-dry fabric, built-in compression liner, zipped back pocket, reflective strips for night runs.',
  attributes_json = JSON_SET(attributes_json, '$.spec', 'Stretch quick-dry / built-in compression liner / reflective strips', '$.brand', 'Plainwear')
WHERE product_id = 'p2018';
UPDATE products SET
  title = 'Cooling Quick-dry Sports T-shirt',
  description = 'Cooling-touch fabric, UPF50+ sun protection, mesh underarm panels.',
  attributes_json = JSON_SET(attributes_json, '$.spec', 'Cooling quick-dry / UPF50+ / breathable mesh', '$.brand', 'Plainwear')
WHERE product_id = 'p2019';
UPDATE products SET
  title = '316 Stainless Steel Insulated Bottle, 500ml',
  description = 'Medical-grade 316 stainless steel liner. 12 hours of insulation and still above 60℃ after six. One-touch flip lid with a rim that stays cool.',
  attributes_json = JSON_SET(attributes_json, '$.spec', '500ml / 316 stainless steel / 12-hour insulation / one-touch flip lid', '$.brand', 'Woodnote')
WHERE product_id = 'p2020';
UPDATE products SET
  title = 'Ceramic Mug, 350ml',
  description = 'High-white clay body, underglaze colour that will not fade, microwave and dishwasher safe, contoured handle.',
  attributes_json = JSON_SET(attributes_json, '$.spec', '350ml / underglaze colour / microwave safe', '$.brand', 'Woodnote')
WHERE product_id = 'p2021';
UPDATE products SET
  title = 'Solid Wood Desk, 1.2 m',
  description = 'Solid oak top with a cable pass-through, reinforced cross beam, 100kg load rating. Self-assembly required.',
  attributes_json = JSON_SET(attributes_json, '$.spec', '1.2m x 0.6m / solid oak / 100kg load rating / cable pass-through', '$.brand', 'Woodnote')
WHERE product_id = 'p2022';
UPDATE products SET
  title = 'Floor Reading Lamp',
  description = 'Ra95 high-CRI LEDs, stepless dimming and colour tuning, flicker-free, movable floor stand.',
  attributes_json = JSON_SET(attributes_json, '$.spec', 'Ra95 / stepless dimming and tuning / flicker-free / floor stand', '$.brand', 'Woodnote')
WHERE product_id = 'p2023';
UPDATE products SET
  title = 'Quiet Aroma Humidifier',
  description = '300ml capacity, quiet ultrasonic misting, automatic dry-run shutoff, takes essential oils.',
  attributes_json = JSON_SET(attributes_json, '$.spec', '300ml / quiet ultrasonic misting / dry-run shutoff', '$.brand', 'Woodnote')
WHERE product_id = 'p2024';

-- ---------- order_items ----------
UPDATE order_items SET title_snapshot = 'Silent Mechanical Keyboard, 87 Keys' WHERE title_snapshot = '静音机械键盘 87 键';
UPDATE order_items SET title_snapshot = 'Ergonomic Office Chair' WHERE title_snapshot = '人体工学办公椅';
UPDATE order_items SET title_snapshot = 'Active Noise Cancelling Earbuds' WHERE title_snapshot = '主动降噪蓝牙耳机';
UPDATE order_items SET title_snapshot = 'Portable Monitor, 15.6 inch' WHERE title_snapshot = '便携显示器 15.6 英寸';
UPDATE order_items SET title_snapshot = 'Desktop Ambient Lamp' WHERE title_snapshot = '桌面氛围灯';
UPDATE order_items SET title_snapshot = '65W GaN Charger' WHERE title_snapshot = '65W 氮化镓充电器';
UPDATE order_items SET title_snapshot = 'Silent Wireless Mouse' WHERE title_snapshot = '静音无线鼠标';
UPDATE order_items SET title_snapshot = 'Lightweight Gaming Mouse' WHERE title_snapshot = '轻量化电竞鼠标';
UPDATE order_items SET title_snapshot = 'Full-size Mechanical Keyboard, 104 Keys' WHERE title_snapshot = '全尺寸机械键盘 104 键';
UPDATE order_items SET title_snapshot = 'Portable Bluetooth Speaker' WHERE title_snapshot = '便携蓝牙音箱';
UPDATE order_items SET title_snapshot = '4K Monitor, 27 inch' WHERE title_snapshot = '4K 显示器 27 英寸';
UPDATE order_items SET title_snapshot = 'Ultrawide Curved Monitor, 34 inch' WHERE title_snapshot = '带鱼屏曲面显示器 34 英寸';
UPDATE order_items SET title_snapshot = 'Aluminium Laptop Stand' WHERE title_snapshot = '铝合金笔记本支架';
UPDATE order_items SET title_snapshot = 'Commuter Backpack, 25L' WHERE title_snapshot = '通勤双肩背包 25L';
UPDATE order_items SET title_snapshot = 'Full-grain Leather Briefcase' WHERE title_snapshot = '头层牛皮公文包';
UPDATE order_items SET title_snapshot = 'Cotton Crew-neck T-shirt' WHERE title_snapshot = '纯棉圆领 T 恤';
UPDATE order_items SET title_snapshot = 'Double-sided Fleece Hoodie' WHERE title_snapshot = '双面抓绒卫衣';
UPDATE order_items SET title_snapshot = 'Running Shorts' WHERE title_snapshot = '跑步运动短裤';
UPDATE order_items SET title_snapshot = 'Cooling Quick-dry Sports T-shirt' WHERE title_snapshot = '冰感速干运动 T 恤';
UPDATE order_items SET title_snapshot = '316 Stainless Steel Insulated Bottle, 500ml' WHERE title_snapshot = '316 不锈钢保温杯 500ml';
UPDATE order_items SET title_snapshot = 'Ceramic Mug, 350ml' WHERE title_snapshot = '陶瓷马克杯 350ml';
UPDATE order_items SET title_snapshot = 'Solid Wood Desk, 1.2 m' WHERE title_snapshot = '实木书桌 1.2 米';
UPDATE order_items SET title_snapshot = 'Floor Reading Lamp' WHERE title_snapshot = '落地阅读灯';
UPDATE order_items SET title_snapshot = 'Quiet Aroma Humidifier' WHERE title_snapshot = '静音香薰加湿器';

-- ---------- orders ----------
UPDATE orders SET status_desc = 'The merchant has accepted the order and is preparing it; expected to ship within 24 hours.' WHERE status_desc = '商家已接单，正在备货中，预计 24 小时内发出。';
UPDATE orders SET status_desc = 'The parcel has left the sorting centre and is on its way to you.' WHERE status_desc = '包裹已从分拣中心发出，正在派往目的地。';
UPDATE orders SET status_desc = 'The parcel has been signed for. Thank you for your purchase.' WHERE status_desc = '包裹已签收，感谢您的购买。';
UPDATE orders SET status_desc = 'The merchant has shipped it and is waiting for the courier to collect it.' WHERE status_desc = '商家已发货，等待快递员揽收。';
UPDATE orders SET status_desc = 'The order was cancelled automatically because payment was not made in time.' WHERE status_desc = '订单因超时未支付已自动取消。';
UPDATE orders SET receiver_name = 'Xiao Ming' WHERE receiver_name = '小明';
UPDATE orders SET receiver_name = 'Aya' WHERE receiver_name = '阿雅';
UPDATE orders SET receiver_address = 'Room 1201, Tower A, 1 Zhongguancun Street, Haidian District, Beijing' WHERE receiver_address = '北京市海淀区中关村大街 1 号 A 座 1201';
UPDATE orders SET receiver_address = 'Room 502, Building 3, 100 Caoxi North Road, Xuhui District, Shanghai' WHERE receiver_address = '上海市徐汇区漕溪北路 100 号 3 号楼 502';

-- ---------- logistics ----------
UPDATE logistics_records SET logistics_company = 'SF Express' WHERE logistics_company = '顺丰速运';
UPDATE logistics_records SET logistics_company = 'JD Logistics' WHERE logistics_company = '京东物流';
UPDATE logistics_records SET logistics_company = 'ZTO Express' WHERE logistics_company = '中通快递';
UPDATE logistics_records SET logistics_company = 'YTO Express' WHERE logistics_company = '圆通速递';
UPDATE logistics_records SET status_desc = 'The parcel has reached the Beijing Haidian sorting centre and is out for delivery.' WHERE status_desc = '快件已到达北京海淀分拣中心，正在派送途中。';
UPDATE logistics_records SET status_desc = 'The parcel was signed for by the recipient.' WHERE status_desc = '快件已由本人签收。';
UPDATE logistics_records SET status_desc = 'The merchant has booked the shipment and is waiting for the courier to collect it.' WHERE status_desc = '商家已下单，等待快递员上门揽收。';
UPDATE logistics_records SET status_desc = 'The parcel has left the Shanghai transit centre.' WHERE status_desc = '快件已从上海转运中心发出。';
UPDATE logistics_traces SET trace_desc = 'Collected at [Shenzhen Bao''an Hub].' WHERE trace_desc = '快件已在【深圳宝安集散中心】完成揽收。';
UPDATE logistics_traces SET trace_desc = 'Departed [Shenzhen Bao''an Hub], next stop [Beijing Daxing Transit Centre].' WHERE trace_desc = '快件已从【深圳宝安集散中心】发出，下一站【北京大兴转运中心】。';
UPDATE logistics_traces SET trace_desc = 'Arrived at [Beijing Daxing Transit Centre].' WHERE trace_desc = '快件已到达【北京大兴转运中心】。';
UPDATE logistics_traces SET trace_desc = 'Arrived at [Beijing Haidian Sorting Centre]; delivery is being arranged.' WHERE trace_desc = '快件已到达【北京海淀分拣中心】，正在安排派送。';
UPDATE logistics_traces SET trace_desc = 'Dispatched from [Beijing Yizhuang Warehouse].' WHERE trace_desc = '快件已在【北京亦庄仓】出库。';
UPDATE logistics_traces SET trace_desc = 'Arrived at [Beijing Haidian Branch].' WHERE trace_desc = '快件已到达【北京海淀营业部】。';
UPDATE logistics_traces SET trace_desc = 'Courier [Mr Zhang] is out delivering your parcel.' WHERE trace_desc = '快递员【张师傅】正在为您派送。';
UPDATE logistics_traces SET trace_desc = 'Your parcel has been signed for by the recipient. Thank you.' WHERE trace_desc = '您的快件已由本人签收，感谢使用。';
UPDATE logistics_traces SET trace_desc = 'The merchant has booked the shipment and is waiting for the courier to collect it.' WHERE trace_desc = '商家已下单，等待快递员上门揽收。';
UPDATE logistics_traces SET trace_desc = 'Collected at [Shanghai Xuhui Branch].' WHERE trace_desc = '快件已在【上海徐汇营业部】完成揽收。';
UPDATE logistics_traces SET trace_desc = 'Departed [Shanghai Transit Centre].' WHERE trace_desc = '快件已从【上海转运中心】发出。';

-- ---------- users ----------
UPDATE users SET nickname = 'Xiao Ming' WHERE nickname = '小明';
UPDATE users SET nickname = 'Aya' WHERE nickname = '阿雅';
UPDATE users SET level = 'Gold member' WHERE level = '黄金会员';
UPDATE users SET level = 'Silver member' WHERE level = '白银会员';

