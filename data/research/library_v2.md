# คลังระบบ v2 — ลงทะเบียนล่วงหน้า (11 พ.ย. 2026)

เขียนและ commit ไฟล์นี้ **ก่อน** รันการวิจัยด้วยคลังใหม่ — กติกาทุกข้อถูกกำหนดก่อนเห็นผล

## กติกา

1. **7 แนวคิด แนวคิดละ 100 ระบบ = 700 ระบบ** (ผู้ใช้กำหนด 11 พ.ย. 2026)
2. **แนวคิดที่มีไม่ถึง 100:** เติมด้วยระบบ ตัวกระตุ้น × ตัวกรอง (`app/strategies_v2.py`) เรียงตามลำดับที่กำหนดไว้: วนตัวกรองทีละตัว แล้วใส่ทุกตัวกระตุ้น จนครบ 100 · ระบบที่ไม่ผ่านชุดตรวจโค้ด (`tests/check_strategies`: ไม่แอบดูอนาคต, ผลบนราคาสุ่ม ≈ 0R, มีสัญญาณ) ถูกตัดก่อนวิจัยและแทนด้วยคู่ถัดไปในลำดับเดียวกัน (9 ระบบ: pa_doji_year, pa_mss_year, pa_out_year, se_asia_m3, se_asia_range, se_ny_year, vb_dq_wild, vb_hv_m3, vb_rx_year)
3. **แนวคิดที่เกิน 100 (ย่อตัว 275, สวนกลับ 118):** คัดด้วยความหลากหลายของจังหวะสัญญาณเท่านั้น ไม่ดูกำไรขาดทุนหรือผลวิจัยใด ๆ (`tools/select_library.py`): สัญญาณด้วยพารามิเตอร์ตั้งต้นบน H4 ของ EURUSD, USDJPY, XAUUSD, US500, BTCUSD, USOIL (ก.ค. 2019 – ก.ย. 2026) รวมทิศรายวัน → เลือกแบบโลภให้ correlation สูงสุดกับระบบที่เลือกแล้วต่ำที่สุด จนครบ 100
   - ย่อตัว: correlation เฉลี่ย 0.217 → 0.154 · สวนกลับ: 0.219 → 0.207
4. **วิธีปิด 3 แบบในทุกระบบ** เป็นตัวเลือกในตารางพารามิเตอร์ (walk-forward เลือกในช่วง in-sample):
   - ① SL + TP: TP = rr × ระยะ SL (rr ตามแนวคิด) ปิดเมื่อชนฝั่งใดฝั่งหนึ่ง ไม่สนสัญญาณกลับทิศระหว่างถือ
   - ② SL + สัญญาณกลับทิศ: ไม่มี TP · สัญญาณตรงข้ามปิดที่ราคาเปิดแท่งถัดไปแล้วเปิดฝั่งตรงข้ามทันที
   - ③ เหมือน ② แต่ SL เลื่อนตาม 2 หรือ 3 × ATR14 จากราคาดีสุดของแท่งที่ปิดแล้ว (เลื่อนเข้าอย่างเดียว)
   - SL = 1.5 / 2.5 × ATR14 (ระบบเดิมใช้ค่าเดิมของตัวเอง) → ราว 20 ชุดต่อระบบ
5. **เกณฑ์กันโชค:** ผลย้อนหลังนับทุกระบบที่เคยทดสอบบนข้อมูลชุดนี้ (บันทึกการทดสอบถาวร รวมระบบที่ถูกคัดออก 210 ระบบ) · วิธีปิด 3 แบบอยู่ในระบบเดียว = 1 การทดสอบ
6. **ผลเทรดจริงหลังวันล็อกคลัง (11 พ.ย. 2026):** กฎเลื่อนขั้น TRIAL → ACTIVE นับเฉพาะ TRIAL ที่ลงทะเบียนจากคลังใหม่ตั้งแต่วันนี้
6b. **แก้ไข 11 พ.ย. 2026 (หลังรันวิจัย ก่อนดูผล):** `mr_stochf_year` ไม่ผ่านชุดตรวจโค้ดเมื่อใช้ตารางวิธีปิดใหม่ (ผลบนราคาสุ่ม +0.085R เกินเกณฑ์ 0.08R) จึงถูกถอดออก และแทนด้วยระบบถัดไปในลำดับความหลากหลายเดียวกัน คือ `mr_rev_st` (จะถูกทดสอบในการวิจัยรอบถัดไป)
7. เกณฑ์ 9 ด่าน หน้าต่าง walk-forward และข้อสอบปิดผนึก 12 เดือน **ไม่เปลี่ยน**

## ตามเทรนด์ (Trend following) — 100 ระบบ

| # | id | ชื่อ | อินดิเคเตอร์ | ชุดพารามิเตอร์ |
|---|---|---|---|---|
| 1 | `donchian_trend` | Donchian Breakout + EMA200 | Donchian Channel (lookback แท่ง) · EMA200 | 30 |
| 2 | `ema_cross` | EMA Crossover | EMA เร็ว (fast) · EMA ช้า (slow) | 40 |
| 11 | `macd_trend` | MACD Cross + Trend Filter | MACD (12, 26, signal) · EMA แนวโน้ม (trend_len) | 20 |
| 12 | `supertrend` | Supertrend Flip | Supertrend (10, st_mult × ATR) | 20 |
| 14 | `ichimoku_tk` | Ichimoku TK Cross + Cloud | Tenkan-sen (tenkan) · Kijun-sen (kijun) · เมฆ Senkou A / B | 10 |
| 20 | `aroon_trend` | Aroon Crossover | Aroon Up / Down (aroon_len) | 20 |
| 21 | `hma_trend` | Hull MA Turn | Hull Moving Average (hma_len) | 20 |
| 22 | `kama_cross` | KAMA Cross | Kaufman Adaptive MA (er_len, 2, 30) | 20 |
| 23 | `tema_cross` | TEMA Crossover | TEMA เร็ว (fast) · TEMA ช้า (slow) | 20 |
| 24 | `vortex` | Vortex Indicator Cross | Vortex VI+ / VI− (vi_len) | 20 |
| 27 | `psar_flip` | Parabolic SAR Flip | Parabolic SAR (psar_step, max 0.2) · EMA200 | 20 |
| 30 | `linreg_slope` | Linear Regression Slope | ความชันเส้นถดถอยเชิงเส้น (lr_len) | 20 |
| 32 | `ema_alignment` | EMA 20/50/200 Alignment | EMA20 · EMA กลาง (mid) · EMA200 | 20 |
| 33 | `heikin_ashi` | Heikin-Ashi Trend Start | Heikin-Ashi · EMA แนวโน้ม (trend_len) | 20 |
| 48 | `fractal_breakout` | Williams Fractal Breakout | Williams Fractals (5 แท่ง ยืนยันแล้ว) · EMA แนวโน้ม (trend_len) | 20 |
| 49 | `alligator` | Williams Alligator Awakening | Alligator: Jaw / Teeth / Lips (SMMA 13/8/5 เลื่อน 8/5/3 แท่ง × scale) | 20 |
| 51 | `ewmac` | EWMAC Multi-speed Trend | EMA 16/64, 32/128, 64/256 (ตามชุด speeds) · ส่วนเบี่ยงเบนมาตรฐานของการเปลี่ยนแปลงราคา 36 แท่ง | 24 |
| 52 | `tsmom_blend` | Time-Series Momentum 1/3/12m | ผลตอบแทนย้อนหลัง 1 / 3 / 12 เดือน (mode 1 = เฉพาะ 3 / 12 เดือน) | 16 |
| 53 | `turtle` | Turtle Breakout (always in) | ช่องทาง Donchian N แท่ง (entry_n) | 16 |
| 54 | `faber_sma` | Faber 10-month SMA Timing | SMA ยาว months เดือน (≈ 22 แท่ง D1 ต่อเดือน) | 16 |
| 55 | `clenow_trend` | Clenow Trend (EMA50/100 + Breakout) | EMA50 · EMA100 · กรอบสูง/ต่ำ breakout_n แท่ง | 16 |
| 57 | `high52` | 52-Week High / Low Breakout | High / Low สูงสุด-ต่ำสุดรอบ months เดือน | 16 |
| 58 | `sma_cross` | SMA Crossover | SMA เร็ว (fast) · SMA ช้า (fast × mult) | 32 |
| 59 | `dema_cross` | DEMA Crossover | DEMA เร็ว (fast) · DEMA ช้า (3 × fast) | 16 |
| 60 | `price_ema` | Price vs EMA | EMA (n) | 16 |
| 61 | `golden_cross` | Golden / Death Cross | SMA ช้า (slow) · SMA เร็ว (slow ÷ 4) | 16 |
| 62 | `ema_ribbon` | EMA Ribbon (n / 2n / 4n) | EMA n / 2n / 4n | 16 |
| 63 | `ema_slope` | EMA Slope | EMA (n) · ความชันเทียบ k แท่งก่อน | 32 |
| 64 | `ma_envelope` | MA Envelope Breakout | SMA (n) · ซอง ± pct % | 32 |
| 65 | `mcginley` | McGinley Dynamic | McGinley Dynamic (n) | 16 |
| 66 | `t3_trend` | Tillson T3 Slope | Tillson T3 (n, v = 0.7) | 16 |
| 67 | `zlema_cross` | Zero-Lag EMA Crossover | Zero-Lag EMA เร็ว (fast) · Zero-Lag EMA ช้า (3 × fast) | 16 |
| 68 | `alma_trend` | ALMA Slope | ALMA (n, offset 0.85, sigma 6) | 16 |
| 69 | `hma_cross` | Hull MA Crossover | Hull MA เร็ว (fast) · Hull MA ช้า (3 × fast) | 16 |
| 70 | `mtf_ema` | Multi-Timeframe EMA | EMA ใหญ่ (days วัน) · EMA10 · EMA30 | 16 |
| 71 | `ma_stack_pullback` | EMA Stack Continuation | EMA เร็ว (fast) · EMA50 · EMA200 | 16 |
| 72 | `donchian_mid` | Donchian Midline | กึ่งกลางกรอบ Donchian n แท่งก่อนหน้า | 16 |
| 73 | `keltner_trend` | Keltner Channel Trend | Keltner Channel (n, mult × ATR) | 32 |
| 74 | `atr_bands` | ATR Bands Trend | SMA (n) · แถบ ± k × ATR(n) | 32 |
| 75 | `bb_trend` | Bollinger Band Trend | Bollinger Bands (n, k) | 32 |
| 76 | `chandelier` | Chandelier Trend | Chandelier: High สูงสุด n − k × ATR / Low ต่ำสุด n + k × ATR | 32 |
| 77 | `close_breakout` | Closing-Price Breakout | ราคาปิดสูงสุด / ต่ำสุด n แท่งก่อนหน้า | 16 |
| 78 | `linreg_channel` | Regression Channel Breakout | เส้นถดถอย n แท่ง ± k SD (ของแท่งก่อนหน้า) | 32 |
| 79 | `median_channel` | Median Channel Trend | Median ราคาปิด n แท่ง · ± k × ATR14 | 32 |
| 80 | `ichimoku_cloud` | Ichimoku Cloud Breakout | Ichimoku: Tenkan (base ÷ 3), Kijun (base), Senkou A/B (เลื่อน base แท่ง) | 16 |
| 81 | `gann_hilo` | Gann HiLo Activator | SMA ของ High (n) · SMA ของ Low (n) | 16 |
| 82 | `donchian_width` | Wide-Channel Breakout | กรอบ Donchian n แท่ง · ATR14 | 32 |
| 84 | `volatility_stop` | Wilder Volatility Stop | ATR (n) · ราคาปิดที่ดีที่สุดตั้งแต่เข้า | 32 |
| 85 | `renko_trend` | ATR Renko Trend | อิฐ Renko ขนาด box_atr × ATR14 | 32 |
| 86 | `ssl_channel` | SSL Channel | SMA ของ High (n) · SMA ของ Low (n) | 16 |
| 102 | `aroon_osc` | Aroon Oscillator Trend | Aroon Oscillator (n) = Aroon Up − Aroon Down | 32 |
| 107 | `adx_rising` | ADX Trend Start | ADX (14) · +DI / −DI (14) | 16 |
| 108 | `chop_break` | Choppiness Breakout | Choppiness Index (n) · EMA50 | 32 |
| 109 | `efficiency_trend` | Efficiency Ratio Trend | Kaufman Efficiency Ratio (n, มีเครื่องหมาย) | 32 |
| 110 | `rsq_trend` | R-Squared Trend | R² ของเส้นตรง n แท่ง · ความชันเส้นถดถอย | 32 |
| 111 | `slope_t` | Significant Slope | t-stat ของความชันเส้นถดถอย n แท่ง | 32 |
| 112 | `vhf_trend` | Vertical Horizontal Filter | Vertical Horizontal Filter (n) · EMA (n) | 32 |
| 113 | `tii_trend` | Trend Intensity Index | Trend Intensity Index (SMA n, นับ n÷2 แท่ง) | 16 |
| 114 | `er_kama` | Efficient KAMA Trend | KAMA (n) · Efficiency Ratio (n) | 32 |
| 119 | `atr_trailing` | ATR Trailing Line | เส้นตามหลังราคาปิด k × ATR14 | 16 |
| 121 | `higher_highs` | Higher Highs / Higher Lows | High และ Low ต่อเนื่อง n แท่ง · EMA50 | 16 |
| 122 | `swing_break` | Swing Point Breakout | Swing High / Low (ยืนยันข้างละ k แท่ง) · EMA100 | 16 |
| 123 | `ha_smoothed` | Smoothed Heikin-Ashi | Heikin-Ashi จาก OHLC เกลี่ย EMA n | 16 |
| 124 | `point_figure` | Point & Figure (3-box) | Point & Figure: กล่อง box_atr × ATR50, กลับทิศ 3 กล่อง | 16 |
| 521 | `tf_don_t200` | Donchian Breakout · above SMA200 | Donchian (n) · ราคาเหนือ SMA200 | 20 |
| 522 | `tf_emax_t200` | EMA Cross 1:4 · above SMA200 | EMA (n) · EMA (4n) · ราคาเหนือ SMA200 | 20 |
| 523 | `tf_stf_t200` | Supertrend Flip · above SMA200 | Supertrend (10, mult) · ราคาเหนือ SMA200 | 20 |
| 524 | `tf_lrs_t200` | Regression Slope Flip · above SMA200 | Linear regression slope (n) · ราคาเหนือ SMA200 | 20 |
| 525 | `tf_don_adx` | Donchian Breakout · ADX > 25 trend | Donchian (n) · ADX > 25 และ +DI > −DI | 20 |
| 526 | `tf_emax_adx` | EMA Cross 1:4 · ADX > 25 trend | EMA (n) · EMA (4n) · ADX > 25 และ +DI > −DI | 20 |
| 527 | `tf_stf_adx` | Supertrend Flip · ADX > 25 trend | Supertrend (10, mult) · ADX > 25 และ +DI > −DI | 20 |
| 528 | `tf_lrs_adx` | Regression Slope Flip · ADX > 25 trend | Linear regression slope (n) · ADX > 25 และ +DI > −DI | 20 |
| 529 | `tf_don_calm` | Donchian Breakout · calm volatility | Donchian (n) · ATR14 ต่ำกว่า ATR100 | 20 |
| 530 | `tf_emax_calm` | EMA Cross 1:4 · calm volatility | EMA (n) · EMA (4n) · ATR14 ต่ำกว่า ATR100 | 20 |
| 531 | `tf_stf_calm` | Supertrend Flip · calm volatility | Supertrend (10, mult) · ATR14 ต่ำกว่า ATR100 | 20 |
| 532 | `tf_lrs_calm` | Regression Slope Flip · calm volatility | Linear regression slope (n) · ATR14 ต่ำกว่า ATR100 | 20 |
| 533 | `tf_don_wild` | Donchian Breakout · high volatility | Donchian (n) · ATR14 สูงกว่า ATR100 | 20 |
| 534 | `tf_emax_wild` | EMA Cross 1:4 · high volatility | EMA (n) · EMA (4n) · ATR14 สูงกว่า ATR100 | 20 |
| 535 | `tf_stf_wild` | Supertrend Flip · high volatility | Supertrend (10, mult) · ATR14 สูงกว่า ATR100 | 20 |
| 536 | `tf_lrs_wild` | Regression Slope Flip · high volatility | Linear regression slope (n) · ATR14 สูงกว่า ATR100 | 20 |
| 537 | `tf_don_m3` | Donchian Breakout · 3-month momentum up | Donchian (n) · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 538 | `tf_emax_m3` | EMA Cross 1:4 · 3-month momentum up | EMA (n) · EMA (4n) · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 539 | `tf_stf_m3` | Supertrend Flip · 3-month momentum up | Supertrend (10, mult) · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 540 | `tf_lrs_m3` | Regression Slope Flip · 3-month momentum up | Linear regression slope (n) · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 541 | `tf_don_year` | Donchian Breakout · upper half of 12-month range | Donchian (n) · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 542 | `tf_emax_year` | EMA Cross 1:4 · upper half of 12-month range | EMA (n) · EMA (4n) · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 543 | `tf_stf_year` | Supertrend Flip · upper half of 12-month range | Supertrend (10, mult) · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 544 | `tf_lrs_year` | Regression Slope Flip · upper half of 12-month range | Linear regression slope (n) · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 545 | `tf_don_gold` | Donchian Breakout · Golden-cross regime | Donchian (n) · SMA50 เหนือ SMA200 | 20 |
| 546 | `tf_emax_gold` | EMA Cross 1:4 · Golden-cross regime | EMA (n) · EMA (4n) · SMA50 เหนือ SMA200 | 20 |
| 547 | `tf_stf_gold` | Supertrend Flip · Golden-cross regime | Supertrend (10, mult) · SMA50 เหนือ SMA200 | 20 |
| 548 | `tf_lrs_gold` | Regression Slope Flip · Golden-cross regime | Linear regression slope (n) · SMA50 เหนือ SMA200 | 20 |
| 549 | `tf_don_r50` | Donchian Breakout · RSI(50) > 50 | Donchian (n) · RSI(50) เหนือ 50 | 20 |
| 550 | `tf_emax_r50` | EMA Cross 1:4 · RSI(50) > 50 | EMA (n) · EMA (4n) · RSI(50) เหนือ 50 | 20 |
| 551 | `tf_stf_r50` | Supertrend Flip · RSI(50) > 50 | Supertrend (10, mult) · RSI(50) เหนือ 50 | 20 |
| 552 | `tf_lrs_r50` | Regression Slope Flip · RSI(50) > 50 | Linear regression slope (n) · RSI(50) เหนือ 50 | 20 |
| 553 | `tf_don_range` | Donchian Breakout · ADX < 20 | Donchian (n) · ADX ต่ำกว่า 20 | 20 |
| 554 | `tf_emax_range` | EMA Cross 1:4 · ADX < 20 | EMA (n) · EMA (4n) · ADX ต่ำกว่า 20 | 20 |
| 555 | `tf_stf_range` | Supertrend Flip · ADX < 20 | Supertrend (10, mult) · ADX ต่ำกว่า 20 | 20 |
| 556 | `tf_lrs_range` | Regression Slope Flip · ADX < 20 | Linear regression slope (n) · ADX ต่ำกว่า 20 | 20 |

