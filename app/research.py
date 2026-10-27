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
from .config import LATEST_RESEARCH, RESEARCH_DIR, SYMBOLS, load_settings, read_json, symbol_spec, write_json
from .metrics import equity_points
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


def _gates(best: dict, rb: dict | None, mc: dict, rob: dict, g: dict, n_cand: int = 1) -> list[dict]:
    o = best["oos"]
    t_need = adjusted_t(n_cand) if g.get("multiple_testing", True) else g["min_t"]
    checks = [
        ("OOS trades", o["trades"], g["min_oos_trades"], o["trades"] >= g["min_oos_trades"]),
        ("OOS expectancy (R)", o["expectancy"], g["min_expectancy"], o["expectancy"] >= g["min_expectancy"]),
        ("OOS t-stat", o["t_stat"], g["min_t"], o["t_stat"] >= g["min_t"]),
        (f"t ปรับตามจำนวนระบบที่เทียบ ({n_cand})", o["t_stat"], t_need, o["t_stat"] >= t_need),
        ("MC PF @95%", mc.get("pf_p5"), g["min_mc_pf_p5"], (mc.get("pf_p5") or 0) >= g["min_mc_pf_p5"]),
        ("ชนะการสุ่มจุดเข้า (percentile)", rb["percentile"] if rb else None, g["min_random_pct"],
         bool(rb) and rb["percentile"] >= g["min_random_pct"]),
        ("Parameter plateau", best["plateau"], g["min_plateau"], best["plateau"] >= g["min_plateau"]),
        ("Robustness score", rob.get("score"), g.get("min_robust_score", 60), (rob.get("score") or 0) >= g.get("min_robust_score", 60)),
    ]
    return [{"name": n, "value": v, "need": need, "pass": bool(ok)} for n, v, need, ok in checks]


MC_KEEP = ("method", "sims", "n_trades", "risk_pct", "return_pct", "max_dd_pct", "pf_p5", "expectancy_p5", "prob_profit",
           "prob_dd_over_limit", "risk_of_ruin", "dd_limit_pct", "confidence", "suggested_risk_pct")
OOS_DIR = RESEARCH_DIR / "oos"          # per symbol x TF: every candidate's OOS trades + folds (chart / lab pages)
PARTS_DIR = RESEARCH_DIR / "parts"      # cloud matrix jobs: one research part per symbol x TF (not committed)


def _status(o: dict, gates: list[dict], has_params: bool) -> str:
    if all(g["pass"] for g in gates) and has_params:
        return "ACTIVE"
    if o["expectancy"] > 0 and o["t_stat"] >= 1.0 and o["trades"] >= 15 and has_params:
        return "WATCH"
    return "OFF"


def evaluate(df, wf: dict, spec: dict, tf: str, cfg: dict, settings: dict) -> tuple[dict, dict]:
    """Every step for ONE candidate (strategy x TF): walk-forward (done) -> random benchmark -> robustness ->
    Monte Carlo. Returns (summary for latest.json, heavy part: OOS trades + folds)."""
    r = [t["r"] for t in wf["oos_trades"]]
    enough = len(r) >= 5
    rb = random_benchmark(df, wf, spec, tf) if enough else None
    per_year = max(int(round(wf["oos"]["trades_per_year"])), 10)
    mc = run_mc(r, 1.0, n_trades=per_year, dd_limit_pct=settings["target_max_dd_pct"]) if enough else {}
    mc_full = run_mc(r, 1.0, n_trades=len(r), sims=3000) if enough else {}
    rob = robustness(df, wf, spec, tf, cfg)
    summary = {k: wf[k] for k in ("strategy", "tf", "n_combos", "oos", "oos_years", "wfe", "plateau",
                                   "param_switches", "distinct_params", "current_params", "current_is_t", "current_lock")}
    summary.update(name=STRATEGIES[wf["strategy"]]["name"], random_benchmark=rb,
                   mc_1y={k: mc[k] for k in MC_KEEP if k in mc}, mc_pf_p5=mc_full.get("pf_p5"), robustness=rob)
    heavy = {"oos_eq": [[int(t), round(float(x["r"]), 3)] for t, x in zip(data.to_unix([x["exit_time"] for x in wf["oos_trades"]]), wf["oos_trades"])],
             "folds": wf["folds"]}
    return summary, heavy


