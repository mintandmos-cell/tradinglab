"""Build the static web app (GitHub Pages): the same front-end, reading pre-computed JSON instead of the API.

  python -m app.export            -> site/
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import datetime, timezone

from . import charts, data, research, scanner
from .charts import clean
from .config import AI_NOTES, CLOUD, DATA_DIR, ROOT, SITE_DIR, SYMBOLS, load_settings, read_json
from .strategies import STRATEGIES, public_list

log = logging.getLogger("tradinglab.export")
CLOUD_STATE = DATA_DIR / "cloud_status.json"   # committed by the bot (cloud_state.json was git-ignored on PCs)
# settings the browser may show; never secrets or the account balance
PUBLIC_SETTINGS = ("risk_per_trade_pct", "portfolio_risk_pct", "max_symbol_risk_pct", "cluster_risk_pct", "kill_dd_mult", "trials", "default_risk_pct", "max_risk_pct", "target_max_dd_pct", "mt5_symbol_map", "mt5_server_time", "research", "alerts", "force_off")


def _dump(path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(obj), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _light(lat: dict) -> dict:
    """research.json for the Systems page without the per-candidate OOS trade lists (those go to sys/*.json)."""
    out = {**lat, "symbols": {}}
    for k, v in (lat.get("symbols") or {}).items():
        out["symbols"][k] = {**v, "candidates": [research.slim(c) for c in v.get("candidates", [])]}
    return out


SYS_CACHE = DATA_DIR / "sys_cache"     # every strategy x chart payload from the last full build (Actions cache between runs)


def live_set(lat: dict) -> set:
    """Payloads that must be fresh on every build: traded systems, the 3 best candidates of each chart, systems with open
    signals. (109 strategies x 20 symbols x 3 TF = 6,540 payloads take ~20 min, so the rest is rebuilt once a day.)"""
    keep = set()
    for sym, e in (lat.get("symbols") or {}).items():
        for x in research.live_systems(e):
            keep.add((sym, x["tf"], x["strategy"]))
        by_tf: dict[str, list] = {}
        for c in e.get("candidates", []):
            by_tf.setdefault(c.get("tf"), []).append(c)
        for tf, cs in by_tf.items():
            for c in sorted(cs, key=lambda c: -((c.get("oos") or {}).get("t_stat") or -9))[:3]:
                keep.add((sym, tf, c["strategy"]))
    for s in scanner.load_signals()[:200]:
        if s.get("outcome") == "open":
            keep.add((s["symbol"], s["tf"], s["strategy"]))
    return {k for k in keep if k[0] in SYMBOLS and k[2] in STRATEGIES}


def _payload(job):
    sym, tf, sid, limit, lat, lite = job
    try:   # lite = a system nobody trades: fewer candles / trades so 6,540 payloads stay ~300 MB on Pages
        return job[:3], charts.system_payload(sym, tf, sid, limit=600 if lite else limit, lat=lat, trades_cap=300 if lite else 1500)
    except Exception as e:
        log.warning("system %s %s %s failed: %s", sym, tf, sid, e)
        return job[:3], None


def build_systems(out_dir, lat: dict, limit: int, full: bool) -> None:
    """full = every strategy on every chart (parallel); otherwise the cached copy + fresh live_set()."""
    import os
    from concurrent.futures import ProcessPoolExecutor
    out_dir.mkdir(parents=True, exist_ok=True)
    if not full and SYS_CACHE.exists():
        shutil.copytree(SYS_CACHE, out_dir, dirs_exist_ok=True)
    live = live_set(lat)
    if full:   # every chart x the 109 core systems, + the pullback / mean-reversion systems that are not OFF (500 systems)
        keep_mr = {(sym, c.get("tf"), c.get("strategy")) for sym, e in (lat.get("symbols") or {}).items()
                   for c in e.get("candidates", []) if c.get("status") != "OFF"}
        jobs = sorted({(sym, tf, sid) for sym in SYMBOLS for tf in ("H1", "H4", "D1") for sid in STRATEGIES
                       if not sid.startswith("mr_") or (sym, tf, sid) in keep_mr} | live)
    else:
        jobs = sorted(live)
    for sym in {j[0] for j in jobs}:                 # load prices once in the parent (forked workers share them)
        for tf in ("H1", "H4", "D1"):
            try:
                data.get_bars(sym, tf, closed_only=False)
            except Exception:
                pass
    workers = max(1, (os.cpu_count() or 2)) if full and len(jobs) > 50 else 1
    if workers > 1:
        with ProcessPoolExecutor(workers) as ex:
            results = ex.map(_payload, [(*j, limit, lat, j not in live) for j in jobs], chunksize=20)
            for key, p in results:
                if p is not None:
                    _dump(out_dir / f"{key[0]}_{key[1]}_{key[2]}.json", p)
    else:
        for j in jobs:
            key, p = _payload((*j, limit, lat, j not in live))
            if p is not None:
                _dump(out_dir / f"{key[0]}_{key[1]}_{key[2]}.json", p)
    SYS_CACHE.mkdir(parents=True, exist_ok=True)
    shutil.copytree(out_dir, SYS_CACHE, dirs_exist_ok=True)
    if full:
        (SYS_CACHE / "_full.txt").write_text(datetime.now(timezone.utc).date().isoformat())
    log.info("system payloads: %d (%s)", len(jobs), "full" if full else "live set + cache")


def full_due() -> bool:
    """A full payload build once a UTC day (or when no cache exists)."""
    f = SYS_CACHE / "_full.txt"
    return not f.exists() or f.read_text().strip() != datetime.now(timezone.utc).date().isoformat()


def build(repo: str = "", chart_limit: int = 1200, full_sys: bool | None = None) -> None:
    if full_sys is None:
        full_sys = full_due()
    settings = load_settings()
    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)
    web = ROOT / "web"
    shutil.copytree(web, SITE_DIR / "static")
    # version = hash of the web app itself (not the data): changes only when code / layout changes
    version = hashlib.sha1(b"".join((web / f).read_bytes() for f in ("index.html", "app.js", "app.css"))).hexdigest()[:10]
    html = (web / "index.html").read_text(encoding="utf-8")
    html = html.replace('static/app.css"', f'static/app.css?v={version}"').replace('static/app.js"', f'static/app.js?v={version}"')
    html = html.replace('src="config.js"', f'src="config.js?t={int(datetime.now(timezone.utc).timestamp())}"')
    (SITE_DIR / "index.html").write_text(html, encoding="utf-8")
    shutil.copy(web / "manifest.webmanifest", SITE_DIR / "manifest.webmanifest")
    (SITE_DIR / ".nojekyll").write_text("")
    d = SITE_DIR / "data"
    state = read_json(CLOUD_STATE, {})
    lat = research.latest()
    cfg = {"static": True, "version": version, "built": datetime.now(timezone.utc).isoformat(), "repo": repo,
           "last_scan": state.get("last_scan"), "scan_error": state.get("last_error"),
           "research_generated": lat.get("generated"), "account_balance": settings["account_balance"]}
    (SITE_DIR / "config.js").write_text("window.TL_STATIC = " + json.dumps(cfg) + ";", encoding="utf-8")
    (SITE_DIR / "version.json").write_text(json.dumps({"version": version, "built": cfg["built"], "last_scan": cfg["last_scan"],
                                                       "research": cfg["research_generated"]}), encoding="utf-8")
    _dump(d / "settings.json", {k: settings[k] for k in PUBLIC_SETTINGS})
    _dump(d / "symbols.json", research.symbol_table(settings))
    _dump(d / "strategies.json", public_list())
    _dump(d / "research.json", _light(lat))
    for sym in (lat.get("symbols") or {}):            # full candidate records per symbol: only the Lab page loads them
        _dump(d / "research" / f"{sym}.json", research.candidates(sym))
    _dump(d / "picks.json", research.picks(lat))
    from . import compare, fills
    _dump(d / "compare.json", compare.load())                   # swap-charged comparison run (Lab page)
    _dump(d / "slippage.json", fills.calibration())             # real fills vs the tested slippage (พอร์ต MT5 page)
    _dump(d / "data_quality.json", read_json(research.RESEARCH_DIR / "data_quality.json", {}))
    _dump(d / "history.json", research.history())
    _dump(d / "signals.json", scanner.load_signals()[:200])
    _dump(d / "forward.json", scanner.forward_summary())
    (d / "notes.md").write_text(AI_NOTES.read_text(encoding="utf-8") if AI_NOTES.exists() else "", encoding="utf-8")
    # candles for every chart
    for sym in SYMBOLS:
        for tf in ("H1", "H4", "D1"):
            try:
                cd = charts.candles(sym, tf, chart_limit, system=False)
                _dump(d / "chart" / f"{sym}_{tf}.json", cd)
                for y in cd["older_years"]:     # older history in yearly files, fetched only when the chart is scrolled back
                    _dump(d / "chart" / f"{sym}_{tf}" / f"{y}.json", charts.candles_year(sym, tf, y, cd["first_time"]))
            except Exception as e:
                log.warning("chart %s %s failed: %s", sym, tf, e)
    build_systems(d / "sys", lat, chart_limit, full=full_sys)
    log.info("site built -> %s", SITE_DIR)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    build()
    print("CLOUD" if CLOUD else "local", SITE_DIR, data.source_for("EURUSD"))
