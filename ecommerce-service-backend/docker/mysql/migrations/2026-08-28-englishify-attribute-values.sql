-- 2026-08-28-englishify-attribute-values.sql
--
-- Englishify the *values* inside products.attributes_json (englishification tier D3).
--
-- READ THIS BEFORE CHANGING EITHER SIDE. use_case / style / size are **matching keys**, not
-- display text. A value written here has to be identical to the value the dialogue backend
-- sends as an attribute filter, and that value is produced in three other places:
--
--   1. this file                                          (what the catalogue stores)
--   2. recommend_products.py  STYLE_VALUES                (refinement buttons)
--   3. user_flows.yml  slot descriptions                  (the allowed list the planner sees)
--   4. user_flows.yml  collect-step quick replies         (what a tap sends back)
--
-- All four must move together, in one commit. If any one of them lags, a tap returns an empty
-- result set -- and an empty result set is indistinguishable from "there really is no matching
-- product". **That is the failure mode to check first if searches start coming back empty.**
--
-- This trade was made knowingly. Before this migration the same drift showed up as Chinese text
-- on an English button: ugly, but visible the moment you looked. Afterwards it is silent. The
-- display-layer mapping this replaces (ATTRIBUTE_VALUE_LABELS) did not have that property.
--
-- color and warranty are in here too but are a different kind of thing: nothing matches on
-- them, they are pure display, and they are translated only so the catalogue is uniform.
--
-- Idempotent: every product's attributes_json is rewritten wholesale from its product_id, so a
-- second run changes nothing, and a re-run repairs the column after any migration that writes
-- the Chinese values back. Must run after 2026-08-27-unify-product-attributes.sql.

USE commerce;
SET NAMES utf8mb4;

UPDATE products SET attributes_json = CAST('{"size": "standard", "spec": "Silent red switches / 87 keys / tri-mode (wired + Bluetooth + 2.4G) / full n-key rollover", "brand": "Plainly", "color": "black", "style": "minimalist", "use_case": "office", "warranty": "2 years"}' AS JSON)
  WHERE product_id = 'p2001';
UPDATE products SET attributes_json = CAST('{"size": "large", "spec": "High-density mesh / 150kg load rating / adjustable seat height, lumbar and armrests", "brand": "Sitwell", "color": "black", "style": "business", "use_case": "office", "warranty": "3 years"}' AS JSON)
  WHERE product_id = 'p2002';
UPDATE products SET attributes_json = CAST('{"size": "small", "spec": "Hybrid ANC 42dB / 8h + 22h battery / Bluetooth 5.3 / IPX4", "brand": "Audiowild", "color": "white", "style": "minimalist", "use_case": "travel", "warranty": "1 year"}' AS JSON)
  WHERE product_id = 'p2003';
UPDATE products SET attributes_json = CAST('{"size": "standard", "spec": "15.6 inch / 1920x1080 IPS / USB-C x2 + mini HDMI / 780g", "brand": "Vistafield", "color": "space gray", "style": "business", "use_case": "office", "warranty": "2 years"}' AS JSON)
  WHERE product_id = 'p2004';
UPDATE products SET attributes_json = CAST('{"size": "small", "spec": "2700K-6500K stepless tuning / Ra95 / app, voice and touch / 12W", "brand": "Woodnote", "color": "off-white", "style": "nordic", "use_case": "home", "warranty": "1 year"}' AS JSON)
  WHERE product_id = 'p2005';
UPDATE products SET attributes_json = CAST('{"size": "small", "spec": "65W / 2C1A / PD3.0 + QC4+ / folding prongs", "brand": "Plainly", "color": "white", "style": "minimalist", "use_case": "travel", "warranty": "1 year"}' AS JSON)
  WHERE product_id = 'p2006';
UPDATE products SET attributes_json = CAST('{"size": "standard", "spec": "Silent micro switches / 2.4G + Bluetooth / 1600DPI / three-device switching", "brand": "Plainly", "color": "white", "style": "minimalist", "use_case": "office", "warranty": "1 year"}' AS JSON)
  WHERE product_id = 'p2007';
UPDATE products SET attributes_json = CAST('{"size": "standard", "spec": "26000DPI / 59g lightweight / 1000Hz polling rate", "brand": "Voltrix", "color": "black", "style": "esports", "use_case": "gaming", "warranty": "2 years"}' AS JSON)
  WHERE product_id = 'p2008';
UPDATE products SET attributes_json = CAST('{"size": "large", "spec": "Brown switches / 104 keys / hot-swappable / RGB backlight / gasket mount", "brand": "Voltrix", "color": "black", "style": "esports", "use_case": "gaming", "warranty": "2 years"}' AS JSON)
  WHERE product_id = 'p2009';
