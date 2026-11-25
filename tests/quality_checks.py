"""
Quality checks. Run: python tests/quality_checks.py
Writes outputs/quality_report.md and exits non-zero if any check fails.
"""
import hashlib
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from engine import simulate          # noqa: E402
from generate import build_world     # noqa: E402

results = []


def check(name, ok, detail):
    results.append((name, bool(ok), detail))


con = duckdb.connect()
inv = f"read_parquet('{ROOT}/data/fact_inventory_daily.parquet')"

# 1. stock balance: opening + receipts + transfers in - transfers out - sales - clearance = closing
bad = con.execute(f"""
    SELECT COUNT(*) FROM {inv}
    WHERE opening_units + receipt_units + transfer_in_units - transfer_out_units
          - sales_units - clearance_units <> closing_units""").fetchone()[0]
n = con.execute(f"SELECT COUNT(*) FROM {inv}").fetchone()[0]
check("Stock balance reconciles on every row", bad == 0, f"{bad} of {n:,} rows break the balance")

# 2. day-to-day continuity: today's opening = yesterday's closing
gap = con.execute(f"""
    SELECT COUNT(*) FROM (
      SELECT opening_units, LAG(closing_units) OVER (PARTITION BY store_id, sku_id, size ORDER BY date) AS prev
      FROM {inv}) WHERE prev IS NOT NULL AND prev <> opening_units""").fetchone()[0]
check("Opening stock equals previous day's closing", gap == 0, f"{gap} breaks")

# 3. no negative inventory, sales never exceed available stock
neg = con.execute(f"SELECT COUNT(*) FROM {inv} WHERE closing_units < 0 OR opening_units < 0").fetchone()[0]
over = con.execute(f"""SELECT COUNT(*) FROM {inv}
    WHERE sales_units > opening_units + receipt_units + transfer_in_units""").fetchone()[0]
check("No negative inventory", neg == 0, f"{neg} negative rows")
check("Sales never exceed stock available that day", over == 0, f"{over} rows")

# 4. censoring: observed sales never exceed true demand
cens = con.execute(f"""SELECT COUNT(*) FROM {inv} i
    JOIN read_parquet('{ROOT}/data/truth_demand_daily.parquet') t USING (date, store_id, sku_id, size)
    WHERE i.sales_units > t.true_demand_units
       OR t.lost_sales_units <> t.true_demand_units - i.sales_units""").fetchone()[0]
check("Sales = min(demand, stock); lost = demand - sales", cens == 0, f"{cens} rows violate it")

# 5. lead-time units are days and sensible
lt = pd.read_csv(ROOT / "data/fact_replenishment_orders.csv")
rec = lt[lt["status"] == "received"]
ok_lt = rec["lead_time_days"].between(2, 90).all()
check("Lead times are whole days between 2 and 90", ok_lt,
      f"range {rec.lead_time_days.min():.0f}-{rec.lead_time_days.max():.0f} days")
dd = (pd.to_datetime(rec["receipt_date"]) - pd.to_datetime(rec["order_date"])).dt.days
check("Receipt date - order date = lead time", (dd == rec["lead_time_days"]).all(), "checked every received line")

# 6. receipts table agrees with the daily fact
r1 = pd.read_csv(ROOT / "data/fact_receipts.csv")["units"].sum()
r2 = con.execute(f"SELECT SUM(receipt_units) FROM {inv}").fetchone()[0]
check("Receipts table total = receipts in daily fact", int(r1) == int(r2), f"{int(r1):,} vs {int(r2):,}")

# 7. reproducibility: same seed -> identical results
def fingerprint():
    w = build_world()
    r = simulate(w, "prioritised")
    h = hashlib.sha256()
    for k in ["demand", "sales", "close"]:
        h.update(r.arrays[k].astype(np.int64).tobytes())
    return h.hexdigest()[:16]
f1, f2 = fingerprint(), fingerprint()
check("Same seed reproduces identical demand, sales and stock", f1 == f2, f"fingerprint {f1}")

# 8. common random numbers: both policies see the same demand while stock and price are the same
w = build_world()
a, b = simulate(w, "fixed"), simulate(w, "prioritised")
same0 = (a.arrays["demand"][0] == b.arrays["demand"][0]).all()
check("Both policies face identical demand on day 1", same0, "same seed, same random draws")

# 9. transfers balance: everything sent arrives (except stock in transit on the last day)
sent, got = b.arrays["transfer_out"].sum(), b.arrays["transfer_in"].sum()
in_transit_end = sum(x[4] for x in b.transfers if x[0] == w.T - 1)
check("Transfers out = transfers in (+ in transit on last day)", sent == got + in_transit_end,
      f"sent {int(sent):,}, received {int(got):,}, in transit {in_transit_end}")

# 10. sensitivity: higher service level -> more safety stock -> more inventory, higher fill
g = pd.read_csv(ROOT / "outputs/what_if_grid.csv")
base = g[(g.policy == "proposed") & (g.delay_days == 0) & (g.demand_shock == 1.0)
         & (g.service_level != "ABC (98/95/90)")].copy()
base["sl"] = base["service_level"].astype(float)
base = base.sort_values("sl")
mono_inv = base["avg_inventory_value_basics_aed"].is_monotonic_increasing
mono_fill = base["fill_rate_basics"].is_monotonic_increasing
check("Raising service level raises inventory (sensitivity)", mono_inv,
      " -> ".join(f"{r.sl:.3g}: AED {r.avg_inventory_value_basics_aed:,.0f}" for r in base.itertuples()))
check("Raising service level raises fill rate (sensitivity)", mono_fill,
      " -> ".join(f"{r.sl:.3g}: {r.fill_rate_basics:.1%}" for r in base.itertuples()))

# 11. supplier delay hurts both policies (direction check)
gg = g[(g.policy == "benchmark") | (g.service_level == "ABC (98/95/90)")]
d0 = gg[(gg.delay_days == 0) & (gg.demand_shock == 1.0)].groupby("policy")["fill_rate_basics"].first()
d14 = gg[(gg.delay_days == 14) & (gg.demand_shock == 1.0)].groupby("policy")["fill_rate_basics"].first()
check("A 14-day supplier delay lowers fill rate for both policies", (d14 < d0).all(),
      f"benchmark {d0['benchmark']:.1%} -> {d14['benchmark']:.1%}; proposed {d0['proposed']:.1%} -> {d14['proposed']:.1%}")

lines = ["# Quality report", "", "| Check | Result | Detail |", "|---|---|---|"]
lines += [f"| {n} | {'PASS' if ok else 'FAIL'} | {d} |" for n, ok, d in results]
(ROOT / "outputs/quality_report.md").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
sys.exit(0 if all(ok for _, ok, _ in results) else 1)
