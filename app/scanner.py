"""Live signal scanner + forward tracking, plus the research scheduler loop."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import data, notify, research
from .config import SIGNALS_FILE, TIMEFRAMES, load_settings, read_json, symbol_spec, write_json
from .engine import cost_price, hold_bars, run, simulate
from . import indicators as ta
from .sizing import lots_for

log = logging.getLogger("tradinglab.scanner")
BKK = timezone(timedelta(hours=7))
STATE = {"last_scan": None, "last_error": None, "scans": 0}
_lock = threading.RLock()


def load_signals() -> list[dict]:
    return read_json(SIGNALS_FILE, [])


def save_signals(sigs: list[dict]) -> None:
    write_json(SIGNALS_FILE, sigs[:600])


def update_signal(sig_id: str, patch: dict) -> dict | None:
    with _lock:
        sigs = load_signals()
        for s in sigs:
            if s["id"] == sig_id:
                s.update({k: v for k, v in patch.items() if k in ("user_status", "note", "fill_price", "user_result_r")})
                s["updated"] = datetime.now(timezone.utc).isoformat()
                save_signals(sigs)
                return s
    return None


def _fmt(x: float, digits: int) -> str:
    return f"{x:.{digits}f}"


def _make_signal(sym: str, cfg: dict, df: pd.DataFrame, res: dict, settings: dict) -> dict:
    spec = symbol_spec(sym, settings)
    p = cfg["params"]
    d = int(res["last_signal"])
    entry = float(df.close.iloc[-1])
    sl_dist = float(p["sl_atr"]) * res["atr_last"]
    sl, tp = entry - d * sl_dist, entry + d * float(p["rr"]) * sl_dist
    info = data.mt5_symbol_info(sym)
    risk_pct = float(cfg.get("risk_pct") or 0) or settings["default_risk_pct"] / 2
    size = lots_for(sym, entry, sl, float(settings["account_balance"]), risk_pct, info, settings)
    bar_t = df.index[-1]
    tfm = TIMEFRAMES[cfg["tf"]]
    return {
        "id": f"{sym}-{cfg['tf']}-{cfg['strategy']}-{int(bar_t.timestamp())}",
        "created": datetime.now(timezone.utc).isoformat(),
        "bar_time": bar_t.isoformat(),
        "valid_until": (bar_t + pd.Timedelta(minutes=2 * tfm)).isoformat(),
        "symbol": sym, "mt5_symbol": settings["mt5_symbol_map"].get(sym, sym), "tf": cfg["tf"],
        "strategy": cfg["strategy"], "strategy_name": cfg.get("strategy_name"), "params": p,
        "system_status": cfg["status"], "side": "LONG" if d > 0 else "SHORT", "dir": d,
        "entry": round(entry, spec["digits"]), "sl": round(sl, spec["digits"]), "tp": round(tp, spec["digits"]),
        "sl_dist": round(sl_dist, spec["digits"]), "tp_dist": round(sl_dist * float(p["rr"]), spec["digits"]),
        "sl_pips": round(sl_dist / spec["pip"], 1), "tp_pips": round(sl_dist * float(p["rr"]) / spec["pip"], 1),
        "rr": float(p["rr"]), "risk_pct": risk_pct, "lots": size["lots"], "risk_usd": size["actual_risk_usd"],
        "reward_usd": round(size["actual_risk_usd"] * float(p["rr"]), 2), "below_min_lot": size["below_min_lot"],
        "max_hold_h": p["max_hold_h"], "close_by": (bar_t + pd.Timedelta(minutes=tfm) + pd.Timedelta(hours=p["max_hold_h"])).isoformat(),
        "price_source": data.source_for(sym, settings), "cost_r": round(cost_price(spec) / sl_dist, 3),
        "outcome": "open", "outcome_r": None, "exit_time": None, "user_status": "new",
    }


def _track(sig: dict, df: pd.DataFrame, spec: dict) -> None:
    """Forward-test the signal with exactly the backtest rules."""
    bt = pd.Timestamp(sig["bar_time"])
    i = df.index.searchsorted(bt)
    if i >= len(df) or df.index[i] != bt or i + 1 >= len(df):
        return
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = ta.atr(h, l, c, 14)
    p = sig["params"]
    res = simulate(o, h, l, c, i + 1, sig["dir"], float(p["sl_atr"]) * a[i], float(p["rr"]), hold_bars(p, sig["tf"]), cost_price(spec))
    if res:
        k, px, reason, entry, *_ = res
        sig.update(outcome={"SL": "loss", "TP": "win", "Time": "time"}[reason],
                   outcome_r=round(sig["dir"] * (px - entry) / (float(p["sl_atr"]) * a[i]), 3),
                   exit_time=df.index[k].isoformat(), exit_price=round(float(px), spec["digits"]))


def scan_once() -> list[dict]:
    settings = load_settings()
    latest = research.latest().get("symbols", {})
    allow = {"ACTIVE"} | ({"WATCH"} if settings["alerts"].get("watch") else set())
    track = {"ACTIVE", "WATCH"}
    new = []
    with _lock:
        sigs = load_signals()
        known = {s["id"] for s in sigs}
        frames: dict[tuple, pd.DataFrame] = {}
        for sym, cfg in latest.items():
            if cfg.get("status") not in track or not cfg.get("params"):
                continue
            try:
                df = data.get_bars(sym, cfg["tf"])
                frames[(sym, cfg["tf"])] = df
                res = run(df, cfg["strategy"], cfg["params"], symbol_spec(sym, settings), cfg["tf"])
                n = len(df)
                flat = res["open_trade"] is None and (not res["trades"] or res["trades"][-1]["exit_i"] < n - 1)
                if res["last_signal"] and flat and np.isfinite(res["atr_last"]):
                    s = _make_signal(sym, cfg, df, res, settings)
                    if s["id"] not in known:
                        sigs.insert(0, s)
                        known.add(s["id"])
                        new.append(s)
            except Exception as e:
                log.exception("scan %s failed", sym)
                STATE["last_error"] = f"{sym}: {e}"
        for s in sigs:
            if s.get("outcome") == "open":
                df = frames.get((s["symbol"], s["tf"]))
                if df is None:
                    try:
                        df = frames[(s["symbol"], s["tf"])] = data.get_bars(s["symbol"], s["tf"])
                    except Exception:
                        continue
                _track(s, df, symbol_spec(s["symbol"], settings))
        save_signals(sigs)
    for s in new:
        if s["system_status"] not in allow:
            continue
        spec = symbol_spec(s["symbol"], settings)
        d = spec["digits"]
        title = f"{'🟢 BUY' if s['dir'] > 0 else '🔴 SELL'} {s['mt5_symbol']} {s['tf']} — {s['lots']} lot"
        msg = (f"Entry ~{_fmt(s['entry'], d)} | SL {_fmt(s['sl'], d)} ({s['sl_pips']} pips) | TP {_fmt(s['tp'], d)} "
               f"| R:R 1:{s['rr']} | เสี่ยง ${s['risk_usd']} ({s['risk_pct']}%)\n{s['strategy_name']} · ระบบ {s['system_status']}")
        notify.send(title, msg, priority=5 if s["system_status"] == "ACTIVE" else 3,
                    tags=["green_circle" if s["dir"] > 0 else "red_circle"])
    STATE.update(last_scan=datetime.now(timezone.utc).isoformat(), scans=STATE["scans"] + 1)
    return new


def forward_summary() -> dict:
    from .metrics import summarize
    all_sigs = load_signals()
    done = [s for s in all_sigs if s.get("outcome") in ("win", "loss", "time")]
    by: dict[str, list[float]] = {}
    for s in done:
        by.setdefault(f"{s['symbol']} {s['tf']} {s['strategy']}", []).append(s["outcome_r"] or 0.0)
    allr = [s["outcome_r"] or 0.0 for s in done]
    taken = [s.get("user_result_r") for s in all_sigs if s.get("user_status") == "taken" and s.get("user_result_r") is not None]
    return {"all": summarize(allr, 1.0) if allr else None, "taken": summarize(taken, 1.0) if taken else None,
            "systems": [{"system": k, **summarize(v, 1.0)} for k, v in by.items()]}


def research_due(settings: dict, now: datetime | None = None) -> bool:
    cfg = settings["research"]
    sched = cfg.get("schedule", "weekly")
    if sched == "off":
        return False
    now = (now or datetime.now(timezone.utc)).astimezone(BKK)
    gen = research.latest().get("generated")
    if not gen:
        return True
    last = datetime.fromisoformat(gen).astimezone(BKK)
    if now.hour < int(cfg.get("schedule_hour_bkk", 10)):
        return False
    if sched == "daily":
        return last.date() < now.date()
    if sched == "monthly":
        return (last.year, last.month) < (now.year, now.month)
    # weekly: Saturday (FX closed) or later in the week if missed
    week_start = (now - timedelta(days=(now.weekday() - 5) % 7)).date()
    return now.date() >= week_start and last.date() < week_start


def loop(stop: threading.Event) -> None:
    while not stop.is_set():
        settings = load_settings()
        try:
            if research_due(settings) and not research.STATE["running"]:
                log.info("scheduled research starting")
                research.run_in_background()
            scan_once()
        except Exception as e:
            STATE["last_error"] = repr(e)
            log.exception("scan loop error")
        stop.wait(max(30, int(settings.get("scan_interval_sec", 120))))
