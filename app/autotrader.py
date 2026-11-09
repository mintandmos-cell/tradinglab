"""Local auto-trader: runs on the user's PC next to MetaTrader 5 (no EA) and trades the ACTIVE systems itself.

User's requests (28 Oct 2026): trade the TradingLab systems automatically while the PC and MT5 stay on (DEMO first,
maybe a real account later), phone alerts and the website must match what the PC trades, only guard against blowing
the account (no prop-style 6 % / 3 % limits), and below the minimum lot trade the minimum lot.

One signal, three places (no second, different signal):
  1. right after each H1 candle closes (PC clock, + a few seconds) the PC downloads the newest research and signal list
     from GitHub, then runs the SAME scanner as the cloud (`scanner.scan_once`) on the broker's own MT5 candles
     -> new signals get the same id, the same alert text (pushed at once to the phone) and are recorded in signals.json;
  2. the new signals are published to GitHub (`ghsync.publish`) -> the website shows them and the cloud scan, which runs
     10-45 min later, sees the id already exists and does NOT alert again (when the PC is off the cloud alerts as before);
  3. every new ACTIVE signal is executed in MT5: market order, visible SL (same distance as the alert), TP = rr x SL
     (none when rr = 0); an opposite signal closes this system's own position first (stop-and-reverse). A failed order
     is retried every 15 s for 10 minutes. The robot then pushes "🤖 DEMO/REAL ..." with the real lot and fill.
  * each system's positions are tagged: magic number + comment "TL{no}-{TF}" — manual orders are never touched
  * lot = risk % of the MT5 balance / SL distance (risk = the signal's risk x risk_scale); below the broker minimum the
    minimum lot is traded (min_lot_always "*"); a signal the alert skips ("capped") is skipped too (follow_cap)
  * anti-wipeout guard on equity: loss from the starting balance >= 25 % -> no new trades; >= 30 % -> close all
    (daily guards available, off by default)
  * safety: trades only on a DEMO account unless allow_live = true; dry_run = true only logs.

Settings live in data/local.json -> "autotrade" (never committed). Start with autotrade.bat.
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timezone

import pandas as pd

from . import data, ghsync, notify, scanner
from .config import DATA_DIR, SYMBOLS, TIMEFRAMES, load_settings, read_json, write_json
from .strategies import STRATEGIES

log = logging.getLogger("tradinglab.autotrader")

DEFAULTS = {
    "enabled": True,
    "dry_run": False,              # true = log what it would do, send nothing to MT5
    "allow_live": False,           # refuse to trade a REAL account unless set to true
    "risk_scale": 1.0,             # x each signal's risk (0.25 % single, 0.10 % portfolio) = same sizing as the alerts
    "follow_cap": True,            # skip a signal the alert also skips (per-symbol risk cap 0.75 %, "capped" on the website),
                                   # so the robot, the phone and the website always agree
    "max_symbol_risk_pct": 1.0,    # robot own cap per asset (user: max 1 % per asset), only for symbols NOT in min_lot_always
    # user's rule 28 Oct 2026: no prop-style 6 % / 3 % limits, only "don't blow the account" (0 = off)
    "pause_total_dd_pct": 25.0,    # loss from the starting balance: stop opening new trades
    "close_total_dd_pct": 30.0,    # ... close everything
    "pause_daily_dd_pct": 0,       # daily limits off
    "close_daily_dd_pct": 0,
    "min_lot_up_to_pct": 0.0,      # symbols NOT in min_lot_always: trade the min lot only when it risks <= this % (0 = skip)
    "min_lot_always": "*",         # user's choice 28 Oct 2026: every symbol trades at least the minimum lot
    "start_balance": None,         # set on the first run (the account's starting balance)
    "magic": 5_600_000,
    "deviation_points": 30,
    "retry_minutes": 10,
    "alert_levels": True,          # place exactly the alert's SL / TP prices (lots sized on the real distance)
    "max_chase_r": 0.5,            # a late signal is not entered once price ran > 0.5 R in its favour (or to within 0.1 R of SL)           # a failed order is retried every 15 s for this long after the signal
    "notify": True,                # push "🤖 DEMO/REAL ..." to the ntfy topic in data/local.json
    "autostart": True,             # start autotrade.bat after every Windows logon (Startup folder)
    "trade_trials": True,          # near-miss WATCH systems ("TRIAL" signals, 0.1 % risk) — DEMO accounts only, never REAL
    "trail_stops": True,           # move the SL of positions whose system uses a trailing stop (after each hourly scan)
}
STATE_FILE = DATA_DIR / "autotrade_state.json"
LOG_FILE = DATA_DIR / "autotrade_log.jsonl"


def cfg() -> dict:
    return {**DEFAULTS, **(load_settings().get("autotrade") or {})}


def _log(event: str, **kw) -> None:
    row = {"time": datetime.now(timezone.utc).isoformat(), "event": event, **kw}
    log.info("%s %s", event, kw)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def _mt5():
    import MetaTrader5 as mt5
    return mt5


def _kind() -> str:
    mt5 = _mt5()
    a = mt5.account_info()
    return "DEMO" if a is None or a.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else "REAL"


def _push(c: dict, title: str, msg: str, priority: int = 3) -> None:
    if c.get("notify"):
        try:
            notify.send(f"🤖 {_kind()} · " + title, msg, priority=priority, tags=["robot"])
        except Exception:
            pass


def _tag(sid: str, tf: str) -> str:
    return f"TL{STRATEGIES[sid]['no'] if sid in STRATEGIES else sid}-{tf}"


def _positions(c: dict) -> list:
    return [p for p in (_mt5().positions_get() or []) if p.magic == c["magic"]]


def _filling(info) -> int:
    mt5 = _mt5()
    fm = int(getattr(info, "filling_mode", 0) or 0)
    if fm & 1:
        return mt5.ORDER_FILLING_FOK
    if fm & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def _value_per_unit(info) -> float:
    return float(info.trade_tick_value) / float(info.trade_tick_size) if info and info.trade_tick_size else 0.0


def _tradable(c: dict, s: dict) -> bool:
    """ACTIVE signals always; TRIAL signals only on a DEMO account and when trade_trials is on (user, 2 Nov 2026)."""
    st_ = s.get("system_status")
    return st_ == "ACTIVE" or (st_ == "TRIAL" and c.get("trade_trials", True) and _kind() == "DEMO")


def _forced(c: dict, sym: str) -> bool:
    mla = c.get("min_lot_always") or []
    return mla == "*" or sym in mla


# ------------------------------------------------------------------------------------------------ orders
def _close(c: dict, p, reason: str) -> bool:
    mt5 = _mt5()
    info, tick = mt5.symbol_info(p.symbol), mt5.symbol_info_tick(p.symbol)
    if not info or not tick:
        return False
    buy = p.type == mt5.POSITION_TYPE_SELL
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume, "position": p.ticket,
           "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL, "price": tick.ask if buy else tick.bid,
           "deviation": c["deviation_points"], "magic": c["magic"], "comment": p.comment, "type_filling": _filling(info),
           "type_time": mt5.ORDER_TIME_GTC}
    if c["dry_run"]:
        _log("close_dry", symbol=p.symbol, ticket=p.ticket, reason=reason)
        return True
    r = mt5.order_send(req)
    ok = r is not None and r.retcode == mt5.TRADE_RETCODE_DONE
    _log("close" if ok else "close_failed", symbol=p.symbol, ticket=p.ticket, comment=p.comment, reason=reason,
         retcode=getattr(r, "retcode", None), msg=getattr(r, "comment", None) or str(mt5.last_error()))
    if ok:
        _push(c, f"ปิด {p.symbol} {p.comment}", f"เหตุผล: {reason} · กำไร/ขาดทุน {p.profit:+.2f} USD")
    return ok


def execute(c: dict, s: dict, balance: float) -> str:
    """Trade one signal. Returns 'done' (opened / deliberately skipped) or 'retry' (order failed)."""
    mt5 = _mt5()
    sym, d = s["symbol"], int(s["dir"])
    tag = _tag(s["strategy"], s["tf"])
    name = data.mt5_resolve(load_settings()["mt5_symbol_map"].get(sym, sym))
    if name is None:
        _log("skip", symbol=sym, system=tag, reason="broker has no such symbol — set mt5_symbol_map in data/local.json")
        return "done"
    mt5.symbol_select(name, True)
    if s.get("capped") and c.get("follow_cap", True):
        # the new entry is skipped like the alert, but an opposite signal still ends this system's old position
        for q in [q for q in _positions(c) if q.symbol == name and q.comment == tag]:
            if (q.type == mt5.POSITION_TYPE_BUY) != (d > 0) and not _close(c, q, "สัญญาณกลับทิศ"):
                return "retry"
        _log("skip", symbol=sym, system=tag, reason="capped like the alert (per-symbol risk cap)")
        return "done"
    mine = [q for q in _positions(c) if q.symbol == name and q.comment == tag]
    if any((q.type == mt5.POSITION_TYPE_BUY) == (d > 0) for q in mine):
        _log("skip", symbol=sym, system=tag, reason="already holding this direction")
        return "done"
    for q in mine:                                                     # opposite position -> stop-and-reverse
        if not _close(c, q, "สัญญาณกลับทิศ"):
            return "retry"
    info, tick = mt5.symbol_info(name), mt5.symbol_info_tick(name)
    if not info or not tick:
        return "retry"
    price = tick.ask if d > 0 else tick.bid
    sl_dist, rr = float(s["sl_dist"]), float(s["rr"])
    moved = d * (price - float(s["entry"])) / sl_dist if sl_dist > 0 else 0.0     # in R, + = in the trade's favour
    if moved > float(c["max_chase_r"]) or moved < -0.9:
        _log("skip", symbol=sym, system=tag, signal=s["id"],
             reason=f"price moved {moved:+.2f} R from the signal's entry {s['entry']} - too late to enter")
        _push(c, f"ไม่เข้า {name} {tag} — ราคาวิ่งไปแล้ว",
              f"สัญญาณ {s['side']} ที่ ~{s['entry']} · ตอนนี้ {price} ({moved:+.2f} R) · เข้าตอนนี้ความเสี่ยง/ผลตอบแทนไม่เหมือนที่ทดสอบไว้")
        return "done"
    risk_pct = float(s.get("risk_pct") or 0.25) * float(c["risk_scale"])
    vpu = _value_per_unit(info)
    # the SAME SL / TP prices as the alert and the website (user's rule 28 Oct 2026: the robot's order must match what the
    # phone and the web show). Lots are sized on the real distance from today's fill to that SL.
    if c.get("alert_levels", True) and s.get("sl") is not None:
        sl_dist = abs(price - float(s["sl"]))
    if vpu <= 0 or sl_dist <= 0:
        _log("skip", symbol=sym, system=tag, reason="bad tick value / SL distance")
        return "done"
    raw = balance * risk_pct / 100 / (sl_dist * vpu)
    step, vmin, vmax = info.volume_step or 0.01, info.volume_min or 0.01, info.volume_max or 100
    lots = min(vmax, math.floor(raw / step + 1e-9) * step)
    trial = s.get("system_status") == "TRIAL"
    forced = _forced(c, sym) and not trial          # a trial never risks more than ~3x its small budget for the min lot
    if trial and lots < vmin and vmin * sl_dist * vpu / balance * 100 <= 3 * risk_pct:
        lots = vmin
    if lots < vmin:
        min_risk_pct = vmin * sl_dist * vpu / balance * 100
        if not forced and min_risk_pct > float(c.get("min_lot_up_to_pct") or 0):
            _log("skip", symbol=sym, system=tag, reason=f"lot {raw:.4f} < min {vmin} ({vmin} lot would risk {min_risk_pct:.2f}%)")
            return "done"
        lots = vmin
    risk_usd = lots * sl_dist * vpu
    if not forced:
        cap_usd = balance * float(c["max_symbol_risk_pct"]) * float(c["risk_scale"]) / 100
        open_risk = sum(abs(q.price_open - q.sl) * _value_per_unit(mt5.symbol_info(q.symbol)) * q.volume
                        for q in _positions(c) if q.symbol == name and q.sl)
        if open_risk + risk_usd > cap_usd + 1e-6:
            _log("skip", symbol=sym, system=tag, reason=f"symbol risk cap {cap_usd:.2f} USD reached")
            return "done"
    digits = info.digits
    if c.get("alert_levels", True) and s.get("sl") is not None:
        sl = round(float(s["sl"]), digits)
        tp = round(float(s["tp"]), digits) if s.get("tp") is not None else 0.0
    else:
        sl = round(price - d * sl_dist, digits)
        tp = round(price + d * rr * sl_dist, digits) if rr > 0 else 0.0
    side = "BUY" if d > 0 else "SELL"
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": name, "volume": round(lots, 6),
           "type": mt5.ORDER_TYPE_BUY if d > 0 else mt5.ORDER_TYPE_SELL, "price": price, "sl": sl, "tp": tp,
           "deviation": c["deviation_points"], "magic": c["magic"], "comment": tag, "type_filling": _filling(info),
           "type_time": mt5.ORDER_TIME_GTC}
    if c["dry_run"]:
        _log("open_dry", symbol=sym, system=tag, signal=s["id"], side=side, lots=lots, price=price, sl=sl, tp=tp)
        return "done"
    r = mt5.order_send(req)
    ok = r is not None and r.retcode == mt5.TRADE_RETCODE_DONE
    fill = getattr(r, "price", 0) or price
    closed = r is not None and r.retcode == getattr(mt5, "TRADE_RETCODE_MARKET_CLOSED", 10018)
    if not (closed and s.get("_closed_logged")):          # "market closed" is logged once per signal, not every 15 s
        _log("open" if ok else "open_failed", symbol=sym, system=tag, signal=s["id"], side=side, lots=lots, price=fill, sl=sl,
             tp=tp, risk_usd=round(risk_usd, 2), retcode=getattr(r, "retcode", None),
             msg=getattr(r, "comment", None) or str(mt5.last_error()))
    if closed:
        s["_closed_logged"] = True
        return "closed"
    if not ok:
        return "retry"
    _opened[str(getattr(r, "order", "") or "")] = s["id"]
    # execution quality (user, 2 Nov 2026): what the fill really cost vs the price asked and vs the signal's entry
    pt = float(info.point or 0) or 1e-9
    _fills.insert(0, {"time": datetime.now(timezone.utc).isoformat(), "signal": s["id"], "symbol": sym, "tf": s["tf"],
                      "status": s.get("system_status"), "side": side, "asked": price, "fill": fill, "signal_entry": s["entry"],
                      "slip_pts": round(d * (fill - price) / pt, 1),                  # + = worse than the quote
                      "drift_r": round(d * (fill - float(s["entry"])) / float(s["sl_dist"]), 3) if s.get("sl_dist") else None,
                      "delay_s": round((pd.Timestamp.now(tz="UTC") - pd.Timestamp(s["bar_time"])
                                        - pd.Timedelta(minutes=TIMEFRAMES[s["tf"]])).total_seconds())})
    del _fills[100:]
    _push(c, f"{side} {name} {lots:.2f} lot · {tag}",
          f"เปิดแล้ว @ {fill:.{digits}f} · SL {sl}" + (f" · TP {tp}" if tp else " · ไม่มี TP")
          + f" · เสี่ยง {risk_usd:.2f} USD ({risk_usd / balance * 100:.2f}% ของทุน)")
    return "done"


# ------------------------------------------------------------------------------------------------ positions -> website
_opened: dict[str, str] = {}          # position ticket -> signal id, filled by execute()
_fills: list[dict] = []                # newest fills (execute) -> state "fills" -> pc_status.json
REASONS = {0: "ปิดเอง", 1: "ปิดเอง (มือถือ)", 2: "ปิดเอง (เว็บ)", 3: "โปรแกรมปิด", 4: "SL", 5: "TP", 6: "Stop out"}


def _signal_of(st: dict, p) -> str | None:
    t = st.setdefault("tickets", {})
    if str(p.ticket) in t:
        return t[str(p.ticket)]
    # positions opened before tickets were recorded: the newest executed signal of the same system
    for sid in reversed(st.get("done", [])):
        parts = sid.split("-")
        if len(parts) >= 4 and _tag(parts[2], parts[1]) == p.comment and p.symbol.upper().startswith(parts[0][:3]):
            t[str(p.ticket)] = sid
            return sid
    return None


def _pos_row(st: dict, p) -> dict:
    mt5 = _mt5()
    info = mt5.symbol_info(p.symbol)
    dg = info.digits if info else 5
    return {"ticket": p.ticket, "symbol": p.symbol, "system": p.comment, "signal_id": _signal_of(st, p),
            "side": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL", "lots": p.volume,
            "price_open": round(p.price_open, dg), "sl": round(p.sl, dg) if p.sl else None, "tp": round(p.tp, dg) if p.tp else None,
            "price": round(p.price_current, dg), "profit": round(p.profit + getattr(p, "swap", 0.0), 2),
            "time": (datetime.fromtimestamp(p.time, timezone.utc) - pd.Timedelta(hours=data.mt5_utc_offset() or 0)).isoformat()}


def watch_positions(c: dict, st: dict, force: bool = False) -> None:
    """Every loop: notice positions that closed (SL / TP at the broker, or by hand), push it, and publish the robot's
    positions to GitHub so the website shows exactly what MT5 holds."""
    mt5 = _mt5()
    st.setdefault("tickets", {}).update(_opened)
    _opened.clear()
    if _fills:
        st["fills"] = (_fills + st.get("fills", []))[:100]
        _fills.clear()
        force = True
    now = {str(p.ticket): p for p in _positions(c)}
    before = st.get("open_tickets", [])
    closed = [t for t in before if t not in now]
    for t in closed:
        known = st.get("rows", {}).get(t, {})
        row = {"ticket": int(t), "signal_id": st["tickets"].get(t), "time": datetime.now(timezone.utc).isoformat(),
               **{k: known[k] for k in ("symbol", "system", "side", "lots", "price_open") if k in known}}
        try:
            deals = mt5.history_deals_get(position=int(t)) or []
            out = [d for d in deals if d.entry in (1, 3)]                    # DEAL_ENTRY_OUT / OUT_BY
            if out:
                d = out[-1]
                row.setdefault("symbol", d.symbol)
                row.setdefault("system", d.comment or "")
                row.update(price=d.price, reason=REASONS.get(d.reason, str(d.reason)),
                           profit=round(sum(x.profit + x.swap + x.commission for x in deals), 2))
        except Exception as e:
            log.warning("deal history %s: %s", t, e)
        st.setdefault("closed", []).insert(0, row)
        del st["closed"][30:]
        _log("closed", **row)
        _push(c, f"ปิด {row.get('symbol', '')} {row.get('system', '')} — {row.get('reason', '')}",
              f"ราคา {row.get('price', '–')} · กำไร/ขาดทุน {row.get('profit', 0):+.2f} USD")
    changed = sorted(now) != sorted(before)
    st["open_tickets"] = sorted(now)
    st["rows"] = {t: {"symbol": p.symbol, "system": p.comment, "side": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
                      "lots": p.volume, "price_open": p.price_open} for t, p in now.items()}
    if changed or force:
        publish_status(c, st)


def publish_status(c: dict, st: dict, **extra) -> bool:
    a = _mt5().account_info()
    status = {**st.get("status", {}), **extra, "time": datetime.now(timezone.utc).isoformat(), "account": _kind(),
              "balance": getattr(a, "balance", None), "equity": getattr(a, "equity", None),
              "start_balance": st.get("start_balance"),
              "positions": [_pos_row(st, p) for p in _positions(c)], "closed": st.get("closed", [])[:15],
              "fills": st.get("fills", [])[:40], "swap_watch": st.get("swap_watch"),
              "bars_published": st.get("bars_published")}
    st["status"] = {k: status[k] for k in ("last_scan_hour", "new_signals", "scan") if k in status}
    return ghsync.put_status(status)


# ------------------------------------------------------------------------------------------------ guards
def _guards(c: dict, st: dict) -> str:
    """'ok' | 'pause' | 'close' from the account's equity."""
    a = _mt5().account_info()
    start = float(c.get("start_balance") or st.get("start_balance") or a.balance)
    st["start_balance"] = start
    srv_day = str(data.utc_to_server(pd.DatetimeIndex([pd.Timestamp.now(tz="UTC")]))[0].date())
    if st.get("day") != srv_day:
        st.update(day=srv_day, day_equity=float(a.equity))
    total_dd = (start - a.equity) / start * 100
    day_dd = (st["day_equity"] - a.equity) / st["day_equity"] * 100
    st.update(equity=float(a.equity), balance=float(a.balance), total_dd=round(total_dd, 2), day_dd=round(day_dd, 2))
    hit = lambda v, lim: float(lim or 0) > 0 and v >= float(lim)
    if hit(total_dd, c["close_total_dd_pct"]) or hit(day_dd, c["close_daily_dd_pct"]):
        return "close"
    if hit(total_dd, c["pause_total_dd_pct"]) or hit(day_dd, c["pause_daily_dd_pct"]):
        return "pause"
    return "ok"


