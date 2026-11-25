"""
Synthetic Abu Dhabi-inspired fashion retail world.

Builds everything the simulator needs from config/scenario.json and
config/sku_master.csv:
  * calendar with day-of-week, store-month, category-season, event, promo
    and product-lifecycle indices (the "planner calendar", which is known)
  * random day-to-day demand noise (unknown to any policy)
  * supplier lead-time draws
  * opening stock and one-time seasonal buys, including the planted issues

TRUE demand is generated here. What a retailer would actually observe
(sales, stock, receipts) only appears once a policy is simulated, and sales
are capped by stock, so lost demand is never visible in the observed data.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SIZES = ["XS", "S", "M", "L", "XL", "XXL"]
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def load_config():
    cfg = json.loads((ROOT / "config" / "scenario.json").read_text())
    skus = pd.read_csv(ROOT / "config" / "sku_master.csv")
    return cfg, skus


@dataclass
class World:
    cfg: dict
    dates: pd.DatetimeIndex          # simulated days
    cal_dates: pd.DatetimeIndex      # simulated days + forward planning horizon
    stores: pd.DataFrame
    skus: pd.DataFrame
    suppliers: pd.DataFrame
    series: pd.DataFrame             # one row per store x sku x size
    style_store: pd.DataFrame        # one row per store x sku
    base_rate: np.ndarray            # [S] expected units/day at index 1.0
    planner_index: np.ndarray        # [Tcal, S] known calendar multiplier
    noise: np.ndarray                # [T, SS] unknown gamma multiplier per style-store-day
    uniforms: np.ndarray             # [T, S] common random numbers for Poisson draws
    promo: np.ndarray                # [Tcal, n_sku] bool
    lead_time: np.ndarray            # [n_supplier, Tcal] days, if ordered that day
    opening_stock: np.ndarray        # [S]
    seasonal_buy: np.ndarray         # [S] units delivered at season start
    season_start_idx: dict
    season_end_idx: dict
    dc_capacity: float               # units/day the DC can dispatch for replenishment

    @property
    def T(self):
        return len(self.dates)


def _month_curve(month_map, months):
    return np.array([month_map.get(m, 1.0) for m in months])


def build_world(cfg=None, skus=None, demand_mult: float = 1.0) -> World:
    if cfg is None or skus is None:
        cfg, skus = load_config()
    rng = np.random.default_rng(cfg["seed"])

    start, end = pd.Timestamp(cfg["start_date"]), pd.Timestamp(cfg["end_date"])
    season_ends = [pd.Timestamp(s["end"]) for s in cfg["seasons"].values()]
    cal_end = max(max(season_ends), end + pd.Timedelta(days=120))
    dates = pd.date_range(start, end, freq="D")
    cal = pd.date_range(start, cal_end, freq="D")
    Tc, T = len(cal), len(dates)

    stores = pd.DataFrame(cfg["stores"])
    suppliers = pd.DataFrame(cfg["suppliers"])
    skus = skus.copy().reset_index(drop=True)
    skus["sku_idx"] = np.arange(len(skus))
    sup_idx = {s: i for i, s in enumerate(suppliers["supplier_id"])}

    # ---------- series (store x sku x size) ----------
    base_curve = np.array([cfg["base_size_curve"][s] for s in SIZES])
    rows = []
    for si, st in stores.iterrows():
        shift = np.array(st["size_shift"])
        curve = base_curve + shift
        curve = curve / curve.sum()
        for _, k in skus.iterrows():
            if k["sized"] == "Y":
                labels = cfg["kids_size_labels"] if k["kids"] == "Y" else SIZES
                for r, (lab, share) in enumerate(zip(labels, curve)):
                    rows.append((st["store_id"], si, k["sku_id"], k["sku_idx"], lab, r, share))
            else:
                rows.append((st["store_id"], si, k["sku_id"], k["sku_idx"], "OS", 0, 1.0))
    series = pd.DataFrame(rows, columns=["store_id", "store_idx", "sku_id", "sku_idx",
                                         "size", "size_rank", "size_share"])
    series = series.merge(skus[["sku_id", "category", "season", "replenishment", "sized",
                                "supplier_id", "unit_cost", "price", "base_daily_demand",
                                "profile", "name", "subcategory"]], on="sku_id")
    series = series.sort_values(["store_idx", "sku_idx", "size_rank"]).reset_index(drop=True)
    series["sid"] = np.arange(len(series))
    series["supplier_idx"] = series["supplier_id"].map(sup_idx)

    ss = series.groupby(["store_idx", "sku_idx"], sort=True).agg(
        store_id=("store_id", "first"), sku_id=("sku_id", "first"),
        first_sid=("sid", "min"), last_sid=("sid", "max")).reset_index()
    ss["ssid"] = np.arange(len(ss))
    series = series.merge(ss[["store_idx", "sku_idx", "ssid"]], on=["store_idx", "sku_idx"])
    series = series.sort_values("sid").reset_index(drop=True)
    S, SS = len(series), len(ss)

    # ---------- base rate ----------
    st_base = stores["base"].to_numpy()[series["store_idx"]]
    cat_mult = np.array([stores.loc[i, "category_mult"][c]
                         for i, c in zip(series["store_idx"], series["category"])])
    base_rate = (series["base_daily_demand"].to_numpy() * st_base * cat_mult
                 * series["size_share"].to_numpy() * demand_mult)

    # ---------- planner calendar index [Tc, S] ----------
    months = cal.month.to_numpy()
    dow = cal.dayofweek.to_numpy()
    dow_idx = np.array([cfg["day_of_week_index"][d] for d in DOW])[dow]          # [Tc]
    store_month = np.stack([np.array(st["monthly_index"])[months - 1] for _, st in stores.iterrows()], 1)
    weekend = (dow >= 5)
    store_wk = np.stack([np.where(weekend, st["weekend_extra"], 1.0) for _, st in stores.iterrows()], 1)

    profiles = {
        "flat": {},
        "summer_up": {12: .92, 1: .92, 2: .92, 5: 1.07, 6: 1.15, 7: 1.15, 8: 1.15, 9: 1.07},
        "summer_down": {12: 1.08, 1: 1.08, 2: 1.08, 6: .88, 7: .88, 8: .88},
        "winter_up": {11: 1.4, 12: 1.4, 1: 1.4, 2: 1.4, 6: .6, 7: .6, 8: .6, 9: .6},
    }
    prof_curve = {p: _month_curve(m, months) for p, m in profiles.items()}

    # events
    ev_all = np.ones(Tc); ev_app = np.ones(Tc); ev_basics = np.ones(Tc)
    for e in cfg["events"]:
        m = (cal >= e["start"]) & (cal <= e["end"])
        target = {"all": ev_all, "apparel": ev_app, "basics": ev_basics}[e["applies_to"]]
        target[m] *= e["index"]

    # seasons & lifecycle
    season_start_idx, season_end_idx, life = {}, {}, {}
    month_overlay = {
        "SUMMER": {4: .8, 5: .95, 6: 1.1, 7: 1.1, 8: 1.0, 9: .8},
        "AW": {10: .6, 11: .85, 12: 1.1, 1: 1.0},
        "RAMADAN_EID": {},
    }
    for code, s in cfg["seasons"].items():
        s0, s1 = pd.Timestamp(s["start"]), pd.Timestamp(s["end"])
        i0, i1 = cal.get_loc(s0), cal.get_loc(s1)
        season_start_idx[code], season_end_idx[code] = i0, i1
        n = i1 - i0 + 1
        x = np.arange(n)
        lc = np.ones(n)
        ramp = min(14, n)
        lc[:ramp] = np.linspace(0.4, 1.0, ramp)
        tail0 = int(n * 0.7)
        lc[tail0:] = np.linspace(1.0, 0.45, n - tail0)
        curve = np.zeros(Tc)
        curve[i0:i1 + 1] = lc * _month_curve(month_overlay[code], months[i0:i1 + 1])
        if code == "RAMADAN_EID":
            pre = cal < pd.Timestamp("2026-02-18")
            post = cal > pd.Timestamp("2026-03-22")
            curve[pre] *= 0.6
            curve[post] *= 0.35
        life[code] = curve

    # promos (known to planners): random 7-day windows + White Friday week
    n_sku = len(skus)
    promo = np.zeros((Tc, n_sku), dtype=bool)
    wf = (cal >= "2026-11-23") & (cal <= "2026-11-29")
    for _, k in skus.iterrows():
        if k["replenishment"] != "continuous":
            continue
        promo[wf, k["sku_idx"]] = True
        for _ in range(rng.integers(2, 4)):
            d0 = rng.integers(20, T - 40)
            promo[d0:d0 + 7, k["sku_idx"]] = True
    promo_lift = cfg["costs"]["promo_lift"]

    PI = np.empty((Tc, S))
    cat = series["category"].to_numpy()
    store_idx = series["store_idx"].to_numpy()
    sku_idx = series["sku_idx"].to_numpy()
    season = series["season"].fillna("").to_numpy()
    profile = series["profile"].to_numpy()
    for sid in range(S):
        idx = dow_idx * store_month[:, store_idx[sid]] * store_wk[:, store_idx[sid]] * ev_all
        if cat[sid] in ("basics", "seasonal"):
            idx = idx * ev_app
        if cat[sid] == "basics":
            idx = idx * ev_basics
        if season[sid]:
            idx = idx * life[season[sid]]
        else:
            idx = idx * prof_curve[profile[sid]]
            idx = idx * np.where(promo[:, sku_idx[sid]], promo_lift, 1.0)
        PI[:, sid] = idx

    # ---------- unknown noise & CRN ----------
    k_shape = cfg["demand_noise_gamma_shape"]
    noise = rng.gamma(k_shape, 1.0 / k_shape, size=(T, SS))
    uniforms = rng.random((T, S))

    # lead times by supplier x order day (orders placed the same day share a shipment)
    lt = np.empty((len(suppliers), Tc))
    for i, sp in suppliers.iterrows():
        draw = rng.normal(sp["lead_time_mean_days"], sp["lead_time_sd_days"], Tc)
        lt[i] = np.clip(np.round(draw), 2, sp["lead_time_mean_days"] * 3)

    # ---------- opening stock (basics & continuous accessories) ----------
    opening = np.zeros(S)
    cont = series["replenishment"].to_numpy() == "continuous"
    style_plan = pd.Series(base_rate).groupby(series["ssid"]).sum().to_numpy()
    n_sizes = series.groupby("ssid")["sid"].count().to_numpy()
    planted_p1 = {"B01", "B02", "B04", "B05"}
    for sid in np.where(cont)[0]:
        s_ = series.iloc[sid]
        units = 30 * style_plan[s_["ssid"]] / n_sizes[s_["ssid"]]   # P5: flat size split
        if s_["store_id"] == "AUH-03" and s_["sku_id"] in planted_p1:
            units *= 4                                               # P1: overstock
        opening[sid] = np.round(units)

    # ---------- seasonal one-time buys ----------
    buy = np.zeros(S)
    buyer_curve = base_curve / base_curve.sum()
    for (store_i, sku_i), grp in series[~cont].groupby(["store_idx", "sku_idx"]):
        code = grp["season"].iloc[0]
        i0, i1 = season_start_idx[code], season_end_idx[code]
        exp_units = sum(base_rate[sid] / series.loc[sid, "size_share"] *
                        (PI[i0:i1 + 1, sid].sum()) for sid in grp["sid"][:1])
        err = float(np.exp(rng.normal(0, 0.2)))
        store_id = grp["store_id"].iloc[0]
        if code == "SUMMER" and store_id == "AUH-02":
            err *= 1.6                                               # P2 overbuy
        if code == "SUMMER" and store_id == "AUH-01":
            err *= 0.6                                               # P2 underbuy
        total = exp_units * err
        if len(grp) > 1:
            alloc = np.round(total * buyer_curve)
        else:
            alloc = np.array([np.round(total)])
        buy[grp["sid"].to_numpy()] = alloc

    # DC capacity for continuous replenishment
    mean_daily_cont = (base_rate[cont][None, :] * PI[:T][:, cont]).sum(1).mean()
    dc_capacity = cfg["dc_dispatch_capacity_multiplier"] * mean_daily_cont

    return World(cfg=cfg, dates=dates, cal_dates=cal, stores=stores, skus=skus,
                 suppliers=suppliers, series=series, style_store=ss, base_rate=base_rate,
                 planner_index=PI, noise=noise, uniforms=uniforms, promo=promo,
                 lead_time=lt, opening_stock=opening, seasonal_buy=buy,
                 season_start_idx=season_start_idx, season_end_idx=season_end_idx,
                 dc_capacity=dc_capacity)


def true_demand_rate(world: World) -> np.ndarray:
    """Expected demand per series-day including the unknown noise. [T, S]"""
    T = world.T
    ssid = world.series["ssid"].to_numpy()
    return world.base_rate[None, :] * world.planner_index[:T] * world.noise[:, ssid]


if __name__ == "__main__":
    w = build_world()
    print("series:", len(w.series), "style-stores:", len(w.style_store), "days:", w.T)
    print("DC capacity/day:", round(w.dc_capacity, 1))
    print("opening units:", int(w.opening_stock.sum()), "seasonal buy units:", int(w.seasonal_buy.sum()))
