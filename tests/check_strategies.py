"""Sanity checks for every strategy in the library (run: python -m tests.check_strategies).

1. causality   — changing prices AFTER bar k never changes signals up to bar k (no look-ahead)
2. random walk — on driftless random prices, with zero costs, average R stays near 0 (no hidden edge from the code)
3. output      — signals are only -1 / 0 / +1, every grid combo runs, H1 and H4 produce trades
"""
from __future__ import annotations

import sys
import time

import numpy as np
import pandas as pd

from app.data import resample
from app.engine import run
from app.strategies import STRATEGIES, defaults, grid, signals


def walk(seed: int, n: int = 30000, start="2016-01-04") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="h", tz="UTC")
    idx = idx[idx.dayofweek < 5]
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, len(idx))))
    o = np.r_[100, c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, .0008, len(idx))))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, .0008, len(idx))))
    return pd.DataFrame(dict(open=o, high=h, low=l, close=c, volume=1.0), index=idx)


def main() -> int:
    bad = []
    base = walk(1, 12000)
    rng = np.random.default_rng(99)
    rows = []
    for sid in STRATEGIES:
        t0 = time.time()
        p = defaults(sid)
        problems = []
        # 1. causality on H1 and H4
        for tf in ("H1", "H4"):
            df = resample(base, tf)
            sig = signals(sid, df, p)[0]
            for k in (len(df) // 3, len(df) // 2, len(df) - 40):
                mod = df.copy()
                noise = np.exp(np.cumsum(rng.normal(0, 0.01, len(df) - k - 1)))
                for col in ("open", "high", "low", "close"):
                    mod.iloc[k + 1:, mod.columns.get_loc(col)] = df[col].iloc[k + 1:].to_numpy() * noise
                mod["high"] = mod[["open", "high", "low", "close"]].max(axis=1)
                mod["low"] = mod[["open", "high", "low", "close"]].min(axis=1)
                sig2 = signals(sid, mod, p)[0]
                if not np.array_equal(sig[: k + 1], sig2[: k + 1]):
                    problems.append(f"look-ahead {tf} k={k}: {int((sig[:k+1] != sig2[:k+1]).sum())} signals changed")
            if not set(np.unique(sig)) <= {-1, 0, 1}:
                problems.append(f"bad values {np.unique(sig)}")
        # 3. every grid combo runs
        for combo in grid(sid, 120):
            signals(sid, resample(base, "H4"), combo)
        # 2. random walk, zero costs, 4 paths, H1 + H4
        exps, ntr = [], {}
        for seed in range(4):
            df = walk(100 + seed)
            for tf in ("H1", "H4"):
                tr = run(resample(df, tf), sid, p, {"spread": 0}, tf)["trades"]
                ntr[tf] = ntr.get(tf, 0) + len(tr)
                if tr:
                    exps.append(np.mean([t["r"] for t in tr]))
        m = float(np.mean(exps)) if exps else float("nan")
        se = float(np.std(exps) / np.sqrt(len(exps))) if len(exps) > 1 else float("nan")
        if not exps or abs(m) > max(0.08, 3 * se):
            problems.append(f"random-walk mean {m:+.3f}R (se {se:.3f})")
        if ntr.get("H1", 0) < 20 and sid not in ("turn_of_month",):
            problems.append(f"few H1 trades {ntr.get('H1')}")
        rows.append((sid, ntr.get("H1", 0), ntr.get("H4", 0), m, time.time() - t0, problems))
        if problems:
            bad.append(sid)
    print(f"{'strategy':22s} {'H1 tr':>6s} {'H4 tr':>6s} {'RW mean R':>10s} {'sec':>5s}  result")
    for sid, a, b, m, sec, pr in rows:
        print(f"{sid:22s} {a:6d} {b:6d} {m:+10.3f} {sec:5.1f}  {'OK' if not pr else '; '.join(pr)}")
    print(f"\n{len(STRATEGIES) - len(bad)}/{len(STRATEGIES)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
