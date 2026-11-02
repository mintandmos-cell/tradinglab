"""Pullback / short-term mean-reversion library (user's request 3 Nov 2026: "add systems up to 500 in total, mostly
pullback / short-term counter-trend").

391 systems = 23 entry TRIGGERS x 17 market FILTERS. A trigger spots a short-term excess (price stretched down / up over a
few bars); a filter decides in which market state that excess is worth fading:
  * with a trend filter it is a PULLBACK: buy a dip inside an up-trend, sell a rally inside a down-trend
    (Connors & Alvarez 2009 "Short Term Trading Strategies That Work"; buying short-term losers in long-term winners)
  * with a range / volatility filter it is MEAN REVERSION: fade stretches when the market is not trending
    (short-term reversal: Jegadeesh 1990, Lehmann 1990; liquidity provision: Nagel 2012 "Evaporating Liquidity")
  * with no filter it is the bare reversal effect (kept as a control: the pure counter-trend systems retired in Oct 2026
    lost on most markets, so the plain version is expected to fail — it still has to sit the same exam).
Economic idea: liquidity providers are paid for absorbing short-term order imbalances; over-reaction to news fades.

Library rules: signal on the bar close, entry at the next open, ONE signal parameter (2 values) — the filter has fixed
settings — exits from the shared grid (SL 1.5 / 2.5 ATR, TP 1.0 / 1.5 R: mean reversion takes quick profits), 8 combos per
system, strictly causal indicators, no volume. Each signal is an EVENT: the first bar of the stretch.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ta

PB, MR = "Pullback", "Mean reversion"


# ---------------------------------------------------------------- helpers
def _ev(cond):
    """The first bar where a condition becomes true (an event, not a state)."""
    c = np.nan_to_num(np.asarray(cond, float)).astype(bool)
    prev = np.concatenate(([False], c[:-1]))
    return c & ~prev


def _hlc(df):
    return df.high.to_numpy(float), df.low.to_numpy(float), df.close.to_numpy(float)


def _bpm(df) -> int:
    """Bars in ~a month (H1 ~520, H4 ~130, D1 ~22)."""
    if len(df) < 3:
        return 520
    d = pd.Series(df.index[-200:]).diff().dropna().median()
    h = float(d.total_seconds() / 3600) if pd.notna(d) else 1.0
    return max(1, int(round(520 / max(h, 1e-9))))


def _stoch(h, l, c, n):
    hh, ll = ta.highest(h, n), ta.lowest(l, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        return 100 * (c - ll) / (hh - ll)


def _streak(c):
    """Consecutive up (+) / down (-) closes."""
    d = np.sign(np.nan_to_num(np.diff(c, prepend=np.nan)))
    out = np.zeros(len(c))
    for i in range(1, len(c)):
        out[i] = (out[i - 1] + 1 if out[i - 1] > 0 else 1) if d[i] > 0 else ((out[i - 1] - 1 if out[i - 1] < 0 else -1) if d[i] < 0 else 0)
    return out


def _pct_rank(x, n):
    """Share of the previous n values below the current one (0..100), causal."""
    x = np.asarray(x, float)
    out = np.full(len(x), np.nan)
    if len(x) <= n:
        return out
    w = np.lib.stride_tricks.sliding_window_view(x, n + 1)
    pr = 100.0 * (w[:, :-1] < w[:, -1:]).mean(axis=1)
    pr[np.isnan(w).any(axis=1)] = np.nan
    out[n:] = pr
    return out


# ---------------------------------------------------------------- 23 triggers: (df, v) -> (stretched down, stretched up)
def t_rsi2(df, v):
    r = ta.rsi(df.close.to_numpy(float), 2)
    return r < v, r > 100 - v


def t_rsi3(df, v):
    r = ta.rsi(df.close.to_numpy(float), 3)
    return r < v, r > 100 - v


def t_rsi14(df, v):
    r = ta.rsi(df.close.to_numpy(float), 14)
    return r < v, r > 100 - v


def t_stoch(df, v):
    k = _stoch(*_hlc(df), 14)
    return k < v, k > 100 - v


def t_stoch_fast(df, v):
    k = _stoch(*_hlc(df), 5)
    return k < v, k > 100 - v


def t_cci(df, v):
    x = ta.cci(*_hlc(df), 20)
    return x < -v, x > v


def t_bb(df, v):
    c = df.close.to_numpy(float)
    _, up, lo = ta.bollinger(c, 20, v)
    return c < lo, c > up


def t_keltner(df, v):
    h, l, c = _hlc(df)
    e, a = ta.ema(c, 20), ta.atr(h, l, c, 20)
    return c < e - v * a, c > e + v * a


def t_zscore(df, v):
    c = df.close.to_numpy(float)
    n = int(v)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = (c - ta.sma(c, n)) / ta.stdev(c, n)
    return z < -2.0, z > 2.0


def t_ibs(df, v):
    h, l, c = _hlc(df)
    with np.errstate(divide="ignore", invalid="ignore"):
        ibs = (c - l) / (h - l)
    return ibs < v, ibs > 1 - v


def t_down_closes(df, v):
    s = _streak(df.close.to_numpy(float))
    return s <= -v, s >= v


def t_n_low(df, v):
    c = df.close.to_numpy(float)
    n = int(v)
    return c <= ta.lowest(c, n), c >= ta.highest(c, n)


def t_drop_atr(df, v):
    h, l, c = _hlc(df)
    a = ta.atr(h, l, c, 14)
    d = c - ta.shift(c, 5)
    return d < -v * a, d > v * a


def t_ema_dist(df, v):
    h, l, c = _hlc(df)
    x = (c - ta.ema(c, 50)) / ta.atr(h, l, c, 14)
    return x < -v, x > v


def t_ema_touch(df, v):
    h, l, c = _hlc(df)
    e = ta.ema(c, int(v))
    return (l <= e) & (c > e), (h >= e) & (c < e)


def t_retrace(df, v):
    h, l, c = _hlc(df)
    hh, ll = ta.highest(h, 20), ta.lowest(l, 20)
    rng = hh - ll
    return c < hh - v * rng, c > ll + v * rng


def t_hammer(df, v):
    o = df.open.to_numpy(float)
    h, l, c = _hlc(df)
    body = np.abs(c - o) + 1e-12
    lower, upper = np.minimum(o, c) - l, h - np.maximum(o, c)
    at_low, at_high = l <= ta.lowest(l, 10), h >= ta.highest(h, 10)
    return (lower >= v * body) & (c > l + 2 * (h - l) / 3) & at_low, (upper >= v * body) & (c < h - 2 * (h - l) / 3) & at_high


def t_reversal_bar(df, v):
    h, l, c = _hlc(df)
    n = int(v)
    pc = ta.shift(c, 1)
    return (l <= ta.lowest(l, n)) & (c > pc), (h >= ta.highest(h, n)) & (c < pc)


def t_macd_turn(df, v):
    c = df.close.to_numpy(float)
    m = ta.ema(c, 12) - ta.ema(c, 26)
    hist = m - ta.ema(m, 9)
    k = int(v)
    falling = np.ones(len(c), bool)
    rising = np.ones(len(c), bool)
    for j in range(1, k + 1):
        falling &= np.nan_to_num(ta.shift(hist, j) < ta.shift(hist, j + 1))
        rising &= np.nan_to_num(ta.shift(hist, j) > ta.shift(hist, j + 1))
    return (hist < 0) & (hist > ta.shift(hist, 1)) & falling, (hist > 0) & (hist < ta.shift(hist, 1)) & rising


def t_roc_extreme(df, v):
    c = df.close.to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = c / ta.shift(c, int(v)) - 1
    pr = _pct_rank(r, 250)
    return pr <= 5, pr >= 95


def t_crsi(df, v):
    """Connors RSI: mean of RSI(3), RSI(2) of the up/down streak and the percent rank of the 1-bar change (100)."""
    c = df.close.to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        ch = c / ta.shift(c, 1) - 1
    x = (ta.rsi(c, 3) + ta.rsi(_streak(c), 2) + _pct_rank(ch, 100)) / 3
    return x < v, x > 100 - v


def t_reclaim(df, v):
    """Close back above the 5-bar SMA after >= v closes below it (the dip ends)."""
    c = df.close.to_numpy(float)
    s = ta.sma(c, 5)
    below, above = np.nan_to_num(c < s), np.nan_to_num(c > s)
    k = int(v)
    was_below = np.ones(len(c), bool)
    was_above = np.ones(len(c), bool)
    for j in range(1, k + 1):
        was_below &= ta.shift(below.astype(float), j) == 1
        was_above &= ta.shift(above.astype(float), j) == 1
    return (c > s) & was_below, (c < s) & was_above


def t_capitulation(df, v):
    """One bar that falls (rises) more than v x ATR — a forced-selling / short-covering bar."""
    o = df.open.to_numpy(float)
    h, l, c = _hlc(df)
    a = ta.shift(ta.atr(h, l, c, 14), 1)
    return (o - c) > v * a, (c - o) > v * a


def t_below_low(df, v):
    """v bars in a row closing below the previous bar's low (above its high) — a run of breakdowns to fade."""
    h, l, c = _hlc(df)
    dn, up = np.nan_to_num(c < ta.shift(l, 1)), np.nan_to_num(c > ta.shift(h, 1))
    k = int(v)
    a, b = np.ones(len(c), bool), np.ones(len(c), bool)
    for j in range(k):
        a &= ta.shift(dn.astype(float), j) == 1 if j else dn.astype(bool)
        b &= ta.shift(up.astype(float), j) == 1 if j else up.astype(bool)
    return a, b


