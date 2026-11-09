"""Live signal scanner + forward tracking, plus the research scheduler loop."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import data, notify, research
from .config import CLOUD, SYMBOLS, SIGNALS_FILE, TIMEFRAMES, load_settings, read_json, symbol_spec, write_json
from .engine import cost_price, exit_rules, friday_cutoff, hold_bars, run, simulate, week_guard, weekend_blocked
from .strategies import STRATEGIES, signals as strat_signals
from . import indicators as ta
from .sizing import lots_for

log = logging.getLogger("tradinglab.scanner")
BKK = timezone(timedelta(hours=7))
SYMBOLS_GROUP = {k: v.get("group") for k, v in SYMBOLS.items()}
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


def _make_signal(sym: str, cfg: dict, df: pd.DataFrame, res: dict, settings: dict, i: int | None = None) -> dict:
    """Signal on the last closed bar, or (i = an earlier bar) a catch-up for a bar a scan missed: the backtest
    entered at the next open and that trade is still open, so the order is still worth taking."""
    spec = symbol_spec(sym, settings)
    p = cfg["params"]
    late = i is not None and i != len(df) - 1
    i = len(df) - 1 if i is None else i
    if late:
        d, sl_dist = int(res["open_trade"]["dir"]), float(res["open_trade"]["sl_dist"])
    else:
        d, sl_dist = int(res["last_signal"]), float(p["sl_atr"]) * res["atr_last"]
    entry = float(df.close.iloc[i])
    rr = float(p["rr"])
    sl, tp = entry - d * sl_dist, (entry + d * rr * sl_dist if rr > 0 else None)      # rr 0 = no take-profit
    info = data.mt5_symbol_info(sym)
    risk_pct = (float(cfg.get("risk_pct") or 0.1) if cfg.get("source") == "trial" else 0) or \
               (float(settings.get("portfolio_risk_pct") or 0.1) if cfg.get("source") == "portfolio" else 0) or \
               (float(settings.get("risk_per_trade_pct") or 0) or float(cfg.get("risk_pct") or 0)
                or settings["default_risk_pct"] / 2)
    balance = float(settings["account_balance"])
    size = lots_for(sym, entry, sl, balance, risk_pct, info, settings)
    min_lot_risk = round(0.01 * sl_dist * size.get("value_per_point_per_lot", 0), 2)  # what the broker's minimum lot would risk
    bar_t = df.index[i]
    tfm = TIMEFRAMES[cfg["tf"]]
    entry_t = bar_t + pd.Timedelta(minutes=tfm)
    rules = exit_rules()
    close_by = entry_t + pd.Timedelta(hours=p["max_hold_h"]) if rules["max_hold"] else None
    if rules["friday"]:
        close_by = min(close_by, friday_cutoff(entry_t)) if close_by is not None else friday_cutoff(entry_t)
    rev = None if late else (res.get("open_trade") or {}).get("reverse_pending")
    trail = float(res["open_trade"].get("trail") or 0) if late else float(p.get("trail_atr") or 0) * float(res["atr_last"])
    return {
        "id": f"{sym}-{cfg['tf']}-{cfg['strategy']}-{int(bar_t.timestamp())}",
        "created": datetime.now(timezone.utc).isoformat(),
        "bar_time": bar_t.isoformat(),
        "valid_until": (bar_t + pd.Timedelta(minutes=2 * tfm)).isoformat(),
        "symbol": sym, "mt5_symbol": settings["mt5_symbol_map"].get(sym, sym), "tf": cfg["tf"],
        "strategy": cfg["strategy"], "strategy_name": cfg.get("strategy_name"), "params": p,
        "system_status": cfg["status"], "source": cfg.get("source", "single"), "side": "LONG" if d > 0 else "SHORT", "dir": d,
        "entry": round(entry, spec["digits"]), "sl": round(sl, spec["digits"]), "tp": round(tp, spec["digits"]) if tp is not None else None,
        "sl_dist": round(sl_dist, spec["digits"]), "tp_dist": round(sl_dist * rr, spec["digits"]) if tp is not None else None,
        "sl_pips": round(sl_dist / spec["pip"], 1), "tp_pips": round(sl_dist * rr / spec["pip"], 1) if tp is not None else None,
        "rr": rr, "risk_pct": risk_pct, "lots": size["lots"], "risk_usd": size["actual_risk_usd"],
        "reward_usd": round(size["actual_risk_usd"] * rr, 2) if tp is not None else None, "below_min_lot": size["below_min_lot"],
        "raw_lots": size.get("raw_lots"), "balance": balance, "min_lot_risk_usd": min_lot_risk,
        "min_lot_risk_pct": round(100 * min_lot_risk / balance, 2) if balance else None,
        "max_hold_h": p["max_hold_h"], "close_by": close_by.isoformat() if close_by is not None else None,
        "reverse_of": ("SHORT" if d > 0 else "LONG") if rev else None,
        "price_source": data.price_source(sym, settings), "cost_r": round(cost_price(spec) / sl_dist, 3),
        "outcome": "open", "outcome_r": None, "exit_time": None, "user_status": "new",
        "late": late, "origin": "cloud" if CLOUD else "pc", "cluster": cluster_of(sym),
        "trail_dist": round(trail, spec["digits"]) if trail > 0 else None,
        "stop_now": round(sl, spec["digits"]),
    }


def _track(sig: dict, df: pd.DataFrame, spec: dict) -> None:
    """Forward-test the signal with the levels that were really ALERTED (= the order the user / the PC robot placed):
    SL and TP exactly as in the push, exit at the next open on an opposite signal. (Recomputing the entry / ATR on a newer
    price feed moved the SL a little and closed signals the real order still held — user's report 28 Oct 2026.)"""
    bt = pd.Timestamp(sig["bar_time"])
    i = df.index.searchsorted(bt)
    if i >= len(df) or df.index[i] != bt:
        # the candle grid changed under an open signal (4 Nov 2026: H4 / D1 moved from the New York-close clock to Exness
        # GMT+0, so an old signal's bar time is no longer a candle start): carry on from the candle that holds the entry
        # time, otherwise the signal would never be tracked again (no SL / TP / reverse exit, no trailing stop)
        et = bt + pd.Timedelta(minutes=TIMEFRAMES[sig["tf"]])
        j = df.index.searchsorted(et, side="right") - 1
        if j < 1 or df.index[j] < bt:
            return
        i = j - 1
    if i + 1 >= len(df):
        return
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    d, entry, sl, tp = int(sig["dir"]), float(sig["entry"]), float(sig["sl"]), sig.get("tp")
    tp = float(tp) if tp is not None else None
    risk = abs(entry - sl) or 1e-12
    rules = exit_rules()
    opp = strat_signals(sig["strategy"], df, sig["params"])[0] if rules["opposite"] else None
    trail = float(sig.get("trail_dist") or 0)
    stop, best = sl, entry
    for k in range(i + 1, len(df)):
        if trail > 0 and k > i + 1:          # the trailing stop follows COMPLETED bars only (same rule as the backtest)
            best = max(best, h[k - 1]) if d > 0 else min(best, l[k - 1])
            stop = max(stop, best - trail) if d > 0 else min(stop, best + trail)
        if opp is not None and k - 1 > i and opp[k - 1] == -d:             # opposite signal on the previous close
            px, reason = o[k], "Rev"
        elif (l[k] <= stop) if d > 0 else (h[k] >= stop):                # SL first when both are touched (conservative)
            gap = (o[k] < stop) if d > 0 else (o[k] > stop)
            px, reason = (o[k] if gap else stop), ("Trail" if stop != sl else "SL")
        elif tp is not None and ((h[k] >= tp) if d > 0 else (l[k] <= tp)):
            px, reason = tp, "TP"
        else:
            continue
        sig["stop_now"] = round(float(stop), spec["digits"])
        r = round(d * (px - entry) / risk, 3)
        sig.update(outcome={"SL": "loss", "TP": "win"}.get(reason) or ("win" if r > 0 else "loss" if r < 0 else "time"),
                   exit_reason=reason, outcome_r=r, exit_time=df.index[k].isoformat(), exit_price=round(float(px), spec["digits"]))
        return
    if trail > 0:                            # still open: the stop for the next bar, from every completed bar so far
        best = max(best, h[len(df) - 1]) if d > 0 else min(best, l[len(df) - 1])
        stop = max(stop, best - trail) if d > 0 else min(stop, best + trail)
        sig["stop_now"] = round(float(stop), spec["digits"])


# Markets that move together share one risk budget (user, 2 Nov 2026): five 0.5% trades on US stocks are one bet.
CLUSTERS = {
    "usd_fx": ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"), "jpy_cross": ("GBPJPY",),
    "metals": ("XAUUSD", "XAGUSD"), "energy": ("USOIL",),
    "us_equity": ("NAS100", "US500", "NVDA", "MSFT", "AMZN", "META", "AAPL"), "world_equity": ("DE30", "JP225"),
    "crypto": ("BTCUSD", "ETHUSD"),
}
CLUSTER_TH = {"usd_fx": "คู่เงิน USD", "jpy_cross": "คู่เงิน JPY", "metals": "โลหะ", "energy": "พลังงาน",
              "us_equity": "หุ้นสหรัฐ", "world_equity": "ดัชนียุโรป/ญี่ปุ่น", "crypto": "คริปโต"}


def cluster_of(sym: str) -> str:
    return next((k for k, v in CLUSTERS.items() if sym in v), sym)


AUTO_OFF_FILE = SIGNALS_FILE.parent / "research" / "auto_off.json"


def auto_off() -> dict:
    return read_json(AUTO_OFF_FILE, {})


def kill_switch(sigs: list[dict], latest: dict, settings: dict) -> list[str]:
    """Pre-agreed stop (user, 2 Nov 2026): a live system whose forward-test drawdown (closed trades, in R) grows beyond
    `kill_dd_mult` (2x) its worst walk-forward OOS drawdown is switched off at once (data/research/auto_off.json) —
    decided in advance, not in the middle of a losing streak. Returns the keys newly switched off."""
    mult = float(settings.get("kill_dd_mult", 2.0))
    off = auto_off()
    new = []
    for sym, entry in latest.items():
        for cfg in research.live_systems(entry) + list(entry.get("trial_systems") or []):
            key = f"{sym}:{cfg['strategy']}:{cfg['tf']}"
            limit = float(cfg.get("max_dd_r") or 0) * mult
            if key in off or limit <= 0:
                continue
            rs = [float(x.get("outcome_r") or 0) for x in reversed(sigs) if x["symbol"] == sym and x["strategy"] == cfg["strategy"]
                  and x["tf"] == cfg["tf"] and x.get("outcome") not in (None, "open")
                  and x.get("created", "") >= str((entry.get("quarter") or {}).get("cutoff") or "")]
            if len(rs) < 5:
                continue
            eq = np.cumsum(rs)
            dd = float(np.max(np.maximum.accumulate(np.concatenate(([0.0], eq)))[1:] - eq))
            if dd > limit:
                off[key] = {"time": datetime.now(timezone.utc).isoformat(), "dd_r": round(dd, 2), "limit_r": round(limit, 2),
                            "trades": len(rs)}
                new.append(key)
    if new:
        write_json(AUTO_OFF_FILE, off)
    return new


def pc_status() -> dict:
    return read_json(SIGNALS_FILE.parent / "pc_status.json", {})


def pc_covered(sig: dict, tf: str) -> bool:
    """The PC auto-trader is the authority while it runs (broker candles, scans seconds after each close): the cloud
    does not add a signal for a candle the PC has already scanned. If the PC is off (no scan for > 2 h) the cloud
    alerts as before."""
    st = pc_status()
    try:
        last = pd.Timestamp(st["last_scan_hour"])
        if pd.Timestamp.now(tz="UTC") - last > pd.Timedelta(hours=2):
            return False
        return pd.Timestamp(sig["bar_time"]) + pd.Timedelta(minutes=TIMEFRAMES[tf]) <= last
    except Exception:
        return False


def scan_once() -> list[dict]:
    settings = load_settings()
    latest = research.latest().get("symbols", {})
    allow = {"ACTIVE"} | ({"WATCH"} if settings["alerts"].get("watch") else set())
    track = {"ACTIVE", "WATCH", "TRIAL"}
    new = []
    STATE.update(errors=[], checked=0)
    with _lock:
        sigs = load_signals()
        known = {s["id"] for s in sigs}
        frames: dict[tuple, pd.DataFrame] = {}
        cap = float(settings.get("max_symbol_risk_pct", 0.75))
        ccaps = settings.get("cluster_risk_pct", {})
        stopped = auto_off()
        for sym, entry in latest.items():
            # every ACTIVE system of the quarter is traded (several per symbol / TF allowed)
            trials = list(entry.get("trial_systems") or []) if settings.get("trials", {}).get("enabled", True) else []
            for cfg in research.live_systems(entry) + trials:
                if cfg.get("status") not in track or not cfg.get("params"):
                    continue
                if research._forced_off(settings, sym, cfg):     # veto takes effect at once, not only at the next research merge
                    continue
                if f"{sym}:{cfg['strategy']}:{cfg['tf']}" in stopped:    # the pre-agreed drawdown stop fired
                    continue
                try:
                    key = (sym, cfg["tf"])
                    df = frames.get(key)
                    if df is None:
                        df = frames[key] = data.get_bars(sym, cfg["tf"])
                    res = run(df, cfg["strategy"], cfg["params"], symbol_spec(sym, settings), cfg["tf"])
                    n = len(df)
                    STATE["checked"] += 1
                    age_h = (pd.Timestamp.now(tz="UTC") - df.index[-1]).total_seconds() / 3600 - TIMEFRAMES[cfg["tf"]] / 60
                    if cfg["tf"] == "H1" and age_h > 3 and (SYMBOLS_GROUP.get(sym) == "Crypto" or pd.Timestamp.now(tz="UTC").dayofweek < 5):
                        STATE["errors"].append(f"{sym} {cfg['tf']}: newest closed bar is {age_h:.0f} h old (stale data)")
                    flat = res["open_trade"] is None and (not res["trades"] or res["trades"][-1]["exit_i"] < n - 1)
                    entry_t = df.index[-1] + pd.Timedelta(minutes=TIMEFRAMES[cfg["tf"]])
                    weekend = exit_rules()["friday"] and (weekend_blocked(entry_t) or
                                                          friday_cutoff(entry_t) < entry_t + pd.Timedelta(minutes=TIMEFRAMES[cfg["tf"]]))
                    ot = res["open_trade"]
                    reverse = bool(ot and ot.get("reverse_pending") and res["last_signal"] == -ot["dir"])
                    s = None
                    if res["last_signal"] and (flat or reverse) and np.isfinite(res["atr_last"]) and not weekend:
                        s = _make_signal(sym, cfg, df, res, settings)
                    elif ot and not reverse and 1 <= n - 1 - ot["signal_i"] <= 2 and not weekend:
                        # a scan was skipped (GitHub runs late, PC restarted): the signal bar is 1-2 bars back and the
                        # backtest's trade from it is still open -> alert it late rather than never
                        s = _make_signal(sym, cfg, df, res, settings, i=ot["signal_i"])
                    if s is not None:
                        if s["id"] in known:
                            continue
                        if CLOUD and pc_covered(s, cfg["tf"]):     # the PC already judged this candle on broker data
                            continue
                        # "close your old order first" only if we actually alerted that order (a system that just became
                        # ACTIVE may hold a backtest-only position the user never saw)
                        if s.get("reverse_of") and not any(x["symbol"] == sym and x["strategy"] == cfg["strategy"] and x["tf"] == cfg["tf"]
                                                           and x.get("outcome") == "open" and x.get("dir") == -s["dir"] for x in sigs):
                            s["reverse_of"] = None
                        # total risk cap per symbol (user's rule): open ACTIVE alerts of this symbol, other systems'
                        # positions + this one; a reversal replaces this system's own open order
                        same_sys = lambda x: x["strategy"] == cfg["strategy"] and x["tf"] == cfg["tf"]
                        open_risk = sum(float(x.get("risk_pct") or 0) for x in sigs
                                        if x["symbol"] == sym and x.get("outcome") == "open" and not x.get("capped")
                                        and x.get("system_status") == "ACTIVE" and not same_sys(x))
                        if s["system_status"] == "ACTIVE" and open_risk + float(s["risk_pct"]) > cap + 1e-9:
                            s.update(capped=True, user_status="skipped",
                                     note=f"ข้าม: ความเสี่ยงรวม {sym} จะเป็น {open_risk + float(s['risk_pct']):.2f}% เกินเพดาน {cap}%")
                        cl = cluster_of(sym)
                        ccap = float(ccaps.get(cl, ccaps.get("default", 0)) or 0)
                        if s["system_status"] == "ACTIVE" and not s.get("capped") and ccap > 0:
                            c_risk = sum(float(x.get("risk_pct") or 0) for x in sigs
                                         if cluster_of(x["symbol"]) == cl and x.get("outcome") == "open" and not x.get("capped")
                                         and x.get("system_status") == "ACTIVE" and not (x["symbol"] == sym and same_sys(x)))
                            if c_risk + float(s["risk_pct"]) > ccap + 1e-9:
                                s.update(capped=True, user_status="skipped",
                                         note=f"ข้าม: ความเสี่ยงรวมกลุ่ม{CLUSTER_TH.get(cl, cl)} จะเป็น {c_risk + float(s['risk_pct']):.2f}% เกินเพดาน {ccap}%")
                        sigs.insert(0, s)
                        known.add(s["id"])
                        new.append(s)
                except Exception as e:
                    log.exception("scan %s %s failed", sym, cfg.get("strategy"))
                    STATE["last_error"] = f"{sym} {cfg.get('strategy')}: {e}"
                    STATE["errors"].append(f"{sym} {cfg.get('strategy')} {cfg.get('tf')}: {e!r}"[:200])
        for s in sigs:
            if s.get("outcome") == "open":
                df = frames.get((s["symbol"], s["tf"]))
                if df is None:
                    try:
                        df = frames[(s["symbol"], s["tf"])] = data.get_bars(s["symbol"], s["tf"])
                    except Exception:
                        continue
                _track(s, df, symbol_spec(s["symbol"], settings))
        killed = kill_switch(sigs, latest, settings)
        save_signals(sigs)
    for key in killed:
        sym_, sid_, tf_ = key.split(":")
        no = STRATEGIES[sid_]["no"] if sid_ in STRATEGIES else sid_
        notify.send(f"⛔ หยุดระบบ #{no} {sym_} {tf_} อัตโนมัติ",
                    f"ขาดทุนต่อเนื่องจริงเกิน {settings.get('kill_dd_mult', 2.0)} เท่าของที่แย่ที่สุดในการทดสอบ — ไม่เปิดออเดอร์ใหม่ของระบบนี้ "
                    "(ออเดอร์ที่ถืออยู่ปล่อยจบด้วย SL/TP)", priority=4, tags=["no_entry"])
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
    closed = pd.Timestamp(s["bar_time"]) + pd.Timedelta(minutes=TIMEFRAMES.get(s["tf"], 60))
    late = max(0, int((pd.Timestamp(s["created"]) - closed).total_seconds() // 60)) if s.get("created") else 0
    when = f"🕒 แท่งปิด {closed.tz_convert(BKK).strftime('%H:%M')} น." + (f" · แจ้งช้า {late} นาที — เช็คราคาใน MT5 ก่อนเข้า" if late >= 15 else "")
    src = {"pc": "💻 จากคอม (ราคา MT5)", "cloud": "☁️ จากคลาวด์ (คอมไม่ได้ออนไลน์)"}.get(s.get("origin"))
    when += f"\n{src}" if src else ""
    msg = (f"{rev}{when}\n{lot_line}\nEntry ~{_fmt(s['entry'], d)}\nSL {_fmt(s['sl'], d)} ({s['sl_pips']} pips)\n"
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
    if sched in ("quarterly", "yearly"):      # once per research period, after its start (the research cut-off moved)
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
            # the PC auto-trader (autotrade.bat) scans, alerts and trades each candle itself; a second scanner here would
            # record the signal first and the robot would never see it as new -> stay quiet while its heartbeat is fresh
            hb = SIGNALS_FILE.parent / "autotrade_state.json"
            if hb.exists() and time.time() - hb.stat().st_mtime < 120:
                STATE["note"] = "auto-trader is running: it scans and alerts"
            else:
                scan_once()
        except Exception as e:
            STATE["last_error"] = repr(e)
            log.exception("scan loop error")
        stop.wait(max(30, int(settings.get("scan_interval_sec", 120))))
