"""Local auto-trader: runs on the user's PC next to MetaTrader 5 (no EA) and trades the ACTIVE systems itself.

User's request (28 Oct 2026): test the TradingLab systems automatically on a DEMO account (10,000 USD) while the PC
and MT5 stay on, with the account drawdown kept under 6 % in total and 3 % per day. The cloud alerts keep running
unchanged (the user still trades by hand on other accounts).

How it works
  * every ~15 s it checks MT5; right after each H1 candle closes (broker clock) it reads the quarter's ACTIVE systems
    (data/research/latest.json from GitHub, local copy as fallback), rebuilds every system on the BROKER's own candles
    (MT5 data, so the signal is the exact candle the user sees) and applies the same rules as the backtest:
      - a new signal on a closed candle -> market order at the next candle's open (= now), SL = sl_atr x ATR14,
        TP = rr x SL distance (no TP when rr = 0)
      - an opposite signal closes this system's own position and reverses (stop-and-reverse)
  * each system's positions are tagged: magic number + comment "TL{no}-{TF}"
  * lot = risk % of the balance / SL distance (risk = the system's risk x risk_scale); below the broker's minimum lot
    the trade is skipped (user's choice); open risk per symbol is capped like the alerts (0.75 % x risk_scale)
  * account guards (equity, measured like a prop firm):
      - total: loss from the starting balance >= pause_total_dd_pct (25 %) -> no new trades; >= close_total_dd_pct (30 %) -> close all
      - daily: loss from the day's starting equity (broker midnight) — off by default (0); set the *_daily_dd_pct to use it
  * safety: trades only on a DEMO account unless allow_live = true; dry_run = true only logs.

Settings live in data/local.json -> "autotrade" (never committed). Start with autotrade.bat.
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

from . import data, notify
from .config import DATA_DIR, LATEST_RESEARCH, TIMEFRAMES, load_settings, read_json, symbol_spec, write_json
from .engine import run
from .research import live_systems
from .strategies import STRATEGIES

log = logging.getLogger("tradinglab.autotrader")

DEFAULTS = {
    "enabled": True,
    "dry_run": False,              # true = log what it would do, send nothing to MT5
    "allow_live": False,           # refuse to trade a REAL account unless set to true
    "risk_scale": 1.0,             # x each system's risk (0.25 % single, 0.10 % portfolio) = same sizing as the alerts
                                   # (history of all ACTIVE systems together: max DD ~15.7 % at x1; 0.35 was for the old 6 % limit)
    "max_symbol_risk_pct": 0.75,   # x risk_scale
    # user's rule 28 Oct 2026: no prop-style 6 % / 3 % limits any more, only "don't blow the account" (0 = off)
    "pause_total_dd_pct": 25.0,    # loss from the starting balance: stop opening new trades
    "close_total_dd_pct": 30.0,    # ... close everything
    "pause_daily_dd_pct": 0,       # daily limits off
    "close_daily_dd_pct": 0,
    "min_lot_up_to_pct": 0.0,      # 0 = skip a trade whose lot is below the broker minimum (user's choice);
                                   # e.g. 0.30 = trade the minimum lot when that risks at most 0.30 % of the balance
    "min_lot_always": "*",         # user's choice 28 Oct 2026: every symbol ("*", or a list like ["XAUUSD"]) trades at least the
                                   # minimum lot even when that risks more than planned (no per-symbol cap for those)
    "start_balance": None,         # set on the first run (the demo's starting balance)
    "magic": 5_600_000,
    "deviation_points": 30,
    "notify": True,                # push "🤖 DEMO ..." to the ntfy topic in data/local.json (if any)
    "research_url": "https://raw.githubusercontent.com/mintandmos-cell/tradinglab/main/data/research/latest.json",
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


def _push(c: dict, title: str, msg: str) -> None:
    if c.get("notify"):
        try:
            mt5 = _mt5()
            a = mt5.account_info()
            kind = "DEMO" if a is None or a.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else "REAL"
            notify.send(f"🤖 {kind} · " + title, msg, priority=3, tags=["robot"])
        except Exception:
            pass


# ------------------------------------------------------------------------------------------------ research
_research = {"at": 0.0, "data": None}


def research(c: dict) -> dict:
    """The quarter's systems: GitHub copy (refreshed hourly), local file as fallback."""
    if _research["data"] is None or time.time() - _research["at"] > 3600:
        try:
            r = requests.get(c["research_url"], timeout=60)
            r.raise_for_status()
            _research.update(data=r.json(), at=time.time())
        except Exception as e:
            log.warning("research download failed (%s) — using the local copy", e)
            _research.update(data=read_json(LATEST_RESEARCH, {"symbols": {}}), at=time.time())
    return _research["data"] or {"symbols": {}}


