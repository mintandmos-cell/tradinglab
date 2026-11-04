"""Position-sizing comparison on the research's own walk-forward OOS trades (user, 4 Nov 2026: "which way of sizing lots
suits trend following best, as research / funds do — compare them").

  python -m tools.sizing_compare [strategy:TF ...]      -> data/research/sizing_compare.json + table

The trades are the OOS trades of each strategy on all 20 symbols (same params per fold as the research, same costs).
Every method trades exactly the same trades; only the risk per trade changes. Equity compounds; P&L is marked to the
close of every day, so open trades count in the drawdown. Caps as the live system: 1 % per symbol, cluster caps.

Methods (risk = % of equity lost if the stop is hit):
  fixed_0.25 / fixed_0.5 / fixed_1.0   fixed fractional (Turtle / Clenow style ATR sizing) — 0.5 % is the live setting
  vol_target   0.5 % x (target vol / portfolio vol of the last 60 days), x0.5 .. x2  (Harvey et al. 2018; AQR / MOP 2012)
  dd_scale     0.5 % x (1 - DD / 25 %), at least 0.1 %  (cut risk while in drawdown; Grossman & Zhou 1993 style)
  pyramid      0.5 % + one more 0.5 % unit each time the open trade gains another 2 R (max 2 add-ons; Concretum 2025)
"""
from __future__ import annotations

import json
import math
import sys

import numpy as np
import pandas as pd

from app import data, research
from app.config import RESEARCH_DIR, SYMBOLS, load_settings, symbol_spec, write_json
from app.scanner import cluster_of
from app.walkforward import walk_forward

OUT = RESEARCH_DIR / "sizing_compare.json"
DEFAULT = ["ichimoku_tk:H1", "range_expansion:H1", "chop_break:H1"]


def oos_trades(sid: str, tf: str, settings: dict) -> list[dict]:
    cfg = settings["research"]
    cut = research.research_cutoff(settings)
    out = []
    for sym in SYMBOLS:
        spec = symbol_spec(sym, settings)
        df = data.get_bars(sym, tf)
        df = df[df.index < cut]
        if len(df) < 500:
            continue
        wf = walk_forward(df, sid, spec, tf, {**cfg, "cutoff": cut.isoformat()})
        c = df["close"].to_numpy(float)
        days = df.index.normalize()
        for t in wf["oos_trades"]:
            e, x = int(t["entry_i"]), int(t["exit_i"])
            d = float(t["dir"])
            dist = abs(float(t["entry"]) - float(t["sl"])) or 1e-12
            path = d * (c[e:x + 1] - float(t["entry"])) / dist          # open R at each close (gross)
            path[-1] = float(t["r"])                                       # the last point = the trade's real net R
            # daily marks: last open R of each day
            dd = pd.Series(path, index=days[e:x + 1]).groupby(level=0).last()
            out.append({"symbol": sym, "entry_day": days[e], "exit_day": days[x], "dir": d, "r": float(t["r"]),
                        "marks": dd, "dist": dist, "entry": float(t["entry"]), "exit": float(t["exit"]),
                        "cost_r": float(t.get("cost_r") or 0), "path": path})
    return out


def simulate(trades: list[dict], method: str, settings: dict) -> dict:
    caps = settings.get("cluster_risk_pct") or {"default": 1.5}
    sym_cap = float(settings.get("max_symbol_risk_pct") or 1.0)
    days = sorted({d for t in trades for d in t["marks"].index})
    starts: dict = {}
    for i, t in enumerate(trades):
        starts.setdefault(t["entry_day"], []).append(i)
    eq, peak = 1.0, 1.0
    open_: dict[int, dict] = {}
    curve, unit_ret = [], []          # unit_ret = the day's return per 1 % risk on every open trade (for vol targeting)
    skipped = 0
    for day in days:
        # 1. P&L of open trades to today's close
        pnl, unit = 0.0, 0.0
        for i, o in list(open_.items()):
            t = trades[i]
            m = t["marks"]
            r_now = float(m.loc[day]) if day in m.index else (t["r"] if day >= t["exit_day"] else None)
            if day >= t["exit_day"]:
                r_now = t["r"]                      # settle at the trade's real net R (also same-day trades)
            if r_now is not None:
                for u in o["units"]:
                    if u["from"] <= day:
                        cur = r_now - u["r0"] if u["addon"] else r_now
                        pnl += u["risk"] * u["eq"] * (cur - u["last"])
                        unit += 0.01 * (cur - u["last"])
                        u["last"] = cur
                # pyramiding: new units once the open profit reaches the next step
                if method == "pyramid":
                    k = len(o["units"])
                    if k <= 2 and r_now >= 2.0 * k and day != t["exit_day"]:
                        o["units"].append({"risk": 0.005, "eq": eq + pnl, "r0": r_now, "last": 0.0, "addon": True, "from": day})
            if day >= t["exit_day"]:
                del open_[i]
        eq += pnl
        peak = max(peak, eq)
        unit_ret.append(unit)
        # 2. new trades at today's entries
        for i in starts.get(day, []):
            t = trades[i]
            ddp = 1 - eq / peak
            if method.startswith("fixed_"):
                risk = float(method.split("_")[1]) / 100
            elif method == "vol_target":
                hist = np.array(unit_ret[-60:])
                vol = hist.std() * math.sqrt(252) if len(hist) >= 20 else None
                target = TARGET.get("v")
                risk = 0.005 if not vol or not target else 0.005 * min(2.0, max(0.5, target / vol))
            elif method == "dd_scale":
                risk = max(0.001, 0.005 * (1 - ddp / 0.25))
            else:
                risk = 0.005
            sym_open = sum(u["risk"] for o in open_.values() if trades[o["i"]]["symbol"] == t["symbol"] for u in o["units"])
            cl = cluster_of(t["symbol"])
            cl_open = sum(u["risk"] for o in open_.values() if cluster_of(trades[o["i"]]["symbol"]) == cl for u in o["units"])
            cap = float(caps.get(cl, caps.get("default", 1.5))) / 100
            if sym_open + risk > sym_cap / 100 + 1e-9 or cl_open + risk > cap + 1e-9:
                skipped += 1
                continue
            # booked from 0 R at the next close (the entry day's move is part of the next mark)
            open_[i] = {"i": i, "units": [{"risk": risk, "eq": eq, "r0": 0.0, "last": 0.0, "addon": False, "from": day}]}
        curve.append((day, eq))
    s = pd.Series([v for _, v in curve], index=pd.DatetimeIndex([d for d, _ in curve]))
    return {"curve": s, "skipped": skipped, "unit": pd.Series(unit_ret, index=s.index)}