# ------------------------------------------------------------------------------------------------ one cycle
def _queue(c: dict, st: dict, sig: dict, why: str) -> None:
    """Queue one ACTIVE signal for execution (once per signal id)."""
    done = st.setdefault("done", [])
    if sig["id"] in done or any(j["signal"]["id"] == sig["id"] for j in st.get("pending", [])):
        return
    st.setdefault("pending", []).append({"signal": sig, "until": time.time() + 60 * float(c["retry_minutes"])})
    _log("queued", signal=sig["id"], source=why)


def _mark_done(st: dict, sig_id: str) -> None:
    done = st.setdefault("done", [])
    if sig_id not in done:
        done.append(sig_id)
    del done[:-500]


def hourly_scan(c: dict, st: dict) -> list[dict]:
    """Same scanner as the cloud on MT5 candles; new ACTIVE signals are queued for execution."""
    ghsync.pull()                                                      # newest research + signal list (ids) first
    data._mem.clear()                                                  # rebuild candles from MT5 for the new close
    new = scanner.scan_once()                                          # records + pushes the normal alert at once
    pub = ghsync.publish(new)                                          # website + cloud see them (no second alert)
    sync_exits(c, st)
    health = {"checked": scanner.STATE.get("checked"), "errors": scanner.STATE.get("errors", [])[:10]}
    hb = publish_status(c, st, last_scan_hour=str(pd.Timestamp.now(tz="UTC").floor("h")), new_signals=[s["id"] for s in new],
                        scan=health)
    _log("scan", new=[s["id"] for s in new], published=pub, heartbeat=hb, **health,
         **({} if (hb and pub) else {"github_error": ghsync.LAST["error"]}))
    if not hb and not st.get("gh_warned"):
        st["gh_warned"] = True
        _push(c, "⚠️ คอมส่งข้อมูลขึ้นเว็บไม่ได้", f"GitHub: {ghsync.LAST['error'][:150]} — คลาวด์จะแจ้งเตือนแทน (ช้ากว่า)", priority=4)
    elif hb:
        st["gh_warned"] = False
    st["scan_ok"] = True
    for s in new:
        if _tradable(c, s):
            _queue(c, st, s, "pc")
    follow_alerts(c, st, pull=False)
    if c.get("trail_stops", True):
        trail_positions(c, st)
    swap_watch(c, st)
    publish_bars(c, st)
    return new