## โมเมนตัม (Momentum) — 100 ระบบ

| # | id | ชื่อ | อินดิเคเตอร์ | ชุดพารามิเตอร์ |
|---|---|---|---|---|
| 13 | `rsi_momentum` | RSI Momentum Regime | RSI (14) · EMA200 | 20 |
| 25 | `cci_trend` | CCI Breakout | CCI (20) | 20 |
| 31 | `elder_impulse` | Elder Impulse System | EMA13 · MACD Histogram (12, 26, 9) · EMA แนวโน้ม (trend_len) | 20 |
| 34 | `awesome_osc` | Awesome Oscillator Zero Cross | Awesome Oscillator (SMA5 − SMA34 ของราคากลาง) · EMA แนวโน้ม (trend_len) | 20 |
| 87 | `roc_trend` | Rate-of-Change Trend | Rate of Change n แท่ง (เกลี่ย EMA5) | 16 |
| 88 | `tsi_cross` | True Strength Index | True Strength Index (long, 13) · เส้นสัญญาณ EMA7 | 16 |
| 89 | `kst` | Know Sure Thing | KST = ROC 10/15/20/30 (× scale) ถ่วงน้ำหนัก 1–4 · เส้นสัญญาณ SMA9 | 16 |
| 90 | `ppo_flip` | PPO Histogram Trend | PPO = (EMA fast − EMA 2×fast) ÷ EMA ช้า · Histogram = PPO − EMA9 | 16 |
| 91 | `cmo_trend` | Chande Momentum Trend | Chande Momentum Oscillator (n) | 32 |
| 92 | `rsi50_trend` | RSI 50-Line Trend | RSI (n) · EMA100 | 16 |
| 93 | `stoch_trend` | Stochastic Momentum Trend | Stochastic %K (n, เกลี่ย 3) · EMA100 | 32 |
| 94 | `willr_trend` | Williams %R Breakout | Williams %R (n) | 16 |
| 95 | `coppock` | Coppock Curve | Coppock = WMA(ROC ยาว + ROC สั้น) | 16 |
| 96 | `schaff_trend` | Schaff Trend Cycle | Schaff Trend Cycle (MACD fast / 2×fast, Stochastic 10 สองชั้น) | 16 |
| 97 | `fisher_trend` | Fisher Transform Trend | Fisher Transform ของราคากลาง (n) | 16 |
| 98 | `tsmom_vol` | Vol-Scaled Momentum | ผลตอบแทน log ย้อนหลัง months เดือน · ความผันผวน × √N | 32 |
| 99 | `dual_momentum` | Dual-Horizon Momentum | ผลตอบแทน short_m เดือน · ผลตอบแทน ¼ ของช่วงนั้น | 16 |
| 100 | `macd_zero` | MACD Zero-Line | MACD = EMA fast − EMA 2.2×fast | 16 |
| 101 | `macd_filtered` | MACD Signal in Trend | MACD (fast, 2.2×fast) · Signal EMA9 | 16 |
| 103 | `rsi_breakout` | RSI Momentum Breakout | RSI (n) | 32 |
| 104 | `rvi_trend` | Relative Vigor Index | Relative Vigor Index (n) · Signal SMA4 | 16 |
| 105 | `pfe_trend` | Polarized Fractal Efficiency | Polarized Fractal Efficiency (n, เกลี่ย EMA5) | 32 |
| 106 | `momentum_accel` | Accelerating Momentum | ROC n แท่ง · การเปลี่ยนแปลงของ ROC (ความเร่ง) | 16 |
| 126 | `weekly_momentum` | Weekly Momentum | ผลตอบแทนย้อนหลัง weeks สัปดาห์ | 16 |
| 518 | `xs_mom` | Cross-Sectional Momentum (all markets) | z = ผลตอบแทน log months เดือน ÷ (ความผันผวน × √N) · อันดับเปอร์เซ็นไทล์ของ z เทียบอีก 29 ตลาด (ราคาปิดแท่งเดียวกัน) | 16 |
| 519 | `xs_mom_group` | Cross-Sectional Momentum (within asset group) | z เหมือน xs_mom · อันดับเทียบตลาดในกลุ่มเดียวกัน (ถ้าน้อยกว่า 5 ตลาด ใช้ทุกตลาด) | 16 |
| 520 | `ens_trend` | Trend Ensemble (5 classic rules vote) | ผลตอบแทน 1, 3, 12 เดือน · ฝั่งของกึ่งกลาง Donchian 55 · SMA50 เทียบ SMA200 | 16 |
| 557 | `mo_roc_t200` | ROC Zero Cross · above SMA200 | ROC (n) · ราคาเหนือ SMA200 | 20 |
| 558 | `mo_rsi_t200` | RSI Thrust · above SMA200 | RSI (14) · ราคาเหนือ SMA200 | 20 |
| 559 | `mo_st50_t200` | Stochastic 50 Cross · above SMA200 | Stochastic %K (n, 3) · ราคาเหนือ SMA200 | 20 |
| 560 | `mo_cci_t200` | CCI Zero Cross · above SMA200 | CCI (n) · ราคาเหนือ SMA200 | 20 |
| 561 | `mo_m121_t200` | 12-1 Momentum · above SMA200 | ผลตอบแทนข้ามเดือนล่าสุด · ราคาเหนือ SMA200 | 20 |
| 562 | `mo_tsi_t200` | TSI Zero Cross · above SMA200 | TSI (n, 13) · ราคาเหนือ SMA200 | 20 |
| 563 | `mo_ppo_t200` | PPO Zero Cross · above SMA200 | EMA เร็ว / ช้า · ราคาเหนือ SMA200 | 20 |
| 564 | `mo_acc_t200` | Momentum Acceleration · above SMA200 | ROC (n) · ROC ย้อนหลัง n แท่ง · ราคาเหนือ SMA200 | 20 |
| 565 | `mo_roc_adx` | ROC Zero Cross · ADX > 25 trend | ROC (n) · ADX > 25 และ +DI > −DI | 20 |
| 566 | `mo_rsi_adx` | RSI Thrust · ADX > 25 trend | RSI (14) · ADX > 25 และ +DI > −DI | 20 |
| 567 | `mo_st50_adx` | Stochastic 50 Cross · ADX > 25 trend | Stochastic %K (n, 3) · ADX > 25 และ +DI > −DI | 20 |
| 568 | `mo_cci_adx` | CCI Zero Cross · ADX > 25 trend | CCI (n) · ADX > 25 และ +DI > −DI | 20 |
| 569 | `mo_m121_adx` | 12-1 Momentum · ADX > 25 trend | ผลตอบแทนข้ามเดือนล่าสุด · ADX > 25 และ +DI > −DI | 20 |
| 570 | `mo_tsi_adx` | TSI Zero Cross · ADX > 25 trend | TSI (n, 13) · ADX > 25 และ +DI > −DI | 20 |
| 571 | `mo_ppo_adx` | PPO Zero Cross · ADX > 25 trend | EMA เร็ว / ช้า · ADX > 25 และ +DI > −DI | 20 |
| 572 | `mo_acc_adx` | Momentum Acceleration · ADX > 25 trend | ROC (n) · ROC ย้อนหลัง n แท่ง · ADX > 25 และ +DI > −DI | 20 |
| 573 | `mo_roc_calm` | ROC Zero Cross · calm volatility | ROC (n) · ATR14 ต่ำกว่า ATR100 | 20 |
| 574 | `mo_rsi_calm` | RSI Thrust · calm volatility | RSI (14) · ATR14 ต่ำกว่า ATR100 | 20 |
| 575 | `mo_st50_calm` | Stochastic 50 Cross · calm volatility | Stochastic %K (n, 3) · ATR14 ต่ำกว่า ATR100 | 20 |
| 576 | `mo_cci_calm` | CCI Zero Cross · calm volatility | CCI (n) · ATR14 ต่ำกว่า ATR100 | 20 |
| 577 | `mo_m121_calm` | 12-1 Momentum · calm volatility | ผลตอบแทนข้ามเดือนล่าสุด · ATR14 ต่ำกว่า ATR100 | 20 |
| 578 | `mo_tsi_calm` | TSI Zero Cross · calm volatility | TSI (n, 13) · ATR14 ต่ำกว่า ATR100 | 20 |
| 579 | `mo_ppo_calm` | PPO Zero Cross · calm volatility | EMA เร็ว / ช้า · ATR14 ต่ำกว่า ATR100 | 20 |
| 580 | `mo_acc_calm` | Momentum Acceleration · calm volatility | ROC (n) · ROC ย้อนหลัง n แท่ง · ATR14 ต่ำกว่า ATR100 | 20 |
| 581 | `mo_roc_wild` | ROC Zero Cross · high volatility | ROC (n) · ATR14 สูงกว่า ATR100 | 20 |
| 582 | `mo_rsi_wild` | RSI Thrust · high volatility | RSI (14) · ATR14 สูงกว่า ATR100 | 20 |
| 583 | `mo_st50_wild` | Stochastic 50 Cross · high volatility | Stochastic %K (n, 3) · ATR14 สูงกว่า ATR100 | 20 |
| 584 | `mo_cci_wild` | CCI Zero Cross · high volatility | CCI (n) · ATR14 สูงกว่า ATR100 | 20 |
| 585 | `mo_m121_wild` | 12-1 Momentum · high volatility | ผลตอบแทนข้ามเดือนล่าสุด · ATR14 สูงกว่า ATR100 | 20 |
| 586 | `mo_tsi_wild` | TSI Zero Cross · high volatility | TSI (n, 13) · ATR14 สูงกว่า ATR100 | 20 |
| 587 | `mo_ppo_wild` | PPO Zero Cross · high volatility | EMA เร็ว / ช้า · ATR14 สูงกว่า ATR100 | 20 |
| 588 | `mo_acc_wild` | Momentum Acceleration · high volatility | ROC (n) · ROC ย้อนหลัง n แท่ง · ATR14 สูงกว่า ATR100 | 20 |
| 589 | `mo_roc_year` | ROC Zero Cross · upper half of 12-month range | ROC (n) · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 590 | `mo_rsi_year` | RSI Thrust · upper half of 12-month range | RSI (14) · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 591 | `mo_st50_year` | Stochastic 50 Cross · upper half of 12-month range | Stochastic %K (n, 3) · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 592 | `mo_cci_year` | CCI Zero Cross · upper half of 12-month range | CCI (n) · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 593 | `mo_m121_year` | 12-1 Momentum · upper half of 12-month range | ผลตอบแทนข้ามเดือนล่าสุด · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 594 | `mo_tsi_year` | TSI Zero Cross · upper half of 12-month range | TSI (n, 13) · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 595 | `mo_ppo_year` | PPO Zero Cross · upper half of 12-month range | EMA เร็ว / ช้า · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 596 | `mo_acc_year` | Momentum Acceleration · upper half of 12-month range | ROC (n) · ROC ย้อนหลัง n แท่ง · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 597 | `mo_roc_gold` | ROC Zero Cross · Golden-cross regime | ROC (n) · SMA50 เหนือ SMA200 | 20 |
| 598 | `mo_rsi_gold` | RSI Thrust · Golden-cross regime | RSI (14) · SMA50 เหนือ SMA200 | 20 |
| 599 | `mo_st50_gold` | Stochastic 50 Cross · Golden-cross regime | Stochastic %K (n, 3) · SMA50 เหนือ SMA200 | 20 |
| 600 | `mo_cci_gold` | CCI Zero Cross · Golden-cross regime | CCI (n) · SMA50 เหนือ SMA200 | 20 |
| 601 | `mo_m121_gold` | 12-1 Momentum · Golden-cross regime | ผลตอบแทนข้ามเดือนล่าสุด · SMA50 เหนือ SMA200 | 20 |
| 602 | `mo_tsi_gold` | TSI Zero Cross · Golden-cross regime | TSI (n, 13) · SMA50 เหนือ SMA200 | 20 |
| 603 | `mo_ppo_gold` | PPO Zero Cross · Golden-cross regime | EMA เร็ว / ช้า · SMA50 เหนือ SMA200 | 20 |
| 604 | `mo_acc_gold` | Momentum Acceleration · Golden-cross regime | ROC (n) · ROC ย้อนหลัง n แท่ง · SMA50 เหนือ SMA200 | 20 |
| 605 | `mo_roc_e200r` | ROC Zero Cross · above rising EMA200 | ROC (n) · ราคาเหนือ EMA200 ที่ชี้ขึ้น | 20 |
| 606 | `mo_rsi_e200r` | RSI Thrust · above rising EMA200 | RSI (14) · ราคาเหนือ EMA200 ที่ชี้ขึ้น | 20 |
| 607 | `mo_st50_e200r` | Stochastic 50 Cross · above rising EMA200 | Stochastic %K (n, 3) · ราคาเหนือ EMA200 ที่ชี้ขึ้น | 20 |
| 608 | `mo_cci_e200r` | CCI Zero Cross · above rising EMA200 | CCI (n) · ราคาเหนือ EMA200 ที่ชี้ขึ้น | 20 |
| 609 | `mo_m121_e200r` | 12-1 Momentum · above rising EMA200 | ผลตอบแทนข้ามเดือนล่าสุด · ราคาเหนือ EMA200 ที่ชี้ขึ้น | 20 |
| 610 | `mo_tsi_e200r` | TSI Zero Cross · above rising EMA200 | TSI (n, 13) · ราคาเหนือ EMA200 ที่ชี้ขึ้น | 20 |
| 611 | `mo_ppo_e200r` | PPO Zero Cross · above rising EMA200 | EMA เร็ว / ช้า · ราคาเหนือ EMA200 ที่ชี้ขึ้น | 20 |
| 612 | `mo_acc_e200r` | Momentum Acceleration · above rising EMA200 | ROC (n) · ROC ย้อนหลัง n แท่ง · ราคาเหนือ EMA200 ที่ชี้ขึ้น | 20 |
| 613 | `mo_roc_st` | ROC Zero Cross · Supertrend up | ROC (n) · Supertrend | 20 |
| 614 | `mo_rsi_st` | RSI Thrust · Supertrend up | RSI (14) · Supertrend | 20 |
| 615 | `mo_st50_st` | Stochastic 50 Cross · Supertrend up | Stochastic %K (n, 3) · Supertrend | 20 |
| 616 | `mo_cci_st` | CCI Zero Cross · Supertrend up | CCI (n) · Supertrend | 20 |
| 617 | `mo_m121_st` | 12-1 Momentum · Supertrend up | ผลตอบแทนข้ามเดือนล่าสุด · Supertrend | 20 |
| 618 | `mo_tsi_st` | TSI Zero Cross · Supertrend up | TSI (n, 13) · Supertrend | 20 |
| 619 | `mo_ppo_st` | PPO Zero Cross · Supertrend up | EMA เร็ว / ช้า · Supertrend | 20 |
| 620 | `mo_acc_st` | Momentum Acceleration · Supertrend up | ROC (n) · ROC ย้อนหลัง n แท่ง · Supertrend | 20 |
| 621 | `mo_roc_don` | ROC Zero Cross · upper half of 100-bar range | ROC (n) · ราคาอยู่ครึ่งบนของกรอบ 100 แท่ง | 20 |
| 622 | `mo_rsi_don` | RSI Thrust · upper half of 100-bar range | RSI (14) · ราคาอยู่ครึ่งบนของกรอบ 100 แท่ง | 20 |
| 623 | `mo_st50_don` | Stochastic 50 Cross · upper half of 100-bar range | Stochastic %K (n, 3) · ราคาอยู่ครึ่งบนของกรอบ 100 แท่ง | 20 |
| 624 | `mo_cci_don` | CCI Zero Cross · upper half of 100-bar range | CCI (n) · ราคาอยู่ครึ่งบนของกรอบ 100 แท่ง | 20 |
| 625 | `mo_m121_don` | 12-1 Momentum · upper half of 100-bar range | ผลตอบแทนข้ามเดือนล่าสุด · ราคาอยู่ครึ่งบนของกรอบ 100 แท่ง | 20 |
| 626 | `mo_tsi_don` | TSI Zero Cross · upper half of 100-bar range | TSI (n, 13) · ราคาอยู่ครึ่งบนของกรอบ 100 แท่ง | 20 |
| 627 | `mo_ppo_don` | PPO Zero Cross · upper half of 100-bar range | EMA เร็ว / ช้า · ราคาอยู่ครึ่งบนของกรอบ 100 แท่ง | 20 |
| 628 | `mo_acc_don` | Momentum Acceleration · upper half of 100-bar range | ROC (n) · ROC ย้อนหลัง n แท่ง · ราคาอยู่ครึ่งบนของกรอบ 100 แท่ง | 20 |
| 629 | `mo_roc_slope` | ROC Zero Cross · EMA50 rising | ROC (n) · EMA50 ชี้ขึ้น | 20 |

