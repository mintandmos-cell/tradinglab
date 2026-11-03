# TradingLab — runbook for Claude

The user (Thai, trades manually in MetaTrader 5) asked Claude to keep the trading systems in this
project up to date for 20 Exness symbols (2 Nov 2026: EURUSD GBPUSD USDJPY GBPJPY AUDUSD USDCAD · XAUUSD XAGUSD USOIL ·
NAS100(USTEC) US500 DE30 JP225 · NVDA MSFT AMZN META AAPL · BTCUSD ETHUSD — SOL/XRP/LTC are disabled on the user's Exness), using
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
- Sizing (user, 2 Nov 2026): EVERY signal risks 0.5% (`risk_per_trade_pct` = `portfolio_risk_pct` = 0.5), max 1% open risk per
  asset (`max_symbol_risk_pct`); target asked 100%/yr — told the user that needs ~1% risk and 60-80% historical DD. Of `account_balance` (5000 USD; cloud = variable
  `ACCOUNT_BALANCE`). Lots appear in the push, the signal cards and the chart labels; below 0.01 lot the push says what 0.01 would risk.
  The user's manual is the "คู่มือ" page (`renderGuide` / `manualChapters` in `web/app.js`) — keep it in sync with behaviour changes
  (chapters 6–8 explain the testing method step by step, ACTIVE/WATCH/OFF, references and limitations).
- UI rules from the user (27 Oct 2026 redesign): icon-only left rail (phone: pinned icon bar on top), page order
  กราฟ → ระบบเทรด → Strategy Lab | สัญญาณ → พอร์ต MT5 → เครื่องคำนวณ | ตั้งค่า → คู่มือ (2 Nov 2026 reorganisation: 3 nav groups
  `NAV_GROUPS`; พอร์ต MT5 = `renderPort`: robot account, open risk per asset class, positions, closes, symbol tiles); the app opens
  on the chart with the last symbol / TF / system. Every symbol list is grouped by asset class in one order (`ASSET_GROUPS`,
  `SYM_ORDER`, `sortSyms`, `symGroups`: FX · metals/oil · indices · US stocks · crypto) — rail, tiles, calculator, Lab select.
  Systems catalogue has family filter chips. lightweight-charts is vendored (`web/vendor/`, unpkg fallback).
  Chart, Systems and Lab are "workspaces" (no page scroll): title + tabs on top, main view in the middle, right rail
  (H1/H4/D1 → system number picker → symbol abbreviations grouped FX · CMD · IDX · STK · CRY), details strip at the bottom.
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
- Auto-trader on the PC (user's requests 28 Oct 2026; DEMO 10,000 USD, maybe real later): `app/autotrader.py` + `autotrade.bat` drive MT5
  from outside through the MetaTrader5 package (no EA). ONE SIGNAL, THREE PLACES: each UTC hour + 5 s it `ghsync.pull`s latest.json +
  signals.json from GitHub, runs the cloud's own `scanner.scan_once` on MT5 candles (same ids, same alert text pushed at once), then
  `ghsync.publish`es the new signals to GitHub (contents API, token = local.json `github_token` or the PC's git credential) -> site
  rebuild (push path data/signals.json) and the cloud scan finds the id and does not alert again; the cloud commit step unions
  signals.json by id (`tools/merge_signals.py`: for one id the PC's copy wins — its SL/TP are what MT5 holds; the cloud scan
  first fetches origin/main's pc_status + signals (`cloud._sync_remote`), and `ghsync.publish` replaces a cloud copy with the PC's). New ACTIVE signals are executed: market order, visible SL (signal's sl_dist), TP rr x
  SL, comment `TL{no}-{TF}`, magic 5600000, stop-and-reverse, retry 10 min. It ALSO trades
  every fresh ACTIVE signal the cloud alerted (polls GitHub every 2 min, `follow_alerts`: outcome open, not capped, before
  up to 4 candles after the signal bar; once per id via state `done`) - the cloud's feed can show a borderline signal the MT5 candles don't. Risk = signal risk x `risk_scale` (1.0) on the MT5
  balance; below min lot -> min lot (`min_lot_always` "*"); signals the alert marks `capped` are skipped (`follow_cap`). Anti-wipeout
  guard only (user dropped 6 %/3 %): total DD 25 % pause / 30 % close-all, daily 0 = off. Refuses REAL accounts unless `allow_live`.
  While its heartbeat (data/autotrade_state.json) is fresh the local server's scanner loop stays quiet. Scanner skips `force_off`
  systems at once. PRIORITY (28 Oct 2026, user: never miss entries / phone, MT5, web must agree): the PC is the authority -
  after each hourly scan it writes the heartbeat data/pc_status.json to GitHub (`ghsync.put_status`, [skip ci]); the cloud scan
  skips new signals for candles the PC already scanned (`scanner.pc_covered`, PC seen < 2 h). Signals carry `origin` pc|cloud
  (shown in the push). Missed candles: `scan_once` also emits a `late` catch-up when the backtest's open trade came from a
  signal 1-2 bars back. The robot follows open signals up to 4 candles after the signal bar but never chases: skip if price ran
  > `max_chase_r` (0.5 R) in favour or within 0.1 R of SL. The static web reads signals.json + pc_status.json live from the GitHub
  API (`ghLive`), falling back to the Pages copy; the Signals page shows PC online/offline. GitHub cron really fires every
  ~45-50 min, not 10. REMOTE UPDATES: between candles (min 20-50, nothing pending) it exits with code 3 when origin/main has new
  code under app/ tools/ requirements.txt; autotrade.bat loops (pull + restart) - so pushing a fix to main updates the PC. Keep the
  bat itself stable (cmd reads a running .bat by offset). LIVE MT5 CLOCK: bars from the running terminal are converted with the
  server offset measured from the newest tick (`data.mt5_utc_offset`; Exness = UTC+0, unlike the NY-close history files that
  `settings.mt5_server_time` describes) and MT5 bars are never merged with an old cache. Chart: an open alerted signal's levels
  win over the chart's own backtest position (`openPosition`). SAME LEVELS EVERYWHERE (user, 28 Oct 2026): the robot places the
  alert's exact SL/TP prices (`alert_levels`, lots sized on the real distance); forward tracking (`scanner._track`) uses the
  alerted SL/TP too. The robot publishes its MT5 positions + recent closes (with signal_id, reason SL/TP) in pc_status.json
  whenever they change (`watch_positions`, closes pushed to ntfy); the Signals page shows them (robot KPI, table, card banner).
  The card button 'ฉันเปิดเอง' is only a manual-account log. AUDIT 28 Oct 2026: `sync_exits` closes a robot position whose signal
  the PC's tracking exited by 'Rev'; a capped reverse still closes the old position; a failed hourly scan is retried (last_hour
  only advances on success); scans report `checked` + `errors` (incl. stale H1 data) in pc_status / cloud_state and the web
  warns; data/cloud_state.json is committed by the bot so the static site knows the cloud's last scan; the web caches GitHub
  API reads 3 min (60 req/h limit). PC folder: D:\Backtest\TradingLab (bridge root D:\Backtest).
  Never help hide automation from a prop firm (The5ers needs written approval for automated trading).
- Settings: `data/settings.json` = shared research config (committed). `data/local.json` = balance, ntfy topic, PIN, MT5 path (git-ignored). Never commit secrets.

## Real-trading upgrades (user: "do everything", 2 Nov 2026)
- COSTS: `data/exness/costs.json` (PC task `cost_stats` -> `autotrader.broker_costs`: 75th pct of the H1 bars' spread over ~3 y,
  swap_long/short + swap_mode + triple day) -> `config.broker_costs` merges into `symbol_spec` (spread, swap in price/night).
  `engine.rollover_weights` / `swap_r`: swap charged per rollover (21:00 UTC; crypto daily, others Mon-Fri with the triple day),
  only charges, credits ignored; trades carry `swap_r` (included in `r` and `cost_r`). Random benchmark pays it too.
- TRAILING: param `trail_atr` (0 = off); trend families also try every no-TP combo with `TRAIL_ATR` 3.0 (grids up to 32).
  The stop follows the best price of COMPLETED bars only (backtest `simulate(trail=)`, tracking `scanner._track` -> `stop_now`,
  robot `trail_positions` moves the MT5 SL after each hourly scan; only tightens). Exit reason "Trail".
- 500 SYSTEMS (user, 3 Nov 2026): `app/strategies_mr.py` = 391 pullback / mean-reversion systems #127-#517 (24 triggers x 17
  market filters minus 5 pairs that failed tests/check_strategies, `DROPPED`; ids `mr_{trigger}_{filter}`, families Pullback /
  Mean reversion, grid 8 = 1 signal param x SL 1.5/2.5 x TP 1.0/1.5). `python -m tests.check_strategies mr_` checks one family.
  Research matrix: 20 symbols x 3 TF x 4 chunks = 240 jobs. latest.json keeps SLIM candidates (`research.slim`); full records in
  `data/research/cands/{SYM}.json.gz` (`research.candidates`, Lab page via /api/research/candidates = site data/research/{SYM}.json).
  Full site payload build covers the 109 core systems + MR systems that are not OFF.
- TESTING UPGRADES (user, 3 Nov 2026): `research.swap_mode` "free" (MT5 deals showed the Pro account books NO swap;
  `config.symbol_spec` zeroes swaps) | "charged"; robot `swap_watch` checks booked swaps daily (push + pc_status swap_watch).
  Entry cost = spread of the entry bar's UTC hour (`engine.cost_by_bar`, PC export `spread_by_hour_p75`). Gates use the
  t of MONTHLY OOS R (`research.monthly_t`, `t_of`, settings t_basis) on the OOS BEFORE the sealed holdout (`split_oos`,
  `oos_sel`; `holdout_months` 12 = 9th gate "holdout expectancy >= 0", < 3 trades = cannot judge). MC = block bootstrap
  (`run_mc(method="block")`, ~1 month of trades per block).
- SWAP AUTO + COMPARISON (user, 3 Nov 2026): `config.swap_mode()` = env TL_SWAP_MODE > flag file data/exness/swap_charged.json
  (the robot's daily `swap_watch` pushes it via `ghsync.put_file` the first day MT5 books a swap; the push re-runs research,
  workflow plan checks commits for that file name — never put that file name in a commit message) > settings. Comparison run:
  Run workflow -> research, variant = swap -> parts with TL_SWAP_MODE=charged -> `app.cloud compare` (`app/compare.py`) judges
  them in a temp research folder and writes only data/research/compare/swap_charged.json; Lab step 8 shows free vs swap.
  Run it AFTER the live run (it compares against latest.json at merge time; re-judge with mode merge + from_run + variant swap).
- DATA QUALITY (3 Nov 2026): `tools/check_history.py` -> data/research/data_quality.json (holes, snap-back spikes, flat, bad
  bars per year). `data._seed_exness` starts after the last thin month (<= 120 bars) and after the last 7-day hole
  (BTCUSD 2014-2017 was sparse -> starts Feb 2018); `history_source` = "exness@YYYY-MM" so a new start re-judges.
- Lab step 8: long/short split (`research.sides_split`) + buy-and-hold over the OOS span (`research.buy_hold`) + swap compare.
  Robustness noise test 30 sims (`research.noise_sims`). SLIPPAGE: cloud scan copies the PC's fills into
  data/research/fills_log.json (`fills.collect`); with >= 50 fills and >= 10 for a symbol, `config.symbol_spec` uses the p75
  real slippage (never below half the estimate); พอร์ต MT5 page shows it.
- WFA WINDOWS PER TF (user, 3 Nov 2026, later the same day — replaces the expanding default): settings research.wfa =
  H1 rolling IS 24 m / OOS 12 m, H4 and D1 rolling IS 36 m / OOS 12 m; folds counted BACKWARDS from the cut-off (the last fold
  ends at the start of the current quarter); live set re-picked each quarterly run (lock 3 months, hysteresis). Robustness
  variants per TF: IS +12 m, IS -12 m, OOS 6 m, expanding. Short histories (stocks / indices since 2019) leave few D1 OOS trades.
- WFA (user, 3 Nov 2026, fixed before results): in-sample = all history before each quarter (`is_mode` expanding, >= 24 m);
  pick on a plateau (`plateau_weight` 0.5: own IS t + mean t of grid neighbours, `walkforward.neighbours`); keep last quarter's
  set unless another scores >= `switch_margin` 0.5 better. Robustness WF variants: expanding 36/3, 24/6, rolling 12/3, 24/3.
- TEST LEDGER (user, 2 Nov 2026): `data/research/trial_ledger.json` = every SYM|strategy|TF ever tested (>= 10 OOS trades),
  never delete entries; `research.ghost_tests` adds ledger tests missing from today's candidates (deleted / retired systems) to
  the luck-gate count, `record_tests` runs at every merge. Portfolio count = every strategy x TF ever tested.
- LUCK GATE: `research.effective_tests` = Li & Ji eigenvalue count of independent tests from weekly OOS R correlations
  (settings research.effective_trials); n = M_eff x (raw incl. retired) / usable. Same for the portfolio test (monthly R).
- TRIALS: `trial_systems` per symbol = WATCH candidates failing ONLY the luck gate (max 2, H4/D1 first), status "TRIAL",
  risk `trials.risk_pct` 0.1, no push, robot trades them on DEMO accounts only (`trade_trials`, `_tradable`).
- RISK: `cluster_risk_pct` caps open ACTIVE risk per group of markets that move together (`scanner.CLUSTERS`, capped + note).
  Kill switch: `scanner.kill_switch` -> `data/research/auto_off.json` when a live system's forward DD > `kill_dd_mult` (2) x its
  OOS max_dd_r (>= 5 closed trades this quarter); skipped like force_off, push sent once.
- EXECUTION: robot records each fill (`fills` in pc_status: slippage points vs quote, drift in R vs the signal entry, delay);
  พอร์ต MT5 page shows them, cluster risk and auto-stopped systems. Lab step 7 shows losing-streak stats (`streakHtml`).

## Layout
- `app/data.py` — MT5 (broker-exact, when the terminal is running) else Yahoo (FX/futures proxies) / Binance.
  With the repo secret `TWELVE_DATA_KEY` (user's request 27 Oct 2026) the newest FX/gold H1 bars come from Twelve Data spot
  (`data.with_twelve`, fetched at most once per hour per symbol, cache in data/cache, falls back to Yahoo); `data.price_source` labels it. H1 cached in `data/cache`, H4/D1 resampled.
  Long history: `data/history/{SYM}_H1.csv.gz` (from `export_mt5_history.bat` on the PC; Dukascopy blocks GitHub runners — `tools/fetch_history.js`
  / workflow History kept for manual retry) is joined with Yahoo bars by `with_history` (ratio-scaled at the overlap). A push touching
  `data/history/` makes the cloud run the full research. MT5 bar times are broker server time ("NY close" = New York + 7 h); `server_to_utc`
  (`settings.mt5_server_time`) converts them to UTC everywhere (seed files and live MT5), so FX weeks open Sunday 21/22 UTC.
- `app/strategies.py` — strategy library (signal at bar close, enter next open; shared ATR SL / R:R TP / max-hold exits);
  `app/strategies_trend.py` adds 69 trend systems (#58-#126) -> 109 active, of which 100 trend / breakout / momentum.
- Method name (user asked for one, 2 Nov 2026): **QWG — Quarterly Walk-Forward Gauntlet** (WF OOS by quarter -> random-entry
  benchmark -> 8 robustness tests -> Monte Carlo -> Šidák multiple-testing gate).
- Exness data (user's rule 2 Nov 2026: backtest on the broker's own prices): the PC robot runs jobs from `data/pc_tasks.json`
  (one step per 2-min poll: symbol list -> data/exness/symbols.json, H1 history -> data/history_exness/{SYM}_H1.csv.gz in UTC,
  report data/exness/task_report.json). `data._seed` prefers these files over data/history; free feeds only extend the tail.
- `app/engine.py` — backtest in R; `app/walkforward.py` — rolling WF + random-entry benchmark; `app/robustness.py` — 8 stress tests; `app/montecarlo.py`.
- `app/research.py` — every strategy × TF × symbol goes through **every** step (WF OOS, random benchmark, 8 robustness tests, MC) via
  `research_part` → `merge`. QUARTER RULES (user, 27 Oct 2026): every candidate is cut at the same research cut-off = start of the
  current quarter on the broker clock (`research_cutoff`, `data.period_start`); WF OOS folds are calendar quarters; live params = best
  in the 12 months before the cut-off, traded unchanged for the quarter. EVERY ACTIVE candidate is traded (`systems` list per symbol,
  several per symbol/TF allowed, MT5 account is Hedging). A mid-quarter re-run (new strategies, button) uses the same cut-off; systems
  already ACTIVE this quarter are kept (`kept`) ONLY when the multiple-testing gate is the one gate they now fail AND the price
  history is the same (`history_source` exness|mt5|free; user, 2 Nov 2026 — the old rule kept systems failing 6/8 gates after the
  switch to Exness prices). Same rule for portfolio systems (`portfolio.history`). `tools/rejudge.py` re-judges latest.json
  from its stored candidates without re-running tests; new ones must pass.
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
