-- Executive inventory health as of the snapshot date, by category.
-- Days of cover = units on hand / average daily sales over the lookback window.
-- Note: average sales understate demand where items were out of stock (see 07).
WITH p AS (SELECT * FROM params),
recent AS (
    SELECT i.store_id, i.sku_id,
           SUM(i.sales_units) * 1.0 / MAX(p.lookback_days)              AS avg_daily_sales
    FROM inventory i, p
    WHERE i.date > p.as_of - p.lookback_days AND i.date <= p.as_of
    GROUP BY 1, 2
),
snap AS (
    SELECT i.store_id, i.sku_id, SUM(i.closing_units) AS units, SUM(i.closing_value_at_cost_aed) AS value_aed
    FROM inventory i, p WHERE i.date = p.as_of GROUP BY 1, 2
),
style_store AS (
    SELECT s.store_id, s.sku_id, k.category, k.replenishment, s.units, s.value_aed,
           r.avg_daily_sales,
           CASE WHEN r.avg_daily_sales > 0 THEN s.units / r.avg_daily_sales END AS days_cover
    FROM snap s JOIN recent r USING (store_id, sku_id) JOIN sku k USING (sku_id)
    WHERE NOT (s.units = 0 AND r.avg_daily_sales = 0)   -- drop seasonal lines whose season has ended
)
SELECT category,
       COUNT(*)                                                   AS style_stores,
       SUM(units)                                                 AS units_on_hand,
       ROUND(SUM(value_aed), 0)                                   AS inventory_value_aed,
       ROUND(SUM(units) / NULLIF(SUM(avg_daily_sales), 0), 1)     AS days_cover,
       ROUND(SUM(CASE WHEN days_cover > (SELECT excess_days_cover FROM p) OR (avg_daily_sales = 0 AND units > 0)
                      THEN value_aed ELSE 0 END), 0)              AS excess_value_aed,
       SUM(CASE WHEN units = 0 THEN 1 ELSE 0 END)                 AS style_stores_fully_out
FROM style_store
GROUP BY ROLLUP (category)
ORDER BY category NULLS LAST;