TRIGGERS = {   # key: (fn, param key, label, type, default, lo, hi, step, grid, name, Thai: down / up condition, indicators)
    "rsi2": (t_rsi2, "level", "RSI(2) ระดับ", "int", 10, 2, 30, 1, [5, 10], "RSI(2)", "RSI(2) ต่ำกว่า level", "RSI(2) สูงกว่า 100 − level", ["RSI(2)"]),
    "rsi3": (t_rsi3, "level", "RSI(3) ระดับ", "int", 15, 5, 30, 1, [10, 20], "RSI(3)", "RSI(3) ต่ำกว่า level", "RSI(3) สูงกว่า 100 − level", ["RSI(3)"]),
    "rsi14": (t_rsi14, "level", "RSI(14) ระดับ", "int", 35, 15, 45, 1, [35, 40], "RSI(14) Oversold", "RSI(14) ต่ำกว่า level", "RSI(14) สูงกว่า 100 − level", ["RSI(14)"]),
    "stoch": (t_stoch, "level", "%K(14) ระดับ", "int", 15, 5, 30, 1, [10, 20], "Stochastic(14)", "%K(14) ต่ำกว่า level", "%K(14) สูงกว่า 100 − level", ["Stochastic %K(14)"]),
    "stochf": (t_stoch_fast, "level", "%K(5) ระดับ", "int", 10, 2, 30, 1, [5, 15], "Fast Stochastic(5)", "%K(5) ต่ำกว่า level", "%K(5) สูงกว่า 100 − level", ["Stochastic %K(5)"]),
    "cci": (t_cci, "level", "CCI ระดับ", "int", 100, 50, 250, 10, [100, 150], "CCI(20) Extreme", "CCI(20) ต่ำกว่า −level", "CCI(20) สูงกว่า +level", ["CCI(20)"]),
    "bb": (t_bb, "k", "Bollinger k", "float", 2.0, 1.0, 3.5, 0.1, [2.0, 2.5], "Bollinger Stretch", "ราคาปิดใต้ Bollinger ล่าง (20, k)", "ราคาปิดเหนือ Bollinger บน (20, k)", ["Bollinger Bands (20, k)"]),
    "kelt": (t_keltner, "k", "Keltner k × ATR", "float", 1.0, 0.5, 3.5, 0.1, [1.0, 1.5], "Keltner Stretch", "ราคาปิดต่ำกว่า EMA20 − k × ATR20", "ราคาปิดสูงกว่า EMA20 + k × ATR20", ["EMA20", "ATR20"]),
    "z": (t_zscore, "n", "Z-score ช่วง", "int", 20, 5, 60, 1, [10, 20], "Z-score Stretch", "Z-score ของราคาปิด (n) ต่ำกว่า −2", "Z-score สูงกว่า +2", ["SMA (n)", "ส่วนเบี่ยงเบนมาตรฐาน (n)"]),
    "ibs": (t_ibs, "level", "IBS ระดับ", "float", 0.15, 0.05, 0.4, 0.05, [0.1, 0.2], "Internal Bar Strength", "ราคาปิดอยู่ช่วงล่างของแท่ง (IBS < level)", "ราคาปิดอยู่ช่วงบนของแท่ง (IBS > 1 − level)", ["IBS = (ปิด − ต่ำ) ÷ (สูง − ต่ำ)"]),
    "dn": (t_down_closes, "n", "ปิดลงติดกัน (แท่ง)", "int", 3, 2, 7, 1, [3, 4], "Consecutive Closes", "ราคาปิดลดลงติดกัน n แท่ง", "ราคาปิดเพิ่มขึ้นติดกัน n แท่ง", ["จำนวนแท่งที่ปิดลง / ขึ้นติดกัน"]),
    "nlow": (t_n_low, "n", "ปิดต่ำสุดรอบ (แท่ง)", "int", 7, 3, 20, 1, [5, 10], "N-Bar Low Close", "ราคาปิดต่ำสุดในรอบ n แท่ง", "ราคาปิดสูงสุดในรอบ n แท่ง", ["ราคาปิดต่ำสุด / สูงสุด n แท่ง"]),
    "drop": (t_drop_atr, "k", "ร่วงใน 5 แท่ง (× ATR)", "float", 2.0, 1.0, 4.0, 0.1, [1.5, 2.5], "5-Bar Drop", "ราคาลดลงใน 5 แท่งเกิน k × ATR14", "ราคาเพิ่มขึ้นใน 5 แท่งเกิน k × ATR14", ["ATR14", "การเปลี่ยนแปลง 5 แท่ง"]),
    "edist": (t_ema_dist, "k", "ห่าง EMA50 (× ATR)", "float", 1.5, 0.5, 5.0, 0.1, [1.0, 1.5], "Stretch from EMA50", "ราคาปิดต่ำกว่า EMA50 เกิน k × ATR14", "ราคาปิดสูงกว่า EMA50 เกิน k × ATR14", ["EMA50", "ATR14"]),
    "touch": (t_ema_touch, "n", "EMA ที่แตะ", "int", 20, 10, 100, 1, [20, 50], "EMA Touch", "Low แตะ EMA(n) แล้วปิดเหนือเส้น", "High แตะ EMA(n) แล้วปิดใต้เส้น", ["EMA (n)"]),
    "fib": (t_retrace, "f", "ย่อจากยอด (สัดส่วนกรอบ 20)", "float", 0.5, 0.3, 0.8, 0.01, [0.5, 0.618], "Swing Retracement", "ราคาย่อจาก High 20 แท่งเกิน f ของกรอบ (เช่น 50% / 61.8%)", "ราคาเด้งจาก Low 20 แท่งเกิน f ของกรอบ", ["High / Low 20 แท่ง"]),
    "pin": (t_hammer, "k", "ไส้ ≥ k × ตัวแท่ง", "float", 2.0, 1.0, 4.0, 0.5, [2.0, 3.0], "Hammer at Low", "แท่ง Hammer (ไส้ล่างยาว ≥ k × ตัวแท่ง ปิดช่วงบน) ที่ Low 10 แท่ง", "แท่ง Shooting star ที่ High 10 แท่ง", ["รูปแท่งเทียน", "High / Low 10 แท่ง"]),
    "rev": (t_reversal_bar, "n", "Low ใหม่รอบ (แท่ง)", "int", 7, 3, 20, 1, [5, 10], "Key Reversal", "ทำ Low ใหม่รอบ n แท่งแต่ปิดสูงกว่าแท่งก่อน", "ทำ High ใหม่รอบ n แท่งแต่ปิดต่ำกว่าแท่งก่อน", ["High / Low n แท่ง"]),
    "macd": (t_macd_turn, "k", "Histogram ลดติดกัน (แท่ง)", "int", 2, 1, 5, 1, [2, 3], "MACD Histogram Turn", "MACD histogram ติดลบ ลดลง k แท่งแล้วหักขึ้น", "Histogram เป็นบวก เพิ่มขึ้น k แท่งแล้วหักลง", ["MACD (12, 26, 9)"]),
    "roc": (t_roc_extreme, "n", "ROC ช่วง (แท่ง)", "int", 3, 1, 10, 1, [3, 5], "ROC Extreme", "การเปลี่ยนแปลง n แท่งต่ำสุด 5% ของ 250 แท่งที่ผ่านมา", "สูงสุด 5% ของ 250 แท่ง", ["ROC (n)", "Percent rank 250 แท่ง"]),
    "crsi": (t_crsi, "level", "Connors RSI ระดับ", "int", 15, 5, 30, 1, [10, 20], "Connors RSI", "Connors RSI ต่ำกว่า level", "Connors RSI สูงกว่า 100 − level", ["RSI(3)", "RSI ของ streak", "Percent rank 100"]),
    "reclaim": (t_reclaim, "k", "ปิดใต้ SMA5 อย่างน้อย (แท่ง)", "int", 3, 2, 6, 1, [3, 5], "SMA5 Reclaim", "ปิดกลับเหนือ SMA5 หลังปิดใต้ ≥ k แท่ง", "ปิดกลับใต้ SMA5 หลังปิดเหนือ ≥ k แท่ง", ["SMA5"]),
    "pll": (t_below_low, "k", "ปิดใต้ Low แท่งก่อนติดกัน (แท่ง)", "int", 1, 1, 3, 1, [1, 2], "Close Below Prior Low", "ราคาปิดต่ำกว่า Low ของแท่งก่อน k แท่งติดกัน", "ราคาปิดสูงกว่า High ของแท่งก่อน k แท่งติดกัน", ["High / Low แท่งก่อน"]),
    "capit": (t_capitulation, "k", "แท่งร่วง ≥ k × ATR", "float", 1.5, 1.0, 3.0, 0.1, [1.5, 2.0], "Capitulation Bar", "แท่งเดียวร่วง (เปิด − ปิด) เกิน k × ATR14", "แท่งเดียวพุ่งเกิน k × ATR14", ["ATR14", "ตัวแท่ง"]),
}


