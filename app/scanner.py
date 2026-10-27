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
from .engine import cost_price, exit_rules, friday_cutoff, hold_bars, run, simulate, week_guard, weekend_blocked
from .strategies import STRATEGIES, signals as strat_signals
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
    rr = float(p["rr"])
    sl, tp = entry - d * sl_dist, (entry + d * rr * sl_dist if rr > 0 else None)      # rr 0 = no take-profit
    info = data.mt5_symbol_info(sym)
    risk_pct = (float(settings.get("risk_per_trade_pct") or 0) or float(cfg.get("risk_pct") or 0)
                or settings["default_risk_pct"] / 2)
    balance = float(settings["account_balance"])
    size = lots_for(sym, entry, sl, balance, risk_pct, info, settings)
    min_lot_risk = round(0.01 * sl_dist * size.get("value_per_point_per_lot", 0), 2)  # what the broker's minimum lot would risk
    bar_t = df.index[-1]
    tfm = TIMEFRAMES[cfg["tf"]]
    entry_t = bar_t + pd.Timedelta(minutes=tfm)
    rules = exit_rules()
    close_by = entry_t + pd.Timedelta(hours=p["max_hold_h"]) if rules["max_hold"] else None
    if rules["friday"]:
        close_by = min(close_by, friday_cutoff(entry_t)) if close_by is not None else friday_cutoff(entry_t)
    rev = (res.get("open_trade") or {}).get("reverse_pending")
    return {
        "id": f"{sym}-{cfg['tf']}-{cfg['strategy']}-{int(bar_t.timestamp())}",
        "created": datetime.now(timezone.utc).isoformat(),
        "bar_time": bar_t.isoformat(),
        "valid_until": (bar_t + pd.Timedelta(minutes=2 * tfm)).isoformat(),
        "symbol": sym, "mt5_symbol": settings["mt5_symbol_map"].get(sym, sym), "tf": cfg["tf"],
        "strategy": cfg["strategy"], "strategy_name": cfg.get("strategy_name"), "params": p,
        "system_status": cfg["status"], "side": "LONG" if d > 0 else "SHORT", "dir": d,
        "entry": round(entry, spec["digits"]), "sl": round(sl, spec["digits"]), "tp": round(tp, spec["digits"]) if tp is not None else None,
        "sl_dist": round(sl_dist, spec["digits"]), "tp_dist": round(sl_dist * rr, spec["digits"]) if tp is not None else None,
        "sl_pips": round(sl_dist / spec["pip"], 1), "tp_pips": round(sl_dist * rr / spec["pip"], 1) if tp is not None else None,
        "rr": rr, "risk_pct": risk_pct, "lots": size["lots"], "risk_usd": size["actual_risk_usd"],
        "reward_usd": round(size["actual_risk_usd"] * rr, 2) if tp is not None else None, "below_min_lot": size["below_min_lot"],
        "raw_lots": size.get("raw_lots"), "balance": balance, "min_lot_risk_usd": min_lot_risk,
        "min_lot_risk_pct": round(100 * min_lot_risk / balance, 2) if balance else None,
        "max_hold_h": p["max_hold_h"], "close_by": close_by.isoformat() if close_by is not None else None,
        "reverse_of": ("SHORT" if d > 0 else "LONG") if rev else None,
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
    rules = exit_rules()
    wlast = week_guard(df.index, sig["tf"])[0] if rules["friday"] else None
    opp = strat_signals(sig["strategy"], df, p)[0] if rules["opposite"] else None
    res = simulate(o, h, l, c, i + 1, sig["dir"], float(p["sl_atr"]) * a[i], float(p["rr"]), hold_bars(p, sig["tf"], rules), cost_price(spec),
                   int(wlast[i + 1]) if wlast is not None else None, opp)
    if res:
        k, px, reason, entry, *_ = res
        r = round(sig["dir"] * (px - entry) / (float(p["sl_atr"]) * a[i]), 3)
        sig.update(outcome={"SL": "loss", "TP": "win"}.get(reason) or ("win" if r > 0 else "loss" if r < 0 else "time"), exit_reason=reason,
                   outcome_r=r,
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
        cap = float(settings.get("max_symbol_risk_pct", 0.75))
        for sym, entry in latest.items():
            # every ACTIVE system of the quarter is traded (several per symbol / TF allowed)
            for cfg in research.live_systems(entry):
                if cfg.get("status") not in track or not cfg.get("params"):
                    continue
                try:
                    key = (sym, cfg["tf"])
                    df = frames.get(key)
                    if df is None:
                        df = frames[key] = data.get_bars(sym, cfg["tf"])
                    res = run(df, cfg["strategy"], cfg["params"], symbol_spec(sym, settings), cfg["tf"])
                    n = len(df)
                    flat = res["open_trade"] is None and (not res["trades"] or res["trades"][-1]["exit_i"] < n - 1)
                    entry_t = df.index[-1] + pd.Timedelta(minutes=TIMEFRAMES[cfg["tf"]])
                    weekend = exit_rules()["friday"] and (weekend_blocked(entry_t) or
                                                          friday_cutoff(entry_t) < entry_t + pd.Timedelta(minutes=TIMEFRAMES[cfg["tf"]]))
                    ot = res["open_trade"]
                    reverse = bool(ot and ot.get("reverse_pending") and res["last_signal"] == -ot["dir"])
                    if res["last_signal"] and (flat or reverse) and np.isfinite(res["atr_last"]) and not weekend:
                        s = _make_signal(sym, cfg, df, res, settings)
                        if s["id"] in known:
                            continue
                        # total risk cap per symbol (user's rule): open ACTIVE alerts of this symbol, other systems'
                        # positions + this one; a reversal replaces this system's own open order
                        same_sys = lambda x: x["strategy"] == cfg["strategy"] and x["tf"] == cfg["tf"]
                        open_risk = sum(float(x.get("risk_pct") or 0) for x in sigs
                                        if x["symbol"] == sym and x.get("outcome") == "open" and not x.get("capped")
                                        and x.get("system_status") == "ACTIVE" and not same_sys(x))
                        if s["system_status"] == "ACTIVE" and open_risk + float(s["risk_pct"]) > cap + 1e-9:
                            s.update(capped=True, user_status="skipped",
                                     note=f"ข้าม: ความเสี่ยงรวม {sym} จะเป็น {open_risk + float(s['risk_pct']):.2f}% เกินเพดาน {cap}%")
                        sigs.insert(0, s)
                        known.add(s["id"])
                        new.append(s)
                except Exception as e:
                    log.exception("scan %s %s failed", sym, cfg.get("strategy"))
                    STATE["last_error"] = f"{sym} {cfg.get('strategy')}: {e}"
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
        if s["system_status"] not in allow or s.get("capped"):
            continue
        title, msg = alert_text(s, symbol_spec(s["symbol"], settings)["digits"])
        notify.send(title, msg, priority=5 if s["system_status"] == "ACTIVE" else 3,
                    tags=["green_circle" if s["dir"] > 0 else "red_circle"])
    STATE.update(last_scan=datetime.now(timezone.utc).isoformat(), scans=STATE["scans"] + 1)
    return new


def alert_text(s: dict, d: int) -> tuple[str, str]:
    """Push title + body: lot first, then the levels to type into MT5."""
    side = "🟢 BUY" if s["dir"] > 0 else "🔴 SELL"
    no = STRATEGIES[s["strategy"]]["no"] if s["strategy"] in STRATEGIES else s["strategy"]
    tag = f"TL{no}-{s['tf']}"

    if s["below_min_lot"]:
        title = f"{side} {s['mt5_symbol']} {s['tf']} #{no} — lot ต่ำกว่า 0.01"
        lot_line = (f"⚠️ Lot ที่คำนวณได้ {s['raw_lots']} ต่ำกว่าขั้นต่ำ 0.01 · ถ้าเปิด 0.01 lot จะเสี่ยง "
                    f"${s['min_lot_risk_usd']} ({s['min_lot_risk_pct']}% ของทุน) มากกว่าที่ตั้งไว้ {s['risk_pct']}%")
    else:
        title = f"{side} {s['mt5_symbol']} {s['tf']} #{no} — เปิด {s['lots']:.2f} lot"
        lot_line = f"📦 LOT {s['lots']:.2f} · เสี่ยง ${s['risk_usd']} ({s['risk_pct']}% ของทุน ${s['balance']:,.0f})"
    reward = f"${s['reward_usd']}" if not s["below_min_lot"] else f"${round(s['min_lot_risk_usd'] * s['rr'], 2)} (ที่ 0.01 lot)"
    tp_line = (f"TP {_fmt(s['tp'], d)} ({s['tp_pips']} pips) · R:R 1:{s['rr']} · กำไรเป้า {reward}" if s.get("tp") is not None
               else "TP: ไม่ตั้ง — ระบบตามเทรนด์ ปล่อยกำไรวิ่ง ออกเมื่อชน SL หรือมีสัญญาณกลับทิศ")
    close_by = (f"ปิดภายใน {datetime.fromisoformat(s['close_by']).astimezone(BKK).strftime('%d/%m %H:%M')} น." if s.get("close_by")
                else "ถือจนชน TP / SL หรือมีสัญญาณกลับทิศ")
    rev = (f"🔁 ปิดออเดอร์ {s['reverse_of']} เดิมของระบบนี้ (comment {tag}) ก่อน แล้วเปิดไม้นี้\n" if s.get("reverse_of") else "")
    msg = (f"{rev}{lot_line}\nEntry ~{_fmt(s['entry'], d)}\nSL {_fmt(s['sl'], d)} ({s['sl_pips']} pips)\n"
           f"{tp_line}\n"
           f"{close_by} · ระบบ: {s['strategy_name']} ({s['tf']}) · {s['system_status']}\n🏷️ ใส่ comment ใน MT5: {tag}")
    return title, msg


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
    if sched == "quarterly":      # once per quarter, after its start (the research cut-off moved)
        cut = research.research_cutoff(settings)
        return datetime.fromisoformat(gen) < cut.to_pydatetime()
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
