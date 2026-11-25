"""Collect everything the dashboard shows into one JSON file (outputs/dashboard_data.json)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd


def _rec(df):
    return json.loads(df.to_json(orient="records", date_format="iso"))


def build(world, fixed, prio, sql, act, act_sizes, act_tr, seas_act, lead, comp, seeds, grid, OUT):
    s, cfg = world.series, world.cfg
    T, dates = world.T, world.dates
    Af, Ap = fixed.arrays, prio.arrays
    cont = (s["replenishment"] == "continuous").to_numpy()
    cost = s["unit_cost"].to_numpy()
    price = s["price"].to_numpy()
    store_ids = world.stores["store_id"].tolist()

    # ---------- weekly comparison series (benchmark vs proposed) ----------
    wk = pd.Series(dates).dt.to_period("W-SUN").dt.start_time
    full_weeks = set(wk.value_counts()[lambda v: v == 7].index)   # charts use complete weeks only
    def weekly(A):
        inst = (A["close"][:, cont] > 0).mean(1)
        val = (A["close"] * cost).sum(1)
        df = pd.DataFrame({"week": wk, "instock": inst, "value": val,
                           "fill_num": A["sales"][:, cont].sum(1), "fill_den": A["demand"][:, cont].sum(1)})
        g = df.groupby("week").agg(instock=("instock", "mean"), value=("value", "last"),
                                   fill_num=("fill_num", "sum"), fill_den=("fill_den", "sum"))
        g["fill"] = g["fill_num"] / g["fill_den"]
        return g
    wf, wp = weekly(Af), weekly(Ap)
    wf, wp = wf[wf.index.isin(full_weeks)], wp[wp.index.isin(full_weeks)]
    trend = pd.DataFrame({"week": wf.index.strftime("%Y-%m-%d"),
                          "benchmark_instock": wf["instock"].round(4), "proposed_instock": wp["instock"].round(4),
                          "benchmark_fill": wf["fill"].round(4), "proposed_fill": wp["fill"].round(4),
                          "benchmark_value": wf["value"].round(0), "proposed_value": wp["value"].round(0)})

    # ---------- censored demand example (observed history) ----------
    ss = world.style_store
    best, best_rate = None, -1
    for _, x in ss.iterrows():
        sids = np.arange(x["first_sid"], x["last_sid"] + 1)
        if not cont[sids[0]] or len(sids) == 1:
            continue
        rate = Af["lost"][:, sids].sum()      # pick the line with the most lost units
        if rate > best_rate and s.iloc[sids[0]]["sku_id"].startswith("B"):
            best, best_rate = x, rate
    sids = np.arange(best["first_sid"], best["last_sid"] + 1)
    ex = pd.DataFrame({"week": wk, "sales": Af["sales"][:, sids].sum(1), "demand": Af["demand"][:, sids].sum(1),
                       "stockout_sizes": (Af["close"][:, sids] == 0).sum(1)})
    exw = ex.groupby("week").agg(sales=("sales", "sum"), demand=("demand", "sum"),
                                 stockout_size_days=("stockout_sizes", "sum")).reset_index()
    exw = exw[exw["week"].isin(full_weeks)]
    exw["week"] = exw["week"].dt.strftime("%Y-%m-%d")
    cens_example = {"store_id": best["store_id"], "sku_id": best["sku_id"],
                    "name": s.iloc[sids[0]]["name"], "size_stockout_rate": round(float((Af["close"][:, sids] == 0).mean()), 3),
                    "weeks": _rec(exw)}

    # ---------- estimated lost sales last 28 days, observed data only ----------
    t = T - 1
    PI = world.planner_index
    lo = t - 27
    est_rate = act_sizes.merge(act[["store_id", "sku_id", "d_forecast"]], on=["store_id", "sku_id"])
    # per-size de-indexed rate from in-stock days in the window
    sl, cl = Af["sales"][lo:t + 1], Af["close"][lo:t + 1]
    adj = sl / np.maximum(PI[lo:t + 1], 1e-9)
    est_lost = 0.0
    for j in np.where(cont)[0]:
        unc = cl[:, j] > 0
        rate = (adj[unc, j].sum() + 7 * world.base_rate[j]) / (unc.sum() + 7)
        out_days = ~unc
        est_lost += np.maximum(0, rate * PI[lo:t + 1, j][out_days] - sl[out_days, j]).sum()
    true_lost = Af["lost"][lo:t + 1][:, cont].sum()

    health = sql["01_inventory_health"]
    tot = health[health["category"].isna()].iloc[0]
    basics_act = act.copy()
    # exposure: even counting stock already on order, the line runs short before a new order could land
    exposure = basics_act[basics_act["inventory_position"] < basics_act["d_forecast"] * basics_act["lead_time_days"]]
    kpis = {
        "inventory_value_aed": float(tot["inventory_value_aed"]),
        "units_on_hand": float(tot["units_on_hand"]),
        "days_cover": float(tot["days_cover"]),
        "excess_value_aed": float(tot["excess_value_aed"]),
        "stockout_exposure_style_stores": int(len(exposure)),
        "replenished_style_stores": int(len(basics_act)),
        "core_size_out_style_stores": int(basics_act["core_size_out"].sum()),
        "est_lost_units_28d": round(float(est_lost), 0),
        "true_lost_units_28d": float(true_lost),
        "orders_recommended": int((act["action"] == "ORDER").sum()),
        "order_units_recommended": int(act["order_qty"].sum()),
    }

    # ---------- size availability heat & broken runs ----------
    sa = sql["04_size_availability"]
    heat = sa[sa["metric"] == "size_availability"][["store_id", "size", "value"]]
    broken = sa[sa["metric"] == "broken_size_run_rate"][["store_id", "week_start", "value"]]
    broken = broken[pd.to_datetime(broken["week_start"]).isin(full_weeks)]
    broken = broken.assign(week_start=pd.to_datetime(broken["week_start"]).dt.strftime("%Y-%m-%d"))

    # ---------- planted-issue detection ----------
    tf_p = pd.DataFrame(prio.transfers, columns=["t", "k", "from", "to", "units", "cost", "benefit", "type"])
    sku_ids = world.skus["sku_id"].to_numpy()
    tf_p["sku_id"] = sku_ids[tf_p["k"]]
    tf_p["from_store"] = np.array(store_ids)[tf_p["from"]]
    tf_p["to_store"] = np.array(store_ids)[tf_p["to"]]
    p1 = tf_p[(tf_p["from_store"] == "AUH-03") & tf_p["sku_id"].isin(["B01", "B02", "B04", "B05"]) & (tf_p["t"] < 120)]
    jan_doc = []
    t31 = 30
    for _, x in ss.iterrows():
        sd = np.arange(x["first_sid"], x["last_sid"] + 1)
        if not cont[sd[0]]:
            continue
        r = Af["sales"][:t31 + 1, sd].sum() / (t31 + 1)
        jan_doc.append((x["store_id"], x["sku_id"], Af["close"][t31, sd].sum() / max(r, 1e-9)))
    jd = pd.DataFrame(jan_doc, columns=["store_id", "sku_id", "doc"]).sort_values("doc", ascending=False)
    top6 = jd.head(6)
    seasons_sku = dict(zip(world.skus["sku_id"], world.skus["season"].fillna("")))
    p2 = tf_p[(tf_p["type"] == "seasonal") & tf_p["sku_id"].map(lambda k: seasons_sku[k] == "SUMMER")]
    p2_flow = p2.groupby(["from_store", "to_store"])["units"].sum().reset_index().sort_values("units", ascending=False)
    summer = (s["season"] == "SUMMER").to_numpy()
    left_by_store = {st: int(Af["salvage_units"][:, summer & (s["store_id"] == st).to_numpy()].sum()) for st in store_ids}
    lt = sql["06_supplier_lead_times"]
    supc = act[act["supplier_id"] == "SUP-C"]
    ratio_c = float((supc["safety_stock"] / supc["safety_stock_if_lead_time_constant"]).mean())
    supb = (s["supplier_id"] == "SUP-B").to_numpy() & cont
    oth = cont & ~supb
    fill_b = float(Af["sales"][:, supb].sum() / Af["demand"][:, supb].sum())
    fill_o = float(Af["sales"][:, oth].sum() / Af["demand"][:, oth].sum())
    fill_b_p = float(Ap["sales"][:, supb].sum() / Ap["demand"][:, supb].sum())
    br = lambda A, a=0, b=42: float(np.mean([  # noqa: E731
        ((A["close"][a:b, sd][:, [2, 3]] == 0).any(1) & (A["close"][a:b, sd].sum(1) > 0)).mean()
        for sd in [np.arange(x["first_sid"], x["last_sid"] + 1) for _, x in ss.iterrows()]
        if cont[sd[0]] and len(sd) == 6]))
    detection = [
        {"id": "P1", "planted": cfg["planted_issues"][0]["what"],
         "found": f"Six highest days-of-cover lines on 31 Jan: {', '.join(f'{a} {b}' for a, b in zip(top6.store_id, top6.sku_id))}. "
                  f"The proposed policy used AUH-03 as a transfer donor {len(p1)} times ({int(p1.units.sum())} units) in the first four months.",
         "verdict": "Found" if sum((top6.store_id == "AUH-03") & top6.sku_id.isin(["B01", "B02", "B04", "B05"])) == 4 else "Partly found"},
        {"id": "P2", "planted": cfg["planted_issues"][1]["what"],
         "found": f"Benchmark summer leftover by store: {', '.join(f'{k} {v}' for k, v in left_by_store.items())} units. "
                  f"The proposed policy's largest summer transfer flow: {p2_flow.iloc[0].from_store} to {p2_flow.iloc[0].to_store} "
                  f"({int(p2_flow.iloc[0].units)} units).",
         "verdict": "Found" if (p2_flow.iloc[0].from_store == "AUH-02" and p2_flow.iloc[0].to_store == "AUH-01") else "Partly found"},
        {"id": "P3", "planted": cfg["planted_issues"][2]["what"],
         "found": f"Lead-time analysis ranks {lt.iloc[0].supplier_id} most variable (sd {lt.iloc[0].actual_sd_days} days, "
                  f"{lt.iloc[0].share_more_than_3_days_late:.0%} of orders more than 3 days late). Safety stock for its items is "
                  f"{ratio_c:.1f}x what the constant-lead-time formula gives.",
         "verdict": "Found" if lt.iloc[0].supplier_id == "SUP-C" else "Not found"},
        {"id": "P4", "planted": cfg["planted_issues"][3]["what"],
         "found": f"Benchmark fill rate on Supplier B items {fill_b:.1%} vs {fill_o:.1%} on other replenished items; "
                  f"proposed policy lifts Supplier B items to {fill_b_p:.1%}.",
         "verdict": "Found" if fill_b < fill_o else "Not found"},
        {"id": "P5", "planted": cfg["planted_issues"][4]["what"],
         "found": f"Share of style-store-days with M or L out while other sizes remain. First six weeks: "
                  f"benchmark {br(Af):.1%}, proposed {br(Ap):.1%} - no reorder can land before the 28-day Supplier B lead time. "
                  f"Weeks 7-26, after size-curve orders arrive: benchmark {br(Af, 42, 182):.1%}, proposed {br(Ap, 42, 182):.1%}.",
         "verdict": "Found after one lead time" if br(Ap, 42, 182) < br(Af, 42, 182) else "Not found"},
    ]

    # ---------- worked example for the calculator ----------
    ex_row = act[(act["store_id"] == "AUH-01") & (act["sku_id"] == "B01")].iloc[0]
    worked = {k: (v.item() if hasattr(v, "item") else v) for k, v in ex_row.to_dict().items()}

    cmp_ = comp.reset_index().rename(columns={"index": "metric"})
    seeds_summary = {}
    if len(seeds):
        for m in ["net_contribution_aed", "fill_rate_basics", "avg_inventory_value_aed", "sell_through_SUMMER",
                  "revenue_RAMADAN_EID_aed"]:
            d = seeds[f"proposed_{m}"] - seeds[f"benchmark_{m}"]
            seeds_summary[m] = {"min": float(d.min()), "max": float(d.max()), "mean": float(d.mean()),
                                "n_better": int((d > 0).sum()), "n": int(len(d))}

    top_orders = act[act["action"] == "ORDER"].sort_values("priority_score_aed", ascending=False)
    data = {
        "meta": {"as_of": cfg["as_of_date"], "start": cfg["start_date"], "seed": cfg["seed"],
                 "stores": _rec(world.stores[["store_id", "name"]]), "n_skus": int(len(world.skus)),
                 "n_series": int(len(s)), "currency": "AED",
                 "dc_capacity_units_per_day": round(float(world.dc_capacity), 0)},
        "kpis": kpis,
        "health_by_category": _rec(health.fillna({"category": "All"})),
        "trend": _rec(trend),
        "abc_xyz": _rec(sql["03_abc_xyz"]),
        "size_heat": _rec(heat),
        "broken_runs": _rec(broken),
        "stores": _rec(sql["05_store_comparison"]),
        "suppliers": _rec(lt),
        "censoring_bands": _rec(sql["07_censored_demand"]),
        "censoring_example": cens_example,
        "orders": _rec(top_orders.head(40)),
        "orders_all_count": int(len(top_orders)),
        "seasonal_actions": _rec(seas_act.sort_values(["lever", "projected_leftover"])),
        "basics_transfers": _rec(act_tr) if len(act_tr) else [],
        "policy_comparison": _rec(cmp_),
        "seeds_summary": seeds_summary,
        "what_if": _rec(grid) if len(grid) else [],
        "detection": detection,
        "worked_example": worked,
        "assumptions": {
            "holding_rate": cfg["costs"]["annual_holding_rate"],
            "service_levels": cfg["policy_prioritised"]["service_level_by_abc"],
            "transfer_fixed": cfg["costs"]["transfer_fixed_aed"], "transfer_unit": cfg["costs"]["transfer_per_unit_aed"],
        },
    }
    (OUT / "dashboard_data.json").write_text(json.dumps(data, default=float))
    return data
