"""Library v2 (user, 11 Nov 2026): seven families of exactly 100 systems each.

The five small families are filled up with TRIGGER x FILTER systems, the same construction as the pullback /
mean-reversion library (app/strategies_mr.py): a trigger is the family's entry event, a filter is a fixed market-state
condition (trend side, volatility regime, momentum, range) taken from strategies_mr.FILTERS. The combinations are taken in
a pre-registered order — for each filter in the family's filter list, every trigger — until the family has 100 systems,
so every trigger appears about equally often. Nothing here was chosen from test results.

  Trend following      64 existing + 36 =  4 triggers x 9 filters
  Momentum             27 existing + 73 =  8 triggers x 10 filters, first 73
  Volatility breakout  12 existing + 88 =  9 triggers x 10 filters, first 88
  Session / liquidity   4 existing + 96 =  8 triggers x 12 filters
  Price action          3 existing + 97 = 10 triggers x 10 filters, first 97

Economic rationale per family (written before any result):
  * trend / momentum: time-series and cross-sectional momentum (Moskowitz, Ooi & Pedersen 2012; Hurst, Ooi & Pedersen 2017;
    Jegadeesh & Titman 1993) — under-reaction to news and slow-moving capital let trends persist
  * volatility breakout: volatility clusters and compressions resolve into directional moves (Bollinger 2001; Crabel 1990,
    "Day Trading with Short Term Price Patterns and Opening Range Breakout")
  * session / liquidity: liquidity and information arrive at known hours (London / New York opens, prior-day and prior-week
    extremes where stops cluster — Osler 2003 "Currency orders and exchange rate dynamics")
  * price action: bar patterns as a compact record of who won the bar (Bulkowski; tested here only as a hypothesis, the
    literature on candlestick edges after costs is mixed — Marshall, Young & Rose 2006)

Rules: signal on the bar close, entry at the next open, one signal parameter (2 values), strictly causal, no volume.
Session triggers read the hour of the day -> clock_sensitive (tested only on years with a verified UTC clock).
Exits come from the shared grid (strategies.grid): SL x ATR with three exit modes.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ta
from .strategies_mr import FILTERS, _bpm, _ev, _hlc, _streak

TF_, MOM, VOL, SES, PA = "Trend following", "Momentum", "Volatility breakout", "Session / liquidity", "Price action"


def _o(df):
    return df.open.to_numpy(float)


def _x(a, b):
    """a crosses above b on this bar."""
    return (a > b) & (ta.shift(a, 1) <= ta.shift(b, 1))


def _hour(df):
    return df.index.hour.to_numpy()


def _day(df):
    return df.index.normalize()


def _cum_by(series: np.ndarray, keys, how: str) -> np.ndarray:
    s = pd.Series(series)
    g = s.groupby(np.asarray(keys))
    return (g.cummax() if how == "max" else g.cummin()).to_numpy()


# ================================================================ TREND (4 triggers) -> (long state/event, short)
def tr_donchian(df, v):
    h, l, c = _hlc(df)
    n = int(v)
    return c > ta.shift(ta.highest(h, n), 1), c < ta.shift(ta.lowest(l, n), 1)


def tr_ema_cross(df, v):
    c = df.close.to_numpy(float)
    f, s = ta.ema(c, int(v)), ta.ema(c, 4 * int(v))
    return f > s, f < s


def tr_supertrend(df, v):
    st = ta.supertrend(*_hlc(df), 10, float(v))
    d = np.asarray(st[1] if isinstance(st, tuple) else st)
    return d > 0, d < 0


def tr_linreg(df, v):
    s = ta.linreg_slope(df.close.to_numpy(float), int(v))
    return s > 0, s < 0


# ================================================================ MOMENTUM (8 triggers)
def mo_roc(df, v):
    c = df.close.to_numpy(float)
    r = c - ta.shift(c, int(v))
    return r > 0, r < 0


def mo_rsi(df, v):
    r = ta.rsi(df.close.to_numpy(float), 14)
    return r > v, r < 100 - v


def mo_stoch50(df, v):
    h, l, c = _hlc(df)
    n = int(v)
    hh, ll = ta.highest(h, n), ta.lowest(l, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        k = 100 * (c - ll) / (hh - ll)
    k = ta.sma(k, 3)
    return k > 50, k < 50


def mo_cci0(df, v):
    x = ta.cci(*_hlc(df), int(v))
    return x > 0, x < 0


def mo_12_1(df, v):
    """Months-long return skipping the most recent month (Jegadeesh & Titman's 12-1 momentum)."""
    c = df.close.to_numpy(float)
    m = _bpm(df)
    r = ta.shift(c, m) / ta.shift(c, int(v) * m) - 1
    return r > 0, r < 0


def mo_tsi(df, v):
    c = df.close.to_numpy(float)
    d = np.diff(c, prepend=np.nan)
    n = int(v)
    num = ta.ema(ta.ema(d, n), 13)
    den = ta.ema(ta.ema(np.abs(d), n), 13)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = num / den
    return t > 0, t < 0


def mo_ppo(df, v):
    c = df.close.to_numpy(float)
    f = int(v)
    p = ta.ema(c, f) - ta.ema(c, int(round(f * 26 / 12)))
    return p > 0, p < 0


def mo_accel(df, v):
    """Momentum that is rising: n-bar return positive AND larger than it was n bars ago."""
    c = df.close.to_numpy(float)
    n = int(v)
    r = c - ta.shift(c, n)
    pr = ta.shift(r, n)
    return (r > 0) & (r > pr), (r < 0) & (r < pr)


# ================================================================ VOLATILITY BREAKOUT (9 triggers)
def vb_squeeze(df, v):
    """Bollinger width at its lowest decile of 120 bars on the previous bar, then a close outside the band."""
    c = df.close.to_numpy(float)
    mid, up, lo = ta.bollinger(c, 20, float(v))
    w = (up - lo) / mid
    lowq = pd.Series(w).rolling(120, min_periods=60).quantile(0.1).to_numpy()
    tight = ta.shift((w <= lowq).astype(float), 1) == 1
    return tight & (c > up), tight & (c < lo)


def vb_keltner(df, v):
    h, l, c = _hlc(df)
    e, a = ta.ema(c, 20), ta.atr(h, l, c, 20)
    return c > e + v * a, c < e - v * a


def vb_nr(df, v):
    """Break of the high / low of the narrowest bar of the last n (NR4 / NR7, Crabel)."""
    h, l, c = _hlc(df)
    n = int(v)
    rng = h - l
    nr = rng <= ta.lowest(rng, n)
    ph, pl, pnr = ta.shift(h, 1), ta.shift(l, 1), ta.shift(nr.astype(float), 1) == 1
    return pnr & (c > ph), pnr & (c < pl)


def vb_atr_bar(df, v):
    o = _o(df)
    h, l, c = _hlc(df)
    a = ta.shift(ta.atr(h, l, c, 14), 1)
    big = (h - l) > v * a
    return big & (c > o) & (c > h - (h - l) / 3), big & (c < o) & (c < l + (h - l) / 3)


def vb_box(df, v):
    """A tight box (last 10 bars' range < v x ATR50) broken by the close."""
    h, l, c = _hlc(df)
    hh, ll = ta.shift(ta.highest(h, 10), 1), ta.shift(ta.lowest(l, 10), 1)
    a = ta.shift(ta.atr(h, l, c, 50), 1)
    tight = (hh - ll) < v * a
    return tight & (c > hh), tight & (c < ll)


def vb_hv(df, v):
    """Realised volatility jumps (10-bar sd > v x its 100-bar average) with the close in the direction of the 10-bar move."""
    c = df.close.to_numpy(float)
    r = np.diff(np.log(c), prepend=np.nan)
    sd = ta.stdev(r, 10)
    base = ta.sma(sd, 100)
    jump = sd > v * base
    mv = c - ta.shift(c, 10)
    return jump & (mv > 0), jump & (mv < 0)


def vb_pctb(df, v):
    c = df.close.to_numpy(float)
    _, up, lo = ta.bollinger(c, int(v), 2.0)
    return c > up, c < lo


def vb_donch_quiet(df, v):
    """20-bar Donchian breakout while ATR14 is below ATR100 (a breakout out of a quiet market)."""
    h, l, c = _hlc(df)
    n = int(v)
    quiet = ta.atr(h, l, c, 14) < ta.atr(h, l, c, 100)
    return quiet & (c > ta.shift(ta.highest(h, n), 1)), quiet & (c < ta.shift(ta.lowest(l, n), 1))


def vb_range_exp(df, v):
    """Today's range exceeds yesterday's by v x and closes at the extreme (range expansion day, Crabel)."""
    o = _o(df)
    h, l, c = _hlc(df)
    rng = h - l
    exp = rng > v * ta.shift(rng, 1)
    return exp & (c > o) & (c >= h - rng / 4), exp & (c < o) & (c <= l + rng / 4)


# ================================================================ SESSION / LIQUIDITY (8 triggers, hour of day in UTC)
def _range_break(df, start_h: int, end_h: int, until_h: int):
    """High / low of today's bars with start_h <= hour < end_h, broken by a close between end_h and until_h."""
    h, l, c = _hlc(df)
    hr, day = _hour(df), _day(df)
    inr = (hr >= start_h) & (hr < end_h)
    hh = _cum_by(np.where(inr, h, -np.inf), day, "max")
    ll = _cum_by(np.where(inr, l, np.inf), day, "min")
    win = (hr >= end_h) & (hr < until_h) & np.isfinite(hh) & np.isfinite(ll)
    return win & (c > hh), win & (c < ll)


def se_asia(df, v):
    return _range_break(df, 0, int(v), int(v) + 6)


def se_london(df, v):
    return _range_break(df, 7, 7 + int(v), 16)


def se_ny(df, v):
    return _range_break(df, 13, 13 + int(v), 20)


def se_prev_day(df, v):
    h, l, c = _hlc(df)
    a = ta.atr(h, l, c, 14)
    day = _day(df)
    dh = pd.Series(h).groupby(np.asarray(day)).transform("max")      # used only shifted by one day below
    dl = pd.Series(l).groupby(np.asarray(day)).transform("min")
    d = pd.DataFrame({"day": day, "h": dh.to_numpy(), "l": dl.to_numpy()}).drop_duplicates("day").set_index("day")
    prev = d.shift(1).reindex(day)
    ph, pl = prev["h"].to_numpy(), prev["l"].to_numpy()
    return c > ph + v * a, c < pl - v * a


def se_prev_week(df, v):
    h, l, c = _hlc(df)
    a = ta.atr(h, l, c, 14)
    idx = df.index
    wk = (idx.normalize() - pd.to_timedelta(idx.dayofweek, unit="D"))
    d = pd.DataFrame({"wk": wk, "h": h, "l": l}).groupby("wk").agg(h=("h", "max"), l=("l", "min"))
    prev = d.shift(1).reindex(wk)
    return c > prev["h"].to_numpy() + v * a, c < prev["l"].to_numpy() - v * a


def se_day_open(df, v):
    """After the London open (>= 08 UTC) the close moves v x ATR away from the UTC day's open."""
    o = _o(df)
    h, l, c = _hlc(df)
    a = ta.atr(h, l, c, 14)
    day, hr = _day(df), _hour(df)
    dopen = pd.Series(o).groupby(np.asarray(day)).transform("first").to_numpy()
    ok = hr >= 8
    return ok & (c > dopen + v * a), ok & (c < dopen - v * a)


def se_london_close(df, v):
    """London-close fade (15-17 UTC): after a day move of more than v x ATR from the day's open, trade back against it."""
    o = _o(df)
    h, l, c = _hlc(df)
    a = ta.atr(h, l, c, 14)
    day, hr = _day(df), _hour(df)
    dopen = pd.Series(o).groupby(np.asarray(day)).transform("first").to_numpy()
    win = (hr >= 15) & (hr < 17)
    return win & (c < dopen - v * a), win & (c > dopen + v * a)


def se_monday(df, v):
    """Break of the week's opening range (the first v hours of the trading week)."""
    h, l, c = _hlc(df)
    idx = df.index
    wk = (idx - pd.Timedelta(hours=21)).normalize() - pd.to_timedelta((idx - pd.Timedelta(hours=21)).dayofweek, unit="D")
    k = pd.Series(1, index=np.arange(len(c))).groupby(np.asarray(wk)).cumcount().to_numpy()
    hrs = int(v)
    inr = k < hrs
    hh = _cum_by(np.where(inr, h, -np.inf), wk, "max")
    ll = _cum_by(np.where(inr, l, np.inf), wk, "min")
    win = (k >= hrs) & np.isfinite(hh) & np.isfinite(ll)
    return win & (c > hh), win & (c < ll)


# ================================================================ PRICE ACTION (10 triggers)
def pa_engulf(df, v):
    o = _o(df)
    h, l, c = _hlc(df)
    po, pc = ta.shift(o, 1), ta.shift(c, 1)
    n = int(v)
    bull = (pc < po) & (c > o) & (c >= po) & (o <= pc) & (ta.shift(l, 1) <= ta.lowest(l, n))
    bear = (pc > po) & (c < o) & (c <= po) & (o >= pc) & (ta.shift(h, 1) >= ta.highest(h, n))
    return bull, bear


def pa_inside(df, v):
    """Close beyond the mother bar after v inside bars in a row."""
    h, l, c = _hlc(df)
    k = int(v)
    ins = (h <= ta.shift(h, 1)) & (l >= ta.shift(l, 1))
    ok = np.ones(len(c), bool)
    for j in range(1, k + 1):
        ok &= ta.shift(ins.astype(float), j) == 1
    mh, ml = ta.shift(h, k + 1), ta.shift(l, k + 1)
    return ok & (c > mh), ok & (c < ml)


def pa_outside(df, v):
    o = _o(df)
    h, l, c = _hlc(df)
    out = (h > ta.shift(h, 1)) & (l < ta.shift(l, 1))
    pos = (c - l) / np.where(h > l, h - l, np.nan)
    return out & (c > o) & (pos >= v), out & (c < o) & (pos <= 1 - v)


def pa_soldiers(df, v):
    o = _o(df)
    h, l, c = _hlc(df)
    rng = np.where(h > l, h - l, np.nan)
    up = (c > o) & ((c - o) / rng >= v) & (c > ta.shift(c, 1))
    dn = (c < o) & ((o - c) / rng >= v) & (c < ta.shift(c, 1))
    u3 = up & (ta.shift(up.astype(float), 1) == 1) & (ta.shift(up.astype(float), 2) == 1)
    d3 = dn & (ta.shift(dn.astype(float), 1) == 1) & (ta.shift(dn.astype(float), 2) == 1)
    return u3, d3


def pa_star(df, v):
    """Morning / evening star at an n-bar extreme: big bar, small middle bar, close back past the first bar's midpoint."""
    o = _o(df)
    h, l, c = _hlc(df)
    o2, c2, o1, c1 = ta.shift(o, 2), ta.shift(c, 2), ta.shift(o, 1), ta.shift(c, 1)
    small = np.abs(c1 - o1) < 0.3 * np.abs(c2 - o2)
    n = int(v)
    morn = (c2 < o2) & small & (c > o) & (c > (o2 + c2) / 2) & (ta.shift(l, 1) <= ta.lowest(l, n))
    even = (c2 > o2) & small & (c < o) & (c < (o2 + c2) / 2) & (ta.shift(h, 1) >= ta.highest(h, n))
    return morn, even


def pa_two_bar(df, v):
    o = _o(df)
    h, l, c = _hlc(df)
    a = ta.shift(ta.atr(h, l, c, 14), 1)
    po, pc = ta.shift(o, 1), ta.shift(c, 1)
    bull = (pc < po) & ((po - pc) > a) & (c > o) & ((c - o) >= v * (po - pc))
    bear = (pc > po) & ((pc - po) > a) & (c < o) & ((o - c) >= v * (pc - po))
    return bull, bear


def pa_marubozu(df, v):
    o = _o(df)
    h, l, c = _hlc(df)
    a = ta.shift(ta.atr(h, l, c, 14), 1)
    rng = np.where(h > l, h - l, np.nan)
    full = (np.abs(c - o) / rng >= v) & ((h - l) > a)
    return full & (c > o), full & (c < o)


def pa_structure(df, v):
    """Market-structure break: close above the last confirmed swing high (fractal of k bars each side), below the last swing low."""
    h, l, c = _hlc(df)
    k = int(v)
    n = len(c)
    sh = np.full(n, np.nan)
    sl = np.full(n, np.nan)
    for i in range(2 * k, n):                 # pivot at i - k is confirmed at bar i (k bars after it)
        j = i - k
        win_h, win_l = h[j - k:j + k + 1], l[j - k:j + k + 1]
        sh[i] = h[j] if h[j] == win_h.max() else sh[i - 1]
        sl[i] = l[j] if l[j] == win_l.min() else sl[i - 1]
    psh, psl = ta.shift(sh, 1), ta.shift(sl, 1)
    return c > psh, c < psl


def pa_doji(df, v):
    """A doji (body <= v of the range) followed by a close beyond its high / low."""
    o = _o(df)
    h, l, c = _hlc(df)
    rng = np.where(h > l, h - l, np.nan)
    doji = np.abs(c - o) / rng <= v
    pd_ = ta.shift(doji.astype(float), 1) == 1
    return pd_ & (c > ta.shift(h, 1)), pd_ & (c < ta.shift(l, 1))


def pa_hikkake(df, v):
    """Hikkake: an inside bar, a false break of it, then a close back through the other side within v bars."""
    h, l, c = _hlc(df)
    n = len(c)
    ins = (h <= ta.shift(h, 1)) & (l >= ta.shift(l, 1))
    w = int(v)
    long_ = np.zeros(n, bool)
    short = np.zeros(n, bool)
    for i in np.flatnonzero(ins[:-2]):
        ih, il = h[i], l[i]
        j = i + 1
        if j >= n:
            break
        if l[j] < il and h[j] <= ih:              # false break down -> long when price closes above the inside high
            for k in range(j + 1, min(n, j + 1 + w)):
                if c[k] > ih:
                    long_[k] = True
                    break
        elif h[j] > ih and l[j] >= il:            # false break up -> short below the inside low
            for k in range(j + 1, min(n, j + 1 + w)):
                if c[k] < il:
                    short[k] = True
                    break
    return long_, short


# ================================================================ triggers per family
# key: (fn, param key, label, type, default, lo, hi, step, grid, name, Thai long rule, Thai short rule, indicators, TP list)
_T = {
    TF_: {
        "don": (tr_donchian, "n", "Donchian (แท่ง)", "int", 20, 10, 100, 1, [20, 55], "Donchian Breakout", "ราคาปิดทะลุ High สูงสุด n แท่ง", "ราคาปิดหลุด Low ต่ำสุด n แท่ง", ["Donchian (n)"], [2.0, 3.0]),
        "emax": (tr_ema_cross, "n", "EMA เร็ว (ช้า = 4 เท่า)", "int", 20, 5, 100, 1, [20, 50], "EMA Cross 1:4", "EMA(n) ตัดขึ้นเหนือ EMA(4n)", "EMA(n) ตัดลงใต้ EMA(4n)", ["EMA (n)", "EMA (4n)"], [2.0, 3.0]),
        "stf": (tr_supertrend, "mult", "Supertrend × ATR", "float", 3.0, 1.0, 5.0, 0.5, [2.0, 3.0], "Supertrend Flip", "Supertrend (10, mult) กลับเป็นขาขึ้น", "Supertrend กลับเป็นขาลง", ["Supertrend (10, mult)"], [2.0, 3.0]),
        "lrs": (tr_linreg, "n", "ความชันถดถอย (แท่ง)", "int", 50, 20, 200, 10, [50, 100], "Regression Slope Flip", "ความชันถดถอยเชิงเส้น n แท่งเปลี่ยนเป็นบวก", "ความชันเปลี่ยนเป็นลบ", ["Linear regression slope (n)"], [2.0, 3.0]),
    },
    MOM: {
        "roc": (mo_roc, "n", "ROC (แท่ง)", "int", 20, 5, 100, 1, [10, 20], "ROC Zero Cross", "ผลตอบแทน n แท่งเปลี่ยนเป็นบวก", "เปลี่ยนเป็นลบ", ["ROC (n)"], [2.0, 3.0]),
        "rsi": (mo_rsi, "level", "RSI(14) ระดับ", "int", 55, 50, 70, 1, [55, 60], "RSI Thrust", "RSI(14) ขึ้นเหนือ level", "RSI(14) ลงใต้ 100 − level", ["RSI (14)"], [2.0, 3.0]),
        "st50": (mo_stoch50, "n", "Stochastic (แท่ง)", "int", 14, 5, 50, 1, [14, 21], "Stochastic 50 Cross", "%K(n) เฉลี่ย 3 แท่ง ขึ้นเหนือ 50", "ลงใต้ 50", ["Stochastic %K (n, 3)"], [2.0, 3.0]),
        "cci": (mo_cci0, "n", "CCI (แท่ง)", "int", 20, 10, 100, 1, [20, 50], "CCI Zero Cross", "CCI(n) ขึ้นเหนือ 0", "CCI(n) ลงใต้ 0", ["CCI (n)"], [2.0, 3.0]),
        "m121": (mo_12_1, "months", "ย้อนหลัง (เดือน, ข้ามเดือนล่าสุด)", "int", 12, 3, 12, 1, [6, 12], "12-1 Momentum", "ผลตอบแทน (months → 1 เดือนก่อน) เปลี่ยนเป็นบวก", "เปลี่ยนเป็นลบ", ["ผลตอบแทนข้ามเดือนล่าสุด"], [2.0, 3.0]),
        "tsi": (mo_tsi, "n", "TSI ช่วงยาว", "int", 25, 10, 50, 1, [25, 40], "TSI Zero Cross", "True Strength Index ขึ้นเหนือ 0", "TSI ลงใต้ 0", ["TSI (n, 13)"], [2.0, 3.0]),
        "ppo": (mo_ppo, "n", "EMA เร็ว (ช้า = ×26/12)", "int", 12, 5, 50, 1, [12, 24], "PPO Zero Cross", "EMA(n) − EMA(n×26/12) เปลี่ยนเป็นบวก", "เปลี่ยนเป็นลบ", ["EMA เร็ว / ช้า"], [2.0, 3.0]),
        "acc": (mo_accel, "n", "ROC (แท่ง)", "int", 20, 5, 100, 1, [10, 20], "Momentum Acceleration", "ผลตอบแทน n แท่งเป็นบวกและมากกว่า n แท่งก่อน", "เป็นลบและต่ำกว่า n แท่งก่อน", ["ROC (n)", "ROC ย้อนหลัง n แท่ง"], [2.0, 3.0]),
    },
    VOL: {
        "sqz": (vb_squeeze, "k", "Bollinger k", "float", 2.0, 1.5, 3.0, 0.1, [2.0, 2.5], "BB Width Squeeze", "ความกว้าง Bollinger ต่ำสุด 10% ของ 120 แท่ง แล้วปิดเหนือแบนด์บน", "แล้วปิดใต้แบนด์ล่าง", ["Bollinger (20, k)", "Percentile ความกว้าง 120 แท่ง"], [1.5, 2.5]),
        "kel": (vb_keltner, "k", "Keltner k × ATR", "float", 2.0, 1.0, 3.5, 0.1, [1.5, 2.5], "Keltner Breakout", "ราคาปิดเหนือ EMA20 + k × ATR20", "ราคาปิดใต้ EMA20 − k × ATR20", ["EMA20", "ATR20"], [1.5, 2.5]),
        "nr": (vb_nr, "n", "แท่งแคบสุดรอบ (แท่ง)", "int", 7, 4, 10, 1, [4, 7], "NR Breakout", "แท่งก่อนแคบสุดใน n แท่ง แล้วปิดทะลุ High ของมัน", "ปิดหลุด Low ของแท่งแคบ", ["ช่วงแท่ง (NR n)"], [1.5, 2.5]),
        "atrb": (vb_atr_bar, "k", "แท่งกว้าง ≥ k × ATR", "float", 1.8, 1.2, 3.0, 0.1, [1.5, 2.0], "Wide-Range Bar", "แท่งเขียวกว้างเกิน k × ATR14 ปิดในส่วนบน 1/3", "แท่งแดงกว้าง ปิดในส่วนล่าง 1/3", ["ATR14", "ช่วงแท่ง"], [1.5, 2.5]),
        "box": (vb_box, "k", "กรอบ 10 แท่ง < k × ATR50", "float", 2.0, 1.0, 4.0, 0.1, [1.5, 2.5], "Tight Box Break", "กรอบ 10 แท่งแคบกว่า k × ATR50 แล้วปิดทะลุด้านบน", "ปิดหลุดด้านล่าง", ["High / Low 10 แท่ง", "ATR50"], [1.5, 2.5]),
        "hv": (vb_hv, "k", "SD 10 แท่ง > k × เฉลี่ย", "float", 1.5, 1.2, 3.0, 0.1, [1.5, 2.0], "Volatility Jump", "ความผันผวน 10 แท่งพุ่งเกิน k เท่าของค่าเฉลี่ย 100 แท่ง และราคา 10 แท่งขึ้น", "และราคา 10 แท่งลง", ["SD ผลตอบแทน 10 แท่ง", "ค่าเฉลี่ย 100 แท่ง"], [1.5, 2.5]),
        "pctb": (vb_pctb, "n", "Bollinger (แท่ง)", "int", 20, 10, 60, 1, [20, 40], "Bollinger Breakout", "ราคาปิดเหนือ Bollinger บน (n, 2)", "ราคาปิดใต้ Bollinger ล่าง", ["Bollinger (n, 2)"], [1.5, 2.5]),
        "dq": (vb_donch_quiet, "n", "Donchian (แท่ง)", "int", 20, 10, 60, 1, [20, 40], "Quiet Donchian Break", "ATR14 < ATR100 และราคาปิดทะลุ High n แท่ง", "ATR14 < ATR100 และปิดหลุด Low n แท่ง", ["Donchian (n)", "ATR14 / ATR100"], [1.5, 2.5]),
        "rx": (vb_range_exp, "k", "ช่วงแท่ง > k × แท่งก่อน", "float", 2.0, 1.5, 3.0, 0.1, [1.5, 2.0], "Range Expansion Bar", "ช่วงแท่งกว้างกว่าแท่งก่อน k เท่า ปิดในส่วนบน ¼", "ปิดในส่วนล่าง ¼", ["ช่วงแท่ง"], [1.5, 2.5]),
    },
    SES: {
        "asia": (se_asia, "end", "กรอบเอเชีย 00 → end (UTC)", "int", 6, 4, 8, 1, [6, 7], "Asian Range Break", "ราคาปิดเหนือ High ของ 00:00 → end UTC ภายใน 6 ชม.", "ปิดใต้ Low ของกรอบ", ["กรอบเอเชีย (UTC)"], [1.0, 2.0]),
        "ldn": (se_london, "hours", "กรอบลอนดอน (ชม. แรก)", "int", 1, 1, 3, 1, [1, 2], "London Opening Range", "ราคาปิดเหนือ High ของ hours ชม. แรกหลัง 07:00 UTC (ก่อน 16:00)", "ปิดใต้ Low ของกรอบ", ["กรอบเปิดลอนดอน 07:00 UTC"], [1.0, 2.0]),
        "ny": (se_ny, "hours", "กรอบนิวยอร์ก (ชม. แรก)", "int", 1, 1, 3, 1, [1, 2], "New York Opening Range", "ราคาปิดเหนือ High ของ hours ชม. แรกหลัง 13:00 UTC (ก่อน 20:00)", "ปิดใต้ Low ของกรอบ", ["กรอบเปิดนิวยอร์ก 13:00 UTC"], [1.0, 2.0]),
        "pday": (se_prev_day, "k", "เผื่อ k × ATR", "float", 0.0, 0.0, 1.0, 0.05, [0.0, 0.25], "Prior-Day Extreme Break", "ราคาปิดเหนือ High ของวันก่อน (UTC) + k × ATR14", "ปิดใต้ Low ของวันก่อน − k × ATR14", ["High / Low วันก่อน", "ATR14"], [1.0, 2.0]),
        "pwk": (se_prev_week, "k", "เผื่อ k × ATR", "float", 0.0, 0.0, 1.0, 0.05, [0.0, 0.25], "Prior-Week Extreme Break", "ราคาปิดเหนือ High ของสัปดาห์ก่อน + k × ATR14", "ปิดใต้ Low ของสัปดาห์ก่อน − k × ATR14", ["High / Low สัปดาห์ก่อน", "ATR14"], [1.0, 2.0]),
        "dopen": (se_day_open, "k", "ห่างจากราคาเปิดวัน (× ATR)", "float", 1.0, 0.25, 3.0, 0.05, [0.5, 1.0], "Day-Open Drive", "หลัง 08:00 UTC ราคาปิดสูงกว่าราคาเปิดวัน k × ATR14", "ราคาปิดต่ำกว่าราคาเปิดวัน k × ATR14", ["ราคาเปิดวัน (UTC)", "ATR14"], [1.0, 2.0]),
        "lclose": (se_london_close, "k", "วิ่งจากราคาเปิดวัน (× ATR)", "float", 1.5, 0.5, 3.0, 0.05, [1.0, 1.5], "London-Close Fade", "15–17 UTC ราคาต่ำกว่าราคาเปิดวันเกิน k × ATR14 → ซื้อสวน", "สูงกว่าราคาเปิดวันเกิน k × ATR14 → ขายสวน", ["ราคาเปิดวัน (UTC)", "ATR14"], [1.0, 2.0]),
        "mon": (se_monday, "hours", "กรอบต้นสัปดาห์ (ชม.)", "int", 4, 2, 12, 1, [4, 8], "Weekly Opening Range", "ราคาปิดเหนือ High ของ hours แท่งแรกของสัปดาห์", "ปิดใต้ Low ของกรอบต้นสัปดาห์", ["กรอบต้นสัปดาห์"], [1.0, 2.0]),
    },
    PA: {
        "eng": (pa_engulf, "n", "ที่จุดต่ำ/สูงสุดรอบ (แท่ง)", "int", 10, 5, 20, 1, [5, 10], "Engulfing at Extreme", "แท่งเขียวกลืนแท่งแดงก่อนหน้า ที่ Low ต่ำสุด n แท่ง", "แท่งแดงกลืนแท่งเขียว ที่ High สูงสุด n แท่ง", ["รูปแท่ง Engulfing", "High / Low n แท่ง"], [1.5, 2.5]),
        "ins": (pa_inside, "k", "Inside bar ติดกัน", "int", 1, 1, 3, 1, [1, 2], "Inside Bar Break", "หลัง inside bar k แท่ง ราคาปิดทะลุ High แท่งแม่", "ปิดหลุด Low แท่งแม่", ["Inside bar"], [1.5, 2.5]),
        "out": (pa_outside, "pos", "ปิดที่ตำแหน่ง (สัดส่วนแท่ง)", "float", 0.75, 0.6, 0.95, 0.05, [0.7, 0.85], "Outside Bar Close", "แท่งคลุมแท่งก่อน (High สูงกว่า Low ต่ำกว่า) เป็นแท่งเขียวปิดช่วงบน", "แท่งแดงปิดช่วงล่าง", ["Outside bar"], [1.5, 2.5]),
        "sold": (pa_soldiers, "body", "ตัวแท่ง ≥ สัดส่วน", "float", 0.5, 0.3, 0.8, 0.05, [0.5, 0.6], "Three Soldiers / Crows", "แท่งเขียว 3 แท่งติด ตัวแท่ง ≥ body ของช่วง ปิดสูงขึ้นทุกแท่ง", "แท่งแดง 3 แท่งติด ปิดต่ำลงทุกแท่ง", ["รูปแท่ง 3 แท่ง"], [1.5, 2.5]),
        "star": (pa_star, "n", "ที่จุดต่ำ/สูงสุดรอบ (แท่ง)", "int", 10, 5, 20, 1, [5, 10], "Morning / Evening Star", "Morning star ที่ Low n แท่ง", "Evening star ที่ High n แท่ง", ["รูปแท่ง 3 แท่ง", "High / Low n แท่ง"], [1.5, 2.5]),
        "two": (pa_two_bar, "k", "คืนแท่งก่อน ≥ สัดส่วน", "float", 0.8, 0.5, 1.2, 0.05, [0.8, 1.0], "Two-Bar Reversal", "แท่งแดงใหญ่ (> ATR) ตามด้วยแท่งเขียวคืน ≥ k ของมัน", "แท่งเขียวใหญ่ตามด้วยแท่งแดงคืน ≥ k", ["ATR14", "ตัวแท่ง"], [1.5, 2.5]),
        "maru": (pa_marubozu, "body", "ตัวแท่ง ≥ สัดส่วนช่วง", "float", 0.85, 0.7, 0.95, 0.05, [0.8, 0.9], "Marubozu Thrust", "แท่งเขียวเกือบไม่มีไส้ (ตัวแท่ง ≥ body) กว้างกว่า ATR14", "แท่งแดงเกือบไม่มีไส้ กว้างกว่า ATR14", ["ตัวแท่ง", "ATR14"], [1.5, 2.5]),
        "mss": (pa_structure, "k", "Swing ยืนยัน (แท่งข้างละ)", "int", 2, 1, 5, 1, [2, 3], "Market Structure Break", "ราคาปิดทะลุ Swing high ล่าสุด (fractal k แท่ง)", "ราคาปิดหลุด Swing low ล่าสุด", ["Swing high / low (fractal k)"], [1.5, 2.5]),
        "doji": (pa_doji, "body", "ตัวแท่ง Doji ≤ สัดส่วน", "float", 0.1, 0.05, 0.3, 0.05, [0.1, 0.2], "Doji Break", "แท่งก่อนเป็น Doji แล้วราคาปิดทะลุ High ของมัน", "ปิดหลุด Low ของ Doji", ["Doji"], [1.5, 2.5]),
        "hik": (pa_hikkake, "w", "รอยืนยัน (แท่ง)", "int", 3, 2, 6, 1, [3, 5], "Hikkake", "Inside bar → หลุดลงหลอก → ปิดทะลุ High ของ inside bar ภายใน w แท่ง", "หลุดขึ้นหลอก → ปิดหลุด Low ภายใน w แท่ง", ["Inside bar"], [1.5, 2.5]),
    },
}
# filters per family, in the order combinations are taken (pre-registered 11 Nov 2026)
_F = {
    TF_: ["t200", "adx", "calm", "wild", "m3", "year", "gold", "r50", "range"],
    MOM: ["t200", "adx", "calm", "wild", "year", "gold", "e200r", "st", "don", "slope"],
    VOL: ["t200", "calm", "wild", "chop", "range", "m3", "year", "gold", "r50", "adx", "e200r"],
    SES: ["slope", "t200", "calm", "wild", "chop", "range", "adx", "m3", "year", "gold", "r50", "e200r", "st"],
    PA: ["raw", "t200", "calm", "wild", "chop", "range", "adx", "m3", "year", "r50"],
}
# Systems that failed tests/check_strategies BEFORE any research (random-walk bias or no signals — 11 Nov 2026); each is
# replaced by the next combination in the same pre-registered order (the extra last filter of VOL / SES exists for that)
DROPPED = {"vb_dq_wild", "vb_hv_m3", "vb_rx_year", "se_asia_range", "se_asia_m3", "se_ny_year",
           "pa_out_year", "pa_mss_year", "pa_doji_year"}
NEED = {TF_: 36, MOM: 73, VOL: 88, SES: 96, PA: 97}
PREFIX = {TF_: "tf", MOM: "mo", VOL: "vb", SES: "se", PA: "pa"}
_WHY = {TF_: "แนวโน้มมักต่อเนื่อง เพราะข่าวถูกซึมซับช้าและเงินทุนเคลื่อนช้า (Moskowitz, Ooi & Pedersen 2012; Hurst, Ooi & Pedersen 2017)",
        MOM: "สิ่งที่แรงมักแรงต่อระยะหนึ่ง (Jegadeesh & Titman 1993; Asness, Moskowitz & Pedersen 2013)",
        VOL: "ความผันผวนเกาะกลุ่ม ช่วงบีบตัวมักคลายออกเป็นการวิ่งแรง (Bollinger 2001; Crabel 1990)",
        SES: "สภาพคล่องและข่าวเข้ามาเป็นเวลา (เปิดลอนดอน/นิวยอร์ก) และ stop มักกองอยู่ที่ High/Low วันก่อน/สัปดาห์ก่อน (Osler 2003)",
        PA: "รูปแท่งบอกว่าฝ่ายไหนชนะในแท่งนั้น — ทดสอบเป็นสมมติฐาน งานวิจัยหลังหักต้นทุนยังไม่ชัด (Marshall, Young & Rose 2006)"}


def _make(fam: str, tkey: str, fkey: str):
    tfn, pkey = _T[fam][tkey][0], _T[fam][tkey][1]
    ffn = FILTERS[fkey][0]

    def fn(df, p):
        lo, sh = tfn(df, p[pkey])
        al, ash = ffn(df)
        b = lambda x: np.nan_to_num(np.asarray(x, float)).astype(bool)
        sig = _ev(b(lo) & b(al)).astype(int) - _ev(b(sh) & b(ash)).astype(int)
        return sig, {}
    return fn


V2_STRATEGIES: dict[str, dict] = {}
V2_DOCS: dict[str, tuple[list[str], str, str]] = {}
for _fam, _trig in _T.items():
    _made = 0
    for _f in _F[_fam]:
        for _t, (_fn, _pk, _pl, _ty, _de, _lo, _hi, _st, _grid, _tname, _long, _short, _ind, _tp) in _trig.items():
            if _made >= NEED[_fam]:
                break
            _ffn, _fname, _ffam, _fl, _fs, _fwhy = FILTERS[_f]
            _id = f"{PREFIX[_fam]}_{_t}_{_f}"
            if _id in DROPPED:
                continue
            V2_STRATEGIES[_id] = {
                "name": _tname + (f" · {_fname}" if _fname else " (raw)"), "family": _fam,
                "rationale": f"{_tname} — {_WHY[_fam]}" + (f" · ตัวกรอง: {_fwhy}" if _fname else ""),
                "fn": _make(_fam, _t, _f),
                "params": [{"key": _pk, "label": _pl, "type": _ty, "default": _de, "min": _lo, "max": _hi, "step": _st}],
                "grid": {_pk: _grid, "sl_atr": [1.5, 2.5], "rr": _tp},
                **({"clock_sensitive": True} if _fam == SES else {}),
            }
            V2_DOCS[_id] = (_ind + ([_fl.split(" (")[0]] if _fl else []),
                            _long + (f" ขณะ{_fl}" if _fl else "") + " (แท่งแรก)",
                            _short + (f" ขณะ{_fs}" if _fs else "") + " (แท่งแรก)")
            _made += 1
    assert _made == NEED[_fam], (_fam, _made)
assert len(V2_STRATEGIES) == 390, len(V2_STRATEGIES)
