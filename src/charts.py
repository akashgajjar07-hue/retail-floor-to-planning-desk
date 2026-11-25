"""Static charts for the README (PNG). The interactive versions live in docs/index.html."""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd              # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "charts"
BLUE, ORANGE, INK, INK2, GRID = "#257de0", "#f47230", "#191919", "#666666", "#e6e6e6"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": GRID,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                     "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
                     "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True})


def title(ax, t, sub):
    ax.set_title(t, loc="left", fontsize=12, color=INK, fontweight="bold", pad=22)
    ax.text(0, 1.03, sub, transform=ax.transAxes, fontsize=9, color=INK2)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    d = json.loads((ROOT / "outputs/dashboard_data.json").read_text())

    # 1. censoring bias by stockout band
    b = pd.DataFrame(d["censoring_bands"])
    fig, ax = plt.subplots(figsize=(7.5, 4))
    x = range(len(b))
    ax.bar([i - 0.2 for i in x], b["naive_bias"] * 100, 0.38, color=ORANGE, label="Forecast from all sales days")
    ax.bar([i + 0.2 for i in x], b["corrected_bias"] * 100, 0.38, color=BLUE, label="Forecast from in-stock days only")
    for i, r in b.iterrows():
        ax.text(i - 0.2, r.naive_bias * 100 - 1.2, f"{r.naive_bias:.0%}", ha="center", va="top", fontsize=9, color=INK)
        ax.text(i + 0.2, r.corrected_bias * 100 - 1.2, f"{r.corrected_bias:.0%}", ha="center", va="top", fontsize=9, color=INK)
    ax.axhline(0, color=INK2, lw=1)
    ax.set_xticks(list(x), [f"{s}\n({n} lines)" for s, n in zip(b.stockout_band, b.style_stores)])
    ax.set_xlabel("Share of size-days out of stock")
    ax.set_ylabel("Forecast error vs true demand (%)")
    ax.set_ylim(min(b.naive_bias.min() * 100 - 8, -10), 4)
    ax.legend(frameon=False, loc="lower left")
    title(ax, "Sales understate demand when items stock out",
          "Replenished items, Jan-Nov 2026. True demand is known only because the data is synthetic.")
    fig.tight_layout(); fig.savefig(OUT / "censored_demand_bias.png", dpi=160); plt.close(fig)

    # 2. example series
    ex = d["censoring_example"]
    w = pd.DataFrame(ex["weeks"])
    fig, ax = plt.subplots(figsize=(7.5, 3.8))
    ax.plot(pd.to_datetime(w.week), w.demand, color=INK2, lw=2, ls="--", label="True demand")
    ax.plot(pd.to_datetime(w.week), w.sales, color=BLUE, lw=2, label="Observed sales")
    ax.fill_between(pd.to_datetime(w.week), w.sales, w.demand, where=w.demand > w.sales, color=ORANGE, alpha=0.25,
                    label="Lost sales (invisible in real data)")
    ax.set_ylabel("Units per week")
    ax.legend(frameon=False, loc="upper left", ncol=3, fontsize=9)
    title(ax, f"{ex['name']} at {ex['store_id']}",
          f"Benchmark policy. Sizes were out of stock on {ex['size_stockout_rate']:.0%} of days.")
    fig.tight_layout(); fig.savefig(OUT / "censored_demand_example.png", dpi=160); plt.close(fig)

    # 3. service level trade-off
    g = pd.DataFrame(d["what_if"])
    base = g[(g.delay_days == 0) & (g.demand_shock == 1.0)]
    p = base[(base.policy == "proposed") & (base.service_level != "ABC (98/95/90)")].copy()
    p["sl"] = p.service_level.astype(float)
    p = p.sort_values("sl")
    abc = base[base.service_level == "ABC (98/95/90)"].iloc[0]
    bm = base[base.policy == "benchmark"].iloc[0]
    fig, ax = plt.subplots(figsize=(7.5, 4))
    ax.plot(p.avg_inventory_value_basics_aed / 1000, p.fill_rate_basics * 100, color=BLUE, lw=2, marker="o", ms=7,
            label="Proposed, single service level")
    for r in p.itertuples():
        ax.annotate(f"{r.sl:.1%}".replace(".0%", "%"), (r.avg_inventory_value_basics_aed / 1000, r.fill_rate_basics * 100),
                    textcoords="offset points", xytext=(6, -12), fontsize=8, color=INK2)
    ax.scatter([abc.avg_inventory_value_basics_aed / 1000], [abc.fill_rate_basics * 100], s=90, color=BLUE,
               edgecolor="white", lw=2, zorder=5, marker="D", label="Proposed, ABC service levels (98/95/90%)")
    ax.scatter([bm.avg_inventory_value_basics_aed / 1000], [bm.fill_rate_basics * 100], s=90, color=ORANGE,
               edgecolor="white", lw=2, zorder=5, label="Benchmark min/max rule")
    ax.set_xlabel("Average inventory of replenished items (AED '000 at cost)")
    ax.set_ylabel("Fill rate, replenished items (%)")
    ax.legend(frameon=False, loc="lower right", fontsize=9)
    title(ax, "Service level is a cost decision", "Each point is a full-year simulation on identical demand.")
    fig.tight_layout(); fig.savefig(OUT / "service_level_tradeoff.png", dpi=160); plt.close(fig)
    print("charts written to", OUT)


if __name__ == "__main__":
    main()