def research_part(sym: str, tf: str, settings: dict | None = None) -> dict:
    """All strategies on one symbol x TF, every step for every strategy (the cloud runs the 24 parts in parallel)."""
    settings = settings or load_settings()
    cfg = settings["research"]
    spec = symbol_spec(sym, settings)
    cutoff = research_cutoff(settings)
    df = data.get_bars(sym, tf)
    df = df[df.index < cutoff]          # every candidate sits the same exam: data up to the start of the current quarter
    cfg = {**cfg, "cutoff": cutoff.isoformat()}
    h1 = data.load_h1(sym)
    out = {"symbol": sym, "tf": tf, "generated": datetime.now(timezone.utc).isoformat(), "data_source": data.source_for(sym, settings),
           "data_from": h1.index[0].isoformat() if len(h1) else None, "data_to": h1.index[-1].isoformat() if len(h1) else None,
           "bars_h1": int(len(h1)), "bars": int(len(df)), "cutoff": cutoff.isoformat(), "candidates": [], "heavy": {}}
    if len(df) < 500:
        return out
    for sid in STRATEGIES:
        STATE["progress"] = f"{sym} {tf} {sid}"
        t0 = time.time()
        wf = walk_forward(df, sid, spec, tf, cfg)
        summary, heavy = evaluate(df, wf, spec, tf, cfg, settings)
        out["candidates"].append(summary)
        out["heavy"][sid] = heavy
        log.info("%s %s %-20s n=%4d t=%5.2f robust=%s (%.0fs)", sym, tf, sid, summary["oos"]["trades"], summary["oos"]["t_stat"],
                 (summary["robustness"] or {}).get("score"), time.time() - t0)
    return out


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
    t_need = adjusted_t(n_port)
    cands = []
    for sid in STRATEGIES:
        for tf in tfs:
            legs, monthly, pooled, first = [], defaultdict(float), [], None
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
                for t_, r in eq:
                    m = pd.Timestamp(int(t_), unit="s", tz="UTC").strftime("%Y-%m")
                    monthly[m] += r
                    pooled.append(r)
                legs.append({"symbol": sym, "trades": c["oos"]["trades"], "exp": c["oos"]["expectancy"], "t": c["oos"]["t_stat"],
                             "params": c["current_params"], "pct": (c.get("random_benchmark") or {}).get("percentile"),
                             "robust": (c.get("robustness") or {}).get("score")})
            if not pooled or first is None:
                continue
            cutoff = pd.Timestamp(next(iter(by_sym.values()))[0].get("cutoff") or research_cutoff(settings))
            months = pd.period_range(first.tz_convert(None).to_period("M"), (cutoff - pd.Timedelta(seconds=1)).tz_convert(None).to_period("M"), freq="M")
            mr = np.array([monthly.get(str(m), 0.0) for m in months])
            sd = mr.std(ddof=1) if len(mr) > 2 else 0
            t_m = float(mr.mean() / sd * np.sqrt(len(mr))) if sd > 0 else 0.0
            pr = np.array(pooled)
            active_legs = [x for x in legs if x["trades"] >= 10]
            breadth = (sum(x["exp"] > 0 for x in active_legs) / len(active_legs)) if active_legs else 0.0
            pcts = [x["pct"] for x in legs if x["pct"] is not None]
            robs = [x["robust"] for x in legs if x["robust"] is not None]
            mc = run_mc(list(pr), 1.0, n_trades=len(pr), sims=2000) if len(pr) >= 20 else {}
            gates = [
                {"name": "เดือน OOS", "value": len(mr), "need": PORT_MIN_MONTHS, "pass": len(mr) >= PORT_MIN_MONTHS},
                {"name": f"t รายเดือนของพอร์ต (ปรับตาม {n_port} ผู้สมัคร)", "value": round(t_m, 2), "need": t_need, "pass": t_m >= t_need},
                {"name": "สินทรัพย์ที่เป็นบวก", "value": round(breadth * 100), "need": round(PORT_MIN_BREADTH * 100), "pass": breadth >= PORT_MIN_BREADTH},
                {"name": "MC PF @95% (ไม้รวม)", "value": mc.get("pf_p5"), "need": 1.0, "pass": (mc.get("pf_p5") or 0) >= 1.0},
                {"name": "ชนะการสุ่มจุดเข้า (มัธยฐาน %)", "value": float(np.median(pcts)) if pcts else None, "need": 75,
                 "pass": bool(pcts) and float(np.median(pcts)) >= 75},
                {"name": "Robustness เฉลี่ย", "value": round(float(np.mean(robs))) if robs else None, "need": 50,
                 "pass": bool(robs) and float(np.mean(robs)) >= 50},
            ]
            ok = all(g["pass"] for g in gates)
            status = "ACTIVE" if ok else ("WATCH" if t_m >= 2 and pr.mean() > 0 else "OFF")
            cands.append({"strategy": sid, "name": STRATEGIES[sid]["name"], "tf": tf, "status": status, "t_month": round(t_m, 2),
                          "months": len(mr), "trades": len(pr), "exp": round(float(pr.mean()), 4), "net_r": round(float(pr.sum()), 1),
                          "r_month": round(float(mr.mean()), 3), "breadth": round(breadth, 2), "gates": gates, "legs": legs,
                          "max_dd_r": round(float(np.max(np.maximum.accumulate(np.cumsum(mr)) - np.cumsum(mr))), 1) if len(mr) else None})
    # same quarter: portfolio systems already ACTIVE stay until the quarter ends
    if prev and (prev.get("cutoff") == (next(iter(by_sym.values()))[0].get("cutoff"))):
        was = {(x["strategy"], x["tf"]) for x in prev.get("candidates", []) if x.get("status") == "ACTIVE"}
        for c in cands:
            if (c["strategy"], c["tf"]) in was and c["status"] != "ACTIVE":
                c["status"], c["kept"] = "ACTIVE", True
    cands.sort(key=lambda c: ({"ACTIVE": 2, "WATCH": 1, "OFF": 0}[c["status"]], c["t_month"]), reverse=True)
    return {"cutoff": next(iter(by_sym.values()))[0].get("cutoff"), "n_candidates": n_port, "t_needed": t_need, "candidates": cands}


