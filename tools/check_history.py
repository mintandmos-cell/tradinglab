"""Data-quality check of the long H1 histories the research runs on (user, 3 Nov 2026: old Exness FX data 2002-2010 may
have holes or bad prints that make results look better or worse than they are).

  python -m tools.check_history            -> data/research/data_quality.json + a table on stdout

Per symbol and per year:
  gaps     missing trading hours: holes longer than the market's normal pauses (weekend, daily break)
  spikes   bars whose range or close-to-close move is > 12 x the median range of the surrounding 200 bars
           (and which snap back: the next close returns at least half of the move = a bad print, not news)
  flat     bars with open = high = low = close (no ticks)
  bad      high < max(open, close), low > min(open, close), or a price <= 0
A year is marked "poor" when holes > 5 % of its hours or there are > 3 snap-back spikes; the research can start after the
last poor year (settings research.history_start per symbol) — that is a decision for the user, nothing is cut here.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from app import data
from app.config import RESEARCH_DIR, SYMBOLS, write_json

OUT = RESEARCH_DIR / "data_quality.json"


def _expected_gap(sym: str) -> pd.Timedelta:
    cls = SYMBOLS[sym].get("group", "")
    if cls == "Crypto":
        return pd.Timedelta(hours=1)
    if cls == "Stocks":
        return pd.Timedelta(hours=18)      # overnight closes
    if cls in ("Indices", "Metals", "Energy"):
        return pd.Timedelta(hours=2)       # daily maintenance break
    return pd.Timedelta(hours=1)


def check(sym: str) -> dict | None:
    df = data._seed(sym)
    if df is None or len(df) < 1000:
        return None
    df = df[~df.index.duplicated()].sort_index()
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    t = df.index
    rng = h - l
    med = pd.Series(rng).rolling(200, min_periods=50, center=True).median().bfill().ffill().to_numpy()
    med = np.where(med > 0, med, np.nanmedian(rng[rng > 0]) if (rng > 0).any() else 1.0)
    move = np.abs(np.diff(c, prepend=c[0]))
    nxt = np.append(c[1:] - c[:-1], 0.0)
    prv = np.diff(c, prepend=c[0])
    big = (np.maximum(rng, move) > 12 * med)
    snap = big & (np.sign(nxt) == -np.sign(prv)) & (np.abs(nxt) >= 0.5 * np.abs(prv))
    flat = (o == h) & (h == l) & (l == c)
    bad = (h < np.maximum(o, c) - 1e-12) | (l > np.minimum(o, c) + 1e-12) | (np.minimum.reduce([o, h, l, c]) <= 0)
    # holes: weekday gaps longer than normal (weekend = Fri 21 UTC .. Sun 21 UTC is skipped for non-crypto)
    dt = np.diff(t.asi8) / 3.6e12            # hours
    allow = _expected_gap(sym) / pd.Timedelta(hours=1)
    crypto = SYMBOLS[sym].get("group") == "Crypto"
    hole_h = np.zeros(len(t))
    for i in np.nonzero(dt > allow + 0.01)[0]:
        a, b = t[i], t[i + 1]
        if not crypto:
            # count only weekday hours inside the gap (the weekend is not a hole)
            hrs = pd.date_range(a + pd.Timedelta(hours=1), b - pd.Timedelta(hours=1), freq="h")
            hrs = hrs[(hrs.dayofweek < 5) & ~((hrs.dayofweek == 4) & (hrs.hour >= 21))]
            miss = max(0, len(hrs) - int(allow))
        else:
            miss = int(dt[i] - 1)
        hole_h[i] = miss
    years = {}
    for y in sorted(set(t.year)):
        m = t.year == y
        n = int(m.sum())
        holes = float(hole_h[m].sum())
        info = {"bars": n, "missing_hours": int(holes), "missing_pct": round(100 * holes / max(n + holes, 1), 1),
                "spikes": int(snap[m].sum()), "big_moves": int(big[m].sum()), "flat": int(flat[m].sum()), "bad": int(bad[m].sum())}
        longest = (dt[m[:-1]].max() if m[:-1].any() else 0)
        info["longest_gap_h"] = round(float(longest), 0)
        info["poor"] = info["missing_pct"] > 5 or info["spikes"] > 3 or info["bad"] > 0
        years[int(y)] = info
    examples = [{"time": t[i].isoformat(), "close_before": round(c[i - 1], 6) if i else None, "close": round(c[i], 6),
                 "next_close": round(c[i + 1], 6) if i + 1 < len(c) else None} for i in np.nonzero(snap)[0][:8]]
    poor = [y for y, v in years.items() if v["poor"]]
    # suggested start: after the last poor year, if the poor years sit at the START of the history (old data)
    first = min(years)
    head = 0
    for y in sorted(years):
        if years[y]["poor"]:
            head = y
        elif y - first > 2:
            break
    return {"symbol": sym, "from": t[0].isoformat(), "to": t[-1].isoformat(), "bars": int(len(t)),
            "source": data.history_source(sym), "years": years, "poor_years": poor, "spike_examples": examples,
            "suggest_start": f"{head + 1}-01-01" if head else None}


def main(argv: list[str]) -> int:
    syms = [s.upper() for s in argv] or list(SYMBOLS)
    out = {"generated": pd.Timestamp.now(tz="UTC").isoformat(), "symbols": {}}
    print(f"{'symbol':8} {'from':10} {'bars':>7}  poor years / suggested start")
    for s in syms:
        r = check(s)
        if not r:
            continue
        out["symbols"][s] = r
        print(f"{s:8} {r['from'][:10]} {r['bars']:7d}  {r['poor_years'] or '-'}  {r['suggest_start'] or ''}")
    write_json(OUT, out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
