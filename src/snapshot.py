"""
Planner's action list as of the snapshot date, built ONLY from observed data
(sales, stock, orders) plus the planning calendar. True demand is never used.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from planning import (largest_remainder, order_up_to, reorder_point, safety_stock,
                      shrunk_mean, transfer_is_worth_it, z_value)


def supplier_lead_stats(world, res, t_asof):
    o = pd.DataFrame(res.orders, columns=["id", "t", "sid", "qty", "sup", "L", "arr"])
    po = o[o["arr"] <= t_asof].drop_duplicates("id")
    out = {}
    for i, sp in world.suppliers.iterrows():
        x = po[po["sup"] == i]["L"]
        if len(x) >= 5:
            out[i] = (float(x.mean()), max(float(x.std(ddof=1)), 1.0), len(x))
        else:
            out[i] = (float(sp["lead_time_mean_days"]), float(sp["lead_time_sd_days"]), len(x))
    return out


def basics_actions(world, res, abc_by_sku: dict):
    cfg, s, A = world.cfg, world.series, res.arrays
    pp, costs = cfg["policy_prioritised"], cfg["costs"]
    T = world.T
    t = T - 1
    PI = world.planner_index
    W, Rv = pp["demand_window_days"], pp["review_period_days"]
    lo = t - W + 1
    sales, close = A["sales"][lo:t + 1], A["close"][lo:t + 1]
    adj = sales / np.maximum(PI[lo:t + 1], 1e-9)
    unc = close > 0
    o = pd.DataFrame(res.orders, columns=["id", "t", "sid", "qty", "sup", "L", "arr"])
    on_order = o[o["arr"] > t].groupby("sid")["qty"].sum().reindex(range(len(s)), fill_value=0).to_numpy()
    lead = supplier_lead_stats(world, res, t)
    rows, size_rows = [], []
    for _, x in world.style_store.iterrows():
        sids = np.arange(x["first_sid"], x["last_sid"] + 1)
        r0 = s.iloc[sids[0]]
        if r0["replenishment"] != "continuous":
            continue
        d_base = np.array([shrunk_mean(adj[unc[:, j], j], world.base_rate[j], 7) for j in sids])
        L, sL, n_po = lead[r0["supplier_idx"]]
        fwd = PI[t + 1:t + 1 + int(round(L)), sids].mean(0)
        d = float((d_base * fwd).sum())
        full = unc[:, sids].all(1)
        if full.sum() >= 10:
            sd_base, sd_src = float(adj[full][:, sids].sum(1).std(ddof=1)), f"{int(full.sum())} fully in-stock days"
        else:
            db = d_base.sum()
            sd_base, sd_src = float(np.sqrt(db + 0.25 * db ** 2)), "assumed (too few fully in-stock days)"
        sd = max(sd_base * fwd.mean(), np.sqrt(max(d, 1e-9)))
        abc = abc_by_sku.get(r0["sku_id"], "C")
        sl = pp["service_level_by_abc"][abc]
        z = z_value(sl)
        ss_units = safety_stock(z, d, sd, L, sL)
        ss_textbook = z * sd * np.sqrt(L)
        rop = reorder_point(d, L, ss_units)
        upto = order_up_to(rop, d, Rv)
        oh = A["close"][t, sids]
        oo = on_order[sids]
        ip = oh.sum() + oo.sum()
        order = ip <= rop
        qty = int(np.ceil(upto - ip)) if order else 0
        share = d_base / max(d_base.sum(), 1e-9)
        need = np.maximum(0, upto * share - oh - oo)
        split = largest_remainder(qty, need if need.sum() > 0 else share) if qty else np.zeros(len(sids), int)
        margin = r0["price"] - r0["unit_cost"]
        shortfall = max(0.0, rop - ip) + max(0.0, d * L - ip)
        days_cover = oh.sum() / d if d > 0 else np.inf
        core_out = any((s.iloc[j]["size"] in ("M", "L")) and oh[k] == 0 for k, j in enumerate(sids))
        rows.append(dict(
            store_id=x["store_id"], sku_id=x["sku_id"], name=r0["name"], supplier_id=r0["supplier_id"],
            abc=abc, service_level=sl, z=round(z, 3),
            d_forecast=round(d, 2), sd_daily=round(sd, 2), sd_source=sd_src,
            lead_time_days=round(L, 1), lead_time_sd_days=round(sL, 1), purchase_orders_observed=n_po,
            safety_stock=round(ss_units, 1), safety_stock_if_lead_time_constant=round(ss_textbook, 1),
            reorder_point=round(rop, 1), order_up_to=round(upto, 1),
            on_hand=int(oh.sum()), on_order=int(oo.sum()), inventory_position=int(ip),
            days_cover=round(days_cover, 1) if np.isfinite(days_cover) else None,
            action="ORDER" if order else ("EXCESS" if days_cover > 90 else "HOLD"),
            order_qty=qty, size_split=" ".join(f"{s.iloc[j]['size']}:{q}" for j, q in zip(sids, split)) if qty else "",
            priority_score_aed=round(shortfall * margin, 0),
            core_size_out=core_out,
            unit_margin_aed=margin,
        ))
        for j, k_ in zip(sids, range(len(sids))):
            size_rows.append(dict(store_id=x["store_id"], sku_id=x["sku_id"], size=s.iloc[j]["size"],
                                  size_share=round(share[k_], 4), on_hand=int(oh[k_]), on_order=int(oo[k_]),
                                  order_qty=int(split[k_])))
    act = pd.DataFrame(rows)
    act["priority_rank"] = act["priority_score_aed"].rank(ascending=False, method="first").astype(int)
    act.loc[act["action"] != "ORDER", "priority_rank"] = None

    # transfer candidates (same rule as the simulator)
    tr = []
    for sku, g in act.groupby("sku_id"):
        donors = g[g["days_cover"].fillna(999) > pp["transfer_donor_min_days_cover"]]
        recv = g[(g["days_cover"].fillna(999) < g["lead_time_days"])]
        for _, r in recv.iterrows():
            if donors.empty:
                break
            dn = donors.sort_values("days_cover", ascending=False).iloc[0]
            need = max(0, int(round(r["d_forecast"] * r["lead_time_days"] - r["inventory_position"])))
            need = max(need, int(round(r["d_forecast"] * r["lead_time_days"] - r["on_hand"])) if r["on_order"] == 0 else need)
            excess = int(max(0, dn["on_hand"] - dn["d_forecast"] * pp["transfer_donor_keep_days_cover"]))
            units = min(need, excess)
            if units <= 0:
                continue
            ok, ben, cst = transfer_is_worth_it(units, r["unit_margin_aed"], pp["transfer_sell_probability"],
                                                costs["transfer_fixed_aed"], costs["transfer_per_unit_aed"])
            tr.append(dict(sku_id=sku, name=r["name"], from_store=dn["store_id"], to_store=r["store_id"],
                           units=units, donor_days_cover=dn["days_cover"], receiver_days_cover=r["days_cover"],
                           receiver_lead_time_days=r["lead_time_days"], expected_margin_saved_aed=round(ben, 0),
                           transfer_cost_aed=round(cst, 0), recommend="TRANSFER" if ok else "NOT WORTH IT"))
    return act, pd.DataFrame(size_rows), pd.DataFrame(tr), lead


def seasonal_actions(world, res, code="AW"):
    """In-season review for a running season: sell-through vs plan, projected leftover, lever."""
    cfg, s, A = world.cfg, world.series, res.arrays
    pp = cfg["policy_prioritised"]
    T, PI = world.T, world.planner_index
    t = T - 1
    i0, i1 = world.season_start_idx[code], world.season_end_idx[code]
    el = cfg["costs"]["markdown_price_elasticity"]
    rows = []
    lo = max(i0, t - 13)
    for _, x in world.style_store.iterrows():
        sids = np.arange(x["first_sid"], x["last_sid"] + 1)
        r0 = s.iloc[sids[0]]
        if r0["season"] != code:
            continue
        bought = world.seasonal_buy[sids].sum()
        sold = A["sales"][i0:t + 1, sids].sum()
        tin = A["transfer_in"][i0:t + 1, sids].sum(); tout = A["transfer_out"][i0:t + 1, sids].sum()
        plan_to_date = (world.base_rate[sids][None, :] * PI[i0:t + 1, sids]).sum()
        plan_season = (world.base_rate[sids][None, :] * PI[i0:i1 + 1, sids]).sum()
        sw, cw = A["sales"][lo:t + 1, sids], A["close"][lo:t + 1, sids]
        est = np.array([shrunk_mean(sw[cw[:, j] > 0, j] / np.maximum(PI[lo:t + 1, sid][cw[:, j] > 0], 1e-9),
                                    world.base_rate[sid], 3) for j, sid in enumerate(sids)])
        rem = (est * PI[t + 1:i1 + 1, sids].sum(0)).sum()
        oh = A["close"][t, sids].sum()
        rows.append(dict(store_id=x["store_id"], sku_id=x["sku_id"], name=r0["name"],
                         bought=int(bought), sold=int(sold), net_transfers=int(tin - tout), on_hand=int(oh),
                         sell_through=round(sold / max(bought + tin - tout, 1), 3),
                         plan_sell_through=round(plan_to_date / max(plan_season, 1e-9), 3),
                         projected_remaining_demand=round(rem, 0),
                         projected_leftover=round(oh - rem, 0),
                         weeks_left=round((i1 - t) / 7, 1)))
    df = pd.DataFrame(rows)
    out = []
    for sku, g in df.groupby("sku_id"):
        chain_left = g["projected_leftover"].clip(lower=0).sum() / max(g["on_hand"].sum(), 1)
        for _, r in g.iterrows():
            if r["projected_leftover"] < -0.2 * r["projected_remaining_demand"] and r["projected_leftover"] < -4:
                lever = "RECEIVE TRANSFER"
            elif r["projected_leftover"] > 0.25 * r["projected_remaining_demand"] and r["projected_leftover"] > 4:
                lever = "SEND TRANSFER" if (g["projected_leftover"] < -4).any() else "MARKDOWN CANDIDATE"
            else:
                lever = "HOLD"
            out.append({**r.to_dict(), "chain_projected_leftover_share": round(chain_left, 3), "lever": lever})
    return pd.DataFrame(out)
