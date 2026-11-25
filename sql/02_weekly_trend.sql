-- Weekly trend: size-level in-stock rate (share of size-store-days ending with stock),
-- sales and closing inventory value. Continuous (replenished) items only.
SELECT c.week_start,
       ROUND(AVG(CASE WHEN i.closing_units > 0 THEN 1.0 ELSE 0 END), 4)  AS instock_rate,
       SUM(i.sales_units)                                                AS sales_units,
       ROUND(SUM(i.sales_value_aed), 0)                                  AS sales_value_aed,
       ROUND(SUM(CASE WHEN c.day_of_week = 'Sunday' THEN i.closing_value_at_cost_aed END), 0) AS closing_value_aed
FROM inventory i
JOIN calendar c USING (date)
JOIN sku k USING (sku_id)
WHERE k.replenishment = 'continuous'
GROUP BY 1
ORDER BY 1;
