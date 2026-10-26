"""Research pipeline: for every symbol, walk-forward every strategy x timeframe, pick the best by
OOS t-stat, stress it (random-entry benchmark + Monte Carlo) and decide a status:

  ACTIVE  passes every gate  -> live alerts
  WATCH   positive OOS but not proven -> forward-tracked, alerts only if settings.alerts.watch
  OFF     no evidence of an edge -> no trading

Run:  python -m app.research            (all symbols)
      python -m app.research USDJPY BTCUSD
"""
from __future__ import annotations

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


def research_symbol(sym: str, settings: dict | None = None) -> dict:
    settings = settings or load_settings()
    cfg = settings["research"]
    spec = symbol_spec(sym, settings)
    h1 = data.load_h1(sym, refresh=True)
    candidates, best, best_df = [], None, None
    for tf in cfg["timeframes"]:
        df = data.get_bars(sym, tf)
        if len(df) < 500:
            continue
        for sid in STRATEGIES:
            STATE["progress"] = f"{sym} {tf} {sid}"
            wf = walk_forward(df, sid, spec, tf, cfg)
            cand = {k: wf[k] for k in ("strategy", "tf", "n_combos", "oos", "oos_years", "wfe", "plateau",
                                        "param_switches", "distinct_params", "current_params", "current_is_t")}
            cand["name"] = STRATEGIES[sid]["name"]
            # compact OOS trades (exit time, R) of every candidate: the chart page's walk-forward equity curve
            cand["oos_eq"] = [[int(t), round(float(x["r"]), 3)] for t, x in
                              zip(data.to_unix([x["exit_time"] for x in wf["oos_trades"]]), wf["oos_trades"])]
            candidates.append(cand)
            score = wf["oos"]["t_stat"] if wf["oos"]["trades"] >= 10 else -99
            if best is None or score > best["_score"]:
                best, best_df = {**wf, "_score": score}, df
    if best is None:
        return {"symbol": sym, "status": "OFF", "reason": "ข้อมูลไม่พอ", "candidates": []}

    r = [t["r"] for t in best["oos_trades"]]
    rb = random_benchmark(best_df, best, spec, best["tf"]) if len(r) >= 5 else None
    per_year = max(int(round(best["oos"]["trades_per_year"])), 10)
    mc = run_mc(r, 1.0, n_trades=per_year, dd_limit_pct=settings["target_max_dd_pct"]) if len(r) >= 5 else {}
    mc_full = run_mc(r, 1.0, n_trades=len(r), sims=3000) if len(r) >= 5 else {}
    STATE["progress"] = f"{sym} robustness"
    rob = robustness(best_df, best, spec, best["tf"], cfg)
    n_cand = sum(1 for c in candidates if c["oos"]["trades"] >= 10)
    gates = _gates(best, rb, {"pf_p5": mc_full.get("pf_p5")}, rob, cfg["gates"], n_cand)
    o = best["oos"]
    if all(g["pass"] for g in gates) and best["current_params"]:
        status = "ACTIVE"
    elif o["expectancy"] > 0 and o["t_stat"] >= 1.0 and o["trades"] >= 15 and best["current_params"]:
        status = "WATCH"
    else:
        status = "OFF"
    if sym in settings.get("force_off", []):   # manual / AI veto, e.g. forward test contradicting research
        status = "OFF"
    # size so the Monte Carlo 95th-percentile 1-year drawdown stays inside the target; unproven systems get half
    suggested = mc.get("suggested_risk_pct") or 0.0
    cap = settings["max_risk_pct"] if status == "ACTIVE" else settings["default_risk_pct"] / 2
    risk = round(min(cap, suggested), 2)
    times = data.to_unix([t["exit_time"] for t in best["oos_trades"]])
    reasons = [g["name"] for g in gates if not g["pass"]]
    return {
        "symbol": sym, "status": status, "data_source": data.source_for(sym, settings),
        "data_from": h1.index[0].isoformat(), "data_to": h1.index[-1].isoformat(), "bars_h1": int(len(h1)),
        "strategy": best["strategy"], "strategy_name": STRATEGIES[best["strategy"]]["name"], "tf": best["tf"],
        "params": best["current_params"], "risk_pct": risk if status != "OFF" else 0.0,
        "oos": o, "oos_years": best["oos_years"], "wfe": best["wfe"], "plateau": best["plateau"],
        "param_switches": best["param_switches"], "folds": best["folds"],
        "random_benchmark": rb, "mc_1y": mc, "mc_pf_p5": mc_full.get("pf_p5"), "robustness": rob, "gates": gates,
        "failed": reasons,
        "trials": int(sum(c["n_combos"] for c in candidates)), "n_candidates": n_cand,
        "t_needed": adjusted_t(n_cand), "candidates": candidates,
        "oos_r": [round(float(x), 4) for x in r],
        "oos_equity": equity_points(times, r, 1.0, 10000.0),
    }


def run_all(symbols: list[str] | None = None) -> dict:
    if not _run_lock.acquire(blocking=False):
        return {"error": "research already running"}
    try:
        STATE.update(running=True, started=datetime.now(timezone.utc).isoformat(), error=None)
        settings = load_settings()
        prev = read_json(LATEST_RESEARCH, {"symbols": {}})
        out = {"generated": None, "settings": settings["research"], "symbols": dict(prev.get("symbols", {}))}
        for sym in symbols or list(SYMBOLS):
            t0 = time.time()
            try:
                res = research_symbol(sym, settings)
                old = prev.get("symbols", {}).get(sym)
                if old:
                    res["previous"] = {k: old.get(k) for k in ("status", "strategy", "tf", "params", "risk_pct")}
                    res["previous"]["generated"] = prev.get("generated")
                out["symbols"][sym] = res
                log.info("%s -> %s %s %s (%.1fs)", sym, res["status"], res.get("strategy"), res.get("tf"), time.time() - t0)
            except Exception as e:
                log.exception("research %s failed", sym)
                out["symbols"][sym] = {"symbol": sym, "status": "OFF", "reason": f"error: {e}", "candidates": []}
        out["generated"] = datetime.now(timezone.utc).isoformat()
        write_json(LATEST_RESEARCH, out)
        write_json(RESEARCH_DIR / "history" / f"{datetime.now(timezone.utc):%Y%m%d_%H%M}.json",
                   {"generated": out["generated"], "symbols": {k: {kk: v.get(kk) for kk in (
                       "status", "strategy", "tf", "params", "risk_pct", "oos", "failed", "trials")}
                       | {"robust_score": (v.get("robustness") or {}).get("score")} for k, v in out["symbols"].items()}})
        return out
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