# ---------------------------------------------------------------- 17 filters: df -> (allow long, allow short)
def _f_none(df):
    n = len(df)
    return np.ones(n, bool), np.ones(n, bool)


def _f_sma200(df):
    c = df.close.to_numpy(float)
    m = ta.sma(c, 200)
    return c > m, c < m


def _f_ema100(df):
    c = df.close.to_numpy(float)
    m = ta.ema(c, 100)
    return c > m, c < m


def _f_ema50_up(df):
    e = ta.ema(df.close.to_numpy(float), 50)
    return e > ta.shift(e, 10), e < ta.shift(e, 10)


def _f_golden(df):
    c = df.close.to_numpy(float)
    f, s = ta.sma(c, 50), ta.sma(c, 200)
    return f > s, f < s


def _f_adx_range(df):
    adx = ta.dmi(*_hlc(df), 14)[2]
    return adx < 20, adx < 20


def _f_adx_trend(df):
    pdi, mdi, adx = ta.dmi(*_hlc(df), 14)
    return (adx > 25) & (pdi > mdi), (adx > 25) & (mdi > pdi)


def _f_donchian(df):
    h, l, c = _hlc(df)
    mid = (ta.highest(h, 100) + ta.lowest(l, 100)) / 2
    return c > mid, c < mid