# ------------------------------------------------------------------------------------------------ MT5 helpers
def _mt5():
    import MetaTrader5 as mt5
    return mt5


def _tag(sid: str, tf: str) -> str:
    return f"TL{STRATEGIES[sid]['no'] if sid in STRATEGIES else sid}-{tf}"


def _positions(c: dict) -> list:
    mt5 = _mt5()
    return [p for p in (mt5.positions_get() or []) if p.magic == c["magic"]]


def _resolve(name: str) -> str | None:
    return data.mt5_resolve(name)


def _filling(info) -> int:
    mt5 = _mt5()
    fm = int(getattr(info, "filling_mode", 0) or 0)
    if fm & 1:
        return mt5.ORDER_FILLING_FOK
    if fm & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def _value_per_unit(info) -> float:
    return float(info.trade_tick_value) / float(info.trade_tick_size) if info.trade_tick_size else 0.0


def _close(c: dict, p, reason: str) -> bool:
    mt5 = _mt5()
    info = mt5.symbol_info(p.symbol)
    tick = mt5.symbol_info_tick(p.symbol)
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


def _open(c: dict, sym: str, name: str, system: dict, d: int, sl_dist: float, rr: float, balance: float,
          open_risk_usd: float) -> None:
    mt5 = _mt5()
    mt5.symbol_select(name, True)
    info, tick = mt5.symbol_info(name), mt5.symbol_info_tick(name)
    if not info or not tick:
        _log("skip", symbol=sym, reason=f"no symbol info for {name}")
        return
    price = tick.ask if d > 0 else tick.bid
    risk_pct = float(system.get("risk_pct") or load_settings().get("risk_per_trade_pct") or 0.25) * float(c["risk_scale"])
    vpu = _value_per_unit(info)
    if vpu <= 0 or sl_dist <= 0:
        _log("skip", symbol=sym, reason="bad tick value / SL distance")
        return
    raw = balance * risk_pct / 100 / (sl_dist * vpu)
    step, vmin, vmax = info.volume_step or 0.01, info.volume_min or 0.01, info.volume_max or 100
    lots = min(vmax, math.floor(raw / step + 1e-9) * step)
    tag = _tag(system["strategy"], system["tf"])
    mla = c.get("min_lot_always") or []
    forced = mla == "*" or sym in mla
    if lots < vmin:
        min_risk_pct = vmin * sl_dist * vpu / balance * 100
        if not forced and min_risk_pct > float(c.get("min_lot_up_to_pct") or 0):
            _log("skip", symbol=sym, system=tag,
                 reason=f"lot {raw:.4f} < min {vmin} (risk {risk_pct:.3f}%; {vmin} lot would risk {min_risk_pct:.2f}%)")
            return
        lots = vmin
    risk_usd = lots * sl_dist * vpu
    cap_usd = balance * float(c["max_symbol_risk_pct"]) * float(c["risk_scale"]) / 100
    if not forced and open_risk_usd + risk_usd > cap_usd + 1e-6:
        _log("skip", symbol=sym, system=tag, reason=f"symbol risk cap {cap_usd:.2f} USD reached")
        return
    digits = info.digits
    sl = round(price - d * sl_dist, digits)
    tp = round(price + d * rr * sl_dist, digits) if rr > 0 else 0.0
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": name, "volume": round(lots, 6),
           "type": mt5.ORDER_TYPE_BUY if d > 0 else mt5.ORDER_TYPE_SELL, "price": price, "sl": sl, "tp": tp,
           "deviation": c["deviation_points"], "magic": c["magic"], "comment": tag, "type_filling": _filling(info),
           "type_time": mt5.ORDER_TIME_GTC}
    if c["dry_run"]:
        _log("open_dry", symbol=sym, system=tag, side="BUY" if d > 0 else "SELL", lots=lots, price=price, sl=sl, tp=tp)
        return
    r = mt5.order_send(req)
    ok = r is not None and r.retcode == mt5.TRADE_RETCODE_DONE
    _log("open" if ok else "open_failed", symbol=sym, system=tag, side="BUY" if d > 0 else "SELL", lots=lots, price=price,
         sl=sl, tp=tp, risk_usd=round(risk_usd, 2), retcode=getattr(r, "retcode", None),
         msg=getattr(r, "comment", None) or str(mt5.last_error()))
    if ok:
        _push(c, f"{'BUY' if d > 0 else 'SELL'} {name} {lots:.2f} lot · {tag}",
              f"ราคา {price} · SL {sl}" + (f" · TP {tp}" if tp else " · ไม่มี TP") + f" · เสี่ยง {risk_usd:.2f} USD ({risk_pct:.3f}%)")


