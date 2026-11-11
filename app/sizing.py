"""Position sizing for MT5 lots, in account currency (USD)."""
from __future__ import annotations

import math

from .config import symbol_spec


# quote currency -> (USD pair, how to convert): P/L in the quote currency x factor = USD
_FX = {"JPY": ("USDJPY", "div"), "CAD": ("USDCAD", "div"), "CHF": ("USDCHF", "div"),
       "EUR": ("EURUSD", "mul"), "GBP": ("GBPUSD", "mul"), "AUD": ("AUDUSD", "mul"), "NZD": ("NZDUSD", "mul")}


def _last_price(sym: str) -> float | None:
    try:
        from . import data
        df = data.load_h1(sym)
        return float(df["close"].iloc[-1]) if len(df) else None
    except Exception:
        return None


def value_per_price_unit(sym: str, price: float, mt5_info: dict | None = None, settings: dict | None = None) -> float:
    """USD P/L of 1.00 lot when price moves by 1.0.
    1. the live MT5 tick value; 2. the tick value the PC exported (data/exness/costs.json, USD account); 3. contract size
    converted from the quote currency with the latest USD rate (9 Nov 2026: GBPJPY / DE30 / JP225 used to raise here, so the
    cloud could not size their alerts when the PC was off)."""
    if mt5_info and mt5_info.get("tick_size") and mt5_info.get("tick_value"):
        return mt5_info["tick_value"] / mt5_info["tick_size"]
    spec = symbol_spec(sym, settings)
    contract = float(spec["contract"])
    if spec["quote"] == "USD":
        return contract
    if sym.startswith("USD"):          # e.g. USDJPY: P/L in JPY -> divide by USDJPY
        return contract / price
    from .config import BROKER_COSTS, read_json
    x = read_json(BROKER_COSTS, {}).get("symbols", {}).get(sym) or {}
    if x.get("tick_value") and x.get("tick_size"):
        return float(x["tick_value"]) / float(x["tick_size"])
    if spec["quote"] == "HKD":         # pegged to the dollar (7.75-7.85): no USDHKD chart in the app
        return contract / 7.8
    pair, how = _FX.get(spec["quote"], (None, None))
    rate = _last_price(pair) if pair else None
    if rate:
        return contract / rate if how == "div" else contract * rate
    raise ValueError(f"no USD conversion for {sym}")


def broker_min_lot(sym: str) -> float | None:
    from .config import BROKER_COSTS, read_json
    v = (read_json(BROKER_COSTS, {}).get("symbols", {}).get(sym) or {}).get("volume_min")
    return float(v) if v else None


def lots_for(sym: str, entry: float, sl: float, balance: float, risk_pct: float,
             mt5_info: dict | None = None, settings: dict | None = None, lot_step: float = 0.01, min_lot: float = 0.01) -> dict:
    dist = abs(entry - sl)
    if dist <= 0 or balance <= 0 or risk_pct <= 0:
        return {"lots": 0.0, "risk_usd": 0.0, "raw_lots": 0.0}
    if mt5_info:
        lot_step = mt5_info.get("volume_step") or lot_step
        min_lot = mt5_info.get("volume_min") or min_lot
    else:                              # cloud / web: the broker's minimum as the PC exported it (US500 0.14, JP225 3.0 …)
        min_lot = broker_min_lot(sym) or min_lot
    vpu = value_per_price_unit(sym, entry, mt5_info, settings)
    risk_usd = balance * risk_pct / 100.0
    raw = risk_usd / (dist * vpu)
    lots = math.floor(raw / lot_step + 1e-9) * lot_step
    lots = round(lots, 6)
    return {"lots": lots if lots >= min_lot else 0.0, "raw_lots": round(raw, 4), "risk_usd": round(risk_usd, 2),
            "actual_risk_usd": round(lots * dist * vpu, 2), "value_per_point_per_lot": vpu,
            "below_min_lot": lots < min_lot, "min_lot": min_lot}
