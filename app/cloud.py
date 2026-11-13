"""Entry point for GitHub Actions (no PC needed):

  python -m app.cloud scan       hourly: fresh prices -> new signals + forward tracking -> push alert -> rebuild site
  python -m app.cloud research   full research in ONE process (slow; the workflow uses prefetch -> part x 24 -> merge instead)
  python -m app.cloud prefetch | part SYMBOL TF | merge   the parallel research run
  python -m app.cloud compare    judge a comparison run (swap charged) into data/research/compare/ only
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


def _sync_remote() -> None:
    """The checkout can be minutes old (GitHub queues runs): before scanning, take the PC's newest heartbeat and signals
    from origin/main, so the cloud never re-creates a signal the PC already alerted and traded (with its own SL/TP)."""
    import subprocess
    from pathlib import Path
    try:
        subprocess.run(["git", "fetch", "-q", "origin", "main"], timeout=90, check=True)
        raw = subprocess.run(["git", "show", "origin/main:data/pc_status.json"], capture_output=True, text=True,
                             encoding="utf-8", timeout=30)
        if raw.returncode == 0 and raw.stdout.strip():
            Path(scanner.SIGNALS_FILE.parent / "pc_status.json").write_text(raw.stdout, encoding="utf-8")
            from . import fills         # keep every real fill (the robot only keeps its newest) for slippage calibration
            fills.collect(json.loads(raw.stdout))
        from tools.merge_signals import main as merge_signals
        merge_signals("origin/main")
    except Exception as e:  # offline / no git: scan with the checkout as before
        log.warning("sync with origin/main skipped: %s", e)


def _part_files() -> tuple[dict, str]:
    """Research part files grouped by symbol + a completeness check (every symbol, every strategy x TF exactly once)."""
    from .config import SYMBOLS
    by_sym: dict[str, list] = {}
    for f in sorted(research.PARTS_DIR.glob("*.json.gz")):
        by_sym.setdefault(f.name.split("_")[0], []).append(f)
    if not by_sym:
        return {}, "no research parts found"
    missing = [s for s in SYMBOLS if s not in by_sym]
    if missing:
        return by_sym, f"parts missing for {len(missing)} symbol(s): {', '.join(missing[:8])}"
    need = research.expected_candidates()
    for sym, fs in by_sym.items():
        if sym not in SYMBOLS:
            continue
        seen = set()
        for f in fs:
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                p = json.load(fh)
            seen |= {(c["strategy"], p["tf"]) for c in p["candidates"]}
        if len(seen) != need:
            return by_sym, f"{sym}: {len(seen)} of {need} candidates (a research job is missing or failed)"
    return by_sym, ""


def _stream_parts(by_sym: dict):
    """(symbol, [its parts]) one symbol at a time — the whole research does not fit in memory at once."""
    for sym, fs in by_sym.items():
        parts = []
        for f in fs:
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                parts.append(json.load(fh))
        yield sym, parts


def main(mode: str, *args: str) -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    research_error = ""
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
        sym, tf_arg = args[0], args[1]
        chunk = args[2] if len(args) > 2 else None
        # "ALL" = every research TF in one job (9 Nov 2026: 30 symbols x 3 TF x 4 chunks would pass the 256-job matrix limit)
        tfs = research.load_settings()["research"]["timeframes"] if tf_arg.upper() == "ALL" else [tf_arg]
        research.PARTS_DIR.mkdir(parents=True, exist_ok=True)
        suffix = f"_{chunk.replace('/', 'of')}" if chunk else ""
        for tf in tfs:
            part = research.research_part(sym, tf, chunk=chunk)
            with gzip.open(research.PARTS_DIR / f"{sym}_{tf}{suffix}.json.gz", "wt", encoding="utf-8") as f:
                json.dump(part, f, separators=(",", ":"), default=str)
        return 0
    if mode == "compare":    # comparison run (variant swap): judge the parts aside, live results untouched, no scan
        from . import compare
        files, problem = _part_files()
        log.info("comparison: %d research part files", len(files))
        if problem:
            log.error("comparison skipped: %s", problem)
            return 1
        compare.compare(_stream_parts(files))
        return 0
    full = None              # system payloads: full rebuild after a research merge, else export decides (once a day)
    if mode in ("merge", "mergeonly"):   # research run, step 3: judge all candidates, write latest.json, then scan + build below
        # "mergeonly" (13 Nov 2026): the 41-market merge + full site build in one step ran the runner out of resources twice
        # and nothing was committed; the workflow now commits right after the merge and builds the site in a later step.
        files, problem = _part_files()
        log.info("merging %d research part files", len(files))
        if problem:
            # 10 Nov 2026: a CANCELLED research run still merged the parts that had finished and overwrote the results with
            # a partial mix. Incomplete parts are never merged: the last complete research stays in force.
            log.error("research NOT merged: %s", problem)
            research_error = problem
        elif files:
            research.merge(_stream_parts(files))
            full = True
            try:                 # every system that would trade, together, on its OOS trades (app/book.py)
                from . import book
                book.build()
            except Exception:
                log.exception("book simulation failed")
        if mode == "mergeonly":
            return 0
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
        _sync_remote()
        new = scanner.scan_once()
        write_json(export.CLOUD_STATE, {"last_scan": datetime.now(timezone.utc).isoformat(), "research_error": research_error,
                                        "last_error": scanner.STATE.get("last_error"), "new_signals": len(new),
                                        "checked": scanner.STATE.get("checked"), "errors": scanner.STATE.get("errors", [])[:10]})
        log.info("scan: %d new signal(s)", len(new))
        try:                     # automatic self-check after every scan (app/health.py)
            from . import health
            h = health.run()
            log.info("health: %d problem(s)", len(h["problems"]))
        except Exception:
            log.exception("health check failed")
    export.build(repo=repo, full_sys=full)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    sys.exit(main(*(sys.argv[1:] or ["scan"])))
