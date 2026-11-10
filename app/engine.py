"""Bar-based backtest engine measured in R (multiples of initial risk).

Rules (identical in backtest, walk-forward, forward-tracking and live signals):
  * signal on bar i close -> enter at bar i+1 open, one position at a time per system
  * entry pays spread + 2x slippage + commission (all in price units) against the trader
  * SL = entry -/+ sl_atr * ATR14(i);  TP = entry +/- rr * that distance
  * if one bar touches both SL and TP, SL is assumed first (conservative)
  * optional trailing stop (params trail_atr x ATR14 at the signal bar) that follows completed bars only
  * overnight swap (the broker's charges; credits ignored) is deducted in R per rollover held
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


def cost_by_bar(times, spec: dict) -> np.ndarray:
    """Entry cost per bar: the broker's spread for the UTC hour of that bar (75th pct of ~3 years, PC export
    spread_by_hour; the rollover hours are several times wider) + 2 x slippage + commission. Flat cost when no profile."""
    base = cost_price(spec)
    byh = spec.get("spread_by_hour")
    n = len(times)
    if not byh or not n:
        return np.full(n, base)
    flat = float(spec.get("spread", 0))
    prof = np.array([float(v) if v is not None else flat for v in byh])
    hrs = pd.DatetimeIndex(times).hour.to_numpy()
    return base - flat + prof[hrs]


ROLLOVER_UTC = 21          # daily rollover ~ New York 17:00 (21:00 UTC in summer, 22:00 in winter: an hour does not matter here)


def rollover_weights(times: pd.DatetimeIndex, spec: dict) -> np.ndarray | None:
    """Per bar: weighted number of rollovers (swap nights) up to that bar's open. Crypto pays every night; other
    markets Monday-Friday with one weekday charged three times (the weekend). None when the symbol has no swap cost."""
    sw = (float(spec.get("swap_long") or 0), float(spec.get("swap_short") or 0))
    if min(sw) >= 0 or not len(times):
        return None
    t = pd.DatetimeIndex(times)
    day0 = (t[0] - pd.Timedelta(hours=ROLLOVER_UTC)).floor("D")
    dn = ((t - pd.Timedelta(hours=ROLLOVER_UTC)).floor("D") - day0).days.to_numpy()   # rollovers passed: day index
    days = pd.date_range(day0, periods=int(dn.max()) + 2, freq="D")
    wd = days.dayofweek.to_numpy()
    if spec.get("group") == "Crypto":
        w = np.ones(len(days))
    else:
        triple = int(spec.get("swap_3day", 2))                    # Monday = 0 … Wednesday = 2 (FX default)
        w = np.where(wd >= 5, 0.0, np.where(wd == triple, 3.0, 1.0))
    cum = np.cumsum(w)
    return cum[dn]


def swap_r(W, e: int, k: int, d: int, sl_dist: float, spec: dict, entry: float | None = None) -> float:
    """Swap paid by a trade held from bar e to bar k, in R. Only charges (negative swaps) count — credits are ignored,
    so the test never earns from the broker's carry (conservative).
    The broker quotes the swap in points at TODAY's price; it is financing interest, so it scales with the price: a share
    that cost 75 USD in 2020 did not pay the swap of a 344 USD share (10 Nov 2026: old years were over-charged ~4x)."""
    if W is None or k <= e:
        return 0.0
    rate = float(spec.get("swap_long") or 0) if d > 0 else float(spec.get("swap_short") or 0)
    if rate >= 0:
        return 0.0
    ref = float(spec.get("swap_ref_price") or 0)
    if entry and ref > 0:
        rate *= float(entry) / ref
    return float(-rate * (W[k] - W[e]) / sl_dist)


def hold_bars(params: dict, tf: str, rules: dict | None = None) -> int:
    if not (rules or exit_rules())["max_hold"]:
        return NO_LIMIT
    return max(1, int(params["max_hold_h"] * 60 // TIMEFRAMES[tf]))


def simulate(o, h, l, c, e: int, d: int, sl_dist: float, rr: float, H: int, cost: float, last: int | None = None,
             opp=None, trail: float = 0.0):
    """Simulate one trade entered at bar e open. Returns (exit_idx, exit_px, reason, entry, sl, tp) or None if still open.
    last  = last bar the trade may hold (Friday close, when enabled) -> "Fri";
    opp   = the strategy's signal array: the first opposite signal at bar j exits at bar j+1 open -> "Rev";
    trail = trailing-stop distance in price (0 = off): the stop follows the best price of the COMPLETED bars
            (bar k uses the extreme of bars e..k-1, never its own) and never moves back; hit -> "Trail"."""
    n = len(o)
    entry = o[e] + d * cost
    sl = entry - d * sl_dist
    tp = entry + d * rr * sl_dist if rr > 0 else None      # rr <= 0: no take-profit (exit by SL / opposite signal)
    fri = last is not None and last + 1 < e + H
    end = min(e + H, n, last + 1) if fri else min(e + H, n)
    j_opp = None
    if opp is not None:
        hits = np.flatnonzero(opp[e:end] == -d)
        if len(hits):
            j_opp = e + int(hits[0])
            end = j_opp + 1                       # SL / TP can still hit up to and including the signal bar
    hh, ll, oo = h[e:end], l[e:end], o[e:end]
    stop = np.full(len(hh), sl)
    if trail > 0 and len(hh):
        if d > 0:
            best = np.maximum.accumulate(np.concatenate(([entry], hh[:-1])))
            stop = np.maximum(sl, best - trail)
        else:
            best = np.minimum.accumulate(np.concatenate(([entry], ll[:-1])))
            stop = np.minimum(sl, best + trail)
    if d > 0:
        sl_hit, tp_hit = ll <= stop, (hh >= tp if tp is not None else np.zeros(len(hh), bool))
    else:
        sl_hit, tp_hit = hh >= stop, (ll <= tp if tp is not None else np.zeros(len(ll), bool))
    i_sl = int(np.argmax(sl_hit)) if sl_hit.any() else None
    i_tp = int(np.argmax(tp_hit)) if tp_hit.any() else None
    if i_sl is not None and (i_tp is None or i_sl <= i_tp):
        k, st_px = e + i_sl, float(stop[i_sl])
        gap = (oo[i_sl] < st_px) if d > 0 else (oo[i_sl] > st_px)
        return k, (oo[i_sl] if gap and i_sl > 0 else st_px), ("Trail" if st_px != sl else "SL"), entry, sl, tp
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
    H, rr = hold_bars(params, tf, rules), float(params["rr"])
    trail_k = float(params.get("trail_atr") or 0)
    times = df.index
    costs = cost_by_bar(times, spec)
    W = rollover_weights(times, spec)
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
        cost = float(costs[e])
        res = simulate(o, h, l, c, e, d, sl_dist, rr, H, cost, int(wlast[e]) if wlast is not None else None, opp,
                       trail_k * a[i])
        if res is None:
            entry = o[e] + d * cost
            close_by = times[e] + pd.Timedelta(minutes=TIMEFRAMES[tf] * H) if rules["max_hold"] else None
            if wcut is not None:
                close_by = min(close_by, wcut[e]) if close_by is not None else wcut[e]
            open_trade = {"signal_i": int(i), "entry_i": e, "entry_time": times[e], "dir": d, "trail": trail_k * a[i],
                          "entry": entry, "sl_dist": sl_dist, "sl": entry - d * sl_dist, "tp": entry + d * rr * sl_dist if rr > 0 else None,
                          "close_by": close_by,
                          # an opposite signal on the last closed bar: close this one and reverse at the next open
                          "reverse_pending": bool(opp is not None and n and sig[-1] == -d and n - 1 >= e)}
            break
        k, px, reason, entry, sl, tp = res
        sw = swap_r(W, e, k, d, sl_dist, spec, entry)
        trades.append({"signal_i": int(i), "entry_i": e, "exit_i": int(k), "entry_time": times[e], "exit_time": times[k],
                       "dir": d, "entry": entry, "sl": sl, "tp": tp, "exit": float(px), "reason": reason,
                       "r": d * (px - entry) / sl_dist - sw, "bars": int(k - e + (0 if reason == "Rev" else 1)),
                       "cost_r": cost / sl_dist + sw, "swap_r": sw})
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