## เบรกความผันผวน (Volatility breakout) — 100 ระบบ

| # | id | ชื่อ | อินดิเคเตอร์ | ชุดพารามิเตอร์ |
|---|---|---|---|---|
| 7 | `squeeze_breakout` | Volatility Squeeze Breakout | Bollinger Bands (bb_len, 2) · Keltner Channel (bb_len, kc_mult × ATR) | 20 |
| 28 | `keltner_breakout` | Keltner Channel Breakout | Keltner Channel (20, kc_mult × ATR20) · EMA200 | 20 |
| 29 | `bb_breakout` | Bollinger Expansion Breakout | Bollinger Bands (20, bb_k) · ความกว้างแบนด์เทียบค่าเฉลี่ย 50 แท่ง | 20 |
| 39 | `nr_breakout` | NR4 / NR7 Breakout | แท่งช่วงแคบที่สุด (NR) ของ nr_len แท่ง | 20 |
| 56 | `vol_breakout` | Volatility Breakout (Crabel) | ATR14 · EMA100 | 16 |
| 83 | `dual_thrust` | Dual Thrust | ช่วง = max(HH−LC, HC−LL) ของ n แท่ง · EMA100 | 32 |
| 115 | `atr_expansion` | ATR Expansion | ATR5 · ATR50 · EMA50 | 16 |
| 116 | `squeeze_momentum` | Squeeze Momentum (LazyBear) | Bollinger (n, 2) · Keltner (n, 1.5) · โมเมนตัม = ความชันของราคา − กึ่งกลางกรอบ | 16 |
| 117 | `bbw_breakout` | Band-Width Low Breakout | ความกว้าง Bollinger (20, 2) · ควอนไทล์ q ของรอบ months เดือน | 32 |
| 118 | `range_expansion` | Range Expansion Bar | ช่วงแท่ง High − Low · ค่าเฉลี่ยช่วง 20 แท่ง · EMA100 | 16 |
| 120 | `hv_breakout` | Volatility-Regime Breakout | ความผันผวนจริง 20 แท่ง (HV20) เทียบ HV120 · กรอบ High/Low n แท่ง | 32 |
| 125 | `consolidation_break` | Tight-Range Breakout in Trend | กรอบ n แท่ง · ATR14 · EMA50 / EMA200 | 32 |
| 630 | `vb_sqz_t200` | BB Width Squeeze · above SMA200 | Bollinger (20, k) · Percentile ความกว้าง 120 แท่ง · ราคาเหนือ SMA200 | 20 |
| 631 | `vb_kel_t200` | Keltner Breakout · above SMA200 | EMA20 · ATR20 · ราคาเหนือ SMA200 | 20 |
| 632 | `vb_nr_t200` | NR Breakout · above SMA200 | ช่วงแท่ง (NR n) · ราคาเหนือ SMA200 | 20 |
| 633 | `vb_atrb_t200` | Wide-Range Bar · above SMA200 | ATR14 · ช่วงแท่ง · ราคาเหนือ SMA200 | 20 |
| 634 | `vb_box_t200` | Tight Box Break · above SMA200 | High / Low 10 แท่ง · ATR50 · ราคาเหนือ SMA200 | 20 |
| 635 | `vb_hv_t200` | Volatility Jump · above SMA200 | SD ผลตอบแทน 10 แท่ง · ค่าเฉลี่ย 100 แท่ง · ราคาเหนือ SMA200 | 20 |
| 636 | `vb_pctb_t200` | Bollinger Breakout · above SMA200 | Bollinger (n, 2) · ราคาเหนือ SMA200 | 20 |
| 637 | `vb_dq_t200` | Quiet Donchian Break · above SMA200 | Donchian (n) · ATR14 / ATR100 · ราคาเหนือ SMA200 | 20 |
| 638 | `vb_rx_t200` | Range Expansion Bar · above SMA200 | ช่วงแท่ง · ราคาเหนือ SMA200 | 20 |
| 639 | `vb_sqz_calm` | BB Width Squeeze · calm volatility | Bollinger (20, k) · Percentile ความกว้าง 120 แท่ง · ATR14 ต่ำกว่า ATR100 | 20 |
| 640 | `vb_kel_calm` | Keltner Breakout · calm volatility | EMA20 · ATR20 · ATR14 ต่ำกว่า ATR100 | 20 |
| 641 | `vb_nr_calm` | NR Breakout · calm volatility | ช่วงแท่ง (NR n) · ATR14 ต่ำกว่า ATR100 | 20 |
| 642 | `vb_atrb_calm` | Wide-Range Bar · calm volatility | ATR14 · ช่วงแท่ง · ATR14 ต่ำกว่า ATR100 | 20 |
| 643 | `vb_box_calm` | Tight Box Break · calm volatility | High / Low 10 แท่ง · ATR50 · ATR14 ต่ำกว่า ATR100 | 20 |
| 644 | `vb_hv_calm` | Volatility Jump · calm volatility | SD ผลตอบแทน 10 แท่ง · ค่าเฉลี่ย 100 แท่ง · ATR14 ต่ำกว่า ATR100 | 20 |
| 645 | `vb_pctb_calm` | Bollinger Breakout · calm volatility | Bollinger (n, 2) · ATR14 ต่ำกว่า ATR100 | 20 |
| 646 | `vb_dq_calm` | Quiet Donchian Break · calm volatility | Donchian (n) · ATR14 / ATR100 · ATR14 ต่ำกว่า ATR100 | 20 |
| 647 | `vb_rx_calm` | Range Expansion Bar · calm volatility | ช่วงแท่ง · ATR14 ต่ำกว่า ATR100 | 20 |
| 648 | `vb_sqz_wild` | BB Width Squeeze · high volatility | Bollinger (20, k) · Percentile ความกว้าง 120 แท่ง · ATR14 สูงกว่า ATR100 | 20 |
| 649 | `vb_kel_wild` | Keltner Breakout · high volatility | EMA20 · ATR20 · ATR14 สูงกว่า ATR100 | 20 |
| 650 | `vb_nr_wild` | NR Breakout · high volatility | ช่วงแท่ง (NR n) · ATR14 สูงกว่า ATR100 | 20 |
| 651 | `vb_atrb_wild` | Wide-Range Bar · high volatility | ATR14 · ช่วงแท่ง · ATR14 สูงกว่า ATR100 | 20 |
| 652 | `vb_box_wild` | Tight Box Break · high volatility | High / Low 10 แท่ง · ATR50 · ATR14 สูงกว่า ATR100 | 20 |
| 653 | `vb_hv_wild` | Volatility Jump · high volatility | SD ผลตอบแทน 10 แท่ง · ค่าเฉลี่ย 100 แท่ง · ATR14 สูงกว่า ATR100 | 20 |
| 654 | `vb_pctb_wild` | Bollinger Breakout · high volatility | Bollinger (n, 2) · ATR14 สูงกว่า ATR100 | 20 |
| 655 | `vb_rx_wild` | Range Expansion Bar · high volatility | ช่วงแท่ง · ATR14 สูงกว่า ATR100 | 20 |
| 656 | `vb_sqz_chop` | BB Width Squeeze · choppy market (ER < 0.3) | Bollinger (20, k) · Percentile ความกว้าง 120 แท่ง · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 657 | `vb_kel_chop` | Keltner Breakout · choppy market (ER < 0.3) | EMA20 · ATR20 · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 658 | `vb_nr_chop` | NR Breakout · choppy market (ER < 0.3) | ช่วงแท่ง (NR n) · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 659 | `vb_atrb_chop` | Wide-Range Bar · choppy market (ER < 0.3) | ATR14 · ช่วงแท่ง · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 660 | `vb_box_chop` | Tight Box Break · choppy market (ER < 0.3) | High / Low 10 แท่ง · ATR50 · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 661 | `vb_hv_chop` | Volatility Jump · choppy market (ER < 0.3) | SD ผลตอบแทน 10 แท่ง · ค่าเฉลี่ย 100 แท่ง · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 662 | `vb_pctb_chop` | Bollinger Breakout · choppy market (ER < 0.3) | Bollinger (n, 2) · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 663 | `vb_dq_chop` | Quiet Donchian Break · choppy market (ER < 0.3) | Donchian (n) · ATR14 / ATR100 · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 664 | `vb_rx_chop` | Range Expansion Bar · choppy market (ER < 0.3) | ช่วงแท่ง · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 665 | `vb_sqz_range` | BB Width Squeeze · ADX < 20 | Bollinger (20, k) · Percentile ความกว้าง 120 แท่ง · ADX ต่ำกว่า 20 | 20 |
| 666 | `vb_kel_range` | Keltner Breakout · ADX < 20 | EMA20 · ATR20 · ADX ต่ำกว่า 20 | 20 |
| 667 | `vb_nr_range` | NR Breakout · ADX < 20 | ช่วงแท่ง (NR n) · ADX ต่ำกว่า 20 | 20 |
| 668 | `vb_atrb_range` | Wide-Range Bar · ADX < 20 | ATR14 · ช่วงแท่ง · ADX ต่ำกว่า 20 | 20 |
| 669 | `vb_box_range` | Tight Box Break · ADX < 20 | High / Low 10 แท่ง · ATR50 · ADX ต่ำกว่า 20 | 20 |
| 670 | `vb_hv_range` | Volatility Jump · ADX < 20 | SD ผลตอบแทน 10 แท่ง · ค่าเฉลี่ย 100 แท่ง · ADX ต่ำกว่า 20 | 20 |
| 671 | `vb_pctb_range` | Bollinger Breakout · ADX < 20 | Bollinger (n, 2) · ADX ต่ำกว่า 20 | 20 |
| 672 | `vb_dq_range` | Quiet Donchian Break · ADX < 20 | Donchian (n) · ATR14 / ATR100 · ADX ต่ำกว่า 20 | 20 |
| 673 | `vb_rx_range` | Range Expansion Bar · ADX < 20 | ช่วงแท่ง · ADX ต่ำกว่า 20 | 20 |
| 674 | `vb_sqz_m3` | BB Width Squeeze · 3-month momentum up | Bollinger (20, k) · Percentile ความกว้าง 120 แท่ง · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 675 | `vb_kel_m3` | Keltner Breakout · 3-month momentum up | EMA20 · ATR20 · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 676 | `vb_nr_m3` | NR Breakout · 3-month momentum up | ช่วงแท่ง (NR n) · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 677 | `vb_atrb_m3` | Wide-Range Bar · 3-month momentum up | ATR14 · ช่วงแท่ง · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 678 | `vb_box_m3` | Tight Box Break · 3-month momentum up | High / Low 10 แท่ง · ATR50 · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 679 | `vb_pctb_m3` | Bollinger Breakout · 3-month momentum up | Bollinger (n, 2) · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 680 | `vb_dq_m3` | Quiet Donchian Break · 3-month momentum up | Donchian (n) · ATR14 / ATR100 · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 681 | `vb_rx_m3` | Range Expansion Bar · 3-month momentum up | ช่วงแท่ง · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 682 | `vb_sqz_year` | BB Width Squeeze · upper half of 12-month range | Bollinger (20, k) · Percentile ความกว้าง 120 แท่ง · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 683 | `vb_kel_year` | Keltner Breakout · upper half of 12-month range | EMA20 · ATR20 · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 684 | `vb_nr_year` | NR Breakout · upper half of 12-month range | ช่วงแท่ง (NR n) · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 685 | `vb_atrb_year` | Wide-Range Bar · upper half of 12-month range | ATR14 · ช่วงแท่ง · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 686 | `vb_box_year` | Tight Box Break · upper half of 12-month range | High / Low 10 แท่ง · ATR50 · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 687 | `vb_hv_year` | Volatility Jump · upper half of 12-month range | SD ผลตอบแทน 10 แท่ง · ค่าเฉลี่ย 100 แท่ง · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 688 | `vb_pctb_year` | Bollinger Breakout · upper half of 12-month range | Bollinger (n, 2) · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 689 | `vb_dq_year` | Quiet Donchian Break · upper half of 12-month range | Donchian (n) · ATR14 / ATR100 · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 690 | `vb_sqz_gold` | BB Width Squeeze · Golden-cross regime | Bollinger (20, k) · Percentile ความกว้าง 120 แท่ง · SMA50 เหนือ SMA200 | 20 |
| 691 | `vb_kel_gold` | Keltner Breakout · Golden-cross regime | EMA20 · ATR20 · SMA50 เหนือ SMA200 | 20 |
| 692 | `vb_nr_gold` | NR Breakout · Golden-cross regime | ช่วงแท่ง (NR n) · SMA50 เหนือ SMA200 | 20 |
| 693 | `vb_atrb_gold` | Wide-Range Bar · Golden-cross regime | ATR14 · ช่วงแท่ง · SMA50 เหนือ SMA200 | 20 |
| 694 | `vb_box_gold` | Tight Box Break · Golden-cross regime | High / Low 10 แท่ง · ATR50 · SMA50 เหนือ SMA200 | 20 |
| 695 | `vb_hv_gold` | Volatility Jump · Golden-cross regime | SD ผลตอบแทน 10 แท่ง · ค่าเฉลี่ย 100 แท่ง · SMA50 เหนือ SMA200 | 20 |
| 696 | `vb_pctb_gold` | Bollinger Breakout · Golden-cross regime | Bollinger (n, 2) · SMA50 เหนือ SMA200 | 20 |
| 697 | `vb_dq_gold` | Quiet Donchian Break · Golden-cross regime | Donchian (n) · ATR14 / ATR100 · SMA50 เหนือ SMA200 | 20 |
| 698 | `vb_rx_gold` | Range Expansion Bar · Golden-cross regime | ช่วงแท่ง · SMA50 เหนือ SMA200 | 20 |
| 699 | `vb_sqz_r50` | BB Width Squeeze · RSI(50) > 50 | Bollinger (20, k) · Percentile ความกว้าง 120 แท่ง · RSI(50) เหนือ 50 | 20 |
| 700 | `vb_kel_r50` | Keltner Breakout · RSI(50) > 50 | EMA20 · ATR20 · RSI(50) เหนือ 50 | 20 |
| 701 | `vb_nr_r50` | NR Breakout · RSI(50) > 50 | ช่วงแท่ง (NR n) · RSI(50) เหนือ 50 | 20 |
| 702 | `vb_atrb_r50` | Wide-Range Bar · RSI(50) > 50 | ATR14 · ช่วงแท่ง · RSI(50) เหนือ 50 | 20 |
| 703 | `vb_box_r50` | Tight Box Break · RSI(50) > 50 | High / Low 10 แท่ง · ATR50 · RSI(50) เหนือ 50 | 20 |
| 704 | `vb_hv_r50` | Volatility Jump · RSI(50) > 50 | SD ผลตอบแทน 10 แท่ง · ค่าเฉลี่ย 100 แท่ง · RSI(50) เหนือ 50 | 20 |
| 705 | `vb_pctb_r50` | Bollinger Breakout · RSI(50) > 50 | Bollinger (n, 2) · RSI(50) เหนือ 50 | 20 |
| 706 | `vb_dq_r50` | Quiet Donchian Break · RSI(50) > 50 | Donchian (n) · ATR14 / ATR100 · RSI(50) เหนือ 50 | 20 |
| 707 | `vb_rx_r50` | Range Expansion Bar · RSI(50) > 50 | ช่วงแท่ง · RSI(50) เหนือ 50 | 20 |
| 708 | `vb_sqz_adx` | BB Width Squeeze · ADX > 25 trend | Bollinger (20, k) · Percentile ความกว้าง 120 แท่ง · ADX > 25 และ +DI > −DI | 20 |
| 709 | `vb_kel_adx` | Keltner Breakout · ADX > 25 trend | EMA20 · ATR20 · ADX > 25 และ +DI > −DI | 20 |
| 710 | `vb_nr_adx` | NR Breakout · ADX > 25 trend | ช่วงแท่ง (NR n) · ADX > 25 และ +DI > −DI | 20 |
| 711 | `vb_atrb_adx` | Wide-Range Bar · ADX > 25 trend | ATR14 · ช่วงแท่ง · ADX > 25 และ +DI > −DI | 20 |
| 712 | `vb_box_adx` | Tight Box Break · ADX > 25 trend | High / Low 10 แท่ง · ATR50 · ADX > 25 และ +DI > −DI | 20 |
| 713 | `vb_hv_adx` | Volatility Jump · ADX > 25 trend | SD ผลตอบแทน 10 แท่ง · ค่าเฉลี่ย 100 แท่ง · ADX > 25 และ +DI > −DI | 20 |
| 714 | `vb_pctb_adx` | Bollinger Breakout · ADX > 25 trend | Bollinger (n, 2) · ADX > 25 และ +DI > −DI | 20 |
| 715 | `vb_dq_adx` | Quiet Donchian Break · ADX > 25 trend | Donchian (n) · ATR14 / ATR100 · ADX > 25 และ +DI > −DI | 20 |
| 716 | `vb_rx_adx` | Range Expansion Bar · ADX > 25 trend | ช่วงแท่ง · ADX > 25 และ +DI > −DI | 20 |
| 717 | `vb_sqz_e200r` | BB Width Squeeze · above rising EMA200 | Bollinger (20, k) · Percentile ความกว้าง 120 แท่ง · ราคาเหนือ EMA200 ที่ชี้ขึ้น | 20 |

