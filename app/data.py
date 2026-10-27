"""Market data: MetaTrader 5 (broker-exact) when available, else Yahoo Finance / Binance.

All bars are UTC, columns open/high/low/close/volume, indexed by bar open time.
H1 is the base series; H4 and D1 are resampled from it so every source behaves the same.
History is cached on disk and merged on refresh, so free-data history grows over time.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

from .config import CACHE_DIR, HISTORY_DIR, SYMBOLS, TIMEFRAMES, load_settings

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", module="yfinance")
log = logging.getLogger("tradinglab.data")

_mem: dict[str, tuple[float, pd.DataFrame]] = {}
_lock = threading.RLock()
_mt5_state = {"ok": None, "checked": 0.0, "error": ""}
REFRESH_SEC = 240


# ----------------------------------------------------------------------------- MT5
def _mt5_init(settings) -> bool:
    now = time.time()
    if _mt5_state["ok"] is not None and now - _mt5_state["checked"] < 300:
        return bool(_mt5_state["ok"])
    _mt5_state["checked"] = now
    try:
        import MetaTrader5 as mt5
        ok = mt5.initialize(path=settings.get("mt5_path") or None, timeout=20000)
        if ok and mt5.terminal_info() is not None and mt5.terminal_info().connected:
            _mt5_state.update(ok=True, error="")
            return True
        _mt5_state.update(ok=False, error=str(mt5.last_error()))
    except Exception as e:  # package missing, terminal not installed, ...
        _mt5_state.update(ok=False, error=repr(e))
    return False


def mt5_status() -> dict:
    s = load_settings()
    if s["data_source"] == "free":
        return {"enabled": False, "connected": False, "error": "data_source = free"}
    ok = _mt5_init(s)
    info = {}
    if ok:
        import MetaTrader5 as mt5
        a = mt5.account_info()
        if a:
            info = {"server": a.server, "currency": a.currency, "balance": a.balance, "leverage": a.leverage}
    return {"enabled": True, "connected": ok, "error": _mt5_state["error"], "account": info}


def mt5_symbol_info(sym: str) -> dict | None:
    s = load_settings()
    if s["data_source"] == "free" or not _mt5_init(s):
        return None
    import MetaTrader5 as mt5
    name = s["mt5_symbol_map"].get(sym, sym)
    mt5.symbol_select(name, True)
    i = mt5.symbol_info(name)
    if not i:
        return None
    return {"name": name, "contract": i.trade_contract_size, "tick_size": i.trade_tick_size,
            "tick_value": i.trade_tick_value, "volume_min": i.volume_min, "volume_step": i.volume_step,
            "digits": i.digits, "bid": i.bid, "ask": i.ask, "spread_price": (i.ask - i.bid) if i.ask and i.bid else None}


def _fetch_mt5(sym: str, settings) -> pd.DataFrame | None:
    import MetaTrader5 as mt5
    name = settings["mt5_symbol_map"].get(sym, sym)
    mt5.symbol_select(name, True)
    rates = mt5.copy_rates_from_pos(name, mt5.TIMEFRAME_H1, 0, 60000)
    if rates is None or len(rates) == 0:
        log.warning("MT5 no data for %s: %s", name, mt5.last_error())
        return None
    df = pd.DataFrame(rates)
    df.index = server_to_utc(pd.to_datetime(df["time"], unit="s"), settings)
    return df.rename(columns={"tick_volume": "volume"})[["open", "high", "low", "close", "volume"]]


# ----------------------------------------------------------------------------- free sources
def _fetch_yahoo(ticker: str) -> pd.DataFrame:
    import yfinance as yf
    d = yf.download(ticker, period="730d", interval="1h", progress=False, auto_adjust=False, threads=False)
    if d is None or d.empty:
        raise RuntimeError(f"Yahoo returned no data for {ticker}")
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    d.index = pd.to_datetime(d.index, utc=True)
    return d.astype(float)


# api.binance.com refuses US addresses (GitHub Actions runners); the public market-data mirror does not
BINANCE_HOSTS = ("https://data-api.binance.vision", "https://api.binance.com")


def _binance_get(params: dict) -> list:
    err = None
    for host in BINANCE_HOSTS:
        try:
            r = requests.get(host + "/api/v3/klines", params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            err = e
    raise err


def _fetch_binance(pair: str, since_ms: int | None) -> pd.DataFrame:
    start = since_ms or int(datetime(2017, 8, 17, tzinfo=timezone.utc).timestamp() * 1000)
    rows = []
    while True:
        chunk = _binance_get({"symbol": pair, "interval": "1h", "limit": 1000, "startTime": start})
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        start = chunk[-1][0] + 3600_000
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume", "ct", "qv", "n", "tb", "tq", "i"])
    df.index = pd.to_datetime(df["t"], unit="ms", utc=True)
    return df[["open", "high", "low", "close", "volume"]].astype(float)


# ----------------------------------------------------------------------------- cache + public API
def _cache_path(sym: str, source: str):
    return CACHE_DIR / f"{source}_{sym}_H1.pkl"


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df[(df["high"] >= df["low"]) & (df["close"] > 0)]
    return df.dropna(subset=["open", "high", "low", "close"])


def server_to_utc(idx, settings=None) -> pd.DatetimeIndex:
    """MT5 bar times are the broker's SERVER clock, not UTC. Most FX brokers run "New York close" servers:
    server time = New York time + 7 h (GMT+2 in winter, GMT+3 while the US is on daylight time), so the
    week opens Monday 00:00 server time. settings.mt5_server_time: "ny_close" (default) | "utc" | hours offset."""
    mode = (settings or load_settings()).get("mt5_server_time", "ny_close")
    naive = pd.DatetimeIndex(idx).tz_localize(None) if pd.DatetimeIndex(idx).tz is not None else pd.DatetimeIndex(idx)
    if mode == "utc":
        return naive.tz_localize("UTC")
    if mode != "ny_close":
        return (naive - pd.Timedelta(hours=float(mode))).tz_localize("UTC")
    ny = (naive - pd.Timedelta(hours=7)).tz_localize("America/New_York", ambiguous=np.zeros(len(naive), bool),
                                                     nonexistent="shift_forward")
    return ny.tz_convert("UTC")


def _seed(sym: str) -> pd.DataFrame | None:
    """Long Dukascopy H1 history committed in data/history (None if not fetched yet)."""
    f = HISTORY_DIR / f"{sym}_H1.csv.gz"
    if not f.exists():
        return None
    try:
        d = pd.read_csv(f)
        d.index = server_to_utc(pd.to_datetime(d.pop("time"), unit="s"))     # exported from MT5 in broker server time
        d["volume"] = 0.0
        # brokers pad old history with DAILY bars stored as H1 (~22 a month, e.g. EURUSD 1971-1998, NAS100 2011-2020):
        # start at the first month that really has hourly bars (> 250 a month), otherwise ATR / signals mix daily and hourly
        month = d.index.year * 12 + d.index.month
        counts = pd.Series(1, index=month).groupby(level=0).size()
        dense = counts[counts > 250]
        if len(dense):
            d = d[month >= int(dense.index.min())]
        return _clean(d.astype(float))
    except Exception as e:
        log.warning("history %s unreadable: %s", sym, e)
        return None


def with_history(sym: str, live: pd.DataFrame) -> pd.DataFrame:
    """Dukascopy history + the newer free-data bars, the latter scaled onto the history's price level.

    Yahoo gold / indices are futures (contango, a few dollars/points off spot); a ratio measured on the
    overlapping bars removes that step so the joined series has no artificial jump."""
    seed = _seed(sym)
    if seed is None or seed.empty:
        return live
    if live is None or live.empty:
        return seed
    common = seed.index.intersection(live.index)[-500:]
    if len(common) >= 20:
        ratio = float(np.median(seed.close.loc[common].to_numpy() / live.close.loc[common].to_numpy()))
    else:
        prev = live.close[live.index <= seed.index[-1]]
        ratio = float(seed.close.iloc[-1] / prev.iloc[-1]) if len(prev) else 1.0
    if not np.isfinite(ratio) or abs(ratio - 1) > 0.05:      # something is off: never distort prices by more than 5%
        ratio = 1.0
    tail = live[live.index > seed.index[-1]].copy()
    for k in ("open", "high", "low", "close"):
        tail[k] = tail[k] * ratio
    return _clean(pd.concat([seed, tail]))


def source_for(sym: str, settings=None) -> str:
    settings = settings or load_settings()
    if settings["data_source"] in ("auto", "mt5") and _mt5_init(settings):
        return "mt5"
    return "binance" if "binance" in SYMBOLS[sym] else "yahoo"


def load_h1(sym: str, refresh: bool = False) -> pd.DataFrame:
    settings = load_settings()
    src = source_for(sym, settings)
    key = f"{src}:{sym}"
    with _lock:
        hit = _mem.get(key)
        if hit and not refresh and time.time() - hit[0] < REFRESH_SEC:
            return hit[1]
        path = _cache_path(sym, src)
        cached = pd.read_pickle(path) if path.exists() else None
        try:
            if cached is not None and os.environ.get("TL_OFFLINE"):   # research matrix jobs: prices were fetched once by "prefetch"
                new = None
            elif src == "mt5":
                new = _fetch_mt5(sym, settings)
            elif src == "binance":
                since = int(cached.index[-1].timestamp() * 1000) if cached is not None and len(cached) else None
                new = _fetch_binance(SYMBOLS[sym]["binance"], since)
            else:
                new = _fetch_yahoo(SYMBOLS[sym]["yahoo"])
            df = _clean(pd.concat([cached, new]) if cached is not None and new is not None else (new if new is not None else cached))
            df.to_pickle(path)
        except Exception as e:
            if cached is None:
                raise
            log.warning("refresh %s via %s failed (%s); using cache", sym, src, e)
            df = cached
        if src == "yahoo":
            df = with_history(sym, df)       # cache keeps the raw Yahoo bars; the history file is joined on read
        _mem[key] = (time.time(), df)
        return df


def resample(h1: pd.DataFrame, tf: str) -> pd.DataFrame:
    if tf == "H1":
        return h1
    rule = {"H4": "4h", "D1": "1D"}[tf]
    out = h1.resample(rule, origin="epoch", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return out.dropna(subset=["open"])


def drop_unclosed(df: pd.DataFrame, tf: str, now: datetime | None = None) -> pd.DataFrame:
    """Remove the still-forming last bar so signals only use closed candles."""
    if df.empty:
        return df
    now = now or datetime.now(timezone.utc)
    end = df.index[-1] + pd.Timedelta(minutes=TIMEFRAMES[tf])
    return df.iloc[:-1] if end > now else df


def get_bars(sym: str, tf: str = "H1", refresh: bool = False, closed_only: bool = True) -> pd.DataFrame:
    df = resample(load_h1(sym, refresh), tf)
    return drop_unclosed(df, tf) if closed_only else df


def years_of(df: pd.DataFrame) -> float:
    return max((df.index[-1] - df.index[0]).days / 365.25, 1e-9) if len(df) > 1 else 0.0


def to_unix(idx) -> np.ndarray:
    return pd.DatetimeIndex(idx).as_unit("s").asi8.astype(np.int64)
