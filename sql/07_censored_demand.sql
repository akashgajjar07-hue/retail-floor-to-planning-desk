-- Why sales are not demand. For each continuous style-store, compare three daily averages:
--   naive      = average sales over all days (what a sales-based forecast would use)
--   corrected  = average sales on days that ended with stock (demand fully served that day)
--   true       = average true demand (only knowable because the data is synthetic)
-- Size-level, then summed to style-store, grouped by how often the item was out of stock.
WITH d AS (
    SELECT i.store_id, i.sku_id, i.size, i.sales_units, i.closing_units, t.true_demand_units
    FROM inventory i
    JOIN truth t USING (date, store_id, sku_id, size)
    JOIN sku k USING (sku_id)
    WHERE k.replenishment = 'continuous'
),
size_level AS (
    SELECT store_id, sku_id, size,
           AVG(sales_units)                                                   AS naive,
           COALESCE(AVG(CASE WHEN closing_units > 0 THEN sales_units END), AVG(sales_units)) AS corrected,
           AVG(true_demand_units)                                             AS true_mean,
           AVG(CASE WHEN closing_units = 0 THEN 1.0 ELSE 0 END)               AS stockout_rate
    FROM d GROUP BY 1, 2, 3
),
style AS (
    SELECT store_id, sku_id, SUM(naive) AS naive, SUM(corrected) AS corrected,
           SUM(true_mean) AS true_mean, AVG(stockout_rate) AS stockout_rate
    FROM size_level GROUP BY 1, 2
)
SELECT CASE WHEN stockout_rate < 0.05 THEN '0-5%'
            WHEN stockout_rate < 0.15 THEN '5-15%'
            WHEN stockout_rate < 0.30 THEN '15-30%'
            ELSE '30%+' END                                AS stockout_band,
       COUNT(*)                                            AS style_stores,
       ROUND(SUM(naive) / SUM(true_mean) - 1, 4)           AS naive_bias,
       ROUND(SUM(corrected) / SUM(true_mean) - 1, 4)       AS corrected_bias
FROM style
GROUP BY 1
ORDER BY MIN(stockout_rate);
