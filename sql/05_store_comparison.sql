-- Store scorecard year to date (observed data only).
WITH p AS (SELECT * FROM params)
SELECT i.store_id, s.name,
       ROUND(SUM(i.sales_value_aed), 0)                                       AS sales_value_aed,
       SUM(i.sales_units)                                                     AS sales_units,
       ROUND(AVG(CASE WHEN k.replenishment = 'continuous'
                      THEN CASE WHEN i.closing_units > 0 THEN 1.0 ELSE 0 END END), 4) AS basics_instock_rate,
       ROUND(SUM(CASE WHEN i.date = p.as_of THEN i.closing_value_at_cost_aed END), 0) AS inventory_value_aed,
       SUM(i.transfer_in_units)                                               AS transfer_in_units,
       SUM(i.transfer_out_units)                                              AS transfer_out_units,
       SUM(i.clearance_units)                                                 AS season_end_clearance_units
FROM inventory i JOIN store s USING (store_id) JOIN sku k USING (sku_id), p
GROUP BY 1, 2
ORDER BY 1;
