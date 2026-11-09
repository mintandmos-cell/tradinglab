"""Research pipeline: for every symbol, walk-forward every strategy x timeframe, pick the best by
OOS t-stat, stress it (random-entry benchmark + Monte Carlo) and decide a status:

  ACTIVE  passes every gate  -> live alerts
  WATCH   positive OOS but not proven -> forward-tracked, alerts only if settings.alerts.watch
  OFF     no evidence of an edge -> no trading

Run:  python -m app.research            (all symbols)
      python -m app.research USDJPY BTCUSD
"""
from __future__ import annotations

import gzip
import json
import logging
import sys
import threading
import time
from datetime import datetime, timezone
from statistics import NormalDist

import numpy as np
import pandas as pd

from . import data
from .config import LATEST_RESEARCH, RESEARCH_DIR, SYMBOLS, load_settings, read_json, swap_mode, symbol_spec, write_json
from .metrics import equity_points, summarize
from .montecarlo import run_mc
from .robustness import robustness
from .strategies import RETIRED, STRATEGIES
from .walkforward import random_benchmark, walk_forward

log = logging.getLogger("tradinglab.research")
STATE = {"running": False, "progress": "", "started": None, "finished": None, "error": None}
_run_lock = threading.Lock()


def adjusted_t(n_candidates: int, alpha: float = 0.05) -> float:
    """t needed when the best of n candidates is picked on OOS results (Sidak correction, one-sided).

    Picking the best of many systems inflates its t-stat by luck alone (data-mining bias; Aronson 2006,
    Harvey-Liu-Zhu 2016, Bailey & Lopez de Prado 2014). Sidak assumes independent candidates; ours are
    correlated, so this threshold is on the strict side."""
    n = max(int(n_candidates), 1)
    p = 1 - (1 - alpha) ** (1 / n)
    return round(NormalDist().inv_cdf(1 - p), 2)


def effective_tests(series: list) -> tuple[float, int]:
    """How many INDEPENDENT tests a set of candidates is worth (user, 2 Nov 2026: similar systems must not count as
    separate lottery tickets). Weekly OOS R of every candidate -> correlation matrix -> eigenvalues -> Li & Ji (2005)
    effective number M_eff = sum over eigenvalues of [lambda >= 1] + (lambda - floor(lambda)). Identical candidates
    count once, unrelated ones count fully. The same idea as the Deflated Sharpe Ratio's 'independent trials'
    (Bailey & Lopez de Prado 2014). Returns (M_eff, number of usable series)."""
    cols = [x for x in series if x is not None and len(x) and float(np.std(x.to_numpy())) > 0]
    k = len(cols)
    if k <= 1:
        return float(max(k, 1)), k
    M = pd.concat(cols, axis=1).fillna(0.0).to_numpy()
    C = np.nan_to_num(np.corrcoef(M.T))
    np.fill_diagonal(C, 1.0)
    lam = np.clip(np.linalg.eigvalsh(C), 0, None)
    m_eff = float(np.sum((lam >= 1).astype(float) + (lam - np.floor(lam))))
    return max(1.0, min(m_eff, float(k))), k


def weekly_r(oos_eq: list) -> pd.Series | None:
    """OOS trades [(unix time, R), …] -> R summed per calendar week."""
    if not oos_eq:
        return None
    t = pd.to_datetime([int(x[0]) for x in oos_eq], unit="s", utc=True)
    return pd.Series([float(x[1]) for x in oos_eq], index=t).resample("W").sum()


def only_mt_failed(gates: list[dict]) -> bool:
    """True when the multiple-testing gate (the 4th, "t ปรับตาม…") is the only failed gate."""
    failed = [i for i, x in enumerate(gates) if not x["pass"]]
    return failed == [3]


def sel_oos(c: dict) -> dict:
    """The OOS figures the gates judge: before the sealed holdout (when present), t of the MONTHLY R (3 Nov 2026)."""
    return c.get("oos_sel") or c["oos"]


def t_of(o: dict) -> float:
    return float(o["t_month"]) if o.get("t_month") is not None else float(o["t_stat"])


def _gates(best: dict, rb: dict | None, mc: dict, rob: dict, g: dict, n_cand: int = 1) -> list[dict]:
    o = sel_oos(best)
    t = t_of(o)
    t_need = adjusted_t(n_cand) if g.get("multiple_testing", True) else g["min_t"]
    hold = best.get("holdout")
    checks = [
        ("OOS trades", o["trades"], g["min_oos_trades"], o["trades"] >= g["min_oos_trades"]),
        ("OOS expectancy (R)", o["expectancy"], g["min_expectancy"], o["expectancy"] >= g["min_expectancy"]),
        ("OOS t-stat (รายเดือน)" if o.get("t_month") is not None else "OOS t-stat", round(t, 2), g["min_t"], t >= g["min_t"]),
        (f"t ปรับตามจำนวนการทดสอบอิสระ (~{n_cand})", round(t, 2), t_need, t >= t_need),
        ("MC PF @95%", mc.get("pf_p5"), g["min_mc_pf_p5"], (mc.get("pf_p5") or 0) >= g["min_mc_pf_p5"]),
        ("ชนะการสุ่มจุดเข้า (percentile)", rb["percentile"] if rb else None, g["min_random_pct"],
         bool(rb) and rb["percentile"] >= g["min_random_pct"]),
        ("Parameter plateau", best["plateau"], g["min_plateau"], best["plateau"] >= g["min_plateau"]),
        ("Robustness score", rob.get("score"), g.get("min_robust_score", 60), (rob.get("score") or 0) >= g.get("min_robust_score", 60)),
    ]
    if hold is not None:   # sealed final exam (last 12 months, never used by the other gates); < 3 trades cannot judge
        checks.append(("ข้อสอบปิดผนึก 12 เดือนล่าสุด (R/ไม้)", hold.get("expectancy") if hold.get("trades", 0) >= 3 else "ไม่พอประเมิน",
                       "≥ 0", hold.get("trades", 0) < 3 or (hold.get("expectancy") or 0) >= 0))
    return [{"name": n, "value": v, "need": need, "pass": bool(ok)} for n, v, need, ok in checks]


