"""Plain-language documentation of every strategy (Thai): indicators used and the exact entry conditions.
Shown on the Systems page ("รายละเอียดระบบ" / "รายชื่อระบบ"). Keep in sync with app/strategies.py —
tests/check_strategies.py fails if a strategy has no entry here.

Every system shares the same exits: SL = sl_atr x ATR14 from the entry, TP = rr x the SL distance,
or an opposite signal (close at the next bar's open and reverse). Signals are read on the bar close
and entered at the next bar's open.
"""
from __future__ import annotations

D: dict[str, tuple[list[str], str, str]] = {
    "donchian_trend": (["Donchian Channel (lookback แท่ง)", "EMA200"],
                       "ราคาปิดทะลุ High สูงสุดของ lookback แท่งก่อนหน้า (แท่งแรกที่ทะลุ) และปิดเหนือ EMA200",
                       "ราคาปิดหลุด Low ต่ำสุดของ lookback แท่งก่อนหน้า และปิดใต้ EMA200"),
    "ema_cross": (["EMA เร็ว (fast)", "EMA ช้า (slow)"], "EMA เร็วตัดขึ้นเหนือ EMA ช้า", "EMA เร็วตัดลงใต้ EMA ช้า"),
    "rsi_reversion": (["RSI (rsi_len)", "EMA200"], "RSI ลงต่ำกว่าระดับ oversold (rsi_level) ขณะราคาอยู่เหนือ EMA200 — ซื้อย่อในขาขึ้น",
                      "RSI ขึ้นเหนือ 100 − rsi_level ขณะราคาอยู่ใต้ EMA200 — ขายเด้งในขาลง"),
    "bb_reversion": (["Bollinger Bands (bb_len, bb_k)"], "ราคาปิดหลุดแบนด์ล่าง (แท่งแรก) — คาดว่ากลับเข้าหาเส้นกลาง", "ราคาปิดทะลุแบนด์บน (แท่งแรก)"),
    "tsmom": (["Rate of Change (lookback แท่ง)"], "ผลตอบแทนย้อนหลัง lookback แท่งเปลี่ยนจากลบเป็นบวก", "ผลตอบแทนย้อนหลังเปลี่ยนจากบวกเป็นลบ"),
    "adx_trend": (["ADX (14)", "+DI / −DI (14)"], "+DI ตัดขึ้นเหนือ −DI ขณะ ADX > adx_min", "−DI ตัดขึ้นเหนือ +DI ขณะ ADX > adx_min"),
    "squeeze_breakout": (["Bollinger Bands (bb_len, 2)", "Keltner Channel (bb_len, kc_mult × ATR)"],
                         "แท่งก่อนหน้า Bollinger อยู่ในกรอบ Keltner (บีบตัว) แล้วแท่งนี้หลุดการบีบตัวและปิดเหนือแบนด์บน",
                         "หลุดการบีบตัวและปิดใต้แบนด์ล่าง"),
    "session_breakout": (["กรอบราคาเอเชีย 00:00 → range_end (UTC)"],
                         "ภายใน 5 ชม. หลัง range_end ราคาปิดเหนือ High ของกรอบเอเชีย (ครั้งแรกของวัน)", "ราคาปิดใต้ Low ของกรอบเอเชีย (ครั้งแรกของวัน)"),
    "ema_pullback": (["EMA เร็ว (fast)", "EMA ช้า (slow)"],
                     "EMA เร็ว > EMA ช้า และราคาเหนือ EMA ช้า · Low แตะ EMA เร็วแล้วปิดกลับเหนือ เป็นแท่งเขียว",
                     "EMA เร็ว < EMA ช้า และราคาใต้ EMA ช้า · High แตะ EMA เร็วแล้วปิดกลับใต้ เป็นแท่งแดง"),
    "range_fade": (["Keltner Channel (20, kc_mult × ATR20)", "ADX (14)"], "ADX < adx_max (ไม่มีเทรนด์) และราคาปิดหลุดกรอบล่าง Keltner",
                   "ADX < adx_max และราคาปิดทะลุกรอบบน Keltner"),
    "macd_trend": (["MACD (12, 26, signal)", "EMA แนวโน้ม (trend_len)"], "MACD ตัดขึ้นเหนือเส้น signal และราคาปิดเหนือ EMA แนวโน้ม",
                   "MACD ตัดลงใต้เส้น signal และราคาปิดใต้ EMA แนวโน้ม"),
    "supertrend": (["Supertrend (10, st_mult × ATR)"], "Supertrend กลับทิศเป็นขาขึ้น (ราคาปิดทะลุเส้นด้านบน)", "Supertrend กลับทิศเป็นขาลง"),
    "rsi_momentum": (["RSI (14)", "EMA200"], "RSI ตัดขึ้นเหนือ rsi_level และราคาปิดเหนือ EMA200", "RSI ตัดลงใต้ 100 − rsi_level และราคาปิดใต้ EMA200"),
    "ichimoku_tk": (["Tenkan-sen (tenkan)", "Kijun-sen (kijun)", "เมฆ Senkou A / B"],
                    "Tenkan ตัดขึ้นเหนือ Kijun และราคาปิดเหนือเมฆ", "Tenkan ตัดลงใต้ Kijun และราคาปิดใต้เมฆ"),
    "inside_bar": (["Inside bar", "EMA แนวโน้ม (ema_len)"], "แท่งก่อนหน้าอยู่ในกรอบแท่งแม่ แล้วแท่งนี้ปิดทะลุ High ของแท่งแม่ และอยู่เหนือ EMA",
                   "ปิดหลุด Low ของแท่งแม่ และอยู่ใต้ EMA"),
    "pin_bar": (["Pin bar", "จุดต่ำ/สูงสุดของ lookback แท่ง"], "ไส้ล่าง ≥ 60% ของแท่ง ปิดในส่วนบน 1/3 และทำ Low ต่ำสุดของ lookback แท่ง",
                "ไส้บน ≥ 60% ของแท่ง ปิดในส่วนล่าง 1/3 และทำ High สูงสุดของ lookback แท่ง"),
    "stoch_pullback": (["Stochastic %K (14, 3)", "EMA200"], "%K ตัดขึ้นเหนือ stoch_level และราคาเหนือ EMA200", "%K ตัดลงใต้ 100 − stoch_level และราคาใต้ EMA200"),
    "zscore_reversion": (["SMA (z_len)", "ส่วนเบี่ยงเบนมาตรฐาน (z_len)"], "Z-score กลับขึ้นเหนือ −z_k (หลังจากต่ำกว่า)", "Z-score กลับลงใต้ +z_k (หลังจากสูงกว่า)"),
    "prev_day_breakout": (["High / Low ของเมื่อวาน", "EMA แนวโน้ม (trend_len)"], "ราคาปิดทะลุ High เมื่อวาน (ครั้งแรกของวัน) และอยู่เหนือ EMA",
                          "ราคาปิดหลุด Low เมื่อวาน (ครั้งแรกของวัน) และอยู่ใต้ EMA"),
    "aroon_trend": (["Aroon Up / Down (aroon_len)"], "Aroon Up ตัดขึ้นเหนือ Aroon Down และ Up ≥ 70", "Aroon Down ตัดขึ้นเหนือ Aroon Up และ Down ≥ 70"),
    "hma_trend": (["Hull Moving Average (hma_len)"], "ความชันของ HMA เปลี่ยนจากลงเป็นขึ้น", "ความชันของ HMA เปลี่ยนจากขึ้นเป็นลง"),
    "kama_cross": (["Kaufman Adaptive MA (er_len, 2, 30)"], "ราคาปิดตัดขึ้นเหนือ KAMA", "ราคาปิดตัดลงใต้ KAMA"),
    "tema_cross": (["TEMA เร็ว (fast)", "TEMA ช้า (slow)"], "TEMA เร็วตัดขึ้นเหนือ TEMA ช้า", "TEMA เร็วตัดลงใต้ TEMA ช้า"),
    "vortex": (["Vortex VI+ / VI− (vi_len)"], "VI+ ตัดขึ้นเหนือ VI−", "VI− ตัดขึ้นเหนือ VI+"),
    "cci_trend": (["CCI (20)"], "CCI ตัดขึ้นเหนือ +cci_level", "CCI ตัดลงใต้ −cci_level"),
    "trix_cross": (["TRIX (trix_len)", "เส้นสัญญาณ EMA9 ของ TRIX"], "TRIX ตัดขึ้นเหนือเส้นสัญญาณ ขณะ TRIX ยังต่ำกว่า 0", "TRIX ตัดลงใต้เส้นสัญญาณ ขณะ TRIX ยังสูงกว่า 0"),
    "psar_flip": (["Parabolic SAR (psar_step, max 0.2)", "EMA200"], "SAR กลับไปอยู่ใต้ราคา (ขาขึ้น) และราคาเหนือ EMA200", "SAR กลับไปอยู่เหนือราคา และราคาใต้ EMA200"),
    "keltner_breakout": (["Keltner Channel (20, kc_mult × ATR20)", "EMA200"], "ราคาปิดทะลุกรอบบน Keltner และเหนือ EMA200", "ราคาปิดหลุดกรอบล่าง Keltner และใต้ EMA200"),
    "bb_breakout": (["Bollinger Bands (20, bb_k)", "ความกว้างแบนด์เทียบค่าเฉลี่ย 50 แท่ง"],
                    "ราคาปิดทะลุแบนด์บน ขณะความกว้างแบนด์มากกว่าค่าเฉลี่ย (กำลังขยาย)", "ราคาปิดหลุดแบนด์ล่าง ขณะแบนด์กำลังขยาย"),
    "linreg_slope": (["ความชันเส้นถดถอยเชิงเส้น (lr_len)"], "ความชันเปลี่ยนจาก ≤ 0 เป็นบวก", "ความชันเปลี่ยนจาก ≥ 0 เป็นลบ"),
    "elder_impulse": (["EMA13", "MACD Histogram (12, 26, 9)", "EMA แนวโน้ม (trend_len)"],
                      "แท่งแรกที่ EMA13 และ Histogram ขึ้นพร้อมกัน (Impulse เขียว) และราคาเหนือ EMA แนวโน้ม", "แท่งแรกที่ทั้งคู่ลงพร้อมกัน (Impulse แดง) และราคาใต้ EMA แนวโน้ม"),
    "ema_alignment": (["EMA20", "EMA กลาง (mid)", "EMA200"], "แท่งแรกที่ EMA20 > EMA กลาง > EMA200", "แท่งแรกที่ EMA20 < EMA กลาง < EMA200"),
    "heikin_ashi": (["Heikin-Ashi", "EMA แนวโน้ม (trend_len)"], "แท่ง HA เขียวไม่มีไส้ล่าง แท่งแรก และราคาเหนือ EMA", "แท่ง HA แดงไม่มีไส้บน แท่งแรก และราคาใต้ EMA"),
    "awesome_osc": (["Awesome Oscillator (SMA5 − SMA34 ของราคากลาง)", "EMA แนวโน้ม (trend_len)"], "AO ตัดขึ้นเหนือ 0 และราคาเหนือ EMA", "AO ตัดลงใต้ 0 และราคาใต้ EMA"),
    "ultimate_osc": (["Ultimate Oscillator (7, 14, 28)", "EMA200"], "UO ตัดขึ้นเหนือ uo_level และราคาเหนือ EMA200", "UO ตัดลงใต้ 100 − uo_level และราคาใต้ EMA200"),
    "cci_reversion": (["CCI (20)"], "CCI ตัดกลับขึ้นเหนือ −100 หลังจากลงต่ำกว่า −cci_extreme ภายใน 5 แท่ง", "CCI ตัดกลับลงใต้ +100 หลังจากขึ้นเกิน +cci_extreme ภายใน 5 แท่ง"),
    "keltner_pullback": (["Keltner Channel (20, kc_mult × ATR20)", "EMA50", "EMA200"], "EMA50 > EMA200 · Low แตะกรอบล่าง Keltner แล้วปิดกลับเหนือกรอบ",
                         "EMA50 < EMA200 · High แตะกรอบบน Keltner แล้วปิดกลับใต้กรอบ"),
    "three_bar_pullback": (["SMA200"], "ปิดลงติดกัน n_bars แท่ง แล้วแท่งนี้ปิดขึ้น ขณะราคาเหนือ SMA200", "ปิดขึ้นติดกัน n_bars แท่ง แล้วแท่งนี้ปิดลง ขณะราคาใต้ SMA200"),
    "nr_breakout": (["แท่งช่วงแคบที่สุด (NR) ของ nr_len แท่ง"], "แท่งก่อนหน้าแคบที่สุดใน nr_len แท่ง แล้วแท่งนี้ปิดทะลุ High ของแท่งนั้น", "แท่งนี้ปิดหลุด Low ของแท่ง NR"),
    "engulfing_reversal": (["แท่งกลืนกิน (Engulfing)", "จุดต่ำ/สูงสุดของ lookback แท่ง"], "Bullish engulfing ที่ทำ Low ต่ำสุดของ lookback แท่ง",
                           "Bearish engulfing ที่ทำ High สูงสุดของ lookback แท่ง"),
    "donchian_fade": (["กรอบสูง/ต่ำของ lookback แท่ง", "ADX (14)"], "ADX < 20 · Low แตะจุดต่ำของกรอบแล้วปิดกลับเหนือ", "ADX < 20 · High แตะจุดสูงของกรอบแล้วปิดกลับใต้"),
    "rsi_divergence": (["RSI (14)", "จุดต่ำ/สูงของ lookback แท่ง"], "ทำ Low ใหม่ของ lookback แท่ง แต่ RSI สูงกว่าตอน Low ครั้งก่อน (> 2 จุด) และแท่งนี้ปิดเขียว",
                       "ทำ High ใหม่ แต่ RSI ต่ำกว่าตอน High ครั้งก่อน และแท่งนี้ปิดแดง"),
    "turn_of_month": (["ปฏิทินวันทำการ"], "แท่งแรกของวันที่อยู่ก่อนวันทำการสุดท้ายของเดือน days_before วัน (Long อย่างเดียว)", "ไม่มี (ระบบนี้ Long อย่างเดียว)"),
    "london_fix_fade": (["ราคาเปิดช่วงลอนดอน (07:00 UTC)", "ATR (14)"], "แท่งที่ปิด 16:00 UTC ราคาลงจากช่วงเปิดลอนดอนเกิน move_atr × ATR (ปรับตามจำนวนชั่วโมง) → ซื้อสวน",
                        "ราคาขึ้นจากช่วงเปิดลอนดอนเกินเกณฑ์ → ขายสวน"),
    "ny_open_breakout": (["กรอบช่วงเปิดนิวยอร์ก 13:00 UTC + range_bars ชม."], "ราคาปิดเหนือ High ของกรอบ (ครั้งแรกของวัน ก่อน 20:00 UTC)", "ราคาปิดใต้ Low ของกรอบ (ครั้งแรกของวัน)"),
    "weekly_breakout": (["High / Low สัปดาห์ก่อน", "EMA แนวโน้ม (trend_len)"], "ราคาปิดทะลุ High สัปดาห์ก่อน (ครั้งแรกของสัปดาห์) และเหนือ EMA",
                        "ราคาปิดหลุด Low สัปดาห์ก่อน (ครั้งแรกของสัปดาห์) และใต้ EMA"),
    "pivot_bounce": (["Pivot S1 / R1 จาก H/L/C เมื่อวาน", "EMA แนวโน้ม (trend_len)"], "Low แตะ S1 แล้วปิดกลับเหนือ S1 และราคาเหนือ EMA", "High แตะ R1 แล้วปิดกลับใต้ R1 และราคาใต้ EMA"),
    "fractal_breakout": (["Williams Fractals (5 แท่ง ยืนยันแล้ว)", "EMA แนวโน้ม (trend_len)"], "ราคาปิดตัดขึ้นเหนือ Fractal ขาขึ้นล่าสุด และเหนือ EMA",
                         "ราคาปิดตัดลงใต้ Fractal ขาลงล่าสุด และใต้ EMA"),
    "alligator": (["Alligator: Jaw / Teeth / Lips (SMMA 13/8/5 เลื่อน 8/5/3 แท่ง × scale)"], "แท่งแรกที่ Lips > Teeth > Jaw และราคาปิดเหนือ Lips",
                  "แท่งแรกที่ Lips < Teeth < Jaw และราคาปิดใต้ Lips"),
    "elder_ray": (["EMA (ema_len)", "Bull Power = High − EMA", "Bear Power = Low − EMA"],
                  "EMA กำลังขึ้น · Bear Power ยังติดลบแต่สูงขึ้น · Bull Power > 0 · ราคาปิดตัดขึ้นเหนือ EMA",
                  "EMA กำลังลง · Bull Power ยังเป็นบวกแต่ลดลง · Bear Power < 0 · ราคาปิดตัดลงใต้ EMA"),
}

COMMON_EXITS = ("ทุกระบบออกด้วยกติกาเดียวกัน: SL = sl_atr × ATR14 จากราคาเข้า · TP = rr × ระยะ SL · "
                "หรือมีสัญญาณตรงข้าม → ปิดที่ราคาเปิดแท่งถัดไปแล้วกลับทิศ · อ่านสัญญาณตอนแท่งปิด เข้าที่ราคาเปิดแท่งถัดไป")


def doc(strategy_id: str) -> dict:
    ind, long_, short = D[strategy_id]
    return {"indicators": ind, "long": long_, "short": short, "exits": COMMON_EXITS}
