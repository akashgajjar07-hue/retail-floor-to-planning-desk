"""
Deterministic planning formulas. No AI, no guessing: every replenishment
quantity in this project comes from these functions.
"""
from __future__ import annotations

import numpy as np
from scipy.stats import norm


def z_value(service_level: float) -> float:
    """Cycle service level -> z (e.g. 0.95 -> 1.645)."""
    return float(norm.ppf(service_level))


def safety_stock(z, d, sd_d, L, sd_L):
    """
    Safety stock with demand AND lead-time uncertainty:
        SS = z * sqrt(L * sd_d^2 + d^2 * sd_L^2)
    With sd_L = 0 this collapses to the textbook z * sd_d * sqrt(L).
    d, sd_d in units/day; L, sd_L in days.
    """
    return z * np.sqrt(L * sd_d ** 2 + (d ** 2) * (sd_L ** 2))


def reorder_point(d, L, ss):
    """ROP = expected demand over lead time + safety stock."""
    return d * L + ss


def order_up_to(rop, d, review_days):
    """Order-up-to level = ROP + one review cycle of demand."""
    return rop + d * review_days


def shrunk_mean(values, prior, k):
    """Mean of observed values pulled toward a prior worth k pseudo-observations.
    Keeps estimates sane when an item was out of stock for most of the window."""
    values = np.asarray(values, dtype=float)
    return (values.sum() + k * prior) / (len(values) + k)


def largest_remainder(total: int, weights):
    """Split an integer total in proportion to weights, preserving the total."""
    w = np.asarray(weights, dtype=float)
    total = int(round(total))
    if total <= 0:
        return np.zeros(len(w), dtype=int)
    if w.sum() <= 0:
        w = np.ones(len(w))
    raw = total * w / w.sum()
    out = np.floor(raw).astype(int)
    rem = total - out.sum()
    if rem > 0:
        order = np.argsort(-(raw - out), kind="stable")
        out[order[:rem]] += 1
    return out


def abc_class(values, cuts=(0.80, 0.95)):
    """Pareto ABC on a value (e.g. revenue): top 80% = A, next 15% = B, rest = C."""
    v = np.asarray(values, dtype=float)
    order = np.argsort(-v)
    share = np.cumsum(v[order]) / max(v.sum(), 1e-9)
    cls = np.empty(len(v), dtype=object)
    prev = np.concatenate([[0.0], share[:-1]])
    cls[order] = np.where(prev < cuts[0], "A", np.where(prev < cuts[1], "B", "C"))
    return cls


def xyz_class(cv, cuts=(0.5, 1.0)):
    """Demand variability: coefficient of variation of weekly demand."""
    cv = np.asarray(cv, dtype=float)
    return np.where(cv < cuts[0], "X", np.where(cv < cuts[1], "Y", "Z"))


def transfer_is_worth_it(units, unit_margin, sell_prob, fixed_cost, per_unit_cost):
    """Move stock only if the margin it is expected to save beats the cost of moving it."""
    benefit = units * unit_margin * sell_prob
    cost = fixed_cost + per_unit_cost * units
    return benefit > cost, benefit, cost
