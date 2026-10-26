"""Causal indicators on numpy arrays (value at i uses bars <= i only)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(span=n, adjust=False, min_periods=n).mean().to_numpy()


def sma(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=n).mean().to_numpy()


def rma(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean().to_numpy()


def atr(h, l, c, n: int = 14) -> np.ndarray:
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    return rma(tr, n)


def rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(c, prepend=np.nan)
    up, dn = rma(np.where(d > 0, d, 0.0), n), rma(np.where(d < 0, -d, 0.0), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        return 100 - 100 / (1 + up / dn)


def highest(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=n).max().to_numpy()


def lowest(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=n).min().to_numpy()


def bollinger(c: np.ndarray, n: int = 20, k: float = 2.0):
    s = pd.Series(c)
    mid = s.rolling(n, min_periods=n).mean()
    sd = s.rolling(n, min_periods=n).std(ddof=0)
    return mid.to_numpy(), (mid + k * sd).to_numpy(), (mid - k * sd).to_numpy()


def shift(x: np.ndarray, k: int = 1) -> np.ndarray:
    out = np.full_like(x, np.nan, dtype=float)
    out[k:] = x[:-k]
    return out


def dmi(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 14):
    """Wilder's directional movement: returns (+DI, -DI, ADX)."""
    up = h - np.r_[np.nan, h[:-1]]
    dn = np.r_[np.nan, l[:-1]] - l
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(h, l, c, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * rma(pdm, n) / a
        mdi = 100 * rma(mdm, n) / a
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    return pdi, mdi, rma(dx, n)


def keltner(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 20, mult: float = 1.5):
    mid = ema(c, n)
    a = atr(h, l, c, n)
    return mid, mid + mult * a, mid - mult * a


def stdev(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=n).std(ddof=0).to_numpy()


def bars_since_extreme(x: np.ndarray, n: int, highest_: bool) -> np.ndarray:
    """Bars since the highest (or lowest) value of the last n+1 bars (Aroon style); NaN during warm-up."""
    out = np.full(len(x), np.nan)
    if len(x) <= n:
        return out
    w = np.lib.stride_tricks.sliding_window_view(np.nan_to_num(x, nan=-np.inf if highest_ else np.inf), n + 1)
    pos = np.argmax(w[:, ::-1], axis=1) if highest_ else np.argmin(w[:, ::-1], axis=1)  # most recent extreme
    out[n:] = pos
    return out


def supertrend(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 10, mult: float = 3.0):
    """Classic Supertrend: returns (line, direction +1/-1)."""
    a = atr(h, l, c, n)
    mid = (h + l) / 2
    ub, lb = mid + mult * a, mid - mult * a
    fu, fl = ub.copy(), lb.copy()
    d = np.zeros(len(c))
    line = np.full(len(c), np.nan)
    for i in range(1, len(c)):
        if not np.isfinite(a[i]):
            continue
        if np.isfinite(fu[i - 1]):
            fu[i] = ub[i] if (ub[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
            fl[i] = lb[i] if (lb[i] > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]
        prev = d[i - 1] or 1
        d[i] = 1 if (prev < 0 and c[i] > fu[i]) else -1 if (prev > 0 and c[i] < fl[i]) else prev
        line[i] = fl[i] if d[i] > 0 else fu[i]
    return line, d


def wma(x: np.ndarray, n: int) -> np.ndarray:
    """Linearly weighted moving average (causal); NaN inputs propagate to the windows that contain them."""
    n = max(int(n), 1)
    w = np.arange(1, n + 1, dtype=float)
    xx = np.nan_to_num(x)
    out = np.convolve(xx, w[::-1], "full")[: len(x)] / w.sum()
    bad = pd.Series(np.isnan(x).astype(float)).rolling(n, min_periods=1).max().to_numpy() > 0
    out[bad] = np.nan
    out[: n - 1] = np.nan
    return out


def kama(c: np.ndarray, n: int = 10, fast: int = 2, slow: int = 30) -> np.ndarray:
    """Kaufman Adaptive Moving Average."""
    change = np.abs(c - shift(c, n))
    vol = pd.Series(np.abs(np.diff(c, prepend=np.nan))).rolling(n, min_periods=n).sum().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        er = np.where(vol > 0, change / vol, 0.0)
    sc = (er * (2 / (fast + 1) - 2 / (slow + 1)) + 2 / (slow + 1)) ** 2
    out = np.full(len(c), np.nan)
    for i in range(n, len(c)):
        prev = out[i - 1] if np.isfinite(out[i - 1]) else c[i - 1]
        out[i] = prev + sc[i] * (c[i] - prev) if np.isfinite(sc[i]) else prev
    return out


def psar(h: np.ndarray, l: np.ndarray, step: float = 0.02, max_step: float = 0.2):
    """Wilder's Parabolic SAR: returns (sar, direction +1/-1)."""
    n = len(h)
    sar, d = np.full(n, np.nan), np.zeros(n)
    if n < 3:
        return sar, d
    up, af, ep, s = True, step, h[0], l[0]
    for i in range(1, n):
        s = s + af * (ep - s)
        if up:
            s = min(s, l[i - 1], l[i - 2] if i > 1 else l[i - 1])
            if l[i] < s:
                up, s, ep, af = False, ep, l[i], step
            elif h[i] > ep:
                ep, af = h[i], min(af + step, max_step)
        else:
            s = max(s, h[i - 1], h[i - 2] if i > 1 else h[i - 1])
            if h[i] > s:
                up, s, ep, af = True, ep, h[i], step
            elif l[i] < ep:
                ep, af = l[i], min(af + step, max_step)
        sar[i], d[i] = s, 1 if up else -1
    return sar, d


def cci(h, l, c, n: int = 20) -> np.ndarray:
    tp = (h + l + c) / 3
    out = np.full(len(c), np.nan)
    if len(c) < n:
        return out
    w = np.lib.stride_tricks.sliding_window_view(tp, n)
    m = w.mean(axis=1)
    md = np.abs(w - m[:, None]).mean(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        out[n - 1:] = (tp[n - 1:] - m) / (0.015 * md)
    return out


def rolling_sum(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=n).sum().to_numpy()


def linreg_slope(x: np.ndarray, n: int) -> np.ndarray:
    t = np.arange(len(x), dtype=float)
    sx, stx = rolling_sum(x, n), rolling_sum(t * x, n)
    st, st2 = rolling_sum(t, n), rolling_sum(t * t, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        return (n * stx - st * sx) / (n * st2 - st * st)


def heikin_ashi(o, h, l, c):
    hc = (o + h + l + c) / 4
    ho = np.empty(len(c))
    if len(c):
        ho[0] = (o[0] + c[0]) / 2
        for i in range(1, len(c)):
            ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    return ho, np.maximum.reduce([h, ho, hc]), np.minimum.reduce([l, ho, hc]), hc
