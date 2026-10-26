"""Position sizing for MT5 lots, in account currency (USD)."""
from __future__ import annotations

import math

from .config import symbol_spec


def value_per_price_unit(sym: str, price: float, mt5_info: dict | None = None, settings: dict | None = None) -> float:
    """USD P/L of 1.00 lot when price moves by 1.0."""
    if mt5_info and mt5_info.get("tick_size") and mt5_info.get("tick_value"):
        return mt5_info["tick_value"] / mt5_info["tick_size"]
    spec = symbol_spec(sym, settings)
    contract = float(spec["contract"])
    if spec["quote"] == "USD":
        return contract
    if sym.startswith("USD"):          # e.g. USDJPY: P/L in JPY -> divide by USDJPY
        return contract / price
    raise ValueError(f"no USD conversion for {sym}")


def lots_for(sym: str, entry: float, sl: float, balance: float, risk_pct: float,
             mt5_info: dict | None = None, settings: dict | None = None, lot_step: float = 0.01, min_lot: float = 0.01) -> dict:
    dist = abs(entry - sl)
    if dist <= 0 or balance <= 0 or risk_pct <= 0:
        return {"lots": 0.0, "risk_usd": 0.0, "raw_lots": 0.0}
    if mt5_info:
        lot_step = mt5_info.get("volume_step") or lot_step
        min_lot = mt5_info.get("volume_min") or min_lot
    vpu = value_per_price_unit(sym, entry, mt5_info, settings)
    risk_usd = balance * risk_pct / 100.0
    raw = risk_usd / (dist * vpu)
    lots = math.floor(raw / lot_step + 1e-9) * lot_step
    lots = round(lots, 6)
    return {"lots": lots if lots >= min_lot else 0.0, "raw_lots": round(raw, 4), "risk_usd": round(risk_usd, 2),
            "actual_risk_usd": round(lots * dist * vpu, 2), "value_per_point_per_lot": vpu,
            "below_min_lot": lots < min_lot}
