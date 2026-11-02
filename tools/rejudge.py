"""Judge the current quarter's research again WITHOUT re-running the tests: rebuilds each symbol from the candidates already
in data/research/latest.json + the heavy files in data/research/oos/, with the current gates and carry-over rules.
Use after a rule change (2 Nov 2026: 'kept' narrowed — same price history and only the multiple-testing gate failed).
usage: python -m tools.rejudge
"""
from __future__ import annotations

import sys

from app import data, research
from app.config import load_settings, write_json


def main() -> int:
    lat = research.latest()
    settings = load_settings()
    for sym, r in list(lat.get("symbols", {}).items()):
        cands = r.get("candidates") or []
        if not cands:
            continue
        parts = []
        for tf in sorted({c["tf"] for c in cands}):
            parts.append({"symbol": sym, "tf": tf, "cutoff": (r.get("quarter") or {}).get("cutoff"),
                          "data_source": r.get("data_source"), "history_source": data.history_source(sym),
                          "data_from": r.get("data_from"), "data_to": r.get("data_to"), "bars_h1": r.get("bars_h1"),
                          "candidates": [c for c in cands if c["tf"] == tf], "heavy": research.heavy(sym, tf)})
        # the previous run carried no history_source -> nothing is carried over (different prices are judged afresh)
        old = r.get("previous") or {}
        prev_sym = {"quarter": old.get("quarter"), "history_source": old.get("history_source"),
                    "systems": [{"strategy": s, "tf": t} for s, t in old.get("systems", [])]}
        new = research.merge_symbol(sym, parts, settings, prev_sym)
        new["previous"] = r.get("previous")
        lat["symbols"][sym] = new
        print(sym, r.get("status"), "->", new["status"], [(x["strategy"], x["tf"]) for x in new["systems"]])
    # portfolio: candidates that were only 'kept' lose ACTIVE unless they pass every gate now
    for pc in (lat.get("portfolio") or {}).get("candidates", []):
        if pc.get("kept") and not all(g["pass"] for g in pc["gates"]):
            pc["status"], pc["kept"] = ("WATCH" if pc["t_month"] >= 2 and pc["exp"] > 0 else "OFF"), False
    write_json(research.LATEST_RESEARCH, lat)
    return 0


if __name__ == "__main__":
    sys.exit(main())