MC_KEEP = ("method", "sims", "n_trades", "risk_pct", "return_pct", "max_dd_pct", "pf_p5", "expectancy_p5", "prob_profit",
           "prob_dd_over_limit", "risk_of_ruin", "dd_limit_pct", "confidence", "suggested_risk_pct")
OOS_DIR = RESEARCH_DIR / "oos"          # per symbol x TF: every candidate's OOS trades + folds (chart / lab pages)
PARTS_DIR = RESEARCH_DIR / "parts"      # cloud matrix jobs: one research part per symbol x TF (not committed)


def _status(o: dict, gates: list[dict], has_params: bool) -> str:
    if all(g["pass"] for g in gates) and has_params:
        return "ACTIVE"
    if o["expectancy"] > 0 and t_of(o) >= 1.0 and o["trades"] >= 15 and has_params:
        return "WATCH"
    return "OFF"


def monthly_t(trades: list[dict], folds: list[dict], until=None, since=None) -> float | None:
    """t-stat of the OOS R summed per calendar month, months without a trade counted as 0, over the OOS span
    (first fold start .. until). Trades that come in clusters are not independent: per-trade t over-states the evidence."""
    if not folds:
        return None
    start = pd.Timestamp(since or folds[0]["oos_start"])
    end = pd.Timestamp(until or folds[-1]["oos_end"])
    months = pd.period_range(start.tz_convert(None).to_period("M"), (end - pd.Timedelta(seconds=1)).tz_convert(None).to_period("M"), freq="M")
    if len(months) < 3:
        return None
    s = pd.Series(0.0, index=months.astype(str))
    for t in trades:
        et = pd.Timestamp(t["entry_time"])
        if start <= et < end:
            m = str(et.tz_convert(None).to_period("M"))
            if m in s.index:
                s[m] += float(t["r"])
    sd = float(s.std(ddof=1))
    return round(float(s.mean() / sd * np.sqrt(len(s))), 2) if sd > 0 else 0.0


def split_oos(wf: dict, cfg: dict):
    """(figures the gates judge = OOS before the holdout, the sealed holdout = last `holdout_months` before the cut-off)."""
    hm = int(cfg.get("holdout_months", 0) or 0)
    folds, tr = wf["folds"], wf["oos_trades"]
    if not hm or not folds or not cfg.get("cutoff"):
        return None, None
    cut = pd.Timestamp(cfg["cutoff"])
    h0 = data.add_months(cut, -hm)
    pre = [t for t in tr if pd.Timestamp(t["entry_time"]) < h0]
    post = [t for t in tr if h0 <= pd.Timestamp(t["entry_time"]) < cut]
    yrs = max(1e-9, (h0 - pd.Timestamp(folds[0]["oos_start"])).days / 365.25)
    sel = summarize([t["r"] for t in pre], yrs, 1.0, bars=[t["bars"] for t in pre], cost_r=[t["cost_r"] for t in pre])
    sel["t_month"] = monthly_t(pre, folds, until=h0)
    hr = [t["r"] for t in post]
    hold = {"from": h0.isoformat(), "trades": len(hr), "net_r": round(float(np.sum(hr)), 2) if hr else 0.0,
            "expectancy": round(float(np.mean(hr)), 4) if hr else None}
    return sel, hold


def evaluate(df, wf: dict, spec: dict, tf: str, cfg: dict, settings: dict) -> tuple[dict, dict]:
    """Every step for ONE candidate (strategy x TF): walk-forward (done) -> random benchmark -> robustness ->
    Monte Carlo. Returns (summary for latest.json, heavy part: OOS trades + folds)."""
    r = [t["r"] for t in wf["oos_trades"]]
    enough = len(r) >= 5
    rb = random_benchmark(df, wf, spec, tf) if enough else None
    per_year = max(int(round(wf["oos"]["trades_per_year"])), 10)
    blk = max(1, int(round(per_year / 12)))        # ~one month of trades per block: losing streaks stay together
    mc = run_mc(r, 1.0, n_trades=per_year, dd_limit_pct=settings["target_max_dd_pct"], method="block", block=blk) if enough else {}
    mc_full = run_mc(r, 1.0, n_trades=len(r), sims=3000, method="block", block=blk) if enough else {}
    sel, hold = split_oos(wf, cfg)
    rob = robustness(df, wf, spec, tf, cfg)
    summary = {k: wf[k] for k in ("strategy", "tf", "n_combos", "oos", "oos_years", "wfe", "plateau",
                                   "param_switches", "distinct_params", "current_params", "current_is_t", "current_lock")}
    summary["oos"] = {**summary["oos"], "t_month": monthly_t(wf["oos_trades"], wf["folds"], None)}
    summary.update(oos_sel=sel, holdout=hold, sides=sides_split(wf["oos_trades"]), bh=buy_hold(df, wf))
    summary.update(name=STRATEGIES[wf["strategy"]]["name"], random_benchmark=rb,
                   mc_1y={k: mc[k] for k in MC_KEEP if k in mc}, mc_pf_p5=mc_full.get("pf_p5"), robustness=rob)
    heavy = {"oos_eq": [[int(t), round(float(x["r"]), 3)] for t, x in zip(data.to_unix([x["exit_time"] for x in wf["oos_trades"]]), wf["oos_trades"])],
             "folds": wf["folds"]}
    return summary, heavy


def sides_split(trades: list[dict]) -> dict:
    """Long vs short OOS results (user, 3 Nov 2026: a system that only earns on longs in a rising market may just be riding
    the market — compare with buy_hold)."""
    out = {}
    for name, d in (("long", 1), ("short", -1)):
        rs = [float(t["r"]) for t in trades if t.get("dir") == d]
        out[name] = {"n": len(rs), "exp": round(float(np.mean(rs)), 4) if rs else None, "net": round(float(np.sum(rs)), 2)}
    return out