TARGET: dict = {}


def stats(s: pd.Series) -> dict:
    s = s[~s.index.duplicated()]
    yrs = max((s.index[-1] - s.index[0]).days / 365.25, 1e-9)
    ret = s.pct_change().dropna()
    dd = 1 - s / s.cummax()
    yearly = s.resample("YE").last().pct_change().dropna()
    # share of 12-month windows that touched a 25 % drawdown from the window's own start / peak
    win, hit = 0, 0
    for a in pd.date_range(s.index[0], s.index[-1] - pd.Timedelta(days=365), freq="MS"):
        w = s[(s.index >= a) & (s.index < a + pd.Timedelta(days=365))]
        if len(w) > 20:
            win += 1
            hit += bool((1 - w / w.cummax()).max() >= 0.25)
    cagr = s.iloc[-1] ** (1 / yrs) - 1
    vol = ret.std() * math.sqrt(252)
    return {"cagr": round(100 * cagr, 1), "vol": round(100 * vol, 1), "sharpe": round(cagr / vol, 2) if vol else None,
            "max_dd": round(100 * dd.max(), 1), "mar": round(cagr / dd.max(), 2) if dd.max() > 0 else None,
            "worst_year": round(100 * yearly.min(), 1) if len(yearly) else None,
            "best_year": round(100 * yearly.max(), 1) if len(yearly) else None,
            "p_dd25_1y": round(100 * hit / win, 0) if win else None, "years": round(yrs, 1)}


METHODS = ["fixed_0.25", "fixed_0.5", "fixed_1.0", "vol_target", "dd_scale", "pyramid"]


def main(argv: list[str]) -> int:
    settings = load_settings()
    out = {"generated": pd.Timestamp.now(tz="UTC").isoformat(), "methods": METHODS, "systems": {}}
    for key in argv or DEFAULT:
        sid, tf = key.split(":")
        trades = oos_trades(sid, tf, settings)
        trades.sort(key=lambda t: t["entry_day"])
        # vol target = the median 60-day vol of the fixed 0.5 % run (so the AVERAGE risk stays ~0.5 %)
        TARGET.clear()
        base = simulate(trades, "fixed_0.5", settings)
        u = base["unit"]
        TARGET["v"] = float((u.rolling(60).std() * math.sqrt(252)).median())
        res = {}
        for m in METHODS:
            r = base if m == "fixed_0.5" else simulate(trades, m, settings)
            res[m] = {**stats(r["curve"]), "skipped": r["skipped"]}
        out["systems"][key] = {"trades": len(trades), "symbols": len({t["symbol"] for t in trades}), "results": res}
        print(f"\n{key}: {len(trades)} trades")
        print(f"{'method':12} {'CAGR%':>7} {'vol%':>6} {'Sharpe':>7} {'MaxDD%':>7} {'MAR':>5} {'worst y':>8} {'P(DD25,1y)':>10}")
        for m, x in res.items():
            print(f"{m:12} {x['cagr']:7} {x['vol']:6} {x['sharpe']:7} {x['max_dd']:7} {x['mar']:5} {x['worst_year']:8} {x['p_dd25_1y']:10}")
    write_json(OUT, out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
