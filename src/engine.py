"""
Day-by-day inventory simulator.

Both policies run on exactly the same true demand (same seed, same common
random numbers, same lead-time draws), so any difference in results comes
from the policy, not from luck.

Key modelling choices (deliberate, and explained in the README):
  * Store retail has no backorders. If the size is not on the rail the sale is
    lost. Inventory position = on hand + on order + in transit.
  * Observed sales = min(true demand, stock). Lost demand is invisible.
  * Basics (continuous items) are planned at style level and allocated to sizes.
  * Seasonal items are bought once; the in-season levers are transfer,
    markdown and hold, never a reorder.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.stats import poisson

from planning import (abc_class, largest_remainder, order_up_to, reorder_point,
                      safety_stock, shrunk_mean, transfer_is_worth_it, z_value)


@dataclass
class SimResult:
    policy: str
    label: str
    arrays: dict
    orders: list = field(default_factory=list)
    transfers: list = field(default_factory=list)
    markdowns: list = field(default_factory=list)
    salvage: list = field(default_factory=list)
    params: list = field(default_factory=list)


def simulate(world, policy="fixed", label=None, service_level=None,
             delay=None, demand_shock=None, record_params=False):
    """
    policy        : "fixed" (benchmark) or "prioritised" (proposed)
    service_level : override the ABC-based service levels with one value
    delay         : (supplier_id, extra_days, from_date) - actual lead times grow; nobody is told
    demand_shock  : (multiplier, from_date) - true demand changes; the plan does not
    """
    cfg, s = world.cfg, world.series
    T, S = world.T, len(s)
    Tc = len(world.cal_dates)
    PI = world.planner_index
    costs = cfg["costs"]
    pf, pp = cfg["policy_fixed"], cfg["policy_prioritised"]
    rng = np.random.default_rng(cfg["seed"] + 7)       # only for FIFO order shuffling

    ssid = s["ssid"].to_numpy()
    sku_idx = s["sku_idx"].to_numpy()
    store_idx = s["store_idx"].to_numpy()
    sup_idx = s["supplier_idx"].to_numpy()
    price = s["price"].to_numpy(float)
    cost = s["unit_cost"].to_numpy(float)
    margin = price - cost
    cont = (s["replenishment"] == "continuous").to_numpy()
    season = s["season"].fillna("").to_numpy()
    base = world.base_rate
    promo_disc = costs["promo_discount"]
    elasticity = costs["markdown_price_elasticity"]

    ss_df = world.style_store
    n_ss = len(ss_df)
    ss_sids = [np.arange(a, b + 1) for a, b in zip(ss_df["first_sid"], ss_df["last_sid"])]
    ss_cont = np.array([cont[x[0]] for x in ss_sids])
    ss_sup = np.array([sup_idx[x[0]] for x in ss_sids])
    ss_sku = ss_df["sku_idx"].to_numpy()
    ss_store = ss_df["store_idx"].to_numpy()
    ss_margin = np.array([margin[x[0]] for x in ss_sids])
    n_sku = len(world.skus)
    n_store = len(world.stores)
    sku_ss = {k: np.where(ss_sku == k)[0] for k in range(n_sku)}   # style-store ids per sku

    dates = world.dates
    delay_sup = delay_days = delay_from = None
    if delay:
        delay_sup = int(world.suppliers.index[world.suppliers["supplier_id"] == delay[0]][0])
        delay_days, delay_from = delay[1], dates.get_loc(pd.Timestamp(delay[2]))
    shock_mult, shock_from = (1.0, T)
    if demand_shock:
        shock_mult, shock_from = demand_shock[0], dates.get_loc(pd.Timestamp(demand_shock[1]))

    # -------- state --------
    H = Tc + 200
    incoming = np.zeros((H, S))       # supplier orders arriving
    tr_in = np.zeros((H, S))          # transfers arriving
    on_hand = world.opening_stock.astype(float).copy()
    on_order = np.zeros(S)
    in_transit = np.zeros(S)
    md = np.zeros(n_sku)              # current markdown by sku (chain price)
    md_last_change = np.full(n_sku, -999)
    md_step = np.zeros(n_sku, dtype=int)

    A = {k: np.zeros((T, S)) for k in
         ["open", "receipt", "transfer_in", "transfer_out", "demand", "sales",
          "lost", "close", "revenue", "salvage_units"]}
    res = SimResult(policy=policy, label=label or policy, arrays=A)
    order_id = 0

    # season start / end inside the simulated window
    season_start = {c: i for c, i in world.season_start_idx.items() if i < T}
    season_end = {c: i for c, i in world.season_end_idx.items() if i < T}
    season_days = {c: (world.season_start_idx[c], world.season_end_idx[c]) for c in world.season_start_idx}

    # -------- fixed-policy plan (set once, never updated) --------
    _bc = np.array(list(cfg["base_size_curve"].values()))
    buyer_curve = _bc / _bc.sum()
    mean_pi = PI[:T].mean(0)
    plan_style = np.array([(base[x] * mean_pi[x]).sum() for x in ss_sids])
    fx_min = pf["min_days_of_plan"] * plan_style
    fx_q = np.round(pf["order_qty_days_of_plan"] * plan_style)

    # -------- prioritised-policy state --------
    plan_rev = plan_style * ss_margin
    abc = np.array(["C"] * n_ss, dtype=object)
    abc[ss_cont] = abc_class(plan_rev[ss_cont])
    sl_map = pp["service_level_by_abc"]
    z_ss = np.array([z_value(service_level if service_level else sl_map[c]) for c in abc])
    rop = np.full(n_ss, np.inf)
    upto = np.zeros(n_ss)
    d_size = base.copy()              # base-rate estimate per series (de-indexed)
    d_style_fc = np.zeros(n_ss)
    L_obs = {i: [] for i in range(len(world.suppliers))}
    sup_spec = world.suppliers[["lead_time_mean_days", "lead_time_sd_days"]].to_numpy(float)
    W, R = pp["demand_window_days"], pp["review_period_days"]
    review_dow = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].index(pp["review_day"])

    arrivals = {}
    last_sent, last_recv = {}, {}

    def lead_stats(i):
        obs = L_obs[i]
        if len(obs) >= 5:
            return float(np.mean(obs)), max(float(np.std(obs, ddof=1)), 1.0)
        return sup_spec[i, 0], sup_spec[i, 1]

    def place_order(t, x_ss, qty_sizes):
        nonlocal order_id
        sids = ss_sids[x_ss]
        sp = ss_sup[x_ss]
        L = int(world.lead_time[sp, t])
        if delay_sup is not None and sp == delay_sup and t >= delay_from:
            L += delay_days
        arr = t + L
        arrivals.setdefault(arr, []).append((sp, L))
        for sid, q in zip(sids, qty_sizes):
            if q <= 0:
                continue
            incoming[arr, sid] += q
            on_order[sid] += q
            res.orders.append((order_id, t, sid, int(q), sp, L, arr))
        order_id += 1

    # ======================= daily loop =======================
    for t in range(T):
        A["open"][t] = on_hand
        # receipts
        rec = incoming[t].copy()
        if rec.any():
            on_order -= rec
        for code, i0 in season_start.items():
            if t == i0:
                m = season == code
                rec[m] += world.seasonal_buy[m]
        on_hand += rec
        A["receipt"][t] = rec
        tin = tr_in[t]
        on_hand += tin
        in_transit -= tin
        A["transfer_in"][t] = tin

        # demand (common random numbers -> identical draws when stock/price are identical)
        lam = base * PI[t] * world.noise[t, ssid]
        if t >= shock_from:
            lam = lam * shock_mult
        md_mult = (1.0 - md[sku_idx]) ** (-elasticity)
        lam = lam * md_mult
        dem = np.where(lam > 0, poisson.ppf(world.uniforms[t], np.maximum(lam, 1e-12)), 0.0)
        sales = np.minimum(dem, on_hand)
        on_hand -= sales
        promo_on = world.promo[t, sku_idx]
        unit_rev = price * np.where(promo_on, 1 - promo_disc, 1.0) * (1 - md[sku_idx])
        A["demand"][t], A["sales"][t], A["lost"][t] = dem, sales, dem - sales
        A["revenue"][t] = sales * unit_rev

        # season end: leftover sold to a jobber at salvage value
        for code, i1 in season_end.items():
            if t == i1:
                m = (season == code) & (on_hand > 0)
                A["salvage_units"][t][m] = on_hand[m]
                for sid in np.where(m)[0]:
                    res.salvage.append((t, sid, on_hand[sid]))
                on_hand[m] = 0

        dow = dates[t].dayofweek
        tout = np.zeros(S)

        # ================= FIXED (benchmark) =================
        if policy == "fixed":
            # seasonal: one late markdown, no transfers
            for code, (i0, i1) in season_days.items():
                if i0 <= t <= i1 and (i1 - t) < pf["season_end_markdown_days"]:
                    for k in np.unique(sku_idx[season == code]):
                        if md[k] < pf["season_end_markdown"]:
                            md[k] = pf["season_end_markdown"]
                            res.markdowns.append((t, k, md[k]))
            # basics: min/max on the annual plan, first come first served at the DC
            reqs = []
            for x in np.where(ss_cont)[0]:
                sids = ss_sids[x]
                ip = on_hand[sids].sum() + on_order[sids].sum()
                if ip <= fx_min[x]:
                    reqs.append((x, fx_q[x]))
            rng.shuffle(reqs)
            cap = world.dc_capacity
            for x, q in reqs:
                if cap <= 0:
                    break
                q = min(q, np.floor(cap))
                if q <= 0:
                    continue
                cap -= q
                # standard buying size ratio, the same for every store and never updated
                place_order(t, x, largest_remainder(q, buyer_curve[:len(ss_sids[x])] if len(ss_sids[x]) > 1 else [1.0]))

        # ================= PRIORITISED (proposed) =================
        else:
            # learn lead times from orders that arrived today
            for sp_, L_ in arrivals.pop(t, []):
                L_obs[sp_].append(L_)

            # ---- weekly re-estimation of basics parameters ----
            if dow == review_dow or t == 0:
                lo = max(0, t - W + 1)
                if t > 0:
                    sales_w = A["sales"][lo:t + 1]
                    close_w = np.vstack([A["close"][lo:t], on_hand[None, :]]) if t > lo else on_hand[None, :]
                    pi_w = PI[lo:t + 1]
                    adj = np.where(pi_w > 0, sales_w / np.maximum(pi_w, 1e-9), 0.0)
                    uncens = close_w > 0
                for x in np.where(ss_cont)[0]:
                    sids = ss_sids[x]
                    if t > 0:
                        for sid in sids:
                            d_size[sid] = shrunk_mean(adj[uncens[:, sid], sid], base[sid], 7)
                    L, sL = lead_stats(ss_sup[x])
                    Li = int(round(L))
                    fwd = PI[t + 1:t + 1 + Li, sids].mean(0)
                    d = float((d_size[sids] * fwd).sum())
                    # style-level daily sd from fully in-stock days, else Poisson+noise assumption
                    sd_base = None
                    if t > 0:
                        full = uncens[:, sids].all(1)
                        if full.sum() >= 10:
                            sd_base = float(adj[full][:, sids].sum(1).std(ddof=1))
                    dbase = float(d_size[sids].sum())
                    if sd_base is None:
                        sd_base = np.sqrt(dbase + 0.25 * dbase ** 2)
                    sd = max(sd_base * fwd.mean(), np.sqrt(max(d, 1e-9)))
                    ss_units = safety_stock(z_ss[x], d, sd, L, sL)
                    rop[x] = reorder_point(d, L, ss_units)
                    upto[x] = order_up_to(rop[x], d, R)
                    d_style_fc[x] = d
                    if record_params:
                        res.params.append((t, x, d, sd, L, sL, z_ss[x], ss_units, rop[x], upto[x]))

            # ---- basics transfers: receiver will run out before resupply, donor has excess ----
            look = incoming[t + 1:t + 61]
            has_inc = look > 0
            next_arr = np.where(has_inc.any(0), has_inc.argmax(0) + 1, 999)
            for k in range(n_sku):
                xs = sku_ss[k]
                if not ss_cont[xs[0]]:
                    continue
                d_st = d_style_fc[xs]
                oh_st = np.array([on_hand[ss_sids[x]].sum() for x in xs])
                doc = np.where(d_st > 0, oh_st / np.maximum(d_st, 1e-9), 999)
                donors = [i for i in range(len(xs)) if doc[i] > pp["transfer_donor_min_days_cover"]]
                if not donors:
                    continue
                L, _ = lead_stats(ss_sup[xs[0]])
                for ri in np.argsort(doc):
                    if ri in donors or doc[ri] >= L:
                        continue
                    rx = xs[ri]
                    r_sids = ss_sids[rx]
                    share = d_size[r_sids] / max(d_size[r_sids].sum(), 1e-9)
                    d_r = d_st[ri] * share
                    horizon = np.minimum(next_arr[r_sids], L)
                    need = np.maximum(0, np.round(d_r * horizon - on_hand[r_sids] - in_transit[r_sids]))
                    if need.sum() <= 0:
                        continue
                    di = max(donors, key=lambda i: doc[i])
                    dx = xs[di]
                    d_sids = ss_sids[dx]
                    dshare = d_size[d_sids] / max(d_size[d_sids].sum(), 1e-9)
                    keep = d_st[di] * dshare * pp["transfer_donor_keep_days_cover"]
                    excess = np.maximum(0, np.floor(on_hand[d_sids] - keep))
                    move = np.minimum(need, excess)
                    ok, ben, cst = transfer_is_worth_it(move.sum(), ss_margin[rx],
                                                        pp["transfer_sell_probability"],
                                                        costs["transfer_fixed_aed"],
                                                        costs["transfer_per_unit_aed"])
                    if move.sum() > 0 and ok:
                        on_hand[d_sids] -= move
                        tout[d_sids] += move
                        arr = t + costs["transfer_lead_time_days"]
                        tr_in[arr, r_sids] += move
                        in_transit[r_sids] += move
                        res.transfers.append((t, k, ss_store[dx], ss_store[rx], int(move.sum()),
                                              float(cst), float(ben), "basics"))
                        oh_st[di] -= move.sum()
                        doc[di] = oh_st[di] / max(d_st[di], 1e-9)
                        if doc[di] <= pp["transfer_donor_min_days_cover"]:
                            donors.remove(di)
                            if not donors:
                                break

            # ---- basics replenishment with priority under DC capacity ----
            reqs = []
            for x in np.where(ss_cont)[0]:
                sids = ss_sids[x]
                ip = on_hand[sids].sum() + on_order[sids].sum() + in_transit[sids].sum()
                if ip <= rop[x]:
                    q = np.ceil(upto[x] - ip)
                    if q > 0:
                        L, _ = lead_stats(ss_sup[x])
                        shortfall = max(0.0, rop[x] - ip) + max(0.0, d_style_fc[x] * L - ip)
                        reqs.append((shortfall * ss_margin[x], x, q))
            reqs.sort(key=lambda r: -r[0])
            cap = world.dc_capacity
            for _, x, q in reqs:
                if cap <= 0:
                    break
                q = min(q, np.floor(cap))
                if q <= 0:
                    continue
                cap -= q
                sids = ss_sids[x]
                share = d_size[sids] / max(d_size[sids].sum(), 1e-9)
                target = upto[x] * share
                need = np.maximum(0, target - on_hand[sids] - on_order[sids] - in_transit[sids])
                place_order(t, x, largest_remainder(q, need if need.sum() > 0 else share))

            # ---- seasonal weekly review: transfer, markdown, hold ----
            if dow == review_dow:
                for code, (i0, i1) in season_days.items():
                    if not (i0 + 7 <= t <= i1):
                        continue
                    days_left = i1 - t
                    for k in np.unique(sku_idx[season == code]):
                        xs = sku_ss[k]
                        lo = max(i0, t - 13)
                        proj, dem_rem = {}, {}
                        for x in xs:
                            sids = ss_sids[x]
                            pi_w = PI[lo:t + 1, sids]
                            sw = A["sales"][lo:t + 1, sids]
                            cw = A["close"][lo:t + 1, sids].copy()
                            cw[-1] = on_hand[sids]
                            mult = (1 - md[k]) ** (-elasticity)
                            est = np.array([shrunk_mean(sw[cw[:, j] > 0, j] /
                                                        np.maximum(pi_w[cw[:, j] > 0, j] * mult, 1e-9),
                                                        base[sid], 3)
                                            for j, sid in enumerate(sids)])
                            rem_pi = PI[t + 1:i1 + 1, sids].sum(0)
                            proj[x] = on_hand[sids] - est * rem_pi * mult
                            dem_rem[x] = est * rem_pi * mult
                        # transfers between stores, size by size
                        if days_left >= pp["seasonal_transfer_min_days_left"]:
                            # buffers stop noisy projections from ping-ponging stock:
                            # receiver must be short even at 80% of forecast, donor long even at 125%
                            for rx in xs:
                                if t - last_sent.get((k, rx), -999) < 28:
                                    continue
                                deficit = np.maximum(0, np.floor(0.8 * dem_rem[rx] - on_hand[ss_sids[rx]] - in_transit[ss_sids[rx]]))
                                if deficit.sum() < 4:
                                    continue
                                for dx in sorted(xs, key=lambda x: -proj[x].sum()):
                                    if dx == rx or t - last_recv.get((k, dx), -999) < 28:
                                        continue
                                    surplus = np.maximum(0, np.floor(on_hand[ss_sids[dx]] - 1.25 * dem_rem[dx]))
                                    move = np.minimum(deficit, surplus)
                                    if move.sum() < 4:
                                        continue
                                    unit_gain = price[ss_sids[rx][0]] * (1 - md[k]) - cost[ss_sids[rx][0]] * costs["salvage_rate_of_cost"]
                                    ok, ben, cst = transfer_is_worth_it(move.sum(), unit_gain, 0.7,
                                                                        costs["transfer_fixed_aed"],
                                                                        costs["transfer_per_unit_aed"])
                                    if ok:
                                        on_hand[ss_sids[dx]] -= move
                                        tout[ss_sids[dx]] += move
                                        arr = t + costs["transfer_lead_time_days"]
                                        tr_in[arr, ss_sids[rx]] += move
                                        in_transit[ss_sids[rx]] += move
                                        proj[dx] = proj[dx] - move
                                        proj[rx] = proj[rx] + move
                                        last_sent[(k, dx)] = t
                                        last_recv[(k, rx)] = t
                                        deficit = deficit - move
                                        res.transfers.append((t, k, ss_store[dx], ss_store[rx], int(move.sum()),
                                                              float(cst), float(ben), "seasonal"))
                                    if deficit.sum() <= 0:
                                        break
                        # stepped markdown if the chain is heading for too much leftover
                        oh_total = sum(on_hand[ss_sids[x]].sum() + in_transit[ss_sids[x]].sum() for x in xs)
                        leftover = sum(np.maximum(0, proj[x]).sum() for x in xs)
                        if (oh_total > 0 and t - i0 >= pp["seasonal_min_days_before_markdown"]
                                and leftover / oh_total > pp["seasonal_leftover_trigger"]
                                and md_step[k] < len(pp["markdown_steps"])
                                and t - md_last_change[k] >= pp["markdown_step_gap_days"]):
                            md[k] = pp["markdown_steps"][md_step[k]]
                            md_step[k] += 1
                            md_last_change[k] = t
                            res.markdowns.append((t, k, md[k]))
            # final clearance
            for code, (i0, i1) in season_days.items():
                if i0 <= t <= i1 and (i1 - t) < pp["final_clearance_days"]:
                    for k in np.unique(sku_idx[season == code]):
                        if md[k] < pp["final_clearance"]:
                            md[k] = pp["final_clearance"]
                            res.markdowns.append((t, k, md[k]))

        A["transfer_out"][t] = tout
        A["close"][t] = on_hand.copy()

    return res