def buy_hold(df, wf: dict) -> dict | None:
    """Buy-and-hold over the same OOS span: % change and % per year of the close (no costs, no leverage)."""
    folds = wf.get("folds") or []
    if not folds or not len(df):
        return None
    a, b = pd.Timestamp(folds[0]["oos_start"]), pd.Timestamp(folds[-1]["oos_end"])
    seg = df["close"][(df.index >= a) & (df.index < b)]
    if len(seg) < 2 or seg.iloc[0] <= 0:
        return None
    yrs = max((seg.index[-1] - seg.index[0]).days / 365.25, 1e-9)
    tot = float(seg.iloc[-1] / seg.iloc[0])
    return {"from": seg.index[0].isoformat(), "to": seg.index[-1].isoformat(), "pct": round(100 * (tot - 1), 1),
            "pct_year": round(100 * (tot ** (1 / yrs) - 1), 1) if yrs >= 0.5 else None}


def research_part(sym: str, tf: str, settings: dict | None = None, chunk: str | None = None) -> dict:
    """All strategies on one symbol x TF, every step for every strategy (the cloud runs the parts in parallel).
    chunk "k/n" = only every n-th strategy starting at k (109 strategies are split over 2 jobs per symbol x TF)."""
    settings = settings or load_settings()
    cfg = settings["research"]
    spec = symbol_spec(sym, settings)
    cutoff = research_cutoff(settings)
    df = data.get_bars(sym, tf)
    df = df[df.index < cutoff]          # every candidate sits the same exam: data up to the start of the current quarter
    cfg = {**cfg, "cutoff": cutoff.isoformat()}
    h1 = data.load_h1(sym)
    out = {"symbol": sym, "tf": tf, "generated": datetime.now(timezone.utc).isoformat(), "data_source": data.price_source(sym, settings), "history_source": data.history_source(sym),
           "data_from": h1.index[0].isoformat() if len(h1) else None, "data_to": h1.index[-1].isoformat() if len(h1) else None,
           "bars_h1": int(len(h1)), "bars": int(len(df)), "cutoff": cutoff.isoformat(), "candidates": [], "heavy": {}}
    if len(df) < 500:
        return out
    ids = list(STRATEGIES)
    if chunk:
        k, n = (int(x) for x in chunk.split("/"))
        ids = ids[k::n]
    for sid in ids:
        STATE["progress"] = f"{sym} {tf} {sid}"
        t0 = time.time()
        wf = walk_forward(df, sid, spec, tf, cfg)
        summary, heavy = evaluate(df, wf, spec, tf, cfg, settings)
        out["candidates"].append(summary)
        out["heavy"][sid] = heavy
        log.info("%s %s %-20s n=%4d t=%5.2f robust=%s (%.0fs)", sym, tf, sid, summary["oos"]["trades"], summary["oos"]["t_stat"],
                 (summary["robustness"] or {}).get("score"), time.time() - t0)
    return out


LEDGER_FILE = RESEARCH_DIR / "trial_ledger.json"


def ledger() -> dict:
    """The permanent record of every test ever looked at (user, 2 Nov 2026): "SYM|strategy|TF" -> {first, last}. Systems
    deleted from the library stay in it, so dropping losers (or swapping 100 failures for 100 new ideas) never lowers the
    multiple-testing bar: 100 removed + 100 new = 200 tests. Only tests with >= 10 OOS trades are recorded (as counted)."""
    return read_json(LEDGER_FILE, {"tests": {}})


def record_tests(by_sym: dict[str, list[dict]]) -> dict:
    led = ledger()
    tests = led.setdefault("tests", {})
    today = datetime.now(timezone.utc).date().isoformat()
    for sym, parts in by_sym.items():
        for p in parts:
            for c in p["candidates"]:
                if c["oos"]["trades"] >= 10:
                    t = tests.setdefault(f"{sym}|{c['strategy']}|{p['tf']}", {"first": today})
                    t["last"] = today
    led["updated"] = today
    write_json(LEDGER_FILE, led)
    return led


def ghost_tests(sym: str, current: set, settings: dict) -> int:
    """Tests of this symbol in the ledger that are not among today's candidates (deleted / retired systems, or ones that
    now have too few trades). Without a ledger: the retired strategies x timeframes, as before."""
    tests = ledger().get("tests", {})
    mine = {k for k in tests if k.split("|")[0] == sym}
    if not mine:
        return n_retired_candidates(settings)
    return len({k for k in mine if k not in current})


def n_retired_candidates(settings: dict) -> int:
    return len(RETIRED) * len(settings["research"].get("timeframes", ["H1", "H4", "D1"]))


PORT_MIN_MONTHS = 36
PORT_MIN_BREADTH = 0.6        # share of symbols (with >= 10 OOS trades) whose own OOS expectancy is positive


