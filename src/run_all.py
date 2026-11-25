"""
One command rebuilds everything:  python src/run_all.py
  1. generate the synthetic world (seeded, reproducible)
  2. simulate the benchmark policy -> this becomes the "observed" history in data/
  3. run the SQL analysis (DuckDB) on that history
  4. build the planner's action list as of the snapshot date
  5. compare benchmark vs proposed policy on identical demand (+ other seeds)
  6. run the what-if grid (service level x supplier delay x demand shock)
  7. write outputs/ and the dashboard data
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from engine import simulate                     # noqa: E402
from export import export_tables               # noqa: E402
from generate import ROOT, build_world, load_config   # noqa: E402
from snapshot import basics_actions, seasonal_actions  # noqa: E402

DATA, OUT, SQL = ROOT / "data", ROOT / "outputs", ROOT / "sql"
REUSE = "--reuse" in sys.argv   # skip the slow seed & what-if runs if their outputs exist


def metrics(world, res):
    s, A, c = world.series, world.cfg, None
    A = res.arrays
    cost = s["unit_cost"].to_numpy()
    price = s["price"].to_numpy()
    cont = (s["replenishment"] == "continuous").to_numpy()
    seas = ~cont
    sized_basic = cont & (s["size"] != "OS").to_numpy()
    hold_rate = world.cfg["costs"]["annual_holding_rate"] / 365
    salv = world.cfg["costs"]["salvage_rate_of_cost"]
    inv_val = A["close"] * cost
    tr_cost = sum(x[5] for x in res.transfers)
    rev = A["revenue"].sum()
    cogs = (A["sales"] * cost).sum()
    holding = inv_val.sum() * hold_rate
    salvage_loss = (A["salvage_units"] * cost).sum() * (1 - salv)
    lost_margin = (A["lost"] * (price - cost)).sum()
    # broken size runs: style-store-days with M or L at zero while style has stock
    close = A["close"]
    ssid = s["ssid"].to_numpy()
    core = s["size"].isin(["M", "L"]).to_numpy() & sized_basic
    df = pd.DataFrame({"ssid": ssid[sized_basic]})
    tot = pd.DataFrame(close[:, sized_basic].T).groupby(df["ssid"].to_numpy()).sum().T.to_numpy()
    core_out = pd.DataFrame((close[:, core] == 0).T.astype(int)).groupby(ssid[core]).max().T.to_numpy()
    broken = ((core_out > 0) & (tot > 0)).mean()
    m = {
        "fill_rate_total": A["sales"].sum() / A["demand"].sum(),
        "fill_rate_basics": A["sales"][:, cont].sum() / A["demand"][:, cont].sum(),
        "fill_rate_seasonal": A["sales"][:, seas].sum() / A["demand"][:, seas].sum(),
        "lost_units_basics": A["lost"][:, cont].sum(),
        "lost_margin_basics_aed": (A["lost"][:, cont] * (price - cost)[cont]).sum(),
        "lost_margin_aed": lost_margin,
        "revenue_aed": rev,
        "gross_margin_aed": rev - cogs,
        "avg_inventory_value_aed": inv_val.sum(1).mean(),
        "avg_inventory_value_basics_aed": inv_val[:, cont].sum(1).mean(),
        "holding_cost_aed": holding,
        "holding_cost_basics_aed": inv_val[:, cont].sum() * hold_rate,
        "transfer_cost_aed": tr_cost,
        "transfers": len(res.transfers),
        "transfer_units": sum(x[4] for x in res.transfers),
        "season_end_leftover_units": A["salvage_units"].sum(),
        "salvage_loss_aed": salvage_loss,
        "net_contribution_aed": rev - cogs - holding - tr_cost - salvage_loss,
        "size_instock_rate_basics": (close[:, sized_basic] > 0).mean(),
        "broken_size_run_rate": broken,
        "purchase_order_lines": len(res.orders),
    }
    for code in ["RAMADAN_EID", "SUMMER"]:
        mm = (s["season"] == code).to_numpy()
        bought = world.seasonal_buy[mm].sum()
        m[f"sell_through_{code}"] = A["sales"][:, mm].sum() / bought
        m[f"revenue_{code}_aed"] = A["revenue"][:, mm].sum()
        m[f"leftover_{code}_units"] = A["salvage_units"][:, mm].sum()
    return {k: float(v) for k, v in m.items()}


def run_sql(con, name):
    q = (SQL / name).read_text()
    return con.execute(q).df()


def main(scenarios=True, seeds=5):
    t0 = time.time()
    cfg, skus = load_config()
    world = build_world(cfg, skus)
    print(f"[1] world: {len(world.series)} series, {len(world.style_store)} style-stores, {world.T} days")

    fixed = simulate(world, "fixed", "Benchmark (fixed min/max)")
    prio = simulate(world, "prioritised", "Proposed (prioritised)", record_params=True)
    print(f"[2] simulated both policies ({time.time()-t0:.0f}s)")

    export_tables(world, fixed, DATA)
    # the proposed policy's own logs, for reference
    export_tables(world, prio, OUT / "proposed_policy_run")
    print(f"[3] exported tables ({time.time()-t0:.0f}s)")

    import os
    os.chdir(ROOT)
    con = duckdb.connect()
    con.execute((SQL / "00_views.sql").read_text())
    sql_out = {}
    for f in sorted(SQL.glob("0[1-9]_*.sql")):
        df = run_sql(con, f.name)
        df.to_csv(OUT / f"sql_{f.stem}.csv", index=False)
        sql_out[f.stem] = df
    print(f"[4] ran {len(sql_out)} SQL queries")

    abc_by_sku = dict(zip(sql_out["03_abc_xyz"]["sku_id"], sql_out["03_abc_xyz"]["abc"]))
    act, act_sizes, act_tr, lead = basics_actions(world, fixed, abc_by_sku)
    seas_act = seasonal_actions(world, fixed, "AW")
    act.to_csv(OUT / "actions_basics.csv", index=False)
    act_sizes.to_csv(OUT / "actions_basics_sizes.csv", index=False)
    act_tr.to_csv(OUT / "actions_transfers.csv", index=False)
    seas_act.to_csv(OUT / "actions_seasonal_AW.csv", index=False)
    print(f"[5] action list: {int((act.action=='ORDER').sum())} orders, "
          f"{int((act_tr.recommend=='TRANSFER').sum()) if len(act_tr) else 0} transfers, "
          f"{int((seas_act.lever!='HOLD').sum())} seasonal levers")

    m_fixed, m_prio = metrics(world, fixed), metrics(world, prio)
    comp = pd.DataFrame({"benchmark": m_fixed, "proposed": m_prio})
    comp["difference"] = comp["proposed"] - comp["benchmark"]
    comp.to_csv(OUT / "policy_comparison.csv")

    # robustness: other seeds
    seed_rows = []
    if REUSE and (OUT / "policy_comparison_other_seeds.csv").exists():
        seeds = 0
    for k in range(seeds):
        c2 = json.loads(json.dumps(cfg)); c2["seed"] = cfg["seed"] + 101 * (k + 1)
        w2 = build_world(c2, skus)
        a, b = metrics(w2, simulate(w2, "fixed")), metrics(w2, simulate(w2, "prioritised"))
        seed_rows.append({"seed": c2["seed"], **{f"benchmark_{x}": a[x] for x in a}, **{f"proposed_{x}": b[x] for x in b}})
    if seed_rows or not REUSE:
        seeds_df = pd.DataFrame(seed_rows)
        seeds_df.to_csv(OUT / "policy_comparison_other_seeds.csv", index=False)
    else:
        seeds_df = pd.read_csv(OUT / "policy_comparison_other_seeds.csv")
    print(f"[6] policy comparison + {seeds} extra seeds ({time.time()-t0:.0f}s)")

    # what-if grid
    grid = []
    if REUSE and (OUT / "what_if_grid.csv").exists():
        grid = pd.read_csv(OUT / "what_if_grid.csv").to_dict("records")
        scenarios = False
    if scenarios:
        keys = ["fill_rate_basics", "lost_margin_basics_aed", "avg_inventory_value_basics_aed",
                "holding_cost_basics_aed", "net_contribution_aed", "lost_units_basics"]
        for delay in [0, 7, 14]:
            for shock in [0.8, 1.0, 1.2]:
                kw = {}
                if delay:
                    kw["delay"] = ("SUP-B", delay, "2026-06-01")
                if shock != 1.0:
                    kw["demand_shock"] = (shock, "2026-06-01")
                mf = metrics(world, simulate(world, "fixed", **kw))
                grid.append({"policy": "benchmark", "service_level": None, "delay_days": delay,
                             "demand_shock": shock, **{x: mf[x] for x in keys}})
                for sl in [0.85, 0.90, 0.95, 0.98, 0.995, None]:
                    mp = metrics(world, simulate(world, "prioritised", service_level=sl, **kw))
                    grid.append({"policy": "proposed", "service_level": sl if sl else "ABC (98/95/90)",
                                 "delay_days": delay, "demand_shock": shock, **{x: mp[x] for x in keys}})
        pd.DataFrame(grid).to_csv(OUT / "what_if_grid.csv", index=False)
    print(f"[7] what-if grid: {len(grid)} runs ({time.time()-t0:.0f}s)")

    # censored-demand example series for the chart: one style-store with heavy stockouts
    A = fixed.arrays
    s = world.series
    json.dump({"policy_comparison": comp.reset_index().rename(columns={"index": "metric"}).to_dict("records")},
              open(OUT / "policy_comparison.json", "w"), indent=1, default=float)

    from build_dashboard_data import build
    build(world, fixed, prio, sql_out, act, act_sizes, act_tr, seas_act, lead, comp, seeds_df,
          pd.DataFrame(grid), OUT)
    print(f"[done] {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main(scenarios="--quick" not in sys.argv, seeds=0 if "--quick" in sys.argv else 5)