def _f_year_mid(df):
    h, l, c = _hlc(df)
    n = 12 * _bpm(df)
    mid = (ta.highest(h, n) + ta.lowest(l, n)) / 2
    return c > mid, c < mid


def _f_supertrend(df):
    st = ta.supertrend(*_hlc(df), 10, 3.0)
    d = st[1] if isinstance(st, tuple) else st
    return np.asarray(d) > 0, np.asarray(d) < 0


def _f_calm(df):
    h, l, c = _hlc(df)
    q = ta.atr(h, l, c, 14) < ta.atr(h, l, c, 100)
    return q, q


def _f_wild(df):
    h, l, c = _hlc(df)
    q = ta.atr(h, l, c, 14) > ta.atr(h, l, c, 100)
    return q, q


def _f_mom1m(df):
    c = df.close.to_numpy(float)
    r = c - ta.shift(c, _bpm(df))
    return r > 0, r < 0


def _f_mom3m(df):
    c = df.close.to_numpy(float)
    r = c - ta.shift(c, 3 * _bpm(df))
    return r > 0, r < 0


def _f_choppy(df):
    c = df.close.to_numpy(float)
    net = np.abs(c - ta.shift(c, 20))
    path = ta.rolling_sum(np.abs(np.diff(c, prepend=np.nan)), 20)
    with np.errstate(divide="ignore", invalid="ignore"):
        er = net / path
    q = er < 0.3
    return q, q


