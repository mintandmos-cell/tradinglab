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