RECENT_BARS = 1500


def publish_bars(c: dict, st: dict) -> None:
    """User, 4 Nov 2026: the website chart, the cloud's signals and MT5 must show the SAME candles. After each hourly scan
    the last RECENT_BARS closed H1 bars of every symbol (straight from this MT5, in UTC) go to GitHub in one commit:
    data/exness/recent/{SYM}_H1.csv. The cloud joins them to the Exness history (data._seed_exness) and the web chart
    reads them for its newest candles; the free feeds only fill in when the PC has been off."""
    from .config import SYMBOLS
    files = {}
    for sym in SYMBOLS:
        try:
            df = data.drop_unclosed(data.load_h1(sym, src="mt5"), "H1").tail(RECENT_BARS)
            if f"mt5:{sym}" not in data._mem or len(df) < 50:     # MT5 had no data -> free-feed fallback: never label it Exness
                continue
            out = df[["open", "high", "low", "close", "volume"]].copy()
            out.index = out.index.strftime("%Y-%m-%dT%H:%M:%SZ")
            files[f"data/exness/recent/{sym}_H1.csv"] = out.to_csv(index_label="time", float_format="%.10g").encode()
        except Exception as e:
            log.warning("recent bars %s: %s", sym, e)
    if files:
        ok = ghsync.put_files(files, "pc: recent Exness bars [skip ci]")
        st["bars_published"] = {"time": pd.Timestamp.now(tz="UTC").isoformat(), "symbols": len(files), "ok": ok}
        _log("bars", symbols=len(files), ok=ok)


