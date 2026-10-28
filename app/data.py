"""Market data: MetaTrader 5 (broker-exact) when available, else Yahoo Finance / Binance.

All bars are UTC, columns open/high/low/close/volume, indexed by bar open time.
H1 is the base series; H4 and D1 are resampled from it so every source behaves the same.
History is cached on disk and merged on refresh, so free-data history grows over time.
"""
from __future__ import annotations

import json
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


# the same market under other brokers' names (Exness: USTEC / US500, The5ers: NAS100 / SP500, ...)
SYMBOL_ALIASES = [
    ["NAS100", "USTEC", "US100", "NDX100", "NQ100", "USTECH", "NAS100USD", "USTEC100"],
    ["US500", "SP500", "SPX500", "US500USD", "SPX", "USA500"],
    ["XAUUSD", "GOLD"],
    ["BTCUSD", "BITCOIN"],
    ["ETHUSD", "ETHEREUM"],
]
_resolved: dict[str, str] = {}


def mt5_resolve(name: str) -> str | None:
    """Broker symbol name for a mapped name: exact, then the same market's other names (NAS100 -> USTEC on Exness),
    each also with a broker suffix (XAUUSDm, US500.cash, USTEC.r ...). Shortest match wins."""
    import MetaTrader5 as mt5
    if name in _resolved and mt5.symbol_info(_resolved[name]) is not None:
        return _resolved[name]
    family = next((f for f in SYMBOL_ALIASES if name.upper() in f), [])
    tries = [name] + [a for a in family if a != name.upper()]
    for t in tries:
        if mt5.symbol_info(t) is not None:
            _resolved[name] = t
            return t
    for t in tries:
        cands = sorted((x.name for x in (mt5.symbols_get(f"{t}*") or []) if x.name.upper().startswith(t.upper())), key=len)
        if cands:
            _resolved[name] = cands[0]
            log.info("MT5 symbol %s -> %s", name, cands[0])
            return cands[0]
    return None


def _fetch_mt5(sym: str, settings) -> pd.DataFrame | None:
    import MetaTrader5 as mt5
    name = mt5_resolve(settings["mt5_symbol_map"].get(sym, sym))
    if name is None:
        return None
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


_MODE_CACHE = [0.0, "ny_close"]


def _server_mode(settings=None) -> str:
    if settings is not None:
        return settings.get("mt5_server_time", "ny_close")
    if time.time() - _MODE_CACHE[0] > 60:          # read settings at most once a minute (called in tight loops)
        _MODE_CACHE[:] = [time.time(), load_settings().get("mt5_server_time", "ny_close")]
    return _MODE_CACHE[1]


def server_to_utc(idx, settings=None) -> pd.DatetimeIndex:
    """MT5 bar times are the broker's SERVER clock, not UTC. Most FX brokers run "New York close" servers:
    server time = New York time + 7 h (GMT+2 in winter, GMT+3 while the US is on daylight time), so the
    week opens Monday 00:00 server time. settings.mt5_server_time: "ny_close" (default) | "utc" | hours offset."""
    mode = _server_mode(settings)
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


# ---------------------------------------------------------------- Twelve Data (spot FX / gold) — user's request 27 Oct 2026
# Yahoo gold is a futures contract (a few dollars off the broker's spot price) and its FX bars are rough, so with the
# repository secret TWELVE_DATA_KEY the newest H1 bars of these symbols come from Twelve Data's spot feed instead —
# the same feed the chart page shows. Free plan: 800 requests a day, so each symbol is fetched at most once per hour.
TWELVE = {"EURUSD": "EUR/USD", "GBPUSD": "GBP/USD", "USDJPY": "USD/JPY", "XAUUSD": "XAU/USD"}
PRICE_SOURCE: dict[str, str] = {}


def _twelve_key() -> str:
    return os.environ.get("TWELVE_DATA_KEY", "").strip()


def twelve_h1(sym: str) -> pd.DataFrame | None:
    if sym not in TWELVE:
        return None
    path = CACHE_DIR / f"twelve_{sym}_H1.pkl"
    meta = CACHE_DIR / f"twelve_{sym}_H1.json"
    cached = pd.read_pickle(path) if path.exists() else None
    key = _twelve_key()
    if not key or os.environ.get("TL_OFFLINE"):
        return cached
    try:
        last = json.loads(meta.read_text()).get("fetched") if meta.exists() else None
    except Exception:
        last = None
    now = datetime.now(timezone.utc)
    if cached is not None and last and pd.Timestamp(last).floor("h") >= pd.Timestamp(now).floor("h"):
        return cached                                    # already fetched after the latest hourly close
    try:
        r = requests.get("https://api.twelvedata.com/time_series", timeout=30, params={
            "symbol": TWELVE[sym], "interval": "1h", "timezone": "UTC", "apikey": key,
            "outputsize": 5000 if cached is None or cached.empty else 80})
        j = r.json()
        if j.get("status") == "error" or "values" not in j:
            raise RuntimeError(j.get("message") or "no data")
        d = pd.DataFrame(j["values"])
        d.index = pd.to_datetime(d.pop("datetime"), utc=True)
        d = d[["open", "high", "low", "close"]].astype(float)
        d["volume"] = 0.0
        d = d[d.high > d.low]                            # flat bars = market closed (weekends, holidays)
        df = _clean(pd.concat([cached, d]) if cached is not None else d)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        df.to_pickle(path)
        meta.write_text(json.dumps({"fetched": now.isoformat()}))
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::notice title=Twelve Data::{sym} ok — {len(d)} bars fetched, {d.index.min():%Y-%m-%d} → {d.index.max():%Y-%m-%d %H:%M} UTC", flush=True)
        return df
    except Exception as e:
        log.warning("Twelve Data %s failed (%s); using %s", sym, e, "cache" if cached is not None else "Yahoo")
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::warning title=Twelve Data::{sym} failed ({e}) — using {'cache' if cached is not None else 'Yahoo'}", flush=True)
        return cached


