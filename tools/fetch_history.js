/* Long H1 history from Dukascopy (free, FX / gold / US indices back to 2012) -> data/history/{SYMBOL}_H1.csv.gz
 *
 * Runs in GitHub Actions (.github/workflows/history.yml): weekly before the Saturday research, or by hand.
 * The cloud then uses this history plus recent Yahoo bars (app/data.py), so walk-forward gets ~14 years instead of ~2.
 *   node tools/fetch_history.js            all symbols
 *   node tools/fetch_history.js XAUUSD     one symbol
 */
const fs = require("fs");
const path = require("path");
const zlib = require("zlib");
const { getHistoricalRates } = require("dukascopy-node");

const MAP = { EURUSD: "eurusd", GBPUSD: "gbpusd", USDJPY: "usdjpy", XAUUSD: "xauusd", NAS100: "usatechidxusd", US500: "usa500idxusd" };
const START_YEAR = 2012;
const OUT = path.join(__dirname, "..", "data", "history");

async function year(instrument, y) {
  const from = new Date(Date.UTC(y, 0, 1)), to = new Date(Math.min(Date.UTC(y + 1, 0, 1), Date.now()));
  if (from >= to) return [];
  return getHistoricalRates({
    instrument, dates: { from, to }, timeframe: "h1", priceType: "bid", format: "array", volumes: false,
    batchSize: 6, pauseBetweenBatchesMs: 800, retryCount: 4, pauseBetweenRetriesMs: 3000, useCache: true,
    ...(process.env.CHROME_PATH ? { chromePath: process.env.CHROME_PATH } : {}),
  });
}

const LOG = [];
const say = (level, msg) => {   // console + GitHub annotation + data/history/fetch_log.txt (job logs are not always readable)
  const line = `${new Date().toISOString()} ${level} ${msg}`;
  LOG.push(line); console.log(line);
  if (level !== "info") console.log(`::${level === "error" ? "error" : "warning"}::${msg.slice(0, 900)}`);
};

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const want = process.argv.slice(2).map((s) => s.toUpperCase());
  const syms = Object.keys(MAP).filter((s) => !want.length || want.includes(s));
  let failed = 0;
  for (const sym of syms) {
    const rows = [];
    try {
      for (let y = START_YEAR; y <= new Date().getUTCFullYear(); y++) {
        const r = await year(MAP[sym], y);
        rows.push(...r);
        say("info", `${sym} ${y}: ${r.length} bars`);
      }
    } catch (e) {
      failed++;
      say("error", `${sym} failed after ${rows.length} bars: ${e && (e.stack || e.message || e)}`.replace(/\n/g, " | "));
      continue;   // keep the previous file for this symbol
    }
    if (rows.length < 1000) { failed++; say("error", `${sym}: only ${rows.length} bars, file not written`); continue; }
    rows.sort((a, b) => a[0] - b[0]);
    const csv = "time,open,high,low,close\n" + rows.map((r) => [Math.round(r[0] / 1000), r[1], r[2], r[3], r[4]].join(",")).join("\n") + "\n";
    fs.writeFileSync(path.join(OUT, `${sym}_H1.csv.gz`), zlib.gzipSync(csv, { level: 9 }));
    say("warning", `${sym}: ${rows.length} bars ${new Date(rows[0][0]).toISOString()} -> ${new Date(rows[rows.length - 1][0]).toISOString()}`);
  }
  fs.writeFileSync(path.join(OUT, "fetch_log.txt"), LOG.join("\n") + "\n");
  if (failed === syms.length) process.exit(1);
})();
