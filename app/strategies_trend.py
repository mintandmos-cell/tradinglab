"""Trend-following library extension (user's request 2 Nov 2026: "100 trend-following systems to put through the QWG").

69 systems (#58-#126) that, with the 31 trend / breakout / momentum systems already in app/strategies.py, make 100.
Every one rests on the same economic idea — prices trend because information spreads slowly, investors herd and
under-react, and hedgers / central banks move gradually (Moskowitz, Ooi & Pedersen 2012 "Time Series Momentum";
Hurst, Ooi & Pedersen 2017 "A Century of Evidence on Trend-Following"). They differ in HOW a trend is recognised:
moving averages, channels, momentum oscillators, trend-strength filters, volatility regimes, price structure.

Rules kept from the library: signal on the bar close, entry at the next open, <= 3 signal params, small grids
(trend families also try rr = 0 = no TP), no volume (Yahoo FX volume is empty), strictly causal indicators.
Signals are either an EVENT (_sig: the bar where a condition starts) or a REGIME FLIP (_flip: the bar where an
always-in state changes side).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ta

TF, MOM, VB = "Trend following", "Momentum", "Volatility breakout"


# ---------------------------------------------------------------- helpers
def _sig(long_, short):
    return (np.nan_to_num(np.asarray(long_, float)).astype(bool).astype(int)
            - np.nan_to_num(np.asarray(short, float)).astype(bool).astype(int))


def _flip(state):
    st = pd.Series(np.asarray(state, float)).replace(0, np.nan).ffill().fillna(0).to_numpy()
    prev = ta.shift(st)
    prev = np.where(np.isnan(prev), 0, prev)
    return _sig((st > 0) & (prev <= 0), (st < 0) & (prev >= 0))


def _side(x, eps=0.0):
    """+1 above eps, -1 below -eps, 0 otherwise / nan."""
    x = np.asarray(x, float)
    return np.where(np.isnan(x), 0, np.where(x > eps, 1, np.where(x < -eps, -1, 0)))


def _cross(a, b):
    """+1 where a crosses above b, -1 where it crosses below."""
    pa, pb = ta.shift(a), ta.shift(b)
    return _sig((a > b) & (pa <= pb), (a < b) & (pa >= pb))


def _c(df):
    return df.close.to_numpy(float)


def _hlc(df):
    return df.high.to_numpy(float), df.low.to_numpy(float), df.close.to_numpy(float)


def _bpm(df) -> int:
    """Bars in ~a month (520 trading hours): H1 ~520, H4 ~130, D1 ~22."""
    if len(df) < 3:
        return 520
    d = pd.Series(df.index[-200:]).diff().dropna().median()
    h = float(d.total_seconds() / 3600) if pd.notna(d) else 1.0
    return max(1, int(round(520 / max(h, 1e-9))))


def _roc(c, n):
    with np.errstate(divide="ignore", invalid="ignore"):
        return c / ta.shift(c, n) - 1


def _dema(x, n):
    e = ta.ema(x, n)
    return 2 * e - ta.ema(e, n)


def _zlema(x, n):
    lag = max(1, (n - 1) // 2)
    return ta.ema(x + (x - ta.shift(x, lag)), n)


def _t3(x, n, v=0.7):
    e1 = ta.ema(x, n); e2 = ta.ema(e1, n); e3 = ta.ema(e2, n); e4 = ta.ema(e3, n); e5 = ta.ema(e4, n); e6 = ta.ema(e5, n)
    c1, c2, c3, c4 = -v ** 3, 3 * v ** 2 + 3 * v ** 3, -6 * v ** 2 - 3 * v - 3 * v ** 3, 1 + 3 * v + v ** 3 + 3 * v ** 2
    return c1 * e6 + c2 * e5 + c3 * e4 + c4 * e3


def _alma(x, n, offset=0.85, sigma=6.0):
    m, s = offset * (n - 1), n / sigma
    w = np.exp(-((np.arange(n) - m) ** 2) / (2 * s * s)); w /= w.sum()
    xx = np.nan_to_num(x)
    out = np.convolve(xx, w[::-1], "full")[: len(x)]        # out[i] = sum_j w[j] * x[i-n+1+j]  (causal)
    bad = pd.Series(np.isnan(x).astype(float)).rolling(n, min_periods=1).max().to_numpy() > 0
    out[bad] = np.nan
    out[: n - 1] = np.nan
    return out


def _mcginley(x, n):
    out = np.full(len(x), np.nan)
    md = np.nan
    for i, v in enumerate(x):
        if np.isnan(v):
            continue
        if np.isnan(md):
            md = v
        else:
            r = v / md if md else 1.0
            md = md + (v - md) / (n * max(r, 1e-9) ** 4)
        out[i] = md
    out[: n - 1] = np.nan
    return out


def _stoch(h, l, c, n):
    hh, ll = ta.highest(h, n), ta.lowest(l, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        return 100 * (c - ll) / (hh - ll)


def _cmo(c, n):
    d = np.diff(c, prepend=np.nan)
    up = ta.rolling_sum(np.where(d > 0, d, 0.0), n)
    dn = ta.rolling_sum(np.where(d < 0, -d, 0.0), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        return 100 * (up - dn) / (up + dn)


def _tsi(c, r=25, s=13):
    d = np.diff(c, prepend=np.nan)
    d = np.nan_to_num(d)
    num, den = ta.ema(ta.ema(d, r), s), ta.ema(ta.ema(np.abs(d), r), s)
    with np.errstate(divide="ignore", invalid="ignore"):
        return 100 * num / den


def _er(c, n):
    """Kaufman efficiency ratio (signed): net move / path length over n bars."""
    net = c - ta.shift(c, n)
    path = ta.rolling_sum(np.abs(np.diff(c, prepend=np.nan)), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        return net / path


def _chop(h, l, c, n):
    tr = np.nanmax(np.vstack([h - l, np.abs(h - ta.shift(c)), np.abs(l - ta.shift(c))]), axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        return 100 * np.log10(ta.rolling_sum(tr, n) / (ta.highest(h, n) - ta.lowest(l, n))) / np.log10(n)


def _rsq(c, n):
    """R-squared of a straight-line fit to the last n closes (how 'linear' = trending the move is)."""
    s = pd.Series(c)
    t = pd.Series(np.arange(len(c), dtype=float))
    corr = s.rolling(n, min_periods=n).corr(t)
    return (corr ** 2).to_numpy()


# ---------------------------------------------------------------- 1. moving averages (#58-#71)
def _sma_cross(df, p):
    c = _c(df); f, s = ta.sma(c, int(p["fast"])), ta.sma(c, int(p["fast"]) * int(p["mult"]))
    return _cross(f, s), {"SMA fast": f, "SMA slow": s}


def _dema_cross(df, p):
    c = _c(df); f, s = _dema(c, int(p["fast"])), _dema(c, int(p["fast"]) * 3)
    return _cross(f, s), {"DEMA fast": f, "DEMA slow": s}


def _price_ema(df, p):
    c = _c(df); e = ta.ema(c, int(p["n"]))
    return _flip(_side(c - e)), {"EMA": e}


def _golden_cross(df, p):
    c = _c(df); f, s = ta.sma(c, int(p["slow"]) // 4), ta.sma(c, int(p["slow"]))
    return _cross(f, s), {"SMA fast": f, "SMA slow": s}


def _ribbon(df, p):
    c = _c(df); n = int(p["n"]); a, b, d = ta.ema(c, n), ta.ema(c, 2 * n), ta.ema(c, 4 * n)
    st = np.where((c > a) & (a > b) & (b > d), 1, np.where((c < a) & (a < b) & (b < d), -1, 0))
    return _flip(st), {"EMA n": a, "EMA 2n": b, "EMA 4n": d}


def _ema_slope(df, p):
    c = _c(df); e = ta.ema(c, int(p["n"]))
    return _flip(_side(e - ta.shift(e, int(p["k"])))), {"EMA": e}


def _ma_envelope(df, p):
    c = _c(df); m = ta.sma(c, int(p["n"])); k = float(p["pct"]) / 100
    st = np.where(c > m * (1 + k), 1, np.where(c < m * (1 - k), -1, 0))
    return _flip(st), {"SMA": m, "Upper": m * (1 + k), "Lower": m * (1 - k)}


def _mcginley_trend(df, p):
    c = _c(df); m = _mcginley(c, int(p["n"]))
    return _flip(_side(c - m)), {"McGinley": m}


def _t3_trend(df, p):
    c = _c(df); t = _t3(c, int(p["n"]))
    return _flip(_side(t - ta.shift(t))), {"T3": t}


def _zlema_cross(df, p):
    c = _c(df); f, s = _zlema(c, int(p["fast"])), _zlema(c, int(p["fast"]) * 3)
    return _cross(f, s), {"ZLEMA fast": f, "ZLEMA slow": s}


def _alma_trend(df, p):
    c = _c(df); a = _alma(c, int(p["n"]))
    return _flip(_side(a - ta.shift(a, 2))), {"ALMA": a}


def _hma_cross(df, p):
    c = _c(df); n = int(p["fast"])
    def hma(x, k):
        return ta.wma(2 * ta.wma(x, max(1, k // 2)) - ta.wma(x, k), max(1, int(np.sqrt(k))))
    f, s = hma(c, n), hma(c, n * 3)
    return _cross(f, s), {"HMA fast": f, "HMA slow": s}


def _mtf_ema(df, p):
    """Higher-timeframe direction (slow EMA ~ n days) + lower-timeframe EMA cross in the same direction."""
    c = _c(df); bpd = max(1, _bpm(df) // 22)
    big = ta.ema(c, int(p["days"]) * bpd)
    f, s = ta.ema(c, 10), ta.ema(c, 30)
    x = _cross(f, s)
    return _sig((x > 0) & (c > big), (x < 0) & (c < big)), {"EMA (days)": big, "EMA10": f, "EMA30": s}


def _ma_stack_pullback(df, p):
    """Trend = EMA50 > EMA200; enter when price closes back above EMA20 after dipping below (continuation)."""
    c = _c(df); e20, e50, e200 = ta.ema(c, int(p["fast"])), ta.ema(c, 50), ta.ema(c, 200)
    x = _cross(c, e20)
    return _sig((x > 0) & (e50 > e200), (x < 0) & (e50 < e200)), {"EMA fast": e20, "EMA50": e50, "EMA200": e200}


# ---------------------------------------------------------------- 2. channels / breakouts (#72-#86)
def _donchian_mid(df, p):
    h, l, c = _hlc(df); n = int(p["n"])
    mid = (ta.shift(ta.highest(h, n)) + ta.shift(ta.lowest(l, n))) / 2
    return _flip(_side(c - mid)), {"Channel mid": mid}


def _keltner_trend(df, p):
    h, l, c = _hlc(df); mid, up, dn = ta.keltner(h, l, c, int(p["n"]), float(p["mult"]))
    st = np.where(c > up, 1, np.where(c < dn, -1, 0))
    return _flip(st), {"Keltner mid": mid, "Upper": up, "Lower": dn}


def _atr_bands(df, p):
    h, l, c = _hlc(df); m = ta.sma(c, int(p["n"])); a = ta.atr(h, l, c, int(p["n"]))
    up, dn = m + float(p["k"]) * a, m - float(p["k"]) * a
    st = np.where(c > up, 1, np.where(c < dn, -1, 0))
    return _flip(st), {"SMA": m, "Upper": up, "Lower": dn}


def _bb_trend(df, p):
    c = _c(df); mid, up, dn = ta.bollinger(c, int(p["n"]), float(p["k"]))
    st = np.where(c > up, 1, np.where(c < dn, -1, 0))
    return _flip(st), {"BB mid": mid, "BB up": up, "BB low": dn}


def _chandelier(df, p):
    h, l, c = _hlc(df); n = int(p["n"]); a = ta.atr(h, l, c, n); k = float(p["k"])
    long_stop, short_stop = ta.highest(h, n) - k * a, ta.lowest(l, n) + k * a
    st = np.zeros(len(c))
    cur = 0
    for i in range(1, len(c)):
        if np.isnan(long_stop[i - 1]) or np.isnan(short_stop[i - 1]):
            continue
        if cur <= 0 and c[i] > short_stop[i - 1]:
            cur = 1
        elif cur >= 0 and c[i] < long_stop[i - 1]:
            cur = -1
        st[i] = cur
    return _flip(st), {"Long stop": long_stop, "Short stop": short_stop}


def _close_breakout(df, p):
    c = _c(df); n = int(p["n"])
    hi, lo = ta.shift(ta.highest(c, n)), ta.shift(ta.lowest(c, n))
    return _sig((c > hi) & (ta.shift(c) <= ta.shift(hi)), (c < lo) & (ta.shift(c) >= ta.shift(lo))), {"Close high": hi, "Close low": lo}


def _linreg_channel(df, p):
    c = _c(df); n = int(p["n"]); k = float(p["k"])
    s = pd.Series(c)
    slope = ta.linreg_slope(c, n)
    mean = s.rolling(n, min_periods=n).mean().to_numpy()
    fit = mean + slope * (n - 1) / 2                       # regression value at the last bar
    sd = s.rolling(n, min_periods=n).std(ddof=0).to_numpy()
    up, dn = ta.shift(fit + k * sd), ta.shift(fit - k * sd)
    st = np.where(c > up, 1, np.where(c < dn, -1, 0))
    return _flip(st), {"Reg up": up, "Reg low": dn}


def _tsf_cross(df, p):
    c = _c(df); n = int(p["n"])
    slope = ta.linreg_slope(c, n)
    mean = ta.sma(c, n)
    tsf = mean + slope * ((n - 1) / 2 + 1)                 # one-bar-ahead forecast
    return _flip(_side(c - ta.shift(tsf))), {"TSF": tsf}


def _median_channel(df, p):
    c = _c(df); n = int(p["n"])
    med = pd.Series(c).rolling(n, min_periods=n).median().to_numpy()
    a = ta.atr(*_hlc(df), 14)
    st = np.where(c > med + float(p["k"]) * a, 1, np.where(c < med - float(p["k"]) * a, -1, 0))
    return _flip(st), {"Median": med}


def _ichimoku_cloud(df, p):
    h, l, c = _hlc(df); n = int(p["base"])
    tenkan = (ta.highest(h, n // 3) + ta.lowest(l, n // 3)) / 2
    kijun = (ta.highest(h, n) + ta.lowest(l, n)) / 2
    span_a = ta.shift((tenkan + kijun) / 2, n)
    span_b = ta.shift((ta.highest(h, 2 * n) + ta.lowest(l, 2 * n)) / 2, n)
    top, bot = np.fmax(span_a, span_b), np.fmin(span_a, span_b)
    st = np.where(c > top, 1, np.where(c < bot, -1, 0))
    return _flip(st), {"Cloud top": top, "Cloud bottom": bot}


def _kijun_cross(df, p):
    h, l, c = _hlc(df); n = int(p["n"])
    k = (ta.highest(h, n) + ta.lowest(l, n)) / 2
    return _cross(c, ta.shift(k)), {"Kijun": k}


def _gann_hilo(df, p):
    h, l, c = _hlc(df); n = int(p["n"])
    hi, lo = ta.shift(ta.sma(h, n)), ta.shift(ta.sma(l, n))
    st = np.where(c > hi, 1, np.where(c < lo, -1, 0))
    return _flip(st), {"SMA highs": hi, "SMA lows": lo}


def _donchian_width(df, p):
    """Breakout that counts only when the channel is wide enough to be a real move (width > k x ATR)."""
    h, l, c = _hlc(df); n = int(p["n"])
    hi, lo = ta.shift(ta.highest(h, n)), ta.shift(ta.lowest(l, n))
    a = ta.atr(h, l, c, 14)
    wide = (hi - lo) > float(p["k"]) * a
    return _sig((c > hi) & (ta.shift(c) <= ta.shift(hi)) & wide, (c < lo) & (ta.shift(c) >= ta.shift(lo)) & wide), {"High": hi, "Low": lo}


def _dual_thrust(df, p):
    """Dual Thrust (M. Chalek): range = max(HH-LC, HC-LL) of the last n bars; break of open +/- k x range."""
    o = df.open.to_numpy(float); h, l, c = _hlc(df); n = int(p["n"]); k = float(p["k"])
    hh, hc, lc, ll = ta.shift(ta.highest(h, n)), ta.shift(ta.highest(c, n)), ta.shift(ta.lowest(c, n)), ta.shift(ta.lowest(l, n))
    rng = np.fmax(hh - lc, hc - ll)
    up, dn = o + k * rng, o - k * rng
    trend = ta.ema(c, 100)
    return _sig((c > up) & (c > trend), (c < dn) & (c < trend)), {"EMA100": trend}


def _volatility_stop(df, p):
    """Wilder's Volatility System: stop-and-reverse k x ATR from the most favourable close."""
    h, l, c = _hlc(df); a = ta.atr(h, l, c, int(p["n"])); k = float(p["k"])
    st = np.zeros(len(c)); cur, ext = 0, np.nan
    for i in range(len(c)):
        if np.isnan(a[i]):
            continue
        if cur == 0:
            cur, ext = (1 if i and c[i] >= c[i - 1] else -1), c[i]
        elif cur > 0:
            ext = max(ext, c[i])
            if c[i] < ext - k * a[i]:
                cur, ext = -1, c[i]
        else:
            ext = min(ext, c[i])
            if c[i] > ext + k * a[i]:
                cur, ext = 1, c[i]
        st[i] = cur
    return _flip(st), {}


