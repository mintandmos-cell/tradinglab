"""Bar-based backtest engine measured in R (multiples of initial risk).

Rules (identical in backtest, walk-forward, forward-tracking and live signals):
  * signal on bar i close -> enter at bar i+1 open, one position at a time per system
  * entry pays spread + 2x slippage + commission (all in price units) against the trader
  * SL = entry -/+ sl_atr * ATR14(i);  TP = entry +/- rr * that distance
  * if one bar touches both SL and TP, SL is assumed first (conservative)
  * gaps through SL fill at the bar open; exit by time at the close of the last allowed bar
  * exits are set in settings research.exit_* (user's choice, Oct 2026: TP / SL / opposite signal only):
      exit_opposite_signal  an opposite signal on bar j closes the trade at bar j+1 open ("Rev"); the new
                            opposite trade enters at that same open (stop-and-reverse)
      exit_max_hold         time exit after max_hold_h ("Time")              — off
      exit_friday_close     flat by Friday 20:00 Bangkok, no entries until Sunday 22:00 UTC ("Fri") — off
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import indicators as ta
from .config import TIMEFRAMES, load_settings
from .strategies import signals


FRIDAY_CLOSE_UTC = 13      # Friday 20:00 Bangkok: user's rule, close everything before the weekend
REOPEN_SUN_UTC = 22        # FX reopens Sunday ~22:00 UTC (Monday 05:00 Bangkok)


def friday_cutoff(ts) -> pd.Timestamp:
    """The first Friday 13:00 UTC strictly after ts (ts itself if ts is earlier on that Friday)."""
    ts = pd.Timestamp(ts)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    cut = ts.normalize() + pd.Timedelta(days=(4 - ts.dayofweek) % 7, hours=FRIDAY_CLOSE_UTC)
    return cut if ts < cut else cut + pd.Timedelta(days=7)


def weekend_blocked(ts) -> bool:
    ts = pd.Timestamp(ts)
    wd, hr = ts.dayofweek, ts.hour
    return (wd == 4 and hr >= FRIDAY_CLOSE_UTC) or wd == 5 or (wd == 6 and hr < REOPEN_SUN_UTC)


def week_guard(index: pd.DatetimeIndex, tf: str):
    """Per bar: last bar index a trade entered on that bar may still hold (its end <= next Friday cutoff;
    a huge number when the cutoff lies beyond the data), and whether a new entry on that bar is blocked."""
    idx = pd.DatetimeIndex(index)
    n = len(idx)
    if n == 0:
        return np.zeros(0, np.int64), np.zeros(0, bool), idx
    wd, hr = idx.dayofweek.to_numpy(), idx.hour.to_numpy()
    cut = idx.normalize() + pd.to_timedelta((4 - wd) % 7, unit="D") + pd.Timedelta(hours=FRIDAY_CLOSE_UTC)
    cut = cut.where(idx < cut, cut + pd.Timedelta(days=7))
    ends = idx + pd.Timedelta(minutes=TIMEFRAMES[tf])
    last = np.searchsorted(ends.asi8, cut.asi8, side="right") - 1
    last = np.where(cut.asi8 > ends.asi8[-1], np.iinfo(np.int64).max // 2, last).astype(np.int64)
    blocked = ((wd == 4) & (hr >= FRIDAY_CLOSE_UTC)) | (wd == 5) | ((wd == 6) & (hr < REOPEN_SUN_UTC))
    blocked |= last < np.arange(n)          # the entry bar itself runs past the cutoff (e.g. H4 12:00 Friday, D1 Friday)
    return last, blocked, cut


def exit_rules() -> dict:
    r = load_settings()["research"]
    return {"opposite": bool(r.get("exit_opposite_signal", True)), "max_hold": bool(r.get("exit_max_hold", False)),
            "friday": bool(r.get("exit_friday_close", False))}


NO_LIMIT = 10 ** 9


def cost_price(spec: dict) -> float:
    return float(spec.get("spread", 0)) + 2 * float(spec.get("slippage", 0)) + float(spec.get("commission", 0))


def hold_bars(params: dict, tf: str, rules: dict | None = None) -> int:
    if not (rules or exit_rules())["max_hold"]:
        return NO_LIMIT
    return max(1, int(params["max_hold_h"] * 60 // TIMEFRAMES[tf]))


def simulate(o, h, l, c, e: int, d: int, sl_dist: float, rr: float, H: int, cost: float, last: int | None = None,
             opp=None):
    """Simulate one trade entered at bar e open. Returns (exit_idx, exit_px, reason, entry, sl, tp) or None if still open.
    last = last bar the trade may hold (Friday close, when enabled) -> "Fri";
    opp  = the strategy's signal array: the first opposite signal at bar j exits at bar j+1 open -> "Rev"."""
    n = len(o)
    entry = o[e] + d * cost
    sl = entry - d * sl_dist
    tp = entry + d * rr * sl_dist
    fri = last is not None and last + 1 < e + H
    end = min(e + H, n, last + 1) if fri else min(e + H, n)
    j_opp = None
    if opp is not None:
        hits = np.flatnonzero(opp[e:end] == -d)
        if len(hits):
            j_opp = e + int(hits[0])
            end = j_opp + 1                       # SL / TP can still hit up to and including the signal bar
    hh, ll, oo = h[e:end], l[e:end], o[e:end]
    if d > 0:
        sl_hit, tp_hit = ll <= sl, hh >= tp
    else:
        sl_hit, tp_hit = hh >= sl, ll <= tp
    i_sl = int(np.argmax(sl_hit)) if sl_hit.any() else None
    i_tp = int(np.argmax(tp_hit)) if tp_hit.any() else None
    if i_sl is not None and (i_tp is None or i_sl <= i_tp):
        k = e + i_sl
        gap = (oo[i_sl] < sl) if d > 0 else (oo[i_sl] > sl)
        return k, (oo[i_sl] if gap and i_sl > 0 else sl), "SL", entry, sl, tp
    if i_tp is not None:
        return e + i_tp, tp, "TP", entry, sl, tp
    if j_opp is not None:
        return (j_opp + 1, o[j_opp + 1], "Rev", entry, sl, tp) if j_opp + 1 < n else None
    if fri and last < n:
        return last, c[last], "Fri", entry, sl, tp
    if e + H <= n:
        k = e + H - 1
        return k, c[k], "Time", entry, sl, tp
    return None  # still running at the end of data


