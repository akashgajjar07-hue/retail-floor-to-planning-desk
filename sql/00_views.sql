-- Views over the generated files. Run from the repo root:
--   duckdb -c ".read sql/00_views.sql"   (or python src/run_all.py, which does this for you)
CREATE OR REPLACE VIEW inventory  AS SELECT * FROM read_parquet('data/fact_inventory_daily.parquet');
CREATE OR REPLACE VIEW truth      AS SELECT * FROM read_parquet('data/truth_demand_daily.parquet');  -- synthetic-only: true demand & lost sales
CREATE OR REPLACE VIEW orders     AS SELECT * FROM read_csv_auto('data/fact_replenishment_orders.csv');
CREATE OR REPLACE VIEW receipts   AS SELECT * FROM read_csv_auto('data/fact_receipts.csv');
CREATE OR REPLACE VIEW transfers  AS SELECT * FROM read_csv_auto('data/fact_transfers.csv');
CREATE OR REPLACE VIEW sku        AS SELECT * FROM read_csv_auto('data/dim_sku.csv');
CREATE OR REPLACE VIEW store      AS SELECT * FROM read_csv_auto('data/dim_store.csv');
CREATE OR REPLACE VIEW supplier   AS SELECT * FROM read_csv_auto('data/dim_supplier.csv');
CREATE OR REPLACE VIEW calendar   AS SELECT * FROM read_csv_auto('data/dim_calendar.csv');
CREATE OR REPLACE TABLE params AS SELECT DATE '2026-11-30' AS as_of, 28 AS lookback_days, 90 AS excess_days_cover;
