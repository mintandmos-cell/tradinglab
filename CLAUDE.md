# TradingLab — runbook for Claude

The user (Thai, trades manually in MetaTrader 5) asked Claude to keep the trading systems in this
project up to date for USDJPY, EURUSD, GBPUSD, XAUUSD, NAS100, US500, BTCUSD, ETHUSD, using
walk-forward, robustness tests and Monte Carlo, long and short (exits: see below). Reply to the user in Thai.

## How it runs (no PC needed)
- GitHub repo (this folder) + **GitHub Actions** `.github/workflows/tradinglab.yml`:
  - every 10 minutes (cron 1,11,21… — GitHub delays a lone hourly cron by ~40 min): `python -m app.cloud scan` → free data (Yahoo / Binance mirror) → new signals + forward tracking → ntfy push → rebuild site → **GitHub Pages**.
  - Research is QUARTERLY (user's rule, 27 Oct 2026): cron 1 Jan/Apr/Jul/Oct 09:30 Bangkok; the Saturday cron only runs it if this
    quarter's research is missing (mode "due" in `plan`). "Run workflow" button (mode research) any time: job `plan` (prefetch prices → cache) → matrix job `part`
    (8 symbols × 3 TF, `python -m app.cloud part SYM TF`, offline `TL_OFFLINE=1`) → job `run` (`app.cloud merge` = gates/status + scan + site).
    Research has its own concurrency group so queued hourly scans never cancel it. Mode `test` = one ntfy test push.
  - push to `main` touching app/web/research/settings: rebuild the site only.
  - The bot commits `data/signals.json` and `data/research/**`. Secrets: `NTFY_TOPIC`, `TWELVE_DATA_KEY` (+ optional `TELEGRAM_*`), variable `ACCOUNT_BALANCE`.
- The local server (`start_server.bat`, port 8800) is optional: interactive Lab and MT5 broker data.
- Exits (user's rule, 26 Oct 2026): **only TP, SL or an opposite signal** (exit at the next bar's open and reverse, reason "Rev").
  No time limit and no Friday close — `research.exit_max_hold` / `exit_friday_close` stay false unless the user asks again
  (the code for both is kept: `engine.week_guard` / `friday_cutoff`). A reversal signal carries `reverse_of` and tells the user to close the old order.
- Sizing: every signal risks the fixed `risk_per_trade_pct` (0.25%, user's choice) of `account_balance` (5000 USD; cloud = variable
  `ACCOUNT_BALANCE`). Lots appear in the push, the signal cards and the chart labels; below 0.01 lot the push says what 0.01 would risk.
  The user's manual is the "คู่มือ" page (`renderGuide` / `manualChapters` in `web/app.js`) — keep it in sync with behaviour changes
  (chapters 6–8 explain the testing method step by step, ACTIVE/WATCH/OFF, references and limitations).
- UI rules from the user (27 Oct 2026 redesign): icon-only left rail (phone: pinned icon bar on top), page order
  กราฟ → ระบบเทรด → Strategy Lab → สัญญาณ → เครื่องคำนวณ → ตั้งค่า → คู่มือ; the app opens on the chart with the last symbol / TF / system.
  Chart, Systems and Lab are "workspaces" (no page scroll): title + tabs on top, main view in the middle, right rail
  (H1/H4/D1 → system number picker 1–50 → symbol abbreviations EU GU UJ XAU NAS SPX BTC ETH), details strip at the bottom.
  Chart: latest candle centred, tabs chart / latest trades. Systems: equity curve / system details (indicators, params, rules
  from `app/strategy_docs.py`) / catalogue. Lab: step-by-step results of one system, default = the symbol's best; the
  "re-run research now" button lives only here. Page must not pinch-zoom on iPad (only charts zoom).
  Back button (Android, user's rule): closes the open popup (`Back` in web/app.js); pages never add history entries (`go()` replaces),
  so with nothing open, back leaves the app.
  ⚡ quick pick at the top of the rail = ACTIVE → WATCH candidates (any strategy/TF) sorted by status then t (`research.picks`, static `picks.json`).
- Live prices (display only, never used for signals): forming candle updated every 1 s — BTC/ETH from the Binance WebSocket, FX/gold/indices
  from Finnhub (user pastes a free key in Settings; OANDA symbols). Device prefs (theme, home page, candle colours, bars, live, key) live in
  browser localStorage `tl.prefs` with a backup code. Cross-device sync (user's request): `data/sync.json` = those prefs + personal
  settings ENCRYPTED (AES-GCM, PBKDF2-SHA256 600k, passphrase given to the user in chat 27 Oct 2026; repo is public, never commit
  keys in plain text). Browser `Sync` object reads it via the GitHub API and writes it back with a GitHub token kept inside the blob.
  The chart tail is replaced by real recent H1 candles from the live feed's family (Binance klines for BTC/ETH; Twelve Data with the
  user's optional free key for FX/gold) — `recentBars` / `aggregate` in `web/app.js`. H4/D1 are cut on the BROKER clock everywhere
  (`data.resample` via `utc_to_server`, JS `bucketStart`), like MT5: H4 = 00/04/08… server time, a day = New York close.
- Auto-trader on the PC (user's request 28 Oct 2026, DEMO account 10,000 USD): `app/autotrader.py` + `autotrade.bat` drive MT5 from
  outside through the MetaTrader5 package (no EA). Each closed H1 bar: ACTIVE systems from GitHub latest.json, signals on MT5 broker
  candles with the same rules as the scanner, market order + visible SL/TP, comment `TL{no}-{TF}`, magic 5600000, stop-and-reverse,
  risk = system risk × `risk_scale` (0.35), lot below min → skip (`min_lot_up_to_pct` 0), per-symbol cap; EXCEPT `min_lot_always`
  (["XAUUSD"], user's choice 28 Oct 2026): gold always trades the minimum lot, no per-symbol cap. Opens/closes push "🤖 DEMO" via ntfy at once. Equity guards: total DD
  pause 5 % / close-all 5.6 % (limit 6 %), daily pause 2.5 % / close-all 2.8 % (limit 3 %). Refuses REAL accounts unless
  `allow_live`. Config `data/local.json → autotrade`; log `data/autotrade_log.jsonl`. Cloud alerts unchanged. Never help hide
  automation from a prop firm (The5ers needs written approval for automated trading).
- Settings: `data/settings.json` = shared research config (committed). `data/local.json` = balance, ntfy topic, PIN, MT5 path (git-ignored). Never commit secrets.

## Layout
- `app/data.py` — MT5 (broker-exact, when the terminal is running) else Yahoo (FX/futures proxies) / Binance.
  With the repo secret `TWELVE_DATA_KEY` (user's request 27 Oct 2026) the newest FX/gold H1 bars come from Twelve Data spot
  (`data.with_twelve`, fetched at most once per hour per symbol, cache in data/cache, falls back to Yahoo); `data.price_source` labels it. H1 cached in `data/cache`, H4/D1 resampled.
  Long history: `data/history/{SYM}_H1.csv.gz` (from `export_mt5_history.bat` on the PC; Dukascopy blocks GitHub runners — `tools/fetch_history.js`
  / workflow History kept for manual retry) is joined with Yahoo bars by `with_history` (ratio-scaled at the overlap). A push touching
  `data/history/` makes the cloud run the full research. MT5 bar times are broker server time ("NY close" = New York + 7 h); `server_to_utc`
  (`settings.mt5_server_time`) converts them to UTC everywhere (seed files and live MT5), so FX weeks open Sunday 21/22 UTC.
- `app/strategies.py` — strategy library (50) (signal at bar close, enter next open; shared ATR SL / R:R TP / max-hold exits).
- `app/engine.py` — backtest in R; `app/walkforward.py` — rolling WF + random-entry benchmark; `app/robustness.py` — 8 stress tests; `app/montecarlo.py`.
- `app/research.py` — every strategy × TF × symbol goes through **every** step (WF OOS, random benchmark, 8 robustness tests, MC) via
  `research_part` → `merge`. QUARTER RULES (user, 27 Oct 2026): every candidate is cut at the same research cut-off = start of the
  current quarter on the broker clock (`research_cutoff`, `data.period_start`); WF OOS folds are calendar quarters; live params = best
  in the 12 months before the cut-off, traded unchanged for the quarter. EVERY ACTIVE candidate is traded (`systems` list per symbol,
  several per symbol/TF allowed, MT5 account is Hedging). A mid-quarter re-run (new strategies, button) uses the same cut-off; systems
  already ACTIVE this quarter are kept (`kept`) even if the stricter multiple-testing gate now fails them; new ones must pass.
  Early stop only via `force_off` ("SYM" or "SYM:strategy:TF", Monday forward check). Open risk of all systems of one symbol is capped at
  `max_symbol_risk_pct` (0.75%): extra signals are recorded with `capped` (no push). Push title/comment carry the system tag `TL{no}-{TF}`. Summaries →
  `data/research/latest.json` (+ `history/`); heavy per-candidate data (`oos_eq`, folds) → `data/research/oos/{SYM}_{TF}.json.gz` (`research.heavy`). Status ACTIVE / WATCH / OFF via gates in `data/settings.json → research.gates` (incl. `min_robust_score`, and `multiple_testing`: OOS t must also beat the Šidák-adjusted threshold for the number of candidates compared — the best of ~20 OOS results is picked, so luck must be priced in). Robustness noise test = 10 sims.
- `app/scanner.py` — signals on closed bars, forward tracking with the same rules, ntfy/Telegram push.
- `app/charts.py` (`system_payload`: any strategy on any chart, view-only — chart page picker + equity curve; static build writes `site/data/sys/{SYM}_{TF}_{strategy}.json`), `app/export.py` (static site → `site/`), `app/cloud.py` (Actions entry), `app/server.py` + `web/` (same front-end; `window.TL_STATIC` switches it to static JSON).

## Monday session ("อัปเดตระบบ", "เก็บข้อมูลวิเคราะห์แล้วปรับระบบ", …) — finish within the day
1. `git pull --rebase` (gets the bot's signals + Saturday research). If `data/signals.json` conflicts, take the remote version.
2. Research is quarterly; only re-run mid-quarter if the user asks or the library changed (same cut-off, so results only add systems). Cloud: dispatch the workflow with mode research (~1–2 h). Locally
   `.venv\Scripts\python -W ignore -m app.research` runs all parts in sequence (many hours with 50 strategies).
   Skip if `generated` in `data/research/latest.json` is after this quarter's cut-off and the user did not ask.
3. Read `data/research/latest.json` (incl. `robustness`), the newest `data/research/history/` files, and forward results in `data/signals.json`
   (`outcome`, `outcome_r`, `system_status`, `user_status`).
4. For every ACTIVE system (each entry of `systems`) compare forward-test R with its research OOS: after ≥ 20 forward trades, if forward
   expectancy is below the Monte Carlo 5th-percentile expectancy (`mc_1y.confidence` of that candidate), add "SYM:strategy:TF" to
   `force_off` in `data/settings.json` and say why.
5. Prepend a dated section to `data/research/ai_notes.md` (Thai, short): changes vs `previous` (status/strategy/TF/params/risk/robustness),
   forward-test health, concrete next steps. Shown on the "ระบบเทรด" page.
6. Commit (`data/research`, `data/settings.json`, any code) and `git push` → Actions rebuilds the site; the hourly scanner uses the new systems.
7. Report to the user in a few lines; do not claim profitability the data does not show.

## Improving the strategy library — rules
- Library: 40 active strategies (Oct 2026: 4 → 10 → 20 → 50; then 17 losers retired to `RETIRED` and 7 research-backed added, #51–57).
  System numbers are STABLE (`STRATEGIES[id]["no"]`, used in MT5 comments) — never renumber. Retired candidates stay counted in
  the Šidák gate (`research.n_retired_candidates`). Trend / breakout / momentum families also try rr = 0 (no TP: exit by SL or
  opposite signal; user's choice 27 Oct 2026) — grids up to 24 combos for those.
- Portfolio test (user's choice 27 Oct 2026): `research.portfolio_research` pools each strategy × TF across all symbols (monthly R,
  gates: ≥36 months, monthly t ≥ Šidák for all candidates ever, ≥60% symbols positive, pooled MC PF@95% ≥ 1, median random pct ≥ 75,
  mean robustness ≥ 50). ACTIVE portfolio systems are appended to every symbol's `systems` with `source: "portfolio"`.
  They risk `portfolio_risk_pct` (0.10%, user's choice 27 Oct 2026) per trade instead of 0.25%.
  The multiple-testing gate then asks OOS t ≥ ~3.4. More strategies make passing harder, not easier — say so when the user asks for more.
  `python -m tests.check_strategies` must pass for every strategy (no look-ahead: future price changes never alter past signals;
  random-walk mean R ≈ 0; every grid combo runs). Volume-based indicators are avoided (Yahoo FX volume is empty).
- New strategy = economic rationale first (written in `rationale`), ≤ 3 signal params, grid ≤ 16 combos (24 with the no-TP option). Every addition raises the trial count; mention it.
- Judge only on walk-forward OOS + random benchmark + robustness + MC of OOS trades. Never tune on OOS results, never re-run folds until something passes.
- Never lower `research.gates` or change exit rules on your own to manufacture signals.
- Keep costs realistic (spread + slippage + commission per symbol in `app/config.py` / `symbol_overrides`).
- After code changes: `python -m app.research XAUUSD`, `python -m app.export`, and check the pages (local server and `site/` via a static server).
- Nothing here is investment advice; the user executes trades themself.