# ------------------------------------------------------------------------------------------------ guards
def _guards(c: dict, st: dict) -> str:
    """'ok' | 'pause' | 'close' from the account's equity (prop-style limits)."""
    mt5 = _mt5()
    a = mt5.account_info()
    start = float(c.get("start_balance") or st.get("start_balance") or a.balance)
    st["start_balance"] = start
    srv_day = str(data.utc_to_server(pd.DatetimeIndex([pd.Timestamp.now(tz="UTC")]))[0].date())
    if st.get("day") != srv_day:
        st.update(day=srv_day, day_equity=float(a.equity), day_note="")
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
def cycle(c: dict, st: dict, new_bar: bool) -> None:
    mt5 = _mt5()
    a = mt5.account_info()
    if a is None:
        _log("error", reason=f"no account: {mt5.last_error()}")
        return
    if a.trade_mode == mt5.ACCOUNT_TRADE_MODE_REAL and not c["allow_live"]:
        if not st.get("warned_live"):
            _log("refused", reason="REAL account — set autotrade.allow_live = true to trade it")
            st["warned_live"] = True
        return
    state = _guards(c, st)
    if state == "close":
        for p in _positions(c):
            _close(c, p, f"ถึงเพดานขาดทุน (รวม {st['total_dd']}% · วันนี้ {st['day_dd']}%)")
        if st.get("guard") != "close":
            _push(c, "หยุดเทรด — ถึงเพดานขาดทุน", f"รวม {st['total_dd']}% · วันนี้ {st['day_dd']}% · ปิดทุกออเดอร์แล้ว")
        st["guard"] = "close"
        return
    if state == "pause" and st.get("guard") != "pause":
        _push(c, "พักเปิดออเดอร์ใหม่", f"ขาดทุนรวม {st['total_dd']}% · วันนี้ {st['day_dd']}% (ใกล้เพดาน)")
    st["guard"] = state
    if not new_bar:
        return
    settings = load_settings()
    lat = research(c).get("symbols", {})
    positions = _positions(c)
    for sym, entry in lat.items():
        name = _resolve(settings["mt5_symbol_map"].get(sym, sym))
        if name is None:
            if not st.setdefault("missing", {}).get(sym):
                _log("skip", symbol=sym, reason="broker has no such symbol — set mt5_symbol_map in data/local.json")
                st["missing"][sym] = True
            continue
        for system in live_systems(entry):
            if system.get("status") != "ACTIVE" or not system.get("params"):
                continue
            if f"{sym}:{system['strategy']}:{system['tf']}" in settings.get("force_off", []) or sym in settings.get("force_off", []):
                continue
            tag = _tag(system["strategy"], system["tf"])
            try:
                df = data.get_bars(sym, system["tf"])             # closed broker candles only
                p = system["params"]
                res = run(df, system["strategy"], p, symbol_spec(sym, settings), system["tf"])
            except Exception as e:
                _log("error", symbol=sym, system=tag, reason=repr(e))
                continue
            sig = int(res["last_signal"])
            if not sig or not np.isfinite(res["atr_last"]):
                continue
            mine = [q for q in positions if q.symbol == name and q.comment == tag]
            same = [q for q in mine if (q.type == mt5.POSITION_TYPE_BUY) == (sig > 0)]
            if same:
                continue                                          # already holding this direction
            for q in mine:                                        # opposite position -> stop-and-reverse
                _close(c, q, "สัญญาณกลับทิศ")
            ot = res.get("open_trade")
            flat = ot is None and (not res["trades"] or res["trades"][-1]["exit_i"] < len(df) - 1)
            reverse = bool(ot and ot.get("reverse_pending") and sig == -ot["dir"])
            if not (flat or reverse or mine):
                continue                                          # the backtest is still in a trade from an earlier signal
            if state != "ok":
                _log("skip", symbol=sym, system=tag, reason=f"guard {state} (รวม {st['total_dd']}% · วันนี้ {st['day_dd']}%)")
                continue
            open_risk = sum(abs(q.price_open - q.sl) * _value_per_unit(mt5.symbol_info(q.symbol)) * q.volume
                            for q in _positions(c) if q.symbol == name and q.sl)
            sl_dist = float(p["sl_atr"]) * float(res["atr_last"])
            _open(c, sym, name, system, sig, sl_dist, float(p["rr"]), float(a.balance), open_risk)
            positions = _positions(c)


