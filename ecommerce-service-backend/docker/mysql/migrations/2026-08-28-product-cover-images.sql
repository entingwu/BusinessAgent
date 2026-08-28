-- 2026-08-28-product-cover-images.sql
--
-- Replace the demo catalogue's cover images with ones that actually depict the product.
--
-- The seed used https://picsum.photos/seed/<product_id>/400/400, which returns a *random*
-- photo keyed by the id. It is stable per product, so nothing looked broken -- it just meant
-- the 65W charger showed a star-trail night sky and the mechanical keyboard showed a clock.
--
-- loremflickr serves a Flickr photo matching a tag. Two details matter:
--   - `?lock=<n>` pins one specific photo. Without it the image changes on every request, and
--     a product card whose picture changes each time you scroll past reads as a bug.
--   - `a,b/all` requires BOTH tags. Single ambiguous words pick up the wrong subject entirely
--     -- `mouse` alone returns rodents -- so anything ambiguous is constrained to two tags.
--     Every one of the 24 URLs below was checked to return HTTP 200 with a real JPEG body.
--
-- WHAT IS NOT GUARANTEED: the tags are Flickr's, applied by whoever uploaded the photo, so a
-- picture can be tagged `keyboard` and still be a poor illustration of this product. Judging
-- that needs eyes on the image. If one looks wrong, change that row's `lock` value -- it
-- selects a different photo for the same tag and needs no other change.
--
-- source.unsplash.com would have been the obvious alternative and is **dead** (503); it was
-- checked rather than assumed.
--
-- cover_url is pure display: nothing in either service or the front end matches on it, so
-- this migration carries none of the key-column risk that 2026-08-28-englishify-display-fields
-- documents. It is also unconditional per product_id, so re-running it repairs the column
-- after any other migration writes the old seed URLs back.

USE commerce;
SET NAMES utf8mb4;

UPDATE products SET cover_url = 'https://loremflickr.com/400/400/mechanical,keyboard/all?lock=2001' WHERE product_id = 'p2001';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/office,chair/all?lock=2002' WHERE product_id = 'p2002';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/headphones?lock=2003' WHERE product_id = 'p2003';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/portable,monitor/all?lock=2004' WHERE product_id = 'p2004';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/desk,lamp/all?lock=2005' WHERE product_id = 'p2005';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/usb,charger/all?lock=2006' WHERE product_id = 'p2006';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/computer,mouse/all?lock=2007' WHERE product_id = 'p2007';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/gaming,mouse/all?lock=2008' WHERE product_id = 'p2008';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/mechanical,keyboard/all?lock=2009' WHERE product_id = 'p2009';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/bluetooth,speaker/all?lock=2010' WHERE product_id = 'p2010';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/computer,monitor/all?lock=2011' WHERE product_id = 'p2011';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/curved,monitor/all?lock=2012' WHERE product_id = 'p2012';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/laptop,stand/all?lock=2013' WHERE product_id = 'p2013';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/backpack?lock=2014' WHERE product_id = 'p2014';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/leather,briefcase/all?lock=2015' WHERE product_id = 'p2015';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/tshirt?lock=2016' WHERE product_id = 'p2016';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/hoodie?lock=2017' WHERE product_id = 'p2017';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/running,shorts/all?lock=2018' WHERE product_id = 'p2018';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/sports,shirt/all?lock=2019' WHERE product_id = 'p2019';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/thermos,bottle/all?lock=2020' WHERE product_id = 'p2020';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/ceramic,mug/all?lock=2021' WHERE product_id = 'p2021';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/wooden,desk/all?lock=2022' WHERE product_id = 'p2022';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/floor,lamp/all?lock=2023' WHERE product_id = 'p2023';
UPDATE products SET cover_url = 'https://loremflickr.com/400/400/humidifier?lock=2024' WHERE product_id = 'p2024';

