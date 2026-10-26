"""Build the static web app (GitHub Pages): the same front-end, reading pre-computed JSON instead of the API.

  python -m app.export            -> site/
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone

from . import charts, data, research, scanner
from .charts import clean
from .config import AI_NOTES, CLOUD, DATA_DIR, ROOT, SITE_DIR, SYMBOLS, load_settings, read_json
from .strategies import public_list

log = logging.getLogger("tradinglab.export")
CLOUD_STATE = DATA_DIR / "cloud_state.json"
# settings the browser may show; never secrets or the account balance
PUBLIC_SETTINGS = ("default_risk_pct", "max_risk_pct", "target_max_dd_pct", "mt5_symbol_map", "research", "alerts", "force_off")


def _dump(path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(obj), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def build(repo: str = "", chart_limit: int = 1200) -> None:
    settings = load_settings()
    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)
    web = ROOT / "web"
    shutil.copytree(web, SITE_DIR / "static")
    shutil.copy(web / "index.html", SITE_DIR / "index.html")
    shutil.copy(web / "manifest.webmanifest", SITE_DIR / "manifest.webmanifest")
    (SITE_DIR / ".nojekyll").write_text("")
    d = SITE_DIR / "data"
    state = read_json(CLOUD_STATE, {})
    lat = research.latest()
    cfg = {"static": True, "built": datetime.now(timezone.utc).isoformat(), "repo": repo,
           "last_scan": state.get("last_scan"), "scan_error": state.get("last_error"),
           "research_generated": lat.get("generated")}
    (SITE_DIR / "config.js").write_text("window.TL_STATIC = " + json.dumps(cfg) + ";", encoding="utf-8")
    _dump(d / "settings.json", {k: settings[k] for k in PUBLIC_SETTINGS})
    _dump(d / "symbols.json", research.symbol_table(settings))
    _dump(d / "strategies.json", public_list())
    _dump(d / "research.json", lat)
    _dump(d / "history.json", research.history())
    _dump(d / "signals.json", scanner.load_signals()[:200])
    _dump(d / "forward.json", scanner.forward_summary())
    (d / "notes.md").write_text(AI_NOTES.read_text(encoding="utf-8") if AI_NOTES.exists() else "", encoding="utf-8")
    for sym in SYMBOLS:
        for tf in ("H1", "H4", "D1"):
            try:
                _dump(d / "chart" / f"{sym}_{tf}.json", charts.candles(sym, tf, chart_limit))
            except Exception as e:
                log.warning("chart %s %s failed: %s", sym, tf, e)
    log.info("site built -> %s", SITE_DIR)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    build()
    print("CLOUD" if CLOUD else "local", SITE_DIR, data.source_for("EURUSD"))
