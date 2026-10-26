"""Robustness tests for the system chosen by walk-forward.

Each test re-creates the stitched out-of-sample trades (every fold trades the parameters it picked
in-sample) under a perturbation and asks whether the edge survives. Nothing here selects or tunes
parameters — it only tries to break what walk-forward already chose.

  wf_windows   other IS/OOS window lengths still give a positive OOS expectancy
  cost_x2      double spread + slippage + commission
  delay_1      enter one bar late
  noise        small random noise on every OHLC price (indicators and fills both move)
  long_short   neither direction is a clear loser
  time         most half-years are profitable
  top_trades   still positive without the best 5% of trades (not living on a few outliers)
  plateau      most neighbouring parameter sets are profitable over the full history
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ta
from .engine import run
from .walkforward import walk_forward

WF_WINDOWS = [(6, 2), (9, 3), (12, 3), (18, 3), (24, 6)]


def _stitched(df: pd.DataFrame, wf: dict, spec: dict, tf: str, **kw) -> list[dict]:
    """OOS trades of the walk-forward folds, re-run on (possibly perturbed) data / rules."""
    cache: dict[int, list[dict]] = {}
    out = []
    for f in wf["folds"]:
        if f.get("combo") is None:
            continue
        if f["combo"] not in cache:
            cache[f["combo"]] = run(df, wf["strategy"], f["params"], spec, tf, **kw)["trades"]
        lo, hi = pd.Timestamp(f["oos_start"]), pd.Timestamp(f["oos_end"])
        out.extend(t for t in cache[f["combo"]] if lo <= t["entry_time"] < hi)
    return out


def _exp(trades) -> float | None:
    return float(np.mean([t["r"] for t in trades])) if trades else None


def _noisy(df: pd.DataFrame, rng: np.random.Generator, frac: float) -> pd.DataFrame:
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = ta.atr(h, l, c, 14)
    a = np.where(np.isfinite(a), a, np.nanmedian(a))
    px = np.stack([o, h, l, c]) + rng.normal(0, 1, (4, len(o))) * a * frac
    out = df.copy()
    out["open"], out["close"] = px[0], px[3]
    out["high"] = px.max(axis=0)
    out["low"] = px.min(axis=0)
    return out


def _test(key, name, value, need, ok, note=""):
    return {"key": key, "name": name, "value": value, "need": need, "pass": None if ok is None else bool(ok), "note": note}


def robustness(df: pd.DataFrame, wf: dict, spec: dict, tf: str, cfg: dict, noise_sims: int = 20, seed: int = 3) -> dict:
    base = wf["oos_trades"]
    tests = []
    if len(base) < 10:
        return {"score": None, "grade": "ไม่พอประเมิน", "tests": [], "passed": 0, "applicable": 0}

    # 1. walk-forward window lengths
    rows = []
    for is_m, oos_m in WF_WINDOWS:
        if (is_m, oos_m) == (cfg.get("is_months", 12), cfg.get("oos_months", 3)):
            o = wf["oos"]
        else:
            o = walk_forward(df, wf["strategy"], spec, tf, {**cfg, "is_months": is_m, "oos_months": oos_m})["oos"]
        if o["trades"] >= 10:
            rows.append({"is": is_m, "oos": oos_m, "trades": o["trades"], "expectancy": o["expectancy"], "t": o["t_stat"]})
    pos = sum(r["expectancy"] > 0 for r in rows)
    tests.append(_test("wf_windows", "WF หลายขนาดหน้าต่าง (IS/OOS)", f"{pos}/{len(rows)}", "≥ 75% เป็นบวก",
                       pos / len(rows) >= 0.75 if len(rows) >= 2 else None,
                       " · ".join(f"{r['is']}/{r['oos']}m: {r['expectancy']:+.3f}R" for r in rows)))

    # 2. costs x2: pay the round-trip cost once more, straight out of every trade's R
    e = float(np.mean([t["r"] - t["cost_r"] for t in base]))
    tests.append(_test("cost_x2", "ต้นทุน ×2 (spread+slippage+comm.)", round(e, 3), "> 0R", e > 0,
                       f"ต้นทุนเฉลี่ย {np.mean([t['cost_r'] for t in base]):.3f}R/ไม้"))

    # 3. late entry
    e = _exp(_stitched(df, wf, spec, tf, delay=1))
    tests.append(_test("delay_1", "เข้าช้าไป 1 แท่ง", None if e is None else round(e, 3), "> 0R", e is not None and e > 0))

    # 4. price noise
    rng = np.random.default_rng(seed)
    exps = [x for x in (_exp(_stitched(_noisy(df, rng, 0.1), wf, spec, tf)) for _ in range(noise_sims)) if x is not None]
    share = float(np.mean(np.array(exps) > 0) * 100) if exps else 0.0
    tests.append(_test("noise", f"Noise ในราคา ±0.1 ATR ({noise_sims} รอบ)", round(share, 0), "≥ 70% ของรอบยังกำไร",
                       share >= 70, f"expectancy เฉลี่ย {np.mean(exps):+.3f}R" if exps else ""))

    # 5. long vs short
    r = np.array([t["r"] for t in base])
    d = np.array([t["dir"] for t in base])
    sides = {k: r[d == v] for k, v in (("L", 1), ("S", -1))}
    bad = [k for k, x in sides.items() if len(x) >= 10 and x.mean() < -0.05]
    tests.append(_test("long_short", "ทั้งฝั่ง Long และ Short", " / ".join(
        f"{k} {x.mean():+.2f}R ({len(x)})" for k, x in sides.items() if len(x)), "ไม่มีฝั่งที่ขาดทุนชัดเจน", not bad))

    # 6. consistency over time (half-years with >= 3 trades)
    t = pd.DatetimeIndex([x["exit_time"] for x in base])
    buckets = pd.Series(r, index=t).groupby([t.year, (t.month - 1) // 6]).agg(["sum", "size"])
    buckets = buckets[buckets["size"] >= 3]
    pos = int((buckets["sum"] > 0).sum())
    tests.append(_test("time", "กำไรสม่ำเสมอรายครึ่งปี", f"{pos}/{len(buckets)}", "≥ 60% ของครึ่งปี",
                       pos / len(buckets) >= 0.6 if len(buckets) >= 2 else None))

    # 7. not living on outliers
    k = max(1, int(round(len(r) * 0.05)))
    trimmed = np.sort(r)[:-k]
    tests.append(_test("top_trades", f"ตัด {k} ไม้ที่ดีที่สุด (5%) ออก", round(float(trimmed.mean()), 3), "> 0R", trimmed.mean() > 0))

    # 8. parameter neighbourhood
    tests.append(_test("plateau", "พารามิเตอร์ข้างเคียงก็กำไร", wf["plateau"], "≥ 0.5", wf["plateau"] >= 0.5))

    judged = [x for x in tests if x["pass"] is not None]
    passed = sum(x["pass"] for x in judged)
    score = round(passed / len(judged) * 100) if judged else None
    grade = "ไม่พอประเมิน" if score is None else "แข็งแรง" if score >= 80 else "พอใช้" if score >= 60 else "เปราะบาง"
    return {"score": score, "grade": grade, "passed": passed, "applicable": len(judged), "tests": tests, "wf_windows": rows}
