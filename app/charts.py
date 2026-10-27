"""Chart payloads (candles + system lines + trades + open position), shared by the API and the static export."""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pandas as pd

from . import data, research
from .config import symbol_spec
from .engine import run
from .metrics import summarize
from .strategies import STRATEGIES, normalize


def clean(o):
    """Make numpy / pandas values JSON-safe (NaN and inf become null)."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        v = float(o)
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(o, np.ndarray):
        return clean(o.tolist())
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    return o


def _line(times, arr):
    return [{"time": int(t), "value": float(v)} for t, v in zip(times, arr) if v is not None and np.isfinite(v)]


def trade_out(t: dict, digits: int) -> dict:
    return {"entry_time": int(pd.Timestamp(t["entry_time"]).timestamp()), "exit_time": int(pd.Timestamp(t["exit_time"]).timestamp()),
            "dir": t["dir"], "entry": round(t["entry"], digits), "sl": round(t["sl"], digits), "tp": round(t["tp"], digits) if t["tp"] is not None else None,
            "exit": round(t["exit"], digits), "reason": t["reason"], "r": round(t["r"], 3), "bars": t["bars"]}


def _bars(times, df: pd.DataFrame, d: int) -> list[dict]:
    r = d + 1
    return [{"time": int(t), "open": round(float(o), r), "high": round(float(h), r), "low": round(float(l), r), "close": round(float(c), r)}
            for t, o, h, l, c in zip(times, df.open.values, df.high.values, df.low.values, df.close.values)]


def candles_year(symbol: str, tf: str, year: int, before: int | None = None) -> dict:
    """Older candles of one calendar year (chart page: loaded when the user scrolls back). before = unix time cut-off."""
    df = data.get_bars(symbol, tf, closed_only=False)
    df = df[df.index.year == int(year)]
    times = data.to_unix(df.index)
    if before is not None:
        keep = times < int(before)
        df, times = df[keep], times[keep]
    return {"symbol": symbol, "tf": tf, "year": int(year), "candles": _bars(times, df, symbol_spec(symbol)["digits"])}


def candles(symbol: str, tf: str, limit: int = 1500, cfg: dict | None = None, system: bool = True) -> dict:
    """cfg = {"strategy", "params"} to overlay a custom system; else the researched system when its TF matches."""
    df = data.get_bars(symbol, tf, closed_only=False)
    spec = symbol_spec(symbol)
    d = spec["digits"]
    times = data.to_unix(df.index)
    first = int(times[-limit]) if len(times) > limit else (int(times[0]) if len(times) else None)
    older = sorted({y for y in df.index[:-limit].year}) if len(times) > limit else []
    out = {"symbol": symbol, "tf": tf, "digits": d, "source": data.source_for(symbol),
           "first_time": first, "older_years": older,           # the chart page loads these on demand when scrolled left
           "history_from": df.index[0].isoformat() if len(df) else None,
           "candles": _bars(times[-limit:], df.iloc[-limit:], d)}
    if cfg is None and system:
        r = research.latest().get("symbols", {}).get(symbol)
        if r and r.get("params") and r.get("tf") == tf:
            cfg = {"strategy": r["strategy"], "params": r["params"]}
    if cfg:
        closed = data.drop_unclosed(df, tf)
        res = run(closed, cfg["strategy"], cfg["params"], spec, tf, with_lines=True)
        t_closed = data.to_unix(closed.index)
        cut = t_closed[-limit] if len(t_closed) > limit else t_closed[0]
        out["system"] = {"strategy": cfg["strategy"], "name": STRATEGIES[cfg["strategy"]]["name"], "params": cfg["params"]}
        out["lines"] = {k: _line(t_closed[-limit:], v[-limit:]) for k, v in res["lines"].items()}
        out["trades"] = [trade_out(t, d) for t in res["trades"] if pd.Timestamp(t["entry_time"]).timestamp() >= cut]
        out["last_signal"] = res["last_signal"]
        ot = res["open_trade"]
        if ot:  # the system's position that is still running: drawn from entry to the latest candle
            out["open_trade"] = {"entry_time": int(pd.Timestamp(ot["entry_time"]).timestamp()), "dir": ot["dir"],
                                 "entry": round(ot["entry"], d), "sl": round(ot["sl"], d), "tp": round(ot["tp"], d) if ot["tp"] is not None else None,
                                 "close_by": int(pd.Timestamp(ot["close_by"]).timestamp()) if ot.get("close_by") is not None else None,
                                 "reverse_pending": bool(ot.get("reverse_pending"))}
    return out


REASON = {"SL": 0, "TP": 1, "Time": 2}


def system_params(symbol: str, tf: str, strategy: str, lat: dict | None = None) -> tuple[dict, str, dict | None]:
    """Which parameters to show for a strategy on the chart page, and where they come from:
    live  = the researched system itself (what the alerts trade)
    wf    = the latest walk-forward choice for that strategy x TF (a research candidate, not traded)
    default = the library defaults (no research for that strategy x TF, e.g. D1)."""
    r = (lat if lat is not None else research.latest()).get("symbols", {}).get(symbol) or {}
    ls = next((x for x in research.live_systems(r) if x["strategy"] == strategy and x["tf"] == tf and x.get("params")), None)
    if ls:
        cand = next((c for c in r.get("candidates", []) if c["strategy"] == strategy and c["tf"] == tf), None)
        return normalize(strategy, ls["params"]), "live", cand
    cand = next((c for c in r.get("candidates", []) if c["strategy"] == strategy and c["tf"] == tf), None)
    if cand and cand.get("current_params"):
        return normalize(strategy, cand["current_params"]), "wf", cand
    return normalize(strategy, None), "default", cand


def system_payload(symbol: str, tf: str, strategy: str, params: dict | None = None, limit: int = 1500,
                   lat: dict | None = None) -> dict:
    """One strategy on one chart: indicator lines for the visible candles, every trade of the full history
    (equity curve), the detailed recent trades (markers / table), the position still running, and the
    walk-forward OOS trades from the research when that strategy x TF was a candidate.
    Viewing never changes the live system — the scanner only reads research/latest.json."""
    spec = symbol_spec(symbol)
    d = spec["digits"]
    if params is None:
        params, src, cand = system_params(symbol, tf, strategy, lat)
    else:
        params, src, cand = normalize(strategy, params), "custom", None
    live_sys = (lat if lat is not None else research.latest()).get("symbols", {}).get(symbol) or {}
    closed = data.drop_unclosed(data.get_bars(symbol, tf, closed_only=False), tf)
    res = run(closed, strategy, params, spec, tf, with_lines=True)
    t = data.to_unix(closed.index)
    tr = res["trades"]
    r = [x["r"] for x in tr]
    yrs = data.years_of(closed)
    m = summarize(r, yrs, 1.0, bars=[x["bars"] for x in tr], cost_r=[x["cost_r"] for x in tr])
    m["long"] = summarize([x["r"] for x in tr if x["dir"] > 0], yrs)["expectancy"]
    m["short"] = summarize([x["r"] for x in tr if x["dir"] < 0], yrs)["expectancy"]
    eq = [[int(pd.Timestamp(x["exit_time"]).timestamp()), round(float(x["r"]), 3)] for x in tr]
    out = {
        "symbol": symbol, "tf": tf, "strategy": strategy, "name": STRATEGIES[strategy]["name"],
        "family": STRATEGIES[strategy]["family"], "params": params, "params_source": src,
        "is_live": src == "live", "live": {k: live_sys.get(k) for k in ("status", "strategy", "tf", "risk_pct")},
        "digits": d, "from": closed.index[0].isoformat() if len(closed) else None, "years": round(yrs, 2),
        "t": [int(x) for x in t[-limit:]],
        "lines": {k: [None if not np.isfinite(v) else round(float(v), d + 1) for v in arr[-limit:]] for k, arr in res["lines"].items()},
        "metrics": m, "equity": eq,
        "trades": [trade_out(x, d) for x in tr[-1500:]],          # whole history (markers when scrolling back)
        "last_signal": res["last_signal"], "open_trade": None, "oos": None,
    }
    ot = res["open_trade"]
    if ot:
        out["open_trade"] = {"entry_time": int(pd.Timestamp(ot["entry_time"]).timestamp()), "dir": ot["dir"],
                             "entry": round(ot["entry"], d), "sl": round(ot["sl"], d), "tp": round(ot["tp"], d) if ot["tp"] is not None else None,
                             "close_by": int(pd.Timestamp(ot["close_by"]).timestamp()) if ot.get("close_by") is not None else None,
                                 "reverse_pending": bool(ot.get("reverse_pending"))}
    oos_eq = (research.heavy(symbol, tf).get(strategy) or {}).get("oos_eq") if cand else None
    if cand and oos_eq:
        out["oos"] = {"equity": oos_eq, "metrics": cand["oos"], "years": cand.get("oos_years"),
                      "wfe": cand.get("wfe"), "plateau": cand.get("plateau")}
    return out