## ย่อตัวในเทรนด์ (Pullback) — 100 ระบบ

| # | id | ชื่อ | อินดิเคเตอร์ | ชุดพารามิเตอร์ |
|---|---|---|---|---|
| 35 | `ultimate_osc` | Ultimate Oscillator Pullback | Ultimate Oscillator (7, 14, 28) · EMA200 | 20 |
| 50 | `elder_ray` | Elder-Ray Pullback | EMA (ema_len) · Bull Power = High − EMA · Bear Power = Low − EMA | 20 |
| 133 | `mr_rsi2_adx` | RSI(2) · ADX > 25 trend | RSI(2) · ADX > 25 และ +DI > −DI | 20 |
| 140 | `mr_rsi2_m3` | RSI(2) · 3-month momentum up | RSI(2) · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 143 | `mr_rsi2_r50` | RSI(2) · RSI(50) > 50 | RSI(2) · RSI(50) เหนือ 50 | 20 |
| 145 | `mr_rsi3_t200` | RSI(3) · above SMA200 | RSI(3) · ราคาเหนือ SMA200 | 20 |
| 150 | `mr_rsi3_adx` | RSI(3) · ADX > 25 trend | RSI(3) · ADX > 25 และ +DI > −DI | 20 |
| 152 | `mr_rsi3_year` | RSI(3) · upper half of 12-month range | RSI(3) · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 153 | `mr_rsi3_st` | RSI(3) · Supertrend up | RSI(3) · Supertrend | 20 |
| 162 | `mr_rsi14_t200` | RSI(14) Oversold · above SMA200 | RSI(14) · ราคาเหนือ SMA200 | 20 |
| 166 | `mr_rsi14_don` | RSI(14) Oversold · upper half of 100-bar range | RSI(14) · ราคาอยู่ครึ่งบนของกรอบ 100 แท่ง | 20 |
| 167 | `mr_rsi14_year` | RSI(14) Oversold · upper half of 12-month range | RSI(14) · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 173 | `mr_rsi14_r50` | RSI(14) Oversold · RSI(50) > 50 | RSI(14) · RSI(50) เหนือ 50 | 20 |
| 180 | `mr_stoch_adx` | Stochastic(14) · ADX > 25 trend | Stochastic %K(14) · ADX > 25 และ +DI > −DI | 20 |
| 182 | `mr_stoch_year` | Stochastic(14) · upper half of 12-month range | Stochastic %K(14) · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 183 | `mr_stoch_st` | Stochastic(14) · Supertrend up | Stochastic %K(14) · Supertrend | 20 |
| 189 | `mr_stoch_e200r` | Stochastic(14) · above rising EMA200 | Stochastic %K(14) · ราคาเหนือ EMA200 ที่ชี้ขึ้น | 20 |
| 194 | `mr_stochf_slope` | Fast Stochastic(5) · EMA50 rising | Stochastic %K(5) · EMA50 ชี้ขึ้น | 20 |
| 195 | `mr_stochf_gold` | Fast Stochastic(5) · Golden-cross regime | Stochastic %K(5) · SMA50 เหนือ SMA200 | 20 |
| 197 | `mr_stochf_adx` | Fast Stochastic(5) · ADX > 25 trend | Stochastic %K(5) · ADX > 25 และ +DI > −DI | 20 |
| 199 | `mr_stochf_year` | Fast Stochastic(5) · upper half of 12-month range | Stochastic %K(5) · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 211 | `mr_cci_slope` | CCI(20) Extreme · EMA50 rising | CCI(20) · EMA50 ชี้ขึ้น | 20 |
| 212 | `mr_cci_gold` | CCI(20) Extreme · Golden-cross regime | CCI(20) · SMA50 เหนือ SMA200 | 20 |
| 214 | `mr_cci_adx` | CCI(20) Extreme · ADX > 25 trend | CCI(20) · ADX > 25 และ +DI > −DI | 20 |
| 216 | `mr_cci_year` | CCI(20) Extreme · upper half of 12-month range | CCI(20) · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 217 | `mr_cci_st` | CCI(20) Extreme · Supertrend up | CCI(20) · Supertrend | 20 |
| 229 | `mr_bb_gold` | Bollinger Stretch · Golden-cross regime | Bollinger Bands (20, k) · SMA50 เหนือ SMA200 | 20 |
| 231 | `mr_bb_adx` | Bollinger Stretch · ADX > 25 trend | Bollinger Bands (20, k) · ADX > 25 และ +DI > −DI | 20 |
| 233 | `mr_bb_year` | Bollinger Stretch · upper half of 12-month range | Bollinger Bands (20, k) · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 234 | `mr_bb_st` | Bollinger Stretch · Supertrend up | Bollinger Bands (20, k) · Supertrend | 20 |
| 241 | `mr_bb_r50` | Bollinger Stretch · RSI(50) > 50 | Bollinger Bands (20, k) · RSI(50) เหนือ 50 | 20 |
| 248 | `mr_kelt_adx` | Keltner Stretch · ADX > 25 trend | EMA20 · ATR20 · ADX > 25 และ +DI > −DI | 20 |
| 249 | `mr_kelt_don` | Keltner Stretch · upper half of 100-bar range | EMA20 · ATR20 · ราคาอยู่ครึ่งบนของกรอบ 100 แท่ง | 20 |
| 251 | `mr_kelt_st` | Keltner Stretch · Supertrend up | EMA20 · ATR20 · Supertrend | 20 |
| 254 | `mr_kelt_m1` | Keltner Stretch · 1-month momentum up | EMA20 · ATR20 · ราคาสูงกว่า 1 เดือนก่อน | 20 |
| 255 | `mr_kelt_m3` | Keltner Stretch · 3-month momentum up | EMA20 · ATR20 · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 265 | `mr_z_adx` | Z-score Stretch · ADX > 25 trend | SMA (n) · ส่วนเบี่ยงเบนมาตรฐาน (n) · ADX > 25 และ +DI > −DI | 20 |
| 266 | `mr_z_don` | Z-score Stretch · upper half of 100-bar range | SMA (n) · ส่วนเบี่ยงเบนมาตรฐาน (n) · ราคาอยู่ครึ่งบนของกรอบ 100 แท่ง | 20 |
| 268 | `mr_z_st` | Z-score Stretch · Supertrend up | SMA (n) · ส่วนเบี่ยงเบนมาตรฐาน (n) · Supertrend | 20 |
| 272 | `mr_z_m3` | Z-score Stretch · 3-month momentum up | SMA (n) · ส่วนเบี่ยงเบนมาตรฐาน (n) · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 282 | `mr_ibs_adx` | Internal Bar Strength · ADX > 25 trend | IBS = (ปิด − ต่ำ) ÷ (สูง − ต่ำ) · ADX > 25 และ +DI > −DI | 20 |
| 284 | `mr_ibs_year` | Internal Bar Strength · upper half of 12-month range | IBS = (ปิด − ต่ำ) ÷ (สูง − ต่ำ) · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 285 | `mr_ibs_st` | Internal Bar Strength · Supertrend up | IBS = (ปิด − ต่ำ) ÷ (สูง − ต่ำ) · Supertrend | 20 |
| 288 | `mr_ibs_m1` | Internal Bar Strength · 1-month momentum up | IBS = (ปิด − ต่ำ) ÷ (สูง − ต่ำ) · ราคาสูงกว่า 1 เดือนก่อน | 20 |
| 299 | `mr_dn_adx` | Consecutive Closes · ADX > 25 trend | จำนวนแท่งที่ปิดลง / ขึ้นติดกัน · ADX > 25 และ +DI > −DI | 20 |
| 301 | `mr_dn_year` | Consecutive Closes · upper half of 12-month range | จำนวนแท่งที่ปิดลง / ขึ้นติดกัน · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 302 | `mr_dn_st` | Consecutive Closes · Supertrend up | จำนวนแท่งที่ปิดลง / ขึ้นติดกัน · Supertrend | 20 |
| 305 | `mr_dn_m1` | Consecutive Closes · 1-month momentum up | จำนวนแท่งที่ปิดลง / ขึ้นติดกัน · ราคาสูงกว่า 1 เดือนก่อน | 20 |
| 313 | `mr_nlow_slope` | N-Bar Low Close · EMA50 rising | ราคาปิดต่ำสุด / สูงสุด n แท่ง · EMA50 ชี้ขึ้น | 20 |
| 314 | `mr_nlow_gold` | N-Bar Low Close · Golden-cross regime | ราคาปิดต่ำสุด / สูงสุด n แท่ง · SMA50 เหนือ SMA200 | 20 |
| 316 | `mr_nlow_adx` | N-Bar Low Close · ADX > 25 trend | ราคาปิดต่ำสุด / สูงสุด n แท่ง · ADX > 25 และ +DI > −DI | 20 |
| 318 | `mr_nlow_year` | N-Bar Low Close · upper half of 12-month range | ราคาปิดต่ำสุด / สูงสุด n แท่ง · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 333 | `mr_drop_adx` | 5-Bar Drop · ADX > 25 trend | ATR14 · การเปลี่ยนแปลง 5 แท่ง · ADX > 25 และ +DI > −DI | 20 |
| 335 | `mr_drop_year` | 5-Bar Drop · upper half of 12-month range | ATR14 · การเปลี่ยนแปลง 5 แท่ง · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 336 | `mr_drop_st` | 5-Bar Drop · Supertrend up | ATR14 · การเปลี่ยนแปลง 5 แท่ง · Supertrend | 20 |
| 339 | `mr_drop_m1` | 5-Bar Drop · 1-month momentum up | ATR14 · การเปลี่ยนแปลง 5 แท่ง · ราคาสูงกว่า 1 เดือนก่อน | 20 |
| 347 | `mr_edist_slope` | Stretch from EMA50 · EMA50 rising | EMA50 · ATR14 · EMA50 ชี้ขึ้น | 20 |
| 350 | `mr_edist_adx` | Stretch from EMA50 · ADX > 25 trend | EMA50 · ATR14 · ADX > 25 และ +DI > −DI | 20 |
| 351 | `mr_edist_don` | Stretch from EMA50 · upper half of 100-bar range | EMA50 · ATR14 · ราคาอยู่ครึ่งบนของกรอบ 100 แท่ง | 20 |
| 352 | `mr_edist_year` | Stretch from EMA50 · upper half of 12-month range | EMA50 · ATR14 · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 353 | `mr_edist_st` | Stretch from EMA50 · Supertrend up | EMA50 · ATR14 · Supertrend | 20 |
| 356 | `mr_edist_m1` | Stretch from EMA50 · 1-month momentum up | EMA50 · ATR14 · ราคาสูงกว่า 1 เดือนก่อน | 20 |
| 360 | `mr_edist_r50` | Stretch from EMA50 · RSI(50) > 50 | EMA50 · ATR14 · RSI(50) เหนือ 50 | 20 |
| 367 | `mr_touch_adx` | EMA Touch · ADX > 25 trend | EMA (n) · ADX > 25 และ +DI > −DI | 20 |
| 368 | `mr_touch_don` | EMA Touch · upper half of 100-bar range | EMA (n) · ราคาอยู่ครึ่งบนของกรอบ 100 แท่ง | 20 |
| 369 | `mr_touch_year` | EMA Touch · upper half of 12-month range | EMA (n) · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 370 | `mr_touch_st` | EMA Touch · Supertrend up | EMA (n) · Supertrend | 20 |
| 384 | `mr_fib_adx` | Swing Retracement · ADX > 25 trend | High / Low 20 แท่ง · ADX > 25 และ +DI > −DI | 20 |
| 385 | `mr_fib_don` | Swing Retracement · upper half of 100-bar range | High / Low 20 แท่ง · ราคาอยู่ครึ่งบนของกรอบ 100 แท่ง | 20 |
| 386 | `mr_fib_year` | Swing Retracement · upper half of 12-month range | High / Low 20 แท่ง · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 387 | `mr_fib_st` | Swing Retracement · Supertrend up | High / Low 20 แท่ง · Supertrend | 20 |
| 390 | `mr_fib_m1` | Swing Retracement · 1-month momentum up | High / Low 20 แท่ง · ราคาสูงกว่า 1 เดือนก่อน | 20 |
| 391 | `mr_fib_m3` | Swing Retracement · 3-month momentum up | High / Low 20 แท่ง · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 401 | `mr_pin_adx` | Hammer at Low · ADX > 25 trend | รูปแท่งเทียน · High / Low 10 แท่ง · ADX > 25 และ +DI > −DI | 20 |
| 403 | `mr_pin_year` | Hammer at Low · upper half of 12-month range | รูปแท่งเทียน · High / Low 10 แท่ง · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 404 | `mr_pin_st` | Hammer at Low · Supertrend up | รูปแท่งเทียน · High / Low 10 แท่ง · Supertrend | 20 |
| 407 | `mr_pin_m1` | Hammer at Low · 1-month momentum up | รูปแท่งเทียน · High / Low 10 แท่ง · ราคาสูงกว่า 1 เดือนก่อน | 20 |
| 416 | `mr_rev_gold` | Key Reversal · Golden-cross regime | High / Low n แท่ง · SMA50 เหนือ SMA200 | 20 |
| 418 | `mr_rev_adx` | Key Reversal · ADX > 25 trend | High / Low n แท่ง · ADX > 25 และ +DI > −DI | 20 |
| 419 | `mr_rev_don` | Key Reversal · upper half of 100-bar range | High / Low n แท่ง · ราคาอยู่ครึ่งบนของกรอบ 100 แท่ง | 20 |
| 425 | `mr_rev_m3` | Key Reversal · 3-month momentum up | High / Low n แท่ง · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 435 | `mr_macd_adx` | MACD Histogram Turn · ADX > 25 trend | MACD (12, 26, 9) · ADX > 25 และ +DI > −DI | 20 |
| 437 | `mr_macd_year` | MACD Histogram Turn · upper half of 12-month range | MACD (12, 26, 9) · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 438 | `mr_macd_st` | MACD Histogram Turn · Supertrend up | MACD (12, 26, 9) · Supertrend | 20 |
| 441 | `mr_macd_m1` | MACD Histogram Turn · 1-month momentum up | MACD (12, 26, 9) · ราคาสูงกว่า 1 เดือนก่อน | 20 |
| 447 | `mr_roc_t200` | ROC Extreme · above SMA200 | ROC (n) · Percent rank 250 แท่ง · ราคาเหนือ SMA200 | 20 |
| 454 | `mr_roc_year` | ROC Extreme · upper half of 12-month range | ROC (n) · Percent rank 250 แท่ง · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 455 | `mr_roc_st` | ROC Extreme · Supertrend up | ROC (n) · Percent rank 250 แท่ง · Supertrend | 20 |
| 468 | `mr_crsi_adx` | Connors RSI · ADX > 25 trend | RSI(3) · RSI ของ streak · Percent rank 100 · ADX > 25 และ +DI > −DI | 20 |
| 470 | `mr_crsi_year` | Connors RSI · upper half of 12-month range | RSI(3) · RSI ของ streak · Percent rank 100 · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 471 | `mr_crsi_st` | Connors RSI · Supertrend up | RSI(3) · RSI ของ streak · Percent rank 100 · Supertrend | 20 |
| 474 | `mr_crsi_m1` | Connors RSI · 1-month momentum up | RSI(3) · RSI ของ streak · Percent rank 100 · ราคาสูงกว่า 1 เดือนก่อน | 20 |
| 485 | `mr_reclaim_adx` | SMA5 Reclaim · ADX > 25 trend | SMA5 · ADX > 25 และ +DI > −DI | 20 |
| 488 | `mr_reclaim_st` | SMA5 Reclaim · Supertrend up | SMA5 · Supertrend | 20 |
| 491 | `mr_reclaim_m1` | SMA5 Reclaim · 1-month momentum up | SMA5 · ราคาสูงกว่า 1 เดือนก่อน | 20 |
| 492 | `mr_reclaim_m3` | SMA5 Reclaim · 3-month momentum up | SMA5 · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 507 | `mr_capit_adx` | Capitulation Bar · ADX > 25 trend | ATR14 · ตัวแท่ง · ADX > 25 และ +DI > −DI | 20 |
| 509 | `mr_capit_year` | Capitulation Bar · upper half of 12-month range | ATR14 · ตัวแท่ง · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 510 | `mr_capit_st` | Capitulation Bar · Supertrend up | ATR14 · ตัวแท่ง · Supertrend | 20 |
| 513 | `mr_capit_m1` | Capitulation Bar · 1-month momentum up | ATR14 · ตัวแท่ง · ราคาสูงกว่า 1 เดือนก่อน | 20 |