def trail_positions(c: dict, st: dict) -> None:
    """Systems with a trailing stop: move the position's SL to the stop the forward tracking computed on the broker's
    COMPLETED candles (signal 'stop_now' — the same rule as the backtest). Only ever tightens."""
    mt5 = _mt5()
    sigs = {x["id"]: x for x in scanner.load_signals()}
    for p in _positions(c):
        sig = sigs.get(_signal_of(st, p) or "")
        if not sig or not sig.get("trail_dist") or sig.get("stop_now") is None or sig.get("outcome") != "open":
            continue
        info = mt5.symbol_info(p.symbol)
        new = round(float(sig["stop_now"]), info.digits if info else 5)
        buy = p.type == mt5.POSITION_TYPE_BUY
        if p.sl and ((buy and new <= p.sl + (info.point if info else 0)) or (not buy and new >= p.sl - (info.point if info else 0))):
            continue
        req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": p.symbol, "position": p.ticket, "sl": new, "tp": p.tp, "magic": c["magic"]}
        if c["dry_run"]:
            _log("trail_dry", symbol=p.symbol, ticket=p.ticket, sl=new)
            continue
        r = mt5.order_send(req)
        ok = r is not None and r.retcode == mt5.TRADE_RETCODE_DONE
        _log("trail" if ok else "trail_failed", symbol=p.symbol, ticket=p.ticket, old_sl=p.sl, sl=new,
             retcode=getattr(r, "retcode", None), msg=getattr(r, "comment", None) or str(mt5.last_error()))


