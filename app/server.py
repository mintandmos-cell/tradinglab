"""FastAPI server: JSON API + the web app.

Start:  .venv\\Scripts\\python -m uvicorn app.server:app --host 0.0.0.0 --port 8800
"""
from __future__ import annotations

import json
import logging
import math
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

import numpy as np
import pandas as pd
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import charts, data, notify, research, scanner
from .charts import clean
from .config import AI_NOTES, DATA_DIR, ROOT, SYMBOLS, TIMEFRAMES, load_settings, save_settings, symbol_spec
from .engine import run
from .metrics import equity_points, summarize
from .montecarlo import run_mc
from .sizing import lots_for
from .strategies import STRATEGIES, normalize, public_list
from .walkforward import random_benchmark, walk_forward

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(), RotatingFileHandler(DATA_DIR / "server.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")])
log = logging.getLogger("tradinglab.server")
WEB = ROOT / "web"
_stop = threading.Event()


@asynccontextmanager
async def lifespan(_app):
    load_settings()
    t = threading.Thread(target=scanner.loop, args=(_stop,), daemon=True, name="scanner")
    t.start()
    yield
    _stop.set()


app = FastAPI(title="TradingLab", lifespan=lifespan)


def ok(o) -> JSONResponse:
    return JSONResponse(clean(o))


@app.middleware("http")
async def pin_guard(request: Request, call_next):
    pin = load_settings().get("access_pin") or ""
    if pin and request.url.path.startswith("/api/") and request.headers.get("x-pin") != pin:
        return JSONResponse({"detail": "PIN required"}, status_code=401)
    return await call_next(request)


def _check(sym: str, tf: str | None = None, strategy: str | None = None):
    if sym not in SYMBOLS:
        raise HTTPException(400, f"unknown symbol {sym}")
    if tf and tf not in TIMEFRAMES:
        raise HTTPException(400, f"unknown timeframe {tf}")
    if strategy and strategy not in STRATEGIES:
        raise HTTPException(400, f"unknown strategy {strategy}")


