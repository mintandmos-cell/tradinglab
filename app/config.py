"""Symbol universe, default settings and JSON persistence helpers."""
from __future__ import annotations

import json
import os
import secrets
import threading
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
RESEARCH_DIR = DATA_DIR / "research"
for _d in (DATA_DIR, CACHE_DIR, RESEARCH_DIR, RESEARCH_DIR / "history"):
    _d.mkdir(parents=True, exist_ok=True)

SETTINGS_FILE = DATA_DIR / "settings.json"      # shared research config, committed to git (the cloud uses it)
LOCAL_FILE = DATA_DIR / "local.json"            # personal / secret values, never committed
SITE_DIR = ROOT / "site"                        # static build for GitHub Pages
SIGNALS_FILE = DATA_DIR / "signals.json"
LATEST_RESEARCH = RESEARCH_DIR / "latest.json"
AI_NOTES = RESEARCH_DIR / "ai_notes.md"

TIMEFRAMES = {"H1": 60, "H4": 240, "D1": 1440}

# Free-data tickers are proxies: Yahoo FX is mid-quote, GC/NQ/ES are futures (levels differ
# slightly from broker CFDs), so signals always also carry SL/TP *distances*.
SYMBOLS: dict[str, dict] = {
    "EURUSD": {"name": "Euro / US Dollar", "group": "Forex", "yahoo": "EURUSD=X", "quote": "USD",
               "contract": 100000, "digits": 5, "pip": 0.0001, "spread": 0.00008, "slippage": 0.00002, "commission": 0.00007},
    "GBPUSD": {"name": "British Pound / US Dollar", "group": "Forex", "yahoo": "GBPUSD=X", "quote": "USD",
               "contract": 100000, "digits": 5, "pip": 0.0001, "spread": 0.00012, "slippage": 0.00002, "commission": 0.00007},
    "USDJPY": {"name": "US Dollar / Japanese Yen", "group": "Forex", "yahoo": "JPY=X", "quote": "JPY",
               "contract": 100000, "digits": 3, "pip": 0.01, "spread": 0.012, "slippage": 0.002, "commission": 0.010},
    "XAUUSD": {"name": "Gold / US Dollar", "group": "Metals", "yahoo": "GC=F", "quote": "USD",
               "contract": 100, "digits": 2, "pip": 0.1, "spread": 0.20, "slippage": 0.05, "commission": 0.0},
    "NAS100": {"name": "Nasdaq 100", "group": "Indices", "yahoo": "NQ=F", "quote": "USD",
               "contract": 1, "digits": 2, "pip": 1.0, "spread": 1.5, "slippage": 0.5, "commission": 0.0},
    "US500": {"name": "S&P 500", "group": "Indices", "yahoo": "ES=F", "quote": "USD",
              "contract": 1, "digits": 2, "pip": 1.0, "spread": 0.5, "slippage": 0.15, "commission": 0.0},
    "BTCUSD": {"name": "Bitcoin / US Dollar", "group": "Crypto", "binance": "BTCUSDT", "quote": "USD",
               "contract": 1, "digits": 2, "pip": 1.0, "spread": 15.0, "slippage": 5.0, "commission": 0.0},
    "ETHUSD": {"name": "Ethereum / US Dollar", "group": "Crypto", "binance": "ETHUSDT", "quote": "USD",
               "contract": 1, "digits": 2, "pip": 0.1, "spread": 1.2, "slippage": 0.4, "commission": 0.0},
}

