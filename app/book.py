"""Whole-book simulation (user, 9 Nov 2026): every system that would trade, run TOGETHER on its walk-forward OOS trades,
with the live rules — risk per trade, 1 % open risk per symbol, the cluster caps, and the robot's anti-wipeout guard
(no new trades at -25 % from the start balance, close everything and stop at -30 %).

Two books are simulated after each research merge (data/research/book.json, พอร์ต MT5 page):
  live        the ACTIVE systems (single + portfolio legs + forward-promoted trials) at their own risk
  candidates  the live book + every TRIAL system as if it were promoted (0.5 % risk) — what the account would look like
              if the near-misses all made it
P&L is marked to the close of every day (open trades count in the drawdown); equity compounds.
"""
from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

from . import data, research
from .config import RESEARCH_DIR, load_settings, symbol_spec, write_json
from .scanner import cluster_of
from .walkforward import walk_forward

log = logging.getLogger("tradinglab.book")
OUT = RESEARCH_DIR / "book.json"


def system_trades(sym: str, sid: str, tf: str, settings: dict) -> list[dict]:
    cfg = settings["research"]
    cut = research.research_cutoff(settings)
    df = data.get_bars(sym, tf)
    df = df[df.index < cut]
    if len(df) < 500:
        return []
    wf = walk_forward(df, sid, symbol_spec(sym, settings), tf, {**cfg, "cutoff": cut.isoformat()})
    c = df["close"].to_numpy(float)
    days = df.index.normalize()
    out = []
    for t in wf["oos_trades"]:
        e, x = int(t["entry_i"]), int(t["exit_i"])
        dist = abs(float(t["entry"]) - float(t["sl"])) or 1e-12
        path = float(t["dir"]) * (c[e:x + 1] - float(t["entry"])) / dist
        path[-1] = float(t["r"])
        out.append({"symbol": sym, "key": f"{sym}:{sid}:{tf}", "entry_day": days[e], "exit_day": days[x], "r": float(t["r"]),
                    "marks": pd.Series(path, index=days[e:x + 1]).groupby(level=0).last()})
    return out


def simulate(trades: list[dict], settings: dict, guard: bool = True) -> dict:
    caps = settings.get("cluster_risk_pct") or {"default": 1.5}
    sym_cap = float(settings.get("max_symbol_risk_pct") or 1.0) / 100
    days = sorted({d for t in trades for d in t["marks"].index})
    starts: dict = {}
    for i, t in enumerate(trades):
        starts.setdefault(t["entry_day"], []).append(i)
    eq, peak, state = 1.0, 1.0, "ok"
    open_: dict[int, dict] = {}
    curve, risk_open, skipped, guard_days = [], [], 0, {"pause": None, "stop": None}
    for day in days:
        pnl = 0.0
        for i, o in list(open_.items()):
            t = trades[i]
            r_now = t["r"] if day >= t["exit_day"] else (float(t["marks"].loc[day]) if day in t["marks"].index else None)
            if r_now is not None:
                pnl += o["risk"] * o["eq"] * (r_now - o["last"])
                o["last"] = r_now
            if day >= t["exit_day"]:
                del open_[i]
        eq += pnl
        peak = max(peak, eq)
        if guard and state != "stop":
            if eq <= 0.70:                      # close everything and stop (robot: close_total_dd_pct 30)
                for i, o in list(open_.items()):
                    del open_[i]
                state = "stop"
                guard_days["stop"] = guard_days["stop"] or str(day.date())
            elif eq <= 0.75:                    # no new trades (pause_total_dd_pct 25)
                state = "pause"
                guard_days["pause"] = guard_days["pause"] or str(day.date())
            else:
                state = "ok"
        if state == "ok" or not guard:
            for i in starts.get(day, []):
                t = trades[i]
                risk = t["risk"]
                so = sum(o["risk"] for j, o in open_.items() if trades[j]["symbol"] == t["symbol"])
                cl = cluster_of(t["symbol"])
                co = sum(o["risk"] for j, o in open_.items() if cluster_of(trades[j]["symbol"]) == cl)
                cap = float(caps.get(cl, caps.get("default", 1.5))) / 100
                if so + risk > sym_cap + 1e-9 or co + risk > cap + 1e-9:
                    skipped += 1
                    continue
                open_[i] = {"risk": risk, "eq": eq, "last": 0.0}
        risk_open.append(sum(o["risk"] for o in open_.values()))
        curve.append((day, eq))
    s = pd.Series([v for _, v in curve], index=pd.DatetimeIndex([d for d, _ in curve]))
    return {"curve": s, "skipped": skipped, "guard": guard_days, "avg_open_risk": float(np.mean(risk_open)) if risk_open else 0.0,
            "max_open_risk": float(np.max(risk_open)) if risk_open else 0.0}