def _slice(df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    if start:
        df = df[df.index >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df.index < pd.Timestamp(end, tz="UTC")]
    return df


_trade_out = charts.trade_out


def _yearly(trades: list[dict]) -> list[dict]:
    if not trades:
        return []
    df = pd.DataFrame({"y": [pd.Timestamp(t["exit_time"]).year for t in trades], "r": [t["r"] for t in trades]})
    g = df.groupby("y")["r"]
    return [{"year": int(y), "trades": int(n), "net_r": round(float(s), 2), "win": round(float(w) * 100, 1)}
            for y, n, s, w in zip(g.size().index, g.size(), g.sum(), g.apply(lambda x: (x > 0).mean()))]


# ----------------------------------------------------------------------------- status & metadata
@app.get("/api/status")
def status():
    s = load_settings()
    lat = research.latest()
    return ok({"server_time": datetime.now(timezone.utc), "mt5": data.mt5_status(), "scanner": scanner.STATE,
               "research": research.STATE, "research_generated": lat.get("generated"),
               "schedule": s["research"]["schedule"], "ntfy_topic": s["notify"]["ntfy_topic"],
               "telegram": bool(s["notify"].get("telegram_token"))})


@app.get("/api/symbols")
def symbols():
    return ok(research.symbol_table())


@app.get("/api/strategies")
def strategies():
    return ok(public_list())


# ----------------------------------------------------------------------------- charts
@app.get("/api/candles")
def candles(symbol: str, tf: str = "H1", limit: int = 1500, strategy: str | None = None, params: str | None = None,
            system: bool = True):
    _check(symbol, tf, strategy)
    cfg = {"strategy": strategy, "params": normalize(strategy, json.loads(params) if params else None)} if strategy else None
    return ok(charts.candles(symbol, tf, limit, cfg, system))


@app.get("/api/candles_year")
def candles_year(symbol: str, tf: str = "H1", year: int = 2020, before: int | None = None):
    _check(symbol, tf)
    return ok(charts.candles_year(symbol, tf, year, before))


@app.get("/api/system")
def system(symbol: str, tf: str = "H1", strategy: str = "", params: str | None = None, limit: int = 1500):
    """One strategy on the chart page (view-only: never changes the live system or alerts)."""
    _check(symbol, tf, strategy)
    return ok(charts.system_payload(symbol, tf, strategy, json.loads(params) if params else None, limit))


# ----------------------------------------------------------------------------- research tools
@app.post("/api/backtest")
def backtest(body: dict = Body(...)):
    sym, tf, sid = body.get("symbol"), body.get("tf", "H1"), body.get("strategy")
    _check(sym, tf, sid)
    params = normalize(sid, body.get("params"))
    spec = {**symbol_spec(sym), **{k: float(v) for k, v in (body.get("costs") or {}).items() if v is not None}}
    df = _slice(data.get_bars(sym, tf), body.get("start"), body.get("end"))
    if len(df) < 250:
        raise HTTPException(400, "ข้อมูลน้อยเกินไปในช่วงที่เลือก")
    risk = float(body.get("risk_pct", 1.0))
    res = run(df, sid, params, spec, tf)
    tr = res["trades"]
    r = [t["r"] for t in tr]
    yrs = data.years_of(df)
    m = summarize(r, yrs, risk, bars=[t["bars"] for t in tr], cost_r=[t["cost_r"] for t in tr])
    m["long"] = summarize([t["r"] for t in tr if t["dir"] > 0], yrs, risk)
    m["short"] = summarize([t["r"] for t in tr if t["dir"] < 0], yrs, risk)
    times = data.to_unix([t["exit_time"] for t in tr])
    bal = float(body.get("balance") or load_settings()["account_balance"])
    return ok({"symbol": sym, "tf": tf, "strategy": sid, "params": params, "costs": {k: spec[k] for k in ("spread", "slippage", "commission")},
               "from": df.index[0], "to": df.index[-1], "years": round(yrs, 2), "metrics": m,
               "equity": equity_points(times, r, risk, bal), "r": [round(x, 4) for x in r], "yearly": _yearly(tr),
               "exit_mix": pd.Series([t["reason"] for t in tr]).value_counts().to_dict() if tr else {},
               "trades": [_trade_out(t, spec["digits"]) for t in tr][-400:][::-1]})


@app.post("/api/walkforward")
def walkforward_api(body: dict = Body(...)):
    sym, tf, sid = body.get("symbol"), body.get("tf", "H1"), body.get("strategy")
    _check(sym, tf, sid)
    s = load_settings()
    cfg = {**s["research"], **{k: body[k] for k in ("is_months", "oos_months", "min_is_trades") if body.get(k)}}
    cfg["max_hold_hours"] = int(body.get("max_hold_h") or cfg["max_hold_hours"])
    spec = symbol_spec(sym, s)
    df = data.get_bars(sym, tf)
    wf = walk_forward(df, sid, spec, tf, cfg)
    rb = random_benchmark(df, wf, spec, tf) if body.get("benchmark", True) else None
    r = [t["r"] for t in wf["oos_trades"]]
    times = data.to_unix([t["exit_time"] for t in wf["oos_trades"]])
    out = {k: v for k, v in wf.items() if k != "oos_trades"}
    out.update(symbol=sym, random_benchmark=rb, r=[round(x, 4) for x in r], equity=equity_points(times, r, float(body.get("risk_pct", 1.0))),
               trades=[{**_trade_out(t, spec["digits"]), "fold": t["fold"]} for t in wf["oos_trades"]][::-1][:400],
               yearly=_yearly(wf["oos_trades"]), is_months=cfg["is_months"], oos_months=cfg["oos_months"])
    return ok(out)


@app.post("/api/montecarlo")
def montecarlo_api(body: dict = Body(...)):
    r = [float(x) for x in body.get("r", []) if x is not None]
    s = load_settings()
    return ok(run_mc(r, float(body.get("risk_pct", 1.0)), int(body["n_trades"]) if body.get("n_trades") else None,
                     int(min(max(int(body.get("sims", 5000)), 200), 20000)), body.get("method", "bootstrap"),
                     float(body.get("dd_limit_pct", s["target_max_dd_pct"])), float(body.get("ruin_pct", 30))))


@app.get("/api/size")
def size(symbol: str, entry: float, sl: float, risk_pct: float, balance: float | None = None, tp: float | None = None):
    _check(symbol)
    s = load_settings()
    info = data.mt5_symbol_info(symbol)
    res = lots_for(symbol, entry, sl, float(balance or s["account_balance"]), risk_pct, info, s)
    spec = symbol_spec(symbol, s)
    dist = abs(entry - sl)
    res.update(sl_pips=round(dist / spec["pip"], 1), mt5=info)
    if tp:
        res.update(rr=round(abs(tp - entry) / dist, 2) if dist else None, tp_pips=round(abs(tp - entry) / spec["pip"], 1),
                   reward_usd=round(res["lots"] * abs(tp - entry) * res.get("value_per_point_per_lot", 0), 2))
    return ok(res)


# ----------------------------------------------------------------------------- research pipeline
@app.get("/api/research")
def research_latest():
    return ok(research.latest())


@app.get("/api/research/state")
def research_state():
    return ok(research.STATE)


@app.post("/api/research/run")
def research_run(body: dict = Body(default={})):
    syms = [x for x in (body.get("symbols") or []) if x in SYMBOLS] or None
    started = research.run_in_background(syms)
    return ok({"started": started, "state": research.STATE})


@app.get("/api/research/history")
def research_history():
    return ok(research.history())


@app.get("/api/research/notes", response_class=PlainTextResponse)
def research_notes():
    return AI_NOTES.read_text(encoding="utf-8") if AI_NOTES.exists() else ""


# ----------------------------------------------------------------------------- signals
@app.get("/api/signals")
def signals_list(limit: int = 200):
    return ok(scanner.load_signals()[:limit])


@app.post("/api/signals/{sig_id}")
def signals_update(sig_id: str, body: dict = Body(...)):
    s = scanner.update_signal(sig_id, body)
    if not s:
        raise HTTPException(404, "signal not found")
    return ok(s)


@app.post("/api/scan")
def scan_now():
    return ok({"new": scanner.scan_once()})


@app.get("/api/forward")
def forward_stats():
    return ok(scanner.forward_summary())


# ----------------------------------------------------------------------------- settings
@app.get("/api/settings")
def settings_get():
    return ok(load_settings())


@app.post("/api/settings")
def settings_set(body: dict = Body(...)):
    body.pop("access_pin_confirm", None)
    return ok(save_settings(body))


@app.post("/api/notify/test")
def notify_test():
    return ok(notify.send("TradingLab ทดสอบการแจ้งเตือน", "ถ้าเห็นข้อความนี้ แปลว่าการแจ้งเตือนบนมือถือทำงานแล้ว ✅", priority=5, tags=["white_check_mark"]))


# ----------------------------------------------------------------------------- web app
app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/config.js")
def config_js():  # the static (GitHub Pages) build ships its own config.js; the local server is the live API
    return PlainTextResponse("window.TL_STATIC = null;", media_type="application/javascript")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(WEB / "manifest.webmanifest", media_type="application/manifest+json")
