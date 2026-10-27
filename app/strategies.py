"""Strategy library.

Each strategy turns closed bars into a signal array: +1 long, -1 short, 0 nothing, evaluated at the
bar close and executed at the next bar open. Exits are shared: ATR stop, fixed R:R target and a
maximum holding time. Grids are deliberately small — every extra combination is another chance to
fit noise, and the research report counts them.
"""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from . import indicators as ta

EXIT_PARAMS = [
    {"key": "sl_atr", "label": "SL (x ATR14)", "type": "float", "default": 2.0, "min": 0.5, "max": 6, "step": 0.1},
    {"key": "rr", "label": "TP (x ระยะ SL = R:R) · 0 = ไม่มี TP (ออกด้วย SL หรือสัญญาณกลับทิศ)", "type": "float", "default": 2.0, "min": 0, "max": 10, "step": 0.1},
    {"key": "max_hold_h", "label": "ถือสูงสุด (ชม.) — ใช้เมื่อเปิดกฎจำกัดเวลา", "type": "int", "default": 120, "min": 4, "max": 120, "step": 1},
]


def _cross_up(a, b):
    return (a > b) & (ta.shift(a) <= ta.shift(b))


def _donchian(df: pd.DataFrame, p: dict):
    c, h, l = df.close.to_numpy(), df.high.to_numpy(), df.low.to_numpy()
    hi, lo = ta.shift(ta.highest(h, p["lookback"])), ta.shift(ta.lowest(l, p["lookback"]))
    trend = ta.ema(c, 200)
    pc = ta.shift(c)
    long_ = (c > hi) & (pc <= ta.shift(hi)) & (c > trend)
    short = (c < lo) & (pc >= ta.shift(lo)) & (c < trend)
    return long_.astype(int) - short.astype(int), {"Donchian High": hi, "Donchian Low": lo, "EMA200": trend}


def _ema_cross(df, p):
    c = df.close.to_numpy()
    f, s = ta.ema(c, p["fast"]), ta.ema(c, p["slow"])
    return _cross_up(f, s).astype(int) - _cross_up(s, f).astype(int), {f"EMA{p['fast']}": f, f"EMA{p['slow']}": s}


def _rsi_reversion(df, p):
    c = df.close.to_numpy()
    r, trend = ta.rsi(c, p["rsi_len"]), ta.ema(c, 200)
    lo, hi = p["rsi_level"], 100 - p["rsi_level"]
    long_ = (r < lo) & (ta.shift(r) >= lo) & (c > trend)
    short = (r > hi) & (ta.shift(r) <= hi) & (c < trend)
    return long_.astype(int) - short.astype(int), {"EMA200": trend}


def _bb_reversion(df, p):
    c = df.close.to_numpy()
    mid, up, dn = ta.bollinger(c, p["bb_len"], p["bb_k"])
    pc = ta.shift(c)
    long_ = (c < dn) & (pc >= ta.shift(dn))
    short = (c > up) & (pc <= ta.shift(up))
    return long_.astype(int) - short.astype(int), {"BB Upper": up, "BB Mid": mid, "BB Lower": dn}


def _tsmom(df, p):
    """Sign of the N-bar return flips: a new time-series-momentum position."""
    c = df.close.to_numpy()
    roc = c / ta.shift(c, int(p["lookback"])) - 1
    pr = ta.shift(roc)
    return ((roc > 0) & (pr <= 0)).astype(int) - ((roc < 0) & (pr >= 0)).astype(int), {}


def _adx_trend(df, p):
    c, h, l = df.close.to_numpy(), df.high.to_numpy(), df.low.to_numpy()
    pdi, mdi, adx = ta.dmi(h, l, c, 14)
    strong = adx > p["adx_min"]
    return (_cross_up(pdi, mdi) & strong).astype(int) - (_cross_up(mdi, pdi) & strong).astype(int), {}


def _squeeze(df, p):
    c, h, l = df.close.to_numpy(), df.high.to_numpy(), df.low.to_numpy()
    n = int(p["bb_len"])
    mid, up, dn = ta.bollinger(c, n, 2.0)
    _, ku, kd = ta.keltner(h, l, c, n, p["kc_mult"])
    on = (up < ku) & (dn > kd)                      # Bollinger inside Keltner = volatility compressed
    fired = ta.shift(on.astype(float)) == 1
    fired &= ~on                                    # compression just released
    return (fired & (c > up)).astype(int) - (fired & (c < dn)).astype(int), {"BB Upper": up, "BB Lower": dn, "KC Upper": ku, "KC Lower": kd}


def _session(df, p):
    """Break of the Asian-session range (00:00 UTC -> range_end) during the following 5 hours, first break per day."""
    idx = df.index
    hour = idx.hour.to_numpy()
    day = idx.normalize()
    re = int(p["range_end"])
    in_rng = hour < re
    rh = pd.Series(np.where(in_rng, df.high.to_numpy(), np.nan), index=idx).groupby(day).transform("max").to_numpy()
    rl = pd.Series(np.where(in_rng, df.low.to_numpy(), np.nan), index=idx).groupby(day).transform("min").to_numpy()
    c = df.close.to_numpy()
    win = (hour >= re) & (hour < re + 5)
    raw = np.where(win & (c > rh), 1, np.where(win & (c < rl), -1, 0))
    first = pd.Series(raw != 0, index=idx).groupby(day).cumsum().to_numpy() == 1
    sig = np.where(first & (raw != 0), raw, 0)
    return sig, {"Asia High": np.where(hour >= re, rh, np.nan), "Asia Low": np.where(hour >= re, rl, np.nan)}


def _ema_pullback(df, p):
    o, c, h, l = df.open.to_numpy(), df.close.to_numpy(), df.high.to_numpy(), df.low.to_numpy()
    f, s = ta.ema(c, int(p["fast"])), ta.ema(c, int(p["slow"]))
    up = (f > s) & (c > s)
    dn = (f < s) & (c < s)
    long_ = up & (l <= f) & (c > f) & (c > o)       # dipped to the fast EMA and closed back above it, bullish bar
    short = dn & (h >= f) & (c < f) & (c < o)
    return long_.astype(int) - short.astype(int), {f"EMA{int(p['fast'])}": f, f"EMA{int(p['slow'])}": s}


def _range_fade(df, p):
    c, h, l = df.close.to_numpy(), df.high.to_numpy(), df.low.to_numpy()
    mid, ku, kd = ta.keltner(h, l, c, 20, p["kc_mult"])
    _, _, adx = ta.dmi(h, l, c, 14)
    calm = adx < p["adx_max"]
    pc = ta.shift(c)
    long_ = calm & (c < kd) & (pc >= ta.shift(kd))
    short = calm & (c > ku) & (pc <= ta.shift(ku))
    return long_.astype(int) - short.astype(int), {"KC Upper": ku, "KC Mid": mid, "KC Lower": kd}


def _macd(df, p):
    c = df.close.to_numpy()
    m = ta.ema(c, 12) - ta.ema(c, 26)
    sg = np.where(np.isnan(m), np.nan, ta.ema(np.nan_to_num(m), int(p["signal"])))
    trend = ta.ema(c, int(p["trend_len"]))
    return (_cross_up(m, sg) & (c > trend)).astype(int) - (_cross_up(sg, m) & (c < trend)).astype(int), {f"EMA{int(p['trend_len'])}": trend}


def _supertrend(df, p):
    c, h, l = df.close.to_numpy(), df.high.to_numpy(), df.low.to_numpy()
    line, d = ta.supertrend(h, l, c, 10, p["st_mult"])
    pd_ = ta.shift(d)
    return ((d > 0) & (pd_ < 0)).astype(int) - ((d < 0) & (pd_ > 0)).astype(int), {"Supertrend": line}


def _rsi_momentum(df, p):
    c = df.close.to_numpy()
    r, trend = ta.rsi(c, 14), ta.ema(c, 200)
    lv = p["rsi_level"]
    return (_cross_up(r, np.full_like(r, lv)) & (c > trend)).astype(int) - (_cross_up(np.full_like(r, 100 - lv), r) & (c < trend)).astype(int), {"EMA200": trend}


def _ichimoku(df, p):
    c, h, l = df.close.to_numpy(), df.high.to_numpy(), df.low.to_numpy()
    t_n, k_n = int(p["tenkan"]), int(p["kijun"])
    tenkan = (ta.highest(h, t_n) + ta.lowest(l, t_n)) / 2
    kijun = (ta.highest(h, k_n) + ta.lowest(l, k_n)) / 2
    span_a = ta.shift((tenkan + kijun) / 2, k_n)                      # cloud plotted k_n bars ahead = value from k_n bars ago
    span_b = ta.shift((ta.highest(h, 2 * k_n) + ta.lowest(l, 2 * k_n)) / 2, k_n)
    top, bot = np.fmax(span_a, span_b), np.fmin(span_a, span_b)
    return (_cross_up(tenkan, kijun) & (c > top)).astype(int) - (_cross_up(kijun, tenkan) & (c < bot)).astype(int), \
        {"Tenkan": tenkan, "Kijun": kijun, "Cloud A": span_a, "Cloud B": span_b}


def _inside_bar(df, p):
    c, h, l = df.close.to_numpy(), df.high.to_numpy(), df.low.to_numpy()
    h1, l1, h2, l2 = ta.shift(h), ta.shift(l), ta.shift(h, 2), ta.shift(l, 2)
    inside = (h1 < h2) & (l1 > l2)                                     # previous bar inside the bar before it
    trend = ta.ema(c, int(p["ema_len"]))
    return (inside & (c > h2) & (c > trend)).astype(int) - (inside & (c < l2) & (c < trend)).astype(int), {f"EMA{int(p['ema_len'])}": trend}


