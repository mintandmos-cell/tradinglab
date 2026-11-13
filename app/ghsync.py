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
import time

import requests

from .config import LATEST_RESEARCH, SIGNALS_FILE, load_settings, read_json, write_json

log = logging.getLogger("tradinglab.ghsync")
REPO = "mintandmos-cell/tradinglab"
API = f"https://api.github.com/repos/{REPO}/contents/"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/"
_tok = {"v": None}


LAST = {"error": "", "source": ""}


def token() -> str:
    # an empty result (e.g. Windows credential manager not ready right after a reboot) is retried every 5 minutes
    if _tok["v"] == "" and time.time() - _tok.get("at", 0) > 300:
        _tok["v"] = None
    if _tok["v"] is None:
        _tok["at"] = time.time()
        t = (load_settings().get("github_token") or "").strip()
        LAST["source"] = "local.json" if t else ""
        if not t:
            try:
                out = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                                     capture_output=True, text=True, timeout=20).stdout
                t = next((ln.split("=", 1)[1] for ln in out.splitlines() if ln.startswith("password=")), "")
                LAST["source"] = "git credential" if t else "none"
            except Exception as e:
                t, LAST["source"] = "", f"none ({e!r})"
        _tok["v"] = t
    return _tok["v"]


def check() -> dict:
    """Can this PC write to the repository? -> {ok, source, status, detail} (shown in the log and the heartbeat)."""
    _tok["v"] = None
    t = token()
    if not t:
        return {"ok": False, "source": LAST["source"], "status": None, "detail": "no token"}
    try:
        r = requests.get(f"https://api.github.com/repos/{REPO}", headers=_hdr(), timeout=30)
        push = bool(r.ok and r.json().get("permissions", {}).get("push"))
        return {"ok": push, "source": LAST["source"], "status": r.status_code,
                "detail": "" if push else (r.text[:160] if not r.ok else "token has no write access")}
    except Exception as e:
        return {"ok": False, "source": LAST["source"], "status": None, "detail": repr(e)[:160]}


def _fail(msg: str) -> None:
    LAST["error"] = msg[:300]
    log.warning(msg)
    if " 401" in msg:                      # expired / revoked: read the credential again next time
        _tok["v"] = None


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
        _fail("no GitHub token: signals not published (set github_token in data/local.json)")
        return False
    for _ in range(4):
        try:
            text, sha = _get("data/signals.json")
            remote = json.loads(text)
            have = {s.get("id"): s for s in remote}
            # new ids, and ids the cloud alerted first on its free feed: the PC's copy replaces those (it is what MT5 traded)
            add = [s for s in new if s["id"] not in have or (s.get("origin") == "pc" and have[s["id"]].get("origin") != "pc")]
            if not add:
                return True
            from tools.merge_signals import union
            merged = union(add, remote)
            body = json.dumps(merged, ensure_ascii=False, indent=1, default=str)
            names = ", ".join(f"{s['symbol']} {s['tf']} {s['strategy']}" for s in add)
            r = requests.put(API + "data/signals.json", headers=_hdr(), timeout=60, json={
                "message": f"pc: signal {names}", "sha": sha, "branch": "main",
                "content": base64.b64encode(body.encode("utf-8")).decode()})
            if r.status_code in (200, 201):
                local = read_json(SIGNALS_FILE, [])
                write_json(SIGNALS_FILE, merged if not local else union(local, merged))
                return True
            _fail(f"publish: HTTP {r.status_code} {r.text[:200]}")
            if r.status_code not in (409, 422):
                return False
        except Exception as e:
            _fail(f"publish failed: {e!r}")
    return False


def put_status(status: dict) -> bool:
    """data/pc_status.json on GitHub = the PC's heartbeat: the cloud scan leaves candles the PC already scanned to the PC
    (scanner.pc_covered). Not in the workflow's push paths, so it does not trigger a site build."""
    if not token():
        _fail("no GitHub token: heartbeat not sent")
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
            _fail(f"heartbeat: HTTP {r.status_code} {r.text[:200]}")
        except Exception as e:
            _fail(f"heartbeat failed: {e!r}")
    return False