def portfolio_research(by_sym: dict[str, list[dict]], settings: dict, prev: dict | None = None) -> dict:
    """Fund-style test (user's request, 27 Oct 2026): one strategy x TF traded on ALL symbols at the same risk per trade,
    judged on the pooled walk-forward OOS of the whole basket. Monthly portfolio R (sum over symbols) -> t-stat, so
    correlation between markets is priced in. Trend-following CTAs are judged like this (Moskowitz et al. 2012;
    Hurst, Ooi & Pedersen 2017): each market alone is weak, the diversified basket is not.
    Gates: >= 36 OOS months, monthly t >= Sidak threshold for ALL strategy x TF candidates ever tested (retired included),
    >= 60% of markets positive, pooled MC PF@95% >= 1, median random-entry percentile >= 75, mean robustness >= 50."""
    from collections import defaultdict
    tfs = settings["research"].get("timeframes", ["H1", "H4", "D1"])
    n_port = (len(STRATEGIES) + len(RETIRED)) * len(tfs)
    seen = {tuple(k.split("|")[1:]) for k in ledger().get("tests", {})}          # every strategy x TF ever tested
    tested = {(c["strategy"], p["tf"]) for parts in by_sym.values() for p in parts for c in p["candidates"]}
    n_port = max(len(seen | tested), (len({x[0] for x in tested}) + len(RETIRED)) * len(tfs))
    t_need = adjusted_t(n_port)          # replaced below by the effective number of independent portfolio candidates
    cands = []
    for sid in STRATEGIES:
        for tf in tfs:
            legs, monthly, pooled, first = [], defaultdict(float), [], None
            pooled_m = []            # month of each pooled trade (sealed holdout split)
            for sym, parts in by_sym.items():
                part = next((p for p in parts if p["tf"] == tf), None)
                c = next((x for x in (part or {}).get("candidates", []) if x["strategy"] == sid), None)
                if not c:
                    continue
                eq = part["heavy"].get(sid, {}).get("oos_eq", [])
                folds = part["heavy"].get(sid, {}).get("folds", [])
                if folds:
                    f0 = pd.Timestamp(folds[0]["oos_start"])
                    first = f0 if first is None else min(first, f0)
                if eq:      # vectorised: 500 systems x 20 symbols x 3 TF (a per-trade Timestamp loop took ~40 min)
                    arr = np.asarray(eq, float)
                    ms = pd.to_datetime(arr[:, 0].astype("int64"), unit="s", utc=True).strftime("%Y-%m")
                    for m, r in pd.Series(arr[:, 1]).groupby(np.asarray(ms)).sum().items():
                        monthly[m] += float(r)
                    pooled.extend(arr[:, 1].tolist())
                    pooled_m.extend(ms.tolist())
                legs.append({"symbol": sym, "trades": c["oos"]["trades"], "exp": c["oos"]["expectancy"], "t": c["oos"]["t_stat"],
                             "params": c["current_params"], "pct": (c.get("random_benchmark") or {}).get("percentile"),
                             "robust": (c.get("robustness") or {}).get("score")})
            if not pooled or first is None:
                continue
            cutoff = pd.Timestamp(next(iter(by_sym.values()))[0].get("cutoff") or research_cutoff(settings))
            months = pd.period_range(first.tz_convert(None).to_period("M"), (cutoff - pd.Timedelta(seconds=1)).tz_convert(None).to_period("M"), freq="M")
            # same sealed final exam as single systems (user, 3 Nov 2026): judge the months BEFORE the last
            # holdout_months, then the holdout on its own (pooled expectancy >= 0)
            hm = int(settings["research"].get("holdout_months", 12) or 0)
            mr_all = np.array([monthly.get(str(m), 0.0) for m in months])
            hold_from = str(months[-hm]) if hm and len(months) > hm + PORT_MIN_MONTHS else None
            sel_m = [m for m in months if hold_from is None or str(m) < hold_from]
            mr = np.array([monthly.get(str(m), 0.0) for m in sel_m])
            sd = mr.std(ddof=1) if len(mr) > 2 else 0
            t_m = float(mr.mean() / sd * np.sqrt(len(mr))) if sd > 0 else 0.0
            pm = np.asarray(pooled_m)
            pr_all = np.array(pooled)
            pr = pr_all[pm < hold_from] if hold_from else pr_all
            ph = pr_all[pm >= hold_from] if hold_from else np.array([])
            if not len(pr):
                continue
            active_legs = [x for x in legs if x["trades"] >= 10]
            breadth = (sum(x["exp"] > 0 for x in active_legs) / len(active_legs)) if active_legs else 0.0
            pcts = [x["pct"] for x in legs if x["pct"] is not None]
            robs = [x["robust"] for x in legs if x["robust"] is not None]
            blk = max(1, int(round(len(pr) / max(len(mr), 1))))       # ~one month of pooled trades per block
            mc = run_mc(list(pr), 1.0, n_trades=len(pr), sims=2000, method="block", block=blk) if len(pr) >= 20 else {}
            hold = {"from": hold_from, "trades": int(len(ph)), "expectancy": round(float(ph.mean()), 4) if len(ph) else None,
                    "net_r": round(float(ph.sum()), 1)} if hold_from else None
            gates = [
                {"name": "เดือน OOS", "value": len(mr), "need": PORT_MIN_MONTHS, "pass": len(mr) >= PORT_MIN_MONTHS},
                {"name": f"t รายเดือนของพอร์ต (ปรับตาม {n_port} ผู้สมัคร)", "value": round(t_m, 2), "need": t_need, "pass": t_m >= t_need},
                {"name": "สินทรัพย์ที่เป็นบวก", "value": round(breadth * 100), "need": round(PORT_MIN_BREADTH * 100), "pass": breadth >= PORT_MIN_BREADTH},
                {"name": "MC PF @95% (ไม้รวม)", "value": mc.get("pf_p5"), "need": 1.0, "pass": (mc.get("pf_p5") or 0) >= 1.0},
                {"name": "ชนะการสุ่มจุดเข้า (มัธยฐาน %)", "value": float(np.median(pcts)) if pcts else None, "need": 75,
                 "pass": bool(pcts) and float(np.median(pcts)) >= 75},
                {"name": "Robustness เฉลี่ย", "value": round(float(np.mean(robs))) if robs else None, "need": 50,
                 "pass": bool(robs) and float(np.mean(robs)) >= 50},
                {"name": f"ข้อสอบปิดผนึก {hm} เดือนล่าสุด (Exp ≥ 0)", "value": (hold or {}).get("expectancy"), "need": 0,
                 "pass": not hold or hold["trades"] < 3 or hold["expectancy"] >= 0},
            ]
            ok = all(g["pass"] for g in gates)
            status = "ACTIVE" if ok else ("WATCH" if t_m >= 2 and pr.mean() > 0 else "OFF")
            cands.append({"_mr": pd.Series(mr, index=[str(m) for m in sel_m]),
                          "strategy": sid, "name": STRATEGIES[sid]["name"], "tf": tf, "status": status, "t_month": round(t_m, 2),
                          "months": len(mr), "trades": len(pr), "exp": round(float(pr.mean()), 4), "net_r": round(float(pr.sum()), 1),
                          "r_month": round(float(mr.mean()), 3), "breadth": round(breadth, 2), "gates": gates, "legs": legs,
                          "holdout": hold, "months_all": len(mr_all),
                          "max_dd_r": round(float(np.max(np.maximum.accumulate(np.cumsum(mr)) - np.cumsum(mr))), 1) if len(mr) else None})
    # same quarter: portfolio systems already ACTIVE stay until the quarter ends
    n_raw = n_port
    if settings["research"].get("effective_trials", True) and cands:
        m_eff, k = effective_tests([c["_mr"] for c in cands])
        n_port = max(1, int(round(m_eff * n_raw / max(k, 1))))
        t_need = adjusted_t(n_port)
        for c in cands:
            g1 = c["gates"][1]
            g1.update(name=f"t รายเดือนของพอร์ต (ปรับตาม ~{n_port} การทดสอบอิสระ จาก {n_raw})", need=t_need,
                      **{"pass": g1["value"] >= t_need})
            ok = all(g["pass"] for g in c["gates"])
            c["status"] = "ACTIVE" if ok else ("WATCH" if c["t_month"] >= 2 and c["exp"] > 0 else "OFF")
    for c in cands:
        c.pop("_mr", None)
    # same rule as single systems: same quarter + same price histories + only the multiple-testing gate (index 1) failed
    hist = {s: p[0].get("history_source") for s, p in by_sym.items()}
    if prev and prev.get("cutoff") == (next(iter(by_sym.values()))[0].get("cutoff")) and prev.get("history") == hist:
        was = {(x["strategy"], x["tf"]) for x in prev.get("candidates", []) if x.get("status") == "ACTIVE"}
        for c in cands:
            if (c["strategy"], c["tf"]) in was and c["status"] != "ACTIVE" and [i for i, x in enumerate(c["gates"]) if not x["pass"]] == [1]:
                c["status"], c["kept"] = "ACTIVE", True
    cands.sort(key=lambda c: ({"ACTIVE": 2, "WATCH": 1, "OFF": 0}[c["status"]], c["t_month"]), reverse=True)
    return {"cutoff": next(iter(by_sym.values()))[0].get("cutoff"), "history": hist, "n_candidates": n_port, "n_raw": n_raw,
            "t_needed": t_need,
            "candidates": cands}