def _pin_bar(df, p):
    o, c, h, l = df.open.to_numpy(), df.close.to_numpy(), df.high.to_numpy(), df.low.to_numpy()
    n = int(p["lookback"])
    rng = np.where(h - l > 0, h - l, np.nan)
    body_lo, body_hi = np.minimum(o, c), np.maximum(o, c)
    hammer = ((body_lo - l) / rng >= 0.6) & ((c - l) / rng >= 0.66) & (l <= ta.lowest(l, n))
    star = ((h - body_hi) / rng >= 0.6) & ((h - c) / rng >= 0.66) & (h >= ta.highest(h, n))
    return hammer.astype(int) - star.astype(int), {}


def _stoch_pullback(df, p):
    c, h, l = df.close.to_numpy(), df.high.to_numpy(), df.low.to_numpy()
    hh, ll = ta.highest(h, 14), ta.lowest(l, 14)
    with np.errstate(divide="ignore", invalid="ignore"):
        k = ta.sma(100 * (c - ll) / (hh - ll), 3)
    trend = ta.ema(c, 200)
    lv = p["stoch_level"]
    return (_cross_up(k, np.full_like(k, lv)) & (c > trend)).astype(int) - (_cross_up(np.full_like(k, 100 - lv), k) & (c < trend)).astype(int), {"EMA200": trend}


