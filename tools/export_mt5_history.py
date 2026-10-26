"""Export the longest H1 history your MT5 broker has -> data/history/{SYMBOL}_H1.csv.gz (then commit + push).

The cloud (GitHub Actions) only gets ~2 years of H1 from Yahoo and Dukascopy blocks cloud servers, so the long
history has to come from a home PC once. The cloud then joins it with fresh Yahoo bars (app/data.py, with_history),
scaled to the broker's price level, and the Saturday research runs on the full length.

Run with MT5 open and logged in:  export_mt5_history.bat   (or  .venv\\Scripts\\python -m tools.export_mt5_history)
Tip: MT5 -> Tools -> Options -> Charts -> "Max bars in chart" = Unlimited lets the terminal keep more history.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config import HISTORY_DIR, load_settings  # noqa: E402

SYMS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "NAS100", "US500"]   # crypto already has years of Binance data


def main() -> int:
    import MetaTrader5 as mt5
    s = load_settings()
    if not mt5.initialize(path=s.get("mt5_path") or None, timeout=30000):
        print("เชื่อม MT5 ไม่ได้ — เปิด MT5 และล็อกอินก่อน:", mt5.last_error())
        return 1
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    for sym in SYMS:
        name = s["mt5_symbol_map"].get(sym, sym)
        mt5.symbol_select(name, True)
        rates = None
        for n in (200000, 120000, 100000, 60000):          # ask for as much as the terminal allows
            rates = mt5.copy_rates_from_pos(name, mt5.TIMEFRAME_H1, 0, n)
            if rates is not None and len(rates):
                break
        if rates is None or not len(rates):
            print(f"{sym} ({name}): ไม่มีข้อมูล — {mt5.last_error()}")
            continue
        df = pd.DataFrame(rates)[["time", "open", "high", "low", "close"]]
        df = df.iloc[:-1]                                   # drop the still-forming bar
        df.to_csv(HISTORY_DIR / f"{sym}_H1.csv.gz", index=False, compression="gzip")
        t0, t1 = pd.to_datetime(df.time.iloc[0], unit="s"), pd.to_datetime(df.time.iloc[-1], unit="s")
        print(f"{sym} ({name}): {len(df):,} แท่ง  {t0:%Y-%m-%d} -> {t1:%Y-%m-%d}  ({(t1 - t0).days / 365.25:.1f} ปี)")
        ok += 1
    mt5.shutdown()
    print(f"\nบันทึกแล้ว {ok}/{len(SYMS)} สินทรัพย์ -> {HISTORY_DIR}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