UPDATE products SET attributes_json = CAST('{"size": "small", "spec": "IPX7 waterproof / 12h battery / stereo pairing", "brand": "Audiowild", "color": "navy", "style": "sports", "use_case": "sports", "warranty": "1 year"}' AS JSON)
  WHERE product_id = 'p2010';
UPDATE products SET attributes_json = CAST('{"size": "large", "spec": "27 inch / 3840x2160 IPS / USB-C 90W power delivery / ΔE<2", "brand": "Vistafield", "color": "silver", "style": "business", "use_case": "office", "warranty": "3 years"}' AS JSON)
  WHERE product_id = 'p2011';
UPDATE products SET attributes_json = CAST('{"size": "large", "spec": "34 inch / 3440x1440 / 165Hz / 1ms / HDR400", "brand": "Vistafield", "color": "black", "style": "esports", "use_case": "gaming", "warranty": "3 years"}' AS JSON)
  WHERE product_id = 'p2012';
UPDATE products SET attributes_json = CAST('{"size": "standard", "spec": "Six angle settings / 8kg load rating / all-aluminium cooling", "brand": "Plainly", "color": "silver", "style": "minimalist", "use_case": "office", "warranty": "1 year"}' AS JSON)
  WHERE product_id = 'p2013';
UPDATE products SET attributes_json = CAST('{"size": "large", "spec": "25L / 15.6-inch compartment / water-repellent / luggage strap", "brand": "Trailpath", "color": "dark gray", "style": "business", "use_case": "travel", "warranty": "2 years"}' AS JSON)
  WHERE product_id = 'p2014';
UPDATE products SET attributes_json = CAST('{"size": "standard", "spec": "Full-grain leather / 14-inch compartment / detachable strap", "brand": "Trailpath", "color": "brown", "style": "business", "use_case": "travel", "warranty": "2 years"}' AS JSON)
  WHERE product_id = 'p2015';
UPDATE products SET attributes_json = CAST('{"size": "S", "spec": "260g combed cotton / crew neck / regular fit", "brand": "Plainwear", "color": "white", "style": "minimalist", "use_case": "home", "warranty": "none"}' AS JSON)
  WHERE product_id = 'p2016';
UPDATE products SET attributes_json = CAST('{"size": "L", "spec": "Double-sided fleece / relaxed drop shoulder / ribbed hem", "brand": "Plainwear", "color": "dark navy", "style": "retro", "use_case": "home", "warranty": "none"}' AS JSON)
  WHERE product_id = 'p2017';
UPDATE products SET attributes_json = CAST('{"size": "M", "spec": "Stretch quick-dry / built-in compression liner / reflective strips", "brand": "Plainwear", "color": "black", "style": "sports", "use_case": "sports", "warranty": "none"}' AS JSON)
  WHERE product_id = 'p2018';
UPDATE products SET attributes_json = CAST('{"size": "L", "spec": "Cooling quick-dry / UPF50+ / breathable mesh", "brand": "Plainwear", "color": "neon green", "style": "sports", "use_case": "sports", "warranty": "none"}' AS JSON)
  WHERE product_id = 'p2019';
UPDATE products SET attributes_json = CAST('{"size": "small", "spec": "500ml / 316 stainless steel / 12-hour insulation / one-touch flip lid", "brand": "Woodnote", "color": "off-white", "style": "nordic", "use_case": "travel", "warranty": "1 year"}' AS JSON)
  WHERE product_id = 'p2020';
UPDATE products SET attributes_json = CAST('{"size": "small", "spec": "350ml / underglaze colour / microwave safe", "brand": "Woodnote", "color": "cream", "style": "nordic", "use_case": "home", "warranty": "none"}' AS JSON)
  WHERE product_id = 'p2021';
UPDATE products SET attributes_json = CAST('{"size": "large", "spec": "1.2m x 0.6m / solid oak / 100kg load rating / cable pass-through", "brand": "Woodnote", "color": "natural wood", "style": "nordic", "use_case": "office", "warranty": "5 years"}' AS JSON)
  WHERE product_id = 'p2022';
UPDATE products SET attributes_json = CAST('{"size": "large", "spec": "Ra95 / stepless dimming and tuning / flicker-free / floor stand", "brand": "Woodnote", "color": "off-white", "style": "nordic", "use_case": "home", "warranty": "2 years"}' AS JSON)
  WHERE product_id = 'p2023';
UPDATE products SET attributes_json = CAST('{"size": "small", "spec": "300ml / quiet ultrasonic misting / dry-run shutoff", "brand": "Woodnote", "color": "white", "style": "minimalist", "use_case": "home", "warranty": "1 year"}' AS JSON)
  WHERE product_id = 'p2024';

