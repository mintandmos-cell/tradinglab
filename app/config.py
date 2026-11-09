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
HISTORY_DIR = DATA_DIR / "history"                # long H1 history (Dukascopy), committed; see tools/fetch_history.js
SITE_DIR = ROOT / "site"                        # static build for GitHub Pages
SIGNALS_FILE = DATA_DIR / "signals.json"
LATEST_RESEARCH = RESEARCH_DIR / "latest.json"
AI_NOTES = RESEARCH_DIR / "ai_notes.md"

TIMEFRAMES = {"H1": 60, "H4": 240, "D1": 1440}

# Costs = the user's broker, Exness Pro (no commission; spreads from the PC's symbol list 2 Nov 2026, rounded up).
# Free-data tickers are proxies: Yahoo FX is mid-quote, GC/NQ/ES are futures (levels differ
# slightly from broker CFDs), so signals always also carry SL/TP *distances*.
SYMBOLS: dict[str, dict] = {
    "EURUSD": {"name": "Euro / US Dollar", "group": "Forex", "yahoo": "EURUSD=X", "quote": "USD",
               "contract": 100000, "digits": 5, "pip": 0.0001, "spread": 0.00007, "slippage": 0.00002, "commission": 0.0},
    "GBPUSD": {"name": "British Pound / US Dollar", "group": "Forex", "yahoo": "GBPUSD=X", "quote": "USD",
               "contract": 100000, "digits": 5, "pip": 0.0001, "spread": 0.00009, "slippage": 0.00002, "commission": 0.0},
    "USDJPY": {"name": "US Dollar / Japanese Yen", "group": "Forex", "yahoo": "JPY=X", "quote": "JPY",
               "contract": 100000, "digits": 3, "pip": 0.01, "spread": 0.009, "slippage": 0.002, "commission": 0.0},
    "XAUUSD": {"name": "Gold / US Dollar", "group": "Metals", "yahoo": "GC=F", "quote": "USD",
               "contract": 100, "digits": 2, "pip": 0.1, "spread": 0.18, "slippage": 0.05, "commission": 0.0},
    "NAS100": {"name": "Nasdaq 100", "group": "Indices", "yahoo": "NQ=F", "quote": "USD",
               "contract": 1, "digits": 2, "pip": 1.0, "spread": 1.0, "slippage": 0.5, "commission": 0.0},
    "US500": {"name": "S&P 500", "group": "Indices", "yahoo": "ES=F", "quote": "USD",
              "contract": 1, "digits": 2, "pip": 1.0, "spread": 0.35, "slippage": 0.15, "commission": 0.0},
    "BTCUSD": {"name": "Bitcoin / US Dollar", "group": "Crypto", "binance": "BTCUSDT", "quote": "USD",
               "contract": 1, "digits": 2, "pip": 1.0, "spread": 6.0, "slippage": 5.0, "commission": 0.0},
    "ETHUSD": {"name": "Ethereum / US Dollar", "group": "Crypto", "binance": "ETHUSDT", "quote": "USD",
               "contract": 1, "digits": 2, "pip": 0.1, "spread": 0.8, "slippage": 0.4, "commission": 0.0},
    # --- universe added 2 Nov 2026 (user: Exness, FX + US stocks + crypto + gold/oil/indices). Contract sizes from the PC's
    # Exness symbol list (data/exness/symbols.json). Exness disables SOL / XRP / LTC (only BTC / ETH crypto are tradable on
    # the user's account), so silver, DAX and Nikkei take those 3 places.
    "GBPJPY": {"name": "British Pound / Japanese Yen", "group": "Forex", "yahoo": "GBPJPY=X", "quote": "JPY",
               "contract": 100000, "digits": 3, "pip": 0.01, "spread": 0.025, "slippage": 0.005, "commission": 0.0},
    "AUDUSD": {"name": "Australian Dollar / US Dollar", "group": "Forex", "yahoo": "AUDUSD=X", "quote": "USD",
               "contract": 100000, "digits": 5, "pip": 0.0001, "spread": 0.00012, "slippage": 0.00002, "commission": 0.0},
    "USDCAD": {"name": "US Dollar / Canadian Dollar", "group": "Forex", "yahoo": "CAD=X", "quote": "CAD",
               "contract": 100000, "digits": 5, "pip": 0.0001, "spread": 0.00018, "slippage": 0.00002, "commission": 0.0},
    "NVDA": {"name": "NVIDIA", "group": "Stocks", "yahoo": "NVDA", "quote": "USD",
             "contract": 100, "digits": 2, "pip": 0.01, "spread": 0.10, "slippage": 0.03, "commission": 0.0},
    "MSFT": {"name": "Microsoft", "group": "Stocks", "yahoo": "MSFT", "quote": "USD",
             "contract": 100, "digits": 2, "pip": 0.01, "spread": 0.20, "slippage": 0.05, "commission": 0.0},
    "AMZN": {"name": "Amazon", "group": "Stocks", "yahoo": "AMZN", "quote": "USD",
             "contract": 100, "digits": 2, "pip": 0.01, "spread": 0.12, "slippage": 0.03, "commission": 0.0},
    "META": {"name": "Meta Platforms", "group": "Stocks", "yahoo": "META", "quote": "USD",
             "contract": 100, "digits": 2, "pip": 0.01, "spread": 0.35, "slippage": 0.08, "commission": 0.0},
    "AAPL": {"name": "Apple", "group": "Stocks", "yahoo": "AAPL", "quote": "USD",
             "contract": 100, "digits": 2, "pip": 0.01, "spread": 0.12, "slippage": 0.03, "commission": 0.0},
    "XAGUSD": {"name": "Silver / US Dollar", "group": "Metals", "yahoo": "SI=F", "quote": "USD",
               "contract": 5000, "digits": 3, "pip": 0.01, "spread": 0.020, "slippage": 0.005, "commission": 0.0},
    "DE30": {"name": "Germany 40 (DAX)", "group": "Indices", "yahoo": "^GDAXI", "quote": "EUR",
             "contract": 1, "digits": 2, "pip": 1.0, "spread": 1.2, "slippage": 0.4, "commission": 0.0},
    "JP225": {"name": "Japan 225 (Nikkei)", "group": "Indices", "yahoo": "^N225", "quote": "JPY",
              "contract": 1, "digits": 2, "pip": 1.0, "spread": 8.0, "slippage": 2.0, "commission": 0.0},
    "USOIL": {"name": "WTI Crude Oil", "group": "Energy", "yahoo": "CL=F", "quote": "USD",
              "contract": 1000, "digits": 3, "pip": 0.01, "spread": 0.03, "slippage": 0.01, "commission": 0.0},
}

