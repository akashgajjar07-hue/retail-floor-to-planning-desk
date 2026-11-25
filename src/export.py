"""Turn a simulation run into the tables a retailer's systems would hold."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def export_tables(world, res, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    s, A, cfg = world.series, res.arrays, world.cfg
    T, S = world.T, len(s)
    dates = world.dates

    # ---- dimensions ----
    st = world.stores[["store_id", "name", "base"]].rename(columns={"base": "traffic_index"})
    st.to_csv(out / "dim_store.csv", index=False)
    world.skus.drop(columns=["sku_idx", "base_daily_demand", "profile"]).to_csv(out / "dim_sku.csv", index=False)
    world.suppliers.rename(columns={"lead_time_mean_days": "quoted_lead_time_days",
                                    "lead_time_sd_days": "quoted_lead_time_sd_days"}).to_csv(out / "dim_supplier.csv", index=False)
    cal = pd.DataFrame({"date": dates, "day_of_week": dates.day_name(), "week_start": dates.to_period("W-SUN").start_time})
    cal["event"] = ""
    for e in cfg["events"]:
        m = (cal["date"] >= e["start"]) & (cal["date"] <= e["end"])
        cal.loc[m, "event"] = np.where(cal.loc[m, "event"] == "", e["name"], cal.loc[m, "event"] + "; " + e["name"])
    cal.to_csv(out / "dim_calendar.csv", index=False)

    # ---- daily inventory fact (observed) ----
    key = s[["store_id", "sku_id", "size"]]
    frames = []
    unit_cost = s["unit_cost"].to_numpy()
    for t in range(T):
        f = key.copy()
        f.insert(0, "date", dates[t])
        f["opening_units"] = A["open"][t]
        f["receipt_units"] = A["receipt"][t]
        f["transfer_in_units"] = A["transfer_in"][t]
        f["transfer_out_units"] = A["transfer_out"][t]
        f["sales_units"] = A["sales"][t]
        f["sales_value_aed"] = A["revenue"][t].round(2)
        f["clearance_units"] = A["salvage_units"][t]
        f["closing_units"] = A["close"][t]
        f["closing_value_at_cost_aed"] = (A["close"][t] * unit_cost).round(2)
        frames.append(f)
    inv = pd.concat(frames, ignore_index=True)
    int_cols = [c for c in inv.columns if c.endswith("_units")]
    inv[int_cols] = inv[int_cols].astype("int32")
    inv.to_parquet(out / "fact_inventory_daily.parquet", index=False)

    # ---- truth table: only exists because the data is synthetic ----
    tr = key.copy()
    tr = pd.concat([tr.assign(date=dates[t], true_demand_units=A["demand"][t].astype("int32"),
                              lost_sales_units=A["lost"][t].astype("int32")) for t in range(T)], ignore_index=True)
    tr[["date", "store_id", "sku_id", "size", "true_demand_units", "lost_sales_units"]].to_parquet(
        out / "truth_demand_daily.parquet", index=False)

    # ---- replenishment orders & receipts ----
    o = pd.DataFrame(res.orders, columns=["order_line_id", "t", "sid", "qty", "sup", "lead_time_days", "arr"])
    o["order_date"] = dates[o["t"].clip(upper=T - 1)].values
    o["expected_by_quote"] = o["order_date"] + pd.to_timedelta(
        world.suppliers["lead_time_mean_days"].to_numpy()[o["sup"]], unit="D")
    o["receipt_date"] = [world.cal_dates[a] if a < len(world.cal_dates) else pd.NaT for a in o["arr"]]
    o["status"] = np.where(o["arr"] < T, "received", "open")
    o = o.join(key, on="sid")
    o["supplier_id"] = world.suppliers["supplier_id"].to_numpy()[o["sup"]]
    o["order_id"] = "PO-" + o["order_line_id"].astype(str).str.zfill(5)
    o = o[["order_id", "order_date", "store_id", "sku_id", "size", "qty", "supplier_id",
           "expected_by_quote", "receipt_date", "lead_time_days", "status"]]
    o.loc[o["status"] == "open", ["receipt_date", "lead_time_days"]] = [pd.NaT, np.nan]
    o.to_csv(out / "fact_replenishment_orders.csv", index=False)

    rec = o[o["status"] == "received"][["receipt_date", "store_id", "sku_id", "size", "qty", "order_id"]]
    rec = rec.rename(columns={"receipt_date": "date", "qty": "units"}).assign(source="supplier order")
    seas = []
    for code, i0 in world.season_start_idx.items():
        if i0 < T:
            m = (s["season"] == code).to_numpy() & (world.seasonal_buy > 0)
            seas.append(key[m].assign(date=dates[i0], units=world.seasonal_buy[m].astype(int),
                                      order_id="SEASON-" + code, source="initial season allocation"))
    receipts = pd.concat([rec] + seas, ignore_index=True)[["date", "store_id", "sku_id", "size", "units", "source", "order_id"]]
    receipts.to_csv(out / "fact_receipts.csv", index=False)

    # ---- transfers, markdowns, promos ----
    sku_ids = world.skus["sku_id"].to_numpy()
    store_ids = world.stores["store_id"].to_numpy()
    tf = pd.DataFrame(res.transfers, columns=["t", "k", "from", "to", "units", "cost_aed", "expected_benefit_aed", "type"])
    if len(tf):
        tf = tf.assign(date=dates[tf["t"]].values, sku_id=sku_ids[tf["k"]],
                       from_store=store_ids[tf["from"]], to_store=store_ids[tf["to"]])
        tf = tf[["date", "sku_id", "from_store", "to_store", "units", "cost_aed", "expected_benefit_aed", "type"]]
    tf.to_csv(out / "fact_transfers.csv", index=False)
    md = pd.DataFrame(res.markdowns, columns=["t", "k", "markdown_pct"])
    md = md.assign(date=dates[md["t"]].values, sku_id=sku_ids[md["k"]])[["date", "sku_id", "markdown_pct"]]
    md.to_csv(out / "fact_markdowns.csv", index=False)
    pr = []
    P = world.promo[:T]
    for k, sku in enumerate(sku_ids):
        on = P[:, k].astype(int)
        if not on.any():
            continue
        edges = np.diff(np.concatenate([[0], on, [0]]))
        for a, b in zip(np.where(edges == 1)[0], np.where(edges == -1)[0] - 1):
            pr.append((sku, dates[a], dates[b], cfg["costs"]["promo_discount"]))
    pd.DataFrame(pr, columns=["sku_id", "start_date", "end_date", "discount_pct"]).to_csv(out / "fact_promotions.csv", index=False)
    return inv
