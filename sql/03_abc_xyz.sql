-- ABC on chain sales value per SKU (Pareto 80/15/5): sets the service level.
-- XYZ on the variability of weekly unit sales per store-SKU (coefficient of variation;
-- X < 0.5 steady, Y < 1.0 variable, Z erratic). Complete weeks only, replenished items.
WITH weekly AS (
    SELECT i.store_id, i.sku_id, c.week_start, SUM(i.sales_units) AS units, SUM(i.sales_value_aed) AS value
    FROM inventory i JOIN calendar c USING (date) JOIN sku k USING (sku_id)
    WHERE k.replenishment = 'continuous'
      AND c.week_start IN (SELECT week_start FROM calendar GROUP BY 1 HAVING COUNT(*) = 7)
    GROUP BY 1, 2, 3
),
chain AS (
    SELECT sku_id, SUM(value) AS sales_value FROM weekly GROUP BY 1
),
abc AS (
    SELECT sku_id, sales_value,
           SUM(sales_value) OVER (ORDER BY sales_value DESC ROWS UNBOUNDED PRECEDING) / SUM(sales_value) OVER () AS cum_share,
           sales_value / SUM(sales_value) OVER () AS share
    FROM chain
),
xyz AS (
    SELECT store_id, sku_id, SUM(value) AS store_sales_value,
           STDDEV_SAMP(units) / NULLIF(AVG(units), 0) AS cv
    FROM weekly GROUP BY 1, 2
)
SELECT x.store_id, x.sku_id, k.name, k.category,
       ROUND(x.store_sales_value, 0)                                   AS store_sales_value_aed,
       ROUND(a.sales_value, 0)                                         AS chain_sales_value_aed,
       ROUND(a.cum_share, 4)                                           AS chain_cum_share,
       CASE WHEN a.cum_share - a.share < 0.80 THEN 'A'
            WHEN a.cum_share - a.share < 0.95 THEN 'B' ELSE 'C' END    AS abc,
       ROUND(x.cv, 3)                                                  AS weekly_cv,
       CASE WHEN x.cv < 0.5 THEN 'X' WHEN x.cv < 1.0 THEN 'Y' ELSE 'Z' END AS xyz
FROM xyz x JOIN abc a USING (sku_id) JOIN sku k USING (sku_id)
ORDER BY a.sales_value DESC, x.store_id;
