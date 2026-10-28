"""Union of data/signals.json with the copy at a git ref (default origin/main), by signal id.

The cloud bot and the PC auto-trader both add signals (the PC within seconds of the candle close, the bot 10-45 min later).
Before the bot commits, this keeps every signal from both sides: for an id present on both, the LOCAL copy wins (the bot has
the newest forward-test tracking). Newest first, 600 max — same as scanner.save_signals.
usage: python -m tools.merge_signals [ref]
"""
from __future__ import annotations

import json
import subprocess
import sys

from app.config import SIGNALS_FILE, read_json, write_json


def union(local: list[dict], other: list[dict]) -> list[dict]:
    ids = {s["id"] for s in local}
    out = local + [s for s in other if s.get("id") not in ids]
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