def _last_closed_h1(settings) -> pd.Timestamp | None:
    """Open time of the newest CLOSED H1 candle on the broker (reference: the first mapped symbol)."""
    mt5 = _mt5()
    name = settings["mt5_symbol_map"].get("EURUSD", "EURUSD")
    mt5.symbol_select(name, True)
    r = mt5.copy_rates_from_pos(name, mt5.TIMEFRAME_H1, 1, 1)          # position 1 = last closed candle
    return pd.Timestamp(int(r[0]["time"]), unit="s") if r is not None and len(r) else None


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
    if not data._mt5_init({**settings, "data_source": "mt5"}):
        print("❌ เชื่อม MT5 ไม่ได้ — เปิดโปรแกรม MT5 และล็อกอินบัญชี Demo ก่อน แล้วรันใหม่")
        return
    mt5 = _mt5()
    a = mt5.account_info()
    print(f"✅ MT5: {a.server} · บัญชี {a.login} · {'DEMO' if a.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else 'REAL'} · "
          f"ทุน {a.balance:,.2f} {a.currency}")
    if not mt5.terminal_info().trade_allowed:
        print("⚠️  ใน MT5 ให้กดปุ่ม 'Algo Trading' ให้เป็นสีเขียว ไม่งั้นส่งคำสั่งไม่ได้")
    st = read_json(STATE_FILE, {})
    if not st.get("start_balance"):
        st["start_balance"] = float(c.get("start_balance") or a.balance)
    off = lambda v: f"{v}%" if float(v or 0) > 0 else "ปิด"
    print(f"   กันล้างพอร์ต: ขาดทุนรวม {off(c['pause_total_dd_pct'])} หยุดเปิดใหม่ · {off(c['close_total_dd_pct'])} ปิดทั้งหมด "
          f"(จากทุนเริ่ม {st['start_balance']:,.2f}) · ต่อวัน {off(c['close_daily_dd_pct'])} · ความเสี่ยง x{c['risk_scale']}"
          + (" · DRY RUN (ไม่ส่งคำสั่งจริง)" if c["dry_run"] else ""))
    _log("start", login=a.login, server=a.server, balance=a.balance, demo=a.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO, config=c)
    last_bar = st.get("last_bar")
    while True:
        try:
            c = cfg()
            if not c["enabled"]:
                time.sleep(30)
                continue
            if mt5.terminal_info() is None:
                data._mt5_state["ok"] = None
                data._mt5_init({**load_settings(), "data_source": "mt5"})
            bar = _last_closed_h1(load_settings())
            new_bar = bar is not None and str(bar) != last_bar
            if new_bar:
                data._mem.clear()                                 # rebuild candles from MT5 for the new close
                time.sleep(3)                                     # let every symbol's new candle arrive
            cycle(c, st, new_bar)
            if new_bar:
                last_bar = str(bar)
                st["last_bar"] = last_bar
                _log("bar", closed=last_bar, guard=st.get("guard"), equity=st.get("equity"), total_dd=st.get("total_dd"),
                     day_dd=st.get("day_dd"), positions=len(_positions(c)))
            write_json(STATE_FILE, st)
        except KeyboardInterrupt:
            print("หยุดแล้ว")
            return
        except Exception as e:
            _log("error", reason=repr(e))
        try:
            time.sleep(15)
        except KeyboardInterrupt:
            print("หยุดแล้ว")
            return


if __name__ == "__main__":
    main()
