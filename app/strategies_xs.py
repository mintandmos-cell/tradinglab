"""New trend ideas fixed BEFORE any of their results were seen (user, 9 Nov 2026: "ideas that are really different").

1. Cross-sectional momentum (#518, #519) — not "is this market going up?" but "is it going up MORE than the others?".
   Every bar, each market's volatility-adjusted return over the look-back is ranked against the other 29 markets
   (or against its own asset group). Long the top third while its own trend is up, short the bottom third while its own
   trend is down. Economic rationale: Asness, Moskowitz & Pedersen (2013) "Value and Momentum Everywhere" — relative
   momentum pays across asset classes; Jegadeesh & Titman (1993); Okunev & White (2003) for currencies.
2. Trend ensemble (#520) — five classic CTA trend rules vote (1-, 3- and 12-month returns, the 55-bar Donchian channel
   side, the 50/200 moving-average side), entry when k of the 5 agree. Rationale: a blend of look-backs is steadier than
   any single rule (Hurst, Ooi & Pedersen 2017 "A Century of Evidence on Trend-Following"; AQR's multi-speed trend).

All signals use closed bars only; the peers' closes are taken as of the same bar (asof, never later). Exits are the
library's (ATR SL, R:R TP or no TP, trailing stop, opposite signal). ≤ 2 signal parameters, grid ≤ 16.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import indicators as ta

log = logging.getLogger("tradinglab.strategies_xs")
MOM = "Momentum"
_CACHE: dict = {}


def _tf_of(df: pd.DataFrame) -> str:
    d = pd.Series(df.index[-300:]).diff().dropna().median() if len(df) > 3 else pd.Timedelta(hours=1)
    h = float(d.total_seconds() / 3600) if pd.notna(d) else 1.0
    return "H1" if h <= 1.5 else "H4" if h <= 6 else "D1"


def _bpm(tf: str) -> int:
    return {"H1": 520, "H4": 130, "D1": 22}[tf]


def _zscore(close: pd.Series, n: int) -> pd.Series:
    """Volatility-adjusted n-bar return: log return / (bar sigma x sqrt(n))."""
    lc = np.log(close.astype(float))
    r = lc - lc.shift(n)
    sd = lc.diff().rolling(min(n, 500), min_periods=max(20, min(n, 500) // 2)).std() * np.sqrt(n)
    return r / sd


def _peer_z(tf: str, n: int) -> pd.DataFrame:
    """z-scores of every universe market on its own bars (cached per process)."""
    key = (tf, n)
    if key not in _CACHE:
        from . import data
        from .config import SYMBOLS
        cols = {}
        for sym in SYMBOLS:
            try:
                bars = data.get_bars(sym, tf)
                if len(bars) > n + 50:
                    cols[sym] = _zscore(bars["close"], n)
            except Exception as e:  # a market without data simply does not take part
                log.debug("peer %s %s: %s", sym, tf, e)
        _CACHE[key] = cols
    return _CACHE[key]


def _rank(df: pd.DataFrame, n: int, group: bool) -> tuple[np.ndarray, np.ndarray]:
    """(own z, own percentile among the peers 0..1) on df's bars. NaN where fewer than 6 peers have a value."""
    from .config import SYMBOLS
    tf = _tf_of(df)
    own_sym = df.attrs.get("symbol")
    own = _zscore(df["close"], n)
    peers = _peer_z(tf, n)
    if group and own_sym in SYMBOLS:
        g = SYMBOLS[own_sym].get("group")
        grp = {"Stocks": ("Stocks", "Indices"), "Indices": ("Stocks", "Indices"), "Metals": ("Metals", "Energy"),
               "Energy": ("Metals", "Energy"), "Crypto": ("Crypto", "Stocks", "Indices")}.get(g, (g,))
        names = [s for s in peers if SYMBOLS[s].get("group") in grp]
    else:
        names = list(peers)
    names = [s for s in names if s != own_sym]
    if len(names) < 5:
        names = [s for s in peers if s != own_sym]
    mat = np.column_stack([peers[s].reindex(df.index, method="ffill").to_numpy() for s in names]) if names else np.empty((len(df), 0))
    o = own.to_numpy()
    valid = np.isfinite(mat)
    cnt = valid.sum(axis=1)
    below = np.where(valid, mat < o[:, None], False).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        pct = np.where((cnt >= 6) & np.isfinite(o), below / np.maximum(cnt, 1), np.nan)
    return o, pct


def _flip(state: np.ndarray) -> np.ndarray:
    st = pd.Series(np.asarray(state, float)).replace(0, np.nan).ffill().fillna(0).to_numpy()
    prev = np.r_[0.0, st[:-1]]
    return ((st > 0) & (prev <= 0)).astype(int) - ((st < 0) & (prev >= 0)).astype(int)