def research_cutoff(settings: dict | None = None, now: datetime | None = None) -> pd.Timestamp:
    """The research cut-off = start of the current research period on the broker clock: 1 Jan each year
    (research.period_months 12, user 9 Nov 2026 — was the start of each quarter). A run in the middle of the period
    (new strategies, button) uses the same cut-off, so it sees exactly the same data. research.cutoff_floor keeps the
    last quarterly cut-off (1 Oct 2026) until the first yearly one. settings.research.cutoff (ISO date) pins it for testing."""
    cfg = (settings or load_settings())["research"]
    pm = int(cfg.get("period_months") or 3)
    if cfg.get("cutoff"):
        return data.period_start(pd.Timestamp(cfg["cutoff"]), pm)
    cut = data.period_start(pd.Timestamp(now or datetime.now(timezone.utc)), pm)
    floor = cfg.get("cutoff_floor")
    if floor and cut < pd.Timestamp(floor) <= pd.Timestamp(now or datetime.now(timezone.utc)):
        cut = pd.Timestamp(floor)
    return cut


def _forced_off(settings: dict, sym: str, c: dict) -> bool:
    fo = settings.get("force_off", [])
    return sym in fo or f"{sym}:{c['strategy']}:{c['tf']}" in fo


def trial_systems(sym: str, cands: list[dict], settings: dict) -> list[dict]:
    """Near-misses traded on the DEMO account only, at a small risk, to collect live evidence (user, 2 Nov 2026):
    WATCH candidates that pass every gate except the multiple-testing one. H4 / D1 first (lower costs), then t."""
    tcfg = settings.get("trials", {})
    if not tcfg.get("enabled", True):
        return []
    near = [c for c in cands if c["status"] == "WATCH" and c.get("current_params") and only_mt_failed(c["gates"])
            and not _forced_off(settings, sym, c)]
    near.sort(key=lambda c: (c["tf"] != "H1", t_of(sel_oos(c))), reverse=True)
    return [{"strategy": c["strategy"], "strategy_name": STRATEGIES[c["strategy"]]["name"], "tf": c["tf"],
             "params": c["current_params"], "status": "TRIAL", "source": "trial",
             "risk_pct": float(tcfg.get("risk_pct", 0.1)), "t": t_of(sel_oos(c)), "exp": c["oos"]["expectancy"],
             "trades": c["oos"]["trades"], "max_dd_r": c["oos"].get("max_dd_r"),
             "robust": (c["robustness"] or {}).get("score")} for c in near[:int(tcfg.get("max_per_symbol", 2))]]


