"""Performance statistics on an R-multiple series."""
from __future__ import annotations

import math

import numpy as np


def summarize(r, years: float, risk_pct: float = 1.0, bars=None, cost_r=None) -> dict:
    r = np.asarray(r, dtype=float)
    n = len(r)
    if n == 0:
        return {"trades": 0, "net_r": 0.0, "expectancy": 0.0, "win_rate": 0.0, "pf": 0.0, "max_dd_r": 0.0,
                "t_stat": 0.0, "trades_per_year": 0.0, "cagr_pct": 0.0, "max_dd_pct": 0.0, "mar": 0.0,
                "avg_win": 0.0, "avg_loss": 0.0, "max_consec_loss": 0, "gross_expectancy": 0.0, "avg_bars": 0.0}
    eq = np.cumsum(r)
    dd_r = float((np.maximum.accumulate(np.r_[0.0, eq])[1:] - eq).max())
    wins, losses = r[r > 0], r[r <= 0]
    gl = -losses.sum()
    sd = r.std(ddof=1) if n > 1 else 0.0
    f = risk_pct / 100.0
    curve = np.cumprod(1 + f * r)
    peak = np.maximum.accumulate(np.r_[1.0, curve])[1:]
    dd_pct = float(((peak - curve) / peak).max() * 100)
    cagr = (curve[-1] ** (1 / years) - 1) * 100 if years > 0 and curve[-1] > 0 else -100.0
    streak = best = 0
    for x in r:
        streak = streak + 1 if x <= 0 else 0
        best = max(best, streak)
    return {
        "trades": n,
        "net_r": round(float(r.sum()), 2),
        "expectancy": round(float(r.mean()), 4),
        "gross_expectancy": round(float(r.mean() + np.mean(cost_r)), 4) if cost_r is not None and len(cost_r) else None,
        "win_rate": round(float((r > 0).mean() * 100), 1),
        "pf": round(float(wins.sum() / gl), 3) if gl > 0 else (99.0 if wins.sum() > 0 else 0.0),
        "avg_win": round(float(wins.mean()), 3) if len(wins) else 0.0,
        "avg_loss": round(float(losses.mean()), 3) if len(losses) else 0.0,
        "max_dd_r": round(dd_r, 2),
        "t_stat": round(float(r.mean() / (sd / math.sqrt(n))), 2) if sd > 0 else 0.0,
        "trades_per_year": round(n / years, 1) if years > 0 else 0.0,
        "risk_pct": risk_pct,
        "cagr_pct": round(float(cagr), 2),
        "max_dd_pct": round(dd_pct, 2),
        "mar": round(float(cagr / dd_pct), 2) if dd_pct > 0 else 0.0,
        "max_consec_loss": int(best),
        "avg_bars": round(float(np.mean(bars)), 1) if bars is not None and len(bars) else None,
    }


def equity_points(times, r, risk_pct: float, start: float = 10000.0):
    """Compounded equity curve for charts: list of {time, value}."""
    f = risk_pct / 100.0
    eq = start * np.cumprod(1 + f * np.asarray(r, float))
    return [{"time": int(t), "value": round(float(v), 2)} for t, v in zip(times, eq)]