DEFAULT_SETTINGS = {
    "account_balance": 5000.0,
    "account_currency": "USD",
    "risk_per_trade_pct": 0.5,          # fixed risk per signal, every asset alike (user's choice 2 Nov 2026; was 0.25)
    "portfolio_risk_pct": 0.5,          # portfolio systems risk the same as single ones (user, 2 Nov 2026: equal risk per asset)
    "max_symbol_risk_pct": 1.0,         # cap on the open risk of ALL systems of one symbol (user, 2 Nov 2026: max 1 % per asset)
    "default_risk_pct": 0.5,
    "max_risk_pct": 1.0,
    "target_max_dd_pct": 10.0,          # Monte Carlo 95th-percentile drawdown the sizing must respect
    "data_source": "auto",              # auto | mt5 | free
    "mt5_server_time": "utc",           # broker server clock: utc (Exness = GMT+0, user 4 Nov 2026) | ny_close (GMT+2/+3) | hours
    "history_server_time": "ny_close",  # clock of the old MT5 export files in data/history (not the Exness ones, those are UTC)
    "mt5_path": r"C:\Program Files\MetaTrader 5\terminal64.exe",
    "mt5_symbol_map": {k: k for k in SYMBOLS} | {"NAS100": "USTEC"},
    "symbol_overrides": {},             # per-symbol contract/spread/commission edits from the UI
    "research": {
        "timeframes": ["H1", "H4", "D1"],
        "is_mode": "expanding", "is_months": 24, "oos_months": 3,   # 3 Nov 2026: all history before each fold (>= 24 m)
        "plateau_weight": 0.5, "switch_margin": 0.5,                # pick on a plateau; switch sets only when clearly better
        # per-TF walk-forward windows (user, 3 Nov 2026): rolling in-sample, 1-year OOS folds counted back from the cut-off
        "wfa": {"H1": {"is_mode": "rolling", "is_months": 24, "oos_months": 12},
                "H4": {"is_mode": "rolling", "is_months": 36, "oos_months": 12},
                "D1": {"is_mode": "rolling", "is_months": 36, "oos_months": 12}},
        "min_is_trades": 20,
        "max_hold_hours": 120,           # only used when exit_max_hold is on
        "exit_opposite_signal": True,    # user's rule (Oct 2026): exits = TP / SL / opposite signal only
        "exit_max_hold": False,
        "exit_friday_close": False,
        "schedule": "yearly",            # yearly (1 Jan, the research cut-off; user 9 Nov 2026) | quarterly | weekly | monthly | daily | off
        "period_months": 12,             # user, 9 Nov 2026: research + live parameter set ONCE A YEAR (1 Jan), locked 12 months —
                                         # the same rhythm as the 12-month walk-forward OOS folds the systems are judged on
        "cutoff_floor": "2026-10-01T00:00:00+00:00",   # switch-over: the Oct 2026 research stays the cut-off until 1 Jan 2027
        "schedule_hour_bkk": 10,
        "gates": {"min_oos_trades": 30, "min_expectancy": 0.05, "min_t": 2.0,
                  "min_mc_pf_p5": 1.0, "min_random_pct": 90.0, "min_plateau": 0.4,
                  "min_robust_score": 60.0,
                  "multiple_testing": True},   # OOS t must also beat the Sidak-adjusted threshold for the number of candidates compared
        "effective_trials": True,
        "swap_mode": "free",             # free (the account books no swap, 3 Nov 2026) | charged
        "noise_sims": 30,                # robustness noise test runs (was 10; user, 3 Nov 2026)
        "holdout_months": 12,            # sealed final exam: the last 12 months before the cut-off are judged on their own
        "t_basis": "monthly",            # t of the monthly OOS R (trades in a cluster are not independent) | trade        # count correlated candidates as fewer independent tests (Li & Ji eigenvalues; 2 Nov 2026)
    },
    # 2 Nov 2026 (user: "do everything"): risk shared by markets that move together, a pre-agreed drawdown stop, and
    # near-miss systems traded on the DEMO account only at a small risk to collect live evidence
    "cluster_risk_pct": {"default": 1.5, "metals": 1.0, "energy": 1.0, "crypto": 1.0, "jpy_cross": 1.0},
    "kill_dd_mult": 2.0,
    # TRIAL -> ACTIVE by the forward test (user, 9 Nov 2026; fixed before any result — app/promotion.py)
    "promotion": {"enabled": True, "min_trades": 30, "min_months": 3, "min_exp_r": 0.05, "alpha": 0.05},
    "trials": {"enabled": True, "risk_pct": 0.1, "max_per_symbol": 2},
    "alerts": {"active": True, "watch": False},
    "force_off": [],                    # vetoed by the user / AI review: "XAUUSD" (whole symbol) or "XAUUSD:ichimoku_tk:H1" (one system)
    "scan_interval_sec": 120,
    "notify": {"ntfy_topic": "tradinglab-" + secrets.token_hex(5), "ntfy_server": "https://ntfy.sh",
               "telegram_token": "", "telegram_chat_id": ""},
    "public_url": "",
    "access_pin": "",
}