def merge_symbol(sym: str, parts: list[dict], settings: dict, prev: dict | None = None) -> dict:
    """Judge every candidate with the same gates. EVERY candidate that passes is ACTIVE and traded for the quarter
    (user's rule, 27 Oct 2026). A re-run in the same quarter keeps systems that were already ACTIVE until the quarter ends
    (only a manual / forward-test veto in force_off stops them early); new candidates must pass the current gates."""
    cfg = settings["research"]
    g = cfg["gates"]
    cands = [c for p in parts for c in p["candidates"]]
    if not cands:
        return {"symbol": sym, "status": "OFF", "reason": "ข้อมูลไม่พอ", "candidates": [], "systems": []}
    cutoff = pd.Timestamp(parts[0].get("cutoff") or research_cutoff(settings))
    # the set is traded until the next yearly cut-off (1 Jan); the switch-over set (cut 1 Oct 2026) until 1 Jan 2027
    pm = int(cfg.get("period_months") or 3)
    until = data.add_months(data.period_start(cutoff, pm), pm)
    # retired strategies stay in the count (user's rule): dropping losers must never lower the bar for the rest.
    # The count is the EFFECTIVE number of independent tests (correlated candidates count less), retired ones scaled alike.
    used = [c for c in cands if c["oos"]["trades"] >= 10]
    raw_n = len(used) + ghost_tests(sym, {f"{sym}|{c['strategy']}|{c['tf']}" for c in used}, settings)
    if cfg.get("effective_trials", True) and used:
        heavy_of = {(p["tf"], sid): h for p in parts for sid, h in p["heavy"].items()}
        m_eff, k = effective_tests([weekly_r(heavy_of.get((c["tf"], c["strategy"]), {}).get("oos_eq", [])) for c in used])
        n_cand = max(1, int(round(m_eff * raw_n / max(k, 1)))) if k else raw_n
    else:
        n_cand = raw_n
    for c in cands:
        c["gates"] = _gates(c, c["random_benchmark"], {"pf_p5": c["mc_pf_p5"]}, c["robustness"] or {}, g, n_cand)
        c["status"] = _status(sel_oos(c), c["gates"], bool(c["current_params"]))
        c.pop("kept", None)
    # carry-over (user, 27 Oct 2026; narrowed 2 Nov 2026): in the SAME quarter, on the SAME price history, a system that was
    # ACTIVE stays ACTIVE when the ONLY gate it now fails is the multiple-testing one (that bar rises whenever strategies are
    # added). Different prices (e.g. the switch to the broker's own history) or any other failed gate = judged afresh.
    first_hist = hist = parts[0].get("history_source")
    same_q = bool(prev) and (prev.get("quarter") or {}).get("cutoff") == cutoff.isoformat()
    same_data = bool(prev) and bool(first_hist) and prev.get("history_source") == hist
    if same_q and same_data:
        was = {(x["strategy"], x["tf"]) for x in prev.get("systems", []) if x.get("source", "single") == "single"}
        for c in cands:
            if (c["strategy"], c["tf"]) in was and c["status"] != "ACTIVE" and c["current_params"] and only_mt_failed(c["gates"]):
                c["status"], c["kept"] = "ACTIVE", True
    for c in cands:
        if c["status"] == "ACTIVE" and _forced_off(settings, sym, c):
            c["status"], c["forced_off"] = "OFF", True
    rank = {"ACTIVE": 2, "WATCH": 1, "OFF": 0}
    key = lambda c: (rank[c["status"]], t_of(sel_oos(c)) if c["oos"]["trades"] >= 10 else -99)
    cands.sort(key=key, reverse=True)
    active = [c for c in cands if c["status"] == "ACTIVE"]
    best = cands[0]
    status = best["status"]
    risk = float(settings.get("risk_per_trade_pct") or settings["default_risk_pct"])
    systems = [{"strategy": c["strategy"], "strategy_name": STRATEGIES[c["strategy"]]["name"], "tf": c["tf"],
                "params": c["current_params"], "status": "ACTIVE", "risk_pct": risk, "kept": bool(c.get("kept")), "source": "single",
                "t": t_of(sel_oos(c)), "exp": c["oos"]["expectancy"], "trades": c["oos"]["trades"],
                "max_dd_r": c["oos"].get("max_dd_r"), "robust": (c["robustness"] or {}).get("score")} for c in active]
    mc = best["mc_1y"] or {}
    part = next(p for p in parts if p["tf"] == best["tf"])
    heavy = part["heavy"].get(best["strategy"], {})
    eq = heavy.get("oos_eq", [])
    r = [x[1] for x in eq]
    first = parts[0]
    return {
        "symbol": sym, "status": status, "data_source": first["data_source"], "history_source": first.get("history_source"),
        "data_from": first["data_from"], "data_to": first["data_to"], "bars_h1": first["bars_h1"],
        "quarter": {"cutoff": cutoff.isoformat(), "until": until.isoformat()}, "systems": systems,
        "max_symbol_risk_pct": settings.get("max_symbol_risk_pct", 0.75),
        "strategy": best["strategy"], "strategy_name": STRATEGIES[best["strategy"]]["name"], "tf": best["tf"],
        "params": best["current_params"], "risk_pct": risk if status != "OFF" else 0.0,
        "locked_from": cutoff.isoformat(), "locked_until": until.isoformat(),
        "oos": best["oos"], "oos_years": best["oos_years"], "wfe": best["wfe"], "plateau": best["plateau"],
        "param_switches": best["param_switches"], "folds": heavy.get("folds", []),
        "random_benchmark": best["random_benchmark"], "mc_1y": mc, "mc_pf_p5": best["mc_pf_p5"], "robustness": best["robustness"],
        "gates": best["gates"], "failed": [x["name"] for x in best["gates"] if not x["pass"]],
        "trials": int(sum(c["n_combos"] for c in cands)), "n_candidates": n_cand, "t_needed": adjusted_t(n_cand),
        "n_active": len(active), "n_watch": sum(c["status"] == "WATCH" for c in cands), "n_raw": raw_n,
        "trial_systems": trial_systems(sym, cands, settings),
        "candidates": cands, "oos_r": r, "oos_equity": equity_points([x[0] for x in eq], r, 1.0, 10000.0),
    }


def save_heavy(part: dict) -> None:
    OOS_DIR.mkdir(parents=True, exist_ok=True)
    with gzip.open(OOS_DIR / f"{part['symbol']}_{part['tf']}.json.gz", "wt", encoding="utf-8") as f:
        json.dump(part["heavy"], f, separators=(",", ":"))


_heavy_cache: dict = {}


def heavy(sym: str, tf: str) -> dict:
    """Per-candidate OOS trades + folds for a symbol x TF (strategy id -> {oos_eq, folds})."""
    f = OOS_DIR / f"{sym}_{tf}.json.gz"
    if not f.exists():
        return {}
    key = (sym, tf, f.stat().st_mtime)
    if key not in _heavy_cache:
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            _heavy_cache[key] = json.load(fh)
    return _heavy_cache[key]


def attach_portfolio(out: dict, settings: dict) -> None:
    """ACTIVE portfolio systems trade on every symbol (marked source = "portfolio")."""
    risk = float(settings.get("risk_per_trade_pct") or settings["default_risk_pct"])
    for pc in (out.get("portfolio") or {}).get("candidates", []):
        if pc["status"] != "ACTIVE":
            continue
        for leg in pc["legs"]:
            r = out["symbols"].get(leg["symbol"])
            if not r or not leg.get("params") or f"{leg['symbol']}:{pc['strategy']}:{pc['tf']}" in settings.get("force_off", []) \
                    or leg["symbol"] in settings.get("force_off", []):
                continue
            syst = r.setdefault("systems", [])
            if any(x["strategy"] == pc["strategy"] and x["tf"] == pc["tf"] for x in syst):
                continue
            syst.append({"strategy": pc["strategy"], "strategy_name": pc["name"], "tf": pc["tf"], "params": leg["params"],
                         "status": "ACTIVE", "risk_pct": float(settings.get("portfolio_risk_pct") or 0.1), "kept": bool(pc.get("kept")), "source": "portfolio",
                         "t": leg["t"], "exp": leg["exp"], "trades": leg["trades"], "robust": leg["robust"]})
            if r.get("status") != "ACTIVE":
                r["status"] = "ACTIVE"
                r["risk_pct"] = risk


