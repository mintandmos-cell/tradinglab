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