# keys that belong to the person, not to the research: kept in local.json / env, never in git
LOCAL_KEYS = ("account_balance", "account_currency", "data_source", "mt5_path", "notify", "public_url", "access_pin", "autotrade", "github_token")
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


BROKER_COSTS = DATA_DIR / "exness" / "costs.json"
_costs_cache: dict = {"mtime": None, "data": {}}
# MetaTrader SYMBOL_SWAP_MODE: 1 points · 2 base currency · 3 margin currency · 4 deposit currency (money per lot)
#                              5 interest on current price · 6 interest on open price (annual %)


def broker_costs(sym: str) -> dict:
    """The broker's real costs (data/exness/costs.json, exported by the PC robot): spread = median spread of the H1 bars
    of ~3 years (price units), swaps converted to price units per night (negative = the trader pays)."""
    try:
        m = BROKER_COSTS.stat().st_mtime
    except OSError:
        return {}
    if _costs_cache["mtime"] != m:
        _costs_cache.update(mtime=m, data=_read_json(BROKER_COSTS, {}).get("symbols", {}))
    x = _costs_cache["data"].get(sym) or {}
    if not x or x.get("error"):
        return {}
    pt = float(x.get("point") or 0)
    out = {}
    # MetaTrader stores one spread per bar (close to the bar's lowest): the 75th percentile is the conservative choice
    sp = x.get("spread_p75") or x.get("spread_median")
    if sp and pt:
        out["spread"] = float(sp) * pt
    mode = int(x.get("swap_mode") or 1)
    price = float(x.get("price") or 0)
    tv, ts = float(x.get("tick_value") or 0), float(x.get("tick_size") or 0)
    money_per_price = tv / ts if tv and ts else 0.0                 # account money of a 1.0 price move for 1 lot
    for side in ("long", "short"):
        v = float(x.get(f"swap_{side}") or 0)
        if mode == 1:
            px = v * pt
        elif mode in (2, 3, 4):
            px = v / money_per_price if money_per_price else 0.0
        elif mode in (5, 6):
            px = price * v / 100 / 360 if price else 0.0
        else:
            px = 0.0
        out[f"swap_{side}"] = px
    out["swap_3day"] = int(x.get("swap_3day", 3)) - 1 if x.get("swap_3day") is not None else 2   # MT5: 0 = Sunday
    byh = x.get("spread_by_hour_p75")
    if byh and pt:
        out["spread_by_hour"] = [float(v) * pt if v is not None else None for v in byh]
    return out