def _zscore(df, p):
    c = df.close.to_numpy()
    n, k = int(p["z_len"]), p["z_k"]
    mu, sd = ta.sma(c, n), ta.stdev(c, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = (c - mu) / sd
    pz = ta.shift(z)
    return ((z > -k) & (pz <= -k)).astype(int) - ((z < k) & (pz >= k)).astype(int), {f"SMA{n}": mu, "+k": mu + k * sd, "-k": mu - k * sd}


def _prev_day(df, p):
    idx = df.index
    day = idx.normalize()
    dh = df.high.groupby(day).max()
    dl = df.low.groupby(day).min()
    pdh = pd.Series(dh.shift(1).reindex(day).to_numpy(), index=idx).to_numpy()   # yesterday's high, known all of today
    pdl = pd.Series(dl.shift(1).reindex(day).to_numpy(), index=idx).to_numpy()
    c = df.close.to_numpy()
    trend = ta.ema(c, int(p["trend_len"]))
    pc = ta.shift(c)
    raw = np.where((c > pdh) & (pc <= pdh) & (c > trend), 1, np.where((c < pdl) & (pc >= pdl) & (c < trend), -1, 0))
    first = pd.Series(raw != 0, index=idx).groupby(day).cumsum().to_numpy() == 1
    return np.where(first, raw, 0), {"Prev-day High": pdh, "Prev-day Low": pdl}


def _aroon(df, p):
    h, l = df.high.to_numpy(), df.low.to_numpy()
    n = int(p["aroon_len"])
    up = 100 * (n - ta.bars_since_extreme(h, n, True)) / n
    dn = 100 * (n - ta.bars_since_extreme(l, n, False)) / n
    return (_cross_up(up, dn) & (up >= 70)).astype(int) - (_cross_up(dn, up) & (dn >= 70)).astype(int), {}



# ----------------------------------------------------------------------------- library extension (Oct 2026, +30)
def _ohlc(df):
    return df.open.to_numpy(), df.high.to_numpy(), df.low.to_numpy(), df.close.to_numpy()


def _sig(long_, short):
    return np.nan_to_num(np.asarray(long_, float)).astype(bool).astype(int) - np.nan_to_num(np.asarray(short, float)).astype(bool).astype(int)


def _bar_hours(df) -> float:
    if len(df) < 3:
        return 1.0
    d = pd.Series(df.index[-200:]).diff().dropna().median()
    return float(d.total_seconds() / 3600) if pd.notna(d) else 1.0


def _hma(df, p):
    c = df.close.to_numpy()
    n = int(p["hma_len"])
    hma = ta.wma(2 * ta.wma(c, n // 2) - ta.wma(c, n), int(np.sqrt(n)))
    d = np.sign(hma - ta.shift(hma))
    pd_ = ta.shift(d)
    return _sig((d > 0) & (pd_ < 0), (d < 0) & (pd_ > 0)), {f"HMA{n}": hma}


def _kama(df, p):
    c = df.close.to_numpy()
    k = ta.kama(c, int(p["er_len"]))
    return _sig(_cross_up(c, k), _cross_up(k, c)), {"KAMA": k}


def _tema(df, p):
    c = df.close.to_numpy()
    def tema(n):
        e1 = ta.ema(c, n); e2 = ta.ema(np.nan_to_num(e1), n); e3 = ta.ema(np.nan_to_num(e2), n)
        out = np.array(3 * e1 - 3 * e2 + e3)
        out[: 3 * n] = np.nan
        return out
    f, s = tema(int(p["fast"])), tema(int(p["slow"]))
    return _sig(_cross_up(f, s), _cross_up(s, f)), {f"TEMA{int(p['fast'])}": f, f"TEMA{int(p['slow'])}": s}


def _vortex(df, p):
    o, h, l, c = _ohlc(df)
    n = int(p["vi_len"])
    vp, vm = np.abs(h - ta.shift(l)), np.abs(l - ta.shift(h))
    tr = np.fmax(h - l, np.fmax(np.abs(h - ta.shift(c)), np.abs(l - ta.shift(c))))
    with np.errstate(divide="ignore", invalid="ignore"):
        vip, vim = ta.rolling_sum(vp, n) / ta.rolling_sum(tr, n), ta.rolling_sum(vm, n) / ta.rolling_sum(tr, n)
    return _sig(_cross_up(vip, vim), _cross_up(vim, vip)), {}


def _cci_trend(df, p):
    o, h, l, c = _ohlc(df)
    x, lv = ta.cci(h, l, c, 20), p["cci_level"]
    return _sig(_cross_up(x, np.full_like(x, lv)), _cross_up(np.full_like(x, -lv), x)), {}


def _trix(df, p):
    c = df.close.to_numpy()
    n = int(p["trix_len"])
    e3 = np.array(ta.ema(np.nan_to_num(ta.ema(np.nan_to_num(ta.ema(c, n)), n)), n))
    e3[: 3 * n] = np.nan
    tx = 100 * (e3 / ta.shift(e3) - 1)
    sg = np.where(np.isnan(tx), np.nan, ta.ema(np.nan_to_num(tx), 9))
    return _sig(_cross_up(tx, sg) & (tx < 0), _cross_up(sg, tx) & (tx > 0)), {}


def _psar(df, p):
    o, h, l, c = _ohlc(df)
    sar, d = ta.psar(h, l, p["psar_step"], 0.2)
    trend = ta.ema(c, 200)
    pd_ = ta.shift(d)
    return _sig((d > 0) & (pd_ < 0) & (c > trend), (d < 0) & (pd_ > 0) & (c < trend)), {"PSAR": sar, "EMA200": trend}


def _keltner_breakout(df, p):
    o, h, l, c = _ohlc(df)
    mid, ku, kd = ta.keltner(h, l, c, 20, p["kc_mult"])
    trend = ta.ema(c, 200)
    return _sig(_cross_up(c, ku) & (c > trend), _cross_up(kd, c) & (c < trend)), {"KC Upper": ku, "KC Lower": kd, "EMA200": trend}


def _bb_breakout(df, p):
    c = df.close.to_numpy()
    mid, up, dn = ta.bollinger(c, 20, p["bb_k"])
    bw = (up - dn) / mid
    expanding = bw > ta.sma(bw, 50)
    return _sig(_cross_up(c, up) & expanding, _cross_up(dn, c) & expanding), {"BB Upper": up, "BB Mid": mid, "BB Lower": dn}


def _linreg(df, p):
    c = df.close.to_numpy()
    s = ta.linreg_slope(c, int(p["lr_len"]))
    ps = ta.shift(s)
    return _sig((s > 0) & (ps <= 0), (s < 0) & (ps >= 0)), {}


def _elder_impulse(df, p):
    c = df.close.to_numpy()
    e13 = ta.ema(c, 13)
    m = ta.ema(c, 12) - ta.ema(c, 26)
    hist = m - np.where(np.isnan(m), np.nan, ta.ema(np.nan_to_num(m), 9))
    green = (e13 > ta.shift(e13)) & (hist > ta.shift(hist))
    red = (e13 < ta.shift(e13)) & (hist < ta.shift(hist))
    trend = ta.ema(c, int(p["trend_len"]))
    return _sig(green & ~(ta.shift(green.astype(float)) == 1) & (c > trend), red & ~(ta.shift(red.astype(float)) == 1) & (c < trend)), {"EMA13": e13}


def _ema_align(df, p):
    c = df.close.to_numpy()
    f, m, s = ta.ema(c, 20), ta.ema(c, int(p["mid"])), ta.ema(c, 200)
    up = (f > m) & (m > s)
    dn = (f < m) & (m < s)
    return _sig(up & ~(ta.shift(up.astype(float)) == 1), dn & ~(ta.shift(dn.astype(float)) == 1)), {"EMA20": f, f"EMA{int(p['mid'])}": m, "EMA200": s}


def _heikin(df, p):
    o, h, l, c = _ohlc(df)
    ho, hh, hl, hc = ta.heikin_ashi(o, h, l, c)
    tol = 1e-9 * np.abs(hc)
    bull = (hc > ho) & (np.abs(ho - hl) <= tol)      # strong green: no lower shadow
    bear = (hc < ho) & (np.abs(hh - ho) <= tol)
    prev_bull, prev_bear = ta.shift(bull.astype(float)) == 1, ta.shift(bear.astype(float)) == 1
    trend = ta.ema(c, int(p["trend_len"]))
    return _sig(bull & ~prev_bull & (c > trend), bear & ~prev_bear & (c < trend)), {f"EMA{int(p['trend_len'])}": trend}


def _awesome(df, p):
    o, h, l, c = _ohlc(df)
    med = (h + l) / 2
    ao = ta.sma(med, 5) - ta.sma(med, 34)
    trend = ta.ema(c, int(p["trend_len"]))
    z = np.zeros_like(ao)
    return _sig(_cross_up(ao, z) & (c > trend), _cross_up(z, ao) & (c < trend)), {f"EMA{int(p['trend_len'])}": trend}


def _ultimate(df, p):
    o, h, l, c = _ohlc(df)
    pc = ta.shift(c)
    bp = c - np.fmin(l, pc)
    tr = np.fmax(h, pc) - np.fmin(l, pc)
    with np.errstate(divide="ignore", invalid="ignore"):
        a = [ta.rolling_sum(bp, n) / ta.rolling_sum(tr, n) for n in (7, 14, 28)]
    uo = 100 * (4 * a[0] + 2 * a[1] + a[2]) / 7
    lv, trend = p["uo_level"], ta.ema(c, 200)
    return _sig(_cross_up(uo, np.full_like(uo, lv)) & (c > trend), _cross_up(np.full_like(uo, 100 - lv), uo) & (c < trend)), {"EMA200": trend}


def _cci_reversion(df, p):
    o, h, l, c = _ohlc(df)
    x = ta.cci(h, l, c, 20)
    lo5, hi5 = ta.lowest(x, 5), ta.highest(x, 5)
    lv = p["cci_extreme"]
    return _sig(_cross_up(x, np.full_like(x, -100.0)) & (lo5 < -lv), _cross_up(np.full_like(x, 100.0), x) & (hi5 > lv)), {}


def _keltner_pullback(df, p):
    o, h, l, c = _ohlc(df)
    mid, ku, kd = ta.keltner(h, l, c, 20, p["kc_mult"])
    e50, e200 = ta.ema(c, 50), ta.ema(c, 200)
    return _sig((e50 > e200) & (l <= kd) & (c > kd), (e50 < e200) & (h >= ku) & (c < ku)), {"KC Upper": ku, "KC Lower": kd, "EMA200": e200}


def _three_bar(df, p):
    c = df.close.to_numpy()
    n = int(p["n_bars"])
    d = np.diff(c, prepend=np.nan)
    downs = pd.Series((d < 0).astype(float)).rolling(n, min_periods=n).sum().to_numpy()
    ups = pd.Series((d > 0).astype(float)).rolling(n, min_periods=n).sum().to_numpy()
    sma = ta.sma(c, 200)
    return _sig((ta.shift(downs) == n) & (d > 0) & (c > sma), (ta.shift(ups) == n) & (d < 0) & (c < sma)), {"SMA200": sma}


def _nr_breakout(df, p):
    o, h, l, c = _ohlc(df)
    n = int(p["nr_len"])
    r = h - l
    nr = (r == ta.lowest(r, n))                       # narrowest bar of the last n
    prev_nr = ta.shift(nr.astype(float)) == 1
    return _sig(prev_nr & (c > ta.shift(h)), prev_nr & (c < ta.shift(l))), {}


def _engulfing(df, p):
    o, h, l, c = _ohlc(df)
    po, pc = ta.shift(o), ta.shift(c)
    n = int(p["lookback"])
    at_low = np.fmin(l, ta.shift(l)) <= ta.lowest(l, n)
    at_high = np.fmax(h, ta.shift(h)) >= ta.highest(h, n)
    bull = (c > o) & (pc < po) & (c >= po) & (o <= pc) & at_low
    bear = (c < o) & (pc > po) & (c <= po) & (o >= pc) & at_high
    return _sig(bull, bear), {}


def _donchian_fade(df, p):
    o, h, l, c = _ohlc(df)
    n = int(p["lookback"])
    lo, hi = ta.shift(ta.lowest(l, n)), ta.shift(ta.highest(h, n))
    _, _, adx = ta.dmi(h, l, c, 14)
    calm = adx < 20
    return _sig(calm & (l <= lo) & (c > lo), calm & (h >= hi) & (c < hi)), {"Range High": hi, "Range Low": lo}


def _rsi_div(df, p):
    o, h, l, c = _ohlc(df)
    n = int(p["lookback"])
    r = ta.rsi(c, 14)
    out_l, out_s = np.zeros(len(c), bool), np.zeros(len(c), bool)
    m = n - 3
    if len(c) > n + 1 and m >= 3:
        wl = np.lib.stride_tricks.sliding_window_view(np.nan_to_num(l, nan=np.inf), m)     # window s .. s+m-1
        wh = np.lib.stride_tricks.sliding_window_view(np.nan_to_num(h, nan=-np.inf), m)
        i = np.arange(n - 1, len(c))                  # bar i uses the earlier low inside [i-n+1, i-3]
        s = i - n + 1
        jl = s + np.argmin(wl[s], axis=1)
        jh = s + np.argmax(wh[s], axis=1)
        new_low = l[i] <= ta.lowest(l, n)[i]
        new_high = h[i] >= ta.highest(h, n)[i]
        out_l[i] = new_low & (r[i] > r[jl] + 2) & (c[i] > o[i])
        out_s[i] = new_high & (r[i] < r[jh] - 2) & (c[i] < o[i])
    return _sig(out_l, out_s), {}


def _turn_of_month(df, p):
    idx = df.index
    day = idx.normalize()
    k = int(p["days_before"])
    me = (day + pd.offsets.BMonthEnd(0)) - pd.offsets.BDay(k)   # k business days before the month's last business day
    first_bar = np.r_[True, day[1:] != day[:-1]]
    return _sig(first_bar & (day == me.normalize()), np.zeros(len(idx), bool)), {}


def _london_fade(df, p):
    o, h, l, c = _ohlc(df)
    idx = df.index
    hrs = _bar_hours(df)
    end_h = ((idx.hour.to_numpy() + hrs) % 24).round().astype(int)
    day = idx.normalize()
    first_h = 7 - hrs + 1                              # the bar that contains 07:00 UTC (London open)
    in_ldn = idx.hour.to_numpy() >= first_h
    open7 = pd.Series(np.where(in_ldn, o, np.nan), index=idx).groupby(day).transform("first").to_numpy()
    a = ta.atr(h, l, c, 14)
    at_fix = (end_h == 16) & (hrs < 24)
    move = c - open7
    k = p["move_atr"] * a * np.sqrt(8 / max(hrs, 1))
    return _sig(at_fix & (move < -k), at_fix & (move > k)), {}


def _ny_open(df, p):
    idx = df.index
    hour = idx.hour.to_numpy()
    day = idx.normalize()
    nb = int(p["range_bars"])
    in_rng = (hour >= 13) & (hour < 13 + nb)
    rh = pd.Series(np.where(in_rng, df.high.to_numpy(), np.nan), index=idx).groupby(day).transform("max").to_numpy()
    rl = pd.Series(np.where(in_rng, df.low.to_numpy(), np.nan), index=idx).groupby(day).transform("min").to_numpy()
    c = df.close.to_numpy()
    win = (hour >= 13 + nb) & (hour < 20)
    raw = np.where(win & (c > rh), 1, np.where(win & (c < rl), -1, 0))
    first = pd.Series(raw != 0, index=idx).groupby(day).cumsum().to_numpy() == 1
    return np.where(first, raw, 0), {"NY range High": np.where(win, rh, np.nan), "NY range Low": np.where(win, rl, np.nan)}


def _weekly(df, p):
    idx = df.index
    wk = idx.normalize() - pd.to_timedelta(idx.dayofweek, unit="D")
    wh, wl = df.high.groupby(wk).max(), df.low.groupby(wk).min()
    pwh = wh.shift(1).reindex(wk).to_numpy()
    pwl = wl.shift(1).reindex(wk).to_numpy()
    c = df.close.to_numpy()
    trend = ta.ema(c, int(p["trend_len"]))
    pc = ta.shift(c)
    raw = np.where((c > pwh) & (pc <= pwh) & (c > trend), 1, np.where((c < pwl) & (pc >= pwl) & (c < trend), -1, 0))
    first = pd.Series(raw != 0, index=idx).groupby(wk).cumsum().to_numpy() == 1
    return np.where(first, raw, 0), {"Prev-week High": pwh, "Prev-week Low": pwl}


def _pivot(df, p):
    idx = df.index
    day = idx.normalize()
    g = df.groupby(day)
    H, L, C = g.high.max().shift(1), g.low.min().shift(1), g.close.last().shift(1)
    P = (H + L + C) / 3
    s1 = (2 * P - H).reindex(day).to_numpy()
    r1 = (2 * P - L).reindex(day).to_numpy()
    o, h, l, c = _ohlc(df)
    trend = ta.ema(c, int(p["trend_len"]))
    return _sig((l <= s1) & (c > s1) & (c > trend), (h >= r1) & (c < r1) & (c < trend)), {"Pivot S1": s1, "Pivot R1": r1}


def _fractal(df, p):
    o, h, l, c = _ohlc(df)
    h2 = ta.shift(h, 2)                               # the middle bar of the 5 that end at the current bar
    up = (h2 > ta.shift(h, 3)) & (h2 > ta.shift(h, 4)) & (h2 > ta.shift(h, 1)) & (h2 > h)
    l2 = ta.shift(l, 2)
    dn = (l2 < ta.shift(l, 3)) & (l2 < ta.shift(l, 4)) & (l2 < ta.shift(l, 1)) & (l2 < l)
    upl = pd.Series(np.where(up, h2, np.nan)).ffill().to_numpy()
    dnl = pd.Series(np.where(dn, l2, np.nan)).ffill().to_numpy()
    trend = ta.ema(c, int(p["trend_len"]))
    return _sig(_cross_up(c, upl) & (c > trend), _cross_up(dnl, c) & (c < trend)), {"Fractal High": upl, "Fractal Low": dnl}


def _alligator(df, p):
    o, h, l, c = _ohlc(df)
    med = (h + l) / 2
    k = p["scale"]
    jaw = ta.shift(ta.rma(med, int(13 * k)), int(8 * k))
    teeth = ta.shift(ta.rma(med, int(8 * k)), int(5 * k))
    lips = ta.shift(ta.rma(med, int(5 * k)), int(3 * k))
    up = (lips > teeth) & (teeth > jaw) & (c > lips)
    dn = (lips < teeth) & (teeth < jaw) & (c < lips)
    return _sig(up & ~(ta.shift(up.astype(float)) == 1), dn & ~(ta.shift(dn.astype(float)) == 1)), {"Jaw": jaw, "Teeth": teeth, "Lips": lips}


def _elder_ray(df, p):
    o, h, l, c = _ohlc(df)
    n = int(p["ema_len"])
    e = ta.ema(c, n)
    bull, bear = h - e, l - e
    rising, falling = e > ta.shift(e), e < ta.shift(e)
    z = np.zeros(len(c))
    return _sig(rising & (bear < 0) & _cross_up(bear, ta.shift(bear)) & (ta.shift(bear) < 0) & (bull > 0) & _cross_up(c, e),
                falling & (bull > 0) & (bull < ta.shift(bull)) & (bear < 0) & _cross_up(e, c)), {f"EMA{n}": e}


# ---------------------------------------------------------------- research-backed additions (Oct 2026, user's request)
def _bars_per_month(df) -> int:
    """~520 trading hours a month (FX / gold / indices ~22 days x 24 h); D1 -> ~22 bars, H4 -> ~130, H1 -> ~520."""
    return max(1, int(round(520 / max(_bar_hours(df), 1e-9))))


def _flip(state):
    """Signal on the bar where a +1/-1 state changes sign (0 = no opinion keeps the previous state)."""
    st = pd.Series(np.asarray(state, float)).replace(0, np.nan).ffill().fillna(0).to_numpy()
    prev = ta.shift(st)
    prev = np.where(np.isnan(prev), 0, prev)
    return _sig((st > 0) & (prev <= 0), (st < 0) & (prev >= 0))


EWMAC_SETS = {1: [(16, 64)], 2: [(32, 128)], 3: [(16, 64), (32, 128), (64, 256)]}


def _ewmac(df, p):
    """Carver (2015) EWMAC: (EMA fast - EMA slow) / price volatility, several speeds averaged; trade the sign."""
    c = df.close.to_numpy()
    vol = ta.stdev(np.diff(c, prepend=np.nan), 36)
    fc = np.zeros(len(c))
    for f, s_ in EWMAC_SETS[int(p["speeds"])]:
        with np.errstate(divide="ignore", invalid="ignore"):
            fc = fc + (ta.ema(c, f) - ta.ema(c, s_)) / vol
    fc = np.where(np.isfinite(fc), fc, 0)
    f0, s0 = EWMAC_SETS[int(p["speeds"])][0]
    return _flip(np.sign(fc)), {f"EMA{f0}": ta.ema(c, f0), f"EMA{s0}": ta.ema(c, s0)}


def _tsmom_blend(df, p):
    """Hurst, Ooi & Pedersen (2017) / Moskowitz et al. (2012): average sign of the 1-, 3- and 12-month returns."""
    c = df.close.to_numpy()
    m = _bars_per_month(df)
    looks = [1, 3, 12] if int(p["mode"]) == 0 else [3, 12]
    vote = np.zeros(len(c))
    for k in looks:
        with np.errstate(divide="ignore", invalid="ignore"):
            r = c / ta.shift(c, k * m) - 1
        vote = vote + np.sign(np.where(np.isfinite(r), r, 0))
    return _flip(np.sign(vote)), {}


def _turtle(df, p):
    """Turtle-style always-in breakout (Faith 2007): long on an N-bar high, short on an N-bar low, no trend filter."""
    c, h, l = df.close.to_numpy(), df.high.to_numpy(), df.low.to_numpy()
    n = int(p["entry_n"])
    hi, lo = ta.shift(ta.highest(h, n)), ta.shift(ta.lowest(l, n))
    state = np.where(c > hi, 1, np.where(c < lo, -1, 0))
    return _flip(state), {"Channel High": hi, "Channel Low": lo}


def _faber(df, p):
    """Faber (2007) tactical timing: price above / below its ~10-month simple moving average."""
    c = df.close.to_numpy()
    n = int(p["months"]) * _bars_per_month(df)
    ma = ta.sma(c, n)
    state = np.where(np.isnan(ma), 0, np.where(c > ma, 1, -1))
    return _flip(state), {f"SMA {int(p['months'])}m": ma}


def _clenow(df, p):
    """Clenow (2013) 'Following the Trend': EMA50 vs EMA100 regime, enter on an N-bar breakout in that direction."""
    c, h, l = df.close.to_numpy(), df.high.to_numpy(), df.low.to_numpy()
    e50, e100 = ta.ema(c, 50), ta.ema(c, 100)
    n = int(p["breakout_n"])
    hi, lo = ta.shift(ta.highest(h, n)), ta.shift(ta.lowest(l, n))
    pc = ta.shift(c)
    return _sig((e50 > e100) & (c > hi) & (pc <= ta.shift(hi)), (e50 < e100) & (c < lo) & (pc >= ta.shift(lo))), \
        {"EMA50": e50, "EMA100": e100}


def _vol_breakout(df, p):
    """Crabel (1990) / L. Williams volatility breakout: a close more than k x ATR away from the previous close."""
    c, h, l = df.close.to_numpy(), df.high.to_numpy(), df.low.to_numpy()
    a = ta.shift(ta.atr(h, l, c, 14))
    pc = ta.shift(c)
    k = float(p["k_atr"])
    trend = ta.ema(c, 100)
    return _sig((c > pc + k * a) & (c > trend), (c < pc - k * a) & (c < trend)), {"EMA100": trend}


def _high52(df, p):
    """George & Hwang (2004) 52-week-high anchor: break of the 6- / 12-month high (low) in trend direction."""
    c, h, l = df.close.to_numpy(), df.high.to_numpy(), df.low.to_numpy()
    n = int(p["months"]) * _bars_per_month(df)
    hi, lo = ta.shift(ta.highest(h, n)), ta.shift(ta.lowest(l, n))
    pc = ta.shift(c)
    return _sig((c > hi) & (pc <= ta.shift(hi)), (c < lo) & (pc >= ta.shift(lo))), {"High N": hi, "Low N": lo}


STRATEGIES: dict[str, dict] = {
    "donchian_trend": {
        "name": "Donchian Breakout + EMA200",
        "family": "Trend following",
        "rationale": "ตามแนวโน้ม: ราคาทะลุกรอบสูง/ต่ำ N แท่ง ในทิศเดียวกับ EMA200 — อาศัย time-series momentum ซึ่งมีหลักฐานข้ามหลายตลาดและหลายทศวรรษ",
        "fn": _donchian,
        "params": [{"key": "lookback", "label": "Lookback (แท่ง)", "type": "int", "default": 40, "min": 5, "max": 200, "step": 1}],
        "grid": {"lookback": [20, 40, 60], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    "ema_cross": {
        "name": "EMA Crossover",
        "family": "Trend following",
        "rationale": "ตามแนวโน้มแบบช้า: เส้นเร็วตัดเส้นช้า — เข้าช้ากว่าแต่กรองการแกว่งได้มาก",
        "fn": _ema_cross,
        "params": [{"key": "fast", "label": "EMA เร็ว", "type": "int", "default": 20, "min": 2, "max": 100, "step": 1},
                   {"key": "slow", "label": "EMA ช้า", "type": "int", "default": 100, "min": 10, "max": 400, "step": 1}],
        "grid": {"fast": [10, 20], "slow": [50, 100], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    "rsi_reversion": {
        "name": "RSI Pullback in Trend",
        "family": "Mean reversion",
        "rationale": "ซื้อย่อในขาขึ้น/ขายเด้งในขาลง: RSI สั้นสุดโต่ง ขณะราคายังอยู่ฝั่งเดียวกับ EMA200 — short-term reversal ภายในแนวโน้มใหญ่",
        "fn": _rsi_reversion,
        "params": [{"key": "rsi_len", "label": "RSI Length", "type": "int", "default": 3, "min": 2, "max": 30, "step": 1},
                   {"key": "rsi_level", "label": "RSI ระดับ Oversold (Overbought = 100 - ค่านี้)", "type": "float", "default": 15, "min": 2, "max": 45, "step": 1}],
        "grid": {"rsi_len": [3], "rsi_level": [10, 20], "sl_atr": [1.5, 2.5], "rr": [1.0, 1.5]},
    },
    "bb_reversion": {
        "name": "Bollinger Band Fade",
        "family": "Mean reversion",
        "rationale": "สวนการยืดตัว: ปิดหลุดแบนด์ k ส่วนเบี่ยงเบน แล้วคาดว่ากลับเข้าหาค่าเฉลี่ย — ใช้ได้ในตลาดที่แกว่งตัวในกรอบ",
        "fn": _bb_reversion,
        "params": [{"key": "bb_len", "label": "BB Length", "type": "int", "default": 20, "min": 5, "max": 100, "step": 1},
                   {"key": "bb_k", "label": "BB Std (k)", "type": "float", "default": 2.0, "min": 1, "max": 4, "step": 0.1}],
        "grid": {"bb_len": [20], "bb_k": [2.0, 2.5], "sl_atr": [1.5, 2.5], "rr": [1.0, 1.5]},
    },
    "tsmom": {
        "name": "Time-Series Momentum",
        "family": "Trend following",
        "rationale": "ผลตอบแทนย้อนหลัง N แท่งเปลี่ยนเครื่องหมาย = แนวโน้มเปลี่ยนทิศ — Moskowitz, Ooi & Pedersen (2012) พบ time-series momentum ใน 58 ตลาด (FX, ดัชนี, สินค้าโภคภัณฑ์, พันธบัตร)",
        "fn": _tsmom,
        "params": [{"key": "lookback", "label": "Lookback (แท่ง)", "type": "int", "default": 120, "min": 10, "max": 500, "step": 1}],
        "grid": {"lookback": [60, 120, 240], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    "adx_trend": {
        "name": "ADX + DI Crossover",
        "family": "Trend following",
        "rationale": "เข้าตามแนวโน้มเฉพาะเมื่อแนวโน้มแรงจริง (ADX สูง) และ +DI/−DI ตัดกัน — J. Welles Wilder (1978) ตัดช่วงตลาดไซด์เวย์ที่ระบบตามแนวโน้มมักเสียเงิน",
        "fn": _adx_trend,
        "params": [{"key": "adx_min", "label": "ADX ขั้นต่ำ", "type": "float", "default": 20, "min": 10, "max": 40, "step": 1}],
        "grid": {"adx_min": [20, 25], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    "squeeze_breakout": {
        "name": "Volatility Squeeze Breakout",
        "family": "Volatility breakout",
        "rationale": "ความผันผวนมักเกาะกลุ่ม (volatility clustering): ช่วงบีบตัว (Bollinger อยู่ในกรอบ Keltner) มักตามด้วยการขยายตัว เข้าตามทิศที่ราคาหลุดกรอบ — แนวคิด TTM Squeeze (John Carter)",
        "fn": _squeeze,
        "params": [{"key": "bb_len", "label": "BB/KC Length", "type": "int", "default": 20, "min": 10, "max": 60, "step": 1},
                   {"key": "kc_mult", "label": "Keltner (x ATR)", "type": "float", "default": 1.5, "min": 1, "max": 3, "step": 0.1}],
        "grid": {"bb_len": [20], "kc_mult": [1.5, 2.0], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    "session_breakout": {
        "name": "Asian Range Breakout",
        "family": "Session / liquidity",
        "rationale": "ช่วงเอเชียสภาพคล่องต่ำ ราคามักอยู่ในกรอบแคบ เมื่อตลาดลอนดอนเปิด (สภาพคล่องเข้ามามาก) การทะลุกรอบเอเชียมักไปต่อ — กลยุทธ์ session breakout ที่นิยมใน FX/ทอง (ใช้ได้ดีที่สุดบน H1)",
        "fn": _session,
        "params": [{"key": "range_end", "label": "กรอบเอเชียสิ้นสุด (ชม. UTC)", "type": "int", "default": 7, "min": 4, "max": 9, "step": 1}],
        "grid": {"range_end": [6, 7], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    "ema_pullback": {
        "name": "EMA Pullback in Trend",
        "family": "Pullback",
        "rationale": "ในแนวโน้มที่ชัด (EMA เร็วอยู่ฝั่งเดียวกับ EMA ช้า) ราคาย่อมาแตะ EMA เร็วแล้วปิดกลับทิศเดิม = จังหวะเข้าที่ราคาดีกว่าการไล่ breakout — แนวคิดเทรดตามแนวโน้มแบบรอย่อ (Linda Raschke / Al Brooks)",
        "fn": _ema_pullback,
        "params": [{"key": "fast", "label": "EMA เร็ว", "type": "int", "default": 20, "min": 5, "max": 100, "step": 1},
                   {"key": "slow", "label": "EMA ช้า", "type": "int", "default": 200, "min": 50, "max": 400, "step": 1}],
        "grid": {"fast": [20, 50], "slow": [200], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    "range_fade": {
        "name": "Keltner Fade (no-trend regime)",
        "family": "Mean reversion",
        "rationale": "สวนการยืดตัวเฉพาะตอนตลาดไม่มีแนวโน้ม (ADX ต่ำ): ราคาหลุดกรอบ Keltner แล้วมักกลับเข้าหาค่าเฉลี่ย — แยก regime ก่อน เพราะ mean reversion เสียหนักในช่วงเทรนด์แรง",
        "fn": _range_fade,
        "params": [{"key": "kc_mult", "label": "Keltner (x ATR20)", "type": "float", "default": 2.0, "min": 1, "max": 4, "step": 0.1},
                   {"key": "adx_max", "label": "ADX สูงสุด (ไม่มีเทรนด์)", "type": "float", "default": 20, "min": 10, "max": 35, "step": 1}],
        "grid": {"kc_mult": [1.5, 2.0], "adx_max": [20, 25], "sl_atr": [1.5, 2.5], "rr": [1.0, 1.5]},
    },
    "macd_trend": {
        "name": "MACD Cross + Trend Filter",
        "family": "Trend following",
        "rationale": "MACD (Gerald Appel) ตัดเส้นสัญญาณ = โมเมนตัมระยะสั้นเปลี่ยนทิศ รับเฉพาะทิศเดียวกับ EMA ยาว เพื่อตามแนวโน้มใหญ่และตัดสัญญาณสวนเทรนด์",
        "fn": _macd,
        "params": [{"key": "signal", "label": "MACD Signal", "type": "int", "default": 9, "min": 3, "max": 30, "step": 1},
                   {"key": "trend_len", "label": "EMA แนวโน้ม", "type": "int", "default": 200, "min": 50, "max": 400, "step": 1}],
        "grid": {"signal": [9], "trend_len": [100, 200], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    "supertrend": {
        "name": "Supertrend Flip",
        "family": "Trend following",
        "rationale": "เส้นตามแนวโน้มแบบ ATR trailing (Olivier Seban): กลับทิศเมื่อราคาปิดทะลุเส้น = แนวโน้มเปลี่ยน ระยะปรับตามความผันผวนเอง",
        "fn": _supertrend,
        "params": [{"key": "st_mult", "label": "ตัวคูณ ATR", "type": "float", "default": 3.0, "min": 1, "max": 6, "step": 0.1}],
        "grid": {"st_mult": [2.0, 3.0], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    "rsi_momentum": {
        "name": "RSI Momentum Regime",
        "family": "Momentum",
        "rationale": "RSI ทะลุโซน 55–60 ในทิศเดียวกับ EMA200 = แรงซื้อเข้าสู่ “โหมดขาขึ้น” (แนวคิด RSI range shift ของ Andrew Cardwell / Constance Brown) ต่างจากการใช้ RSI สวนเทรนด์",
        "fn": _rsi_momentum,
        "params": [{"key": "rsi_level", "label": "ระดับ RSI (ขาลง = 100 - ค่านี้)", "type": "float", "default": 55, "min": 51, "max": 70, "step": 1}],
        "grid": {"rsi_level": [55, 60], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    "ichimoku_tk": {
        "name": "Ichimoku TK Cross + Cloud",
        "family": "Trend following",
        "rationale": "Tenkan ตัด Kijun ขณะราคาอยู่นอกเมฆ (Goichi Hosoda) = แนวโน้มระยะสั้นและกลางสอดคล้องกัน นิยมใน FX และทองในตลาดเอเชีย",
        "fn": _ichimoku,
        "params": [{"key": "tenkan", "label": "Tenkan", "type": "int", "default": 9, "min": 5, "max": 30, "step": 1},
                   {"key": "kijun", "label": "Kijun", "type": "int", "default": 26, "min": 10, "max": 80, "step": 1}],
        "grid": {"tenkan": [9], "kijun": [26], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    "inside_bar": {
        "name": "Inside Bar Breakout",
        "family": "Price action",
        "rationale": "แท่งในกรอบ (inside bar) = ความลังเลและความผันผวนหด การทะลุกรอบแท่งแม่ตามทิศ EMA มักไปต่อ — รูปแบบ price action ที่ Toby Crabel ศึกษาเรื่องช่วงแคบก่อนขยายตัว",
        "fn": _inside_bar,
        "params": [{"key": "ema_len", "label": "EMA แนวโน้ม", "type": "int", "default": 50, "min": 20, "max": 300, "step": 1}],
        "grid": {"ema_len": [50, 200], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    "pin_bar": {
        "name": "Pin Bar Rejection",
        "family": "Price action",
        "rationale": "ไส้ยาวทำจุดต่ำสุด/สูงสุดใหม่ของ N แท่งแล้วปิดกลับ = ราคาถูกปฏิเสธ (stop-run แล้วกลับตัว) เทรดสวนการยืดตัวระยะสั้น",
        "fn": _pin_bar,
        "params": [{"key": "lookback", "label": "จุดสุดของกี่แท่ง", "type": "int", "default": 20, "min": 5, "max": 100, "step": 1}],
        "grid": {"lookback": [20, 50], "sl_atr": [1.5, 2.5], "rr": [1.0, 1.5]},
    },
    "stoch_pullback": {
        "name": "Stochastic Pullback in Trend",
        "family": "Pullback",
        "rationale": "ในขาขึ้น (เหนือ EMA200) รอ Stochastic (George Lane) ลงโซนต่ำแล้วตัดขึ้น = ย่อจบแล้ว กลับเข้าทิศแนวโน้ม",
        "fn": _stoch_pullback,
        "params": [{"key": "stoch_level", "label": "ระดับ Stoch (ขาลง = 100 - ค่านี้)", "type": "float", "default": 20, "min": 5, "max": 40, "step": 1}],
        "grid": {"stoch_level": [20, 30], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    "zscore_reversion": {
        "name": "Z-Score Snap-back",
        "family": "Mean reversion",
        "rationale": "ราคาห่างค่าเฉลี่ยเกิน k ส่วนเบี่ยงเบนแล้วเริ่มกลับเข้า = การยืดตัวสุดโต่งจบลง (short-term reversal ที่พบในงานวิจัยหลายตลาด) เข้าเมื่อเริ่มกลับ ไม่รับมีดที่กำลังตก",
        "fn": _zscore,
        "params": [{"key": "z_len", "label": "ช่วงค่าเฉลี่ย", "type": "int", "default": 20, "min": 10, "max": 200, "step": 1},
                   {"key": "z_k", "label": "Z ระดับ", "type": "float", "default": 2.0, "min": 1, "max": 4, "step": 0.1}],
        "grid": {"z_len": [20, 50], "z_k": [2.0, 2.5], "sl_atr": [1.5, 2.5], "rr": [1.0, 1.5]},
    },
    "prev_day_breakout": {
        "name": "Previous-Day High/Low Breakout",
        "family": "Session / liquidity",
        "rationale": "High/Low ของเมื่อวานคือระดับที่เทรดเดอร์ทั่วโลกเฝ้าดูและวาง stop ไว้ เมื่อทะลุตามทิศแนวโน้ม stop ที่ถูกกินช่วยดันราคาต่อ — ครั้งแรกของวันเท่านั้น",
        "fn": _prev_day,
        "params": [{"key": "trend_len", "label": "EMA แนวโน้ม", "type": "int", "default": 200, "min": 50, "max": 400, "step": 1}],
        "grid": {"trend_len": [100, 200], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    "aroon_trend": {
        "name": "Aroon Crossover",
        "family": "Trend following",
        "rationale": "Aroon (Tushar Chande) วัดว่าจุดสูง/ต่ำสุดล่าสุดเกิดเร็วแค่ไหน Aroon Up ตัดขึ้นเหนือ Down และ ≥ 70 = แนวโน้มใหม่เริ่มต้นชัดเจน",
        "fn": _aroon,
        "params": [{"key": "aroon_len", "label": "Aroon Length", "type": "int", "default": 25, "min": 10, "max": 100, "step": 1}],
        "grid": {"aroon_len": [25, 50], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'hma_trend': {
        "name": 'Hull MA Turn',
        "family": 'Trend following',
        "rationale": 'Hull Moving Average (Alan Hull) ตอบสนองเร็วแต่เรียบ: ความชันกลับทิศ = แนวโน้มระยะสั้นเปลี่ยน',
        "fn": _hma,
        "params": [{"key": "hma_len", "label": "HMA Length", "type": "int", "default": 50, "min": 10, "max": 200, "step": 1}],
        "grid": {"hma_len": [20, 50], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'kama_cross': {
        "name": 'KAMA Cross',
        "family": 'Trend following',
        "rationale": 'Kaufman Adaptive MA ช้าลงเองเมื่อตลาดแกว่ง เร็วขึ้นเมื่อมีทิศทาง (Perry Kaufman) ราคาตัด KAMA = แนวโน้มเริ่ม โดยลดสัญญาณหลอกในช่วงไซด์เวย์',
        "fn": _kama,
        "params": [{"key": "er_len", "label": "Efficiency Ratio Length", "type": "int", "default": 10, "min": 5, "max": 50, "step": 1}],
        "grid": {"er_len": [10, 20], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'tema_cross': {
        "name": 'TEMA Crossover',
        "family": 'Trend following',
        "rationale": 'Triple EMA (Patrick Mulloy) ลดความหน่วงของเส้นค่าเฉลี่ย: เส้นเร็วตัดเส้นช้าเร็วกว่า EMA ธรรมดา',
        "fn": _tema,
        "params": [{"key": "fast", "label": "TEMA เร็ว", "type": "int", "default": 10, "min": 3, "max": 50, "step": 1}, {"key": "slow", "label": "TEMA ช้า", "type": "int", "default": 50, "min": 20, "max": 200, "step": 1}],
        "grid": {"fast": [10, 20], "slow": [50], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'vortex': {
        "name": 'Vortex Indicator Cross',
        "family": 'Trend following',
        "rationale": 'Vortex (Botes & Siepman, 2010) วัดแรงเคลื่อนขึ้น/ลงเทียบช่วงราคาจริง: VI+ ตัด VI− = แนวโน้มใหม่',
        "fn": _vortex,
        "params": [{"key": "vi_len", "label": "VI Length", "type": "int", "default": 14, "min": 7, "max": 50, "step": 1}],
        "grid": {"vi_len": [14, 21], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'cci_trend': {
        "name": 'CCI Breakout',
        "family": 'Momentum',
        "rationale": 'CCI (Donald Lambert) ทะลุ ±100–150 = ราคาเคลื่อนห่างค่าปกติแรงผิดปกติ ใช้เป็นสัญญาณโมเมนตัมเริ่มต้นแนวโน้ม',
        "fn": _cci_trend,
        "params": [{"key": "cci_level", "label": "ระดับ CCI", "type": "float", "default": 100, "min": 50, "max": 250, "step": 5}],
        "grid": {"cci_level": [100, 150], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'trix_cross': {
        "name": 'TRIX Signal Cross',
        "family": 'Momentum',
        "rationale": 'TRIX (Jack Hutson) = อัตราเปลี่ยนของ EMA สามชั้น กรองสัญญาณรบกวนสูง ตัดเส้นสัญญาณจากฝั่งตรงข้ามศูนย์ = โมเมนตัมกลับตัว',
        "fn": _trix,
        "params": [{"key": "trix_len", "label": "TRIX Length", "type": "int", "default": 15, "min": 5, "max": 60, "step": 1}],
        "grid": {"trix_len": [15, 30], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'psar_flip': {
        "name": 'Parabolic SAR Flip',
        "family": 'Trend following',
        "rationale": 'Parabolic SAR (J. Welles Wilder, 1978) จุดหยุดที่เร่งตามเวลา: กลับทิศในทางเดียวกับ EMA200 = แนวโน้มกลับมาต่อ',
        "fn": _psar,
        "params": [{"key": "psar_step", "label": "SAR Step", "type": "float", "default": 0.02, "min": 0.005, "max": 0.1, "step": 0.005}],
        "grid": {"psar_step": [0.01, 0.02], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'keltner_breakout': {
        "name": 'Keltner Channel Breakout',
        "family": 'Volatility breakout',
        "rationale": 'ราคาปิดทะลุกรอบ Keltner (Chester Keltner / Linda Raschke) ตามทิศ EMA200 = การขยายตัวของความผันผวนในทิศแนวโน้ม',
        "fn": _keltner_breakout,
        "params": [{"key": "kc_mult", "label": "Keltner (x ATR20)", "type": "float", "default": 2.0, "min": 1, "max": 4, "step": 0.1}],
        "grid": {"kc_mult": [1.5, 2.0], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'bb_breakout': {
        "name": 'Bollinger Expansion Breakout',
        "family": 'Volatility breakout',
        "rationale": 'ปิดนอก Bollinger Band ขณะแบนด์กำลังกว้างขึ้น (John Bollinger: band walk) = เริ่มแนวโน้มแรง ต่างจาก BB Fade ที่สวนทาง',
        "fn": _bb_breakout,
        "params": [{"key": "bb_k", "label": "BB Std (k)", "type": "float", "default": 2.0, "min": 1, "max": 3.5, "step": 0.1}],
        "grid": {"bb_k": [2.0, 2.5], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'linreg_slope': {
        "name": 'Linear Regression Slope',
        "family": 'Trend following',
        "rationale": 'ความชันของเส้นถดถอยเชิงเส้น N แท่งเปลี่ยนเครื่องหมาย = ทิศทางเชิงสถิติของราคาเปลี่ยน (วิธี time-series trend ที่ใช้ใน CTA)',
        "fn": _linreg,
        "params": [{"key": "lr_len", "label": "Regression Length", "type": "int", "default": 50, "min": 10, "max": 300, "step": 1}],
        "grid": {"lr_len": [50, 100], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'elder_impulse': {
        "name": 'Elder Impulse System',
        "family": 'Momentum',
        "rationale": 'Impulse System (Alexander Elder): EMA13 และ MACD histogram ขึ้นพร้อมกัน = แรงซื้อและแนวโน้มสอดคล้อง เข้าแท่งแรกที่เปลี่ยนเป็นเขียว',
        "fn": _elder_impulse,
        "params": [{"key": "trend_len", "label": "EMA แนวโน้ม", "type": "int", "default": 200, "min": 50, "max": 400, "step": 1}],
        "grid": {"trend_len": [100, 200], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'ema_alignment': {
        "name": 'EMA 20/50/200 Alignment',
        "family": 'Trend following',
        "rationale": 'เส้นค่าเฉลี่ยเรียงตัวครบ (20 > กลาง > 200) ครั้งแรก = แนวโน้มทุกระดับเวลาเห็นตรงกัน',
        "fn": _ema_align,
        "params": [{"key": "mid", "label": "EMA กลาง", "type": "int", "default": 50, "min": 30, "max": 150, "step": 1}],
        "grid": {"mid": [50, 100], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'heikin_ashi': {
        "name": 'Heikin-Ashi Trend Start',
        "family": 'Trend following',
        "rationale": 'แท่ง Heikin-Ashi สีเขียวไม่มีไส้ล่าง = แรงซื้อเต็มแท่ง แท่งแรกหลังเปลี่ยนสีในทิศ EMA = เริ่มแนวโน้ม',
        "fn": _heikin,
        "params": [{"key": "trend_len", "label": "EMA แนวโน้ม", "type": "int", "default": 200, "min": 50, "max": 400, "step": 1}],
        "grid": {"trend_len": [100, 200], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'awesome_osc': {
        "name": 'Awesome Oscillator Zero Cross',
        "family": 'Momentum',
        "rationale": 'Awesome Oscillator (Bill Williams) = SMA5 − SMA34 ของราคากลาง ตัดศูนย์ = โมเมนตัมเปลี่ยนฝั่ง ใช้กับตัวกรองแนวโน้ม',
        "fn": _awesome,
        "params": [{"key": "trend_len", "label": "EMA แนวโน้ม", "type": "int", "default": 200, "min": 50, "max": 400, "step": 1}],
        "grid": {"trend_len": [100, 200], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'ultimate_osc': {
        "name": 'Ultimate Oscillator Pullback',
        "family": 'Pullback',
        "rationale": 'Ultimate Oscillator (Larry Williams) รวม 3 กรอบเวลา ลดสัญญาณหลอก ตัดขึ้นจากโซนต่ำในขาขึ้น = ย่อจบ',
        "fn": _ultimate,
        "params": [{"key": "uo_level", "label": "ระดับ UO (ขาลง = 100 - ค่านี้)", "type": "float", "default": 30, "min": 15, "max": 45, "step": 1}],
        "grid": {"uo_level": [30, 35], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'cci_reversion': {
        "name": 'CCI Extreme Snap-back',
        "family": 'Mean reversion',
        "rationale": 'CCI ลงสุดโต่ง (< −150/−200) แล้วกลับขึ้นเหนือ −100 = การขายมากเกินจบลง (Lambert ใช้ ±100 เป็นขอบปกติ)',
        "fn": _cci_reversion,
        "params": [{"key": "cci_extreme", "label": "CCI สุดโต่ง", "type": "float", "default": 200, "min": 120, "max": 300, "step": 10}],
        "grid": {"cci_extreme": [150, 200], "sl_atr": [1.5, 2.5], "rr": [1.0, 1.5]},
    },
    'keltner_pullback': {
        "name": 'Keltner Pullback in Trend',
        "family": 'Pullback',
        "rationale": 'ในขาขึ้น (EMA50 > EMA200) ราคาย่อแตะกรอบล่าง Keltner แล้วปิดกลับเข้ากรอบ = จังหวะซื้อย่อที่ปรับตามความผันผวน',
        "fn": _keltner_pullback,
        "params": [{"key": "kc_mult", "label": "Keltner (x ATR20)", "type": "float", "default": 1.5, "min": 0.5, "max": 3, "step": 0.1}],
        "grid": {"kc_mult": [1.0, 1.5], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'three_bar_pullback': {
        "name": 'N-Bar Pullback',
        "family": 'Pullback',
        "rationale": 'ราคาปิดลง N แท่งติดในขาขึ้น (เหนือ SMA200) แล้วปิดขึ้น = ย่อสั้นจบ — งานของ Larry Connors พบ short-term pullback ในแนวโน้มให้ผลดี',
        "fn": _three_bar,
        "params": [{"key": "n_bars", "label": "จำนวนแท่งที่ย่อ", "type": "int", "default": 3, "min": 2, "max": 6, "step": 1}],
        "grid": {"n_bars": [3, 4], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'nr_breakout': {
        "name": 'NR4 / NR7 Breakout',
        "family": 'Volatility breakout',
        "rationale": 'แท่งที่ช่วงแคบที่สุดใน N แท่ง (Toby Crabel, NR4/NR7) = ความผันผวนหดตัวสุด การทะลุกรอบแท่งนั้นมักวิ่งต่อ',
        "fn": _nr_breakout,
        "params": [{"key": "nr_len", "label": "NR ของกี่แท่ง", "type": "int", "default": 7, "min": 3, "max": 14, "step": 1}],
        "grid": {"nr_len": [4, 7], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'engulfing_reversal': {
        "name": 'Engulfing at Extreme',
        "family": 'Price action',
        "rationale": 'แท่งกลืนกิน (engulfing) ที่จุดต่ำ/สูงสุดของ N แท่ง = ฝั่งตรงข้ามเข้ามาคุมราคาทันที — รูปแบบแท่งเทียนญี่ปุ่น (Steve Nison) ที่ใช้เฉพาะตรงจุดสุดโต่ง',
        "fn": _engulfing,
        "params": [{"key": "lookback", "label": "จุดสุดของกี่แท่ง", "type": "int", "default": 20, "min": 5, "max": 100, "step": 1}],
        "grid": {"lookback": [10, 20], "sl_atr": [1.5, 2.5], "rr": [1.0, 1.5]},
    },
    'donchian_fade': {
        "name": 'Range Edge Fade',
        "family": 'Mean reversion',
        "rationale": 'ตลาดไม่มีแนวโน้ม (ADX < 20) ราคาแตะขอบกรอบ N แท่งแล้วปิดกลับเข้า = ขอบกรอบยังรับได้ เทรดกลับกลางกรอบ',
        "fn": _donchian_fade,
        "params": [{"key": "lookback", "label": "กรอบกี่แท่ง", "type": "int", "default": 20, "min": 10, "max": 100, "step": 1}],
        "grid": {"lookback": [20, 50], "sl_atr": [1.5, 2.5], "rr": [1.0, 1.5]},
    },
    'rsi_divergence': {
        "name": 'RSI Divergence',
        "family": 'Price action',
        "rationale": 'ราคาทำจุดต่ำใหม่แต่ RSI สูงกว่าจุดต่ำก่อนหน้า (bullish divergence) = แรงขายอ่อนลง ยืนยันด้วยแท่งเขียว',
        "fn": _rsi_div,
        "params": [{"key": "lookback", "label": "มองย้อนกี่แท่ง", "type": "int", "default": 30, "min": 10, "max": 100, "step": 1}],
        "grid": {"lookback": [20, 40], "sl_atr": [1.5, 2.5], "rr": [1.0, 1.5]},
    },
    'turn_of_month': {
        "name": 'Turn-of-the-Month (long)',
        "family": 'Calendar',
        "rationale": 'ช่วงปลายเดือน–ต้นเดือนมีเงินไหลเข้าตลาดหุ้น (เงินเดือน, กองทุน) — Ariel (1987), Lakonishok & Smidt (1988) พบผลตอบแทนกระจุกช่วงนี้ เหมาะกับดัชนี (Long อย่างเดียว)',
        "fn": _turn_of_month,
        "params": [{"key": "days_before", "label": "เข้ากี่วันทำการก่อนสิ้นเดือน", "type": "int", "default": 1, "min": 0, "max": 4, "step": 1}],
        "grid": {"days_before": [0, 1], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'london_fix_fade': {
        "name": 'London Fix Reversal',
        "family": 'Session / liquidity',
        "rationale": 'หลัง WM/Reuters fix 16:00 ลอนดอน คำสั่งซื้อขายจากกองทุนหมดไป ราคาที่วิ่งแรงตั้งแต่เช้ามักย้อนกลับบางส่วน (Krohn et al. งานวิจัยเรื่อง FX fixing)',
        "fn": _london_fade,
        "params": [{"key": "move_atr", "label": "วิ่งมากกว่า (x ATR)", "type": "float", "default": 1.0, "min": 0.5, "max": 4, "step": 0.1}],
        "grid": {"move_atr": [0.75, 1.0], "sl_atr": [1.5, 2.5], "rr": [1.0, 1.5]},
    },
    'ny_open_breakout': {
        "name": 'New York Open Range Breakout',
        "family": 'Session / liquidity',
        "rationale": 'ช่วงเปิดตลาดนิวยอร์ก (13:30 UTC) สภาพคล่องสูงสุดของวัน การทะลุกรอบช่วงเปิดมักไปต่อ (opening range breakout, Toby Crabel) เหมาะกับดัชนีและทอง บน H1',
        "fn": _ny_open,
        "params": [{"key": "range_bars", "label": "กรอบกี่แท่ง H1", "type": "int", "default": 1, "min": 1, "max": 3, "step": 1}],
        "grid": {"range_bars": [1, 2], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'weekly_breakout': {
        "name": 'Previous-Week High/Low Breakout',
        "family": 'Session / liquidity',
        "rationale": 'High/Low สัปดาห์ก่อนเป็นระดับที่สถาบันเฝ้าดู ทะลุตามทิศแนวโน้ม = stop ที่ถูกกินดันราคาต่อ ครั้งแรกของสัปดาห์เท่านั้น',
        "fn": _weekly,
        "params": [{"key": "trend_len", "label": "EMA แนวโน้ม", "type": "int", "default": 200, "min": 50, "max": 400, "step": 1}],
        "grid": {"trend_len": [100, 200], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'pivot_bounce': {
        "name": 'Daily Pivot S1/R1 Bounce',
        "family": 'Price action',
        "rationale": 'Floor-trader pivot (S1/R1 จาก H/L/C เมื่อวาน) เป็นแนวรับต้านที่เทรดเดอร์ทั่วโลกใช้ ราคาแตะ S1 แล้วปิดกลับขึ้นในขาขึ้น = แนวรับทำงาน',
        "fn": _pivot,
        "params": [{"key": "trend_len", "label": "EMA แนวโน้ม", "type": "int", "default": 200, "min": 50, "max": 400, "step": 1}],
        "grid": {"trend_len": [100, 200], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'fractal_breakout': {
        "name": 'Williams Fractal Breakout',
        "family": 'Trend following',
        "rationale": 'Fractal (Bill Williams) = จุดกลับตัวย่อยที่ยืนยันแล้ว ราคาปิดทะลุ fractal ล่าสุดตามทิศแนวโน้ม = โครงสร้าง higher-high ต่อเนื่อง',
        "fn": _fractal,
        "params": [{"key": "trend_len", "label": "EMA แนวโน้ม", "type": "int", "default": 200, "min": 50, "max": 400, "step": 1}],
        "grid": {"trend_len": [50, 200], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'alligator': {
        "name": 'Williams Alligator Awakening',
        "family": 'Trend following',
        "rationale": 'Alligator (Bill Williams) = SMMA 3 เส้นเลื่อนไปข้างหน้า เส้นเรียงตัวครั้งแรก (“จระเข้ตื่น”) = แนวโน้มเริ่ม',
        "fn": _alligator,
        "params": [{"key": "scale", "label": "ตัวคูณช่วงเส้น", "type": "float", "default": 1.0, "min": 0.5, "max": 3, "step": 0.5}],
        "grid": {"scale": [1.0, 2.0], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    'elder_ray': {
        "name": 'Elder-Ray Pullback',
        "family": 'Pullback',
        "rationale": 'Elder-Ray (Alexander Elder): ในขาขึ้น (EMA ขึ้น) Bear Power ติดลบแต่เริ่มดีขึ้นและราคาปิดกลับเหนือ EMA = ย่อจบ แรงขายหมด',
        "fn": _elder_ray,
        "params": [{"key": "ema_len", "label": "EMA Length", "type": "int", "default": 13, "min": 8, "max": 50, "step": 1}],
        "grid": {"ema_len": [13, 21], "sl_atr": [1.5, 2.5], "rr": [1.5, 2.5]},
    },
    "ewmac": {
        "name": "EWMAC Multi-speed Trend",
        "family": "Trend following",
        "rationale": "ระบบตามแนวโน้มแบบกองทุน CTA: ส่วนต่าง EMA เร็ว-ช้าหารด้วยความผันผวน หลายความเร็วเฉลี่ยกัน — Carver (2015) Systematic Trading; AQR 'A Century of Evidence on Trend-Following' (Hurst, Ooi & Pedersen 2017)",
        "fn": _ewmac,
        "params": [{"key": "speeds", "label": "ชุดความเร็ว (1=16/64, 2=32/128, 3=รวม 3 ความเร็ว)", "type": "int", "default": 3, "min": 1, "max": 3, "step": 1}],
        "grid": {"speeds": [1, 2, 3], "sl_atr": [2.5, 4.0], "rr": [3.0, 0]},
    },
    "tsmom_blend": {
        "name": "Time-Series Momentum 1/3/12m",
        "family": "Trend following",
        "rationale": "โหวตทิศจากผลตอบแทนย้อนหลัง 1, 3, 12 เดือน — Moskowitz, Ooi & Pedersen (2012) พบใน 58 ตลาด; Hurst, Ooi & Pedersen (2017) ใช้ส่วนผสม 1/3/12 เดือน ย้อนหลัง 110 ปี",
        "fn": _tsmom_blend,
        "params": [{"key": "mode", "label": "ช่วงที่ใช้ (0 = 1/3/12 เดือน, 1 = 3/12 เดือน)", "type": "int", "default": 0, "min": 0, "max": 1, "step": 1}],
        "grid": {"mode": [0, 1], "sl_atr": [2.5, 4.0], "rr": [3.0, 0]},
    },
    "turtle": {
        "name": "Turtle Breakout (always in)",
        "family": "Trend following",
        "rationale": "กฎเต่า (Richard Dennis 1983, Faith 2007): ซื้อเมื่อทำ High ใหม่ N แท่ง ขายเมื่อทำ Low ใหม่ N แท่ง ไม่มีตัวกรอง อยู่ในตลาดตลอด",
        "fn": _turtle,
        "params": [{"key": "entry_n", "label": "ช่องทาง N แท่ง", "type": "int", "default": 55, "min": 10, "max": 300, "step": 1}],
        "grid": {"entry_n": [20, 55], "sl_atr": [2.0, 3.0], "rr": [3.0, 0]},
    },
    "faber_sma": {
        "name": "Faber 10-month SMA Timing",
        "family": "Trend following",
        "rationale": "อยู่ฝั่งเดียวกับเส้นค่าเฉลี่ยราว 10 เดือน — Faber (2007) 'A Quantitative Approach to Tactical Asset Allocation' ลด drawdown ของหุ้น พันธบัตร สินค้าโภคภัณฑ์",
        "fn": _faber,
        "params": [{"key": "months", "label": "SMA (เดือน)", "type": "int", "default": 10, "min": 2, "max": 24, "step": 1}],
        "grid": {"months": [6, 10], "sl_atr": [2.5, 4.0], "rr": [3.0, 0]},
    },
    "clenow_trend": {
        "name": "Clenow Trend (EMA50/100 + Breakout)",
        "family": "Trend following",
        "rationale": "ระบบในหนังสือ 'Following the Trend' (Clenow 2013) ของผู้จัดการกองทุนตามแนวโน้ม: EMA50 เทียบ EMA100 เป็นทิศ แล้วเข้าเมื่อทะลุกรอบ N แท่ง",
        "fn": _clenow,
        "params": [{"key": "breakout_n", "label": "Breakout N แท่ง", "type": "int", "default": 50, "min": 10, "max": 300, "step": 1}],
        "grid": {"breakout_n": [50, 100], "sl_atr": [2.0, 3.0], "rr": [3.0, 0]},
    },
    "vol_breakout": {
        "name": "Volatility Breakout (Crabel)",
        "family": "Volatility breakout",
        "rationale": "แท่งที่วิ่งเกิน k × ATR จากราคาปิดก่อนหน้าบอกแรงซื้อขายใหม่ — Crabel (1990) 'Day Trading with Short Term Price Patterns', Larry Williams; กรองด้วย EMA100",
        "fn": _vol_breakout,
        "params": [{"key": "k_atr", "label": "k × ATR", "type": "float", "default": 1.0, "min": 0.3, "max": 3, "step": 0.1}],
        "grid": {"k_atr": [1.0, 1.5], "sl_atr": [1.5, 2.5], "rr": [2.0, 0]},
    },
    "high52": {
        "name": "52-Week High / Low Breakout",
        "family": "Trend following",
        "rationale": "ราคาที่ทำจุดสูงสุดรอบปีมักไปต่อเพราะนักลงทุนยึดจุดอ้างอิง — George & Hwang (2004) 'The 52-Week High and Momentum Investing'",
        "fn": _high52,
        "params": [{"key": "months", "label": "กรอบ (เดือน)", "type": "int", "default": 12, "min": 3, "max": 24, "step": 1}],
        "grid": {"months": [6, 12], "sl_atr": [2.5, 4.0], "rr": [3.0, 0]},
    },
}


# Stable system numbers: shown to the user and written into MT5 comments (TL{no}-{TF}) — never renumber.
for _i, _k in enumerate(list(STRATEGIES)):
    STRATEGIES[_k]["no"] = _i + 1

# Retired Oct 2026 (user's request): negative across all 8 symbols x 3 TFs in the Q4/2026 research (mean OOS t < -0.5 and
# no candidate t >= 1.5). They stay counted in the multiple-testing gate (research.n_retired_candidates), so removing
# losers never lowers the bar for the rest.
RETIRED = {k: STRATEGIES[k]["no"] for k in [
    "rsi_reversion", "bb_reversion", "tsmom", "adx_trend", "ema_pullback", "range_fade", "stoch_pullback",
    "zscore_reversion", "trix_cross", "cci_reversion", "keltner_pullback", "three_bar_pullback", "engulfing_reversal",
    "donchian_fade", "rsi_divergence", "turn_of_month", "london_fix_fade"] if k in STRATEGIES}
for _k in RETIRED:
    STRATEGIES.pop(_k)

# "No TP" variant (user's choice): trend / breakout / momentum systems also try rr = 0 (exit only by SL or an opposite
# signal, how trend-following funds let winners run). Walk-forward picks it in-sample like any other parameter.
for _k, _v in STRATEGIES.items():
    if _v["family"] in ("Trend following", "Volatility breakout", "Momentum") and 0 not in _v["grid"].get("rr", []):
        _v["grid"]["rr"] = list(_v["grid"]["rr"]) + [0]


def schema(strategy_id: str) -> list[dict]:
    return STRATEGIES[strategy_id]["params"] + EXIT_PARAMS


def defaults(strategy_id: str) -> dict:
    return {p["key"]: p["default"] for p in schema(strategy_id)}


def normalize(strategy_id: str, params: dict | None) -> dict:
    out = defaults(strategy_id)
    for p in schema(strategy_id):
        if params and p["key"] in params and params[p["key"]] is not None:
            v = float(params[p["key"]])
            out[p["key"]] = int(round(v)) if p["type"] == "int" else v
    return out


def grid(strategy_id: str, max_hold_h: int) -> list[dict]:
    g = STRATEGIES[strategy_id]["grid"]
    keys = list(g)
    return [normalize(strategy_id, dict(zip(keys, vals)) | {"max_hold_h": max_hold_h})
            for vals in itertools.product(*(g[k] for k in keys))]


def signals(strategy_id: str, df: pd.DataFrame, params: dict):
    sig, lines = STRATEGIES[strategy_id]["fn"](df, params)
    sig = np.nan_to_num(np.asarray(sig, dtype=float)).astype(np.int8)
    return sig, lines


def public_list() -> list[dict]:
    from .strategy_docs import doc
    return [{"id": k, "no": v["no"], "name": v["name"], "family": v["family"], "rationale": v["rationale"],
             "params": schema(k), "grid": v["grid"], "grid_size": len(grid(k, 120)), **doc(k)}
            for i, (k, v) in enumerate(STRATEGIES.items())]
