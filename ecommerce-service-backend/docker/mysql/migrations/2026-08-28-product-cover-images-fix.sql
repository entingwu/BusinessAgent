-- 2026-08-28-product-cover-images-fix.sql
--
-- Five covers from 2026-08-28-product-cover-images.sql did not depict their product. Replaces
-- them with Wikimedia Commons files.
--
-- WHY THE SOURCE CHANGED, and it is the interesting part: loremflickr returns whatever Flickr
-- users tagged with a word, so `curved,monitor` yields photos *tagged* curved and monitor,
-- which is not the same as photos *of* a curved monitor. Nothing in that pipeline can be
-- checked without a human looking at each image, and the first round needed exactly that --
-- five of twenty-four came back wrong.
--
-- Commons files are named for their content, so the filename is evidence:
--   p2007  A black wireless computer mouse.jpg
--   p2012  Computer Screen Monitor.jpg
--   p2019  Sports T-Shirt.png
--   p2022  Wooden Slope Writing Desk.jpg
--   p2024  Aroma Diffuser.jpg
--
-- That does not make the picture good, but it does make the choice checkable without eyes on
-- it, which blind `lock` retries against loremflickr never would have been. Retrying there
-- would have been guessing with the user paying the verification cost each round.
--
-- The other nineteen stay on loremflickr: they were judged correct, and churning a working
-- image to make the source uniform would be tidiness at the cost of re-verification.
--
-- URLs are the canonical 500px thumbnails with the `?utm_source=` tracking parameters that the
-- Commons API appends stripped off. All five verified: HTTP 200, real image bodies, requested
-- with a browser User-Agent and Referer rather than curl's default.
--
-- Unconditional per product_id, so re-running repairs the column after any migration that
-- writes older URLs back. Run this after 2026-08-28-product-cover-images.sql.

USE commerce;
SET NAMES utf8mb4;

-- A black wireless computer mouse.jpg
UPDATE products SET cover_url = 'https://upload.wikimedia.org/wikipedia/commons/thumb/f/f1/A_black_wireless_computer_mouse.jpg/500px-A_black_wireless_computer_mouse.jpg' WHERE product_id = 'p2007';
-- Computer Screen Monitor.jpg
UPDATE products SET cover_url = 'https://upload.wikimedia.org/wikipedia/commons/thumb/7/77/Computer_Screen_Monitor.jpg/500px-Computer_Screen_Monitor.jpg' WHERE product_id = 'p2012';
-- Sports T-Shirt.png
UPDATE products SET cover_url = 'https://upload.wikimedia.org/wikipedia/commons/thumb/0/09/Sports_T-Shirt.png/500px-Sports_T-Shirt.png' WHERE product_id = 'p2019';
-- Wooden Slope Writing Desk.jpg
UPDATE products SET cover_url = 'https://upload.wikimedia.org/wikipedia/commons/thumb/8/84/Wooden_Slope_Writing_Desk.jpg/500px-Wooden_Slope_Writing_Desk.jpg' WHERE product_id = 'p2022';
-- Aroma Diffuser.jpg
UPDATE products SET cover_url = 'https://upload.wikimedia.org/wikipedia/commons/thumb/b/b5/Aroma_Diffuser.jpg/500px-Aroma_Diffuser.jpg' WHERE product_id = 'p2024';