def run(df: pd.DataFrame, strategy_id: str, params: dict, spec: dict, tf: str, with_lines: bool = False,
        delay: int = 0) -> dict:
    """delay > 0 is a robustness stress: enter that many bars after the normal entry bar."""
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = ta.atr(h, l, c, 14)
    sig, lines = signals(strategy_id, df, params)
    rules = exit_rules()
    cost, H, rr = cost_price(spec), hold_bars(params, tf, rules), float(params["rr"])
    times = df.index
    n = len(o)
    if rules["friday"]:
        wlast, wblock, wcut = week_guard(times, tf)
    else:
        wlast, wblock, wcut = None, np.zeros(n, bool), None
    opp = sig if rules["opposite"] else None
    trades, open_trade, free_from = [], None, 0
    for i in np.flatnonzero(sig):
        if not np.isfinite(a[i]) or a[i] <= 0:
            continue
        e = i + 1 + delay
        if e < free_from:                         # still in the previous trade
            continue
        if e >= n:
            break
        if wblock[e]:
            continue
        d = int(sig[i])
        sl_dist = float(params["sl_atr"]) * a[i]
        res = simulate(o, h, l, c, e, d, sl_dist, rr, H, cost, int(wlast[e]) if wlast is not None else None, opp)
        if res is None:
            entry = o[e] + d * cost
            close_by = times[e] + pd.Timedelta(minutes=TIMEFRAMES[tf] * H) if rules["max_hold"] else None
            if wcut is not None:
                close_by = min(close_by, wcut[e]) if close_by is not None else wcut[e]
            open_trade = {"signal_i": int(i), "entry_i": e, "entry_time": times[e], "dir": d,
                          "entry": entry, "sl_dist": sl_dist, "sl": entry - d * sl_dist, "tp": entry + d * rr * sl_dist,
                          "close_by": close_by,
                          # an opposite signal on the last closed bar: close this one and reverse at the next open
                          "reverse_pending": bool(opp is not None and n and sig[-1] == -d and n - 1 >= e)}
            break
        k, px, reason, entry, sl, tp = res
        trades.append({"signal_i": int(i), "entry_i": e, "exit_i": int(k), "entry_time": times[e], "exit_time": times[k],
                       "dir": d, "entry": entry, "sl": sl, "tp": tp, "exit": float(px), "reason": reason,
                       "r": d * (px - entry) / sl_dist, "bars": int(k - e + (0 if reason == "Rev" else 1)), "cost_r": cost / sl_dist})
        free_from = k if reason == "Rev" else k + 1   # a reversal re-enters at the same open it exits on
    out = {"trades": trades, "open_trade": open_trade, "last_signal": int(sig[-1]) if len(sig) else 0,
           "atr_last": float(a[-1]) if len(a) else math.nan}
    if with_lines:
        out["lines"] = lines
        out["signals"] = sig
    return out


def trades_frame(trades: list[dict]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame(columns=["entry_time", "exit_time", "dir", "r", "reason", "bars", "cost_r"])
    return pd.DataFrame(trades)