## สวนกลับค่าเฉลี่ย (Mean reversion) — 100 ระบบ

| # | id | ชื่อ | อินดิเคเตอร์ | ชุดพารามิเตอร์ |
|---|---|---|---|---|
| 127 | `mr_rsi2_raw` | RSI(2) (raw) | RSI(2) | 20 |
| 141 | `mr_rsi2_chop` | RSI(2) · choppy market (ER < 0.3) | RSI(2) · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 149 | `mr_rsi3_range` | RSI(3) · ADX < 20 | RSI(3) · ADX ต่ำกว่า 20 | 20 |
| 154 | `mr_rsi3_calm` | RSI(3) · calm volatility | RSI(3) · ATR14 ต่ำกว่า ATR100 | 20 |
| 155 | `mr_rsi3_wild` | RSI(3) · high volatility | RSI(3) · ATR14 สูงกว่า ATR100 | 20 |
| 158 | `mr_rsi3_chop` | RSI(3) · choppy market (ER < 0.3) | RSI(3) · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 161 | `mr_rsi14_raw` | RSI(14) Oversold (raw) | RSI(14) | 20 |
| 165 | `mr_rsi14_range` | RSI(14) Oversold · ADX < 20 | RSI(14) · ADX ต่ำกว่า 20 | 20 |
| 168 | `mr_rsi14_calm` | RSI(14) Oversold · calm volatility | RSI(14) · ATR14 ต่ำกว่า ATR100 | 20 |
| 169 | `mr_rsi14_wild` | RSI(14) Oversold · high volatility | RSI(14) · ATR14 สูงกว่า ATR100 | 20 |
| 172 | `mr_rsi14_chop` | RSI(14) Oversold · choppy market (ER < 0.3) | RSI(14) · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 174 | `mr_stoch_raw` | Stochastic(14) (raw) | Stochastic %K(14) | 20 |
| 179 | `mr_stoch_range` | Stochastic(14) · ADX < 20 | Stochastic %K(14) · ADX ต่ำกว่า 20 | 20 |
| 184 | `mr_stoch_calm` | Stochastic(14) · calm volatility | Stochastic %K(14) · ATR14 ต่ำกว่า ATR100 | 20 |
| 185 | `mr_stoch_wild` | Stochastic(14) · high volatility | Stochastic %K(14) · ATR14 สูงกว่า ATR100 | 20 |
| 188 | `mr_stoch_chop` | Stochastic(14) · choppy market (ER < 0.3) | Stochastic %K(14) · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 191 | `mr_stochf_raw` | Fast Stochastic(5) (raw) | Stochastic %K(5) | 20 |
| 196 | `mr_stochf_range` | Fast Stochastic(5) · ADX < 20 | Stochastic %K(5) · ADX ต่ำกว่า 20 | 20 |
| 201 | `mr_stochf_calm` | Fast Stochastic(5) · calm volatility | Stochastic %K(5) · ATR14 ต่ำกว่า ATR100 | 20 |
| 202 | `mr_stochf_wild` | Fast Stochastic(5) · high volatility | Stochastic %K(5) · ATR14 สูงกว่า ATR100 | 20 |
| 205 | `mr_stochf_chop` | Fast Stochastic(5) · choppy market (ER < 0.3) | Stochastic %K(5) · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 213 | `mr_cci_range` | CCI(20) Extreme · ADX < 20 | CCI(20) · ADX ต่ำกว่า 20 | 20 |
| 218 | `mr_cci_calm` | CCI(20) Extreme · calm volatility | CCI(20) · ATR14 ต่ำกว่า ATR100 | 20 |
| 219 | `mr_cci_wild` | CCI(20) Extreme · high volatility | CCI(20) · ATR14 สูงกว่า ATR100 | 20 |
| 222 | `mr_cci_chop` | CCI(20) Extreme · choppy market (ER < 0.3) | CCI(20) · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 225 | `mr_bb_raw` | Bollinger Stretch (raw) | Bollinger Bands (20, k) | 20 |
| 230 | `mr_bb_range` | Bollinger Stretch · ADX < 20 | Bollinger Bands (20, k) · ADX ต่ำกว่า 20 | 20 |
| 235 | `mr_bb_calm` | Bollinger Stretch · calm volatility | Bollinger Bands (20, k) · ATR14 ต่ำกว่า ATR100 | 20 |
| 236 | `mr_bb_wild` | Bollinger Stretch · high volatility | Bollinger Bands (20, k) · ATR14 สูงกว่า ATR100 | 20 |
| 239 | `mr_bb_chop` | Bollinger Stretch · choppy market (ER < 0.3) | Bollinger Bands (20, k) · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 242 | `mr_kelt_raw` | Keltner Stretch (raw) | EMA20 · ATR20 | 20 |
| 247 | `mr_kelt_range` | Keltner Stretch · ADX < 20 | EMA20 · ATR20 · ADX ต่ำกว่า 20 | 20 |
| 252 | `mr_kelt_calm` | Keltner Stretch · calm volatility | EMA20 · ATR20 · ATR14 ต่ำกว่า ATR100 | 20 |
| 253 | `mr_kelt_wild` | Keltner Stretch · high volatility | EMA20 · ATR20 · ATR14 สูงกว่า ATR100 | 20 |
| 264 | `mr_z_range` | Z-score Stretch · ADX < 20 | SMA (n) · ส่วนเบี่ยงเบนมาตรฐาน (n) · ADX ต่ำกว่า 20 | 20 |
| 269 | `mr_z_calm` | Z-score Stretch · calm volatility | SMA (n) · ส่วนเบี่ยงเบนมาตรฐาน (n) · ATR14 ต่ำกว่า ATR100 | 20 |
| 270 | `mr_z_wild` | Z-score Stretch · high volatility | SMA (n) · ส่วนเบี่ยงเบนมาตรฐาน (n) · ATR14 สูงกว่า ATR100 | 20 |
| 273 | `mr_z_chop` | Z-score Stretch · choppy market (ER < 0.3) | SMA (n) · ส่วนเบี่ยงเบนมาตรฐาน (n) · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 281 | `mr_ibs_range` | Internal Bar Strength · ADX < 20 | IBS = (ปิด − ต่ำ) ÷ (สูง − ต่ำ) · ADX ต่ำกว่า 20 | 20 |
| 286 | `mr_ibs_calm` | Internal Bar Strength · calm volatility | IBS = (ปิด − ต่ำ) ÷ (สูง − ต่ำ) · ATR14 ต่ำกว่า ATR100 | 20 |
| 287 | `mr_ibs_wild` | Internal Bar Strength · high volatility | IBS = (ปิด − ต่ำ) ÷ (สูง − ต่ำ) · ATR14 สูงกว่า ATR100 | 20 |
| 290 | `mr_ibs_chop` | Internal Bar Strength · choppy market (ER < 0.3) | IBS = (ปิด − ต่ำ) ÷ (สูง − ต่ำ) · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 298 | `mr_dn_range` | Consecutive Closes · ADX < 20 | จำนวนแท่งที่ปิดลง / ขึ้นติดกัน · ADX ต่ำกว่า 20 | 20 |
| 303 | `mr_dn_calm` | Consecutive Closes · calm volatility | จำนวนแท่งที่ปิดลง / ขึ้นติดกัน · ATR14 ต่ำกว่า ATR100 | 20 |
| 304 | `mr_dn_wild` | Consecutive Closes · high volatility | จำนวนแท่งที่ปิดลง / ขึ้นติดกัน · ATR14 สูงกว่า ATR100 | 20 |
| 307 | `mr_dn_chop` | Consecutive Closes · choppy market (ER < 0.3) | จำนวนแท่งที่ปิดลง / ขึ้นติดกัน · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 315 | `mr_nlow_range` | N-Bar Low Close · ADX < 20 | ราคาปิดต่ำสุด / สูงสุด n แท่ง · ADX ต่ำกว่า 20 | 20 |
| 320 | `mr_nlow_calm` | N-Bar Low Close · calm volatility | ราคาปิดต่ำสุด / สูงสุด n แท่ง · ATR14 ต่ำกว่า ATR100 | 20 |
| 321 | `mr_nlow_wild` | N-Bar Low Close · high volatility | ราคาปิดต่ำสุด / สูงสุด n แท่ง · ATR14 สูงกว่า ATR100 | 20 |
| 324 | `mr_nlow_chop` | N-Bar Low Close · choppy market (ER < 0.3) | ราคาปิดต่ำสุด / สูงสุด n แท่ง · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 332 | `mr_drop_range` | 5-Bar Drop · ADX < 20 | ATR14 · การเปลี่ยนแปลง 5 แท่ง · ADX ต่ำกว่า 20 | 20 |
| 337 | `mr_drop_calm` | 5-Bar Drop · calm volatility | ATR14 · การเปลี่ยนแปลง 5 แท่ง · ATR14 ต่ำกว่า ATR100 | 20 |
| 338 | `mr_drop_wild` | 5-Bar Drop · high volatility | ATR14 · การเปลี่ยนแปลง 5 แท่ง · ATR14 สูงกว่า ATR100 | 20 |
| 341 | `mr_drop_chop` | 5-Bar Drop · choppy market (ER < 0.3) | ATR14 · การเปลี่ยนแปลง 5 แท่ง · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 344 | `mr_edist_raw` | Stretch from EMA50 (raw) | EMA50 · ATR14 | 20 |
| 349 | `mr_edist_range` | Stretch from EMA50 · ADX < 20 | EMA50 · ATR14 · ADX ต่ำกว่า 20 | 20 |
| 354 | `mr_edist_calm` | Stretch from EMA50 · calm volatility | EMA50 · ATR14 · ATR14 ต่ำกว่า ATR100 | 20 |
| 355 | `mr_edist_wild` | Stretch from EMA50 · high volatility | EMA50 · ATR14 · ATR14 สูงกว่า ATR100 | 20 |
| 358 | `mr_edist_chop` | Stretch from EMA50 · choppy market (ER < 0.3) | EMA50 · ATR14 · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 366 | `mr_touch_range` | EMA Touch · ADX < 20 | EMA (n) · ADX ต่ำกว่า 20 | 20 |
| 371 | `mr_touch_calm` | EMA Touch · calm volatility | EMA (n) · ATR14 ต่ำกว่า ATR100 | 20 |
| 372 | `mr_touch_wild` | EMA Touch · high volatility | EMA (n) · ATR14 สูงกว่า ATR100 | 20 |
| 375 | `mr_touch_chop` | EMA Touch · choppy market (ER < 0.3) | EMA (n) · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 378 | `mr_fib_raw` | Swing Retracement (raw) | High / Low 20 แท่ง | 20 |
| 383 | `mr_fib_range` | Swing Retracement · ADX < 20 | High / Low 20 แท่ง · ADX ต่ำกว่า 20 | 20 |
| 388 | `mr_fib_calm` | Swing Retracement · calm volatility | High / Low 20 แท่ง · ATR14 ต่ำกว่า ATR100 | 20 |
| 389 | `mr_fib_wild` | Swing Retracement · high volatility | High / Low 20 แท่ง · ATR14 สูงกว่า ATR100 | 20 |
| 392 | `mr_fib_chop` | Swing Retracement · choppy market (ER < 0.3) | High / Low 20 แท่ง · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 395 | `mr_pin_raw` | Hammer at Low (raw) | รูปแท่งเทียน · High / Low 10 แท่ง | 20 |
| 400 | `mr_pin_range` | Hammer at Low · ADX < 20 | รูปแท่งเทียน · High / Low 10 แท่ง · ADX ต่ำกว่า 20 | 20 |
| 405 | `mr_pin_calm` | Hammer at Low · calm volatility | รูปแท่งเทียน · High / Low 10 แท่ง · ATR14 ต่ำกว่า ATR100 | 20 |
| 406 | `mr_pin_wild` | Hammer at Low · high volatility | รูปแท่งเทียน · High / Low 10 แท่ง · ATR14 สูงกว่า ATR100 | 20 |
| 417 | `mr_rev_range` | Key Reversal · ADX < 20 | High / Low n แท่ง · ADX ต่ำกว่า 20 | 20 |
| 422 | `mr_rev_calm` | Key Reversal · calm volatility | High / Low n แท่ง · ATR14 ต่ำกว่า ATR100 | 20 |
| 423 | `mr_rev_wild` | Key Reversal · high volatility | High / Low n แท่ง · ATR14 สูงกว่า ATR100 | 20 |
| 426 | `mr_rev_chop` | Key Reversal · choppy market (ER < 0.3) | High / Low n แท่ง · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 434 | `mr_macd_range` | MACD Histogram Turn · ADX < 20 | MACD (12, 26, 9) · ADX ต่ำกว่า 20 | 20 |
| 439 | `mr_macd_calm` | MACD Histogram Turn · calm volatility | MACD (12, 26, 9) · ATR14 ต่ำกว่า ATR100 | 20 |
| 440 | `mr_macd_wild` | MACD Histogram Turn · high volatility | MACD (12, 26, 9) · ATR14 สูงกว่า ATR100 | 20 |
| 443 | `mr_macd_chop` | MACD Histogram Turn · choppy market (ER < 0.3) | MACD (12, 26, 9) · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 451 | `mr_roc_range` | ROC Extreme · ADX < 20 | ROC (n) · Percent rank 250 แท่ง · ADX ต่ำกว่า 20 | 20 |
| 456 | `mr_roc_wild` | ROC Extreme · high volatility | ROC (n) · Percent rank 250 แท่ง · ATR14 สูงกว่า ATR100 | 20 |
| 459 | `mr_roc_chop` | ROC Extreme · choppy market (ER < 0.3) | ROC (n) · Percent rank 250 แท่ง · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 462 | `mr_crsi_raw` | Connors RSI (raw) | RSI(3) · RSI ของ streak · Percent rank 100 | 20 |
| 467 | `mr_crsi_range` | Connors RSI · ADX < 20 | RSI(3) · RSI ของ streak · Percent rank 100 · ADX ต่ำกว่า 20 | 20 |
| 472 | `mr_crsi_calm` | Connors RSI · calm volatility | RSI(3) · RSI ของ streak · Percent rank 100 · ATR14 ต่ำกว่า ATR100 | 20 |
| 473 | `mr_crsi_wild` | Connors RSI · high volatility | RSI(3) · RSI ของ streak · Percent rank 100 · ATR14 สูงกว่า ATR100 | 20 |
| 476 | `mr_crsi_chop` | Connors RSI · choppy market (ER < 0.3) | RSI(3) · RSI ของ streak · Percent rank 100 · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 484 | `mr_reclaim_range` | SMA5 Reclaim · ADX < 20 | SMA5 · ADX ต่ำกว่า 20 | 20 |
| 489 | `mr_reclaim_calm` | SMA5 Reclaim · calm volatility | SMA5 · ATR14 ต่ำกว่า ATR100 | 20 |
| 490 | `mr_reclaim_wild` | SMA5 Reclaim · high volatility | SMA5 · ATR14 สูงกว่า ATR100 | 20 |
| 493 | `mr_reclaim_chop` | SMA5 Reclaim · choppy market (ER < 0.3) | SMA5 · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 498 | `mr_pll_range` | Close Below Prior Low · ADX < 20 | High / Low แท่งก่อน · ADX ต่ำกว่า 20 | 20 |
| 499 | `mr_pll_calm` | Close Below Prior Low · calm volatility | High / Low แท่งก่อน · ATR14 ต่ำกว่า ATR100 | 20 |
| 500 | `mr_pll_chop` | Close Below Prior Low · choppy market (ER < 0.3) | High / Low แท่งก่อน · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 501 | `mr_capit_raw` | Capitulation Bar (raw) | ATR14 · ตัวแท่ง | 20 |
| 506 | `mr_capit_range` | Capitulation Bar · ADX < 20 | ATR14 · ตัวแท่ง · ADX ต่ำกว่า 20 | 20 |
| 511 | `mr_capit_calm` | Capitulation Bar · calm volatility | ATR14 · ตัวแท่ง · ATR14 ต่ำกว่า ATR100 | 20 |
| 512 | `mr_capit_wild` | Capitulation Bar · high volatility | ATR14 · ตัวแท่ง · ATR14 สูงกว่า ATR100 | 20 |
| 515 | `mr_capit_chop` | Capitulation Bar · choppy market (ER < 0.3) | ATR14 · ตัวแท่ง · Efficiency ratio 20 แท่ง < 0.3 | 20 |