def put_file(path: str, content: bytes, message: str) -> bool:
    """Create / replace any file in the repository (used for the PC's Exness history exports)."""
    if not token():
        _fail(f"no GitHub token: {path} not sent")
        return False
    for _ in range(3):
        try:
            r = requests.get(API + path, headers=_hdr(), params={"ref": "main"}, timeout=30)
            sha = r.json().get("sha") if r.status_code == 200 else None
            body = {"message": message, "branch": "main", "content": base64.b64encode(content).decode()}
            if sha:
                body["sha"] = sha
            r = requests.put(API + path, headers=_hdr(), json=body, timeout=180)
            if r.status_code in (200, 201):
                return True
            _fail(f"put {path}: HTTP {r.status_code} {r.text[:200]}")
        except Exception as e:
            _fail(f"put {path} failed: {e!r}")
    return False


def put_files(files: dict[str, bytes], message: str) -> bool:
    """Several files in ONE commit (git data API: blobs -> tree -> commit -> move main), e.g. the 20 symbols' recent
    Exness bars every hour without 20 commits. Retries when main moved meanwhile."""
    if not token() or not files:
        _fail("no GitHub token: files not sent") if not token() else None
        return False
    base = f"https://api.github.com/repos/{REPO}/git/"
    blobs: dict[str, str] = {}              # uploaded once, reused by every retry
    # 13 Nov 2026: 3 quick retries all lost the race when the cloud bot committed at hh:06 -> wait longer between tries
    for attempt in range(6):
        if attempt:
            time.sleep((5, 15, 30, 60, 90)[attempt - 1])
        try:
            ref = requests.get(base + "ref/heads/main", headers=_hdr(), timeout=30).json()
            head = ref["object"]["sha"]
            tree0 = requests.get(base + f"commits/{head}", headers=_hdr(), timeout=30).json()["tree"]["sha"]
            entries = []
            for path, content in files.items():
                if path not in blobs:
                    blobs[path] = requests.post(base + "blobs", headers=_hdr(), timeout=60,
                                                json={"content": base64.b64encode(content).decode(), "encoding": "base64"}).json()["sha"]
                entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blobs[path]})
            tree = requests.post(base + "trees", headers=_hdr(), timeout=60, json={"base_tree": tree0, "tree": entries}).json()["sha"]
            com = requests.post(base + "commits", headers=_hdr(), timeout=60,
                                json={"message": message, "tree": tree, "parents": [head]}).json()["sha"]
            r = requests.patch(base + "refs/heads/main", headers=_hdr(), timeout=30, json={"sha": com, "force": False})
            if r.status_code == 200:
                return True
            _fail(f"put_files: HTTP {r.status_code} {r.text[:200]}")       # 422 = main moved: try again on the new head
        except Exception as e:
            _fail(f"put_files failed: {e!r}")
    return False


def get_json(path: str):
    try:
        text, _ = _get(path)
        return json.loads(text)
    except Exception:
        return None


BEAT_BRANCH = "pc-heartbeat"


def beat() -> bool:
    """A cheap "I am alive" signal every few minutes (user, 13 Nov 2026: alert on a power cut / lost internet within
    ~15 min). One parentless commit on the side branch pc-heartbeat, force-moved each time: main's history stays clean
    and no workflow starts (they listen to main only). The cloud ticker reads the branch's commit date."""
    if not token():
        return False
    base = f"https://api.github.com/repos/{REPO}/git/"
    try:
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        tree = requests.post(base + "trees", headers=_hdr(), timeout=10,
                             json={"tree": [{"path": "beat", "mode": "100644", "type": "blob", "content": now}]}).json()["sha"]
        com = requests.post(base + "commits", headers=_hdr(), timeout=10, json={"message": "pc heartbeat", "tree": tree, "parents": []}).json()["sha"]
        r = requests.patch(base + f"refs/heads/{BEAT_BRANCH}", headers=_hdr(), timeout=10, json={"sha": com, "force": True})
        if r.status_code in (404, 422):
            r = requests.post(base + "refs", headers=_hdr(), timeout=10, json={"ref": f"refs/heads/{BEAT_BRANCH}", "sha": com})
        return r.status_code in (200, 201)
    except Exception:
        return False
