"""Union of data/signals.json with the copy at a git ref (default origin/main), by signal id.

The cloud bot and the PC auto-trader both add signals (the PC within seconds of the candle close, the bot 10-45 min later).
Before the bot commits, this keeps every signal from both sides: for an id present on both, the PC's copy wins (it defines the
levels the robot placed), else the LOCAL copy (the bot has the newest forward-test tracking). Newest first, 600 max — same as scanner.save_signals.
usage: python -m tools.merge_signals [ref]
"""
from __future__ import annotations

import json
import subprocess
import sys

from app.config import SIGNALS_FILE, read_json, write_json


TRACK = ("outcome", "outcome_r", "exit_time", "exit_reason", "exit_price")


def _pick(a: dict, b: dict) -> dict:
    """Two copies of one signal id (a = local). The PC's copy (broker candles; the robot placed exactly its SL/TP)
    defines the trade, else the local copy. A copy with the SAME levels that already finished tracking lends its result
    to one still marked open (results tracked on other levels would be wrong). The local user_status is kept."""
    base, other = (b, a) if b.get("origin") == "pc" and a.get("origin") != "pc" else (a, b)
    out = dict(base)
    if out.get("outcome") == "open" and other.get("outcome") not in (None, "open") \
            and other.get("sl") == out.get("sl") and other.get("tp") == out.get("tp"):
        out.update({k: other[k] for k in TRACK if k in other})
    if "user_status" in a:
        out["user_status"] = a["user_status"]
    return out


def union(local: list[dict], other: list[dict]) -> list[dict]:
    theirs = {s.get("id"): s for s in other}
    out = [_pick(s, theirs[s["id"]]) if s.get("id") in theirs else s for s in local]
    ids = {s["id"] for s in local}
    out += [s for s in other if s.get("id") not in ids]
    out.sort(key=lambda s: s.get("created") or "", reverse=True)
    return out[:600]


def main(ref: str = "origin/main") -> int:
    try:
        raw = subprocess.run(["git", "show", f"{ref}:data/signals.json"], capture_output=True, text=True, check=True,
                             encoding="utf-8").stdout
        other = json.loads(raw)
    except Exception as e:
        print(f"merge_signals: nothing to merge ({e})")
        return 0
    local = read_json(SIGNALS_FILE, [])
    merged = union(local, other)
    added = len({s["id"] for s in merged} - {s["id"] for s in local})
    write_json(SIGNALS_FILE, merged)
    print(f"merge_signals: +{added} signal(s) from {ref}")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