def merge(parts: list[dict], symbols: list[str] | None = None) -> dict:
    """Write latest.json (+ history snapshot) from research parts; heavy OOS data goes to data/research/oos/*.json.gz."""
    settings = load_settings()
    prev = read_json(LATEST_RESEARCH, {"symbols": {}})
    out = {"generated": None, "settings": settings["research"], "symbols": dict(prev.get("symbols", {}))}
    # parts split by strategy chunks: one part per symbol x TF again (candidates + heavy data joined)
    joined: dict[tuple, dict] = {}
    for p in parts:
        key = (p["symbol"], p["tf"])
        if key not in joined:
            joined[key] = {**p, "candidates": list(p["candidates"]), "heavy": dict(p["heavy"])}
        else:
            joined[key]["candidates"] += p["candidates"]
            joined[key]["heavy"].update(p["heavy"])
    parts = list(joined.values())
    by_sym: dict[str, list[dict]] = {}
    for p in parts:
        by_sym.setdefault(p["symbol"], []).append(p)
        save_heavy(p)
    record_tests(by_sym)                 # the permanent test ledger first: deleted systems keep counting
    for sym in symbols or list(SYMBOLS):
        if sym not in by_sym:
            continue
        try:
            res = merge_symbol(sym, by_sym[sym], settings, prev.get("symbols", {}).get(sym))
        except Exception as e:
            log.exception("merge %s failed", sym)
            res = {"symbol": sym, "status": "OFF", "reason": f"error: {e}", "candidates": []}
        old = prev.get("symbols", {}).get(sym)
        if old:
            res["previous"] = {k: old.get(k) for k in ("status", "strategy", "tf", "params", "risk_pct", "quarter")} | {
                "generated": prev.get("generated"), "systems": [(x["strategy"], x["tf"]) for x in old.get("systems", [])]}
        out["symbols"][sym] = res
    # fund-style basket test; its ACTIVE systems trade on every symbol (marked source = "portfolio")
    if len(by_sym) >= 4:
        try:
            out["portfolio"] = portfolio_research(by_sym, settings, prev.get("portfolio"))
        except Exception:
            log.exception("portfolio research failed")
    elif prev.get("portfolio"):
        out["portfolio"] = prev["portfolio"]
    attach_portfolio(out, settings)
    try:
        from . import promotion                  # every trial ever listed counts in the forward-promotion field
        promotion.register(out["symbols"])
    except Exception:
        log.exception("trial registry failed")
    for sym, r in out["symbols"].items():           # full records to cands/{SYM}.json.gz, a slim copy stays in latest.json
        if r.get("candidates"):
            save_candidates(sym, r["candidates"])
            r["candidates"] = [slim(c) for c in r["candidates"]]
    out["generated"] = datetime.now(timezone.utc).isoformat()
    write_json(LATEST_RESEARCH, out)
    write_json(RESEARCH_DIR / "history" / f"{datetime.now(timezone.utc):%Y%m%d_%H%M}.json",
               {"generated": out["generated"], "symbols": {k: {kk: v.get(kk) for kk in (
                   "status", "strategy", "tf", "params", "risk_pct", "oos", "failed", "trials", "n_active", "n_watch")}
                   | {"robust_score": (v.get("robustness") or {}).get("score")} for k, v in out["symbols"].items()}})
    return out


def run_all(symbols: list[str] | None = None) -> dict:
    """Local / PC: every part one after another (slow: hours with long history), then merge."""
    if not _run_lock.acquire(blocking=False):
        return {"error": "research already running"}
    try:
        STATE.update(running=True, started=datetime.now(timezone.utc).isoformat(), error=None)
        settings = load_settings()
        parts = []
        for sym in symbols or list(SYMBOLS):
            data.load_h1(sym, refresh=True)
            for tf in settings["research"]["timeframes"]:
                try:
                    parts.append(research_part(sym, tf, settings))
                except Exception:
                    log.exception("research %s %s failed", sym, tf)
        return merge(parts, symbols)
    except Exception as e:
        STATE["error"] = repr(e)
        raise
    finally:
        STATE.update(running=False, finished=datetime.now(timezone.utc).isoformat(), progress="")
        _run_lock.release()


def run_in_background(symbols=None) -> bool:
    if STATE["running"]:
        return False
    threading.Thread(target=run_all, args=(symbols,), daemon=True, name="research").start()
    return True


def latest() -> dict:
    return read_json(LATEST_RESEARCH, {"generated": None, "symbols": {}})


CANDS_DIR = RESEARCH_DIR / "cands"       # full candidate records per symbol (gates, robustness, MC …) — latest.json keeps a slim copy
SLIM_OOS = ("trades", "net_r", "expectancy", "gross_expectancy", "win_rate", "pf", "t_stat", "max_dd_r", "trades_per_year",
            "avg_win", "avg_loss", "max_consec_loss", "avg_bars")


def slim(c: dict) -> dict:
    """What the scanner, the quick-pick list and the overview need (500 systems x 3 TF x 20 symbols must stay small)."""
    out = {k: c.get(k) for k in ("strategy", "tf", "status", "kept", "forced_off", "current_params", "n_combos", "oos_years",
                                 "wfe", "plateau", "param_switches", "mc_pf_p5") if c.get(k) is not None}
    out["oos"] = {k: (c.get("oos") or {}).get(k) for k in SLIM_OOS}
    if c.get("oos_sel"):
        out["oos_sel"] = {k: c["oos_sel"].get(k) for k in SLIM_OOS + ("t_month",)}
    if c.get("holdout"):
        out["holdout"] = c["holdout"]
    out["oos"]["t_month"] = (c.get("oos") or {}).get("t_month")
    out["robustness"] = {"score": (c.get("robustness") or {}).get("score")}
    if c.get("random_benchmark"):
        out["random_benchmark"] = {"percentile": c["random_benchmark"].get("percentile")}
    return out