def _xs(df: pd.DataFrame, p: dict, group: bool):
    n = int(p["months"]) * _bpm(_tf_of(df))
    z, pct = _rank(df, n, group)
    st = np.where((pct >= 2 / 3) & (z > 0), 1, np.where((pct <= 1 / 3) & (z < 0), -1, 0))
    return _flip(st), {}


def _xs_mom(df, p):
    return _xs(df, p, group=False)


def _xs_mom_group(df, p):
    return _xs(df, p, group=True)


def _ens_trend(df, p):
    c = df["close"].to_numpy(float)
    m = _bpm(_tf_of(df))
    votes = np.zeros(len(c))
    for months in (1, 3, 12):
        r = c / ta.shift(c, months * m) - 1
        votes += np.where(np.isnan(r), 0, np.sign(r))
    hi, lo = ta.highest(df["high"].to_numpy(float), 55), ta.lowest(df["low"].to_numpy(float), 55)
    mid = (hi + lo) / 2
    votes += np.where(np.isnan(mid), 0, np.sign(c - mid))
    f, s = ta.sma(c, 50), ta.sma(c, 200)
    votes += np.where(np.isnan(s), 0, np.sign(f - s))
    k = int(p["k"])
    st = np.where(votes >= k, 1, np.where(votes <= -k, -1, 0))
    return _flip(st), {}


def _S(name, rationale, fn, params, grid):
    return {"name": name, "family": MOM, "rationale": rationale, "fn": fn, "params": params, "grid": grid}


def _P(key, label, typ, default, lo, hi, step):
    return {"key": key, "label": label, "type": typ, "default": default, "min": lo, "max": hi, "step": step}


_X = {"sl_atr": [2.0, 3.0], "rr": [2.5]}
XS_STRATEGIES: dict[str, dict] = {
    "xs_mom": _S("Cross-Sectional Momentum (all markets)",
                 "เทียบความแรงข้ามตลาด: ซื้อเมื่อโมเมนตัมปรับความผันผวนอยู่ 1/3 บนของ 30 ตลาดและตัวเองเป็นขาขึ้น ขายเมื่ออยู่ 1/3 ล่างและเป็นขาลง — Asness, Moskowitz & Pedersen (2013)",
                 _xs_mom, [_P("months", "ย้อนหลัง (เดือน)", "int", 3, 1, 12, 1)], {"months": [3, 12], **_X}),
    "xs_mom_group": _S("Cross-Sectional Momentum (within asset group)",
                       "เทียบความแรงในกลุ่มเดียวกัน (FX กับ FX, โลหะ/พลังงาน, หุ้น/ดัชนี) — แยกผลของทั้งกลุ่มออก เหลือความแรงเฉพาะตัว (Okunev & White 2003; Asness et al. 2013)",
                       _xs_mom_group, [_P("months", "ย้อนหลัง (เดือน)", "int", 3, 1, 12, 1)], {"months": [3, 12], **_X}),
    "ens_trend": _S("Trend Ensemble (5 classic rules vote)",
                    "รวม 5 กฎตามเทรนด์คลาสสิก (ผลตอบแทน 1/3/12 เดือน, ฝั่งของกรอบ Donchian 55, MA50 เทียบ MA200) เข้าเมื่อเห็นตรงกัน k เสียง — Hurst, Ooi & Pedersen (2017)",
                    _ens_trend, [_P("k", "ต้องเห็นตรงกัน (เสียงจาก 5)", "int", 3, 3, 5, 1)], {"k": [3, 5], **_X}),
}
XS_DOCS = {
    "xs_mom": (["z = ผลตอบแทน log months เดือน ÷ (ความผันผวน × √N)", "อันดับเปอร์เซ็นไทล์ของ z เทียบอีก 29 ตลาด (ราคาปิดแท่งเดียวกัน)"],
               "เข้าสู่ 1/3 บน และ z > 0", "เข้าสู่ 1/3 ล่าง และ z < 0"),
    "xs_mom_group": (["z เหมือน xs_mom", "อันดับเทียบตลาดในกลุ่มเดียวกัน (ถ้าน้อยกว่า 5 ตลาด ใช้ทุกตลาด)"],
                     "เข้าสู่ 1/3 บนของกลุ่ม และ z > 0", "เข้าสู่ 1/3 ล่างของกลุ่ม และ z < 0"),
    "ens_trend": (["ผลตอบแทน 1, 3, 12 เดือน", "ฝั่งของกึ่งกลาง Donchian 55", "SMA50 เทียบ SMA200"],
                  "คะแนนรวม ≥ +k (จาก 5 เสียง)", "คะแนนรวม ≤ −k"),
}