def symbol_spec(sym: str, settings: dict | None = None) -> dict:
    settings = settings or load_settings()
    # the broker's MEASURED costs win over hand-set estimates (user, 2 Nov 2026: test on the broker's own costs)
    spec = {**SYMBOLS[sym], **settings.get("symbol_overrides", {}).get(sym, {}), **broker_costs(sym), "symbol": sym}
    # user, 3 Nov 2026: the Exness Pro account books NO swap (checked on MT5 deals) -> research runs swap-free by default;
    # "charged" puts the quoted swaps back (comparison runs / if the robot's daily swap check starts seeing charges)
    if swap_mode(settings, sym) == "free":
        spec["swap_long"] = spec["swap_short"] = 0.0
    # user, 3 Nov 2026: slippage from the robot's real fills once there are enough (app/fills.py), else the estimate
    from .fills import measured_slippage
    slip = measured_slippage(sym)
    if slip is not None:
        spec["slippage"] = slip
        spec["slippage_source"] = "fills"
    return spec


SWAP_FLAG = DATA_DIR / "exness" / "swap_charged.json"   # written by the PC robot the first day MT5 books a swap


def swap_mode(settings: dict | None = None, sym: str | None = None) -> str:
    """'free' | 'charged' for one symbol. Env TL_SWAP_MODE wins (comparison runs); then the PC's flag file
    data/exness/swap_charged.json: the symbols MT5 really booked a swap on, and their asset groups (9 Nov 2026: Exness books
    swap on US stocks, not on the FX / metals / crypto of this account); then settings research.swap_mode."""
    env = os.environ.get("TL_SWAP_MODE", "").strip().lower()
    if env in ("free", "charged"):
        return env
    if SWAP_FLAG.exists():
        flag = _read_json(SWAP_FLAG, {})
        syms = set(flag.get("symbols") or [])
        groups = set(flag.get("groups") or [])
        if not syms and not groups:               # old flag (3-5 Nov 2026): only examples, by MT5 name
            syms = {e.get("symbol") for e in flag.get("examples", [])}
            groups = {SYMBOLS[x]["group"] for x in syms if x in SYMBOLS}
        if sym is None:
            return "charged" if syms or groups else "free"
        if sym in syms or SYMBOLS.get(sym, {}).get("group") in groups:
            return "charged"
        return "free"
    settings = settings or load_settings()
    return settings.get("research", {}).get("swap_mode", "free")


def read_json(path: Path, default):
    with _lock:
        return _read_json(path, default)


def write_json(path: Path, obj) -> None:
    with _lock:
        _write_json(path, obj)