## ช่วงเวลาตลาด (Session / liquidity) — 100 ระบบ

| # | id | ชื่อ | อินดิเคเตอร์ | ชุดพารามิเตอร์ |
|---|---|---|---|---|
| 8 | `session_breakout` | Asian Range Breakout | กรอบราคาเอเชีย 00:00 → range_end (UTC) | 20 |
| 19 | `prev_day_breakout` | Previous-Day High/Low Breakout | High / Low ของเมื่อวาน · EMA แนวโน้ม (trend_len) | 20 |
| 45 | `ny_open_breakout` | New York Open Range Breakout | กรอบช่วงเปิดนิวยอร์ก 13:00 UTC + range_bars ชม. | 20 |
| 46 | `weekly_breakout` | Previous-Week High/Low Breakout | High / Low สัปดาห์ก่อน · EMA แนวโน้ม (trend_len) | 20 |
| 718 | `se_asia_slope` | Asian Range Break · EMA50 rising | กรอบเอเชีย (UTC) · EMA50 ชี้ขึ้น | 20 |
| 719 | `se_ldn_slope` | London Opening Range · EMA50 rising | กรอบเปิดลอนดอน 07:00 UTC · EMA50 ชี้ขึ้น | 20 |
| 720 | `se_ny_slope` | New York Opening Range · EMA50 rising | กรอบเปิดนิวยอร์ก 13:00 UTC · EMA50 ชี้ขึ้น | 20 |
| 721 | `se_pday_slope` | Prior-Day Extreme Break · EMA50 rising | High / Low วันก่อน · ATR14 · EMA50 ชี้ขึ้น | 20 |
| 722 | `se_pwk_slope` | Prior-Week Extreme Break · EMA50 rising | High / Low สัปดาห์ก่อน · ATR14 · EMA50 ชี้ขึ้น | 20 |
| 723 | `se_dopen_slope` | Day-Open Drive · EMA50 rising | ราคาเปิดวัน (UTC) · ATR14 · EMA50 ชี้ขึ้น | 20 |
| 724 | `se_lclose_slope` | London-Close Fade · EMA50 rising | ราคาเปิดวัน (UTC) · ATR14 · EMA50 ชี้ขึ้น | 20 |
| 725 | `se_mon_slope` | Weekly Opening Range · EMA50 rising | กรอบต้นสัปดาห์ · EMA50 ชี้ขึ้น | 20 |
| 726 | `se_asia_t200` | Asian Range Break · above SMA200 | กรอบเอเชีย (UTC) · ราคาเหนือ SMA200 | 20 |
| 727 | `se_ldn_t200` | London Opening Range · above SMA200 | กรอบเปิดลอนดอน 07:00 UTC · ราคาเหนือ SMA200 | 20 |
| 728 | `se_ny_t200` | New York Opening Range · above SMA200 | กรอบเปิดนิวยอร์ก 13:00 UTC · ราคาเหนือ SMA200 | 20 |
| 729 | `se_pday_t200` | Prior-Day Extreme Break · above SMA200 | High / Low วันก่อน · ATR14 · ราคาเหนือ SMA200 | 20 |
| 730 | `se_pwk_t200` | Prior-Week Extreme Break · above SMA200 | High / Low สัปดาห์ก่อน · ATR14 · ราคาเหนือ SMA200 | 20 |
| 731 | `se_dopen_t200` | Day-Open Drive · above SMA200 | ราคาเปิดวัน (UTC) · ATR14 · ราคาเหนือ SMA200 | 20 |
| 732 | `se_lclose_t200` | London-Close Fade · above SMA200 | ราคาเปิดวัน (UTC) · ATR14 · ราคาเหนือ SMA200 | 20 |
| 733 | `se_mon_t200` | Weekly Opening Range · above SMA200 | กรอบต้นสัปดาห์ · ราคาเหนือ SMA200 | 20 |
| 734 | `se_asia_calm` | Asian Range Break · calm volatility | กรอบเอเชีย (UTC) · ATR14 ต่ำกว่า ATR100 | 20 |
| 735 | `se_ldn_calm` | London Opening Range · calm volatility | กรอบเปิดลอนดอน 07:00 UTC · ATR14 ต่ำกว่า ATR100 | 20 |
| 736 | `se_ny_calm` | New York Opening Range · calm volatility | กรอบเปิดนิวยอร์ก 13:00 UTC · ATR14 ต่ำกว่า ATR100 | 20 |
| 737 | `se_pday_calm` | Prior-Day Extreme Break · calm volatility | High / Low วันก่อน · ATR14 · ATR14 ต่ำกว่า ATR100 | 20 |
| 738 | `se_pwk_calm` | Prior-Week Extreme Break · calm volatility | High / Low สัปดาห์ก่อน · ATR14 · ATR14 ต่ำกว่า ATR100 | 20 |
| 739 | `se_dopen_calm` | Day-Open Drive · calm volatility | ราคาเปิดวัน (UTC) · ATR14 · ATR14 ต่ำกว่า ATR100 | 20 |
| 740 | `se_lclose_calm` | London-Close Fade · calm volatility | ราคาเปิดวัน (UTC) · ATR14 · ATR14 ต่ำกว่า ATR100 | 20 |
| 741 | `se_mon_calm` | Weekly Opening Range · calm volatility | กรอบต้นสัปดาห์ · ATR14 ต่ำกว่า ATR100 | 20 |
| 742 | `se_asia_wild` | Asian Range Break · high volatility | กรอบเอเชีย (UTC) · ATR14 สูงกว่า ATR100 | 20 |
| 743 | `se_ldn_wild` | London Opening Range · high volatility | กรอบเปิดลอนดอน 07:00 UTC · ATR14 สูงกว่า ATR100 | 20 |
| 744 | `se_ny_wild` | New York Opening Range · high volatility | กรอบเปิดนิวยอร์ก 13:00 UTC · ATR14 สูงกว่า ATR100 | 20 |
| 745 | `se_pday_wild` | Prior-Day Extreme Break · high volatility | High / Low วันก่อน · ATR14 · ATR14 สูงกว่า ATR100 | 20 |
| 746 | `se_pwk_wild` | Prior-Week Extreme Break · high volatility | High / Low สัปดาห์ก่อน · ATR14 · ATR14 สูงกว่า ATR100 | 20 |
| 747 | `se_dopen_wild` | Day-Open Drive · high volatility | ราคาเปิดวัน (UTC) · ATR14 · ATR14 สูงกว่า ATR100 | 20 |
| 748 | `se_lclose_wild` | London-Close Fade · high volatility | ราคาเปิดวัน (UTC) · ATR14 · ATR14 สูงกว่า ATR100 | 20 |
| 749 | `se_mon_wild` | Weekly Opening Range · high volatility | กรอบต้นสัปดาห์ · ATR14 สูงกว่า ATR100 | 20 |
| 750 | `se_asia_chop` | Asian Range Break · choppy market (ER < 0.3) | กรอบเอเชีย (UTC) · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 751 | `se_ldn_chop` | London Opening Range · choppy market (ER < 0.3) | กรอบเปิดลอนดอน 07:00 UTC · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 752 | `se_ny_chop` | New York Opening Range · choppy market (ER < 0.3) | กรอบเปิดนิวยอร์ก 13:00 UTC · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 753 | `se_pday_chop` | Prior-Day Extreme Break · choppy market (ER < 0.3) | High / Low วันก่อน · ATR14 · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 754 | `se_pwk_chop` | Prior-Week Extreme Break · choppy market (ER < 0.3) | High / Low สัปดาห์ก่อน · ATR14 · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 755 | `se_dopen_chop` | Day-Open Drive · choppy market (ER < 0.3) | ราคาเปิดวัน (UTC) · ATR14 · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 756 | `se_lclose_chop` | London-Close Fade · choppy market (ER < 0.3) | ราคาเปิดวัน (UTC) · ATR14 · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 757 | `se_mon_chop` | Weekly Opening Range · choppy market (ER < 0.3) | กรอบต้นสัปดาห์ · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 758 | `se_ldn_range` | London Opening Range · ADX < 20 | กรอบเปิดลอนดอน 07:00 UTC · ADX ต่ำกว่า 20 | 20 |
| 759 | `se_ny_range` | New York Opening Range · ADX < 20 | กรอบเปิดนิวยอร์ก 13:00 UTC · ADX ต่ำกว่า 20 | 20 |
| 760 | `se_pday_range` | Prior-Day Extreme Break · ADX < 20 | High / Low วันก่อน · ATR14 · ADX ต่ำกว่า 20 | 20 |
| 761 | `se_pwk_range` | Prior-Week Extreme Break · ADX < 20 | High / Low สัปดาห์ก่อน · ATR14 · ADX ต่ำกว่า 20 | 20 |
| 762 | `se_dopen_range` | Day-Open Drive · ADX < 20 | ราคาเปิดวัน (UTC) · ATR14 · ADX ต่ำกว่า 20 | 20 |
| 763 | `se_lclose_range` | London-Close Fade · ADX < 20 | ราคาเปิดวัน (UTC) · ATR14 · ADX ต่ำกว่า 20 | 20 |
| 764 | `se_mon_range` | Weekly Opening Range · ADX < 20 | กรอบต้นสัปดาห์ · ADX ต่ำกว่า 20 | 20 |
| 765 | `se_asia_adx` | Asian Range Break · ADX > 25 trend | กรอบเอเชีย (UTC) · ADX > 25 และ +DI > −DI | 20 |
| 766 | `se_ldn_adx` | London Opening Range · ADX > 25 trend | กรอบเปิดลอนดอน 07:00 UTC · ADX > 25 และ +DI > −DI | 20 |
| 767 | `se_ny_adx` | New York Opening Range · ADX > 25 trend | กรอบเปิดนิวยอร์ก 13:00 UTC · ADX > 25 และ +DI > −DI | 20 |
| 768 | `se_pday_adx` | Prior-Day Extreme Break · ADX > 25 trend | High / Low วันก่อน · ATR14 · ADX > 25 และ +DI > −DI | 20 |
| 769 | `se_pwk_adx` | Prior-Week Extreme Break · ADX > 25 trend | High / Low สัปดาห์ก่อน · ATR14 · ADX > 25 และ +DI > −DI | 20 |
| 770 | `se_dopen_adx` | Day-Open Drive · ADX > 25 trend | ราคาเปิดวัน (UTC) · ATR14 · ADX > 25 และ +DI > −DI | 20 |
| 771 | `se_lclose_adx` | London-Close Fade · ADX > 25 trend | ราคาเปิดวัน (UTC) · ATR14 · ADX > 25 และ +DI > −DI | 20 |
| 772 | `se_mon_adx` | Weekly Opening Range · ADX > 25 trend | กรอบต้นสัปดาห์ · ADX > 25 และ +DI > −DI | 20 |
| 773 | `se_ldn_m3` | London Opening Range · 3-month momentum up | กรอบเปิดลอนดอน 07:00 UTC · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 774 | `se_ny_m3` | New York Opening Range · 3-month momentum up | กรอบเปิดนิวยอร์ก 13:00 UTC · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 775 | `se_pday_m3` | Prior-Day Extreme Break · 3-month momentum up | High / Low วันก่อน · ATR14 · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 776 | `se_pwk_m3` | Prior-Week Extreme Break · 3-month momentum up | High / Low สัปดาห์ก่อน · ATR14 · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 777 | `se_dopen_m3` | Day-Open Drive · 3-month momentum up | ราคาเปิดวัน (UTC) · ATR14 · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 778 | `se_lclose_m3` | London-Close Fade · 3-month momentum up | ราคาเปิดวัน (UTC) · ATR14 · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 779 | `se_mon_m3` | Weekly Opening Range · 3-month momentum up | กรอบต้นสัปดาห์ · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 780 | `se_asia_year` | Asian Range Break · upper half of 12-month range | กรอบเอเชีย (UTC) · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 781 | `se_ldn_year` | London Opening Range · upper half of 12-month range | กรอบเปิดลอนดอน 07:00 UTC · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 782 | `se_pday_year` | Prior-Day Extreme Break · upper half of 12-month range | High / Low วันก่อน · ATR14 · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 783 | `se_pwk_year` | Prior-Week Extreme Break · upper half of 12-month range | High / Low สัปดาห์ก่อน · ATR14 · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 784 | `se_dopen_year` | Day-Open Drive · upper half of 12-month range | ราคาเปิดวัน (UTC) · ATR14 · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 785 | `se_lclose_year` | London-Close Fade · upper half of 12-month range | ราคาเปิดวัน (UTC) · ATR14 · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 786 | `se_mon_year` | Weekly Opening Range · upper half of 12-month range | กรอบต้นสัปดาห์ · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 787 | `se_asia_gold` | Asian Range Break · Golden-cross regime | กรอบเอเชีย (UTC) · SMA50 เหนือ SMA200 | 20 |
| 788 | `se_ldn_gold` | London Opening Range · Golden-cross regime | กรอบเปิดลอนดอน 07:00 UTC · SMA50 เหนือ SMA200 | 20 |
| 789 | `se_ny_gold` | New York Opening Range · Golden-cross regime | กรอบเปิดนิวยอร์ก 13:00 UTC · SMA50 เหนือ SMA200 | 20 |
| 790 | `se_pday_gold` | Prior-Day Extreme Break · Golden-cross regime | High / Low วันก่อน · ATR14 · SMA50 เหนือ SMA200 | 20 |
| 791 | `se_pwk_gold` | Prior-Week Extreme Break · Golden-cross regime | High / Low สัปดาห์ก่อน · ATR14 · SMA50 เหนือ SMA200 | 20 |
| 792 | `se_dopen_gold` | Day-Open Drive · Golden-cross regime | ราคาเปิดวัน (UTC) · ATR14 · SMA50 เหนือ SMA200 | 20 |
| 793 | `se_lclose_gold` | London-Close Fade · Golden-cross regime | ราคาเปิดวัน (UTC) · ATR14 · SMA50 เหนือ SMA200 | 20 |
| 794 | `se_mon_gold` | Weekly Opening Range · Golden-cross regime | กรอบต้นสัปดาห์ · SMA50 เหนือ SMA200 | 20 |
| 795 | `se_asia_r50` | Asian Range Break · RSI(50) > 50 | กรอบเอเชีย (UTC) · RSI(50) เหนือ 50 | 20 |
| 796 | `se_ldn_r50` | London Opening Range · RSI(50) > 50 | กรอบเปิดลอนดอน 07:00 UTC · RSI(50) เหนือ 50 | 20 |
| 797 | `se_ny_r50` | New York Opening Range · RSI(50) > 50 | กรอบเปิดนิวยอร์ก 13:00 UTC · RSI(50) เหนือ 50 | 20 |
| 798 | `se_pday_r50` | Prior-Day Extreme Break · RSI(50) > 50 | High / Low วันก่อน · ATR14 · RSI(50) เหนือ 50 | 20 |
| 799 | `se_pwk_r50` | Prior-Week Extreme Break · RSI(50) > 50 | High / Low สัปดาห์ก่อน · ATR14 · RSI(50) เหนือ 50 | 20 |
| 800 | `se_dopen_r50` | Day-Open Drive · RSI(50) > 50 | ราคาเปิดวัน (UTC) · ATR14 · RSI(50) เหนือ 50 | 20 |
| 801 | `se_lclose_r50` | London-Close Fade · RSI(50) > 50 | ราคาเปิดวัน (UTC) · ATR14 · RSI(50) เหนือ 50 | 20 |
| 802 | `se_mon_r50` | Weekly Opening Range · RSI(50) > 50 | กรอบต้นสัปดาห์ · RSI(50) เหนือ 50 | 20 |
| 803 | `se_asia_e200r` | Asian Range Break · above rising EMA200 | กรอบเอเชีย (UTC) · ราคาเหนือ EMA200 ที่ชี้ขึ้น | 20 |
| 804 | `se_ldn_e200r` | London Opening Range · above rising EMA200 | กรอบเปิดลอนดอน 07:00 UTC · ราคาเหนือ EMA200 ที่ชี้ขึ้น | 20 |
| 805 | `se_ny_e200r` | New York Opening Range · above rising EMA200 | กรอบเปิดนิวยอร์ก 13:00 UTC · ราคาเหนือ EMA200 ที่ชี้ขึ้น | 20 |
| 806 | `se_pday_e200r` | Prior-Day Extreme Break · above rising EMA200 | High / Low วันก่อน · ATR14 · ราคาเหนือ EMA200 ที่ชี้ขึ้น | 20 |
| 807 | `se_pwk_e200r` | Prior-Week Extreme Break · above rising EMA200 | High / Low สัปดาห์ก่อน · ATR14 · ราคาเหนือ EMA200 ที่ชี้ขึ้น | 20 |
| 808 | `se_dopen_e200r` | Day-Open Drive · above rising EMA200 | ราคาเปิดวัน (UTC) · ATR14 · ราคาเหนือ EMA200 ที่ชี้ขึ้น | 20 |
| 809 | `se_lclose_e200r` | London-Close Fade · above rising EMA200 | ราคาเปิดวัน (UTC) · ATR14 · ราคาเหนือ EMA200 ที่ชี้ขึ้น | 20 |
| 810 | `se_mon_e200r` | Weekly Opening Range · above rising EMA200 | กรอบต้นสัปดาห์ · ราคาเหนือ EMA200 ที่ชี้ขึ้น | 20 |
| 811 | `se_asia_st` | Asian Range Break · Supertrend up | กรอบเอเชีย (UTC) · Supertrend | 20 |
| 812 | `se_ldn_st` | London Opening Range · Supertrend up | กรอบเปิดลอนดอน 07:00 UTC · Supertrend | 20 |
| 813 | `se_ny_st` | New York Opening Range · Supertrend up | กรอบเปิดนิวยอร์ก 13:00 UTC · Supertrend | 20 |

