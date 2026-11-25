-- Size availability for sized basics over the lookback window, and broken size runs:
-- a style-store-day where a core size (M or L) is out while the style still has stock.
WITH p AS (SELECT * FROM params),
w AS (
    SELECT i.* FROM inventory i JOIN sku k USING (sku_id), p
    WHERE k.category = 'basics' AND k.sized = 'Y'
)
SELECT 'size_availability' AS metric, store_id, size, NULL AS week_start,
       ROUND(AVG(CASE WHEN closing_units > 0 THEN 1.0 ELSE 0 END), 4) AS value
FROM w, p WHERE date > p.as_of - p.lookback_days AND date <= p.as_of
GROUP BY store_id, size
UNION ALL
SELECT 'broken_size_run_rate', store_id, NULL, week_start, ROUND(AVG(broken), 4)
FROM (
    SELECT w.store_id, w.sku_id, w.date, c.week_start,
           CASE WHEN SUM(CASE WHEN w.size IN ('M','L') AND w.closing_units = 0 THEN 1 ELSE 0 END) > 0
                 AND SUM(w.closing_units) > 0 THEN 1.0 ELSE 0 END AS broken
    FROM w JOIN calendar c USING (date)
    GROUP BY 1, 2, 3, 4
) GROUP BY store_id, week_start
ORDER BY metric, store_id, size, week_start;
