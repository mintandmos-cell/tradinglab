"""Entry point for GitHub Actions (no PC needed):

  python -m app.cloud scan       hourly: fresh prices -> new signals + forward tracking -> push alert -> rebuild site
  python -m app.cloud research   weekly / one button: full auto research (WF + robustness + MC) -> scan -> rebuild
  python -m app.cloud build      rebuild the site only
  python -m app.cloud test       send a test push (priority 5, like a real ACTIVE signal)
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

from . import export, research, scanner
from .config import write_json

log = logging.getLogger("tradinglab.cloud")


def main(mode: str) -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
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
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "scan"))