def sync_exits(c: dict, st: dict) -> None:
    """A position whose signal the system has exited by an OPPOSITE signal (forward tracking on the broker's candles,
    reason "Rev") is closed too - normally the reverse signal itself closes it, this catches the cases where no new
    entry was made (reverse skipped by the cap, or the position came from a cloud signal the PC's backtest never held)."""
    sigs = {x["id"]: x for x in scanner.load_signals()}
    for p in _positions(c):
        sig = sigs.get(_signal_of(st, p) or "")
        if sig and sig.get("outcome") != "open" and sig.get("exit_reason") == "Rev":
            _close(c, p, "ระบบออกด้วยสัญญาณกลับทิศ")


# ------------------------------------------------------------------------------------------------ jobs from Claude
def broker_costs(mt5) -> dict:
    """The broker's real trading costs per symbol (user, 2 Nov 2026: test on the broker's own costs): the median spread of
    the H1 bars of the last ~3 years (overall and in the busiest hours), the swap rates with their unit (swap_mode) and the
    weekday that is charged three times. Bars carry the spread in points (MetaTrader's per-bar minimum spread)."""
    out = {"generated": datetime.now(timezone.utc).isoformat(), "symbols": {}}
    for sym in SYMBOLS:
        try:
            real = data.mt5_resolve(sym) or sym
            mt5.symbol_select(real, True)
            i = mt5.symbol_info(real)
            rates = mt5.copy_rates_from_pos(real, mt5.TIMEFRAME_H1, 0, 20000)
            row = {"name": real, "point": i.point, "digits": i.digits, "contract": i.trade_contract_size,
                   "tick_value": i.trade_tick_value, "tick_size": i.trade_tick_size, "volume_min": i.volume_min,
                   "swap_mode": int(i.swap_mode), "swap_long": i.swap_long, "swap_short": i.swap_short,
                   "swap_3day": int(getattr(i, "swap_rollover3days", 3)), "spread_now": i.spread,
                   "currency_profit": i.currency_profit, "currency_margin": getattr(i, "currency_margin", ""),
                   "price": (i.bid + i.ask) / 2 if i.bid and i.ask else None}
            if rates is not None and len(rates):
                df = pd.DataFrame(rates)
                sp = df["spread"].astype(float)
                sp = sp[sp > 0]
                if len(sp):
                    busy = sp[df.loc[sp.index, "tick_volume"] >= df["tick_volume"].quantile(0.5)]
                    row.update(spread_median=float(sp.median()), spread_p75=float(sp.quantile(0.75)),
                               spread_busy=float(busy.median()) if len(busy) else float(sp.median()), bars=int(len(df)))
                    # spread by UTC hour of the bar (user, 3 Nov 2026: the rollover hours are far wider than the day)
                    hrs = ((df.loc[sp.index, "time"].astype("int64") // 3600 - int(round(data.mt5_utc_offset() or 0))) % 24).astype(int)
                    row["spread_by_hour_p75"] = [float(sp[hrs == h].quantile(0.75)) if (hrs == h).sum() >= 20 else None
                                                 for h in range(24)]
            out["symbols"][sym] = row
        except Exception as e:
            out["symbols"][sym] = {"error": repr(e)[:200]}
    return out


def swap_check(mt5) -> dict:
    """What the account REALLY pays overnight (user, 3 Nov 2026: is the Pro demo account swap-free?): the swap booked on
    every deal of the last 60 days and on the open positions, next to the symbol's quoted swap rate."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    deals = mt5.history_deals_get(now - timedelta(days=60), now + timedelta(days=1)) or []
    by: dict = {}
    for d in deals:
        if not d.position_id:
            continue
        r = by.setdefault(str(d.position_id), {"symbol": d.symbol, "swap": 0.0, "commission": 0.0, "profit": 0.0,
                                                "open": None, "close": None, "volume": 0.0, "type": None})
        r["swap"] += float(d.swap); r["commission"] += float(d.commission); r["profit"] += float(d.profit)
        t = datetime.fromtimestamp(d.time, timezone.utc).isoformat()
        if d.entry == 0:
            r.update(open=t, volume=float(d.volume), type="BUY" if d.type == 0 else "SELL")
        elif d.entry in (1, 3):
            r["close"] = t
    for p in mt5.positions_get() or []:
        r = by.setdefault(str(p.ticket), {"symbol": p.symbol, "commission": 0.0, "profit": 0.0, "close": None})
        r.update(swap=float(p.swap), open=datetime.fromtimestamp(p.time, timezone.utc).isoformat(), volume=float(p.volume),
                 type="BUY" if p.type == 0 else "SELL", still_open=True, profit=float(p.profit))
    rates = {}
    for r in by.values():
        i = mt5.symbol_info(r["symbol"])
        if i:
            rates[r["symbol"]] = {"swap_long": i.swap_long, "swap_short": i.swap_short, "swap_mode": int(i.swap_mode)}
    a = mt5.account_info()
    return {"time": now.isoformat(), "account": {"server": getattr(a, "server", ""), "trade_mode": getattr(a, "trade_mode", None),
            "leverage": getattr(a, "leverage", None), "name": "", "currency": getattr(a, "currency", "")},
            "positions": by, "quoted_rates": rates}


def swap_watch(c: dict, st: dict) -> None:
    """Once a day (user, 3 Nov 2026: the Pro demo account was charged no swap at all, so research runs swap-free):
    look at the swap MT5 booked on the robot's positions of the last 3 days. Any non-zero swap -> push + status flag, so
    the research can go back to charging swaps."""
    day = datetime.now(timezone.utc).date().isoformat()
    if st.get("swap_day") == day:
        return
    st["swap_day"] = day
    try:
        body = swap_check(_mt5())
        back = {v: k for k, v in load_settings().get("mt5_symbol_map", {}).items()}
        ours = lambda name: back.get(name) or (name if name in SYMBOLS else None)
        paid = {k: r for k, r in body["positions"].items() if abs(float(r.get("swap") or 0)) > 1e-9}
        charged = sorted({ours(r["symbol"]) for r in paid.values()} - {None})
        st["swap_watch"] = {"day": day, "checked": len(body["positions"]), "charged": len(paid), "symbols": charged,
                            "examples": [{"symbol": r["symbol"], "swap": round(r["swap"], 2), "type": r.get("type")} for r in list(paid.values())[:5]]}
        _log("swap_watch", **st["swap_watch"])
        # 9 Nov 2026: the swap is PER SYMBOL (Exness books it on US stocks, not on FX / metals / crypto of this account).
        # The flag file lists the symbols seen paying swap + their asset groups (one stock paying = the stock group pays);
        # config.swap_mode(sym) charges swap only there. A NEW symbol/group -> file updated + push; the commit message
        # carries [research] so the workflow re-runs the research with the new costs.
        old = ghsync.get_json("data/exness/swap_charged.json") or {}
        known = set(old.get("symbols") or [])
        groups = sorted({SYMBOLS[s].get("group") for s in set(charged) | known if s in SYMBOLS} - {None})
        if set(charged) - known:
            flag = {"since": old.get("since") or day, "updated": day, "account": body["account"].get("server"),
                    "symbols": sorted(known | set(charged)), "groups": groups, "examples": st["swap_watch"]["examples"]}
            sent = ghsync.put_file("data/exness/swap_charged.json",
                                   json.dumps(flag, ensure_ascii=False, indent=1).encode("utf-8"),
                                   f"pc: swap booked on {', '.join(sorted(set(charged) - known))} [research]")
            _push(c, "⚠️ เริ่มถูกหักค่า swap",
                  " · ".join(f"{r['symbol']} {r['swap']:+.2f}" for r in list(paid.values())[:4])
                  + (f" — กลุ่ม {', '.join(groups)} จะถูกทดสอบแบบหัก swap (เริ่มวิจัยใหม่อัตโนมัติ)" if sent
                     else " — ส่งไฟล์แจ้ง GitHub ไม่สำเร็จ จะลองใหม่พรุ่งนี้"), priority=4)
    except Exception as e:
        log.warning("swap watch: %s", e)


def run_tasks(c: dict, st: dict) -> None:
    """data/pc_tasks.json on GitHub = work Claude asks this PC to do (the cloud cannot reach the broker):
       {"id": "...", "list_symbols": true, "export_history": {"EURUSD": "EURUSD", "NAS100": "USTEC", ...}, "bars": 200000}
    Results go back to GitHub: data/exness/symbols.json, data/history_exness/{SYM}_H1.csv.gz (UTC bar times),
    data/exness/task_report.json. ONE step per call (called every 2 min, between candles only) so trading never waits."""
    import gzip
    import io
    now = pd.Timestamp.now(tz="UTC")
    if not (5 <= now.minute <= 50) or st.get("pending"):
        return
    job = st.get("task")
    if not job:
        task = ghsync.get_json("data/pc_tasks.json")
        if not task or not task.get("id") or task["id"] == st.get("task_done"):
            return
        steps = (["__list__"] if task.get("list_symbols") else []) + (["__costs__"] if task.get("cost_stats") else []) \
            + (["__swaps__"] if task.get("swap_check") else []) \
            + list((task.get("export_history") or {}).items())
        job = st["task"] = {"id": task["id"], "steps": steps, "bars": int(task.get("bars", 200000)),
                            "report": {"id": task["id"], "started": now.isoformat(), "symbols": {}}}
        _log("task", id=task["id"], steps=len(steps))
    mt5 = _mt5()
    rep = job["report"]
    rep.update(server=getattr(mt5.account_info(), "server", ""), utc_offset_h=data.mt5_utc_offset())
    if job["steps"]:
        step = job["steps"].pop(0)
        if step == "__swaps__":
            body = swap_check(mt5)
            ghsync.put_file("data/exness/swap_check.json", json.dumps(body, indent=1).encode(), "pc: real swaps charged [skip ci]")
            rep["swaps"] = len(body["positions"])
        elif step == "__costs__":
            body = broker_costs(mt5)
            ghsync.put_file("data/exness/costs.json", json.dumps(body, indent=1).encode(), "pc: Exness spreads + swaps [skip ci]")
            rep["costs"] = {k: v.get("error") or "ok" for k, v in body["symbols"].items()}
        elif step == "__list__":
            rows = [{k: getattr(i, k, None) for k in (
                "name", "path", "description", "digits", "point", "spread", "trade_contract_size", "trade_tick_value",
                "trade_tick_size", "volume_min", "volume_step", "swap_long", "swap_short", "currency_profit", "trade_mode")}
                for i in mt5.symbols_get() or []]
            ghsync.put_file("data/exness/symbols.json", json.dumps(rows, indent=0).encode(), "pc: Exness symbol list [skip ci]")
            rep["listed"] = len(rows)
        else:
            sym, name = step
            try:
                real = data.mt5_resolve(name) or name
                mt5.symbol_select(real, True)
                rates = None
                for n in (job["bars"], 150000, 100000, 60000, 30000):
                    rates = mt5.copy_rates_from_pos(real, mt5.TIMEFRAME_H1, 0, n)
                    if rates is not None and len(rates):
                        break
                if rates is None or not len(rates):
                    rep["symbols"][sym] = {"name": real, "error": str(mt5.last_error())}
                else:
                    df = pd.DataFrame(rates)
                    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True) - pd.Timedelta(hours=data.mt5_utc_offset() or 0)
                    df = df.rename(columns={"tick_volume": "volume"})[["time", "open", "high", "low", "close", "volume"]]
                    buf = io.BytesIO()
                    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as g:
                        g.write(df.to_csv(index=False, date_format="%Y-%m-%dT%H:%M:%SZ").encode())
                    ok = ghsync.put_file(f"data/history_exness/{sym}_H1.csv.gz", buf.getvalue(), f"pc: Exness H1 history {sym} [skip ci]")
                    rep["symbols"][sym] = {"name": real, "bars": len(df), "from": str(df.time.iloc[0]), "to": str(df.time.iloc[-1]), "sent": ok}
            except Exception as e:
                rep["symbols"][sym] = {"name": name, "error": repr(e)[:200]}
            _log("task_step", symbol=sym, **{k: v for k, v in rep["symbols"].get(sym, {}).items() if k != "name"})
    if not job["steps"]:
        rep["finished"] = datetime.now(timezone.utc).isoformat()
        ghsync.put_file("data/exness/task_report.json", json.dumps(rep, indent=1).encode(), "pc: task report [skip ci]")
        st["task_done"], st["task"] = job["id"], None
        _log("task_done", id=job["id"], symbols=len(rep["symbols"]))


def follow_alerts(c: dict, st: dict, pull: bool = True) -> None:
    """Trade every ACTIVE signal the PHONE got, also the ones the cloud added on its own (PC was restarting, or a
    catch-up for a missed candle). Followed while the backtest still holds it (outcome open), within 3 candles of the
    signal candle, not skipped by the risk cap; execute() then refuses to chase a price that already ran away."""
    if pull:
        ghsync.pull()
    now = pd.Timestamp.now(tz="UTC")
    if not st.get("follow_from"):                                      # never replay signals from before the first run
        st["follow_from"] = now.isoformat()
    since = pd.Timestamp(st["follow_from"])
    for s in scanner.load_signals():
        try:
            tfm = pd.Timedelta(minutes=TIMEFRAMES[s["tf"]])
            if (_tradable(c, s) and s.get("outcome") == "open" and not s.get("capped")
                    and pd.Timestamp(s["bar_time"]) + 4 * tfm > now and pd.Timestamp(s["created"]) >= since - pd.Timedelta(hours=2)):
                _queue(c, st, s, s.get("origin") or "alert")
        except Exception:
            continue


def cycle(c: dict, st: dict, new_bar: bool) -> None:
    mt5 = _mt5()
    a = mt5.account_info()
    if a is None:
        _log("error", reason=f"no account: {mt5.last_error()}")
        return
    if new_bar:
        hourly_scan(c, st)                                             # alerts + website run even if trading is refused
    if a.trade_mode == mt5.ACCOUNT_TRADE_MODE_REAL and not c["allow_live"]:
        if not st.get("warned_live"):
            _log("refused", reason="REAL account — set autotrade.allow_live = true to trade it")
            print("[STOP] REAL account - no orders are sent (set autotrade.allow_live = true in data/local.json to allow)")
            st["warned_live"] = True
        st["pending"] = []
        return
    watch_positions(c, st)
    state = _guards(c, st)
    if state == "close":
        for p in _positions(c):
            _close(c, p, f"กันล้างพอร์ต (ขาดทุนรวม {st['total_dd']}% · วันนี้ {st['day_dd']}%)")
        if st.get("guard") != "close":
            _push(c, "หยุดเทรด — ถึงจุดกันล้างพอร์ต", f"ขาดทุนรวม {st['total_dd']}% · ปิดทุกออเดอร์ของโปรแกรมแล้ว", priority=5)
        st.update(guard="close", pending=[])
        return
    if state == "pause" and st.get("guard") != "pause":
        _push(c, "หยุดเปิดออเดอร์ใหม่", f"ขาดทุนรวม {st['total_dd']}% · วันนี้ {st['day_dd']}%", priority=5)
    st["guard"] = state
    keep = []
    for job in st.get("pending", []):
        s = job["signal"]
        if state != "ok":
            _log("skip", symbol=s["symbol"], signal=s["id"], reason=f"guard {state} (ขาดทุนรวม {st['total_dd']}%)")
            _mark_done(st, s["id"])
            continue
        if time.time() < job.get("next", 0):                # waiting for the market to open again
            keep.append(job)
            continue
        res = execute(c, s, float(a.balance))
        if res == "done":
            _mark_done(st, s["id"])
        elif res == "closed":
            # 5 Nov 2026: USOIL's signal came on the bar before the daily break; 10 min of retries ran out while the
            # market was shut and the trade was lost. Wait for the reopen instead (check once a minute), as long as the
            # signal is still fresh: up to 4 candles after the signal bar, like follow_alerts. The chase rule in
            # execute() still skips it if price ran away meanwhile.
            last = pd.Timestamp(s["bar_time"]) + 5 * pd.Timedelta(minutes=TIMEFRAMES[s["tf"]])
            if pd.Timestamp.now(tz="UTC") < last:
                job["next"] = time.time() + 60
                job["until"] = max(job["until"], last.timestamp())
                keep.append(job)
            else:
                _mark_done(st, s["id"])
                _log("give_up", symbol=s["symbol"], signal=s["id"], reason="market closed until the signal went stale")
        else:
            if time.time() < job["until"]:
                keep.append(job)
            else:
                _mark_done(st, s["id"])
                _log("give_up", symbol=s["symbol"], signal=s["id"])
                _push(c, f"เปิดออเดอร์ไม่สำเร็จ {s['symbol']} {_tag(s['strategy'], s['tf'])}",
                      "ลองซ้ำ 10 นาทีแล้วไม่ผ่าน — ดูบันทึกใน data/autotrade_log.jsonl", priority=4)
    st["pending"] = keep


_upd = {"at": 0.0, "blocked": 0.0}
_poll = {"at": 0.0}
_retry = {"at": 0.0}
_single = None
CODE_PATHS = ["app", "tools", "requirements.txt"]


def _git(*args, timeout=120):
    import subprocess
    return subprocess.run(["git", *args], capture_output=True, text=True, timeout=timeout)


def code_update_available() -> bool:
    """Newer program code on GitHub? Pull it HERE first (moving aside any untracked file that blocks the pull) and only
    report True once this folder really has it, so autotrade.bat restarts into the new code - never a restart loop."""
    try:
        if _git("fetch", "-q", "origin", "main", timeout=60).returncode:
            return False
        if not _git("diff", "--name-only", "HEAD", "origin/main", "--", *CODE_PATHS).stdout.strip():
            return False
        _git("checkout", "--", "data/signals.json", "data/research/latest.json")
        r = _git("pull", "--rebase", "--autostash", "-X", "theirs")
        if r.returncode and "would be overwritten" in (r.stderr + r.stdout):
            moved = DATA_DIR / "_moved_by_update"
            moved.mkdir(exist_ok=True)
            for ln in (r.stderr + r.stdout).splitlines():
                f = DATA_DIR.parent / ln.strip()
                if ln.startswith("\t") and f.is_file():
                    f.replace(moved / f.name)
                    _log("update_moved", file=ln.strip())
            r = _git("pull", "--rebase", "--autostash", "-X", "theirs")
        if _git("diff", "--name-only", "HEAD", "origin/main", "--", *CODE_PATHS).stdout.strip():
            _log("update_failed", detail=(r.stderr or r.stdout)[-400:])
            _upd["blocked"] = time.time() + 6 * 3600
            _push(cfg(), "⚠️ อัปเดตโปรแกรมไม่สำเร็จ", "git pull ไม่ผ่าน — โปรแกรมเดิมยังทำงานต่อ จะลองใหม่ใน 6 ชม.", priority=4)
            return False
        return True
    except Exception as e:
        log.warning("update check failed: %s", e)
        return False


def ensure_autostart(c: dict) -> None:
    """User's choice (2 Nov 2026): start the auto-trader by itself after every Windows logon / restart (MT5 is opened by
    the MetaTrader5 package when the robot connects). Writes one small .cmd into the user's Startup folder."""
    import os
    import sys
    if not c.get("autostart", True) or not sys.platform.startswith("win"):
        return
    try:
        folder = os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup")
        path = os.path.join(folder, "TradingLab-autotrade.cmd")
        root = str(DATA_DIR.parent)
        body = (f'@echo off\r\nREM TradingLab auto-trader - starts after logon (remove this file to stop that)\r\n'
                f'timeout /t 60 /nobreak >NUL\r\ncd /d "{root}"\r\nstart "TradingLab auto-trader" /min cmd /c autotrade.bat\r\n')
        if not os.path.exists(path) or open(path, encoding="utf-8", errors="ignore").read() != body.replace("\r\n", "\n"):
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(body)
            _log("autostart", path=path)
            print(f"   Auto-start after logon: {path}")
    except Exception as e:
        _log("autostart_failed", reason=repr(e))


def main() -> None:
    import sys
    for f in (sys.stdout, sys.stderr):
        try:
            f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import socket
    global _single
    _single = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:                                                 # one robot per PC (Startup + a manual double-click = 2 robots)
        _single.bind(("127.0.0.1", 8899))
    except OSError:
        print("[STOP] The auto-trader is already running in another window - this one closes.")
        return
    c = cfg()
    settings = load_settings()
    if settings.get("data_source") == "free":
        print("[X] data/local.json has data_source = free - change it to auto to use MT5 prices")
        return
    if not data._mt5_init(settings):
        print("[X] Cannot connect to MT5 - open MT5, log in, then run again")
        return
    mt5 = _mt5()
    a = mt5.account_info()
    print(f"[OK] MT5: {a.server} | account {a.login} | {_kind()} | balance {a.balance:,.2f} {a.currency}")
    if not mt5.terminal_info().trade_allowed:
        print("[!] Turn on the 'Algo Trading' button in MT5 (green) - otherwise orders are rejected")
    names = {sym: data.mt5_resolve(settings["mt5_symbol_map"].get(sym, sym)) for sym in settings["mt5_symbol_map"]}
    print("   MT5 symbols: " + " | ".join(f"{k}->{v or '[NOT FOUND]'}" for k, v in names.items()))
    topic = (settings.get("notify") or {}).get("ntfy_topic") or ""
    print(f"   Phone alerts: ntfy topic = {topic or '(not set)'}  <- must be the topic your phone's ntfy app subscribes to")
    gh = ghsync.check()
    _log("github", **gh)
    print(f"   Website: {'[OK] GitHub connected (' + gh['source'] + ')' if gh['ok'] else '[!] GitHub NOT writable - ' + str(gh['source']) + ' ' + str(gh['status']) + ' ' + gh['detail'] + '  -> put github_token in data/local.json'}")
    print(f"   Alert lots use balance {float(settings['account_balance']):,.0f} USD (Settings page) | "
          f"robot lots use the real MT5 balance")
    st = read_json(STATE_FILE, {})
    st.setdefault("start_balance", float(c.get("start_balance") or a.balance))
    off = lambda v: f"{v}%" if float(v or 0) > 0 else "off"
    print(f"   Anti-wipeout: total loss {off(c['pause_total_dd_pct'])} = no new trades | {off(c['close_total_dd_pct'])} = close all "
          f"(start balance {st['start_balance']:,.2f}) | risk x{c['risk_scale']}"
          + (" | DRY RUN (no real orders)" if c["dry_run"] else ""))
    print("   Running. Scans + trades right after every H1 close. Keep this window and MT5 open. Log: data/autotrade_log.jsonl")
    ensure_autostart(c)
    _log("start", login=a.login, server=a.server, balance=a.balance, kind=_kind(), config=c)
    last_hour = st.get("last_hour")
    while True:
        try:
            c = cfg()
            if c["enabled"]:
                if mt5.terminal_info() is None:
                    data._mt5_state["ok"] = None
                    data._mt5_init(load_settings())
                now = pd.Timestamp.now(tz="UTC")
                hour = str(now.floor("h"))
                new_bar = hour != last_hour and (now - now.floor("h")).total_seconds() >= 5 and time.time() >= _retry["at"]
                if not new_bar and time.time() - _poll["at"] > 120:   # alerts the cloud sent (between candles)
                    _poll["at"] = time.time()
                    follow_alerts(c, st)
                    try:
                        run_tasks(c, st)
                    except Exception as e:
                        _log("task_error", reason=repr(e))
                cycle(c, st, new_bar)
                if new_bar and st.pop("scan_ok", False):         # a failed scan (MT5 not connected...) is retried
                    last_hour = st["last_hour"] = hour
                elif new_bar:
                    _retry["at"] = time.time() + 60
                    _log("bar", hour=hour, guard=st.get("guard"), equity=st.get("equity"), total_dd=st.get("total_dd"),
                         positions=len(_positions(c)))
                write_json(STATE_FILE, st)
                # remote updates (user's request 28 Oct 2026: Claude fixes the program while the user is away): when the
                # program code on GitHub changed, exit with code 3 -> autotrade.bat pulls the new code and starts again.
                # Checked between candles only (minute 20-50) and never with an order still waiting.
                if 20 <= now.minute <= 50 and not st.get("pending") and time.time() - _upd["at"] > 300 \
                        and time.time() > _upd["blocked"]:
                    _upd["at"] = time.time()
                    if code_update_available():
                        _log("update", reason="new program code on GitHub - restarting")
                        print("Update found on GitHub - restarting with the new code ...")
                        raise SystemExit(3)
        except KeyboardInterrupt:
            print("stopped")
            return
        except Exception as e:
            _log("error", reason=repr(e))
        try:
            time.sleep(15)
        except KeyboardInterrupt:
            print("stopped")
            return


if __name__ == "__main__":
    main()
