"""Rolling walk-forward optimisation and the random-entry benchmark.

Every grid combination is backtested once over the full history (indicators are causal, so a
trade's outcome does not depend on where a window starts). Each fold then picks the combination
with the best in-sample t-statistic and trades it — untouched — in the following out-of-sample
window. If no combination is positive in-sample, the fold stays flat. The stitched OOS trades are
the only numbers used to judge a system.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ta
from .data import add_months, period_start
from .engine import cost_price, exit_rules, hold_bars, run, simulate, week_guard
from .strategies import signals as strat_signals
from .metrics import summarize
from .strategies import grid


def _tstat(r: np.ndarray) -> float:
    if len(r) < 3:
        return -np.inf
    sd = r.std(ddof=1)
    return float(r.mean() / sd * np.sqrt(len(r))) if sd > 0 else -np.inf


def backtest_combos(df: pd.DataFrame, strategy_id: str, spec: dict, tf: str, combos: list[dict]) -> list[dict]:
    runs = []
    for p in combos:
        tr = run(df, strategy_id, p, spec, tf)["trades"]
        runs.append({
            "params": p, "trades": tr,
            "t": pd.DatetimeIndex([x["entry_time"] for x in tr]) if tr else pd.DatetimeIndex([], tz="UTC"),
            "r": np.array([x["r"] for x in tr], float),
        })
    return runs


def walk_forward(df: pd.DataFrame, strategy_id: str, spec: dict, tf: str, cfg: dict, combos: list[dict] | None = None,
                 runs: list[dict] | None = None) -> dict:
    """runs = the full-history backtest of every combo, reusable across window lengths (robustness test)."""
    combos = combos or grid(strategy_id, cfg.get("max_hold_hours", 120))
    is_m, oos_m, min_tr = cfg.get("is_months", 12), cfg.get("oos_months", 3), cfg.get("min_is_trades", 20)
    runs = runs if runs is not None else backtest_combos(df, strategy_id, spec, tf, combos)
    # Calendar-aligned folds (user's rule, 27 Oct 2026): OOS windows are calendar periods (quarters for 3 months) on the
    # broker clock and every candidate is cut at the SAME research cut-off (start of the current quarter), so all systems
    # sit the same exam; the live parameters are what the in-sample window just before the cut-off picks, traded unchanged
    # for the following period (= the next out-of-sample fold, now happening live).
    first = df.index[0] + pd.Timedelta(days=30)
    end = pd.Timestamp(cfg["cutoff"]) if cfg.get("cutoff") else period_start(df.index[-1], oos_m)
    end = period_start(end, oos_m) if end != period_start(end, oos_m) else end
    q = period_start(first, oos_m)
    while add_months(q, -is_m) < first:
        q = add_months(q, oos_m)
    folds, oos_trades, is_rates = [], [], []

    def pick(a, b):
        best, best_t = None, -np.inf
        for j, rr in enumerate(runs):
            x = rr["r"][(rr["t"] >= a) & (rr["t"] < b)]
            t = _tstat(x) if len(x) >= min_tr and x.mean() > 0 else -np.inf
            if t > best_t:
                best, best_t = j, t
        return best, best_t

    while q < end:
        is_start, is_end, oos_end = add_months(q, -is_m), q, min(add_months(q, oos_m), end)
        best, best_t = pick(is_start, is_end)
        fold = {"is_start": is_start.isoformat(), "is_end": is_end.isoformat(), "oos_start": is_end.isoformat(),
                "oos_end": oos_end.isoformat(), "partial": False}
        if best is None:
            fold.update(params=None, is_trades=0, is_exp=None, oos_trades=0, oos_r=0.0, oos_exp=None)
        else:
            rr = runs[best]
            mi = (rr["t"] >= is_start) & (rr["t"] < is_end)
            mo = (rr["t"] >= is_end) & (rr["t"] < oos_end)
            x_is, x_oos = rr["r"][mi], rr["r"][mo]
            is_rates.append(x_is.sum() / is_m)
            for t_idx in np.flatnonzero(mo):
                oos_trades.append({**rr["trades"][t_idx], "combo": best, "fold": len(folds)})
            fold.update(params=rr["params"], combo=best, is_trades=int(len(x_is)), is_exp=round(float(x_is.mean()), 4),
                        is_t=round(best_t, 2), oos_trades=int(len(x_oos)), oos_r=round(float(x_oos.sum()), 2),
                        oos_exp=round(float(x_oos.mean()), 4) if len(x_oos) else None)
        folds.append(fold)
        q = add_months(q, oos_m)

    # live parameters: in-sample = the is_m months before the cut-off, traded from the cut-off for one period
    cur, cur_t = pick(add_months(end, -is_m), end)
    lock = {"start": end.isoformat(), "end": add_months(end, oos_m).isoformat()}
    end_eff = end
    oos_trades.sort(key=lambda x: x["entry_time"])
    oos_r = np.array([x["r"] for x in oos_trades], float)
    oos_years = sum((pd.Timestamp(f["oos_end"]) - pd.Timestamp(f["oos_start"])).days for f in folds) / 365.25
    full_exp = [rr["r"].mean() if len(rr["r"]) >= min_tr else -1 for rr in runs]
    chosen = [f.get("combo") for f in folds if f.get("combo") is not None]
    oos_rate = oos_r.sum() / (oos_years * 12) if oos_years > 0 else 0.0
    is_rate = float(np.mean(is_rates)) if is_rates else 0.0
    return {
        "strategy": strategy_id, "tf": tf, "n_combos": len(combos), "folds": folds,
        "oos_trades": oos_trades,
        "oos": summarize(oos_r, oos_years, 1.0, bars=[x["bars"] for x in oos_trades], cost_r=[x["cost_r"] for x in oos_trades]),
        "oos_years": round(oos_years, 2),
        "wfe": round(oos_rate / is_rate, 2) if is_rate > 0 else None,
        "plateau": round(float(np.mean(np.array(full_exp) > 0)), 2),
        "param_switches": int(sum(1 for a, b in zip(chosen, chosen[1:]) if a != b)),
        "distinct_params": len(set(chosen)),
        "current_params": runs[cur]["params"] if cur is not None else None,
        "current_is_t": round(cur_t, 2) if cur is not None else None, "cutoff": end_eff.isoformat(),
        "current_lock": lock,
        "_runs": runs,
        "combos_full": [{"params": rr["params"], "trades": int(len(rr["r"])),
                         "expectancy": round(float(rr["r"].mean()), 4) if len(rr["r"]) else None} for rr in runs],
    }


def random_benchmark(df: pd.DataFrame, wf: dict, spec: dict, tf: str, sims: int = 300, seed: int = 7) -> dict | None:
    """Same exits, same number of trades per fold, same long/short mix — random entry bars.
    Percentile = share of random runs whose mean R is below the system's OOS mean R."""
    trades = wf["oos_trades"]
    if len(trades) < 5:
        return None
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = ta.atr(h, l, c, 14)
    idx = df.index
    cost = cost_price(spec)
    rules = exit_rules()
    if rules["friday"]:
        wlast, wblock, _ = week_guard(idx, tf)
    else:
        wlast, wblock = None, np.zeros(len(idx), bool)
    sig_cache: dict = {}   # random entries leave on the same exits as the system, incl. its opposite signals
    rng = np.random.default_rng(seed)
    p_long = np.mean([t["dir"] > 0 for t in trades])
    per_fold = {}
    for t in trades:
        f = wf["folds"][t["fold"]]
        lo = idx.searchsorted(pd.Timestamp(f["oos_start"]))
        hi = idx.searchsorted(pd.Timestamp(f["oos_end"])) - 2
        p = f["params"]
        per_fold.setdefault(t["fold"], {"lo": lo, "hi": hi, "n": 0, "p": p})["n"] += 1
    means = np.empty(sims)
    for s in range(sims):
        rs = []
        for f in per_fold.values():
            if f["hi"] <= f["lo"]:
                continue
            H = hold_bars(f["p"], tf, rules)
            key = tuple(sorted(f["p"].items()))
            if rules["opposite"] and key not in sig_cache:
                sig_cache[key] = strat_signals(wf["strategy"], df, f["p"])[0]
            opp = sig_cache.get(key)
            for i in rng.integers(f["lo"], f["hi"], size=f["n"]):
                if not np.isfinite(a[i]) or a[i] <= 0 or wblock[i + 1]:
                    continue
                d = 1 if rng.random() < p_long else -1
                sl_dist = f["p"]["sl_atr"] * a[i]
                res = simulate(o, h, l, c, i + 1, d, sl_dist, f["p"]["rr"], H, cost,
                               int(wlast[i + 1]) if wlast is not None else None, opp)
                if res:
                    rs.append(d * (res[1] - res[3]) / sl_dist)
        means[s] = np.mean(rs) if rs else 0.0
    actual = float(np.mean([t["r"] for t in trades]))
    return {"sims": sims, "actual_exp": round(actual, 4), "random_mean": round(float(means.mean()), 4),
            "random_p95": round(float(np.percentile(means, 95)), 4),
            "percentile": round(float((means < actual).mean() * 100), 1)}
