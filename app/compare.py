"""Comparison research runs (user, 3 Nov 2026): the same research with swap CHARGED, judged next to the live (swap-free)
results without replacing them — shows which systems only work because the account books no swap.

  python -m app.cloud compare      (workflow: Run workflow -> research, variant = swap)

The parts are judged in a throw-away copy of data/research (no previous run, so no "kept" carry-over), then a small
summary goes to data/research/compare/swap_charged.json and the Lab page shows it next to each candidate.
"""
from __future__ import annotations

import gzip
import json
import logging
import shutil
import statistics as st
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import research
from .config import RESEARCH_DIR, read_json, write_json

log = logging.getLogger("tradinglab.compare")
COMPARE_DIR = RESEARCH_DIR / "compare"
OUT = COMPARE_DIR / "swap_charged.json"


def _row(c: dict) -> dict:
    o = c.get("oos_sel") or c.get("oos") or {}
    return {"status": c.get("status"), "t": o.get("t_month"), "exp": o.get("expectancy"), "n": o.get("trades"),
            "hold": (c.get("holdout") or {}).get("expectancy")}


def _cands(cdir: Path, sym: str) -> list[dict]:
    f = cdir / f"{sym}.json.gz"
    if not f.exists():
        return []
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _groups(rows: list[dict]) -> dict:
    out = {}
    for name, sel in (("core", lambda k: ":mr_" not in k), ("mr", lambda k: ":mr_" in k)):
        xs = [r for k, r in rows if sel(k) and (r.get("n") or 0) >= 30 and r.get("exp") is not None]
        if xs:
            out[name] = {"n": len(xs), "exp": round(st.mean(r["exp"] for r in xs), 4),
                         "pos_pct": round(100 * sum(r["exp"] > 0 for r in xs) / len(xs), 1),
                         "t_gt2": sum((r.get("t") or 0) > 2 for r in xs), "t_gt3": sum((r.get("t") or 0) > 3 for r in xs)}
    return out


def judge_parts(parts: list[dict]) -> dict:
    """research.merge into a temporary research folder; returns {key: row} of the comparison run."""
    tmp = Path(tempfile.mkdtemp(prefix="tl_cmp_"))
    (tmp / "history").mkdir()
    keep = {n: getattr(research, n) for n in ("LATEST_RESEARCH", "RESEARCH_DIR", "OOS_DIR", "CANDS_DIR", "LEDGER_FILE")}
    try:
        if research.LEDGER_FILE.exists():
            shutil.copy(research.LEDGER_FILE, tmp / "trial_ledger.json")
        research.LATEST_RESEARCH = tmp / "latest.json"
        research.RESEARCH_DIR = tmp
        research.OOS_DIR = tmp / "oos"
        research.CANDS_DIR = tmp / "cands"
        research.LEDGER_FILE = tmp / "trial_ledger.json"
        out = research.merge(parts)
        rows = {}
        for sym in out.get("symbols", {}):
            for c in _cands(tmp / "cands", sym):
                rows[f"{sym}:{c['strategy']}:{c['tf']}"] = _row(c)
        meta = {"t_needed": {s: v.get("t_needed") for s, v in out["symbols"].items()},
                "active": [f"{s}:{x['strategy']}:{x['tf']}" for s, v in out["symbols"].items() for x in v.get("systems", [])],
                "trials": [f"{s}:{x['strategy']}:{x['tf']}" for s, v in out["symbols"].items() for x in v.get("trial_systems", [])],
                "portfolio": [{"strategy": c["strategy"], "tf": c["tf"], "status": c["status"], "t": c.get("t_month")}
                              for c in (out.get("portfolio") or {}).get("candidates", [])[:10]]}
        return {"rows": rows, "meta": meta}
    finally:
        for n, v in keep.items():
            setattr(research, n, v)
        shutil.rmtree(tmp, ignore_errors=True)


def compare(parts: list[dict]) -> dict:
    cmp = judge_parts(parts)
    live = research.latest()
    live_rows = {}
    for sym in live.get("symbols", {}):
        for c in research.candidates(sym):
            live_rows[f"{sym}:{c['strategy']}:{c['tf']}"] = _row(c)
    live_active = [f"{s}:{x['strategy']}:{x['tf']}" for s, v in live["symbols"].items() for x in v.get("systems", [])]
    live_trials = [f"{s}:{x['strategy']}:{x['tf']}" for s, v in live["symbols"].items() for x in v.get("trial_systems", [])]
    # per candidate: keep the ones that matter in either run (not OFF somewhere, or t > 2) — small enough for the site
    both = {}
    for k in set(live_rows) | set(cmp["rows"]):
        a, b = live_rows.get(k) or {}, cmp["rows"].get(k) or {}
        if a.get("status") in ("ACTIVE", "WATCH") or b.get("status") in ("ACTIVE", "WATCH") \
                or (a.get("t") or 0) > 2 or (b.get("t") or 0) > 2:
            both[k] = {"free": [a.get("status"), a.get("t"), a.get("exp")], "swap": [b.get("status"), b.get("t"), b.get("exp")]}
    # systems that pass / trade only without swap
    watch = sorted(set(live_active + live_trials), key=lambda k: -((live_rows.get(k) or {}).get("t") or 0))
    dependent = [k for k in watch if (cmp["rows"].get(k) or {}).get("status") not in ("ACTIVE",) and k in live_active] + \
                [k for k in live_trials if k not in cmp["meta"]["trials"] and k not in cmp["meta"]["active"]]
    summary = {
        "generated": datetime.now(timezone.utc).isoformat(), "live_generated": live.get("generated"),
        "variant": "swap_charged",
        "live": {"active": live_active, "trials": live_trials, "groups": _groups(list(live_rows.items()))},
        "swap": {**cmp["meta"], "groups": _groups(list(cmp["rows"].items()))},
        "swap_dependent": dependent,
        "watch": [{"key": k, "free": live_rows.get(k), "swap": cmp["rows"].get(k)} for k in watch],
        "candidates": both,
    }
    COMPARE_DIR.mkdir(parents=True, exist_ok=True)
    write_json(OUT, summary)
    log.info("compare: live ACTIVE %d / with swap %d · swap-dependent %d", len(live_active), len(cmp["meta"]["active"]),
             len(dependent))
    return summary


def load() -> dict:
    return read_json(OUT, {})
