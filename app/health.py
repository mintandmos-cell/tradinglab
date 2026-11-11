"""Automatic self-check (user, 9 Nov 2026: the bugs so far were found by eye — the clock, BTC's holes, the swap flag).
Runs after every cloud scan; problems go to data/research/health.json (shown on the พอร์ต MT5 page) and a NEW problem
is pushed to the phone once (again only after 12 h if it is still there).

Checks
  pc_offline       the PC's heartbeat is older than 2 h (no orders are placed while it is off)
  bars_stale       the PC is online but its Exness bars on GitHub are > 3 h old (chart / cloud fall back to free feeds)
  untracked        an open signal whose candle cannot be found any more (forward tracking would never close it)
  robot_orphan     an MT5 position of the robot whose signal is already closed
  robot_missing    an ACTIVE signal (not capped) that the robot should hold but MT5 has no position for, 2+ hours on
  robot_sl         a robot position whose SL differs from the signal's level (alerted SL or the trailing stop)
  swap_new         MT5 booked swap on a symbol the research still tests swap-free
  scan_errors      the scanner reported errors / stale data
  research_bad     latest.json holds NaN / absurd values, or is older than the research cut-off
  auto_off         a system was stopped by the kill switch (information)
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone

import pandas as pd

from .config import RESEARCH_DIR, SYMBOLS, read_json, swap_mode, symbol_spec, write_json

log = logging.getLogger("tradinglab.health")
OUT = RESEARCH_DIR / "health.json"
REPUSH = timedelta(hours=12)


def _p(key: str, text: str, level: str = "warn") -> dict:
    return {"key": key, "text": text, "level": level}


def checks(now: datetime | None = None) -> tuple[list[dict], list[str]]:
    from . import data, research, scanner
    now = now or datetime.now(timezone.utc)
    probs, done = [], []
    pc = scanner.pc_status()
    sigs = scanner.load_signals()
    settings = research.load_settings()

    # PC heartbeat
    done.append("pc_offline")
    age_h = (now - datetime.fromisoformat(pc["time"])).total_seconds() / 3600 if pc.get("time") else None
    online = age_h is not None and age_h < 2
    if age_h is None or age_h >= 2:
        probs.append(_p("pc_offline", f"คอมออฟไลน์มา {age_h:.0f} ชม. — โปรแกรมไม่เปิดออเดอร์ให้ (คลาวด์แจ้งเตือนแทน)" if age_h else "ยังไม่เคยได้รับสัญญาณชีพจากคอม"))

    # Exness bars freshness (BTC trades 24/7, so its file must be recent when the PC is on)
    done.append("bars_stale")
    rec = data.recent_exness("BTCUSD")
    if online and rec is not None and len(rec):
        bars_h = (pd.Timestamp(now) - rec.index[-1]).total_seconds() / 3600
        if bars_h > 3:
            probs.append(_p("bars_stale", f"แท่ง Exness ที่คอมส่งขึ้นเว็บเก่า {bars_h:.0f} ชม. ทั้งที่คอมออนไลน์ — กราฟ/คลาวด์ใช้แหล่งฟรีแทน"))

    # open signals whose candle grid no longer matches (tracking would stop)
    done.append("untracked")
    frames: dict = {}
    stuck = []
    for s in sigs:
        if s.get("outcome") != "open":
            continue
        key = (s["symbol"], s["tf"])
        try:
            df = frames.get(key)
            if df is None:
                df = frames[key] = data.get_bars(*key)
            bt = pd.Timestamp(s["bar_time"])
            et = bt + pd.Timedelta(minutes={"H1": 60, "H4": 240, "D1": 1440}[s["tf"]])
            if not len(df) or (bt not in df.index and df.index.searchsorted(et, side="right") - 1 < 1):
                stuck.append(s["id"])
        except Exception as e:
            stuck.append(f"{s['id']} ({e.__class__.__name__})")
    if stuck:
        probs.append(_p("untracked", f"สัญญาณที่ติดตามผลไม่ได้ {len(stuck)} ตัว: {', '.join(stuck[:4])}"))

    # robot positions vs signals
    by_id = {s["id"]: s for s in sigs}
    if pc.get("positions") is not None and online:
        done += ["robot_orphan", "robot_sl", "robot_missing"]
        held = set()
        for p in pc["positions"]:
            sid = p.get("signal_id")
            if not sid:
                continue
            held.add(sid)
            s = by_id.get(sid)
            if s and s.get("outcome") != "open" and s.get("exit_reason") != "Rev":
                probs.append(_p(f"robot_orphan:{sid}", f"MT5 ยังถือ {p.get('symbol')} ({sid}) แต่สัญญาณจบไปแล้ว ({s.get('exit_reason')})"))
            if s and s.get("outcome") == "open" and p.get("sl"):
                want = s.get("stop_now") if s.get("stop_now") is not None else s.get("sl")
                tick = 10 ** -int(symbol_spec(s["symbol"], settings)["digits"])
                if want is not None and abs(float(p["sl"]) - float(want)) > max(5 * tick, 0.02 * float(s.get("sl_dist") or 0)):
                    probs.append(_p(f"robot_sl:{sid}", f"SL ใน MT5 ของ {p.get('symbol')} = {p['sl']} แต่ระบบควรเป็น {want} ({sid})"))
        recent_closed = {c.get("signal_id") for c in pc.get("closed", [])}
        skipped = {x.get("signal_id"): x.get("reason") for x in pc.get("skipped") or []}
        for s in sigs:
            if s.get("outcome") != "open" or s.get("system_status") != "ACTIVE" or s.get("capped") or s["id"] in held \
                    or s["id"] in recent_closed:
                continue
            created = datetime.fromisoformat(s["created"])
            if now - created > timedelta(hours=2) and now - created < timedelta(days=3):
                why = skipped.get(s["id"])
                probs.append(_p(f"robot_missing:{s['id']}", f"สัญญาณ ACTIVE {s['symbol']} {s['tf']} ({s['id']}) ไม่มีออเดอร์ใน MT5 — "
                                + (f"โปรแกรมไม่เปิดเพราะ: {why}" if why else "ดูเหตุผลใน log ของคอม (เช่น ตลาดปิด/ราคาวิ่งไปแล้ว/lot ต่ำกว่าขั้นต่ำ)"), "info"))

    # swap booked where the research does not charge it
    done.append("swap_new")
    sw = (pc.get("swap_watch") or {}).get("symbols") or []
    fresh = [x for x in sw if x in SYMBOLS and swap_mode(settings, x) != "charged"]
    if fresh:
        probs.append(_p("swap_new", f"MT5 หัก swap ใน {', '.join(fresh)} แต่การทดสอบยังคิดแบบไม่หัก — คอมจะส่งไฟล์แจ้งและสั่งวิจัยใหม่"))

    # scanner errors (cloud + PC)
    done.append("scan_errors")
    errs = (pc.get("scan") or {}).get("errors", []) if online else []
    errs = list(errs) + list(scanner.STATE.get("errors") or [])
    if errs:
        probs.append(_p("scan_errors", f"สแกนไม่สำเร็จบางระบบ ({len(errs)}): {'; '.join(map(str, errs[:3]))}"))

    # research sanity
    done.append("research_bad")
    lat = research.latest()
    bad = 0
    for r in lat.get("symbols", {}).values():
        for c in r.get("candidates", []):
            o = c.get("oos_sel") or c.get("oos") or {}
            for k in ("t_month", "expectancy"):
                x = o.get(k)
                if x is not None and (not math.isfinite(float(x)) or abs(float(x)) > 50):
                    bad += 1
    if bad:
        probs.append(_p("research_bad", f"ผลวิจัยมีค่าผิดปกติ {bad} รายการ (NaN / ค่าเกินจริง)"))
    try:
        if lat.get("generated") and pd.Timestamp(lat["generated"]) < research.research_cutoff(settings):
            probs.append(_p("research_old", "ผลวิจัยเก่ากว่ารอบตัดข้อมูลปัจจุบัน — รอบวิจัยประจำปีอาจไม่ได้รัน"))
    except Exception:
        pass

    # kill switch (information)
    done.append("auto_off")
    for k, v in scanner.auto_off().items():
        probs.append(_p(f"auto_off:{k}", f"ระบบ {k} ถูกหยุดอัตโนมัติ (ขาดทุนจริง {v.get('dd_r')}R เกิน {v.get('limit_r')}R)", "info"))
    return probs, done


def run(notify_new: bool = True) -> dict:
    from . import notify
    now = datetime.now(timezone.utc)
    try:
        probs, done = checks(now)
    except Exception as e:
        log.exception("health check failed")
        probs, done = [_p("health_error", f"ตัวตรวจสุขภาพเองล้มเหลว: {e!r}"[:200])], []
    old = read_json(OUT, {})
    pushed = {k: v for k, v in (old.get("pushed") or {}).items() if any(p["key"] == k for p in probs)}
    new = []
    for p in probs:
        last = pushed.get(p["key"])
        if p["level"] != "info" and (not last or now - datetime.fromisoformat(last) > REPUSH):
            new.append(p)
            pushed[p["key"]] = now.isoformat()
    if notify_new and new:
        try:
            notify.send(f"🩺 ตรวจระบบพบ {len(new)} เรื่อง", "\n".join("• " + p["text"] for p in new[:6]), priority=4, tags=["warning"])
        except Exception as e:
            log.warning("health push failed: %s", e)
    out = {"checked_at": now.isoformat(), "checks": done, "problems": probs, "pushed": pushed}
    write_json(OUT, out)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(run(notify_new=False), ensure_ascii=False, indent=1)[:3000])
