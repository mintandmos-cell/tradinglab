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
from .config import DATA_DIR, TIMEFRAMES, load_settings, read_json, write_json
from .strategies import STRATEGIES

log = logging.getLogger("tradinglab.autotrader")

DEFAULTS = {
    "enabled": True,
    "dry_run": False,              # true = log what it would do, send nothing to MT5
    "allow_live": False,           # refuse to trade a REAL account unless set to true
    "risk_scale": 1.0,             # x each signal's risk (0.25 % single, 0.10 % portfolio) = same sizing as the alerts
    "follow_cap": True,            # skip a signal the alert also skips (per-symbol risk cap 0.75 %, "capped" on the website),
                                   # so the robot, the phone and the website always agree
    "max_symbol_risk_pct": 0.75,   # robot's own cap, only for symbols NOT in min_lot_always
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
    if s.get("capped") and c.get("follow_cap", True):
        _log("skip", symbol=sym, system=tag, reason="capped like the alert (per-symbol risk cap)")
        return "done"
    name = data.mt5_resolve(load_settings()["mt5_symbol_map"].get(sym, sym))
    if name is None:
        _log("skip", symbol=sym, system=tag, reason="broker has no such symbol — set mt5_symbol_map in data/local.json")
        return "done"
    mt5.symbol_select(name, True)
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
    forced = _forced(c, sym)
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
    _log("open" if ok else "open_failed", symbol=sym, system=tag, signal=s["id"], side=side, lots=lots, price=fill, sl=sl,
         tp=tp, risk_usd=round(risk_usd, 2), retcode=getattr(r, "retcode", None),
         msg=getattr(r, "comment", None) or str(mt5.last_error()))
    if not ok:
        return "retry"
    _opened[str(getattr(r, "order", "") or "")] = s["id"]
    _push(c, f"{side} {name} {lots:.2f} lot · {tag}",
          f"เปิดแล้ว @ {fill:.{digits}f} · SL {sl}" + (f" · TP {tp}" if tp else " · ไม่มี TP")
          + f" · เสี่ยง {risk_usd:.2f} USD ({risk_usd / balance * 100:.2f}% ของทุน)")
    return "done"


# ------------------------------------------------------------------------------------------------ positions -> website
_opened: dict[str, str] = {}          # position ticket -> signal id, filled by execute()
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
    now = {str(p.ticket): p for p in _positions(c)}
    before = st.get("open_tickets", [])
    closed = [t for t in before if t not in now]
    for t in closed:
        row = {"ticket": int(t), "signal_id": st["tickets"].get(t), "time": datetime.now(timezone.utc).isoformat()}
        try:
            deals = mt5.history_deals_get(position=int(t)) or []
            out = [d for d in deals if d.entry in (1, 3)]                    # DEAL_ENTRY_OUT / OUT_BY
            if out:
                d = out[-1]
                row.update(symbol=d.symbol, system=d.comment or "", price=d.price, reason=REASONS.get(d.reason, str(d.reason)),
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
    if changed or force:
        publish_status(c, st)


def publish_status(c: dict, st: dict, **extra) -> bool:
    a = _mt5().account_info()
    status = {**st.get("status", {}), **extra, "time": datetime.now(timezone.utc).isoformat(), "account": _kind(),
              "balance": getattr(a, "balance", None), "equity": getattr(a, "equity", None),
              "positions": [_pos_row(st, p) for p in _positions(c)], "closed": st.get("closed", [])[:15]}
    st["status"] = {k: status[k] for k in ("last_scan_hour", "new_signals") if k in status}
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
    hb = publish_status(c, st, last_scan_hour=str(pd.Timestamp.now(tz="UTC").floor("h")), new_signals=[s["id"] for s in new])
    _log("scan", new=[s["id"] for s in new], published=pub, heartbeat=hb)
    for s in new:
        if s.get("system_status") == "ACTIVE":
            _queue(c, st, s, "pc")
    follow_alerts(c, st, pull=False)
    return new


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
            if (s.get("system_status") == "ACTIVE" and s.get("outcome") == "open" and not s.get("capped")
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
        res = execute(c, s, float(a.balance))
        if res == "done":
            _mark_done(st, s["id"])
        else:
            if time.time() < job["until"]:
                keep.append(job)
            else:
                _mark_done(st, s["id"])
                _log("give_up", symbol=s["symbol"], signal=s["id"])
                _push(c, f"เปิดออเดอร์ไม่สำเร็จ {s['symbol']} {_tag(s['strategy'], s['tf'])}",
                      "ลองซ้ำ 10 นาทีแล้วไม่ผ่าน — ดูบันทึกใน data/autotrade_log.jsonl", priority=4)
    st["pending"] = keep


_upd = {"at": 0.0}
_poll = {"at": 0.0}
CODE_PATHS = ["app", "tools", "requirements.txt"]


def code_update_available() -> bool:
    """Does origin/main have newer program code than this folder? (data files are ignored)"""
    import subprocess
    try:
        subprocess.run(["git", "fetch", "-q", "origin", "main"], capture_output=True, timeout=60, check=True)
        out = subprocess.run(["git", "diff", "--name-only", "HEAD", "origin/main", "--", *CODE_PATHS],
                             capture_output=True, text=True, timeout=30, check=True).stdout
        return bool(out.strip())
    except Exception as e:
        log.warning("update check failed: %s", e)
        return False


def main() -> None:
    import sys
    for f in (sys.stdout, sys.stderr):
        try:
            f.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
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
    print(f"   Website: {'[OK] GitHub connected - PC signals go to the website' if ghsync.token() else '[!] no GitHub token - PC signals will NOT reach the website'}")
    print(f"   Alert lots use balance {float(settings['account_balance']):,.0f} USD (Settings page) | "
          f"robot lots use the real MT5 balance")
    st = read_json(STATE_FILE, {})
    st.setdefault("start_balance", float(c.get("start_balance") or a.balance))
    off = lambda v: f"{v}%" if float(v or 0) > 0 else "off"
    print(f"   Anti-wipeout: total loss {off(c['pause_total_dd_pct'])} = no new trades | {off(c['close_total_dd_pct'])} = close all "
          f"(start balance {st['start_balance']:,.2f}) | risk x{c['risk_scale']}"
          + (" | DRY RUN (no real orders)" if c["dry_run"] else ""))
    print("   Running. Scans + trades right after every H1 close. Keep this window and MT5 open. Log: data/autotrade_log.jsonl")
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
                new_bar = hour != last_hour and (now - now.floor("h")).total_seconds() >= 5
                if not new_bar and time.time() - _poll["at"] > 120:   # alerts the cloud sent (between candles)
                    _poll["at"] = time.time()
                    follow_alerts(c, st)
                cycle(c, st, new_bar)
                if new_bar:
                    last_hour = st["last_hour"] = hour
                    _log("bar", hour=hour, guard=st.get("guard"), equity=st.get("equity"), total_dd=st.get("total_dd"),
                         positions=len(_positions(c)))
                write_json(STATE_FILE, st)
                # remote updates (user's request 28 Oct 2026: Claude fixes the program while the user is away): when the
                # program code on GitHub changed, exit with code 3 -> autotrade.bat pulls the new code and starts again.
                # Checked between candles only (minute 20-50) and never with an order still waiting.
                if 20 <= now.minute <= 50 and not st.get("pending") and time.time() - _upd["at"] > 300:
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
