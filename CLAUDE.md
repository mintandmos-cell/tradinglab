# TradingLab — runbook for Claude

The user (Thai, trades manually in MetaTrader 5) asked Claude to keep the trading systems in this
project up to date for USDJPY, EURUSD, GBPUSD, XAUUSD, NAS100, US500, BTCUSD, ETHUSD, using
walk-forward, robustness tests and Monte Carlo, long and short (exits: see below). Reply to the user in Thai.

## How it runs (no PC needed)
- GitHub repo (this folder) + **GitHub Actions** `.github/workflows/tradinglab.yml`:
  - every hour: `python -m app.cloud scan` → free data (Yahoo / Binance mirror) → new signals + forward tracking → ntfy push → rebuild site → **GitHub Pages**.
  - Saturday 09:30 Bangkok + "Run workflow" button: `python -m app.cloud research` (full auto research) then scan.
  - push to `main` touching app/web/research/settings: rebuild the site only.
  - The bot commits `data/signals.json` and `data/research/**`. Secrets: `NTFY_TOPIC` (+ optional `TELEGRAM_*`), variable `ACCOUNT_BALANCE`.
- The local server (`start_server.bat`, port 8800) is optional: interactive Lab and MT5 broker data.
- Exits (user's rule, 26 Oct 2026): **only TP, SL or an opposite signal** (exit at the next bar's open and reverse, reason "Rev").
  No time limit and no Friday close — `research.exit_max_hold` / `exit_friday_close` stay false unless the user asks again
  (the code for both is kept: `engine.week_guard` / `friday_cutoff`). A reversal signal carries `reverse_of` and tells the user to close the old order.
- Sizing: every signal risks the fixed `risk_per_trade_pct` (0.25%, user's choice) of `account_balance` (5000 USD; cloud = variable
  `ACCOUNT_BALANCE`). Lots appear in the push, the signal cards and the chart labels; below 0.01 lot the push says what 0.01 would risk.
  The user's manual is the "คู่มือ" page (`renderGuide` / `manualChapters` in `web/app.js`) — keep it in sync with behaviour changes
  (chapters 6–8 explain the testing method step by step, ACTIVE/WATCH/OFF, references and limitations).
- UI rules from the user: the page must not pinch-zoom on iPad (only charts zoom); the "re-run research now" button lives only in Strategy Lab;
  the chart page order is chart → equity curve → system info → latest trades, and its strategy picker is view-only.
- Settings: `data/settings.json` = shared research config (committed). `data/local.json` = balance, ntfy topic, PIN, MT5 path (git-ignored). Never commit secrets.

## Layout
- `app/data.py` — MT5 (broker-exact, when the terminal is running) else Yahoo (FX/futures proxies) / Binance. H1 cached in `data/cache`, H4/D1 resampled.
- `app/strategies.py` — strategy library (signal at bar close, enter next open; shared ATR SL / R:R TP / max-hold exits).
- `app/engine.py` — backtest in R; `app/walkforward.py` — rolling WF + random-entry benchmark; `app/robustness.py` — 8 stress tests; `app/montecarlo.py`.
- `app/research.py` — per-symbol pipeline → `data/research/latest.json` (+ `history/`). Status ACTIVE / WATCH / OFF via gates in `data/settings.json → research.gates` (incl. `min_robust_score`, and `multiple_testing`: OOS t must also beat the Šidák-adjusted threshold for the number of candidates compared — the best of ~20 OOS results is picked, so luck must be priced in). Every candidate keeps its compact OOS trades (`oos_eq`) for the chart page.
- `app/scanner.py` — signals on closed bars, forward tracking with the same rules, ntfy/Telegram push.
- `app/charts.py` (`system_payload`: any strategy on any chart, view-only — chart page picker + equity curve; static build writes `site/data/sys/{SYM}_{TF}_{strategy}.json`), `app/export.py` (static site → `site/`), `app/cloud.py` (Actions entry), `app/server.py` + `web/` (same front-end; `window.TL_STATIC` switches it to static JSON).

## Monday session ("อัปเดตระบบ", "เก็บข้อมูลวิเคราะห์แล้วปรับระบบ", …) — finish within the day
1. `git pull --rebase` (gets the bot's signals + Saturday research). If `data/signals.json` conflicts, take the remote version.
2. Refresh research: `.venv\Scripts\python -W ignore -m app.research` (~1–2 min; uses MT5 if the terminal is open, else free data).
   Skip if `generated` in `data/research/latest.json` is after the most recent Saturday 00:00 Bangkok and the user did not ask.
3. Read `data/research/latest.json` (incl. `robustness`), the newest `data/research/history/` files, and forward results in `data/signals.json`
   (`outcome`, `outcome_r`, `system_status`, `user_status`).
4. For every ACTIVE/WATCH system compare forward-test R with the research OOS: after ≥ 20 forward trades, if forward expectancy is below
   the Monte Carlo 5th-percentile expectancy (`mc_1y.confidence`), add the symbol to `force_off` in `data/settings.json` and say why.
5. Prepend a dated section to `data/research/ai_notes.md` (Thai, short): changes vs `previous` (status/strategy/TF/params/risk/robustness),
   forward-test health, concrete next steps. Shown on the "ระบบเทรด" page.
6. Commit (`data/research`, `data/settings.json`, any code) and `git push` → Actions rebuilds the site; the hourly scanner uses the new systems.
7. Report to the user in a few lines; do not claim profitability the data does not show.

## Improving the strategy library — rules
- Library: 20 strategies (Oct 2026: +tsmom, adx_trend, squeeze_breakout, session_breakout, ema_pullback, range_fade; then +macd_trend, supertrend,
  rsi_momentum, ichimoku_tk, inside_bar, pin_bar, stoch_pullback, zscore_reversion, prev_day_breakout, aroon_trend) → 188 combos per TF, 40 candidates
  per symbol, so the multiple-testing gate asks OOS t ≥ ~3.0. More strategies make passing harder, not easier — say so when the user asks for more.
- New strategy = economic rationale first (written in `rationale`), ≤ 3 signal params, grid ≤ 16 combos. Every addition raises the trial count; mention it.
- Judge only on walk-forward OOS + random benchmark + robustness + MC of OOS trades. Never tune on OOS results, never re-run folds until something passes.
- Never lower `research.gates` or change exit rules on your own to manufacture signals.
- Keep costs realistic (spread + slippage + commission per symbol in `app/config.py` / `symbol_overrides`).
- After code changes: `python -m app.research XAUUSD`, `python -m app.export`, and check the pages (local server and `site/` via a static server).
- Nothing here is investment advice; the user executes trades themself.
