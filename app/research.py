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

from . import data
from .config import LATEST_RESEARCH, RESEARCH_DIR, SYMBOLS, load_settings, read_json, symbol_spec, write_json
from .metrics import equity_points
from .montecarlo import run_mc
from .robustness import robustness
from .strategies import STRATEGIES
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
                                   "param_switches", "distinct_params", "current_params", "current_is_t")}
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
    df = data.get_bars(sym, tf)
    h1 = data.load_h1(sym)
    out = {"symbol": sym, "tf": tf, "generated": datetime.now(timezone.utc).isoformat(), "data_source": data.source_for(sym, settings),
           "data_from": h1.index[0].isoformat() if len(h1) else None, "data_to": h1.index[-1].isoformat() if len(h1) else None,
           "bars_h1": int(len(h1)), "bars": int(len(df)), "candidates": [], "heavy": {}}
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


def merge_symbol(sym: str, parts: list[dict], settings: dict) -> dict:
    """Judge every candidate with the same gates; the symbol trades its best candidate (ACTIVE first, then OOS t)."""
    cfg = settings["research"]
    g = cfg["gates"]
    cands = [c for p in parts for c in p["candidates"]]
    if not cands:
        return {"symbol": sym, "status": "OFF", "reason": "ข้อมูลไม่พอ", "candidates": []}
    n_cand = sum(1 for c in cands if c["oos"]["trades"] >= 10)
    for c in cands:
        c["gates"] = _gates(c, c["random_benchmark"], {"pf_p5": c["mc_pf_p5"]}, c["robustness"] or {}, g, n_cand)
        c["status"] = _status(c["oos"], c["gates"], bool(c["current_params"]))
    rank = {"ACTIVE": 2, "WATCH": 1, "OFF": 0}
    best = max(cands, key=lambda c: (rank[c["status"]], c["oos"]["t_stat"] if c["oos"]["trades"] >= 10 else -99))
    status = best["status"] if sym not in settings.get("force_off", []) else "OFF"   # manual / AI veto
    mc = best["mc_1y"] or {}
    suggested = mc.get("suggested_risk_pct") or 0.0
    cap = settings["max_risk_pct"] if status == "ACTIVE" else settings["default_risk_pct"] / 2
    risk = round(min(cap, suggested), 2)
    part = next(p for p in parts if p["tf"] == best["tf"])
    heavy = part["heavy"].get(best["strategy"], {})
    eq = heavy.get("oos_eq", [])
    r = [x[1] for x in eq]
    first = parts[0]
    return {
        "symbol": sym, "status": status, "data_source": first["data_source"],
        "data_from": first["data_from"], "data_to": first["data_to"], "bars_h1": first["bars_h1"],
        "strategy": best["strategy"], "strategy_name": STRATEGIES[best["strategy"]]["name"], "tf": best["tf"],
        "params": best["current_params"], "risk_pct": risk if status != "OFF" else 0.0,
        "oos": best["oos"], "oos_years": best["oos_years"], "wfe": best["wfe"], "plateau": best["plateau"],
        "param_switches": best["param_switches"], "folds": heavy.get("folds", []),
        "random_benchmark": best["random_benchmark"], "mc_1y": mc, "mc_pf_p5": best["mc_pf_p5"], "robustness": best["robustness"],
        "gates": best["gates"], "failed": [x["name"] for x in best["gates"] if not x["pass"]],
        "trials": int(sum(c["n_combos"] for c in cands)), "n_candidates": n_cand, "t_needed": adjusted_t(n_cand),
        "n_active": sum(c["status"] == "ACTIVE" for c in cands), "n_watch": sum(c["status"] == "WATCH" for c in cands),
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
            res = merge_symbol(sym, by_sym[sym], settings)
        except Exception as e:
            log.exception("merge %s failed", sym)
            res = {"symbol": sym, "status": "OFF", "reason": f"error: {e}", "candidates": []}
        old = prev.get("symbols", {}).get(sym)
        if old:
            res["previous"] = {k: old.get(k) for k in ("status", "strategy", "tf", "params", "risk_pct")} | {"generated": prev.get("generated")}
        out["symbols"][sym] = res
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


def picks(lat: dict | None = None) -> list[dict]:
    """Quick-pick list for the right rail: every ACTIVE / WATCH candidate of every symbol (plus each symbol's
    current system), most important first: ACTIVE before WATCH, then the higher OOS t-stat."""
    lat = lat or latest()
    out = []
    for sym, r in (lat.get("symbols") or {}).items():
        for c in r.get("candidates", []):
            st = c.get("status")
            live = c.get("strategy") == r.get("strategy") and c.get("tf") == r.get("tf")
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
                    "robust_score": (r.get("robustness") or {}).get("score")})
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