def _f_ema200_rising(df):
    c = df.close.to_numpy(float)
    e = ta.ema(c, 200)
    return (c > e) & (e > ta.shift(e, 20)), (c < e) & (e < ta.shift(e, 20))


def _f_rsi50(df):
    r = ta.rsi(df.close.to_numpy(float), 50)
    return r > 50, r < 50


FILTERS = {   # key: (fn, name, family, Thai long / short rule, why)
    "raw": (_f_none, "", MR, "", "", "ไม่มีตัวกรอง — การกลับตัวระยะสั้นล้วน ๆ (Jegadeesh 1990, Lehmann 1990) ใช้เป็นตัวเทียบ"),
    "t200": (_f_sma200, "above SMA200", PB, "ราคาเหนือ SMA200", "ราคาใต้ SMA200", "ซื้อตอนย่อในขาขึ้นใหญ่ (Connors & Alvarez 2009)"),
    "t100": (_f_ema100, "above EMA100", PB, "ราคาเหนือ EMA100", "ราคาใต้ EMA100", "ซื้อตอนย่อเมื่อราคาอยู่เหนือค่าเฉลี่ยกลาง"),
    "slope": (_f_ema50_up, "EMA50 rising", PB, "EMA50 ชี้ขึ้น (สูงกว่า 10 แท่งก่อน)", "EMA50 ชี้ลง", "ย่อตัวระหว่างแนวโน้มที่ยังชันอยู่"),
    "gold": (_f_golden, "Golden-cross regime", PB, "SMA50 เหนือ SMA200", "SMA50 ใต้ SMA200", "ซื้อตอนย่อในช่วงตลาดกระทิงระยะกลาง"),
    "range": (_f_adx_range, "ADX < 20", MR, "ADX ต่ำกว่า 20 (ไม่มีเทรนด์)", "ADX ต่ำกว่า 20", "สวนเฉพาะตอนตลาดไม่มีเทรนด์ ราคามักแกว่งกลับกลาง"),
    "adx": (_f_adx_trend, "ADX > 25 trend", PB, "ADX > 25 และ +DI > −DI", "ADX > 25 และ −DI > +DI", "ย่อตัวในเทรนด์ที่แข็งแรง (วัดด้วย ADX)"),
    "don": (_f_donchian, "upper half of 100-bar range", PB, "ราคาอยู่ครึ่งบนของกรอบ 100 แท่ง", "ราคาอยู่ครึ่งล่างของกรอบ 100 แท่ง", "ย่อตัวเมื่อราคายังอยู่ฝั่งแข็งแรงของกรอบ"),
    "year": (_f_year_mid, "upper half of 12-month range", PB, "ราคาอยู่ครึ่งบนของกรอบ 12 เดือน", "ราคาอยู่ครึ่งล่างของกรอบ 12 เดือน", "ย่อตัวในตลาดที่แข็งแรงรอบปี (George & Hwang 2004)"),
    "st": (_f_supertrend, "Supertrend up", PB, "Supertrend (10, 3) เป็นขาขึ้น", "Supertrend เป็นขาลง", "ย่อตัวเมื่อ Supertrend ยังชี้ทางเดิม"),
    "calm": (_f_calm, "calm volatility", MR, "ATR14 ต่ำกว่า ATR100 (ตลาดเงียบ)", "ATR14 ต่ำกว่า ATR100", "ตลาดเงียบมักแกว่งในกรอบ การยืดตัวจึงกลับมาง่าย"),
    "wild": (_f_wild, "high volatility", MR, "ATR14 สูงกว่า ATR100 (ตลาดผันผวน)", "ATR14 สูงกว่า ATR100", "ช่วงผันผวน ผู้ให้สภาพคล่องได้ค่าตอบแทนสูง (Nagel 2012)"),
    "m1": (_f_mom1m, "1-month momentum up", PB, "ราคาสูงกว่า 1 เดือนก่อน", "ราคาต่ำกว่า 1 เดือนก่อน", "ย่อตัวระยะสั้นในโมเมนตัม 1 เดือน"),
    "m3": (_f_mom3m, "3-month momentum up", PB, "ราคาสูงกว่า 3 เดือนก่อน", "ราคาต่ำกว่า 3 เดือนก่อน", "ย่อตัวในโมเมนตัม 3 เดือน (Moskowitz et al. 2012)"),
    "chop": (_f_choppy, "choppy market (ER < 0.3)", MR, "Efficiency ratio 20 แท่ง < 0.3 (ตลาดเหวี่ยง)", "Efficiency ratio < 0.3", "ตลาดเหวี่ยงไม่มีทิศ (Kaufman) — สวนการยืดตัว"),
    "e200r": (_f_ema200_rising, "above rising EMA200", PB, "ราคาเหนือ EMA200 ที่ชี้ขึ้น", "ราคาใต้ EMA200 ที่ชี้ลง", "ย่อตัวในขาขึ้นใหญ่ที่ยังชันขึ้น"),
    "r50": (_f_rsi50, "RSI(50) > 50", PB, "RSI(50) เหนือ 50", "RSI(50) ใต้ 50", "ย่อตัวเมื่อแรงซื้อระยะยาวยังเหนือกว่า"),
}