def _renko_trend(df, p):
    """ATR 'bricks': the trend flips after the close moves k bricks against the last brick (box = ATR at start)."""
    h, l, c = _hlc(df); a = ta.atr(h, l, c, 14); rev = int(p["bricks"])
    st = np.zeros(len(c)); cur, last, box = 0, np.nan, np.nan
    for i in range(len(c)):
        if np.isnan(a[i]):
            continue
        if np.isnan(last):
            last, box = c[i], a[i] * float(p["box_atr"])
            continue
        if cur >= 0 and c[i] >= last + box:
            n = int((c[i] - last) // box); last += n * box; cur = 1
        elif cur <= 0 and c[i] <= last - box:
            n = int((last - c[i]) // box); last -= n * box; cur = -1
        elif cur > 0 and c[i] <= last - rev * box:
            n = int((last - c[i]) // box); last -= n * box; cur = -1
        elif cur < 0 and c[i] >= last + rev * box:
            n = int((c[i] - last) // box); last += n * box; cur = 1
        box = a[i] * float(p["box_atr"])
        st[i] = cur
    return _flip(st), {}


def _ssl_channel(df, p):
    h, l, c = _hlc(df); n = int(p["n"])
    hi, lo = ta.sma(h, n), ta.sma(l, n)
    st = np.where(c > hi, 1, np.where(c < lo, -1, 0))
    return _flip(st), {"SMA high": hi, "SMA low": lo}


# ---------------------------------------------------------------- 3. momentum (#87-#104)
def _roc_cross(df, p):
    c = _c(df); r = ta.ema(_roc(c, int(p["n"])), 5)
    return _flip(_side(r)), {}


def _roc_ma(df, p):
    c = _c(df); r = _roc(c, int(p["n"])); m = ta.sma(r, int(p["n"]) // 2 + 1)
    return _cross(r, m) * (np.sign(np.nan_to_num(r)) == np.sign(_cross(r, m))), {}


def _tsi_cross(df, p):
    c = _c(df); t = _tsi(c, int(p["long"]), 13); s = ta.ema(t, 7)
    x = _cross(t, s)
    return _sig((x > 0) & (t > 0), (x < 0) & (t < 0)), {}


def _kst(df, p):
    c = _c(df); m = int(p["scale"])
    k = (ta.sma(_roc(c, 10 * m), 10 * m) * 1 + ta.sma(_roc(c, 15 * m), 10 * m) * 2
         + ta.sma(_roc(c, 20 * m), 10 * m) * 3 + ta.sma(_roc(c, 30 * m), 15 * m) * 4)
    s = ta.sma(k, 9 * m)
    return _cross(k, s), {}


def _ppo_flip(df, p):
    c = _c(df); f = int(p["fast"])
    with np.errstate(divide="ignore", invalid="ignore"):
        ppo = 100 * (ta.ema(c, f) - ta.ema(c, f * 2)) / ta.ema(c, f * 2)
    hist = ppo - ta.ema(ppo, 9)
    return _flip(_side(hist)) * (np.sign(np.nan_to_num(ppo)) == np.sign(_flip(_side(hist)))), {}


def _cmo_trend(df, p):
    c = _c(df); m = _cmo(c, int(p["n"])); lv = float(p["level"])
    return _sig((m > lv) & (ta.shift(m) <= lv), (m < -lv) & (ta.shift(m) >= -lv)), {}


def _rsi50(df, p):
    c = _c(df); r = ta.rsi(c, int(p["n"])); e = ta.ema(c, 100)
    x = _cross(r, np.full(len(c), 50.0))
    return _sig((x > 0) & (c > e), (x < 0) & (c < e)), {"EMA100": e}


def _stoch_trend(df, p):
    h, l, c = _hlc(df); k = ta.sma(_stoch(h, l, c, int(p["n"])), 3); e = ta.ema(c, 100)
    lv = float(p["level"])
    return _sig((k > lv) & (ta.shift(k) <= lv) & (c > e), (k < 100 - lv) & (ta.shift(k) >= 100 - lv) & (c < e)), {"EMA100": e}


def _willr_trend(df, p):
    h, l, c = _hlc(df); w = _stoch(h, l, c, int(p["n"])) - 100        # Williams %R in [-100, 0]
    return _sig((w > -20) & (ta.shift(w) <= -20), (w < -80) & (ta.shift(w) >= -80)), {}


def _coppock(df, p):
    c = _c(df); m = _bpm(df) // int(p["div"]) + 1
    cp = ta.wma(_roc(c, 14 * m) + _roc(c, 11 * m), 10 * m)
    return _flip(_side(cp)), {}


def _stc(df, p):
    """Schaff Trend Cycle: double stochastic of MACD; long above 75-crossing, short below 25-crossing."""
    c = _c(df); f = int(p["fast"])
    macd = ta.ema(c, f) - ta.ema(c, f * 2)
    n = 10
    def st(x):
        hh, ll = ta.highest(x, n), ta.lowest(x, n)
        with np.errstate(divide="ignore", invalid="ignore"):
            return ta.ema(100 * (x - ll) / (hh - ll), 3)
    s = st(st(macd))
    return _sig((s > 75) & (ta.shift(s) <= 75), (s < 25) & (ta.shift(s) >= 25)), {}


def _fisher(df, p):
    h, l, c = _hlc(df); n = int(p["n"])
    mid = (h + l) / 2
    hh, ll = ta.highest(mid, n), ta.lowest(mid, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        x = np.clip(2 * ((mid - ll) / (hh - ll) - 0.5), -0.999, 0.999)
    x = ta.ema(np.nan_to_num(x), 5)
    x = np.clip(x, -0.999, 0.999)
    f = 0.5 * np.log((1 + x) / (1 - x))
    f[: n] = np.nan
    return _flip(_side(f - ta.shift(f))) * (np.sign(np.nan_to_num(f)) == np.sign(_flip(_side(f - ta.shift(f))))), {}


def _tsmom_vol(df, p):
    """Volatility-scaled time-series momentum: n-bar return / (sigma x sqrt(n)) beyond a threshold."""
    c = _c(df); n = int(p["months"]) * _bpm(df)
    r = np.log(c / ta.shift(c, n))
    sd = ta.stdev(np.diff(np.log(c), prepend=np.nan), min(n, 500)) * np.sqrt(n)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = r / sd
    return _flip(_side(z, float(p["z"]))), {}


def _dual_momentum(df, p):
    """Short and long look-back returns must agree (Antonacci-style absolute momentum on two horizons)."""
    c = _c(df); m = _bpm(df)
    a, b = _roc(c, int(p["short_m"]) * m // 4 + 1), _roc(c, int(p["short_m"]) * m)
    st = np.where((a > 0) & (b > 0), 1, np.where((a < 0) & (b < 0), -1, 0))
    return _flip(st), {}


def _macd_zero(df, p):
    c = _c(df); f = int(p["fast"]); m = ta.ema(c, f) - ta.ema(c, int(f * 2.2))
    return _flip(_side(m)), {}


def _macd_filtered(df, p):
    c = _c(df); f = int(p["fast"]); m = ta.ema(c, f) - ta.ema(c, int(f * 2.2)); s = ta.ema(m, 9)
    x = _cross(m, s)
    return _sig((x > 0) & (m > 0), (x < 0) & (m < 0)), {}


def _aroon_osc(df, p):
    h, l, _ = _hlc(df); n = int(p["n"])
    up = 100 * (n - ta.bars_since_extreme(h, n, True)) / n
    dn = 100 * (n - ta.bars_since_extreme(l, n, False)) / n
    o = up - dn; lv = float(p["level"])
    return _sig((o > lv) & (ta.shift(o) <= lv), (o < -lv) & (ta.shift(o) >= -lv)), {}


def _rsi_breakout(df, p):
    """Momentum, not reversion: RSI pushing above 70 (below 30) marks a strong new move."""
    c = _c(df); r = ta.rsi(c, int(p["n"])); lv = float(p["level"])
    return _sig((r > lv) & (ta.shift(r) <= lv), (r < 100 - lv) & (ta.shift(r) >= 100 - lv)), {}


def _rvi(df, p):
    """Relative Vigor Index: closes near the highs in up-trends; RVI vs its signal, in RVI's sign."""
    o = df.open.to_numpy(float); h, l, c = _hlc(df); n = int(p["n"])
    num = ta.sma(c - o, n); den = ta.sma(h - l, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = num / den
    s = ta.sma(r, 4)
    x = _cross(r, s)
    return _sig((x > 0) & (r > 0), (x < 0) & (r < 0)), {}


def _pfe(df, p):
    """Polarized Fractal Efficiency: straight-line distance / path length, signed and smoothed."""
    c = _c(df); n = int(p["n"])
    straight = np.sqrt((c - ta.shift(c, n)) ** 2 + n ** 2)
    step = np.sqrt(np.diff(c, prepend=np.nan) ** 2 + 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        pf = 100 * np.sign(c - ta.shift(c, n)) * straight / ta.rolling_sum(step, n)
    s = ta.ema(pf, 5)
    lv = float(p["level"])
    return _sig((s > lv) & (ta.shift(s) <= lv), (s < -lv) & (ta.shift(s) >= -lv)), {}


def _apo(df, p):
    c = _c(df); f = int(p["fast"])
    a = ta.sma(c, f) - ta.sma(c, f * 3)
    return _flip(_side(a)), {}


def _momentum_accel(df, p):
    """Momentum that is both positive and rising (second derivative) — enters accelerating trends."""
    c = _c(df); n = int(p["n"]); m = _roc(c, n); acc = m - ta.shift(m, max(1, n // 2))
    st = np.where((m > 0) & (acc > 0), 1, np.where((m < 0) & (acc < 0), -1, 0))
    return _sig((st > 0) & (ta.shift(st) <= 0), (st < 0) & (ta.shift(st) >= 0)), {}


# ---------------------------------------------------------------- 4. trend strength filters (#105-#112)
def _adx_rising(df, p):
    h, l, c = _hlc(df); pdi, mdi, adx = ta.dmi(h, l, c, 14); lv = float(p["level"])
    start = (adx > lv) & (ta.shift(adx) <= lv)
    return _sig(start & (pdi > mdi), start & (mdi > pdi)), {}


def _chop_break(df, p):
    h, l, c = _hlc(df); ch = _chop(h, l, c, int(p["n"])); e = ta.ema(c, 50); lv = float(p["level"])
    start = (ch < lv) & (ta.shift(ch) >= lv)
    return _sig(start & (c > e), start & (c < e)), {"EMA50": e}


def _efficiency(df, p):
    c = _c(df); er = _er(c, int(p["n"])); lv = float(p["level"])
    return _sig((er > lv) & (ta.shift(er) <= lv), (er < -lv) & (ta.shift(er) >= -lv)), {}


def _rsq_trend(df, p):
    c = _c(df); n = int(p["n"]); r2 = _rsq(c, n); sl = ta.linreg_slope(c, n); lv = float(p["level"])
    start = (r2 > lv) & (ta.shift(r2) <= lv)
    return _sig(start & (sl > 0), start & (sl < 0)), {}


def _slope_t(df, p):
    """Regression slope significance: |t| of the slope > threshold."""
    c = _c(df); n = int(p["n"])
    r2 = np.clip(_rsq(c, n), 0, 0.9999)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.sign(ta.linreg_slope(c, n)) * np.sqrt(r2 * (n - 2) / (1 - r2))
    lv = float(p["t"])
    return _sig((t > lv) & (ta.shift(t) <= lv), (t < -lv) & (ta.shift(t) >= -lv)), {}


def _vhf(df, p):
    h, l, c = _hlc(df); n = int(p["n"])
    with np.errstate(divide="ignore", invalid="ignore"):
        v = (ta.highest(c, n) - ta.lowest(c, n)) / ta.rolling_sum(np.abs(np.diff(c, prepend=np.nan)), n)
    e = ta.ema(c, n)
    start = (v > float(p["level"])) & (ta.shift(v) <= float(p["level"]))
    return _sig(start & (c > e), start & (c < e)), {"EMA": e}


def _tii(df, p):
    """Trend Intensity Index: share of the last n/2 closes above (below) the n-bar SMA."""
    c = _c(df); n = int(p["n"]); m = ta.sma(c, n); k = max(2, n // 2)
    dev = c - m
    pos = ta.rolling_sum(np.where(dev > 0, dev, 0.0), k); neg = ta.rolling_sum(np.where(dev < 0, -dev, 0.0), k)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = 100 * pos / (pos + neg)
    return _sig((t > 80) & (ta.shift(t) <= 80), (t < 20) & (ta.shift(t) >= 20)), {"SMA": m}


def _er_kama_break(df, p):
    """Efficient trend: price above KAMA AND efficiency ratio above the level."""
    c = _c(df); k = ta.kama(c, int(p["n"])); er = np.abs(_er(c, int(p["n"])))
    st = np.where((c > k) & (er > float(p["level"])), 1, np.where((c < k) & (er > float(p["level"])), -1, 0))
    return _sig((st > 0) & (ta.shift(st) <= 0), (st < 0) & (ta.shift(st) >= 0)), {"KAMA": k}


# ---------------------------------------------------------------- 5. volatility regimes (#113-#118)
def _atr_expansion(df, p):
    h, l, c = _hlc(df); fast, slow = ta.atr(h, l, c, 5), ta.atr(h, l, c, 50); e = ta.ema(c, 50)
    start = (fast > float(p["k"]) * slow) & (ta.shift(fast) <= float(p["k"]) * ta.shift(slow))
    up = c > ta.shift(c, 3)
    return _sig(start & up & (c > e), start & ~up & (c < e)), {"EMA50": e}


def _squeeze_mom(df, p):
    """LazyBear Squeeze Momentum: after a BB-inside-Keltner squeeze, trade the momentum histogram's sign."""
    h, l, c = _hlc(df); n = int(p["n"])
    _, bu, bl = ta.bollinger(c, n, 2.0); _, ku, kl = ta.keltner(h, l, c, n, 1.5)
    sq = (bu < ku) & (bl > kl)
    mid = ((ta.highest(h, n) + ta.lowest(l, n)) / 2 + ta.sma(c, n)) / 2
    mom = ta.linreg_slope(c - mid, n)
    was = pd.Series(sq).rolling(6, min_periods=1).max().to_numpy().astype(bool) & ~sq
    return _sig(was & (mom > 0) & (ta.shift(mom) <= mom), was & (mom < 0) & (ta.shift(mom) >= mom)) * (np.r_[0, np.diff(was.astype(int))] > 0), {}


def _bbw_breakout(df, p):
    """Band-width at a 6-month low (quiet market), then a close outside the bands starts the trend."""
    c = _c(df); n = 20; mid, up, dn = ta.bollinger(c, n, 2.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        w = (up - dn) / mid
    look = int(p["months"]) * _bpm(df)
    low = w <= pd.Series(w).rolling(look, min_periods=look // 2).quantile(float(p["q"])).to_numpy()
    recent = pd.Series(low).rolling(10, min_periods=1).max().to_numpy().astype(bool)
    return _sig(recent & (c > up) & (ta.shift(c) <= ta.shift(up)), recent & (c < dn) & (ta.shift(c) >= ta.shift(dn))), {"BB up": up, "BB low": dn}


def _range_expansion(df, p):
    """A bar whose range is k x the average range, closing in its top (bottom) quarter, in the EMA trend."""
    o = df.open.to_numpy(float); h, l, c = _hlc(df); r = h - l; avg = ta.shift(ta.sma(r, 20)); e = ta.ema(c, 100)
    big = r > float(p["k"]) * avg
    with np.errstate(divide="ignore", invalid="ignore"):
        pos = (c - l) / r
    return _sig(big & (pos > 0.75) & (c > o) & (c > e), big & (pos < 0.25) & (c < o) & (c < e)), {"EMA100": e}


def _atr_trailing(df, p):
    """ATR trailing stop line (like a SuperTrend on closes): flip when the close crosses the trailing line."""
    h, l, c = _hlc(df); a = ta.atr(h, l, c, 14); k = float(p["k"])
    st = np.zeros(len(c)); cur, line = 0, np.nan
    for i in range(len(c)):
        if np.isnan(a[i]):
            continue
        if cur == 0:
            cur, line = 1, c[i] - k * a[i]
        elif cur > 0:
            line = max(line, c[i] - k * a[i])
            if c[i] < line:
                cur, line = -1, c[i] + k * a[i]
        else:
            line = min(line, c[i] + k * a[i])
            if c[i] > line:
                cur, line = 1, c[i] - k * a[i]
        st[i] = cur
    return _flip(st), {}


def _hv_breakout(df, p):
    """Historical-volatility regime: realised vol rising out of its low range + n-bar breakout in that direction."""
    h, l, c = _hlc(df); r = np.diff(np.log(c), prepend=np.nan)
    hv, base = ta.stdev(r, 20), ta.stdev(r, 120)
    rising = hv > float(p["k"]) * base
    n = int(p["n"]); hi, lo = ta.shift(ta.highest(h, n)), ta.shift(ta.lowest(l, n))
    return _sig(rising & (c > hi) & (ta.shift(c) <= ta.shift(hi)), rising & (c < lo) & (ta.shift(c) >= ta.shift(lo))), {}


# ---------------------------------------------------------------- 6. price structure (#119-#126)
def _higher_highs(df, p):
    h, l, c = _hlc(df); n = int(p["n"])
    hh = np.ones(len(c), bool); ll = np.ones(len(c), bool)
    sh = lambda x, k: x if k == 0 else ta.shift(x, k)      # noqa: E731
    for k in range(n):
        hh &= (sh(h, k) > sh(h, k + 1)) & (sh(l, k) > sh(l, k + 1))
        ll &= (sh(h, k) < sh(h, k + 1)) & (sh(l, k) < sh(l, k + 1))
    e = ta.ema(c, 50)
    return _sig(hh & ~ta.shift(hh.astype(float)).astype(bool) & (c > e), ll & ~ta.shift(ll.astype(float)).astype(bool) & (c < e)), {"EMA50": e}


def _swing_break(df, p):
    """Break of the last confirmed swing high / low (pivot with k bars each side) in the EMA trend."""
    h, l, c = _hlc(df); k = int(p["k"])
    ph = np.full(len(c), np.nan); pl = np.full(len(c), np.nan)
    last_h = last_l = np.nan
    for i in range(2 * k, len(c)):
        j = i - k                                           # pivot candidate confirmed k bars later
        if h[j] == h[j - k: i + 1].max():
            last_h = h[j]
        if l[j] == l[j - k: i + 1].min():
            last_l = l[j]
        ph[i], pl[i] = last_h, last_l
    e = ta.ema(c, 100)
    ph1, pl1 = ta.shift(ph), ta.shift(pl)
    return _sig((c > ph1) & (ta.shift(c) <= ph1) & (c > e), (c < pl1) & (ta.shift(c) >= pl1) & (c < e)), {"Swing high": ph1, "Swing low": pl1}


def _ha_smoothed(df, p):
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    n = int(p["n"])
    fill = lambda e, raw: np.where(np.isnan(e), raw, e)   # noqa: E731  (warm-up bars: raw prices, else HA is all NaN)
    so, sh, sl, sc = fill(ta.ema(o, n), o), fill(ta.ema(h, n), h), fill(ta.ema(l, n), l), fill(ta.ema(c, n), c)
    hao, _, _, hac = ta.heikin_ashi(so, sh, sl, sc)
    return _flip(_side(ta.ema(hac - hao, n))), {}


def _pnf(df, p):
    """Point & Figure: box = ATR x box_atr, a reversal needs 3 boxes; trade the column direction."""
    h, l, c = _hlc(df); a = ta.atr(h, l, c, 50)
    st = np.zeros(len(c)); cur, ext = 0, np.nan
    for i in range(len(c)):
        if np.isnan(a[i]):
            continue
        box = a[i] * float(p["box_atr"])
        if cur == 0:
            cur, ext = 1, c[i]
        elif cur > 0:
            ext = max(ext, c[i])
            if c[i] <= ext - 3 * box:
                cur, ext = -1, c[i]
        else:
            ext = min(ext, c[i])
            if c[i] >= ext + 3 * box:
                cur, ext = 1, c[i]
        st[i] = cur
    return _flip(st), {}


def _inside_trend_break(df, p):
    """After n bars inside the previous range (consolidation) a break in the direction of EMA50 > EMA200."""
    h, l, c = _hlc(df); n = int(p["n"])
    hi, lo = ta.shift(ta.highest(h, n)), ta.shift(ta.lowest(l, n))
    a = ta.atr(h, l, c, 14)
    tight = (hi - lo) < float(p["k"]) * a
    e50, e200 = ta.ema(c, 50), ta.ema(c, 200)
    return _sig(tight & (c > hi) & (e50 > e200), tight & (c < lo) & (e50 < e200)), {"EMA50": e50, "EMA200": e200}


def _gap_continuation(df, p):
    """A bar that opens beyond the previous high (low) and closes even further — strong demand in the trend."""
    o = df.open.to_numpy(float); h, l, c = _hlc(df); a = ta.atr(h, l, c, 14); e = ta.ema(c, int(p["n"]))
    k = float(p["k"])
    return _sig((o > ta.shift(h)) & (c > o + k * a) & (c > e),
                (o < ta.shift(l)) & (c < o - k * a) & (c < e)), {"EMA": e}


def _two_bar_momentum(df, p):
    """Two consecutive strong closes (each > k x ATR) in the direction of the EMA trend."""
    h, l, c = _hlc(df); a = ta.shift(ta.atr(h, l, c, 14)); d = c - ta.shift(c); k = float(p["k"]); e = ta.ema(c, int(p["n"]))
    up = (d > k * a) & (ta.shift(d) > k * ta.shift(a)); dn = (d < -k * a) & (ta.shift(d) < -k * ta.shift(a))
    return _sig(up & (c > e), dn & (c < e)), {"EMA": e}


def _weekly_momentum(df, p):
    """Four-week momentum sign (Moskowitz et al.: short look-backs carry trend information too)."""
    c = _c(df); n = max(2, int(p["weeks"]) * _bpm(df) // 4)
    return _flip(_side(_roc(c, n))), {}


# ---------------------------------------------------------------- catalogue
def S(name, fam, rationale, fn, params, grid):
    return {"name": name, "family": fam, "rationale": rationale, "fn": fn, "params": params, "grid": grid}


def P(key, label, typ, default, lo, hi, step):
    return {"key": key, "label": label, "type": typ, "default": default, "min": lo, "max": hi, "step": step}


X = {"sl_atr": [2.0, 3.0], "rr": [2.5]}          # shared exit grid (rr 0 = no TP is appended by strategies.py)
R_TREND = "แนวโน้มมีอยู่จริงเพราะข่าวกระจายช้า คนตามกันและปรับตัวช้า (Moskowitz, Ooi & Pedersen 2012; Hurst et al. 2017)"

TREND_STRATEGIES: dict[str, dict] = {
    # moving averages
    "sma_cross": S("SMA Crossover", TF, "เส้นค่าเฉลี่ยธรรมดาเร็ว/ช้าตัดกัน — ระบบตามเทรนด์ยุคแรก (Donchian 1960s) " + R_TREND, _sma_cross,
                   [P("fast", "SMA เร็ว", "int", 20, 5, 100, 1), P("mult", "ช้า = เร็ว × mult", "int", 4, 2, 8, 1)],
                   {"fast": [10, 30], "mult": [3, 5], **X}),
    "dema_cross": S("DEMA Crossover", TF, "Double EMA (Mulloy 1994) ลดความหน่วงของเส้นค่าเฉลี่ย จึงจับเทรนด์ได้เร็วขึ้น", _dema_cross,
                    [P("fast", "DEMA เร็ว (ช้า = 3×)", "int", 20, 5, 100, 1)], {"fast": [10, 30], **X}),
    "price_ema": S("Price vs EMA", TF, "อยู่ฝั่งเดียวกับเส้น EMA เดียว — กฎตามเทรนด์ที่ง่ายที่สุด เปลี่ยนฝั่งเมื่อราคาปิดข้ามเส้น", _price_ema,
                   [P("n", "EMA", "int", 100, 20, 400, 1)], {"n": [50, 200], **X}),
    "golden_cross": S("Golden / Death Cross", TF, "SMA50 ตัด SMA200 (ปรับช่วงได้) — สัญญาณเทรนด์ใหญ่ที่นักลงทุนสถาบันจับตา", _golden_cross,
                      [P("slow", "SMA ช้า (เร็ว = ¼)", "int", 200, 80, 400, 1)], {"slow": [120, 200], **X}),
    "ema_ribbon": S("EMA Ribbon (n / 2n / 4n)", TF, "EMA 3 เส้นเรียงตัวครบทิศเดียวกัน = เทรนด์ทุกระดับเวลาเห็นตรงกัน", _ribbon,
                    [P("n", "EMA สั้นสุด", "int", 20, 5, 60, 1)], {"n": [10, 25], **X}),
    "ema_slope": S("EMA Slope", TF, "ทิศของความชันเส้น EMA — เปลี่ยนเมื่อเส้นหักหัว (ช้าแต่กรองแกว่งดี)", _ema_slope,
                   [P("n", "EMA", "int", 100, 20, 300, 1), P("k", "ความชันเทียบ k แท่ง", "int", 5, 1, 20, 1)],
                   {"n": [50, 150], "k": [3, 10], **X}),
    "ma_envelope": S("MA Envelope Breakout", TF, "ราคาหลุดซองรอบ SMA ± % — ต้องวิ่งไกลพอจึงนับเป็นเทรนด์ (กรองการแกว่งรอบเส้น)", _ma_envelope,
                     [P("n", "SMA", "int", 50, 10, 200, 1), P("pct", "ซอง (%)", "float", 1.0, 0.1, 5, 0.1)],
                     {"n": [50, 100], "pct": [0.5, 1.5], **X}),
    "mcginley": S("McGinley Dynamic", TF, "เส้นค่าเฉลี่ยที่ปรับความเร็วตามตลาด (McGinley 1990) ไม่ทิ้งห่างราคาเวลาตลาดวิ่งแรง", _mcginley_trend,
                  [P("n", "N", "int", 30, 5, 200, 1)], {"n": [20, 60], **X}),
    "t3_trend": S("Tillson T3 Slope", TF, "T3 (Tillson 1998) เรียบกว่า EMA แต่หน่วงน้อย — ตามทิศความชันของเส้น", _t3_trend,
                  [P("n", "T3 N", "int", 10, 3, 50, 1)], {"n": [8, 20], **X}),
    "zlema_cross": S("Zero-Lag EMA Crossover", TF, "ZLEMA (Ehlers & Way 2010) ชดเชยความหน่วง — ตัดกันเร็วกว่า EMA ปกติ", _zlema_cross,
                     [P("fast", "ZLEMA เร็ว (ช้า = 3×)", "int", 15, 5, 80, 1)], {"fast": [10, 25], **X}),
    "alma_trend": S("ALMA Slope", TF, "Arnaud Legoux MA กรองสัญญาณรบกวนด้วยน้ำหนัก Gaussian — ตามทิศความชัน", _alma_trend,
                    [P("n", "ALMA N", "int", 30, 9, 100, 1)], {"n": [21, 50], **X}),
    "hma_cross": S("Hull MA Crossover", TF, "Hull MA เร็วตัด Hull MA ช้า (3×) — HMA หน่วงน้อยแต่เรียบ", _hma_cross,
                   [P("fast", "HMA เร็ว", "int", 20, 5, 80, 1)], {"fast": [15, 30], **X}),
    "mtf_ema": S("Multi-Timeframe EMA", TF, "เทรนด์ใหญ่ (EMA หลายวัน) เป็นตัวกรอง + EMA10/30 ตัดกันในทิศเดียวกัน — ไม่สวนเทรนด์ใหญ่", _mtf_ema,
                 [P("days", "EMA ใหญ่ (วัน)", "int", 20, 5, 100, 1)], {"days": [10, 30], **X}),
    "ma_stack_pullback": S("EMA Stack Continuation", TF, "EMA50 > EMA200 (เทรนด์) แล้วราคากลับขึ้นเหนือ EMA เร็วหลังย่อ — เข้าต่อเทรนด์", _ma_stack_pullback,
                           [P("fast", "EMA เร็ว", "int", 20, 5, 50, 1)], {"fast": [10, 20], **X}),
    # channels / breakouts
    "donchian_mid": S("Donchian Midline", TF, "อยู่ฝั่งเดียวกับกึ่งกลางกรอบ N แท่ง — แบบเต่าแต่กลับทิศเร็วกว่า", _donchian_mid,
                      [P("n", "กรอบ N", "int", 50, 10, 300, 1)], {"n": [40, 100], **X}),
    "keltner_trend": S("Keltner Channel Trend", TF, "ราคาปิดนอกช่อง Keltner (Keltner 1960) = เริ่มเทรนด์ อยู่ฝั่งนั้นจนออกอีกฝั่ง", _keltner_trend,
                       [P("n", "N", "int", 20, 10, 100, 1), P("mult", "× ATR", "float", 2.0, 1, 4, 0.1)],
                       {"n": [20, 50], "mult": [1.5, 2.5], **X}),
    "atr_bands": S("ATR Bands Trend", TF, "SMA ± k×ATR เป็นเส้นแบ่ง — ทะลุแถบด้วยแรงที่เกินความผันผวนปกติ", _atr_bands,
                   [P("n", "N", "int", 50, 10, 200, 1), P("k", "k × ATR", "float", 2.0, 0.5, 5, 0.1)],
                   {"n": [30, 80], "k": [1.5, 3.0], **X}),
    "bb_trend": S("Bollinger Band Trend", TF, "ใช้ Bollinger แบบตามเทรนด์ (ไม่ใช่สวน): ปิดนอกแบนด์ = เทรนด์ใหม่ — แบบที่ Bollinger เองแนะนำในตลาดเทรนด์", _bb_trend,
                  [P("n", "N", "int", 20, 10, 100, 1), P("k", "k (SD)", "float", 2.0, 1, 3.5, 0.1)],
                  {"n": [20, 50], "k": [1.5, 2.5], **X}),
    "chandelier": S("Chandelier Trend", TF, "Chandelier Exit (LeBeau): เส้นหยุดห่างจุดสูงสุด k×ATR — หลุดเส้น = เทรนด์กลับ", _chandelier,
                    [P("n", "N", "int", 22, 10, 100, 1), P("k", "k × ATR", "float", 3.0, 1, 6, 0.1)],
                    {"n": [22, 50], "k": [2.5, 4.0], **X}),
    "close_breakout": S("Closing-Price Breakout", TF, "ใช้ราคาปิด (ไม่ใช่ไส้เทียน) ทำ High/Low ใหม่ N แท่ง — กรองไส้หลอก", _close_breakout,
                        [P("n", "N", "int", 50, 10, 300, 1)], {"n": [30, 80], **X}),
    "linreg_channel": S("Regression Channel Breakout", TF, "ราคาหลุดช่องเส้นถดถอย ± k SD = เทรนด์เปลี่ยนความชันจริง", _linreg_channel,
                        [P("n", "N", "int", 50, 10, 200, 1), P("k", "k (SD)", "float", 2.0, 1, 3.5, 0.1)],
                        {"n": [50, 100], "k": [1.5, 2.5], **X}),
    "tsf_cross": S("Time Series Forecast Cross", TF, "ราคาข้ามค่าพยากรณ์เส้นถดถอย 1 แท่งข้างหน้า — ตามความชันเชิงสถิติ", _tsf_cross,
                   [P("n", "N", "int", 30, 10, 150, 1)], {"n": [25, 60], **X}),
    "median_channel": S("Median Channel Trend", TF, "Median ทนต่อไส้เทียนสุดโต่งกว่าค่าเฉลี่ย — ทะลุ median ± k×ATR", _median_channel,
                        [P("n", "N", "int", 50, 10, 200, 1), P("k", "k × ATR", "float", 1.0, 0.2, 3, 0.1)],
                        {"n": [50, 100], "k": [0.5, 1.5], **X}),
    "ichimoku_cloud": S("Ichimoku Cloud Breakout", TF, "ราคาปิดเหนือ/ใต้เมฆ Ichimoku (Hosoda) = เทรนด์ — ต่างจาก #14 ที่ใช้ TK cross", _ichimoku_cloud,
                        [P("base", "Kijun (เส้นฐาน)", "int", 26, 9, 60, 1)], {"base": [26, 52], **X}),
    "kijun_cross": S("Kijun-sen Cross", TF, "ราคาข้ามเส้นกึ่งกลางกรอบ N แท่ง (Kijun) — เส้นสมดุลราคา", _kijun_cross,
                     [P("n", "N", "int", 26, 9, 100, 1)], {"n": [26, 60], **X}),
    "gann_hilo": S("Gann HiLo Activator", TF, "ราคาปิดเหนือค่าเฉลี่ย High (ใต้ค่าเฉลี่ย Low) — Robert Krausz", _gann_hilo,
                   [P("n", "N", "int", 10, 3, 60, 1)], {"n": [10, 30], **X}),
    "donchian_width": S("Wide-Channel Breakout", TF, "ทะลุกรอบ N แท่ง เฉพาะเมื่อกรอบกว้างกว่า k×ATR — ตัดการทะลุกรอบแคบที่มักหลอก", _donchian_width,
                        [P("n", "N", "int", 40, 10, 200, 1), P("k", "กว้าง > k × ATR", "float", 4.0, 1, 12, 0.5)],
                        {"n": [20, 55], "k": [3.0, 6.0], **X}),
    "dual_thrust": S("Dual Thrust", VB, "Dual Thrust (Michael Chalek) ระบบ CTA ยอดนิยม: ทะลุราคาเปิด ± k × ช่วงราคาย้อนหลัง ในทิศ EMA100", _dual_thrust,
                     [P("n", "ช่วงย้อนหลัง N", "int", 4, 1, 20, 1), P("k", "k", "float", 0.5, 0.2, 1.5, 0.05)],
                     {"n": [4, 10], "k": [0.5, 0.8], **X}),
    "volatility_stop": S("Wilder Volatility Stop", TF, "Wilder (1978) Volatility System: หยุดและกลับทิศเมื่อราคาปิดถอยจากจุดดีที่สุด k×ATR", _volatility_stop,
                         [P("n", "ATR N", "int", 14, 5, 50, 1), P("k", "k × ATR", "float", 3.0, 1, 6, 0.1)],
                         {"n": [14, 30], "k": [2.5, 4.0], **X}),
    "renko_trend": S("ATR Renko Trend", TF, "ก้อนอิฐ Renko ขนาด ATR — นับเฉพาะการเคลื่อนที่ที่ใหญ่พอ ตัดเสียงรบกวนเวลาออก", _renko_trend,
                     [P("box_atr", "ขนาดอิฐ (× ATR)", "float", 1.0, 0.3, 3, 0.1), P("bricks", "กลับทิศเมื่อสวน (อิฐ)", "int", 2, 1, 4, 1)],
                     {"box_atr": [1.0, 2.0], "bricks": [2, 3], **X}),
    "ssl_channel": S("SSL Channel", TF, "ปิดเหนือ SMA ของ High = ขาขึ้น ปิดใต้ SMA ของ Low = ขาลง", _ssl_channel,
                     [P("n", "N", "int", 10, 3, 60, 1)], {"n": [10, 30], **X}),
    # momentum
    "roc_trend": S("Rate-of-Change Trend", MOM, "ผลตอบแทนย้อนหลัง N แท่ง (เกลี่ย 5 แท่ง) บวก/ลบ — time-series momentum ตรงๆ " + R_TREND, _roc_cross,
                   [P("n", "N", "int", 100, 10, 500, 1)], {"n": [60, 200], **X}),
    "roc_ma": S("ROC vs its Average", MOM, "โมเมนตัมตัดค่าเฉลี่ยของตัวเองในฝั่งเดียวกับโมเมนตัม — โมเมนตัมเร่งตัว", _roc_ma,
                [P("n", "N", "int", 20, 5, 200, 1)], {"n": [20, 50], **X}),
    "tsi_cross": S("True Strength Index", MOM, "TSI (Blau 1991) โมเมนตัมที่เกลี่ยสองชั้น ตัดเส้นสัญญาณในฝั่งเดียวกับศูนย์", _tsi_cross,
                   [P("long", "TSI ยาว", "int", 25, 10, 60, 1)], {"long": [25, 40], **X}),
    "kst": S("Know Sure Thing", MOM, "KST (Pring) รวมโมเมนตัม 4 ช่วงเวลา — เทรนด์ต้องเห็นตรงกันหลายระดับ", _kst,
             [P("scale", "สเกลเวลา", "int", 1, 1, 4, 1)], {"scale": [1, 2], **X}),
    "ppo_flip": S("PPO Histogram Trend", MOM, "Percentage Price Oscillator: histogram เปลี่ยนสีในฝั่งเดียวกับ PPO", _ppo_flip,
                  [P("fast", "EMA เร็ว (ช้า = 2×)", "int", 12, 5, 50, 1)], {"fast": [12, 26], **X}),
    "cmo_trend": S("Chande Momentum Trend", MOM, "CMO (Chande 1994) ทะลุระดับสูง = โมเมนตัมแรงพอเป็นเทรนด์", _cmo_trend,
                   [P("n", "N", "int", 14, 5, 50, 1), P("level", "ระดับ", "float", 50, 20, 80, 5)],
                   {"n": [14, 30], "level": [40, 60], **X}),
    "rsi50_trend": S("RSI 50-Line Trend", MOM, "RSI ข้าม 50 ในทิศ EMA100 — โมเมนตัมเปลี่ยนข้างในเทรนด์ใหญ่", _rsi50,
                     [P("n", "RSI", "int", 14, 5, 50, 1)], {"n": [14, 28], **X}),
    "stoch_trend": S("Stochastic Momentum Trend", MOM, "Stochastic ขึ้นเหนือ 80 (ลงใต้ 20) ในทิศ EMA100 = แรงต่อเนื่อง ไม่ใช่สวน", _stoch_trend,
                     [P("n", "N", "int", 14, 5, 50, 1), P("level", "ระดับ", "float", 80, 60, 95, 5)],
                     {"n": [14, 30], "level": [70, 85], **X}),
    "willr_trend": S("Williams %R Breakout", MOM, "%R ขึ้นเหนือ −20 (ลงใต้ −80) — ปิดใกล้ขอบกรอบ N แท่งต่อเนื่อง", _willr_trend,
                     [P("n", "N", "int", 50, 10, 200, 1)], {"n": [30, 80], **X}),
    "coppock": S("Coppock Curve", MOM, "Coppock (1962) โมเมนตัมระยะยาวสำหรับหาเทรนด์ใหญ่ — เปลี่ยนเครื่องหมาย", _coppock,
                 [P("div", "ความเร็ว (หารเดือน)", "int", 4, 1, 8, 1)], {"div": [2, 4], **X}),
    "schaff_trend": S("Schaff Trend Cycle", MOM, "STC (Schaff 2008) = Stochastic ของ MACD สองชั้น ตอบสนองเร็วกว่า MACD", _stc,
                      [P("fast", "MACD เร็ว", "int", 23, 8, 40, 1)], {"fast": [12, 23], **X}),
    "fisher_trend": S("Fisher Transform Trend", MOM, "Fisher Transform (Ehlers 2002) แปลงราคาให้จุดกลับตัวชัด ตามทิศในฝั่งเดียวกับศูนย์", _fisher,
                      [P("n", "N", "int", 10, 5, 50, 1)], {"n": [10, 30], **X}),
    "tsmom_vol": S("Vol-Scaled Momentum", MOM, "ผลตอบแทน N เดือน หารด้วยความผันผวน (z-score) ต้องเกินเกณฑ์ — แบบกองทุน CTA (Moskowitz et al. 2012)", _tsmom_vol,
                   [P("months", "ย้อนหลัง (เดือน)", "int", 3, 1, 12, 1), P("z", "เกณฑ์ z", "float", 0.5, 0, 2, 0.1)],
                   {"months": [1, 3], "z": [0.3, 0.8], **X}),
    "dual_momentum": S("Dual-Horizon Momentum", MOM, "โมเมนตัมระยะสั้นและยาวต้องเห็นตรงกัน (absolute momentum สองช่วง)", _dual_momentum,
                       [P("short_m", "ช่วงยาว (เดือน; สั้น = ¼)", "int", 3, 1, 12, 1)], {"short_m": [2, 6], **X}),
    "macd_zero": S("MACD Zero-Line", MOM, "เส้น MACD ข้ามศูนย์ = EMA เร็วข้าม EMA ช้า (ช้ากว่า signal cross แต่หลอกน้อย)", _macd_zero,
                   [P("fast", "EMA เร็ว", "int", 12, 5, 50, 1)], {"fast": [12, 24], **X}),
    "macd_filtered": S("MACD Signal in Trend", MOM, "MACD ตัด signal เฉพาะฝั่งเดียวกับศูนย์ — เข้าต่อเทรนด์หลังพักตัว", _macd_filtered,
                       [P("fast", "EMA เร็ว", "int", 12, 5, 50, 1)], {"fast": [12, 24], **X}),
    "aroon_osc": S("Aroon Oscillator Trend", TF, "Aroon Oscillator (Chande 1995) เกิน ±ระดับ = High/Low ใหม่เกิดฝั่งเดียวต่อเนื่อง", _aroon_osc,
                   [P("n", "N", "int", 25, 10, 100, 1), P("level", "ระดับ", "float", 50, 30, 90, 5)],
                   {"n": [25, 50], "level": [50, 70], **X}),
    "rsi_breakout": S("RSI Momentum Breakout", MOM, "RSI ทะลุ 70 (หลุด 30) = แรงใหม่เข้ามา — ใช้แบบตามโมเมนตัม ไม่ใช่ขายเมื่อ overbought", _rsi_breakout,
                      [P("n", "RSI", "int", 14, 5, 50, 1), P("level", "ระดับ", "float", 70, 55, 85, 5)],
                      {"n": [14, 28], "level": [65, 75], **X}),
    "rvi_trend": S("Relative Vigor Index", MOM, "RVI: ในขาขึ้นราคาปิดมักใกล้ High — ตัด signal ในฝั่งเดียวกับศูนย์", _rvi,
                   [P("n", "N", "int", 10, 4, 40, 1)], {"n": [10, 20], **X}),
    "pfe_trend": S("Polarized Fractal Efficiency", MOM, "PFE (Hannula 1994): การเคลื่อนที่ 'ตรง' แค่ไหน — ตรงมาก = เทรนด์", _pfe,
                   [P("n", "N", "int", 10, 5, 50, 1), P("level", "ระดับ", "float", 50, 10, 90, 5)],
                   {"n": [10, 30], "level": [30, 60], **X}),
    "apo_trend": S("Absolute Price Oscillator", MOM, "SMA เร็ว − SMA ช้า (3×) เปลี่ยนเครื่องหมาย", _apo,
                   [P("fast", "SMA เร็ว", "int", 20, 5, 100, 1)], {"fast": [15, 40], **X}),
    "momentum_accel": S("Accelerating Momentum", MOM, "โมเมนตัมเป็นบวกและยังเร่งตัว — เข้าช่วงที่เทรนด์แรงขึ้น", _momentum_accel,
                        [P("n", "N", "int", 20, 5, 200, 1)], {"n": [20, 60], **X}),
    # trend strength
    "adx_rising": S("ADX Trend Start", TF, "ADX ข้ามขึ้นเหนือระดับ (Wilder) = เทรนด์เพิ่งเริ่มแรง เข้าตามฝั่ง DI ที่นำ", _adx_rising,
                    [P("level", "ADX", "float", 25, 15, 40, 1)], {"level": [20, 30], **X}),
    "chop_break": S("Choppiness Breakout", TF, "Choppiness Index (Dreiss) ลงใต้ระดับ = ตลาดเลิกแกว่งเริ่มวิ่ง เข้าตามฝั่ง EMA50", _chop_break,
                    [P("n", "N", "int", 14, 7, 50, 1), P("level", "ระดับ", "float", 38.2, 30, 50, 0.5)],
                    {"n": [14, 28], "level": [38.2, 45], **X}),
    "efficiency_trend": S("Efficiency Ratio Trend", TF, "Kaufman Efficiency Ratio สูง = ราคาไปทางเดียวโดยไม่แกว่ง — สัญญาณเทรนด์บริสุทธิ์", _efficiency,
                          [P("n", "N", "int", 20, 5, 100, 1), P("level", "ระดับ", "float", 0.4, 0.1, 0.9, 0.05)],
                          {"n": [20, 50], "level": [0.3, 0.5], **X}),
    "rsq_trend": S("R-Squared Trend", TF, "R² ของเส้นตรงสูง = ราคาวิ่งเป็นเส้นตรง (เทรนด์) — เข้าตามความชัน", _rsq_trend,
                   [P("n", "N", "int", 30, 10, 150, 1), P("level", "R²", "float", 0.7, 0.3, 0.95, 0.05)],
                   {"n": [30, 60], "level": [0.6, 0.8], **X}),
    "slope_t": S("Significant Slope", TF, "ความชันเส้นถดถอยมีนัยสำคัญทางสถิติ (|t| > เกณฑ์) — เทรนด์ไม่ใช่โชค", _slope_t,
                 [P("n", "N", "int", 50, 10, 200, 1), P("t", "เกณฑ์ t", "float", 8, 2, 20, 0.5)],
                 {"n": [30, 80], "t": [6, 12], **X}),
    "vhf_trend": S("Vertical Horizontal Filter", TF, "VHF (White 1991) สูง = ตลาดมีทิศ — เข้าตามฝั่ง EMA", _vhf,
                   [P("n", "N", "int", 28, 10, 100, 1), P("level", "ระดับ", "float", 0.4, 0.2, 0.8, 0.05)],
                   {"n": [28, 60], "level": [0.35, 0.5], **X}),
    "tii_trend": S("Trend Intensity Index", TF, "TII (Pee 2002): สัดส่วนราคาที่อยู่เหนือ SMA — เกิน 80 / ต่ำกว่า 20 = เทรนด์เข้ม", _tii,
                   [P("n", "N", "int", 60, 20, 200, 1)], {"n": [40, 100], **X}),
    "er_kama": S("Efficient KAMA Trend", TF, "ราคาอยู่ฝั่ง KAMA และ Efficiency Ratio สูง — เทรนด์ที่ทั้งชัดและเรียบ", _er_kama_break,
                 [P("n", "N", "int", 20, 5, 60, 1), P("level", "ER", "float", 0.3, 0.1, 0.8, 0.05)],
                 {"n": [10, 30], "level": [0.25, 0.4], **X}),
    # volatility regimes
    "atr_expansion": S("ATR Expansion", VB, "ATR ระยะสั้นพุ่งเกิน k เท่า ATR ระยะยาว = แรงใหม่เข้า ตามทิศ 3 แท่งล่าสุดและ EMA50", _atr_expansion,
                       [P("k", "ATR5 > k × ATR50", "float", 1.3, 1.1, 3, 0.1)], {"k": [1.2, 1.5], **X}),
    "squeeze_momentum": S("Squeeze Momentum (LazyBear)", VB, "หลังการบีบตัว (BB อยู่ใน Keltner) ปล่อยตัว ตามทิศโมเมนตัม — ต่างจาก #7 ที่ใช้การทะลุแบนด์", _squeeze_mom,
                          [P("n", "N", "int", 20, 10, 60, 1)], {"n": [20, 40], **X}),
    "bbw_breakout": S("Band-Width Low Breakout", VB, "ความกว้าง Bollinger ต่ำสุดในรอบหลายเดือน (เงียบ) แล้วทะลุแบนด์ = เริ่มเทรนด์ใหม่", _bbw_breakout,
                      [P("months", "รอบ (เดือน)", "int", 6, 1, 12, 1), P("q", "ควอนไทล์ต่ำ", "float", 0.15, 0.05, 0.4, 0.05)],
                      {"months": [3, 6], "q": [0.1, 0.25], **X}),
    "range_expansion": S("Range Expansion Bar", VB, "แท่งใหญ่ k เท่าค่าเฉลี่ยและปิดใกล้ปลาย ในทิศ EMA100 = แรงซื้อ/ขายจริงจัง", _range_expansion,
                         [P("k", "ช่วงแท่ง > k × เฉลี่ย", "float", 2.0, 1.2, 4, 0.1)], {"k": [1.8, 2.5], **X}),
    "atr_trailing": S("ATR Trailing Line", TF, "เส้นตามหลังราคาปิด k×ATR (เลื่อนตามทิศเท่านั้น) — ปิดข้ามเส้น = กลับทิศ", _atr_trailing,
                      [P("k", "k × ATR", "float", 3.0, 1, 6, 0.1)], {"k": [2.5, 4.0], **X}),
    "hv_breakout": S("Volatility-Regime Breakout", VB, "ความผันผวนจริงเพิ่มจากฐาน + ทะลุกรอบ N แท่ง — เทรนด์มักเริ่มตอนความผันผวนเพิ่ม", _hv_breakout,
                     [P("k", "HV20 > k × HV120", "float", 1.2, 0.8, 2.5, 0.1), P("n", "กรอบ N", "int", 20, 5, 100, 1)],
                     {"k": [1.0, 1.4], "n": [20, 40], **X}),
    # price structure
    "higher_highs": S("Higher Highs / Higher Lows", TF, "นิยามเทรนด์ของ Dow: High และ Low สูงขึ้นต่อเนื่อง n แท่ง ในทิศ EMA50", _higher_highs,
                      [P("n", "ต่อเนื่อง n แท่ง", "int", 3, 2, 6, 1)], {"n": [3, 4], **X}),
    "swing_break": S("Swing Point Breakout", TF, "ทะลุจุดกลับตัว (swing) ล่าสุดที่ยืนยันแล้ว ในทิศ EMA100 — โครงสร้างตลาดเปลี่ยน", _swing_break,
                     [P("k", "แท่งยืนยันข้างละ k", "int", 5, 2, 15, 1)], {"k": [3, 8], **X}),
    "ha_smoothed": S("Smoothed Heikin-Ashi", TF, "Heikin-Ashi จาก OHLC ที่เกลี่ยแล้ว — สีเปลี่ยนน้อยลง ถือเทรนด์ได้นาน", _ha_smoothed,
                     [P("n", "เกลี่ย N", "int", 10, 3, 40, 1)], {"n": [8, 20], **X}),
    "point_figure": S("Point & Figure (3-box)", TF, "Point & Figure: กล่องขนาด ATR กลับทิศเมื่อสวน 3 กล่อง — กราฟไม่มีเวลา ดูแค่การเคลื่อนที่สำคัญ", _pnf,
                      [P("box_atr", "กล่อง (× ATR50)", "float", 1.0, 0.3, 3, 0.1)], {"box_atr": [0.7, 1.5], **X}),
    "consolidation_break": S("Tight-Range Breakout in Trend", VB, "กรอบ N แท่งแคบกว่า k×ATR (พักตัว) แล้วทะลุตามเทรนด์ EMA50/200", _inside_trend_break,
                             [P("n", "กรอบ N", "int", 12, 4, 50, 1), P("k", "แคบกว่า k × ATR", "float", 3.0, 1, 8, 0.5)],
                             {"n": [10, 20], "k": [2.5, 4.0], **X}),
    "gap_continuation": S("Gap-and-Go Continuation", MOM, "แท่งที่เปิดเหนือ High เดิม (ช่องว่าง) แล้วยังปิดห่างราคาเปิด > k×ATR ในทิศ EMA = แรงที่มักไปต่อ", _gap_continuation,
                          [P("k", "k × ATR", "float", 1.0, 0.5, 3, 0.1), P("n", "EMA", "int", 100, 20, 300, 1)],
                          {"k": [0.8, 1.2], "n": [50, 150], **X}),
    "two_bar_momentum": S("Two-Bar Momentum", MOM, "สองแท่งติดที่ปิดเพิ่ม (ลด) > k×ATR ในทิศ EMA — แรงซื้อขายต่อเนื่อง", _two_bar_momentum,
                          [P("k", "k × ATR", "float", 0.5, 0.2, 2, 0.1), P("n", "EMA", "int", 100, 20, 300, 1)],
                          {"k": [0.4, 0.7], "n": [50, 150], **X}),
    "weekly_momentum": S("Weekly Momentum", MOM, "เครื่องหมายผลตอบแทน n สัปดาห์ — look-back สั้นก็มีข้อมูลเทรนด์ (Moskowitz et al. 2012)", _weekly_momentum,
                         [P("weeks", "สัปดาห์", "int", 4, 1, 26, 1)], {"weeks": [2, 6], **X}),
}

# trimmed to 69 (31 existing trend / breakout / momentum systems + 69 = 100): near-duplicates of other entries dropped
for _k in ("roc_ma", "apo_trend", "kijun_cross", "two_bar_momentum", "gap_continuation", "tsf_cross"):
    TREND_STRATEGIES.pop(_k)
assert len(TREND_STRATEGIES) == 69, len(TREND_STRATEGIES)


# Plain-language docs (Thai) for the Systems page — merged into app/strategy_docs.D
TREND_DOCS: dict[str, tuple[list[str], str, str]] = {
    "sma_cross": (["SMA เร็ว (fast)", "SMA ช้า (fast × mult)"], "SMA เร็วตัดขึ้นเหนือ SMA ช้า", "SMA เร็วตัดลงใต้ SMA ช้า"),
    "dema_cross": (["DEMA เร็ว (fast)", "DEMA ช้า (3 × fast)"], "DEMA เร็วตัดขึ้นเหนือ DEMA ช้า", "DEMA เร็วตัดลงใต้ DEMA ช้า"),
    "price_ema": (["EMA (n)"], "ราคาปิดข้ามขึ้นเหนือ EMA (ถือขาขึ้นจนปิดใต้เส้น)", "ราคาปิดข้ามลงใต้ EMA"),
    "golden_cross": (["SMA ช้า (slow)", "SMA เร็ว (slow ÷ 4)"], "SMA เร็วตัดขึ้นเหนือ SMA ช้า (Golden Cross)", "SMA เร็วตัดลงใต้ SMA ช้า (Death Cross)"),
    "ema_ribbon": (["EMA n / 2n / 4n"], "แท่งแรกที่ ราคา > EMA n > EMA 2n > EMA 4n", "แท่งแรกที่ ราคา < EMA n < EMA 2n < EMA 4n"),
    "ema_slope": (["EMA (n)", "ความชันเทียบ k แท่งก่อน"], "EMA เปลี่ยนจากชี้ลงเป็นชี้ขึ้น (EMA วันนี้ > EMA k แท่งก่อน)", "EMA เปลี่ยนเป็นชี้ลง"),
    "ma_envelope": (["SMA (n)", "ซอง ± pct %"], "ราคาปิดเหนือซองบน (SMA × (1 + pct%)) — ถือจนหลุดซองล่าง", "ราคาปิดใต้ซองล่าง"),
    "mcginley": (["McGinley Dynamic (n)"], "ราคาปิดข้ามขึ้นเหนือเส้น McGinley", "ราคาปิดข้ามลงใต้เส้น McGinley"),
    "t3_trend": (["Tillson T3 (n, v = 0.7)"], "T3 เปลี่ยนจากลดลงเป็นเพิ่มขึ้น", "T3 เปลี่ยนจากเพิ่มขึ้นเป็นลดลง"),
    "zlema_cross": (["Zero-Lag EMA เร็ว (fast)", "Zero-Lag EMA ช้า (3 × fast)"], "ZLEMA เร็วตัดขึ้นเหนือ ZLEMA ช้า", "ZLEMA เร็วตัดลงใต้ ZLEMA ช้า"),
    "alma_trend": (["ALMA (n, offset 0.85, sigma 6)"], "ALMA สูงกว่าค่าเมื่อ 2 แท่งก่อน (เปลี่ยนเป็นชี้ขึ้น)", "ALMA เปลี่ยนเป็นชี้ลง"),
    "hma_cross": (["Hull MA เร็ว (fast)", "Hull MA ช้า (3 × fast)"], "HMA เร็วตัดขึ้นเหนือ HMA ช้า", "HMA เร็วตัดลงใต้ HMA ช้า"),
    "mtf_ema": (["EMA ใหญ่ (days วัน)", "EMA10", "EMA30"], "EMA10 ตัดขึ้นเหนือ EMA30 ขณะราคาอยู่เหนือ EMA ใหญ่", "EMA10 ตัดลงใต้ EMA30 ขณะราคาอยู่ใต้ EMA ใหญ่"),
    "ma_stack_pullback": (["EMA เร็ว (fast)", "EMA50", "EMA200"], "EMA50 > EMA200 และราคาปิดข้ามกลับขึ้นเหนือ EMA เร็ว", "EMA50 < EMA200 และราคาปิดข้ามกลับลงใต้ EMA เร็ว"),
    "donchian_mid": (["กึ่งกลางกรอบ Donchian n แท่งก่อนหน้า"], "ราคาปิดข้ามขึ้นเหนือกึ่งกลางกรอบ", "ราคาปิดข้ามลงใต้กึ่งกลางกรอบ"),
    "keltner_trend": (["Keltner Channel (n, mult × ATR)"], "ราคาปิดเหนือเส้นบน Keltner (ถือจนปิดใต้เส้นล่าง)", "ราคาปิดใต้เส้นล่าง Keltner"),
    "atr_bands": (["SMA (n)", "แถบ ± k × ATR(n)"], "ราคาปิดเหนือแถบบน", "ราคาปิดใต้แถบล่าง"),
    "bb_trend": (["Bollinger Bands (n, k)"], "ราคาปิดเหนือแบนด์บน (ถือจนปิดใต้แบนด์ล่าง)", "ราคาปิดใต้แบนด์ล่าง"),
    "chandelier": (["Chandelier: High สูงสุด n − k × ATR / Low ต่ำสุด n + k × ATR"], "ราคาปิดเหนือเส้นหยุดขาลง → กลับเป็นขาขึ้น", "ราคาปิดใต้เส้นหยุดขาขึ้น → กลับเป็นขาลง"),
    "close_breakout": (["ราคาปิดสูงสุด / ต่ำสุด n แท่งก่อนหน้า"], "ราคาปิดทะลุราคาปิดสูงสุด n แท่ง (แท่งแรก)", "ราคาปิดหลุดราคาปิดต่ำสุด n แท่ง"),
    "linreg_channel": (["เส้นถดถอย n แท่ง ± k SD (ของแท่งก่อนหน้า)"], "ราคาปิดเหนือขอบบนช่องถดถอย", "ราคาปิดใต้ขอบล่างช่องถดถอย"),
    "tsf_cross": (["Time Series Forecast (เส้นถดถอย n แท่ง พยากรณ์ 1 แท่ง)"], "ราคาปิดข้ามขึ้นเหนือค่าพยากรณ์", "ราคาปิดข้ามลงใต้ค่าพยากรณ์"),
    "median_channel": (["Median ราคาปิด n แท่ง", "± k × ATR14"], "ราคาปิดเหนือ median + k × ATR", "ราคาปิดใต้ median − k × ATR"),
    "ichimoku_cloud": (["Ichimoku: Tenkan (base ÷ 3), Kijun (base), Senkou A/B (เลื่อน base แท่ง)"], "ราคาปิดเหนือเมฆ (เหนือ Senkou A และ B)", "ราคาปิดใต้เมฆ"),
    "kijun_cross": (["Kijun-sen = กึ่งกลาง High/Low n แท่ง"], "ราคาปิดตัดขึ้นเหนือ Kijun", "ราคาปิดตัดลงใต้ Kijun"),
    "gann_hilo": (["SMA ของ High (n)", "SMA ของ Low (n)"], "ราคาปิดเหนือ SMA ของ High", "ราคาปิดใต้ SMA ของ Low"),
    "donchian_width": (["กรอบ Donchian n แท่ง", "ATR14"], "ทะลุ High n แท่ง (แท่งแรก) และกรอบกว้างกว่า k × ATR", "หลุด Low n แท่ง และกรอบกว้างกว่า k × ATR"),
    "dual_thrust": (["ช่วง = max(HH−LC, HC−LL) ของ n แท่ง", "EMA100"], "ราคาปิดเหนือ ราคาเปิด + k × ช่วง และเหนือ EMA100", "ราคาปิดใต้ ราคาเปิด − k × ช่วง และใต้ EMA100"),
    "volatility_stop": (["ATR (n)", "ราคาปิดที่ดีที่สุดตั้งแต่เข้า"], "ราคาปิดเด้งจากจุดต่ำสุดเกิน k × ATR → กลับเป็นขาขึ้น", "ราคาปิดถอยจากจุดสูงสุดเกิน k × ATR → กลับเป็นขาลง"),
    "renko_trend": (["อิฐ Renko ขนาด box_atr × ATR14"], "อิฐขึ้นใหม่ (หรือกลับทิศขึ้นหลังสวน bricks ก้อน)", "อิฐลงใหม่ (หรือกลับทิศลง)"),
    "ssl_channel": (["SMA ของ High (n)", "SMA ของ Low (n)"], "ราคาปิดเหนือ SMA ของ High (ถือจนปิดใต้ SMA ของ Low)", "ราคาปิดใต้ SMA ของ Low"),
    "roc_trend": (["Rate of Change n แท่ง (เกลี่ย EMA5)"], "ROC เปลี่ยนจากลบเป็นบวก", "ROC เปลี่ยนจากบวกเป็นลบ"),
    "roc_ma": (["ROC n แท่ง", "SMA ของ ROC"], "ROC ตัดขึ้นเหนือค่าเฉลี่ยของตัวเอง ขณะ ROC > 0", "ROC ตัดลงใต้ค่าเฉลี่ย ขณะ ROC < 0"),
    "tsi_cross": (["True Strength Index (long, 13)", "เส้นสัญญาณ EMA7"], "TSI ตัดขึ้นเหนือเส้นสัญญาณ ขณะ TSI > 0", "TSI ตัดลงใต้เส้นสัญญาณ ขณะ TSI < 0"),
    "kst": (["KST = ROC 10/15/20/30 (× scale) ถ่วงน้ำหนัก 1–4", "เส้นสัญญาณ SMA9"], "KST ตัดขึ้นเหนือเส้นสัญญาณ", "KST ตัดลงใต้เส้นสัญญาณ"),
    "ppo_flip": (["PPO = (EMA fast − EMA 2×fast) ÷ EMA ช้า", "Histogram = PPO − EMA9"], "Histogram เปลี่ยนเป็นบวก ขณะ PPO > 0", "Histogram เปลี่ยนเป็นลบ ขณะ PPO < 0"),
    "cmo_trend": (["Chande Momentum Oscillator (n)"], "CMO ข้ามขึ้นเหนือ +level", "CMO ข้ามลงใต้ −level"),
    "rsi50_trend": (["RSI (n)", "EMA100"], "RSI ข้ามขึ้นเหนือ 50 ขณะราคาเหนือ EMA100", "RSI ข้ามลงใต้ 50 ขณะราคาใต้ EMA100"),
    "stoch_trend": (["Stochastic %K (n, เกลี่ย 3)", "EMA100"], "%K ข้ามขึ้นเหนือ level ขณะราคาเหนือ EMA100", "%K ข้ามลงใต้ 100 − level ขณะราคาใต้ EMA100"),
    "willr_trend": (["Williams %R (n)"], "%R ข้ามขึ้นเหนือ −20", "%R ข้ามลงใต้ −80"),
    "coppock": (["Coppock = WMA(ROC ยาว + ROC สั้น)"], "Coppock เปลี่ยนจากลบเป็นบวก", "Coppock เปลี่ยนจากบวกเป็นลบ"),
    "schaff_trend": (["Schaff Trend Cycle (MACD fast / 2×fast, Stochastic 10 สองชั้น)"], "STC ข้ามขึ้นเหนือ 75", "STC ข้ามลงใต้ 25"),
    "fisher_trend": (["Fisher Transform ของราคากลาง (n)"], "Fisher เปลี่ยนเป็นชี้ขึ้น ขณะค่ามากกว่า 0", "Fisher เปลี่ยนเป็นชี้ลง ขณะค่าน้อยกว่า 0"),
    "tsmom_vol": (["ผลตอบแทน log ย้อนหลัง months เดือน", "ความผันผวน × √N"], "z = ผลตอบแทน ÷ ความผันผวน ขึ้นเหนือ +z", "z ลงใต้ −z"),
    "dual_momentum": (["ผลตอบแทน short_m เดือน", "ผลตอบแทน ¼ ของช่วงนั้น"], "ผลตอบแทนทั้งสองช่วงเป็นบวก", "ผลตอบแทนทั้งสองช่วงเป็นลบ"),
    "macd_zero": (["MACD = EMA fast − EMA 2.2×fast"], "MACD ข้ามขึ้นเหนือศูนย์", "MACD ข้ามลงใต้ศูนย์"),
    "macd_filtered": (["MACD (fast, 2.2×fast)", "Signal EMA9"], "MACD ตัดขึ้นเหนือ Signal ขณะ MACD > 0", "MACD ตัดลงใต้ Signal ขณะ MACD < 0"),
    "aroon_osc": (["Aroon Oscillator (n) = Aroon Up − Aroon Down"], "Aroon Osc ข้ามขึ้นเหนือ +level", "Aroon Osc ข้ามลงใต้ −level"),
    "rsi_breakout": (["RSI (n)"], "RSI ข้ามขึ้นเหนือ level (เช่น 70) — ตามแรง", "RSI ข้ามลงใต้ 100 − level"),
    "rvi_trend": (["Relative Vigor Index (n)", "Signal SMA4"], "RVI ตัดขึ้นเหนือ Signal ขณะ RVI > 0", "RVI ตัดลงใต้ Signal ขณะ RVI < 0"),
    "pfe_trend": (["Polarized Fractal Efficiency (n, เกลี่ย EMA5)"], "PFE ข้ามขึ้นเหนือ +level", "PFE ข้ามลงใต้ −level"),
    "apo_trend": (["SMA fast − SMA 3×fast"], "ค่าเปลี่ยนจากลบเป็นบวก", "ค่าเปลี่ยนจากบวกเป็นลบ"),
    "momentum_accel": (["ROC n แท่ง", "การเปลี่ยนแปลงของ ROC (ความเร่ง)"], "แท่งแรกที่ ROC > 0 และ ROC กำลังเพิ่ม", "แท่งแรกที่ ROC < 0 และ ROC กำลังลด"),
    "adx_rising": (["ADX (14)", "+DI / −DI (14)"], "ADX ข้ามขึ้นเหนือ level ขณะ +DI > −DI", "ADX ข้ามขึ้นเหนือ level ขณะ −DI > +DI"),
    "chop_break": (["Choppiness Index (n)", "EMA50"], "Choppiness ลงใต้ level ขณะราคาเหนือ EMA50", "Choppiness ลงใต้ level ขณะราคาใต้ EMA50"),
    "efficiency_trend": (["Kaufman Efficiency Ratio (n, มีเครื่องหมาย)"], "ER ข้ามขึ้นเหนือ +level", "ER ข้ามลงใต้ −level"),
    "rsq_trend": (["R² ของเส้นตรง n แท่ง", "ความชันเส้นถดถอย"], "R² ข้ามขึ้นเหนือ level ขณะความชันเป็นบวก", "R² ข้ามขึ้นเหนือ level ขณะความชันเป็นลบ"),
    "slope_t": (["t-stat ของความชันเส้นถดถอย n แท่ง"], "t ข้ามขึ้นเหนือ +เกณฑ์", "t ข้ามลงใต้ −เกณฑ์"),
    "vhf_trend": (["Vertical Horizontal Filter (n)", "EMA (n)"], "VHF ข้ามขึ้นเหนือ level ขณะราคาเหนือ EMA", "VHF ข้ามขึ้นเหนือ level ขณะราคาใต้ EMA"),
    "tii_trend": (["Trend Intensity Index (SMA n, นับ n÷2 แท่ง)"], "TII ข้ามขึ้นเหนือ 80", "TII ข้ามลงใต้ 20"),
    "er_kama": (["KAMA (n)", "Efficiency Ratio (n)"], "แท่งแรกที่ราคาเหนือ KAMA และ ER > level", "แท่งแรกที่ราคาใต้ KAMA และ ER > level"),
    "atr_expansion": (["ATR5", "ATR50", "EMA50"], "ATR5 ข้ามขึ้นเหนือ k × ATR50 ขณะราคาสูงกว่า 3 แท่งก่อนและเหนือ EMA50", "ATR5 ข้ามขึ้นเหนือ k × ATR50 ขณะราคาต่ำกว่า 3 แท่งก่อนและใต้ EMA50"),
    "squeeze_momentum": (["Bollinger (n, 2)", "Keltner (n, 1.5)", "โมเมนตัม = ความชันของราคา − กึ่งกลางกรอบ"], "แท่งแรกหลังปล่อยการบีบตัว โมเมนตัมเป็นบวกและเพิ่มขึ้น", "แท่งแรกหลังปล่อยการบีบตัว โมเมนตัมเป็นลบและลดลง"),
    "bbw_breakout": (["ความกว้าง Bollinger (20, 2)", "ควอนไทล์ q ของรอบ months เดือน"], "ช่วง 10 แท่งล่าสุดความกว้างแคบสุดๆ แล้วราคาปิดทะลุแบนด์บน", "ความกว้างแคบสุดๆ แล้วราคาปิดหลุดแบนด์ล่าง"),
    "range_expansion": (["ช่วงแท่ง High − Low", "ค่าเฉลี่ยช่วง 20 แท่ง", "EMA100"], "แท่งเขียวช่วงใหญ่ > k × ค่าเฉลี่ย ปิดในส่วนบนสุด ¼ และเหนือ EMA100", "แท่งแดงช่วงใหญ่ ปิดในส่วนล่างสุด ¼ และใต้ EMA100"),
    "atr_trailing": (["เส้นตามหลังราคาปิด k × ATR14"], "ราคาปิดข้ามขึ้นเหนือเส้นตามหลังขาลง", "ราคาปิดข้ามลงใต้เส้นตามหลังขาขึ้น"),
    "hv_breakout": (["ความผันผวนจริง 20 แท่ง (HV20) เทียบ HV120", "กรอบ High/Low n แท่ง"], "HV20 > k × HV120 และราคาปิดทะลุ High n แท่ง", "HV20 > k × HV120 และราคาปิดหลุด Low n แท่ง"),
    "higher_highs": (["High และ Low ต่อเนื่อง n แท่ง", "EMA50"], "แท่งแรกที่ High และ Low สูงขึ้นติดกัน n แท่ง และเหนือ EMA50", "แท่งแรกที่ High และ Low ต่ำลงติดกัน n แท่ง และใต้ EMA50"),
    "swing_break": (["Swing High / Low (ยืนยันข้างละ k แท่ง)", "EMA100"], "ราคาปิดตัดขึ้นเหนือ Swing High ล่าสุด และเหนือ EMA100", "ราคาปิดตัดลงใต้ Swing Low ล่าสุด และใต้ EMA100"),
    "ha_smoothed": (["Heikin-Ashi จาก OHLC เกลี่ย EMA n"], "แท่ง HA (เกลี่ย) เปลี่ยนเป็นเขียว", "แท่ง HA (เกลี่ย) เปลี่ยนเป็นแดง"),
    "point_figure": (["Point & Figure: กล่อง box_atr × ATR50, กลับทิศ 3 กล่อง"], "คอลัมน์ X ใหม่ (ราคาเด้งจากจุดต่ำสุด ≥ 3 กล่อง)", "คอลัมน์ O ใหม่ (ราคาถอยจากจุดสูงสุด ≥ 3 กล่อง)"),
    "consolidation_break": (["กรอบ n แท่ง", "ATR14", "EMA50 / EMA200"], "กรอบแคบกว่า k × ATR แล้วราคาปิดทะลุ High กรอบ ขณะ EMA50 > EMA200", "กรอบแคบแล้วราคาปิดหลุด Low กรอบ ขณะ EMA50 < EMA200"),
    "gap_continuation": (["ราคาเปิดเทียบ High/Low แท่งก่อน", "ATR14", "EMA (n)"], "เปิดเหนือ High แท่งก่อน แล้วปิดสูงกว่าราคาเปิด > k × ATR และเหนือ EMA", "เปิดใต้ Low แท่งก่อน แล้วปิดต่ำกว่าราคาเปิด > k × ATR และใต้ EMA"),
    "two_bar_momentum": (["การเปลี่ยนแปลงราคาปิด 2 แท่งล่าสุด", "ATR14", "EMA (n)"], "สองแท่งติดปิดเพิ่มเกิน k × ATR และเหนือ EMA", "สองแท่งติดปิดลดเกิน k × ATR และใต้ EMA"),
    "weekly_momentum": (["ผลตอบแทนย้อนหลัง weeks สัปดาห์"], "ผลตอบแทนเปลี่ยนจากลบเป็นบวก", "ผลตอบแทนเปลี่ยนจากบวกเป็นลบ"),
}
TREND_DOCS = {k: v for k, v in TREND_DOCS.items() if k in TREND_STRATEGIES}
assert set(TREND_DOCS) == set(TREND_STRATEGIES)
