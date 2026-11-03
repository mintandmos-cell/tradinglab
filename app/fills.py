"""Slippage calibration from the robot's real fills (user, 3 Nov 2026).

The PC robot logs every fill (pc_status.json "fills": slip_pts = fill vs the price it asked, + = worse; delay; drift in R
vs the signal's entry). The robot keeps only its newest fills, so each cloud scan copies new ones into the permanent log
data/research/fills_log.json (by signal id). Once a symbol has MIN_PER_SYMBOL fills and the log holds MIN_TOTAL, the
75th percentile of its slippage (worse side only, price units) replaces the hand-set estimate in config.symbol_spec —
never lower than half of the estimate, so a lucky run of zero-slippage fills cannot make the tests cheaper than real.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import numpy as np

from .config import BROKER_COSTS, RESEARCH_DIR, SYMBOLS, read_json, write_json

log = logging.getLogger("tradinglab.fills")
LOG_FILE = RESEARCH_DIR / "fills_log.json"
MIN_PER_SYMBOL = 10
MIN_TOTAL = 50


def collect(pc_status: dict | None) -> int:
    """Copy the PC's newest fills into the permanent log; returns how many were new."""
    fills = (pc_status or {}).get("fills") or []
    if not fills:
        return 0
    book = read_json(LOG_FILE, {"fills": []})
    seen = {(f.get("signal"), f.get("time")) for f in book["fills"]}
    new = [f for f in fills if (f.get("signal"), f.get("time")) not in seen]
    if new:
        book["fills"] = sorted(book["fills"] + new, key=lambda f: f.get("time") or "", reverse=True)
        book["updated"] = datetime.now(timezone.utc).isoformat()
        write_json(LOG_FILE, book)
    return len(new)


def _points() -> dict:
    return {s: float(x.get("point") or 0) for s, x in read_json(BROKER_COSTS, {}).get("symbols", {}).items()}


def calibration() -> dict:
    """Per symbol: fills, median / p75 slippage in points and price, mean delay; 'use' = the slippage the tests should use
    (None while there are too few fills)."""
    book = read_json(LOG_FILE, {"fills": []})["fills"]
    pts = _points()
    out = {"total": len(book), "min_total": MIN_TOTAL, "min_per_symbol": MIN_PER_SYMBOL, "symbols": {}}
    for sym in SYMBOLS:
        f = [x for x in book if x.get("symbol") == sym and x.get("slip_pts") is not None]
        if not f:
            continue
        slip = np.array([max(0.0, float(x["slip_pts"])) for x in f])           # only the costly side counts
        pt = pts.get(sym) or 0.0
        p75 = float(np.percentile(slip, 75))
        est = float(SYMBOLS[sym].get("slippage", 0))
        ready = len(f) >= MIN_PER_SYMBOL and len(book) >= MIN_TOTAL and pt > 0
        out["symbols"][sym] = {
            "n": len(f), "median_pts": round(float(np.median(slip)), 1), "p75_pts": round(p75, 1),
            "p75_price": round(p75 * pt, 8) if pt else None, "estimate_price": est,
            "delay_s": round(float(np.median([x.get("delay_s") or 0 for x in f])), 0),
            "drift_r": round(float(np.median([x["drift_r"] for x in f if x.get("drift_r") is not None] or [0])), 3),
            "use": round(max(p75 * pt, 0.5 * est), 8) if ready else None}
    return out


_cache: dict = {"mtime": None, "data": {}}


def measured_slippage(sym: str) -> float | None:
    try:
        m = LOG_FILE.stat().st_mtime
    except OSError:
        return None
    if _cache["mtime"] != m:
        try:
            _cache.update(mtime=m, data=calibration())
        except Exception as e:  # never let a bad log stop the research
            log.warning("slippage calibration failed: %s", e)
            _cache.update(mtime=m, data={})
    return ((_cache["data"].get("symbols") or {}).get(sym) or {}).get("use")