## แท่งเทียน (Price action) — 100 ระบบ

| # | id | ชื่อ | อินดิเคเตอร์ | ชุดพารามิเตอร์ |
|---|---|---|---|---|
| 15 | `inside_bar` | Inside Bar Breakout | Inside bar · EMA แนวโน้ม (ema_len) | 20 |
| 16 | `pin_bar` | Pin Bar Rejection | Pin bar · จุดต่ำ/สูงสุดของ lookback แท่ง | 20 |
| 47 | `pivot_bounce` | Daily Pivot S1/R1 Bounce | Pivot S1 / R1 จาก H/L/C เมื่อวาน · EMA แนวโน้ม (trend_len) | 20 |
| 814 | `pa_eng_raw` | Engulfing at Extreme (raw) | รูปแท่ง Engulfing · High / Low n แท่ง | 20 |
| 815 | `pa_ins_raw` | Inside Bar Break (raw) | Inside bar | 20 |
| 816 | `pa_out_raw` | Outside Bar Close (raw) | Outside bar | 20 |
| 817 | `pa_sold_raw` | Three Soldiers / Crows (raw) | รูปแท่ง 3 แท่ง | 20 |
| 818 | `pa_star_raw` | Morning / Evening Star (raw) | รูปแท่ง 3 แท่ง · High / Low n แท่ง | 20 |
| 819 | `pa_two_raw` | Two-Bar Reversal (raw) | ATR14 · ตัวแท่ง | 20 |
| 820 | `pa_maru_raw` | Marubozu Thrust (raw) | ตัวแท่ง · ATR14 | 20 |
| 821 | `pa_mss_raw` | Market Structure Break (raw) | Swing high / low (fractal k) | 20 |
| 822 | `pa_doji_raw` | Doji Break (raw) | Doji | 20 |
| 823 | `pa_hik_raw` | Hikkake (raw) | Inside bar | 20 |
| 824 | `pa_eng_t200` | Engulfing at Extreme · above SMA200 | รูปแท่ง Engulfing · High / Low n แท่ง · ราคาเหนือ SMA200 | 20 |
| 825 | `pa_ins_t200` | Inside Bar Break · above SMA200 | Inside bar · ราคาเหนือ SMA200 | 20 |
| 826 | `pa_out_t200` | Outside Bar Close · above SMA200 | Outside bar · ราคาเหนือ SMA200 | 20 |
| 827 | `pa_sold_t200` | Three Soldiers / Crows · above SMA200 | รูปแท่ง 3 แท่ง · ราคาเหนือ SMA200 | 20 |
| 828 | `pa_star_t200` | Morning / Evening Star · above SMA200 | รูปแท่ง 3 แท่ง · High / Low n แท่ง · ราคาเหนือ SMA200 | 20 |
| 829 | `pa_two_t200` | Two-Bar Reversal · above SMA200 | ATR14 · ตัวแท่ง · ราคาเหนือ SMA200 | 20 |
| 830 | `pa_maru_t200` | Marubozu Thrust · above SMA200 | ตัวแท่ง · ATR14 · ราคาเหนือ SMA200 | 20 |
| 831 | `pa_mss_t200` | Market Structure Break · above SMA200 | Swing high / low (fractal k) · ราคาเหนือ SMA200 | 20 |
| 832 | `pa_doji_t200` | Doji Break · above SMA200 | Doji · ราคาเหนือ SMA200 | 20 |
| 833 | `pa_hik_t200` | Hikkake · above SMA200 | Inside bar · ราคาเหนือ SMA200 | 20 |
| 834 | `pa_eng_calm` | Engulfing at Extreme · calm volatility | รูปแท่ง Engulfing · High / Low n แท่ง · ATR14 ต่ำกว่า ATR100 | 20 |
| 835 | `pa_ins_calm` | Inside Bar Break · calm volatility | Inside bar · ATR14 ต่ำกว่า ATR100 | 20 |
| 836 | `pa_out_calm` | Outside Bar Close · calm volatility | Outside bar · ATR14 ต่ำกว่า ATR100 | 20 |
| 837 | `pa_sold_calm` | Three Soldiers / Crows · calm volatility | รูปแท่ง 3 แท่ง · ATR14 ต่ำกว่า ATR100 | 20 |
| 838 | `pa_star_calm` | Morning / Evening Star · calm volatility | รูปแท่ง 3 แท่ง · High / Low n แท่ง · ATR14 ต่ำกว่า ATR100 | 20 |
| 839 | `pa_two_calm` | Two-Bar Reversal · calm volatility | ATR14 · ตัวแท่ง · ATR14 ต่ำกว่า ATR100 | 20 |
| 840 | `pa_maru_calm` | Marubozu Thrust · calm volatility | ตัวแท่ง · ATR14 · ATR14 ต่ำกว่า ATR100 | 20 |
| 841 | `pa_mss_calm` | Market Structure Break · calm volatility | Swing high / low (fractal k) · ATR14 ต่ำกว่า ATR100 | 20 |
| 842 | `pa_doji_calm` | Doji Break · calm volatility | Doji · ATR14 ต่ำกว่า ATR100 | 20 |
| 843 | `pa_hik_calm` | Hikkake · calm volatility | Inside bar · ATR14 ต่ำกว่า ATR100 | 20 |
| 844 | `pa_eng_wild` | Engulfing at Extreme · high volatility | รูปแท่ง Engulfing · High / Low n แท่ง · ATR14 สูงกว่า ATR100 | 20 |
| 845 | `pa_ins_wild` | Inside Bar Break · high volatility | Inside bar · ATR14 สูงกว่า ATR100 | 20 |
| 846 | `pa_out_wild` | Outside Bar Close · high volatility | Outside bar · ATR14 สูงกว่า ATR100 | 20 |
| 847 | `pa_sold_wild` | Three Soldiers / Crows · high volatility | รูปแท่ง 3 แท่ง · ATR14 สูงกว่า ATR100 | 20 |
| 848 | `pa_star_wild` | Morning / Evening Star · high volatility | รูปแท่ง 3 แท่ง · High / Low n แท่ง · ATR14 สูงกว่า ATR100 | 20 |
| 849 | `pa_two_wild` | Two-Bar Reversal · high volatility | ATR14 · ตัวแท่ง · ATR14 สูงกว่า ATR100 | 20 |
| 850 | `pa_maru_wild` | Marubozu Thrust · high volatility | ตัวแท่ง · ATR14 · ATR14 สูงกว่า ATR100 | 20 |
| 851 | `pa_mss_wild` | Market Structure Break · high volatility | Swing high / low (fractal k) · ATR14 สูงกว่า ATR100 | 20 |
| 852 | `pa_doji_wild` | Doji Break · high volatility | Doji · ATR14 สูงกว่า ATR100 | 20 |
| 853 | `pa_hik_wild` | Hikkake · high volatility | Inside bar · ATR14 สูงกว่า ATR100 | 20 |
| 854 | `pa_eng_chop` | Engulfing at Extreme · choppy market (ER < 0.3) | รูปแท่ง Engulfing · High / Low n แท่ง · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 855 | `pa_ins_chop` | Inside Bar Break · choppy market (ER < 0.3) | Inside bar · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 856 | `pa_out_chop` | Outside Bar Close · choppy market (ER < 0.3) | Outside bar · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 857 | `pa_sold_chop` | Three Soldiers / Crows · choppy market (ER < 0.3) | รูปแท่ง 3 แท่ง · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 858 | `pa_star_chop` | Morning / Evening Star · choppy market (ER < 0.3) | รูปแท่ง 3 แท่ง · High / Low n แท่ง · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 859 | `pa_two_chop` | Two-Bar Reversal · choppy market (ER < 0.3) | ATR14 · ตัวแท่ง · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 860 | `pa_maru_chop` | Marubozu Thrust · choppy market (ER < 0.3) | ตัวแท่ง · ATR14 · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 861 | `pa_mss_chop` | Market Structure Break · choppy market (ER < 0.3) | Swing high / low (fractal k) · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 862 | `pa_doji_chop` | Doji Break · choppy market (ER < 0.3) | Doji · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 863 | `pa_hik_chop` | Hikkake · choppy market (ER < 0.3) | Inside bar · Efficiency ratio 20 แท่ง < 0.3 | 20 |
| 864 | `pa_eng_range` | Engulfing at Extreme · ADX < 20 | รูปแท่ง Engulfing · High / Low n แท่ง · ADX ต่ำกว่า 20 | 20 |
| 865 | `pa_ins_range` | Inside Bar Break · ADX < 20 | Inside bar · ADX ต่ำกว่า 20 | 20 |
| 866 | `pa_out_range` | Outside Bar Close · ADX < 20 | Outside bar · ADX ต่ำกว่า 20 | 20 |
| 867 | `pa_sold_range` | Three Soldiers / Crows · ADX < 20 | รูปแท่ง 3 แท่ง · ADX ต่ำกว่า 20 | 20 |
| 868 | `pa_star_range` | Morning / Evening Star · ADX < 20 | รูปแท่ง 3 แท่ง · High / Low n แท่ง · ADX ต่ำกว่า 20 | 20 |
| 869 | `pa_two_range` | Two-Bar Reversal · ADX < 20 | ATR14 · ตัวแท่ง · ADX ต่ำกว่า 20 | 20 |
| 870 | `pa_maru_range` | Marubozu Thrust · ADX < 20 | ตัวแท่ง · ATR14 · ADX ต่ำกว่า 20 | 20 |
| 871 | `pa_mss_range` | Market Structure Break · ADX < 20 | Swing high / low (fractal k) · ADX ต่ำกว่า 20 | 20 |
| 872 | `pa_doji_range` | Doji Break · ADX < 20 | Doji · ADX ต่ำกว่า 20 | 20 |
| 873 | `pa_hik_range` | Hikkake · ADX < 20 | Inside bar · ADX ต่ำกว่า 20 | 20 |
| 874 | `pa_eng_adx` | Engulfing at Extreme · ADX > 25 trend | รูปแท่ง Engulfing · High / Low n แท่ง · ADX > 25 และ +DI > −DI | 20 |
| 875 | `pa_ins_adx` | Inside Bar Break · ADX > 25 trend | Inside bar · ADX > 25 และ +DI > −DI | 20 |
| 876 | `pa_out_adx` | Outside Bar Close · ADX > 25 trend | Outside bar · ADX > 25 และ +DI > −DI | 20 |
| 877 | `pa_sold_adx` | Three Soldiers / Crows · ADX > 25 trend | รูปแท่ง 3 แท่ง · ADX > 25 และ +DI > −DI | 20 |
| 878 | `pa_star_adx` | Morning / Evening Star · ADX > 25 trend | รูปแท่ง 3 แท่ง · High / Low n แท่ง · ADX > 25 และ +DI > −DI | 20 |
| 879 | `pa_two_adx` | Two-Bar Reversal · ADX > 25 trend | ATR14 · ตัวแท่ง · ADX > 25 และ +DI > −DI | 20 |
| 880 | `pa_maru_adx` | Marubozu Thrust · ADX > 25 trend | ตัวแท่ง · ATR14 · ADX > 25 และ +DI > −DI | 20 |
| 881 | `pa_mss_adx` | Market Structure Break · ADX > 25 trend | Swing high / low (fractal k) · ADX > 25 และ +DI > −DI | 20 |
| 882 | `pa_doji_adx` | Doji Break · ADX > 25 trend | Doji · ADX > 25 และ +DI > −DI | 20 |
| 883 | `pa_hik_adx` | Hikkake · ADX > 25 trend | Inside bar · ADX > 25 และ +DI > −DI | 20 |
| 884 | `pa_eng_m3` | Engulfing at Extreme · 3-month momentum up | รูปแท่ง Engulfing · High / Low n แท่ง · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 885 | `pa_ins_m3` | Inside Bar Break · 3-month momentum up | Inside bar · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 886 | `pa_out_m3` | Outside Bar Close · 3-month momentum up | Outside bar · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 887 | `pa_sold_m3` | Three Soldiers / Crows · 3-month momentum up | รูปแท่ง 3 แท่ง · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 888 | `pa_star_m3` | Morning / Evening Star · 3-month momentum up | รูปแท่ง 3 แท่ง · High / Low n แท่ง · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 889 | `pa_two_m3` | Two-Bar Reversal · 3-month momentum up | ATR14 · ตัวแท่ง · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 890 | `pa_maru_m3` | Marubozu Thrust · 3-month momentum up | ตัวแท่ง · ATR14 · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 891 | `pa_mss_m3` | Market Structure Break · 3-month momentum up | Swing high / low (fractal k) · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 892 | `pa_doji_m3` | Doji Break · 3-month momentum up | Doji · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 893 | `pa_hik_m3` | Hikkake · 3-month momentum up | Inside bar · ราคาสูงกว่า 3 เดือนก่อน | 20 |
| 894 | `pa_eng_year` | Engulfing at Extreme · upper half of 12-month range | รูปแท่ง Engulfing · High / Low n แท่ง · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 895 | `pa_ins_year` | Inside Bar Break · upper half of 12-month range | Inside bar · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 896 | `pa_sold_year` | Three Soldiers / Crows · upper half of 12-month range | รูปแท่ง 3 แท่ง · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 897 | `pa_star_year` | Morning / Evening Star · upper half of 12-month range | รูปแท่ง 3 แท่ง · High / Low n แท่ง · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 898 | `pa_two_year` | Two-Bar Reversal · upper half of 12-month range | ATR14 · ตัวแท่ง · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 899 | `pa_maru_year` | Marubozu Thrust · upper half of 12-month range | ตัวแท่ง · ATR14 · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 900 | `pa_hik_year` | Hikkake · upper half of 12-month range | Inside bar · ราคาอยู่ครึ่งบนของกรอบ 12 เดือน | 20 |
| 901 | `pa_eng_r50` | Engulfing at Extreme · RSI(50) > 50 | รูปแท่ง Engulfing · High / Low n แท่ง · RSI(50) เหนือ 50 | 20 |
| 902 | `pa_ins_r50` | Inside Bar Break · RSI(50) > 50 | Inside bar · RSI(50) เหนือ 50 | 20 |
| 903 | `pa_out_r50` | Outside Bar Close · RSI(50) > 50 | Outside bar · RSI(50) เหนือ 50 | 20 |
| 904 | `pa_sold_r50` | Three Soldiers / Crows · RSI(50) > 50 | รูปแท่ง 3 แท่ง · RSI(50) เหนือ 50 | 20 |
| 905 | `pa_star_r50` | Morning / Evening Star · RSI(50) > 50 | รูปแท่ง 3 แท่ง · High / Low n แท่ง · RSI(50) เหนือ 50 | 20 |
| 906 | `pa_two_r50` | Two-Bar Reversal · RSI(50) > 50 | ATR14 · ตัวแท่ง · RSI(50) เหนือ 50 | 20 |
| 907 | `pa_maru_r50` | Marubozu Thrust · RSI(50) > 50 | ตัวแท่ง · ATR14 · RSI(50) เหนือ 50 | 20 |
| 908 | `pa_mss_r50` | Market Structure Break · RSI(50) > 50 | Swing high / low (fractal k) · RSI(50) เหนือ 50 | 20 |
| 909 | `pa_doji_r50` | Doji Break · RSI(50) > 50 | Doji · RSI(50) เหนือ 50 | 20 |
| 910 | `pa_hik_r50` | Hikkake · RSI(50) > 50 | Inside bar · RSI(50) เหนือ 50 | 20 |

