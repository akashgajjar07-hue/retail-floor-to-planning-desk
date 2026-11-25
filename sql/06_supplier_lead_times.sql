-- Actual vs quoted lead times by supplier (received orders, one row per purchase order).
WITH po AS (
    SELECT DISTINCT order_id, supplier_id, order_date, lead_time_days
    FROM orders WHERE status = 'received'
)
SELECT po.supplier_id, s.name,
       s.quoted_lead_time_days,
       COUNT(*)                                   AS purchase_orders,
       ROUND(AVG(lead_time_days), 1)              AS actual_mean_days,
       ROUND(STDDEV_SAMP(lead_time_days), 1)      AS actual_sd_days,
       MIN(lead_time_days)                        AS min_days,
       MAX(lead_time_days)                        AS max_days,
       ROUND(AVG(CASE WHEN lead_time_days > s.quoted_lead_time_days + 3 THEN 1.0 ELSE 0 END), 3) AS share_more_than_3_days_late
FROM po JOIN supplier s USING (supplier_id)
GROUP BY 1, 2, 3
ORDER BY actual_sd_days DESC;