def stats(sim: dict) -> dict:
    s = sim["curve"]
    if len(s) < 30:
        return {}
    s = s[~s.index.duplicated()]
    yrs = max((s.index[-1] - s.index[0]).days / 365.25, 1e-9)
    dd = 1 - s / s.cummax()
    ret = s.pct_change().dropna()
    yearly = s.resample("YE").last().pct_change().dropna()
    win = hit = 0
    for a in pd.date_range(s.index[0], s.index[-1] - pd.Timedelta(days=365), freq="MS"):
        w = s[(s.index >= a) & (s.index < a + pd.Timedelta(days=365))]
        if len(w) > 20:
            win += 1
            hit += bool((1 - w / w.cummax()).max() >= 0.25)
    cagr = s.iloc[-1] ** (1 / yrs) - 1 if s.iloc[-1] > 0 else -1.0
    vol = ret.std() * math.sqrt(252)
    monthly = s.resample("ME").last().pct_change().dropna()
    last = s[s.index >= s.index[-1] - pd.Timedelta(days=365)]       # the sealed holdout year: picked WITHOUT these months
    hold = {"ret": round(100 * float(last.iloc[-1] / last.iloc[0] - 1), 1),
            "max_dd": round(100 * float((1 - last / last.cummax()).max()), 1)} if len(last) > 20 else None
    f = lambda x: None if x is None or (isinstance(x, float) and not math.isfinite(x)) else float(x)
    return {"from": str(s.index[0].date()), "to": str(s.index[-1].date()), "years": round(yrs, 1), "holdout_12m": hold,
            "cagr": f(round(100 * cagr, 1)), "vol": f(round(100 * vol, 1)), "max_dd": f(round(100 * dd.max(), 1)),
            "mar": f(round(cagr / dd.max(), 2)) if dd.max() > 0 else None,
            "worst_year": f(round(100 * yearly.min(), 1)) if len(yearly) else None,
            "best_year": f(round(100 * yearly.max(), 1)) if len(yearly) else None,
            "pos_months": f(round(100 * (monthly > 0).mean(), 0)) if len(monthly) else None,
            "p_dd25_1y": round(100 * hit / win, 0) if win else None,
            "guard_pause": sim["guard"]["pause"], "guard_stop": sim["guard"]["stop"],
            "avg_open_risk": round(100 * sim["avg_open_risk"], 2), "max_open_risk": round(100 * sim["max_open_risk"], 2),
            "skipped_by_caps": sim["skipped"],
            "curve": [[str(d.date()), round(float(v), 4)] for d, v in s.resample("ME").last().items()]}


def correlation(trades: list[dict]) -> float | None:
    """Average pairwise correlation of the systems' monthly R (how much they lose together)."""
    by: dict = {}
    for t in trades:
        by.setdefault(t["key"], []).append((t["exit_day"], t["r"]))
    cols = {k: pd.Series([r for _, r in v], index=pd.DatetimeIndex([d for d, _ in v])).resample("ME").sum()
            for k, v in by.items() if len(v) >= 10}
    if len(cols) < 2:
        return None
    m = pd.DataFrame(cols).fillna(0.0).corr().to_numpy()
    iu = np.triu_indices_from(m, 1)
    return round(float(np.nanmean(m[iu])), 3)


def build(settings: dict | None = None) -> dict:
    settings = settings or load_settings()
    lat = research.latest().get("symbols", {})
    risk = float(settings.get("risk_per_trade_pct") or 0.5) / 100
    live, cands = [], []
    for sym, r in lat.items():
        for x in research.live_systems(r):
            if x.get("status") == "ACTIVE":
                live.append((sym, x["strategy"], x["tf"], float(x.get("risk_pct") or 0.5) / 100))
        for x in r.get("trial_systems") or []:
            cands.append((sym, x["strategy"], x["tf"], risk))
    out = {"generated": pd.Timestamp.now(tz="UTC").isoformat(), "research_generated": research.latest().get("generated"),
           "rules": {"risk_pct": 100 * risk, "max_symbol_risk_pct": settings.get("max_symbol_risk_pct"),
                     "cluster_risk_pct": settings.get("cluster_risk_pct"), "pause_dd_pct": 25, "stop_dd_pct": 30}}
    cache: dict = {}
    for name, systems in (("live", live), ("candidates", live + cands)):
        trades = []
        for sym, sid, tf, rk in systems:
            key = (sym, sid, tf)
            if key not in cache:
                try:
                    cache[key] = system_trades(sym, sid, tf, settings)
                except Exception as e:
                    log.warning("book %s %s %s: %s", sym, sid, tf, e)
                    cache[key] = []
            trades += [{**t, "risk": rk} for t in cache[key]]
        trades.sort(key=lambda t: t["entry_day"])
        if not trades:
            out[name] = {"systems": [f"{a}:{b}:{c}" for a, b, c, _ in systems], "trades": 0}
            continue
        st = stats(simulate(trades, settings))
        out[name] = {"systems": [f"{a}:{b}:{c}" for a, b, c, _ in systems], "trades": len(trades),
                     "avg_corr": correlation(trades), **st,
                     "no_guard": {k: v for k, v in stats(simulate(trades, settings, guard=False)).items() if k != "curve"}}
        log.info("book %s: %d systems, %d trades, CAGR %s%% maxDD %s%%", name, len(systems), len(trades), st.get("cagr"), st.get("max_dd"))
    write_json(OUT, out)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    r = build()
    for k in ("live", "candidates"):
        print(k, {kk: v for kk, v in (r.get(k) or {}).items() if kk not in ("curve", "systems")})