## ระบบที่ถูกคัดออก (ยังนับในเกณฑ์กันโชค)

#3 `rsi_reversion`, #4 `bb_reversion`, #5 `tsmom`, #6 `adx_trend`, #9 `ema_pullback`, #10 `range_fade`, #17 `stoch_pullback`, #18 `zscore_reversion`, #26 `trix_cross`, #36 `cci_reversion`, #37 `keltner_pullback`, #38 `three_bar_pullback`, #40 `engulfing_reversal`, #41 `donchian_fade`, #42 `rsi_divergence`, #43 `turn_of_month`, #44 `london_fix_fade`, #128 `mr_rsi2_t200`, #129 `mr_rsi2_t100`, #130 `mr_rsi2_slope`, #131 `mr_rsi2_gold`, #132 `mr_rsi2_range`, #134 `mr_rsi2_don`, #135 `mr_rsi2_year`, #136 `mr_rsi2_st`, #137 `mr_rsi2_calm`, #138 `mr_rsi2_wild`, #139 `mr_rsi2_m1`, #142 `mr_rsi2_e200r`, #144 `mr_rsi3_raw`, #146 `mr_rsi3_t100`, #147 `mr_rsi3_slope`, #148 `mr_rsi3_gold`, #151 `mr_rsi3_don`, #156 `mr_rsi3_m1`, #157 `mr_rsi3_m3`, #159 `mr_rsi3_e200r`, #160 `mr_rsi3_r50`, #163 `mr_rsi14_t100`, #164 `mr_rsi14_gold`, #170 `mr_rsi14_m1`, #171 `mr_rsi14_m3`, #175 `mr_stoch_t200`, #176 `mr_stoch_t100`, #177 `mr_stoch_slope`, #178 `mr_stoch_gold`, #181 `mr_stoch_don`, #186 `mr_stoch_m1`, #187 `mr_stoch_m3`, #190 `mr_stoch_r50`, #192 `mr_stochf_t200`, #193 `mr_stochf_t100`, #198 `mr_stochf_don`, #200 `mr_stochf_st`, #203 `mr_stochf_m1`, #204 `mr_stochf_m3`, #206 `mr_stochf_e200r`, #207 `mr_stochf_r50`, #208 `mr_cci_raw`, #209 `mr_cci_t200`, #210 `mr_cci_t100`, #215 `mr_cci_don`, #220 `mr_cci_m1`, #221 `mr_cci_m3`, #223 `mr_cci_e200r`, #224 `mr_cci_r50`, #226 `mr_bb_t200`, #227 `mr_bb_t100`, #228 `mr_bb_slope`, #232 `mr_bb_don`, #237 `mr_bb_m1`, #238 `mr_bb_m3`, #240 `mr_bb_e200r`, #243 `mr_kelt_t200`, #244 `mr_kelt_t100`, #245 `mr_kelt_slope`, #246 `mr_kelt_gold`, #250 `mr_kelt_year`, #256 `mr_kelt_chop`, #257 `mr_kelt_e200r`, #258 `mr_kelt_r50`, #259 `mr_z_raw`, #260 `mr_z_t200`, #261 `mr_z_t100`, #262 `mr_z_slope`, #263 `mr_z_gold`, #267 `mr_z_year`, #271 `mr_z_m1`, #274 `mr_z_e200r`, #275 `mr_z_r50`, #276 `mr_ibs_raw`, #277 `mr_ibs_t200`, #278 `mr_ibs_t100`, #279 `mr_ibs_slope`, #280 `mr_ibs_gold`, #283 `mr_ibs_don`, #289 `mr_ibs_m3`, #291 `mr_ibs_e200r`, #292 `mr_ibs_r50`, #293 `mr_dn_raw`, #294 `mr_dn_t200`, #295 `mr_dn_t100`, #296 `mr_dn_slope`, #297 `mr_dn_gold`, #300 `mr_dn_don`, #306 `mr_dn_m3`, #308 `mr_dn_e200r`, #309 `mr_dn_r50`, #310 `mr_nlow_raw`, #311 `mr_nlow_t200`, #312 `mr_nlow_t100`, #317 `mr_nlow_don`, #319 `mr_nlow_st`, #322 `mr_nlow_m1`, #323 `mr_nlow_m3`, #325 `mr_nlow_e200r`, #326 `mr_nlow_r50`, #327 `mr_drop_raw`, #328 `mr_drop_t200`, #329 `mr_drop_t100`, #330 `mr_drop_slope`, #331 `mr_drop_gold`, #334 `mr_drop_don`, #340 `mr_drop_m3`, #342 `mr_drop_e200r`, #343 `mr_drop_r50`, #345 `mr_edist_t200`, #346 `mr_edist_t100`, #348 `mr_edist_gold`, #357 `mr_edist_m3`, #359 `mr_edist_e200r`, #361 `mr_touch_raw`, #362 `mr_touch_t200`, #363 `mr_touch_t100`, #364 `mr_touch_slope`, #365 `mr_touch_gold`, #373 `mr_touch_m1`, #374 `mr_touch_m3`, #376 `mr_touch_e200r`, #377 `mr_touch_r50`, #379 `mr_fib_t200`, #380 `mr_fib_t100`, #381 `mr_fib_slope`, #382 `mr_fib_gold`, #393 `mr_fib_e200r`, #394 `mr_fib_r50`, #396 `mr_pin_t200`, #397 `mr_pin_t100`, #398 `mr_pin_slope`, #399 `mr_pin_gold`, #402 `mr_pin_don`, #408 `mr_pin_m3`, #409 `mr_pin_chop`, #410 `mr_pin_e200r`, #411 `mr_pin_r50`, #412 `mr_rev_raw`, #413 `mr_rev_t200`, #414 `mr_rev_t100`, #415 `mr_rev_slope`, #420 `mr_rev_year`, #421 `mr_rev_st`, #424 `mr_rev_m1`, #427 `mr_rev_e200r`, #428 `mr_rev_r50`, #429 `mr_macd_raw`, #430 `mr_macd_t200`, #431 `mr_macd_t100`, #432 `mr_macd_slope`, #433 `mr_macd_gold`, #436 `mr_macd_don`, #442 `mr_macd_m3`, #444 `mr_macd_e200r`, #445 `mr_macd_r50`, #446 `mr_roc_raw`, #448 `mr_roc_t100`, #449 `mr_roc_slope`, #450 `mr_roc_gold`, #452 `mr_roc_adx`, #453 `mr_roc_don`, #457 `mr_roc_m1`, #458 `mr_roc_m3`, #460 `mr_roc_e200r`, #461 `mr_roc_r50`, #463 `mr_crsi_t200`, #464 `mr_crsi_t100`, #465 `mr_crsi_slope`, #466 `mr_crsi_gold`, #469 `mr_crsi_don`, #475 `mr_crsi_m3`, #477 `mr_crsi_e200r`, #478 `mr_crsi_r50`, #479 `mr_reclaim_raw`, #480 `mr_reclaim_t200`, #481 `mr_reclaim_t100`, #482 `mr_reclaim_slope`, #483 `mr_reclaim_gold`, #486 `mr_reclaim_don`, #487 `mr_reclaim_year`, #494 `mr_reclaim_e200r`, #495 `mr_reclaim_r50`, #496 `mr_pll_raw`, #497 `mr_pll_t200`, #502 `mr_capit_t200`, #503 `mr_capit_t100`, #504 `mr_capit_slope`, #505 `mr_capit_gold`, #508 `mr_capit_don`, #514 `mr_capit_m3`, #516 `mr_capit_e200r`, #517 `mr_capit_r50`
