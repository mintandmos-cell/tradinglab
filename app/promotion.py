"""Forward promotion of TRIAL systems (user, 9 Nov 2026 — rule fixed BEFORE any of its results were seen).

TRIAL systems (WATCH candidates that failed only the luck gate) trade on the DEMO account at 0.1 % risk. Their forward
trades are NEW data that played no part in picking them, and the field is small (the trials ever registered, ~30 instead
of ~30,000 research tests), so the luck correction is far smaller. A TRIAL becomes ACTIVE (normal risk, push alerts,
traded like any ACTIVE system) when its forward record — same symbol, strategy, TF and the SAME parameters — has:

  * at least `min_trades` (30) closed trades, spread over at least `min_months` (3) months,
  * expectancy >= `min_exp_r` (+0.05 R per trade, after costs: forward R comes from the alerted levels),
  * t-stat of the trade R >= the Sidak threshold for every trial ever registered (one-sided, alpha 5 %),
  * not stopped by the kill switch, not in force_off.

Once promoted (an ACTIVE signal of it exists) it stays ACTIVE while t >= threshold - 1 and expectancy > 0 (hysteresis,
so it does not flip on every trade); the kill switch (2 x the worst OOS drawdown) still applies. The yearly research
judges every system again from scratch with the new data.
Stateless: everything is recomputed from signals.json + the trial registry, so the PC and the cloud always agree.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .config import RESEARCH_DIR, read_json, write_json

REGISTRY = RESEARCH_DIR / "trial_registry.json"
# since = the library lock date (library v2, user 11 Nov 2026): the forward field counts only trials that trade from that
# day on — forward trades are new data, so systems that stopped trading before it took no test on it
DEFAULTS = {"enabled": True, "min_trades": 30, "min_months": 3, "min_exp_r": 0.05, "alpha": 0.05, "since": "2026-11-11"}


def cfg_of(settings: dict) -> dict:
    return {**DEFAULTS, **(settings.get("promotion") or {})}


def register(latest_symbols: dict) -> dict:
    """Research merge: every trial ever listed is recorded (never removed) — it is the size of the forward field."""
    reg = read_json(REGISTRY, {"trials": {}})
    today = datetime.now(timezone.utc).date().isoformat()
    for sym, r in latest_symbols.items():
        for t in r.get("trial_systems") or []:
            reg["trials"].setdefault(f"{sym}:{t['strategy']}:{t['tf']}", {"first": today})
    for sym, r in latest_symbols.items():           # still a trial at this merge: seen trading on/after today
        for t in r.get("trial_systems") or []:
            reg["trials"][f"{sym}:{t['strategy']}:{t['tf']}"]["last"] = today
    reg["updated"] = today
    write_json(REGISTRY, reg)
    return reg


def field_size(latest_symbols: dict, sigs: list[dict] | None = None, since: str | None = None) -> int:
    reg = read_json(REGISTRY, {"trials": {}}).get("trials", {})
    keys = {k for k, v in reg.items() if not since or str(v.get("last") or v.get("first") or "") >= since}
    keys |= {f"{x['symbol']}:{x['strategy']}:{x['tf']}" for x in sigs or [] if x.get("source") in ("trial", "forward")
             and (not since or str(x.get("created") or "")[:10] >= since)}
    keys |= {f"{s}:{t['strategy']}:{t['tf']}" for s, r in latest_symbols.items() for t in r.get("trial_systems") or []}
    return max(len(keys), 1)


def t_needed(n: int, alpha: float = 0.05) -> float:
    from .research import adjusted_t
    return adjusted_t(n, alpha)


def record(sigs: list[dict], sym: str, cfg: dict) -> dict:
    """Forward record of one trial with its current parameters (closed trades only)."""
    rs, times, was_active = [], [], False
    for x in sigs:
        if x.get("symbol") != sym or x.get("strategy") != cfg["strategy"] or x.get("tf") != cfg["tf"]:
            continue
        if x.get("source") not in ("trial", "forward") or x.get("params") != cfg.get("params"):
            continue
        if x.get("source") == "forward":
            was_active = True
        if x.get("outcome") in (None, "open") or x.get("outcome_r") is None:
            continue
        rs.append(float(x["outcome_r"]))
        times.append(pd.Timestamp(x.get("exit_time") or x["bar_time"]))
    n = len(rs)
    r = np.array(rs, float)
    sd = float(r.std(ddof=1)) if n > 2 else 0.0
    t = float(r.mean() / sd * math.sqrt(n)) if sd > 1e-9 else 0.0
    months = (max(times) - min(times)).days / 30.44 if times else 0.0
    return {"n": n, "exp": round(float(r.mean()), 4) if n else None, "t": round(t, 2), "months": round(months, 1),
            "net_r": round(float(r.sum()), 2), "was_active": was_active}


def evaluate(sigs: list[dict], latest_symbols: dict, settings: dict, stopped: set | None = None) -> dict:
    """{key: {..record.., t_need, promoted, why}} for every current trial."""
    c = cfg_of(settings)
    n_field = field_size(latest_symbols, sigs, c.get("since"))
    need = t_needed(n_field, float(c["alpha"]))
    out = {}
    fo = set(settings.get("force_off") or [])
    for sym, r in latest_symbols.items():
        for cfg in r.get("trial_systems") or []:
            key = f"{sym}:{cfg['strategy']}:{cfg['tf']}"
            rec = record(sigs, sym, cfg)
            blocked = key in (stopped or set()) or key in fo or sym in fo
            if rec["was_active"]:      # already promoted: stay while the record holds up (hysteresis)
                ok = rec["t"] >= need - 1 and (rec["exp"] or 0) > 0
            else:
                ok = (rec["n"] >= c["min_trades"] and rec["months"] >= c["min_months"]
                      and (rec["exp"] or 0) >= c["min_exp_r"] and rec["t"] >= need)
            promoted = bool(c["enabled"]) and ok and not blocked
            out[key] = {**rec, "t_need": need, "field": n_field, "min_trades": c["min_trades"], "min_months": c["min_months"],
                        "min_exp_r": c["min_exp_r"], "promoted": promoted, "blocked": blocked}
    return out


def promote(cfg: dict, settings: dict) -> dict:
    """The trial config traded as an ACTIVE system (normal risk, push alerts)."""
    return {**cfg, "status": "ACTIVE", "source": "forward",
            "risk_pct": float(settings.get("risk_per_trade_pct") or settings.get("default_risk_pct", 0.5))}