def research_cutoff(settings: dict | None = None, now: datetime | None = None) -> pd.Timestamp:
    """The research cut-off = start of the current quarter on the broker clock (1 Jan / 1 Apr / 1 Jul / 1 Oct).
    A run in the middle of a quarter (new strategies, button) uses the same cut-off, so it sees exactly the same data.
    settings.research.cutoff (ISO date) pins it for testing."""
    cfg = (settings or load_settings())["research"]
    if cfg.get("cutoff"):
        return data.period_start(pd.Timestamp(cfg["cutoff"]), cfg.get("oos_months", 3))
    return data.period_start(pd.Timestamp(now or datetime.now(timezone.utc)), cfg.get("oos_months", 3))


def _forced_off(settings: dict, sym: str, c: dict) -> bool:
    fo = settings.get("force_off", [])
    return sym in fo or f"{sym}:{c['strategy']}:{c['tf']}" in fo


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
    until = data.add_months(cutoff, cfg.get("oos_months", 3))
    # retired strategies stay in the count (user's rule): dropping losers must never lower the bar for the rest
    n_cand = sum(1 for c in cands if c["oos"]["trades"] >= 10) + n_retired_candidates(settings)
    for c in cands:
        c["gates"] = _gates(c, c["random_benchmark"], {"pf_p5": c["mc_pf_p5"]}, c["robustness"] or {}, g, n_cand)
        c["status"] = _status(c["oos"], c["gates"], bool(c["current_params"]))
        c.pop("kept", None)
    # carry-over: same quarter -> systems that were ACTIVE stay ACTIVE until the quarter ends
    same_q = bool(prev) and (prev.get("quarter") or {}).get("cutoff") == cutoff.isoformat()
    if same_q:
        was = {(x["strategy"], x["tf"]) for x in prev.get("systems", []) if x.get("source", "single") == "single"}
        for c in cands:
            if (c["strategy"], c["tf"]) in was and c["status"] != "ACTIVE" and c["current_params"]:
                c["status"], c["kept"] = "ACTIVE", True
    for c in cands:
        if c["status"] == "ACTIVE" and _forced_off(settings, sym, c):
            c["status"], c["forced_off"] = "OFF", True
    rank = {"ACTIVE": 2, "WATCH": 1, "OFF": 0}
    key = lambda c: (rank[c["status"]], c["oos"]["t_stat"] if c["oos"]["trades"] >= 10 else -99)
    cands.sort(key=key, reverse=True)
    active = [c for c in cands if c["status"] == "ACTIVE"]
    best = cands[0]
    status = best["status"]
    risk = float(settings.get("risk_per_trade_pct") or settings["default_risk_pct"])
    systems = [{"strategy": c["strategy"], "strategy_name": STRATEGIES[c["strategy"]]["name"], "tf": c["tf"],
                "params": c["current_params"], "status": "ACTIVE", "risk_pct": risk, "kept": bool(c.get("kept")), "source": "single",
                "t": c["oos"]["t_stat"], "exp": c["oos"]["expectancy"], "trades": c["oos"]["trades"],
                "robust": (c["robustness"] or {}).get("score")} for c in active]
    mc = best["mc_1y"] or {}
    part = next(p for p in parts if p["tf"] == best["tf"])
    heavy = part["heavy"].get(best["strategy"], {})
    eq = heavy.get("oos_eq", [])
    r = [x[1] for x in eq]
    first = parts[0]
    return {
        "symbol": sym, "status": status, "data_source": first["data_source"],
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
        "n_active": len(active), "n_watch": sum(c["status"] == "WATCH" for c in cands),
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


def merge(parts: list[dict], symbols: list[str] | None = None) -> dict:
    """Write latest.json (+ history snapshot) from research parts; heavy OOS data goes to data/research/oos/*.json.gz."""
    settings = load_settings()
    prev = read_json(LATEST_RESEARCH, {"symbols": {}})
    out = {"generated": None, "settings": settings["research"], "symbols": dict(prev.get("symbols", {}))}
    by_sym: dict[str, list[dict]] = {}
    for p in parts:
        by_sym.setdefault(p["symbol"], []).append(p)
        save_heavy(p)
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
                         "status": "ACTIVE", "risk_pct": risk, "kept": bool(pc.get("kept")), "source": "portfolio",
                         "t": leg["t"], "exp": leg["exp"], "trades": leg["trades"], "robust": leg["robust"]})
            if r.get("status") != "ACTIVE":
                r["status"] = "ACTIVE"
                r["risk_pct"] = risk
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
            if st in ("ACTIVE", "WATCH") or live:
                o = c.get("oos") or {}
                out.append({"symbol": sym, "tf": c["tf"], "strategy": c["strategy"], "status": st or r.get("status"), "live": live,
                            "t": o.get("t_stat"), "exp": o.get("expectancy"), "trades": o.get("trades"),
                            "robust": (c.get("robustness") or {}).get("score")})
    rank = {"ACTIVE": 0, "WATCH": 1}
    out.sort(key=lambda x: (rank.get(x["status"], 2), not x["live"], -(x["t"] or -99)))
    return out


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
                    "systems": [{"strategy": x["strategy"], "tf": x["tf"], "kept": x.get("kept", False)} for x in live_systems(r)
                                if x.get("status") == "ACTIVE"],
                    "quarter": r.get("quarter")})
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