def with_twelve(sym: str, df: pd.DataFrame) -> pd.DataFrame:
    """MT5 history (+ Yahoo where needed) with the Twelve Data spot bars as the newest part."""
    tw = twelve_h1(sym)
    if tw is None or tw.empty:
        return df
    seed = _seed(sym)
    ratio = 1.0
    if seed is not None and not seed.empty:
        common = seed.index.intersection(tw.index)[-500:]
        if len(common) >= 20:
            ratio = float(np.median(seed.close.loc[common].to_numpy() / tw.close.loc[common].to_numpy()))
            if not np.isfinite(ratio) or abs(ratio - 1) > 0.02:     # spot vs broker spot: should be ~1
                ratio = 1.0
    tw = tw.copy()
    for k in ("open", "high", "low", "close"):
        tw[k] = tw[k] * ratio
    cut = seed.index[-1] if seed is not None and not seed.empty and seed.index[-1] >= tw.index[0] else tw.index[0] - pd.Timedelta(seconds=1)
    PRICE_SOURCE[sym] = "twelve"
    return _clean(pd.concat([df[df.index <= cut], tw[tw.index > cut]]))


def price_source(sym: str, settings=None) -> str:
    """Where the newest bars of a symbol come from (shown on signals / chart)."""
    src = source_for(sym, settings)
    return PRICE_SOURCE.get(sym, src) if src == "yahoo" else src



def source_for(sym: str, settings=None) -> str:
    settings = settings or load_settings()
    if settings["data_source"] in ("auto", "mt5") and _mt5_init(settings):
        return "mt5"
    return "binance" if "binance" in SYMBOLS[sym] else "yahoo"


def market_hours(sym: str, df: pd.DataFrame, settings=None) -> pd.DataFrame:
    """FX / gold / indices trade Monday 00:00 – Friday 24:00 on the broker clock (like MT5). Free feeds sometimes print
    flat 'bars' on Saturday / Sunday: drop them so charts, indicators and backtests never see a fake weekend."""
    if SYMBOLS.get(sym, {}).get("group") == "Crypto" or df is None or df.empty:
        return df
    wd = utc_to_server(df.index, settings).dayofweek
    return df[wd < 5]


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
            df = with_twelve(sym, df)        # spot FX / gold from Twelve Data when the key is configured
        df = market_hours(sym, df, settings)
        _mem[key] = (time.time(), df)
        return df


def utc_to_server(idx, settings=None) -> pd.DatetimeIndex:
    """Inverse of server_to_utc: UTC → naive broker server time (NY close = New York + 7 h)."""
    mode = _server_mode(settings)
    idx = pd.DatetimeIndex(idx)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    if mode == "utc":
        return idx.tz_localize(None)
    if mode != "ny_close":
        return idx.tz_localize(None) + pd.Timedelta(hours=float(mode))
    return idx.tz_convert("America/New_York").tz_localize(None) + pd.Timedelta(hours=7)


def period_start(ts, months: int = 3, settings=None) -> pd.Timestamp:
    """Start of the calendar period (quarter for months=3) containing ts, on the BROKER clock, returned in UTC.
    Walk-forward windows and the research cut-off use these boundaries: 1 Jan / 1 Apr / 1 Jul / 1 Oct 00:00 server time."""
    srv = utc_to_server(pd.DatetimeIndex([pd.Timestamp(ts)]), settings)[0]
    mi = (srv.year * 12 + srv.month - 1) // months * months
    return server_to_utc(pd.DatetimeIndex([pd.Timestamp(year=mi // 12, month=mi % 12 + 1, day=1)]), settings)[0]


def add_months(ts_utc, months: int, settings=None) -> pd.Timestamp:
    """ts (a period boundary in UTC) + n calendar months on the broker clock."""
    srv = utc_to_server(pd.DatetimeIndex([pd.Timestamp(ts_utc)]), settings)[0]
    return server_to_utc(pd.DatetimeIndex([srv + pd.DateOffset(months=months)]), settings)[0]


def resample(h1: pd.DataFrame, tf: str, settings=None) -> pd.DataFrame:
    """H4 / D1 candles cut on the BROKER clock (like MT5: H4 = 00/04/08… server time, a day = New York close),
    so the shapes match the user's MT5 chart. Labels are returned in UTC."""
    if tf == "H1" or h1.empty:
        return h1
    rule = {"H4": "4h", "D1": "1D"}[tf]
    srv = h1.copy()
    srv.index = utc_to_server(h1.index, settings)
    out = srv.resample(rule, origin="epoch", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(subset=["open"])
    out.index = server_to_utc(out.index, settings)
    return _clean(out) if out.index.has_duplicates else out


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
