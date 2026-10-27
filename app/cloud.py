"""Entry point for GitHub Actions (no PC needed):

  python -m app.cloud scan       hourly: fresh prices -> new signals + forward tracking -> push alert -> rebuild site
  python -m app.cloud research   full research in ONE process (slow; the workflow uses prefetch -> part x 24 -> merge instead)
  python -m app.cloud prefetch | part SYMBOL TF | merge   the parallel research run
  python -m app.cloud build      rebuild the site only
  python -m app.cloud test       send a test push (priority 5, like a real ACTIVE signal)
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import sys
from datetime import datetime, timezone

from . import export, research, scanner
from .config import write_json

log = logging.getLogger("tradinglab.cloud")


def main(mode: str, *args: str) -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if mode == "prefetch":   # research run, step 1: fresh prices for every symbol into data/cache (shared with the part jobs)
        from . import data
        from .config import SYMBOLS
        for sym in SYMBOLS:
            try:
                data.load_h1(sym, refresh=True)
            except Exception as e:
                log.warning("prefetch %s: %s", sym, e)
        return 0
    if mode == "part":       # research run, step 2 (one matrix job per symbol x TF): every strategy, every step
        sym, tf = args[0], args[1]
        part = research.research_part(sym, tf)
        research.PARTS_DIR.mkdir(parents=True, exist_ok=True)
        with gzip.open(research.PARTS_DIR / f"{sym}_{tf}.json.gz", "wt", encoding="utf-8") as f:
            json.dump(part, f, separators=(",", ":"), default=str)
        return 0
    if mode == "merge":      # research run, step 3: judge all candidates, write latest.json, then scan + build below
        parts = []
        for f in sorted(research.PARTS_DIR.glob("*.json.gz")):
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                parts.append(json.load(fh))
        log.info("merging %d research parts", len(parts))
        if parts:
            research.merge(parts)
        mode = "scan"
    if mode == "test":   # Actions -> TradingLab -> Run workflow -> test: a push at the same priority as real ACTIVE signals
        from .notify import send
        r = send("🔔 TradingLab ทดสอบแจ้งเตือน (เหมือนสัญญาณจริง)",
                 "ถ้าเห็นข้อความนี้และได้ยินเสียง แปลว่าแจ้งเตือนสัญญาณ ACTIVE จะมีเสียงด้วย ✅", priority=5, tags=["bell"])
        log.info("test push: %s", r)
        return 0 if r.get("ntfy") == 200 or r.get("telegram") == 200 else 1
    if mode == "research":
        research.run_all()
    if mode in ("scan", "research"):
        new = scanner.scan_once()
        write_json(export.CLOUD_STATE, {"last_scan": datetime.now(timezone.utc).isoformat(),
                                        "last_error": scanner.STATE.get("last_error"), "new_signals": len(new)})
        log.info("scan: %d new signal(s)", len(new))
    export.build(repo=repo)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    sys.exit(main(*(sys.argv[1:] or ["scan"])))