# ---------------------------------------------------------------- build the 23 x 17 systems
def _make(tkey: str, fkey: str):
    tfn, pkey = TRIGGERS[tkey][0], TRIGGERS[tkey][1]
    ffn = FILTERS[fkey][0]

    def fn(df, p):
        dn, up = tfn(df, p[pkey])
        al, ash = ffn(df)
        long_ = _ev(np.nan_to_num(np.asarray(dn, float)).astype(bool) & np.nan_to_num(np.asarray(al, float)).astype(bool))
        short = _ev(np.nan_to_num(np.asarray(up, float)).astype(bool) & np.nan_to_num(np.asarray(ash, float)).astype(bool))
        sig = long_.astype(int) - short.astype(int)
        return sig, {}
    return fn


# 23 triggers x 17 filters, minus 5 pairs that failed the library checks before any research (3 Nov 2026: almost never fire
# on random prices, or a random-walk bias), replaced by the 24th trigger on 5 filters -> 391
DROPPED = {"mr_rsi14_slope", "mr_rsi14_adx", "mr_rsi14_st", "mr_rsi14_e200r", "mr_roc_calm"}
PLL_FILTERS = ("raw", "t200", "range", "calm", "chop")
MR_STRATEGIES: dict[str, dict] = {}
MR_DOCS: dict[str, tuple[list[str], str, str]] = {}
for _t, (_fn, _pk, _pl, _ty, _de, _lo, _hi, _st, _grid, _tname, _down, _up, _ind) in TRIGGERS.items():
    for _f, (_ffn, _fname, _fam, _fl, _fs, _why) in FILTERS.items():
        _id = f"mr_{_t}_{_f}"
        if _id in DROPPED or (_t == "pll" and _f not in PLL_FILTERS):
            continue
        MR_STRATEGIES[_id] = {
            "name": f"{_tname}" + (f" · {_fname}" if _fname else " (raw)"), "family": _fam,
            "rationale": f"สวนการยืดตัวระยะสั้น ({_tname}) — {_why} · ผู้ให้สภาพคล่องได้ค่าตอบแทนจากการรับแรงซื้อขายที่ไม่สมดุลระยะสั้น",
            "fn": _make(_t, _f),
            "params": [{"key": _pk, "label": _pl, "type": _ty, "default": _de, "min": _lo, "max": _hi, "step": _st}],
            "grid": {_pk: _grid, "sl_atr": [1.5, 2.5], "rr": [1.0, 1.5]},
        }
        MR_DOCS[_id] = (_ind + ([_fl.split(" (")[0]] if _fl else []),
                        _down + (f" ขณะ{_fl}" if _fl else "") + " (แท่งแรก)",
                        _up + (f" ขณะ{_fs}" if _fs else "") + " (แท่งแรก)")
assert len(MR_STRATEGIES) == 391, len(MR_STRATEGIES)