DEFAULT_SETTINGS = {
    "account_balance": 5000.0,
    "account_currency": "USD",
    "risk_per_trade_pct": 0.25,         # fixed risk per signal (0 = use each system's Monte Carlo risk)
    "default_risk_pct": 0.5,
    "max_risk_pct": 1.0,
    "target_max_dd_pct": 10.0,          # Monte Carlo 95th-percentile drawdown the sizing must respect
    "data_source": "auto",              # auto | mt5 | free
    "mt5_path": r"C:\Program Files\MetaTrader 5\terminal64.exe",
    "mt5_symbol_map": {k: k for k in SYMBOLS} | {"NAS100": "USTEC"},
    "symbol_overrides": {},             # per-symbol contract/spread/commission edits from the UI
    "research": {
        "timeframes": ["H1", "H4"],
        "is_months": 12, "oos_months": 3,
        "min_is_trades": 20,
        "max_hold_hours": 120,           # never hold beyond ~5 trading days
        "schedule": "weekly",            # daily | weekly | monthly | off
        "schedule_hour_bkk": 10,
        "gates": {"min_oos_trades": 30, "min_expectancy": 0.05, "min_t": 2.0,
                  "min_mc_pf_p5": 1.0, "min_random_pct": 90.0, "min_plateau": 0.4,
                  "min_robust_score": 60.0},
    },
    "alerts": {"active": True, "watch": False},
    "force_off": [],                    # symbols vetoed by the user / AI review regardless of research
    "scan_interval_sec": 120,
    "notify": {"ntfy_topic": "tradinglab-" + secrets.token_hex(5), "ntfy_server": "https://ntfy.sh",
               "telegram_token": "", "telegram_chat_id": ""},
    "public_url": "",
    "access_pin": "",
}

# keys that belong to the person, not to the research: kept in local.json / env, never in git
LOCAL_KEYS = ("account_balance", "account_currency", "data_source", "mt5_path", "notify", "public_url", "access_pin")
ENV_OVERRIDES = {  # GitHub Actions secrets / variables
    "TL_DATA_SOURCE": ("data_source",), "TL_ACCOUNT_BALANCE": ("account_balance",), "TL_PUBLIC_URL": ("public_url",),
    "NTFY_TOPIC": ("notify", "ntfy_topic"), "NTFY_SERVER": ("notify", "ntfy_server"),
    "TELEGRAM_TOKEN": ("notify", "telegram_token"), "TELEGRAM_CHAT_ID": ("notify", "telegram_chat_id"),
}
CLOUD = bool(os.environ.get("GITHUB_ACTIONS"))

_lock = threading.RLock()


def _read_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return deepcopy(default)


def _write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, path)


def _merge(base: dict, extra: dict) -> dict:
    out = deepcopy(base)
    for k, v in (extra or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _split(s: dict) -> tuple[dict, dict]:
    return ({k: v for k, v in s.items() if k not in LOCAL_KEYS}, {k: v for k, v in s.items() if k in LOCAL_KEYS})


def load_settings() -> dict:
    with _lock:
        shared, local = _read_json(SETTINGS_FILE, {}), _read_json(LOCAL_FILE, {})
        if any(k in shared for k in LOCAL_KEYS):      # migrate the old single-file settings.json
            shared, moved = _split(shared)
            local = _merge(moved, local)
            if not CLOUD:
                _write_json(SETTINGS_FILE, shared)
                _write_json(LOCAL_FILE, local)
        s = _merge(_merge(DEFAULT_SETTINGS, shared), local)
        if CLOUD:  # free data only; notifications only from the repository secrets
            s["data_source"] = "free"
            s["notify"] = {**s["notify"], "ntfy_topic": "", "telegram_token": "", "telegram_chat_id": ""}
        for env, path in ENV_OVERRIDES.items():
            v = os.environ.get(env)
            if v:
                tgt = s
                for k in path[:-1]:
                    tgt = tgt[k]
                tgt[path[-1]] = float(v) if path[-1] == "account_balance" else v
        if not CLOUD and not SETTINGS_FILE.exists():
            sh, lo = _split(s)
            _write_json(SETTINGS_FILE, sh)
            _write_json(LOCAL_FILE, lo)
        return s


def save_settings(patch: dict) -> dict:
    with _lock:
        base = _merge(_merge(DEFAULT_SETTINGS, _read_json(SETTINGS_FILE, {})), _read_json(LOCAL_FILE, {}))
        shared, local = _split(_merge(base, patch))
        _write_json(SETTINGS_FILE, shared)
        _write_json(LOCAL_FILE, local)
        return load_settings()


def symbol_spec(sym: str, settings: dict | None = None) -> dict:
    settings = settings or load_settings()
    return {**SYMBOLS[sym], **settings.get("symbol_overrides", {}).get(sym, {}), "symbol": sym}


def read_json(path: Path, default):
    with _lock:
        return _read_json(path, default)


def write_json(path: Path, obj) -> None:
    with _lock:
        _write_json(path, obj)
