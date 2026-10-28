"""PC <-> GitHub for the auto-trader: fresh research + signals before each scan, and new signals published right after,
so the phone alert, the website and the MT5 order all come from the same signal (same id = no second alert from the cloud).

Uses the GitHub contents API with the user's own token: data/local.json "github_token", else the credential git already
stores on this PC (the one export_mt5_history.bat pushes with). Without a token it still downloads (public repo) but cannot publish.
"""
from __future__ import annotations

import base64
import json
import logging
import subprocess

import requests

from .config import LATEST_RESEARCH, SIGNALS_FILE, load_settings, read_json, write_json

log = logging.getLogger("tradinglab.ghsync")
REPO = "mintandmos-cell/tradinglab"
API = f"https://api.github.com/repos/{REPO}/contents/"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/"
_tok = {"v": None}


def token() -> str:
    if _tok["v"] is None:
        t = (load_settings().get("github_token") or "").strip()
        if not t:
            try:
                out = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                                     capture_output=True, text=True, timeout=20).stdout
                t = next((ln.split("=", 1)[1] for ln in out.splitlines() if ln.startswith("password=")), "")
            except Exception:
                t = ""
        _tok["v"] = t
    return _tok["v"]


def _hdr() -> dict:
    h = {"Accept": "application/vnd.github+json"}
    if token():
        h["Authorization"] = f"Bearer {token()}"
    return h


def _get(path: str) -> tuple[str, str | None]:
    """(text, sha) of a file on main."""
    if token():
        r = requests.get(API + path, headers=_hdr(), params={"ref": "main"}, timeout=30)
        r.raise_for_status()
        j = r.json()
        if j.get("content"):
            return base64.b64decode(j["content"]).decode("utf-8"), j["sha"]
        r = requests.get(j["download_url"], timeout=60)                    # files > 1 MB come without inline content
        r.raise_for_status()
        return r.text, j["sha"]
    r = requests.get(RAW + path, timeout=60)
    r.raise_for_status()
    return r.text, None


def pull() -> bool:
    """Newest research + signals from GitHub into the local files (the scan reads them)."""
    ok = True
    for path, local in (("data/research/latest.json", LATEST_RESEARCH), ("data/signals.json", SIGNALS_FILE)):
        try:
            text, _ = _get(path)
            json.loads(text)
            local.write_text(text, encoding="utf-8")
        except Exception as e:
            log.warning("pull %s failed: %s — using the local copy", path, e)
            ok = False
    return ok


def publish(new: list[dict]) -> bool:
    """Add these signals to data/signals.json on GitHub (union by id, retry when the cloud bot commits at the same time)."""
    if not new:
        return True
    if not token():
        log.warning("no GitHub token: signals not published (set github_token in data/local.json)")
        return False
    for _ in range(4):
        try:
            text, sha = _get("data/signals.json")
            remote = json.loads(text)
            ids = {s.get("id") for s in remote}
            add = [s for s in new if s["id"] not in ids]
            if not add:
                return True
            merged = sorted(add + remote, key=lambda s: s.get("created") or "", reverse=True)[:600]
            body = json.dumps(merged, ensure_ascii=False, indent=1, default=str)
            names = ", ".join(f"{s['symbol']} {s['tf']} {s['strategy']}" for s in add)
            r = requests.put(API + "data/signals.json", headers=_hdr(), timeout=60, json={
                "message": f"pc: signal {names}", "sha": sha, "branch": "main",
                "content": base64.b64encode(body.encode("utf-8")).decode()})
            if r.status_code in (200, 201):
                local = read_json(SIGNALS_FILE, [])
                write_json(SIGNALS_FILE, merged if not local else
                           sorted(local + [s for s in merged if s["id"] not in {x["id"] for x in local}],
                                  key=lambda s: s.get("created") or "", reverse=True)[:600])
                return True
            log.warning("publish: HTTP %s %s", r.status_code, r.text[:200])
            if r.status_code not in (409, 422):
                return False
        except Exception as e:
            log.warning("publish failed: %s", e)
    return False


def put_status(status: dict) -> bool:
    """data/pc_status.json on GitHub = the PC's heartbeat: the cloud scan leaves candles the PC already scanned to the PC
    (scanner.pc_covered). Not in the workflow's push paths, so it does not trigger a site build."""
    if not token():
        return False
    path = "data/pc_status.json"
    for _ in range(3):
        try:
            r = requests.get(API + path, headers=_hdr(), params={"ref": "main"}, timeout=30)
            sha = r.json().get("sha") if r.status_code == 200 else None
            body = {"message": "pc: heartbeat [skip ci]", "branch": "main",
                    "content": base64.b64encode(json.dumps(status, indent=1).encode()).decode()}
            if sha:
                body["sha"] = sha
            r = requests.put(API + path, headers=_hdr(), json=body, timeout=60)
            if r.status_code in (200, 201):
                return True
            log.warning("heartbeat: HTTP %s %s", r.status_code, r.text[:200])
        except Exception as e:
            log.warning("heartbeat failed: %s", e)
    return False