def save_candidates(sym: str, cands: list[dict]) -> None:
    CANDS_DIR.mkdir(parents=True, exist_ok=True)
    with gzip.open(CANDS_DIR / f"{sym}.json.gz", "wt", encoding="utf-8") as f:
        json.dump([{k: v for k, v in c.items() if k != "oos_eq"} for c in cands], f, separators=(",", ":"), default=str)


def candidates(sym: str, tf: str | None = None) -> list[dict]:
    """Full candidate records of a symbol (Lab page, re-judging); falls back to latest.json's copy."""
    f = CANDS_DIR / f"{sym}.json.gz"
    if f.exists():
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            out = json.load(fh)
    else:
        out = (latest().get("symbols", {}).get(sym) or {}).get("candidates", [])
    return [c for c in out if tf is None or c.get("tf") == tf]


def is_live(sym_entry: dict, strategy: str, tf: str) -> bool:
    """Is this strategy x TF one of the symbol's traded (ACTIVE) systems this quarter?"""
    if "systems" in (sym_entry or {}):
        return any(x["strategy"] == strategy and x["tf"] == tf for x in sym_entry["systems"])
    return sym_entry.get("strategy") == strategy and sym_entry.get("tf") == tf and sym_entry.get("status") == "ACTIVE"


def live_systems(sym_entry: dict) -> list[dict]:
    """Systems the scanner trades for a symbol (old latest.json without 'systems': the single ACTIVE/WATCH system)."""
    if "systems" in (sym_entry or {}):
        return sym_entry["systems"]
    if sym_entry.get("status") in ("ACTIVE", "WATCH") and sym_entry.get("params"):
        return [{k: sym_entry.get(k) for k in ("strategy", "strategy_name", "tf", "params", "status", "risk_pct")}]
    return []


def picks(lat: dict | None = None) -> list[dict]:
    """Quick-pick list for the right rail: every ACTIVE / WATCH candidate of every symbol (plus each symbol's
    current system), most important first: ACTIVE before WATCH, then the higher OOS t-stat."""
    lat = lat or latest()
    out = []
    for sym, r in (lat.get("symbols") or {}).items():
        for c in r.get("candidates", []):
            st = c.get("status")
            live = is_live(r, c.get("strategy"), c.get("tf"))
            port = any(x["strategy"] == c.get("strategy") and x["tf"] == c.get("tf") and x.get("source") == "portfolio"
                       for x in r.get("systems", []))
            if port:
                st = "ACTIVE"          # traded as part of a fund-style portfolio system (its own single-symbol status may be WATCH)
            if st in ("ACTIVE", "WATCH") or live:
                o = c.get("oos") or {}
                out.append({"symbol": sym, "tf": c["tf"], "strategy": c["strategy"], "status": st or r.get("status"), "live": live,
                            "portfolio": port,
                            "t": o.get("t_stat"), "exp": o.get("expectancy"), "trades": o.get("trades"),
                            "robust": (c.get("robustness") or {}).get("score")})
    rank = {"ACTIVE": 0, "WATCH": 1}
    out.sort(key=lambda x: (rank.get(x["status"], 2), not x["live"], -(x["t"] or -99)))
    keep = [x for x in out if x["status"] == "ACTIVE" or x["live"]]
    return keep + [x for x in out if x not in keep][:max(0, 300 - len(keep))]     # 500 systems: the best 300 WATCH


def _usd_per_unit(sym: str, settings: dict) -> float | None:
    """USD P/L of 1 lot per 1.0 price move (the web calculator / signal cards size non-USD-quoted symbols with it)."""
    try:
        from .sizing import _last_price, value_per_price_unit
        px = _last_price(sym)
        return round(value_per_price_unit(sym, px, None, settings), 6) if px else None
    except Exception:
        return None


def symbol_table(settings: dict | None = None) -> list[dict]:
    s = settings or load_settings()
    lat = latest().get("symbols", {})
    out = []
    for k in SYMBOLS:
        r = lat.get(k, {})
        out.append({**symbol_spec(k, s), "mt5_symbol": s["mt5_symbol_map"].get(k, k), "status": r.get("status", "-"),
                    "strategy": r.get("strategy"), "strategy_name": r.get("strategy_name"), "tf": r.get("tf"),
                    "params": r.get("params"), "risk_pct": r.get("risk_pct"), "oos": r.get("oos"),
                    "robust_score": (r.get("robustness") or {}).get("score"),
                    "systems": [{"strategy": x["strategy"], "tf": x["tf"], "kept": x.get("kept", False), "source": x.get("source", "single"),
                                 "risk_pct": x.get("risk_pct"), "exp": x.get("exp"), "t": x.get("t")} for x in live_systems(r)
                                if x.get("status") == "ACTIVE"],
                    "quarter": r.get("quarter"), "swap": swap_mode(s, k), "usd_per_unit": _usd_per_unit(k, s)})
    return out


def history(limit: int = 60) -> list[dict]:
    out = []
    for f in sorted((RESEARCH_DIR / "history").glob("*.json"), reverse=True)[:limit]:
        try:
            j = read_json(f, {})
            out.append({"file": f.name, "generated": j.get("generated"),
                        "symbols": {k: {"status": v.get("status"), "strategy": v.get("strategy"), "tf": v.get("tf"),
                                        "expectancy": (v.get("oos") or {}).get("expectancy"),
                                        "robust_score": v.get("robust_score")} for k, v in j.get("symbols", {}).items()}})
        except Exception:
            continue
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    syms = [s.upper() for s in sys.argv[1:]] or None
    res = run_all(syms)
    for k, v in res["symbols"].items():
        o = v.get("oos") or {}
        print(f"{k:7s} {v['status']:6s} {v.get('strategy', '-'):15s} {v.get('tf', '-'):3s} OOS n={o.get('trades')} "
              f"exp={o.get('expectancy')} t={o.get('t_stat')} risk={v.get('risk_pct')} failed={v.get('failed')}")
