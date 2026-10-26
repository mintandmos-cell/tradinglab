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
    {"key": "rr", "label": "TP (x ระยะ SL = R:R)", "type": "float", "default": 2.0, "min": 0.5, "max": 6, "step": 0.1},
    {"key": "max_hold_h", "label": "ถือสูงสุด (ชั่วโมง)", "type": "int", "default": 120, "min": 4, "max": 120, "step": 1},
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
}


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
    return [{"id": k, "name": v["name"], "family": v["family"], "rationale": v["rationale"],
             "params": schema(k), "grid": v["grid"], "grid_size": len(grid(k, 120))} for k, v in STRATEGIES.items()]
