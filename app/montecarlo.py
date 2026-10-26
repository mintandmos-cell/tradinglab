"""Monte Carlo on R-multiples: bootstrap (with replacement) or shuffle (sequence risk only).

Note: Monte Carlo measures how bad the *same* edge could look by luck of ordering/sampling.
It cannot detect over-fitting — feed it out-of-sample trades only.
"""
from __future__ import annotations

import numpy as np


def run_mc(r, risk_pct: float = 1.0, n_trades: int | None = None, sims: int = 5000, method: str = "bootstrap",
           dd_limit_pct: float = 10.0, ruin_pct: float = 30.0, seed: int = 11) -> dict:
    r = np.asarray(r, float)
    if len(r) < 5:
        return {"error": "ต้องมีอย่างน้อย 5 เทรด"}
    rng = np.random.default_rng(seed)
    n = int(n_trades or len(r))
    if method == "shuffle":
        n = len(r)
        paths = np.array([rng.permutation(r) for _ in range(sims)])
    else:
        paths = r[rng.integers(0, len(r), size=(sims, n))]
    f = risk_pct / 100.0
    eq = np.cumprod(1 + f * paths, axis=1)
    peak = np.maximum.accumulate(np.concatenate([np.ones((sims, 1)), eq], axis=1), axis=1)[:, 1:]
    dd = ((peak - eq) / peak).max(axis=1) * 100
    final = (eq[:, -1] - 1) * 100
    wins = np.where(paths > 0, paths, 0).sum(1)
    losses = -np.where(paths <= 0, paths, 0).sum(1)
    pf = np.where(losses > 0, wins / np.maximum(losses, 1e-12), 99.0)
    exp = paths.mean(1)
    pct = [5, 25, 50, 75, 95]
    step = max(1, n // 120)
    fan = {str(p): [round(float(v), 2) for v in (np.percentile(eq[:, ::step], p, axis=0) - 1) * 100] for p in pct}
    conf = []
    for q in [50, 60, 70, 80, 90, 95, 97, 99]:
        lo = 100 - q
        conf.append({"confidence": q, "return_pct": round(float(np.percentile(final, lo)), 2),
                     "max_dd_pct": round(float(np.percentile(dd, q)), 2), "pf": round(float(np.percentile(pf, lo)), 3),
                     "expectancy": round(float(np.percentile(exp, lo)), 4)})
    dd1 = dd / risk_pct if risk_pct > 0 else dd  # drawdown scales ~linearly with risk for small risk
    dd95_per_1pct = float(np.percentile(dd1, 95))
    return {
        "method": method, "sims": sims, "n_trades": n, "risk_pct": risk_pct,
        "return_pct": {str(p): round(float(np.percentile(final, p)), 2) for p in pct},
        "max_dd_pct": {str(p): round(float(np.percentile(dd, p)), 2) for p in pct},
        "pf_p5": round(float(np.percentile(pf, 5)), 3),
        "expectancy_p5": round(float(np.percentile(exp, 5)), 4),
        "prob_profit": round(float((final > 0).mean() * 100), 1),
        "prob_dd_over_limit": round(float((dd > dd_limit_pct).mean() * 100), 2),
        "risk_of_ruin": round(float((dd >= ruin_pct).mean() * 100), 2),
        "dd_limit_pct": dd_limit_pct, "ruin_pct": ruin_pct,
        "confidence": conf,
        "fan_step": step, "fan": fan,
        "dd_hist": np.histogram(dd, bins=30)[0].tolist(), "dd_edges": [round(float(x), 2) for x in np.histogram(dd, bins=30)[1]],
        "suggested_risk_pct": round(float(dd_limit_pct / dd95_per_1pct), 2) if dd95_per_1pct > 0 else None,
    }
