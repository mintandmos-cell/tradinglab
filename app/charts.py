"""Chart payloads (candles + system lines + trades + open position), shared by the API and the static export."""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pandas as pd

from . import data, research
from .config import symbol_spec
from .engine import run
from .strategies import STRATEGIES


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
            "dir": t["dir"], "entry": round(t["entry"], digits), "sl": round(t["sl"], digits), "tp": round(t["tp"], digits),
            "exit": round(t["exit"], digits), "reason": t["reason"], "r": round(t["r"], 3), "bars": t["bars"]}


def candles(symbol: str, tf: str, limit: int = 1500, cfg: dict | None = None, system: bool = True) -> dict:
    """cfg = {"strategy", "params"} to overlay a custom system; else the researched system when its TF matches."""
    df = data.get_bars(symbol, tf, closed_only=False)
    spec = symbol_spec(symbol)
    d = spec["digits"]
    times = data.to_unix(df.index)
    out = {"symbol": symbol, "tf": tf, "digits": d, "source": data.source_for(symbol),
           "candles": [{"time": int(t), "open": float(o), "high": float(h), "low": float(l), "close": float(c)}
                       for t, o, h, l, c in zip(times[-limit:], df.open.values[-limit:], df.high.values[-limit:],
                                                df.low.values[-limit:], df.close.values[-limit:])]}
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
                                 "entry": round(ot["entry"], d), "sl": round(ot["sl"], d), "tp": round(ot["tp"], d),
                                 "close_by": int(pd.Timestamp(ot["close_by"]).timestamp())}
    return out
