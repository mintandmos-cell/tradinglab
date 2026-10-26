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
CLOUD_STATE = DATA_DIR / "cloud_state.json"
# settings the browser may show; never secrets or the account balance
PUBLIC_SETTINGS = ("risk_per_trade_pct", "default_risk_pct", "max_risk_pct", "target_max_dd_pct", "mt5_symbol_map", "research", "alerts", "force_off")


def _dump(path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(obj), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _light(lat: dict) -> dict:
    """research.json for the Systems page without the per-candidate OOS trade lists (those go to sys/*.json)."""
    out = {**lat, "symbols": {}}
    for k, v in (lat.get("symbols") or {}).items():
        out["symbols"][k] = {**v, "candidates": [{kk: vv for kk, vv in c.items() if kk != "oos_eq"} for c in v.get("candidates", [])]}
    return out


def build(repo: str = "", chart_limit: int = 1200) -> None:
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
    _dump(d / "history.json", research.history())
    _dump(d / "signals.json", scanner.load_signals()[:200])
    _dump(d / "forward.json", scanner.forward_summary())
    (d / "notes.md").write_text(AI_NOTES.read_text(encoding="utf-8") if AI_NOTES.exists() else "", encoding="utf-8")
    for sym in SYMBOLS:
        for tf in ("H1", "H4", "D1"):
            try:
                _dump(d / "chart" / f"{sym}_{tf}.json", charts.candles(sym, tf, chart_limit, system=False))
            except Exception as e:
                log.warning("chart %s %s failed: %s", sym, tf, e)
                continue
            # every strategy on every chart (chart page strategy picker): view-only, the scanner never reads these
            for sid in STRATEGIES:
                try:
                    _dump(d / "sys" / f"{sym}_{tf}_{sid}.json", charts.system_payload(sym, tf, sid, limit=chart_limit, lat=lat))
                except Exception as e:
                    log.warning("system %s %s %s failed: %s", sym, tf, sid, e)
    log.info("site built -> %s", SITE_DIR)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    build()
    print("CLOUD" if CLOUD else "local", SITE_DIR, data.source_for("EURUSD"))
