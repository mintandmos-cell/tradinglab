/* TradingLab web app — vanilla JS, hash router, TradingView lightweight-charts v4. */
"use strict";

// ------------------------------------------------------------------ helpers
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const BKK_OFFSET = 7 * 3600;
const store = {
  get(k, d) { try { const v = localStorage.getItem("tl." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("tl." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};
const S = { symbols: [], strategies: [], settings: null, status: null, lab: { bt: null, wf: null, mc: null }, charts: [], candCache: {} };

function toast(msg, ms = 2600) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg; document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

// ------------------------------------------------------------------ static (GitHub Pages) mode
// config.js sets window.TL_STATIC on the cloud build: no server, the "API" is pre-built JSON + browser maths.
const STATIC = window.TL_STATIC || null;
const local = {  // per-device personal settings in static mode
  get() { return { account_balance: STATIC?.account_balance || 5000, risk_pct: null, mt5_symbol_map: {}, ...store.get("personal", {}) }; },
  set(v) { store.set("personal", { ...local.get(), ...v }); Sync.changed(); },
};
function repoUrl(path = "") {
  if (STATIC?.repo) return `https://github.com/${STATIC.repo}${path}`;
  const owner = location.hostname.split(".")[0], repo = location.pathname.split("/").filter(Boolean)[0] || "";
  return `https://github.com/${owner}/${repo}${path}`;
}
const actionsUrl = () => repoUrl("/actions/workflows/tradinglab.yml");

function valuePerUnit(sym, price) {  // USD P/L of 1.00 lot for a 1.0 price move (same rule as app/sizing.py)
  const sp = specOf(sym);
  if (sp.quote === "USD") return +sp.contract;
  if (sym.startsWith("USD")) return +sp.contract / price;
  return +sp.contract;
}
function sizeLocal(sym, entry, sl, riskPct, balance, tp) {
  const dist = Math.abs(entry - sl), vpu = valuePerUnit(sym, entry), sp = specOf(sym);
  const riskUsd = balance * riskPct / 100, raw = dist > 0 ? riskUsd / (dist * vpu) : 0;
  const lots = Math.floor(raw / 0.01 + 1e-9) * 0.01;
  const out = { lots: lots >= 0.01 ? +lots.toFixed(2) : 0, raw_lots: +raw.toFixed(4), risk_usd: +riskUsd.toFixed(2),
    actual_risk_usd: +(lots * dist * vpu).toFixed(2), value_per_point_per_lot: vpu, below_min_lot: lots < 0.01,
    sl_pips: +(dist / sp.pip).toFixed(1), min_lot_risk_usd: +(0.01 * dist * vpu).toFixed(2), mt5: null };
  if (tp) Object.assign(out, { rr: dist ? +(Math.abs(tp - entry) / dist).toFixed(2) : null, tp_pips: +(Math.abs(tp - entry) / sp.pip).toFixed(1),
    reward_usd: +(out.lots * Math.abs(tp - entry) * vpu).toFixed(2) });
  return out;
}
// risk % per trade: this device's choice (cloud site) → the shared fixed risk → the system's Monte Carlo risk
const effRisk = (r) => +((STATIC && local.get().risk_pct) || S.settings?.risk_per_trade_pct || r) || 0;
const myBalance = () => +(STATIC ? local.get().account_balance : S.settings?.account_balance) || 0;
function lotLabel(sym, entry, sl, sysRisk, fixed = false) {  // lots for an open position on the chart, same maths as the signal cards
  const z = sizeLocal(sym, entry, sl, fixed ? sysRisk : effRisk(sysRisk), myBalance());
  return z.below_min_lot ? "<0.01 lot" : `${z.lots.toFixed(2)} lot`;
}

const LOAD_T = Date.now();  // every page load (and the 🔄 button) fetches fresh data
async function staticGet(file, text = false) {
  const res = await fetch(`data/${file}?v=${LOAD_T}`, { cache: "no-store" });  // the cloud rebuilds hourly
  if (!res.ok) throw new Error(`${file}: ${res.status}`);
  return text ? res.text() : res.json();
}
// live files straight from the repository (the PC auto-trader publishes signals there seconds after a candle closes;
// the Pages copy only follows after a site build) — falls back to the Pages copy
const GH_REPO = "mintandmos-cell/tradinglab";
const _ghCache = {};   // one GitHub API read per file per 3 min (the free API allows 60 an hour per phone / network)
async function ghLive(path) {
  const hit = _ghCache[path];
  if (hit && Date.now() - hit.t < 180000) return hit.v;
  const v = await ghFetch(path);
  if (v) _ghCache[path] = { t: Date.now(), v };
  return v || (hit ? hit.v : null);
}
async function ghFetch(path) {
  try {
    const r = await fetch(`https://api.github.com/repos/${GH_REPO}/contents/${path}?ref=main&t=${Date.now()}`,
      { headers: { Accept: "application/vnd.github.raw+json" }, cache: "no-store" });
    if (r.ok) return await r.json();
  } catch { /* offline / rate limit */ }
  return null;
}
async function pcStatus() { return ghLive("data/pc_status.json"); }
async function staticApi(path, opts = {}) {
  const [p, q] = path.split("?"); const qp = Object.fromEntries(new URLSearchParams(q || ""));
  const P = local.get(), post = (opts.method || "GET") === "POST";
  const body = opts.body && typeof opts.body !== "string" ? opts.body : {};
  if (p === "/api/status") return { static: true, mt5: { connected: false }, scanner: { last_scan: STATIC.last_scan, last_error: STATIC.scan_error },
    research: { running: false }, research_generated: STATIC.research_generated, schedule: "weekly" };
  if (p === "/api/symbols") return (await staticGet("symbols.json")).map((x) => ({ ...x, mt5_symbol: P.mt5_symbol_map[x.symbol] || x.mt5_symbol }));
  if (p === "/api/strategies") return staticGet("strategies.json");
  if (p === "/api/settings" && !post) return { ...(await staticGet("settings.json")), account_balance: P.account_balance, my_risk_pct: P.risk_pct, notify: {}, static: true };
  if (p === "/api/settings" && post) { local.set(body); return staticApi("/api/settings"); }
  if (p === "/api/research") return staticGet("research.json");
  if (p === "/api/research/candidates") return staticGet(`research/${qp.symbol}.json`).catch(() => []);
  if (p === "/api/research/notes") return staticGet("notes.md", true);
  if (p === "/api/research/history") return staticGet("history.json");
  if (p === "/api/research/state") return { running: false };
  if (p === "/api/forward") return staticGet("forward.json");
  if (p === "/api/scan") return { new: [] };
  if (p === "/api/candles") return staticGet(`chart/${qp.symbol}_${qp.tf}.json`);
  if (p === "/api/picks") return staticGet("picks.json");
  if (p === "/api/compare") return staticGet("compare.json").catch(() => ({}));
  if (p === "/api/slippage") return staticGet("slippage.json").catch(() => ({}));
  if (p === "/api/data_quality") return staticGet("data_quality.json").catch(() => ({}));
  if (p === "/api/candles_year") return staticGet(`chart/${qp.symbol}_${qp.tf}/${qp.year}.json`);
  if (p === "/api/system") return staticGet(`sys/${qp.symbol}_${qp.tf}_${qp.strategy}.json`).catch(() => {
    // not built yet (new symbol / strategy until the next full build): candles still show, without this system's overlay
    const st = stratById(qp.strategy) || {};
    return { symbol: qp.symbol, tf: qp.tf, strategy: qp.strategy, name: st.name || qp.strategy, family: st.family || "", params: {},
      params_source: "none", is_live: false, live: {}, digits: specOf(qp.symbol)?.digits ?? 2, years: 0, t: [], lines: {},
      metrics: {}, equity: [], trades: [], last_signal: 0, open_trade: null, oos: null, missing: true };
  });
  if (p === "/api/size") return sizeLocal(qp.symbol, +qp.entry, +qp.sl, +qp.risk_pct, +(qp.balance || P.account_balance), qp.tp ? +qp.tp : null);
  if (p.startsWith("/api/signals/") && post) {  // taken / skipped is remembered on this device only
    const marks = store.get("sigMarks", {}); marks[decodeURIComponent(p.slice(13))] = body; store.set("sigMarks", marks); return body;
  }
  if (p === "/api/signals") {
    const marks = store.get("sigMarks", {}), sigs = (await ghLive("data/signals.json")) || await staticGet("signals.json");
    return sigs.slice(0, +(qp.limit || 200)).map((s) => {
      const risk = s.source === "portfolio" ? s.risk_pct : effRisk(s.risk_pct), z = sizeLocal(s.symbol, s.entry, s.sl, risk, P.account_balance, s.tp);
      return { ...s, ...(marks[s.id] || {}), mt5_symbol: P.mt5_symbol_map[s.symbol] || s.mt5_symbol, risk_pct: risk,
        lots: z.lots, raw_lots: z.raw_lots, risk_usd: z.actual_risk_usd, reward_usd: z.reward_usd, below_min_lot: z.below_min_lot,
        balance: P.account_balance, min_lot_risk_usd: z.min_lot_risk_usd, min_lot_risk_pct: +(100 * z.min_lot_risk_usd / P.account_balance).toFixed(2) };
    });
  }
  throw new Error("ฟังก์ชันนี้ใช้ได้เมื่อรันบนคอม (start_server.bat) เท่านั้น");
}

async function api(path, opts = {}) {
  if (STATIC) return staticApi(path, opts);
  const headers ={ "Content-Type": "application/json", "X-Pin": store.get("pin", "") };
  const res = await fetch(path, { ...opts, headers: { ...headers, ...(opts.headers || {}) },
    body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body });
  if (res.status === 401) {
    const pin = prompt("ใส่ PIN เพื่อเข้าใช้งาน");
    if (pin !== null) { store.set("pin", pin); return api(path, opts); }
  }
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = j.detail || msg; } catch { /* not json */ }
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : res.text();
}

const fnum = (v, d = 2) => (v === null || v === undefined || Number.isNaN(+v) ? "–" : (+v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
const fr = (v, d = 2) => (v === null || v === undefined ? "–" : `${v > 0 ? "+" : ""}${fnum(v, d)}R`);
const cls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
const fpx = (v, digits) => (v === null || v === undefined ? "–" : (+v).toFixed(digits));
function fdt(x, withYear = false) {
  if (x === null || x === undefined || x === "") return "–";
  const d = typeof x === "number" ? new Date(x * 1000) : new Date(x);
  return d.toLocaleString("en-GB", { timeZone: "Asia/Bangkok", day: "2-digit", month: "short", ...(withYear ? { year: "numeric" } : {}), hour: "2-digit", minute: "2-digit", hour12: false });
}
function ago(iso) {
  if (!iso) return "–";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "เมื่อสักครู่"; if (s < 3600) return `${Math.round(s / 60)} นาทีที่แล้ว`;
  if (s < 86400) return `${Math.round(s / 3600)} ชม.ที่แล้ว`; return `${Math.round(s / 86400)} วันที่แล้ว`;
}
const specOf = (sym) => S.symbols.find((x) => x.symbol === sym) || { digits: 5, pip: 0.0001 };
const stratName = (id) => (S.strategies.find((s) => s.id === id) || {}).name || id;
const paramText = (p) => (p ? Object.entries(p).filter(([k]) => k !== "max_hold_h").map(([k, v]) => `${k}=${v}`).join(" · ") : "–");
const kpi = (k, v, s = "", c = "") => `<div class="kpi"><div class="k">${k}</div><div class="v ${c}">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`;
const fusd = (v, sign = true) => (v < 0 ? "-" : sign ? "+" : "") + "$" + fnum(Math.abs(+v || 0), 2);
const reasonTh = (r) => ({ Fri: "ปิดศุกร์", Time: "หมดเวลา", Rev: "กลับทิศ" }[r] || r);
const statusBadge = (s) => `<span class="badge ${esc(s)}">${esc(s || "–")}</span>`;
function qs() { const q = location.hash.split("?")[1] || ""; return Object.fromEntries(new URLSearchParams(q)); }
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
function md(text) {
  const lines = esc(text).split("\n"); let html = "", inList = false;
  for (const l of lines) {
    const inline = (s) => s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`(.+?)`/g, "<code>$1</code>")
      .replace(/\[([^\]]+)\]\((https:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>').replace(/\*([^*\s][^*]*?)\*/g, "<i>$1</i>");
    if (/^\s*[-*] /.test(l)) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${inline(l.replace(/^\s*[-*] /, ""))}</li>`; continue; }
    if (inList) { html += "</ul>"; inList = false; }
    const h = l.match(/^(#{1,3}) (.*)/);
    if (h) html += `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`; else if (l.trim()) html += `<p>${inline(l)}</p>`;
  }
  return html + (inList ? "</ul>" : "");
}

// ------------------------------------------------------------------ charts
function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
function makeChart(el, opts = {}) {
  const chart = LightweightCharts.createChart(el, {
    autoSize: true,
    layout: { background: { type: "solid", color: cssVar("--panel") }, textColor: cssVar("--muted"), fontFamily: "Inter, Noto Sans Thai, sans-serif", fontSize: 11 },
    grid: { vertLines: { color: cssVar("--line") }, horzLines: { color: cssVar("--line") } },
    rightPriceScale: { borderColor: cssVar("--line2") },
    timeScale: { borderColor: cssVar("--line2"), timeVisible: true, secondsVisible: false, rightOffset: 4 },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    ...opts,
  });
  S.charts.push(chart);
  return chart;
}
const shiftT = (t) => t + BKK_OFFSET; // lightweight-charts renders UTC; shift so the axis reads Bangkok time
const LINE_COLORS = ["#f5a524", "#60a5fa", "#a78bfa", "#22d3ee", "#f472b6"];
function lineChart(el, series, { pct = false, height } = {}) {
  if (height) el.style.height = height + "px";
  const ch = makeChart(el, { rightPriceScale: { borderColor: cssVar("--line2") } });
  series.forEach((s, i) => {
    const ls = ch.addLineSeries({ color: s.color || LINE_COLORS[i % 5], lineWidth: s.width || 2, priceLineVisible: false, lastValueVisible: !!s.last,
      lineStyle: s.dashed ? 2 : 0, priceFormat: pct ? { type: "custom", formatter: (v) => v.toFixed(1) + "%" } : undefined });
    ls.setData(s.data);
  });
  ch.timeScale().fitContent();
  return ch;
}
function dedupeTimes(points) {
  const out = []; let last = -Infinity;
  for (const p of points) { let t = p.time; if (t <= last) t = last + 1; out.push({ ...p, time: t }); last = t; }
  return out;
}

// ------------------------------------------------------------------ personal display preferences (this device) + live prices
const PREF_DEF = { candle: "classic", bars: 220, live: true, finnhub: "", twelve: "", liveMap: {}, home: "chart" };
const prefs = { get() { return { ...PREF_DEF, ...store.get("prefs", {}) }; }, set(v) { store.set("prefs", { ...prefs.get(), ...v }); Sync.changed(); } };

// ------------------------------------------------------------------ settings sync across the user's devices
// The repository / site is PUBLIC, so the shared settings (API keys, balance, risk, display) live in data/sync.json
// ENCRYPTED (AES-GCM 256, key = PBKDF2-SHA256 of the sync passphrase). Each device types the passphrase once.
// Reading needs nothing else; writing back uses a GitHub token that is itself stored inside the encrypted file.
const Sync = {
  PATH: "data/sync.json", ITER: 600000, busy: false, quiet: false, timer: null,
  repo() { return STATIC?.repo || "mintandmos-cell/tradinglab"; },
  dev() { return store.get("sync", {}); },
  setDev(v) { store.set("sync", { ...Sync.dev(), ...v }); },
  norm(p) { return String(p || "").normalize("NFKC").trim().toLowerCase().replace(/\s+/g, ""); },
  b64e(u8) { let s = ""; u8.forEach((c) => { s += String.fromCharCode(c); }); return btoa(s); },
  b64d(s) { return Uint8Array.from(atob(String(s).replace(/\s/g, "")), (c) => c.charCodeAt(0)); },
  async key(pass, salt, iter) {
    const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(Sync.norm(pass)), "PBKDF2", false, ["deriveKey"]);
    return crypto.subtle.deriveKey({ name: "PBKDF2", hash: "SHA-256", salt, iterations: iter }, base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  },
  async decrypt(blob, pass) {
    const k = await Sync.key(pass, Sync.b64d(blob.salt), blob.iter);
    const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv: Sync.b64d(blob.iv) }, k, Sync.b64d(blob.ct));
    return JSON.parse(new TextDecoder().decode(pt));
  },
  async encrypt(obj, pass) {
    const salt = crypto.getRandomValues(new Uint8Array(16)), iv = crypto.getRandomValues(new Uint8Array(12));
    const k = await Sync.key(pass, salt, Sync.ITER);
    const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, k, new TextEncoder().encode(JSON.stringify(obj))));
    return { v: 1, kdf: "PBKDF2-SHA256", iter: Sync.ITER, salt: Sync.b64e(salt), iv: Sync.b64e(iv), ct: Sync.b64e(ct), updated: obj.updated };
  },
  async fetch(token) {   // fresh copy from the GitHub API (falls back to the raw file, cached ~5 min)
    const h = { Accept: "application/vnd.github+json", ...(token ? { Authorization: "Bearer " + token } : {}) };
    try {
      const r = await fetch(`https://api.github.com/repos/${Sync.repo()}/contents/${Sync.PATH}?ref=main&t=${Date.now()}`, { headers: h, cache: "no-store" });
      if (r.status === 404) return null;
      if (r.ok) { const j = await r.json(); return { blob: JSON.parse(new TextDecoder().decode(Sync.b64d(j.content))), sha: j.sha }; }
    } catch { /* offline or rate-limited: try raw */ }
    const r = await fetch(`https://raw.githubusercontent.com/${Sync.repo()}/main/${Sync.PATH}?t=${Date.now()}`, { cache: "no-store" });
    if (r.status === 404) return null;
    if (!r.ok) throw new Error("HTTP " + r.status);
    return { blob: await r.json(), sha: null };
  },
  payload(token) {
    return { updated: new Date().toISOString(), device: navigator.userAgent.slice(0, 60), prefs: prefs.get(),
      personal: store.get("personal", {}), theme: store.get("theme", "dark"), token: token || "" };
  },
  apply(d) {
    Sync.quiet = true;
    try {
      if (d.prefs) store.set("prefs", { ...prefs.get(), ...d.prefs });
      if (d.personal) store.set("personal", { ...store.get("personal", {}), ...d.personal });
      if (d.theme) { store.set("theme", d.theme); document.documentElement.dataset.theme = d.theme; }
      S.recent = {};
    } finally { Sync.quiet = false; }
  },
  // link this device: decrypt with the passphrase (wrong passphrase -> error), apply, remember
  async link(pass) {
    const f = await Sync.fetch();
    if (!f) throw new Error("ยังไม่มีข้อมูลซิงค์บนคลาวด์");
    const d = await Sync.decrypt(f.blob, pass).catch(() => { throw new Error("รหัสซิงค์ไม่ถูกต้อง"); });
    Sync.apply(d);
    Sync.setDev({ pass: Sync.norm(pass), applied: f.blob.updated, token: d.token || "" });
    return d;
  },
  async pull() {         // on start: newer settings from another device?
    const dv = Sync.dev(); if (!dv.pass || Sync.busy) return false;
    try {
      const f = await Sync.fetch(dv.token);
      if (!f || f.blob.updated === dv.applied) return false;
      const d = await Sync.decrypt(f.blob, dv.pass);
      Sync.apply(d); Sync.setDev({ applied: f.blob.updated, token: d.token || dv.token || "", error: "" });
      return true;
    } catch (e) { Sync.setDev({ error: e.message }); return false; }
  },
  async push() {
    const dv = Sync.dev(); if (!dv.pass) throw new Error("เครื่องนี้ยังไม่ได้เชื่อม");
    if (!dv.token) throw new Error("ต้องมี GitHub token ก่อนจึงส่งขึ้นคลาวด์ได้");
    Sync.busy = true;
    try {
      const cur = await Sync.fetch(dv.token);
      const pl = Sync.payload(dv.token), blob = await Sync.encrypt(pl, dv.pass);
      const body = { message: "sync: settings from a device [skip ci]", content: Sync.b64e(new TextEncoder().encode(JSON.stringify(blob, null, 1))),
        branch: "main", ...(cur?.sha ? { sha: cur.sha } : {}) };
      const r = await fetch(`https://api.github.com/repos/${Sync.repo()}/contents/${Sync.PATH}`, { method: "PUT", body: JSON.stringify(body),
        headers: { Accept: "application/vnd.github+json", Authorization: "Bearer " + dv.token, "Content-Type": "application/json" } });
      if (!r.ok) throw new Error(`GitHub ${r.status}: ${(await r.json().catch(() => ({}))).message || ""}`);
      Sync.setDev({ applied: pl.updated, error: "" });
    } finally { Sync.busy = false; }
  },
  changed() {            // a setting changed on this device: send it to the others (debounced)
    if (Sync.quiet || !Sync.dev().pass || !Sync.dev().token) return;
    clearTimeout(Sync.timer);
    Sync.timer = setTimeout(() => Sync.push().then(() => toast("ซิงค์ไปทุกเครื่องแล้ว", 1400)).catch((e) => toast("ซิงค์ไม่สำเร็จ: " + e.message, 4000)), 1500);
  },
};
const CANDLE = { classic: { up: "#16c784", down: "#ef4444", name: "เขียว / แดง" }, tv: { up: "#26a69a", down: "#ef5350", name: "แบบ TradingView" },
  blue: { up: "#3b82f6", down: "#f59e0b", name: "ฟ้า / ส้ม (ตาบอดสีอ่านง่าย)" }, mono: { up: "#e6e9ef", down: "#6b7280", name: "ขาว / เทา" } };
const LIVE_DEFAULT = { EURUSD: "OANDA:EUR_USD", GBPUSD: "OANDA:GBP_USD", USDJPY: "OANDA:USD_JPY", XAUUSD: "OANDA:XAU_USD",
  NAS100: "OANDA:NAS100_USD", US500: "OANDA:SPX500_USD", GBPJPY: "OANDA:GBP_JPY", AUDUSD: "OANDA:AUD_USD", USDCAD: "OANDA:USD_CAD",
  USOIL: "OANDA:WTICO_USD", XAGUSD: "OANDA:XAG_USD", DE30: "OANDA:DE30_EUR", JP225: "OANDA:JP225_USD",
  NVDA: "NVDA", MSFT: "MSFT", AMZN: "AMZN", META: "META", AAPL: "AAPL" };
const BINANCE_LIVE = { BTCUSD: "btcusdt", ETHUSD: "ethusdt" };
const TWELVE_SYM = { EURUSD: "EUR/USD", GBPUSD: "GBP/USD", USDJPY: "USD/JPY", XAUUSD: "XAU/USD", NAS100: "NDX", US500: "SPX",
  GBPJPY: "GBP/JPY", AUDUSD: "AUD/USD", USDCAD: "USD/CAD", USOIL: "WTI/USD", XAGUSD: "XAG/USD", NVDA: "NVDA", MSFT: "MSFT", AMZN: "AMZN", META: "META", AAPL: "AAPL" };

// Broker clock: MT5 "New York close" servers run New York time + 7 h (GMT+2 / GMT+3). H4 and D1 candles are cut on
// that clock (same as the Python side), so their shapes match the user's MT5 chart.
const _nyOff = {};
const NY_FMT = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit" });
function brokerShift(t) {
  const m = S.settings?.mt5_server_time ?? "ny_close";
  if (m === "utc") return 0;
  if (m !== "ny_close" && isFinite(+m)) return +m * 3600;
  const h = Math.floor(t / 3600);
  if (_nyOff[h] === undefined) {
    let off = -5;
    try {
      const d = new Date(h * 3600000);
      const x = Object.fromEntries(NY_FMT.formatToParts(d).map((q) => [q.type, q.value]));
      off = Math.round((Date.UTC(+x.year, +x.month - 1, +x.day, +x.hour % 24) - d.getTime()) / 3600000);
    } catch { /* very old browser: assume EST */ }
    _nyOff[h] = off;
  }
  return (_nyOff[h] + 7) * 3600;
}
function bucketStart(t, tfs) { if (tfs <= 3600) return t - (t % tfs); const sh = brokerShift(t), x = t + sh; return x - (x % tfs) - sh; }
// Twelve Data keeps printing flat "candles" while the market is shut (weekends, holidays): drop them like MT5 does.
// A bar is kept only if it traded (high > low) and falls Monday–Friday on the broker clock (FX week = Mon 00:00 – Fri 24:00 server).
function marketOpen(b) {
  if (!(b.high > b.low)) return false;
  const wd = new Date((b.time + brokerShift(b.time)) * 1000).getUTCDay();
  return wd >= 1 && wd <= 5;
}
function aggregate(h1, tfs) {
  if (tfs <= 3600) return h1;
  const out = [];
  for (const b of h1) {
    const t = bucketStart(b.time, tfs), o = out[out.length - 1];
    if (o && o.time === t) { o.high = Math.max(o.high, b.high); o.low = Math.min(o.low, b.low); o.close = b.close; }
    else out.push({ time: t, open: b.open, high: b.high, low: b.low, close: b.close, _first: b.time });
  }
  if (out.length && out[0]._first !== out[0].time) out.shift();          // first bucket is only partly covered
  return out.map(({ _first, ...b }) => b);
}
// Recent real H1 candles from the same family of feed as the live price (display only): Binance for BTC/ETH (free),
// Twelve Data for FX / gold (free key, optional). Replaces the tail of the chart so the shapes match the live market.
async function recentBars(sym) {
  const P = prefs.get(); if (!P.live) return null;
  const c = (S.recent ||= {})[sym], ttl = BINANCE_LIVE[sym] ? 60000 : 300000;
  if (c && Date.now() - c.at < ttl) return c.v;
  let v = null;
  const get = async (url) => { const r = await fetch(url, { cache: "no-store" }); if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); };
  try {
    if (BINANCE_LIVE[sym]) {
      const q = `/api/v3/klines?symbol=${BINANCE_LIVE[sym].toUpperCase()}&interval=1h&limit=1000`;
      const k = await get("https://api.binance.com" + q).catch(() => get("https://data-api.binance.vision" + q));
      v = { src: "Binance", bars: k.map((x) => ({ time: Math.round(x[0] / 1000), open: +x[1], high: +x[2], low: +x[3], close: +x[4] })) };
    } else if (P.twelve && TWELVE_SYM[sym]) {
      const j = await get(`https://api.twelvedata.com/time_series?symbol=${encodeURIComponent(TWELVE_SYM[sym])}&interval=1h&outputsize=1000&timezone=UTC&apikey=${encodeURIComponent(P.twelve)}`);
      if (j.status === "error" || !j.values) v = { src: "Twelve Data", error: j.message || "ไม่มีข้อมูล", bars: [] };
      else v = { src: "Twelve Data", bars: j.values.map((x) => ({ time: Math.round(Date.parse(x.datetime.replace(" ", "T") + "Z") / 1000),
        open: +x.open, high: +x.high, low: +x.low, close: +x.close })).filter((b) => isFinite(b.time) && b.close > 0 && marketOpen(b))
        .sort((a, b) => a.time - b.time) };
    }
  } catch (e) { v = { src: BINANCE_LIVE[sym] ? "Binance" : "Twelve Data", error: e.message, bars: [] }; }
  S.recent[sym] = { at: Date.now(), v };
  return v;
}
// Real-time price for the chart's forming candle (display only: systems still decide on closed bars).
// BTC/ETH: Binance trade stream (free, no key). FX / gold / indices: Finnhub websocket with the user's free API key.
const Live = {
  ws: null, price: null, ts: 0, src: "", state: "off", symbol: null,
  start(sym) {
    this.stop(); this.symbol = sym; this.price = null; const P = prefs.get();
    if (!P.live) { this.state = "off"; return; }
    const open = (url, onopen, parse, src, fallback) => {
      let ws; try { ws = new WebSocket(url); } catch { this.state = "error"; return; }
      this.ws = ws; this.src = src; this.state = "connecting";
      ws.onopen = () => { this.state = "live"; onopen && onopen(ws); };
      ws.onmessage = (e) => { try { const x = parse(JSON.parse(e.data)); if (x) { this.price = x.p; this.ts = x.t; } } catch { /* ignore */ } };
      ws.onerror = () => { if (fallback && this.ws === ws) { ws.onclose = null; fallback(); } else this.state = "error"; };
      ws.onclose = () => { if (this.ws === ws && this.state !== "off") this.state = "error"; };
    };
    if (BINANCE_LIVE[sym]) {
      const st = BINANCE_LIVE[sym] + "@aggTrade", parse = (m) => ({ p: +m.p, t: m.T });
      open(`wss://stream.binance.com:9443/ws/${st}`, null, parse, "Binance",
        () => open(`wss://data-stream.binance.vision/ws/${st}`, null, parse, "Binance"));
    } else if (P.finnhub) {
      const fsym = (P.liveMap || {})[sym] || LIVE_DEFAULT[sym];
      open(`wss://ws.finnhub.io?token=${encodeURIComponent(P.finnhub)}`, (ws) => ws.send(JSON.stringify({ type: "subscribe", symbol: fsym })),
        (m) => { const d = m.type === "trade" && m.data?.filter((x) => x.s === fsym).pop(); return d ? { p: +d.p, t: d.t } : null; }, "Finnhub");
    } else this.state = "nokey";
  },
  stop() { const ws = this.ws; this.ws = null; this.state = "off"; if (ws) { ws.onclose = null; try { ws.close(); } catch { /* closed */ } } },
};

// ------------------------------------------------------------------ router / shell
const SVG = (d, extra = "") => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${d}</svg>`;
const ICONS = {
  chart: SVG('<path d="M7 3v18M17 3v18"/><rect x="4.5" y="7" width="5" height="8" rx="1"/><rect x="14.5" y="5" width="5" height="10" rx="1"/>'),
  systems: SVG('<path d="M12 3 3 7.5l9 4.5 9-4.5L12 3z"/><path d="m3 12 9 4.5 9-4.5"/><path d="m3 16.5 9 4.5 9-4.5"/>'),
  lab: SVG('<path d="M9 3h6M10 3v6L4.6 18.6A1.6 1.6 0 0 0 6 21h12a1.6 1.6 0 0 0 1.4-2.4L14 9V3"/><path d="M7.5 15h9"/>'),
  signals: SVG('<path d="M6 9a6 6 0 1 1 12 0c0 6.5 2.5 8.5 2.5 8.5h-17S6 15.5 6 9"/><path d="M10 20.5a2 2 0 0 0 4 0"/>'),
  port: SVG('<rect x="3" y="7" width="18" height="13" rx="2.5"/><path d="M8.5 7V5.5A1.5 1.5 0 0 1 10 4h4a1.5 1.5 0 0 1 1.5 1.5V7"/><path d="M3 12.5h18"/><path d="M12 11v3"/>'),
  calc: SVG('<rect x="5" y="2.5" width="14" height="19" rx="2.5"/><path d="M8.5 6.5h7"/><path d="M8.5 11h.01M12 11h.01M15.5 11h.01M8.5 14.5h.01M12 14.5h.01M15.5 14.5h.01M8.5 18h.01M12 18h.01M15.5 18h.01" stroke-width="2.6"/>'),
  settings: SVG('<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>'),
  guide: SVG('<path d="M4 19.5V5a2 2 0 0 1 2-2h14v15H6a2 2 0 0 0-2 2z"/><path d="M4 19.5A2 2 0 0 0 6 21.5h14"/><path d="M9 7.5h7M9 11h5"/>'),
  refresh: SVG('<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>'),
  theme: SVG('<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 0 0 16z" fill="currentColor"/>'),
  ind: SVG('<path d="M3 17c3-6 5-6 7-3s4 3 7-4l4-4"/>'),
};
const ROUTES = {
  chart: { title: "กราฟ", sub: "แท่งเทียน สัญญาณ และเทรดของระบบ", render: renderChart, ws: true },
  systems: { title: "ระบบเทรด", sub: "Equity curve และรายละเอียดของทุกระบบ (500 ระบบ: ตามเทรนด์ 100 · ย่อตัว / สวนสั้น 391 · อื่น ๆ 9)", render: renderSystems, ws: true },
  lab: { title: "Strategy Lab", sub: "ผลทดสอบทีละขั้น: Backtest → Walk-Forward → สุ่ม → Robustness → Monte Carlo", render: renderLab, ws: true },
  signals: { title: "สัญญาณ", sub: "สัญญาณเข้าเทรด พร้อม Lot / SL / TP · ติดตามผล · ประวัติ", render: renderSignals },
  port: { title: "พอร์ต MT5", sub: "ออเดอร์ที่โปรแกรมเทรดให้ · ความเสี่ยงแยกกลุ่ม · ระบบของแต่ละสินทรัพย์", render: renderPort },
  calc: { title: "เครื่องคำนวณ", sub: "Lot size · R:R · SL / TP", render: renderCalc },
  settings: { title: "ตั้งค่า", sub: "บัญชี ความเสี่ยง ชื่อสัญลักษณ์ การแจ้งเตือน", render: renderSettings },
  guide: { title: "คู่มือ", sub: "วิธีใช้งานทีละขั้น", render: renderGuide },
};
// three groups (2 Nov 2026 reorganisation): analyse → trade → app
const NAV_GROUPS = [["chart", "systems", "lab"], ["signals", "port", "calc"], ["settings", "guide"]];
const NAV_ORDER = NAV_GROUPS.flat();
function route() { const r = location.hash.replace(/^#\/?/, "").split("?")[0]; return ROUTES[r] ? r : (ROUTES[prefs.get().home] ? prefs.get().home : "chart"); }

function renderNav(newCount = 0) {
  const cur = route();
  const item = (k) => `<a href="#/${k}" class="${cur === k ? "active" : ""}" data-tip="${ROUTES[k].title}" aria-label="${ROUTES[k].title}">${ICONS[k]}${k === "signals" && newCount ? `<span class="count">${newCount}</span>` : ""}</a>`;
  const html = NAV_GROUPS.map((g) => g.map(item).join("")).join('<i class="nav-sep"></i>');
  $("#nav").innerHTML = html;
  $("#mnav").innerHTML = html;
}

async function refreshStatus() {
  try {
    S.status = await api("/api/status");
    const st = S.status;
    let src;
    if (st.static) {
      const pc = await pcStatus(); S.pc = pc || S.pc;
      const age = pc && pc.time ? (Date.now() - new Date(pc.time)) / 60000 : null;
      src = age !== null && age < 70 ? `<span class="dot on"></span> คอม MT5 · ${ago(pc.time)}` : `<span class="dot mid"></span> คลาวด์ (คอมออฟไลน์)`;
    } else src = st.mt5.connected ? `<span class="dot on"></span> MT5 ${esc(st.mt5.account?.server || "")}` : `<span class="dot mid"></span> ข้อมูลฟรี`;
    const scan = st.static ? "" : st.scanner.last_scan ? `<span class="dot on"></span> สแกน ${ago(st.scanner.last_scan)}` : `<span class="dot off"></span> ยังไม่สแกน`;
    $("#statusLine").innerHTML = `${src}${scan ? ` <span class="faint">·</span> ${scan}` : ""}${st.research.running ? ` <span class="faint">·</span> <span class="spinner"></span> วิจัย ${esc(st.research.progress)}` : ""}`;
    $("#sideFoot").innerHTML = `<span class="dot ${st.scanner.last_scan ? "on" : "off"}" data-tip="สแกนล่าสุด ${ago(st.scanner.last_scan)} · วิจัย ${ago(st.research_generated)}"></span>`;
  } catch (e) { $("#statusLine").innerHTML = `<span class="dot off"></span> เชื่อมต่อไม่ได้`; }
}

async function navigate() {
  const k = route(), r = ROUTES[k];
  S.charts.forEach((c) => { try { c.remove(); } catch { /* already gone */ } }); S.charts = [];
  closeAllPopups(false);
  Live.stop(); clearInterval(S.liveTimer);
  $("#pageTitle").textContent = r.title; $("#pageSub").textContent = r.sub;
  document.title = `${r.title} · TradingLab`;
  document.body.dataset.page = k;
  renderNav(S.newCount || 0);
  const view = $("#view");
  view.className = "content" + (r.ws ? " fill" : "");
  view.scrollTop = 0;
  view.innerHTML = `<div class="empty"><div class="spinner"></div></div>`;
  try { await r.render(view); } catch (e) { view.innerHTML = `<div class="pad"><div class="banner"><span class="ic">⚠️</span><div>เกิดข้อผิดพลาด: ${esc(e.message)}</div></div></div>`; console.error(e); }
}

// ------------------------------------------------------------------ workspace (chart / systems / lab): main area + right rail
const ABBR = { EURUSD: "EU", GBPUSD: "GU", USDJPY: "UJ", XAUUSD: "XAU", NAS100: "NAS", US500: "SPX", BTCUSD: "BTC", ETHUSD: "ETH",
  GBPJPY: "GJ", AUDUSD: "AU", USDCAD: "UC", USOIL: "OIL", XAGUSD: "XAG", DE30: "DAX", JP225: "NKY",
  NVDA: "NVDA", MSFT: "MSFT", AMZN: "AMZN", META: "META", AAPL: "AAPL" };
const stratById = (id) => S.strategies.find((s) => s.id === id);
// asset classes (user, 2 Nov 2026: 20 Exness symbols) — every symbol list in the app is shown in this order, grouped
const ASSET_GROUPS = [
  { key: "fx", label: "ค่าเงิน (Forex)", short: "FX", icon: "💱", groups: ["Forex"] },
  { key: "cmd", label: "ทองคำ · เงิน · น้ำมัน", short: "CMD", icon: "🥇", groups: ["Metals", "Energy"] },
  { key: "idx", label: "ดัชนีหุ้น", short: "IDX", icon: "📊", groups: ["Indices"] },
  { key: "stk", label: "หุ้นสหรัฐ", short: "STK", icon: "🏢", groups: ["Stocks"] },
  { key: "cry", label: "คริปโต", short: "CRY", icon: "🪙", groups: ["Crypto"] },
];
const SYM_ORDER = ["EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "AUDUSD", "USDCAD", "XAUUSD", "XAGUSD", "USOIL",
  "NAS100", "US500", "DE30", "JP225", "NVDA", "MSFT", "AMZN", "META", "AAPL", "BTCUSD", "ETHUSD"];
const groupIdx = (x) => { const i = ASSET_GROUPS.findIndex((g) => g.groups.includes(x.group)); return i < 0 ? ASSET_GROUPS.length : i; };
const sortSyms = (a) => (a || []).slice().sort((x, y) => groupIdx(x) - groupIdx(y)
  || ((SYM_ORDER.indexOf(x.symbol) + 1 || 99) - (SYM_ORDER.indexOf(y.symbol) + 1 || 99)));
// [{ g: group, list: [symbols] }] in display order
const symGroups = (list = S.symbols) => {
  const out = ASSET_GROUPS.map((g) => ({ g, list: [] })), other = { g: { key: "oth", label: "อื่น ๆ", short: "…", icon: "•" }, list: [] };
  for (const x of sortSyms(list)) (out[groupIdx(x)] || other).list.push(x);
  return [...out, other].filter((o) => o.list.length);
};
// Android back button (user's rule): it closes the open popup first; pages never pile up in the history
// (navigation replaces the current entry), so with nothing open, back leaves the app / closes the web page.
const Back = {
  stack: [], skip: 0, waiters: [],
  open(close) { Back.stack.push(close); history.pushState({ tlPopup: Back.stack.length }, "", location.href); },
  release(close) {          // popup closed by a tap: drop its history entry; resolves once the browser has done so
    const i = Back.stack.lastIndexOf(close);
    if (i < 0) return Promise.resolve();
    Back.stack.splice(i, 1); Back.skip++;
    return new Promise((res) => { Back.waiters.push(res); history.back(); });
  },
};
window.addEventListener("popstate", () => {
  if (Back.skip) { Back.skip--; const w = Back.waiters.shift(); if (w) w(); return; }
  const c = Back.stack.pop();
  if (c) c(true);
});
function closeAllPopups(dropHistory = true) {
  document.querySelectorAll(".syspop, .syspop-bg").forEach((x) => x.remove());
  const n = Back.stack.length; Back.stack = [];
  if (n && dropHistory) { Back.skip++; history.go(-n); }
}
function go(hash) {          // change page without adding a history entry
  if (location.hash === hash) { navigate(); return; }
  history.replaceState(history.state, "", hash); navigate();
}
document.addEventListener("click", (e) => {
  const a = e.target.closest('a[href^="#/"]');
  if (!a || a.target || e.defaultPrevented || e.metaKey || e.ctrlKey) return;
  e.preventDefault(); go(a.getAttribute("href"));
});
const sysOf = (sym) => S.symbols.find((x) => x.symbol === sym) || {};
// the symbol's ACTIVE systems this quarter (several allowed); old data without "systems": the single system
const liveSystems = (sym) => { const r = sysOf(sym); return r.systems || (r.status === "ACTIVE" && r.strategy ? [{ strategy: r.strategy, tf: r.tf }] : []); };
const isLiveSys = (sym, sid, tf) => liveSystems(sym).some((x) => x.strategy === sid && (!tf || x.tf === tf));
// positions the systems hold right now = alerted signals still being tracked (outcome "open", not skipped by the risk cap)
const heldOf = (sym, sid, tf) => (S.sigs || []).filter((x) => x.outcome === "open" && !x.capped && (!sym || x.symbol === sym)
  && (!sid || x.strategy === sid) && (!tf || x.tf === tf));
const heldBadge = (list, short = false) => list.length ? list.map((x) => `<span class="held ${x.dir > 0 ? "long" : "short"}" data-tip="ถืออยู่: ${x.side} @ ${x.entry} · ${x.user_status === "taken" ? "คุณกดเปิดแล้ว" : "ยังไม่ได้กดยืนยันว่าเปิด"}">${x.dir > 0 ? "▲" : "▼"}${short ? " ถือ" : " ถือ " + x.side}</span>`).join("") : "";
async function loadHeld() { try { S.sigs = await api("/api/signals"); } catch { S.sigs = S.sigs || []; } }
const isPortfolioSys = (sym, sid, tf) => liveSystems(sym).some((x) => x.strategy === sid && x.tf === tf && x.source === "portfolio");
const stratLabel = (id) => { const s = stratById(id); return s ? `#${s.no} ${s.name}` : id; };
function pickSel(q, key, fallbackStrategy) {
  const prev = store.get(key, store.get("sel", {}));
  const symbol = S.symbols.some((x) => x.symbol === q.symbol) ? q.symbol : (prev.symbol || "XAUUSD");
  const sys = sysOf(symbol);
  const tf = ["H1", "H4", "D1"].includes(q.tf) ? q.tf : (prev.tf || sys.tf || "H1");
  let strategy = stratById(q.strategy) ? q.strategy : (prev.strategy || fallbackStrategy || sys.strategy);
  if (!stratById(strategy)) strategy = sys.strategy || S.strategies[0].id;
  return { symbol, tf, strategy };
}
function wsHtml({ title, tabs = [], tab, actions = "", main, sel, foot = "" }) {
  return `<div class="ws">
    <div class="ws-head"><div class="ws-title">${title}</div>
      ${tabs.length ? `<div class="ws-tabs" id="wsTabs">${tabs.map(([k, l]) => `<button data-tab="${k}" class="${k === tab ? "on" : ""}">${l}</button>`).join("")}</div>` : ""}
      <div class="ws-act">${actions}</div></div>
    <div class="ws-body"><div class="ws-main">${main}</div>${railHtml(sel)}</div>
    <div class="ws-foot" id="wsFoot">${foot}</div></div>`;
}
function railHtml(sel) {
  const sys = sysOf(sel.symbol), st = stratById(sel.strategy) || {};
  return `<div class="rail">
    <div class="rail-grp"><button class="rb qbtn" id="qBtn" data-tip="เลือกด่วน: ระบบ ACTIVE / WATCH เรียงตามความสำคัญ">⚡<small>ด่วน</small></button></div>
    <div class="rail-grp">${["H1", "H4", "D1"].map((t) => `<button class="rb ${t === sel.tf ? "on" : ""}" data-tf="${t}" data-tip="Timeframe ${t}">${t}</button>`).join("")}</div>
    <div class="rail-grp"><button class="rb sysbtn" id="sysBtn" data-tip="${esc(st.name || "")} — กดเพื่อเลือกระบบ"><small>ระบบ</small>${st.no ?? "?"}${isLiveSys(sel.symbol, sel.strategy, sel.tf) ? '<i class="star">★</i>' : ""}${heldOf(sel.symbol, sel.strategy, sel.tf).length ? '<i class="hd"></i>' : ""}</button></div>
    <div class="rail-grp syms">${symGroups().map(({ g, list }) => `<div class="rg-l" data-tip="${g.label}">${g.short}</div>` + list.map((x) => `<button class="rb ${x.symbol === sel.symbol ? "on" : ""}" data-sym="${x.symbol}" data-tip="${x.symbol} · ${x.status || "-"}">${ABBR[x.symbol] || x.symbol.slice(0, 3)}<i class="sd ${x.status || ""}"></i>${heldOf(x.symbol).length ? `<i class="hd" data-n="${heldOf(x.symbol).length}"></i>` : ""}</button>`).join("")).join("")}</div>
  </div>`;
}
function bindRail(root, sel, onChange) {
  $$("[data-tf]", root).forEach((b) => b.onclick = () => onChange({ ...sel, tf: b.dataset.tf }, "tf"));
  $$("[data-sym]", root).forEach((b) => b.onclick = () => onChange({ ...sel, symbol: b.dataset.sym }, "symbol"));
  $("#sysBtn", root).onclick = (e) => openSysPicker(e.currentTarget, sel, (sid) => onChange({ ...sel, strategy: sid }, "strategy"));
  $("#qBtn", root).onclick = (e) => openQuickPicker(e.currentTarget, sel, (p) => onChange({ symbol: p.symbol, tf: p.tf, strategy: p.strategy }, "pick"));
}
async function openQuickPicker(anchor, sel, pick) {
  closeAllPopups();
  const list = S.picks || (S.picks = await api("/api/picks").catch(() => []));
  const bg = document.createElement("div"); bg.className = "syspop-bg";
  const pop = document.createElement("div"); pop.className = "syspop quick";
  const row = (p) => { const st = stratById(p.strategy) || {}; const on = p.symbol === sel.symbol && p.tf === sel.tf && p.strategy === sel.strategy;
    return `<button data-i="${list.indexOf(p)}" class="${on ? "on" : ""}"><b class="ab">${ABBR[p.symbol] || p.symbol.slice(0, 3)}</b>
      <span><em class="ql">${heldBadge(heldOf(p.symbol, p.strategy, p.tf), true)}${statusBadge(p.status)}${p.portfolio ? ' <span class="badge ghost">พอร์ต</span>' : ""} <i class="mono">${p.tf}</i> · #${st.no ?? "?"} ${esc(st.name || p.strategy)}${p.live ? ' <i class="star">★</i>' : ""}</em>
      <small>${p.symbol} · OOS ${p.trades ?? "–"} ไม้ · ${fr(p.exp, 3)}/ไม้ · t ${fnum(p.t, 2)}${p.robust != null ? " · Robust " + p.robust : ""}</small></span></button>`; };
  const act = list.filter((p) => p.status === "ACTIVE"), wat = list.filter((p) => p.status === "WATCH"), rest = list.filter((p) => !["ACTIVE", "WATCH"].includes(p.status));
  pop.innerHTML = `<div class="syspop-hd"><b>⚡ เลือกด่วน</b><span class="muted small">กดครั้งเดียว เปลี่ยนสินทรัพย์ + TF + ระบบพร้อมกัน · ACTIVE = ระบบที่ส่งแจ้งเตือนจริง (★) รวมระบบพอร์ตที่เทรดทุกสินทรัพย์ · WATCH = ยังไม่ผ่านเกณฑ์ ไม่เทรด</span></div>
    <div class="syspop-list">${act.length ? `<div class="grp">ACTIVE (${act.length})</div>${act.map(row).join("")}` : ""}
      ${wat.length ? `<div class="grp">WATCH (${wat.length})</div>${wat.map(row).join("")}` : ""}
      ${rest.length ? `<div class="grp">ระบบปัจจุบันของสินทรัพย์อื่น (OFF)</div>${rest.map(row).join("")}` : ""}
      ${list.length ? "" : '<div class="hint pad-x">ยังไม่มีผลวิเคราะห์</div>'}</div>`;
  document.body.append(bg, pop);
  const r = anchor.getBoundingClientRect();
  if (window.innerWidth > 760) { pop.style.top = Math.max(8, Math.min(r.top, window.innerHeight - pop.offsetHeight - 8)) + "px"; pop.style.left = Math.max(8, r.left - pop.offsetWidth - 8) + "px"; }
  const close = (fromBack) => { bg.remove(); pop.remove(); return fromBack === true ? Promise.resolve() : Back.release(close); };
  Back.open(close);
  bg.onclick = () => close();
  pop.onclick = (e) => { const b = e.target.closest("[data-i]"); if (b) close().then(() => pick(list[+b.dataset.i])); };
}
function goSel(page, sel, extra = {}) {
  const p = new URLSearchParams({ symbol: sel.symbol, tf: sel.tf, strategy: sel.strategy, ...extra });
  const h = `#/${page}?${p}`;
  go(h);
}
function bindTabs(root, key, onTab) {
  const box = $("#wsTabs", root); if (!box) return;
  box.onclick = (e) => {
    const b = e.target.closest("[data-tab]"); if (!b) return;
    $$("[data-tab]", box).forEach((x) => x.classList.toggle("on", x === b));
    store.set(key, b.dataset.tab); onTab(b.dataset.tab);
  };
}
function openSysPicker(anchor, sel, pick) {
  closeAllPopups();
  const live = new Set(liveSystems(sel.symbol).filter((x) => x.tf === sel.tf).map((x) => x.strategy));
  const bg = document.createElement("div"); bg.className = "syspop-bg";
  const pop = document.createElement("div"); pop.className = "syspop";
  pop.innerHTML = `<div class="syspop-hd"><b>เลือกระบบเทรด</b><span class="muted small">${S.strategies.length} ระบบ · ★ = ระบบที่เทรดจริงของ ${sel.symbol} ${sel.tf} ไตรมาสนี้</span>
      <input class="inp" id="sysQ" placeholder="ค้นหาเลข ชื่อ หรือกลุ่ม…" autocomplete="off"></div>
    <div class="syspop-list">${S.strategies.map((s) => `<button data-sid="${s.id}" class="${s.id === sel.strategy ? "on" : ""}" data-q="${esc((s.no + " " + s.name + " " + s.family).toLowerCase())}">
      <b>${s.no}</b><span>${esc(s.name)}<small>${esc(s.family)}</small></span>${heldBadge(heldOf(sel.symbol, s.id, sel.tf), true)}${live.has(s.id) ? '<i class="star">★</i>' : ""}</button>`).join("")}</div>`;
  document.body.append(bg, pop);
  const r = anchor.getBoundingClientRect();
  if (window.innerWidth > 760) {
    pop.style.top = Math.max(8, Math.min(r.top, window.innerHeight - pop.offsetHeight - 8)) + "px";
    pop.style.left = Math.max(8, r.left - pop.offsetWidth - 8) + "px";
  }
  const close = (fromBack) => { bg.remove(); pop.remove(); return fromBack === true ? Promise.resolve() : Back.release(close); };
  Back.open(close);
  bg.onclick = () => close();
  pop.querySelector(".on")?.scrollIntoView({ block: "center" });
  $("#sysQ", pop).oninput = (e) => { const q = e.target.value.trim().toLowerCase(); $$("[data-sid]", pop).forEach((b) => b.hidden = q && !b.dataset.q.includes(q)); };
  pop.onclick = (e) => { const b = e.target.closest("[data-sid]"); if (b) close().then(() => pick(b.dataset.sid)); };
}
const footItem = (k, v, cls = "", wide = false) => `<span class="fi${wide ? " wide" : ""}"><span class="k">${k}</span><span class="v ${cls}">${v}</span></span>`;

// ------------------------------------------------------------------ SIGNALS
// what the PC robot really did with a signal (data/pc_status.json, published by the auto-trader)
function robotTag(x) {   // "TL56-H1" (older close records carried the broker's deal comment instead)
  if (x.system && !String(x.system).startsWith("[")) return x.system;
  const p = String(x.signal_id || "").split("-");
  return p.length >= 4 ? `TL${stratById(p[2])?.no ?? "?"}-${p[1]}` : (x.system || "");
}
function robotOf(s) {
  const pc = S.pc; if (!pc || !s) return null;
  const p = (pc.positions || []).find((x) => x.signal_id === s.id);
  if (p) return { open: true, ...p };
  const c = (pc.closed || []).find((x) => x.signal_id === s.id);
  return c ? { open: false, ...c } : null;
}
function robotLine(s) {
  const r = robotOf(s); if (!r) return "";
  const dg = specOf(s.symbol).digits;
  return r.open
    ? `<div class="banner ok" style="margin:0 16px 12px 18px"><span class="ic">🤖</span><div><b>MT5 (${esc(S.pc.account || "")}) ถืออยู่</b> ${r.side} ${fnum(r.lots, 2)} lot @ ${fpx(r.price_open, dg)} · SL ${r.sl ? fpx(r.sl, dg) : "–"}${r.tp ? " · TP " + fpx(r.tp, dg) : ""} · <span class="${cls(r.profit)}">${fusd(r.profit)}</span></div></div>`
    : `<div class="banner" style="margin:0 16px 12px 18px"><span class="ic">🤖</span><div><b>MT5 ปิดแล้ว</b> — ${esc(r.reason || "")} @ ${r.price != null ? fpx(r.price, dg) : "–"} · <span class="${cls(r.profit)}">${fusd(r.profit)}</span></div></div>`;
}
function signalCard(s, compact = false) {
  const sp = specOf(s.symbol), d = sp.digits;
  const expired = new Date(s.valid_until) < new Date();
  const done = s.outcome !== "open" || ["taken", "skipped"].includes(s.user_status);
  const outcome = s.outcome !== "open" ? `<span class="badge ${s.outcome}">${s.exit_reason && !["SL", "TP"].includes(s.exit_reason) ? reasonTh(s.exit_reason) : { win: "TP", loss: "SL", time: "หมดเวลา" }[s.outcome]} ${fr(s.outcome_r)}</span>` : `<span class="badge open">${expired ? "กำลังติดตาม" : "รอเข้า"}</span>`;
  return `<div class="card sig ${s.dir < 0 ? "short" : ""} ${done ? "done" : ""}" data-id="${esc(s.id)}">
    <div class="top">
      <span class="side">${s.side}</span>
      <div><div class="sym">${esc(s.mt5_symbol)}</div><div class="meta">${s.tf} · ${esc(s.strategy_name || stratName(s.strategy))}</div></div>
      <div style="margin-left:auto;text-align:right" class="row">${statusBadge(s.system_status)} ${outcome}</div>
    </div>
    <div class="meta" style="padding:0 16px 8px 18px">สัญญาณ ${fdt(s.bar_time)} · ${expired ? "เลยเวลาเข้าแล้ว" : "เข้าได้ถึง " + fdt(s.valid_until)} · ${s.close_by ? "ปิดภายใน " + fdt(s.close_by) : "ถือจนชน TP / SL หรือมีสัญญาณกลับทิศ"}</div>${s.reverse_of ? `<div class="banner" style="margin:0 16px 10px 18px"><span class="ic">🔁</span><div><b>สัญญาณกลับทิศ:</b> ปิดออเดอร์ ${s.reverse_of} เดิมของระบบนี้ก่อน แล้วจึงเปิดไม้นี้</div></div>` : ""}
    <div class="levels">
      <div class="lv"><div class="k">ENTRY ≈</div><div class="v" data-f="entry">${fpx(s.entry, d)}</div><div class="s">${s.price_source}</div></div>
      <div class="lv sl"><div class="k">STOP LOSS</div><div class="v" data-f="sl">${fpx(s.sl, d)}</div><div class="s">${fnum(s.sl_pips, 1)} pips</div></div>
      <div class="lv tp"><div class="k">TAKE PROFIT</div><div class="v" data-f="tp">${s.tp == null ? "ไม่ตั้ง" : fpx(s.tp, d)}</div><div class="s">${s.tp == null ? "ออกด้วย SL / กลับทิศ" : fnum(s.tp_pips, 1) + " pips"}</div></div>
    </div>
    <div class="lotbar">
      <div><div class="lbl">LOT (MT5)</div><div class="lot" data-f="lots">${s.lots > 0 ? fnum(s.lots, 2) : "&lt;0.01"}</div></div>
      <div><div class="lbl">ความเสี่ยง</div><div class="mono" data-f="risk">$${fnum(s.risk_usd, 2)} <span class="muted">(${s.risk_pct}%)</span></div></div>
      <div><div class="lbl">R:R</div><div class="mono">${s.tp == null ? "ไม่ตั้ง TP" : "1 : " + fnum(s.rr, 2)}</div></div>
      <div class="hide-sm"><div class="lbl">กำไรเป้า</div><div class="mono pos">${s.reward_usd == null ? "ปล่อยกำไรวิ่ง" : "$" + fnum(s.reward_usd, 2)}</div></div>
    </div>
    ${robotLine(s)}
    ${s.system_status === "TRIAL" ? `<div class="banner info" style="margin:0 16px 12px 18px"><span class="ic">🧪</span><div><b>ทดลองบน DEMO</b> — ระบบนี้ยังไม่ผ่านด่านหักโชค โปรแกรมเปิดให้เฉพาะบัญชี demo ที่ความเสี่ยงต่ำ เพื่อเก็บผลจริง · ไม่ต้องเปิดในบัญชีจริง</div></div>` : ""}
    ${s.trail_dist ? `<div class="banner info" style="margin:0 16px 12px 18px"><span class="ic">📐</span><div><b>SL เลื่อนตาม (trailing)</b> ห่างจากราคาดีที่สุด ${fpx(s.trail_dist, d)} · SL ตอนนี้ <b>${fpx(s.stop_now ?? s.sl, d)}</b> — ขยับเฉพาะทางที่ล็อกกำไร หลังแท่งปิด (ถ้าเทรดเอง ให้เลื่อน SL ใน MT5 ตามนี้)</div></div>` : ""}
    ${s.capped ? `<div class="banner" style="margin:0 16px 12px 18px"><span class="ic">⛔</span><div>${esc(s.note || "ข้าม: เกินเพดานความเสี่ยงรวมของสินทรัพย์นี้")} — ไม่ได้ส่งแจ้งเตือน (ระบบยังติดตามผลไม้นี้เพื่อวัดผลของระบบ)</div></div>` : ""}
    ${s.below_min_lot ? `<div class="banner" style="margin:0 16px 12px 18px"><span class="ic">⚠️</span><div>Lot ที่คำนวณได้ ${s.raw_lots ?? "–"} ต่ำกว่าขั้นต่ำ 0.01 —
      ถ้าเปิด 0.01 lot จะเสี่ยง <b>$${fnum(s.min_lot_risk_usd, 2)}${s.min_lot_risk_pct ? ` (${s.min_lot_risk_pct}%)` : ""}</b> มากกว่าที่ตั้งไว้ ${s.risk_pct}% · ข้ามไม้นี้ หรือยอมรับความเสี่ยงที่สูงขึ้นเอง</div></div>` : ""}
    <div class="adj">
      <label class="f" style="flex:1">ราคาจริงใน MT5 ตอนเข้า<input class="inp num" type="number" step="any" placeholder="${fpx(s.entry, d)}"></label>
      <button class="btn sm primary" data-a="recalc">คำนวณใหม่</button>
    </div>
    ${compact ? "" : `<div class="acts">
      <button class="btn sm" data-a="copy">📋 คัดลอก</button>
      <button class="btn sm" data-a="adjust">🎯 ปรับตามราคา MT5</button>
      <a class="btn sm" href="#/chart?symbol=${s.symbol}&tf=${s.tf}&strategy=${s.strategy}">📈 กราฟ</a>
      ${s.user_status === "new" ? `<button class="btn sm ghost" data-a="taken" title="ไม่จำเป็นต้องกด — ไว้จดว่าคุณเปิดเองในบัญชีที่เทรดมือ (เช่น The5ers) เพื่อดูสถิติของคุณ">✋ ฉันเปิดเอง</button><button class="btn sm ghost" data-a="skipped" title="จดว่าคุณไม่ได้เข้าไม้นี้ในบัญชีที่เทรดมือ">ข้าม</button>` : `<span class="badge ${s.user_status}">${s.user_status === "taken" ? "เปิดแล้ว" : "ข้าม"}</span>`}
    </div>`}
  </div>`;
}

function bindSignalCards(root, sigs) {
  $$(".sig", root).forEach((card) => {
    const s = sigs.find((x) => x.id === card.dataset.id); if (!s) return;
    const d = specOf(s.symbol).digits;
    card.addEventListener("click", async (e) => {
      const a = e.target.closest("[data-a]")?.dataset.a; if (!a) return;
      if (a === "copy") {
        const txt = `${s.side} ${s.mt5_symbol} ${s.tf}\nLot ${$("[data-f=lots]", card).textContent}\nEntry ${$("[data-f=entry]", card).textContent}\nSL ${$("[data-f=sl]", card).textContent}\nTP ${$("[data-f=tp]", card).textContent}`;
        try { await navigator.clipboard.writeText(txt); toast("คัดลอกแล้ว"); } catch { prompt("คัดลอกข้อความนี้", txt); }
      } else if (a === "adjust") {
        $(".adj", card).classList.toggle("show");
      } else if (a === "recalc") {
        const p = parseFloat($(".adj input", card).value); if (!p) return toast("ใส่ราคาก่อน");
        const sl = p - s.dir * s.sl_dist, tp = s.tp_dist == null ? null : p + s.dir * s.tp_dist;
        const r = await api(`/api/size?symbol=${s.symbol}&entry=${p}&sl=${sl}${tp == null ? "" : "&tp=" + tp}&risk_pct=${s.risk_pct}`);
        $("[data-f=entry]", card).textContent = fpx(p, d); $("[data-f=sl]", card).textContent = fpx(sl, d);
        $("[data-f=tp]", card).textContent = tp == null ? "ไม่ตั้ง" : fpx(tp, d); $("[data-f=lots]", card).textContent = fnum(r.lots, 2);
        $("[data-f=risk]", card).innerHTML = `$${fnum(r.actual_risk_usd, 2)} <span class="muted">(${s.risk_pct}%)</span>`;
        toast("คำนวณ SL/TP/Lot ใหม่จากราคา MT5 แล้ว (ระยะ SL/TP เท่าเดิม)");
      } else if (a === "taken" || a === "skipped") {
        await api(`/api/signals/${encodeURIComponent(s.id)}`, { method: "POST", body: { user_status: a } });
        toast(a === "taken" ? "บันทึก: เปิดออเดอร์แล้ว" : "บันทึก: ข้ามสัญญาณนี้"); navigate();
      }
    });
  });
}

// ------------------------------------------------------------------ SYSTEMS (research)
function gateChips(g) { return `<div class="gates">${(g || []).map((x) => `<span class="gate ${x.pass ? "p" : "f"}" title="ต้องการ ${x.need}">${x.pass ? "✓" : "✗"} ${esc(x.name)} ${x.value === null || x.value === undefined ? "–" : fnum(x.value, typeof x.value === "number" && Math.abs(x.value) < 10 ? 2 : 0)}</span>`).join("")}</div>`; }

function robustBar(rb) {
  if (!rb || rb.score === null || rb.score === undefined) return `<div class="robust"><span class="small muted">Robustness: ${esc(rb?.grade || "ยังไม่ได้ทดสอบ")}</span></div>`;
  const c = rb.score >= 80 ? "pos" : rb.score >= 60 ? "warn" : "neg";
  return `<div class="robust"><div class="row" style="justify-content:space-between"><span class="small"><b>Robustness</b> · ${esc(rb.grade)}</span><span class="mono ${c}" style="font-weight:700">${rb.score}/100</span></div>
    <div class="meter"><i class="${c}" style="width:${rb.score}%"></i></div>
    <div class="small muted">ผ่าน ${rb.passed}/${rb.applicable} การทดสอบ</div></div>`;
}
function robustTable(rb) {
  if (!rb?.tests?.length) return "";
  return `<div class="formsec">Robustness — พยายาม “ทำให้ระบบพัง” ${rb.applicable} แบบ (คะแนน ${rb.score}/100)</div>
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th></th><th>การทดสอบ</th><th class="num">ผล</th><th>เกณฑ์</th></tr></thead><tbody>
    ${rb.tests.map((t) => `<tr><td>${t.pass === null ? '<span class="faint">–</span>' : t.pass ? '<span class="pos">✓</span>' : '<span class="neg">✗</span>'}</td>
      <td class="small">${esc(t.name)}${t.note ? `<div class="tiny faint">${esc(t.note)}</div>` : ""}</td><td class="num small">${esc(t.value ?? "–")}</td><td class="small muted">${esc(t.need)}</td></tr>`).join("")}</tbody></table></div>`;
}

async function pollResearch() {
  const tick = async () => {
    const st = await api("/api/research/state");
    const el = $("#resProg"); if (el) el.textContent = st.running ? st.progress : "เสร็จแล้ว";
    if (st.running) setTimeout(tick, 2500); else { await refreshStatus(); if (route() === "systems") navigate(); toast("วิจัยเสร็จแล้ว"); }
  };
  setTimeout(tick, 2500);
}

// ------------------------------------------------------------------ CHART
function openPosition(d, openSigs) {
  // the order that was really sent (the alert = what the phone, the robot and you traded) wins over the chart's own
  // backtest position: the backtest is recomputed on the newest price feed and its entry / SL can drift a little from
  // the alerted levels (user's report 28 Oct 2026: "SL hit but the order did not close" was the backtest's SL line)
  const s = [...openSigs].sort((a, b) => new Date(b.bar_time) - new Date(a.bar_time))[0];
  if (s) {
    const from = Math.floor(new Date(s.bar_time).getTime() / 1000);
    const step = { H1: 3600, H4: 14400, D1: 86400 }[s.tf] || 3600;
    return { dir: s.dir, entry: s.entry, sl: s.sl, tp: s.tp, from, close_by: s.close_by ? Math.floor(new Date(s.close_by).getTime() / 1000) : null,
      pending: Date.now() / 1000 < from + 2 * step, alerted: true };
  }
  return d.open_trade ? { ...d.open_trade, from: d.open_trade.entry_time, pending: false } : null;
}

function drawTradeLevels(ch, d, open) {
  const times = d.candles.map((c) => c.time), n = times.length, markers = [];
  if (!n) return markers;
  const step = n > 1 ? times[n - 1] - times[n - 2] : 3600;
  const idx = (t) => { let lo = 0, hi = n; while (lo < hi) { const m = (lo + hi) >> 1; if (times[m] < t) lo = m + 1; else hi = m; } return lo; };
  const line = (color, style, width, title) => ch.addLineSeries({ color, lineWidth: width, lineStyle: style, priceLineVisible: false,
    lastValueVisible: !!title, title: title || "", crosshairMarkerVisible: false, autoscaleInfoProvider: title ? undefined : () => null });
  const tset = new Set(times);
  // closed trades (recent part of the chart): faint entry / SL / TP segments from entry to exit, one set per trade
  (d.trades || []).filter((t) => t.exit_time >= times[Math.max(0, n - 500)]).slice(-20).forEach((t) => {
    const a = idx(t.entry_time), b = Math.min(idx(t.exit_time), n - 1);
    if (a >= n) return;
    [[t.entry, "rgba(96,165,250,.55)", 2], [t.sl, "rgba(239,68,68,.55)", 0], [t.tp, "rgba(22,199,132,.55)", 0]].filter(([v]) => v != null).forEach(([v, color, style]) =>
      line(color, style, 1).setData(times.slice(a, b + 1).map((x) => ({ time: shiftT(x), value: v }))));
  });
  (d.trades || []).forEach((t) => {
    if (tset.has(t.entry_time)) markers.push({ time: shiftT(t.entry_time), position: t.dir > 0 ? "belowBar" : "aboveBar", color: t.dir > 0 ? "#16c784" : "#ef4444", shape: t.dir > 0 ? "arrowUp" : "arrowDown", text: t.dir > 0 ? "L" : "S" });
    if (tset.has(t.exit_time)) markers.push({ time: shiftT(t.exit_time), position: t.dir > 0 ? "aboveBar" : "belowBar", color: t.r > 0 ? "#60a5fa" : "#f5a524", shape: "circle", text: `${reasonTh(t.reason)} ${t.r > 0 ? "+" : ""}${t.r.toFixed(1)}R` });
  });
  if (open) {
    // running position: Entry / SL / TP from the entry candle to the latest candle, sticking out to the right with labels;
    // every new candle makes them longer until the trade hits SL, TP or its time limit
    const a = Math.min(idx(open.from), n - 1), pts = [];
    for (let i = a; i < n; i++) pts.push(times[i]);
    for (let j = 1; j <= 6; j++) pts.push(times[n - 1] + j * step);
    const lv = [["ENTRY", open.entry, "#60a5fa", 2, ` · ${open.lot}`], ["SL", open.sl, "#ef4444", 0, ""], ["TP", open.tp, "#16c784", 0, ""]].filter((x) => x[1] != null);
    lv.forEach(([title, v, color, style, extra]) => line(color, style, 2, `${title}${extra}`).setData(pts.map((t) => ({ time: shiftT(t), value: v }))));
    const side = open.dir > 0 ? "BUY" : "SELL";
    markers.push({ time: shiftT(times[a]), position: open.dir > 0 ? "belowBar" : "aboveBar", color: open.dir > 0 ? "#16c784" : "#ef4444",
      shape: open.dir > 0 ? "arrowUp" : "arrowDown", text: `${open.pending ? "สัญญาณ " : ""}${side} ${open.lot} @ ${fpx(open.entry, d.digits)}` });
  }
  return markers;
}

// equity of a list of [exit_time, R] at a fixed risk % per trade, compounded from the balance (TradingView-style stats)
function equityStats(pts, riskPct, bal) {
  const f = riskPct / 100; let eq = bal, peak = bal, maxDd = 0, maxDdUsd = 0, gw = 0, gl = 0, wins = 0;
  const curve = [];
  pts.forEach(([t, r]) => {
    const pnl = eq * f * r; eq += pnl;
    if (pnl > 0) { gw += pnl; wins++; } else gl -= pnl;
    peak = Math.max(peak, eq);
    const dd = peak - eq; if (dd > maxDdUsd) { maxDdUsd = dd; maxDd = dd / peak * 100; }
    curve.push({ time: t, value: +eq.toFixed(2) });
  });
  const rs = pts.map((p) => p[1]), n = rs.length, mean = n ? rs.reduce((a, b) => a + b, 0) / n : 0;
  const sd = n > 1 ? Math.sqrt(rs.reduce((a, b) => a + (b - mean) ** 2, 0) / (n - 1)) : 0;
  return { curve, n, net: eq - bal, netPct: (eq / bal - 1) * 100, winPct: n ? wins / n * 100 : 0, pf: gl > 0 ? gw / gl : (gw > 0 ? 99 : 0),
    maxDd, maxDdUsd, avgR: mean, t: sd > 0 ? mean / sd * Math.sqrt(n) : 0, from: n ? pts[0][0] : null, to: n ? pts[n - 1][0] : null };
}

function equityChart(el, st, bal) {
  const ch = makeChart(el, { rightPriceScale: { borderColor: cssVar("--line2") }, timeScale: { borderColor: cssVar("--line2"), timeVisible: false } });
  const s = ch.addBaselineSeries({ baseValue: { type: "price", price: bal }, lineWidth: 2, priceLineVisible: false,
    topLineColor: "#16c784", topFillColor1: "rgba(22,199,132,.28)", topFillColor2: "rgba(22,199,132,.04)",
    bottomLineColor: "#ef4444", bottomFillColor1: "rgba(239,68,68,.04)", bottomFillColor2: "rgba(239,68,68,.28)",
    priceFormat: { type: "price", precision: 0, minMove: 1 } });
  const pts = st.curve.length ? [{ time: st.curve[0].time - 3600, value: bal }, ...st.curve] : [];
  s.setData(dedupeTimes(pts.map((p) => ({ time: shiftT(p.time), value: p.value }))));
  s.createPriceLine({ price: bal, color: cssVar("--faint"), lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: "ทุนเริ่ม" });
  ch.timeScale().fitContent();
  return ch;
}

const PARAM_SRC = {
  live: ["ระบบจริง", "พารามิเตอร์ที่ใช้ส่งแจ้งเตือนจริงตอนนี้"],
  wf: ["ผลวิจัยล่าสุด", "พารามิเตอร์ที่ Walk-Forward เลือกไว้ตอนต้นรอบ 3 เดือนปัจจุบัน (ไม่ได้ใช้เทรดจริง)"],
  default: ["ค่าเริ่มต้น", "กลยุทธ์ × TF นี้ไม่ได้อยู่ในการวิจัย จึงใช้ค่าเริ่มต้นของกลยุทธ์"],
  custom: ["กำหนดเอง", "พารามิเตอร์ที่คุณใส่เอง"],
};

async function renderChart(view) {
  const token = S.chartToken = (S.chartToken || 0) + 1;
  const sel = pickSel(qs(), "sel");
  store.set("sel", sel);
  const { symbol: sym, tf, strategy: sid } = sel;
  const sys = sysOf(sym);
  const overlay = store.get("chartOverlay", true);
  let tab = store.get("chartTab", "chart");
  view.innerHTML = wsHtml({
    title: `<span class="sym" id="chTitle">${sym}</span><span class="muted" id="chSub">${tf}</span><span id="liveTag"></span><span id="chBadge"></span>${heldBadge(heldOf(sym, sid, tf), innerWidth < 760)}`,
    tabs: [["chart", "กราฟ"], ["trades", "เทรดล่าสุด"]], tab,
    actions: `<button class="ibtn ${overlay ? "on" : ""}" id="ovl" data-tip="แสดง/ซ่อน indicator และจุดเข้าออกของระบบ">${ICONS.ind}</button>`,
    main: `<div class="chart-box" id="chartBox"><div class="chart fill" id="mainChart"></div><div class="hist-note" id="chHist"></div></div><div class="ws-pane" id="trPane" hidden></div>`,
    sel,
  });
  bindRail(view, sel, (n) => goSel("chart", n));
  $("#ovl").onclick = () => { store.set("chartOverlay", !overlay); navigate(); };
  const showTab = (t) => { tab = t; $("#chartBox").hidden = t !== "chart"; $("#trPane").hidden = t !== "trades"; };
  bindTabs(view, "chartTab", showTab);
  showTab(tab);

  const sp = await api(`/api/system?symbol=${sym}&tf=${tf}&strategy=${sid}`);
  if (route() !== "chart" || S.chartToken !== token) return;
  const strat = stratById(sid) || {};
  const live = sp.is_live;
  $("#chBadge").innerHTML = live ? `${statusBadge(sys.status)} ${isPortfolioSys(sym, sid, tf) ? '<span class="badge ghost" data-tip="ระบบพอร์ต: เทรดทุกสินทรัพย์ เสี่ยง ${S.settings?.portfolio_risk_pct ?? 0.5}%/ไม้">★<span class="hide-sm"> พอร์ต ${S.settings?.portfolio_risk_pct ?? 0.5}%</span></span>' : '<span class="badge ghost" data-tip="ระบบที่เทรดจริง">★<span class="hide-sm"> ระบบจริง</span></span>'}` : `<span class="badge ghost" data-tip="ไม่เปลี่ยนระบบจริงและไม่ส่งแจ้งเตือน">ดูอย่างเดียว</span>`;

  // ---- main chart (candles + the chosen strategy), older years load when scrolled left
  let ch = null, last = null, older = [], yearsLeft = null, loadingOld = false;
  let liveSeries = null, liveBar = null, liveDigits = 2, tickBucket = null, tailSrc = "";
  const PARTIAL = { color: "rgba(139,147,161,.55)", wickColor: "rgba(139,147,161,.8)", borderColor: "rgba(139,147,161,.8)" };
  const TFS = { H1: 3600, H4: 14400, D1: 86400 }[tf];
  const LB = ((S.liveBars ||= {})[sym + tf] ||= []);
  const histLabel = () => {
    const el = $("#chHist"); if (!el || !last) return;
    const from = (older[0] || last.cd.candles[0])?.time;
    el.textContent = yearsLeft?.length ? `◀ เลื่อนซ้ายเพื่อดูย้อนหลัง (ถึงปี ${yearsLeft[0]}) · ตอนนี้ตั้งแต่ ${fdt(from, true).split(",")[0]}` : `ข้อมูลตั้งแต่ ${fdt(from, true).split(",")[0]}`;
  };
  const loadOlder = async () => {
    if (loadingOld || !yearsLeft?.length) return;
    loadingOld = true;
    const y = yearsLeft[yearsLeft.length - 1];
    $("#chHist").textContent = `กำลังโหลดปี ${y}…`;
    try {
      const firstT = (older[0] || last.cd.candles[0])?.time;
      const r = await api(`/api/candles_year?symbol=${sym}&tf=${tf}&year=${y}&before=${firstT}`);
      yearsLeft.pop();
      const add = (r.candles || []).filter((c) => c.time < firstT);
      older = add.concat(older);
      await draw(true, add.length, true);
    } catch (e) { toast("โหลดข้อมูลย้อนหลังไม่ได้: " + e.message); } finally { loadingOld = false; histLabel(); }
  };
  const draw = async (keepRange, shiftBy = 0, reuse = false) => {
    const [cd0, sigs] = reuse && last ? [last.cd, last.sigs] : await Promise.all([api(`/api/candles?symbol=${sym}&tf=${tf}&limit=1500&system=false`), api("/api/signals?limit=50")]);
    if (route() !== "chart" || S.chartToken !== token || !$("#mainChart")) return;
    last = { cd: cd0, sigs };
    if (yearsLeft === null) yearsLeft = (cd0.older_years || []).slice();
    const cut = cd0.candles[0]?.time ?? Infinity;
    const cd = { ...cd0, candles: older.filter((c) => c.time < cut).concat(cd0.candles) };
    const rec = await recentBars(sym);
    if (route() !== "chart" || S.chartToken !== token || !$("#mainChart")) return;
    const tail = rec?.bars?.length ? aggregate(rec.bars, TFS) : [];
    if (tail.length) {
      const from = tail[0].time;
      cd.candles = cd.candles.filter((c) => c.time < from).concat(tail);
      cd.source = `${cd0.source || ""} · ${tail.length} แท่งล่าสุดจาก ${rec.src}`;
      tailSrc = rec.src;
    } else if (rec?.error) cd.source = `${cd0.source || ""} · ${rec.src}: ${rec.error}`;
    const range0 = keepRange && ch ? ch.timeScale().getVisibleLogicalRange() : null;
    const range = range0 ? { from: range0.from + shiftBy, to: range0.to + shiftBy } : null;
    if (ch) { S.charts = S.charts.filter((c) => c !== ch); ch.remove(); }
    const dg = sp.digits ?? cd.digits;
    ch = makeChart($("#mainChart"), { localization: { priceFormatter: (p) => p.toFixed(dg) } });
    const CC = CANDLE[prefs.get().candle] || CANDLE.classic;
    const cs = ch.addCandlestickSeries({ upColor: CC.up, downColor: CC.down, borderVisible: false, wickUpColor: CC.up, wickDownColor: CC.down,
      priceFormat: { type: "price", precision: dg, minMove: 1 / 10 ** dg } });
    // FX / gold / indices: no weekend bars (some feeds print flat ones); lines only where a candle exists, so the
    // time axis never gets empty "weekend" columns
    if (!BINANCE_LIVE[sym]) cd.candles = cd.candles.filter((c) => marketOpen({ ...c, high: c.high > c.low ? c.high : c.high + 1e-9 }));
    const candleT = new Set(cd.candles.map((c) => c.time));
    cs.setData(cd.candles.map((c) => ({ ...c, time: shiftT(c.time) })));
    let open = null;
    const legend = [];
    if (overlay) {
      Object.entries(sp.lines || {}).forEach(([name, vals], i) => {
        const color = LINE_COLORS[i % 5];
        const ls = ch.addLineSeries({ color, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
        ls.setData(vals.map((v, k) => (v === null || !candleT.has(sp.t[k]) ? null : { time: shiftT(sp.t[k]), value: v })).filter(Boolean));
        legend.push(`<span><i style="background:${color}"></i>${esc(name)}</span>`);
      });
      const liveSigs = live ? sigs.filter((s) => s.symbol === sym && s.tf === tf && s.strategy === sid && s.outcome === "open") : [];
      open = live ? openPosition(sp, liveSigs) : (sp.open_trade ? { ...sp.open_trade, from: sp.open_trade.entry_time, pending: false } : null);
      const psys = liveSystems(sym).find((x) => x.strategy === sid && x.tf === tf && x.source === "portfolio");
      if (open) open.lot = live ? (psys ? lotLabel(sym, open.entry, open.sl, psys.risk_pct || S.settings?.portfolio_risk_pct || 0.1, true) : lotLabel(sym, open.entry, open.sl, sys.risk_pct)) : "ทดลอง";
      const markers = drawTradeLevels(ch, { candles: cd.candles, trades: sp.trades, digits: dg }, open);
      markers.sort((a, b) => a.time - b.time); cs.setMarkers(markers);
    }
    S.chartOpen = open;
    liveSeries = cs; liveBar = { ...cd.candles[cd.candles.length - 1] }; liveDigits = dg;
    // candles built from live ticks since the page opened survive the periodic redraw
    for (const b of LB) {
      if (b.time < liveBar.time) continue;
      liveBar = b.time === liveBar.time ? { ...liveBar, high: Math.max(liveBar.high, b.high), low: Math.min(liveBar.low, b.low), close: b.close } : { ...b };
      try { cs.update({ ...liveBar, time: shiftT(liveBar.time) }); } catch { /* out of order */ }
    }
    const n = cd.candles.length, half = Math.max(20, Math.round((prefs.get().bars || 220) / 2));
    // the chart reopens where the user left it (per symbol × TF): position kept relative to the latest candle,
    // so new candles keep the same view; first visit = latest candle in the middle
    const vkey = `${sym}|${tf}`, views = store.get("chartView", {}), sv = views[vkey];
    ch.timeScale().setVisibleLogicalRange(range || (sv && isFinite(sv.toOff) && isFinite(sv.width)
      ? { from: n - 1 - sv.toOff - sv.width, to: n - 1 - sv.toOff } : { from: n - 1 - half, to: n - 1 + half }));
    let vt = null;
    ch.timeScale().subscribeVisibleLogicalRangeChange((r) => {
      if (!r) return;
      clearTimeout(vt);
      vt = setTimeout(() => { const nn = liveSeries ? n + Math.max(0, LB.filter((b) => b.time > cd.candles[n - 1].time).length) : n;
        const all = store.get("chartView", {}); all[vkey] = { toOff: nn - 1 - r.to, width: r.to - r.from }; store.set("chartView", all); }, 400);
    });
    $("#chTitle").textContent = sym;
    $("#chSub").innerHTML = `${tf} · <b class="mono">${fpx(cd.candles[n - 1]?.close, dg)}</b>`;
    $("#chHist").innerHTML = "";
    ch.timeScale().subscribeVisibleLogicalRangeChange((r) => { if (r && r.from < 40) loadOlder(); });
    histLabel();
    renderFoot(dg, legend, cd.source);
  };
  const renderFoot = (dg, legend, source) => {
    const o = S.chartOpen;
    $("#wsFoot").innerHTML = `
      <span class="fi strat"><b>#${strat.no}</b> ${esc(sp.name)} <span class="muted">· ${esc(paramText(sp.params))}</span></span>
      ${footItem("สัญญาณแท่งล่าสุด", sp.last_signal > 0 ? "LONG" : sp.last_signal < 0 ? "SHORT" : "ไม่มี", sp.last_signal > 0 ? "pos" : sp.last_signal < 0 ? "neg" : "")}
      ${o ? footItem(live ? "ออเดอร์ที่เปิดอยู่" : "ออเดอร์ (ทดลอง)", `<span class="${o.dir > 0 ? "pos" : "neg"}">${o.dir > 0 ? "LONG" : "SHORT"}</span> ${o.lot} @ ${fpx(o.entry, dg)} · SL <span class="neg">${fpx(o.sl, dg)}</span> · TP <span class="pos">${o.tp == null ? "ไม่ตั้ง" : fpx(o.tp, dg)}</span>${o.reverse_pending ? ' · <span class="warn">กลับทิศแท่งถัดไป</span>' : ""}`, "", true) : footItem("ออเดอร์", "ไม่มี")}
      ${legend.length ? `<span class="fi legend">${legend.join("")}</span>` : ""}
      ${LB.some((b) => b.color) ? '<span class="fi faint"><i style="display:inline-block;width:8px;height:10px;background:rgba(139,147,161,.7);border-radius:2px"></i> แท่งเทา = สร้างจากราคาสดตั้งแต่เปิดหน้า (ยังไม่ครบ)</span>' : ""}
      <span class="fi faint">${esc(source || "")}</span>`;
  };
  await draw(false);
  // live price: the forming candle moves every second (display only)
  Live.start(sym);
  const tag = () => {
    const st = Live.state, el = $("#liveTag"); if (!el) return;
    el.innerHTML = st === "live" && Live.price ? `<span class="live-dot" data-tip="ราคาสดจาก ${Live.src} อัปเดตทุก 1 วินาที">LIVE</span>`
      : st === "nokey" ? `<a class="live-dot off" href="#/settings" title="ใส่ Finnhub API key ในหน้าตั้งค่าเพื่อเปิดราคาสด">ราคาสด: ตั้งค่า</a>`
      : st === "connecting" || (st === "live" && !Live.price) ? `<span class="live-dot off">กำลังเชื่อมต่อ…</span>` : st === "error" ? `<span class="live-dot off">ราคาสดไม่พร้อม</span>` : "";
  };
  S.liveTimer = setInterval(() => {
    if (route() !== "chart" || S.chartToken !== token) return clearInterval(S.liveTimer);
    tag();
    const p = Live.price; if (!p || !liveSeries || !liveBar) return;
    const t = Math.floor((Live.ts || Date.now()) / 1000), start = bucketStart(t, TFS);
    if (start < liveBar.time) return;
    if (tickBucket === null && !tailSrc) {          // first tick: how far is the live feed from the chart's last candle?
      const d = p - liveBar.close;
      if (Math.abs(d) / p > 0.003 && !S.gapWarned?.[sym]) {
        (S.gapWarned ||= {})[sym] = 1;
        toast(`ราคาสด (${Live.src}) ต่างจากแท่งล่าสุดบนกราฟ ${d > 0 ? "+" : ""}${fpx(d, liveDigits)} — แท่งเก่ามาจาก Yahoo ซึ่งอัปเดตทุกชั่วโมงและเป็นคนละแหล่ง` +
          (TWELVE_SYM[sym] && !prefs.get().twelve ? " · ใส่ Twelve Data key ในหน้าตั้งค่าเพื่อดึงแท่งจริงล่าสุด" : ""), 7000);
      }
    }
    if (start > liveBar.time) {
      if (tickBucket === liveBar.time) {
        // we watched the previous candle close: the new one opens at its first trade, exactly like the market
        liveBar = { time: start, open: p, high: p, low: p, close: p };
      } else {
        // page opened in the middle of a candle the chart does not have yet: its real open / high / low before now are unknown.
        // Open it at the previous candle's close (continuous markets) and draw it grey until the hourly data brings the real one.
        const adjacent = bucketStart(liveBar.time + TFS + 60, TFS) === start;
        const o = adjacent ? liveBar.close : p;
        liveBar = { time: start, open: o, high: Math.max(o, p), low: Math.min(o, p), close: p, ...PARTIAL };
      }
    } else liveBar = { ...liveBar, high: Math.max(liveBar.high, p), low: Math.min(liveBar.low, p), close: p };
    tickBucket = start;
    if (LB.length && LB[LB.length - 1].time === liveBar.time) LB[LB.length - 1] = liveBar; else { LB.push(liveBar); if (LB.length > 60) LB.shift(); }
    try { liveSeries.update({ ...liveBar, time: shiftT(liveBar.time) }); } catch { /* chart rebuilding */ }
    $("#chSub").innerHTML = `${tf} · <b class="mono">${fpx(p, liveDigits)}</b>`;
  }, 1000);
  tag();
  const timer = setInterval(() => { if (route() !== "chart" || S.chartToken !== token) return clearInterval(timer); if (!document.hidden && tab === "chart") draw(true).catch(() => {}); },
    STATIC ? 300000 : 60000);

  // ---- latest trades tab
  const dg = sp.digits;
  const tr = (sp.trades || []).slice(-60).reverse();
  $("#trPane").innerHTML = `<div class="pane-hd"><b>เทรดล่าสุดของระบบ #${strat.no} ${esc(sp.name)}</b><span class="muted small">${sym} ${tf} · ${tr.length} ไม้ล่าสุด · ใช้กติกาเดียวกับ backtest</span></div>
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th>เข้า</th><th>ฝั่ง</th><th class="num">Entry</th><th class="num">SL</th><th class="num">TP</th><th>ออก</th><th class="num">Exit</th><th>เหตุผล</th><th class="num">R</th></tr></thead><tbody>
    ${tr.map((t) => `<tr><td>${fdt(t.entry_time, true)}</td><td>${t.dir > 0 ? '<span class="pos">LONG</span>' : '<span class="neg">SHORT</span>'}</td><td class="num">${fpx(t.entry, dg)}</td><td class="num">${fpx(t.sl, dg)}</td><td class="num">${fpx(t.tp, dg)}</td><td>${fdt(t.exit_time)}</td><td class="num">${fpx(t.exit, dg)}</td><td>${reasonTh(t.reason)}</td><td class="num ${cls(t.r)}">${fr(t.r)}</td></tr>`).join("") || '<tr><td colspan="9" class="muted">ยังไม่มีเทรด</td></tr>'}</tbody></table></div>`;
}

// ------------------------------------------------------------------ SYSTEMS: equity curve · system details · catalogue of all systems
function paramRows(st, values) {
  return st.params.filter((p) => p.key !== "max_hold_h").map((p) => {
    const tested = st.grid?.[p.key];
    return `<tr><td>${esc(p.label)}<div class="tiny faint mono">${p.key}</div></td><td class="num"><b>${values?.[p.key] ?? p.default}</b></td><td class="num muted">${p.default}</td><td class="num muted">${tested ? tested.join(" / ") : "–"}</td></tr>`;
  }).join("");
}
function systemDoc(st, sp, opts = {}) {
  const src = PARAM_SRC[sp?.params_source] || null;
  return `<div class="doc">
    <div class="doc-hd"><div class="no">${st.no}</div><div><h2>${esc(st.name)}</h2><div class="muted">${esc(st.family)}</div></div></div>
    <p class="doc-why">${esc(st.rationale)}</p>
    <div class="doc-grid">
      <section><h4>Indicator ที่ใช้</h4><ul>${st.indicators.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></section>
      <section><h4>เงื่อนไขการเทรด</h4>
        <div class="rule long"><span>LONG / BUY</span><p>${esc(st.long)}</p></div>
        <div class="rule short"><span>SHORT / SELL</span><p>${esc(st.short)}</p></div>
        <div class="rule exit"><span>ออกจากออเดอร์</span><p>${esc(st.exits)}</p></div></section>
    </div>
    <section><h4>พารามิเตอร์${opts.symbol ? ` <span class="muted small">(${opts.symbol} ${opts.tf}${src ? " · " + src[0] : ""})</span>` : ""}</h4>
      <div class="tbl-wrap"><table class="tbl"><thead><tr><th>พารามิเตอร์</th><th class="num">ค่าที่ใช้</th><th class="num">ค่าเริ่มต้น</th><th class="num">ค่าที่ทดสอบใน Walk-Forward</th></tr></thead>
      <tbody>${paramRows(st, sp?.params)}</tbody></table></div>
      ${src ? `<div class="hint" style="margin-top:8px">${src[1]}</div>` : ""}</section>
  </div>`;
}

async function renderSystems(view) {
  const token = S.sysToken = (S.sysToken || 0) + 1;
  const sel = pickSel(qs(), "sel");
  store.set("sel", sel);
  const { symbol: sym, tf, strategy: sid } = sel;
  const st = stratById(sid);
  let tab = store.get("sysTab", "equity");
  let mode = store.get("eqMode", "oos");
  view.innerHTML = wsHtml({
    title: `<span class="sym">#${st.no}</span><span class="ttl">${esc(st.name)}</span><span class="muted">${sym} · ${tf}</span><span id="sysBadge"></span>`,
    tabs: [["equity", "Equity curve"], ["detail", "รายละเอียดระบบ"], ["catalog", `รายชื่อระบบ (${S.strategies.length})`]], tab,
    actions: `<div class="seg sm" id="eqSeg"><button data-m="oos" data-tip="ผลบนข้อมูลที่ระบบไม่เคยเห็น (ใช้ตัดสิน)">WF OOS</button><button data-m="bt" data-tip="ใช้พารามิเตอร์ปัจจุบันย้อนทดสอบทั้งช่วง">Backtest</button></div>`,
    main: `<div class="chart fill" id="eqChart"></div><div class="ws-pane" id="detailPane" hidden></div><div class="ws-pane" id="catPane" hidden></div>`,
    sel,
  });
  bindRail(view, sel, (n) => goSel("systems", n));
  const sp = await api(`/api/system?symbol=${sym}&tf=${tf}&strategy=${sid}`);
  if (route() !== "systems" || S.sysToken !== token) return;
  const sys = sysOf(sym);
  $("#sysBadge").innerHTML = sp.is_live ? `${statusBadge(sys.status)} <span class="badge ghost" data-tip="ระบบที่เทรดจริง">★<span class="hide-sm"> ระบบจริง</span></span>` : "";

  const bal = myBalance() || 5000, risk = effRisk(sys.risk_pct || 0.5) || 0.5;
  const modes = { oos: sp.oos?.equity?.length ? sp.oos.equity : null, bt: sp.equity?.length ? sp.equity : null };
  if (!modes[mode]) mode = modes.oos ? "oos" : "bt";
  let eqCh = null;
  const showEq = () => {
    $$("#eqSeg button").forEach((b) => { b.classList.toggle("active", b.dataset.m === mode); b.disabled = !modes[b.dataset.m]; });
    if (eqCh) { S.charts = S.charts.filter((c) => c !== eqCh); eqCh.remove(); eqCh = null; }
    const pts = modes[mode];
    if (!pts) { $("#eqChart").innerHTML = `<div class="empty"><div class="big">📉</div>ระบบนี้ไม่มีเทรดบน ${sym} ${tf}</div>`; $("#wsFoot").innerHTML = ""; return; }
    const s = equityStats(pts, risk, bal);
    eqCh = equityChart($("#eqChart"), s, bal);
    $("#wsFoot").innerHTML = `${footItem(mode === "oos" ? "Walk-Forward OOS" : "Backtest ทั้งช่วง", `${fdt(s.from, true).split(",")[0]} → ${fdt(s.to, true).split(",")[0]}`)}
      ${footItem("กำไรสุทธิ", `${s.net >= 0 ? "+" : ""}$${fnum(s.net, 0)} (${s.netPct >= 0 ? "+" : ""}${fnum(s.netPct, 1)}%)`, cls(s.net))}
      ${footItem("เทรด", s.n)}${footItem("ชนะ", fnum(s.winPct, 1) + "%")}${footItem("PF", fnum(s.pf, 2), s.pf >= 1 ? "pos" : "neg")}
      ${footItem("Max DD", `$${fnum(s.maxDdUsd, 0)} (${fnum(s.maxDd, 1)}%)`, "neg")}${footItem("เฉลี่ย/ไม้", fr(s.avgR, 3), cls(s.avgR))}${footItem("t", fnum(s.t, 2))}
      <span class="fi faint">ทบต้น เริ่ม $${fnum(bal, 0)} เสี่ยง ${risk}%/ไม้ · รวมต้นทุนแล้ว</span>`;
  };
  $("#eqSeg").onclick = (e) => { const b = e.target.closest("[data-m]"); if (b && !b.disabled) { mode = b.dataset.m; store.set("eqMode", mode); showEq(); } };

  $("#detailPane").innerHTML = systemDoc(st, sp, { symbol: sym, tf }) + (sp.oos ? `<div class="doc"><section><h4>ผลบน ${sym} ${tf}</h4><div class="kpis">
      ${kpi("WF OOS เทรด", sp.oos.metrics.trades, `${fnum(sp.oos.years, 1)} ปี`)}${kpi("Expectancy", fr(sp.oos.metrics.expectancy, 3), "t = " + fnum(sp.oos.metrics.t_stat, 2), cls(sp.oos.metrics.expectancy))}
      ${kpi("Profit factor", fnum(sp.oos.metrics.pf, 2))}${kpi("Win rate", sp.oos.metrics.win_rate + "%")}${kpi("Backtest ทั้งช่วง", fr(sp.metrics.expectancy, 3), sp.metrics.trades + " ไม้")}</div>
      <div class="row" style="margin-top:12px"><a class="btn sm" href="#/lab?symbol=${sym}&tf=${tf}&strategy=${sid}">🧪 ดูผลทดสอบทีละขั้นใน Strategy Lab</a><a class="btn sm ghost" href="#/chart?symbol=${sym}&tf=${tf}&strategy=${sid}">📈 ดูบนกราฟ</a></div></section></div>` : "");
  const fams = [...S.strategies.reduce((m, s) => m.set(s.family, (m.get(s.family) || 0) + 1), new Map())].sort((a, b) => b[1] - a[1]);
  const famTh = { "Trend following": "ตามเทรนด์", Momentum: "โมเมนตัม", "Volatility breakout": "เบรกเอาต์ความผันผวน", "Session / liquidity": "ช่วงเวลา / สภาพคล่อง", "Price action": "Price action", Pullback: "ย่อตัว (pullback)" };
  const catF = store.get("catF", "all");
  $("#catPane").innerHTML = `<div class="cat-hd"><input class="inp" id="catQ" placeholder="ค้นหาเลข ชื่อ กลุ่ม หรือ indicator…"><span class="muted small">กดการ์ดเพื่อเลือกระบบนั้น</span></div>
    <div class="pills cat-f" id="catF"><button class="pill ${catF === "all" ? "on" : ""}" data-f="all">ทั้งหมด <small>${S.strategies.length}</small></button><button class="pill ${catF === "live" ? "on" : ""}" data-f="live">★ ACTIVE บน ${sym}</button>${fams.map(([f, n]) => `<button class="pill ${catF === f ? "on" : ""}" data-f="${esc(f)}">${esc(famTh[f] || f)} <small>${n}</small></button>`).join("")}</div>
    <div class="cat">${S.strategies.map((s) => `<button class="cat-card ${s.id === sid ? "on" : ""}" data-sid="${s.id}" data-fam="${esc(s.family)}" data-live="${isLiveSys(sym, s.id) ? 1 : ""}" data-q="${esc((s.no + " " + s.name + " " + s.family + " " + s.indicators.join(" ")).toLowerCase())}">
      <div class="cat-top"><span class="no">${s.no}</span><div><b>${esc(s.name)}</b><div class="tiny muted">${esc(s.family)}</div></div>${isLiveSys(sym, s.id) ? '<i class="star">★</i>' : ""}</div>
      <div class="tiny"><span class="muted">Indicator:</span> ${esc(s.indicators.join(", "))}</div>
      <div class="tiny"><span class="pos">LONG:</span> ${esc(s.long)}</div></button>`).join("")}</div>`;
  let curF = catF;
  const filt = () => { const q = $("#catQ").value.trim().toLowerCase();
    $$(".cat-card").forEach((b) => b.hidden = (q && !b.dataset.q.includes(q)) || (curF === "live" ? !b.dataset.live : curF !== "all" && b.dataset.fam !== curF)); };
  $("#catQ").oninput = filt;
  $("#catF").onclick = (e) => { const b = e.target.closest("[data-f]"); if (!b) return; curF = b.dataset.f; store.set("catF", curF);
    $$("#catF .pill").forEach((x) => x.classList.toggle("on", x === b)); filt(); };
  filt();
  $("#catPane").onclick = (e) => { const b = e.target.closest("[data-sid]"); if (b) { store.set("sysTab", "detail"); goSel("systems", { ...sel, strategy: b.dataset.sid }); } };

  const showTab = (t) => {
    tab = t;
    $("#eqChart").hidden = t !== "equity"; $("#detailPane").hidden = t !== "detail"; $("#catPane").hidden = t !== "catalog";
    $("#eqSeg").style.visibility = t === "equity" ? "visible" : "hidden";
    if (t === "equity") showEq(); else $("#wsFoot").innerHTML = `<span class="fi strat"><b>#${st.no}</b> ${esc(st.name)} <span class="muted">· ${esc(paramText(sp.params))}</span></span>${sp.is_live ? footItem("สถานะ", statusBadge(sys.status)) : footItem("บทบาท", "ดูอย่างเดียว")}`;
  };
  bindTabs(view, "sysTab", showTab);
  showTab(tab);
}

// ------------------------------------------------------------------ STRATEGY LAB: the research result of one system, step by step
function mcJs(r, nTrades, riskPct, sims = 3000) {  // bootstrap Monte Carlo of R-multiples (same idea as app/montecarlo.py)
  if (!r || r.length < 5) return null;
  let seed = 11; const rnd = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
  const f = riskPct / 100, n = Math.max(1, Math.round(nTrades)), rets = [], dds = [], pfs = [], exps = [];
  for (let s = 0; s < sims; s++) {
    let eq = 1, peak = 1, dd = 0, gw = 0, gl = 0, sum = 0;
    for (let i = 0; i < n; i++) {
      const x = r[Math.floor(rnd() * r.length)];
      eq *= 1 + f * x; peak = Math.max(peak, eq); dd = Math.max(dd, (peak - eq) / peak);
      if (x > 0) gw += x; else gl -= x; sum += x;
    }
    rets.push((eq - 1) * 100); dds.push(dd * 100); pfs.push(gl > 0 ? gw / gl : 99); exps.push(sum / n);
  }
  const pct = (a, p) => { const b = a.slice().sort((x, y) => x - y); return b[Math.min(b.length - 1, Math.floor(p / 100 * b.length))]; };
  return { ret50: pct(rets, 50), ret5: pct(rets, 5), dd50: pct(dds, 50), dd95: pct(dds, 95), pf5: pct(pfs, 5), exp5: pct(exps, 5),
    prob: rets.filter((x) => x > 0).length / sims * 100, n };
}
const step = (no, title, body, state = "") => `<section class="step ${state}"><div class="step-no">${no}</div><div class="step-bd"><h3>${title}</h3>${body}</div></section>`;
const verdict = (ok, txt) => `<div class="verdict ${ok === null ? "na" : ok ? "ok" : "no"}">${ok === null ? "–" : ok ? "✓" : "✗"} ${txt}</div>`;

async function renderLab(view) {
  const token = S.labToken = (S.labToken || 0) + 1;
  const [res, notes] = await Promise.all([api("/api/research"), api("/api/research/notes")]);
  const q = qs(), prev = store.get("labSel", {});
  const sym = S.symbols.some((x) => x.symbol === q.symbol) ? q.symbol : (prev.symbol || store.get("sel", {}).symbol || "XAUUSD");
  const R = res.symbols?.[sym] || {};
  const best = { strategy: R.strategy, tf: R.tf };
  // default = the best system of this symbol; picking TF / system on the rail shows that candidate instead
  const sameSym = prev.symbol === sym;
  const tf = ["H1", "H4", "D1"].includes(q.tf) ? q.tf : (sameSym && prev.tf) || best.tf || "H1";
  const sid = stratById(q.strategy) ? q.strategy : (sameSym && prev.strategy) || best.strategy || S.strategies[0].id;
  const sel = { symbol: sym, tf, strategy: sid };
  store.set("labSel", sel);
  const st = stratById(sid);
  const isBest = isLiveSys(sym, sid, tf) || (!(R.systems || []).length && best.strategy === sid && best.tf === tf);
  let tab = store.get("labTab2", "steps");
  const runBtn = STATIC ? `<a class="btn sm primary" href="${actionsUrl()}" target="_blank" rel="noopener" data-tip="เปิด GitHub Actions → Run workflow → research">▶<span class="hide-sm"> สั่งวิเคราะห์ใหม่ทันที</span><span class="only-sm"> วิเคราะห์ใหม่</span></a>`
    : `<button class="btn sm primary" id="runAll">▶<span class="hide-sm"> สั่งวิเคราะห์ใหม่ทันที</span><span class="only-sm"> วิเคราะห์ใหม่</span></button>`;
  const tabs = [["steps", "ผลทดสอบทีละขั้น"], ["overview", "ภาพรวมทุกสินทรัพย์"], ["notes", "บันทึกจาก AI"]];
  if (!STATIC) tabs.push(["manual", "ทดลองเอง"]);
  view.innerHTML = wsHtml({
    title: `<span class="sym">#${st.no}</span><span class="ttl">${esc(st.name)}</span><span class="muted">${sym} · ${tf}</span>${isPortfolioSys(sym, sid, tf) ? `${statusBadge("ACTIVE")}<span class="badge ghost" data-tip="ผ่านเกณฑ์แบบพอร์ต (ทุกสินทรัพย์รวมกัน) — ผลรายสินทรัพย์ด้านล่างอาจยังไม่ผ่านเกณฑ์เดี่ยว">พอร์ต</span>` : statusBadge((R.candidates || []).find((c) => c.strategy === sid && c.tf === tf)?.status || R.status || "–")}${isLiveSys(sym, sid, tf) ? '<span class="badge ghost" data-tip="เทรดจริงไตรมาสนี้">★<span class="hide-sm"> เทรดจริงไตรมาสนี้</span></span>' : ""}`,
    tabs, tab, actions: runBtn,
    main: `<div class="ws-pane" id="stepsPane"><div class="empty"><div class="spinner"></div></div></div><div class="ws-pane" id="ovPane" hidden></div><div class="ws-pane md" id="notesPane" hidden></div><div class="ws-pane" id="manPane" hidden></div>`,
    sel,
  });
  bindRail(view, sel, (n, what) => {
    if (what === "symbol") { const b = res.symbols?.[n.symbol]; store.set("labSel", { symbol: n.symbol }); goSel("lab", { symbol: n.symbol, tf: b?.tf || n.tf, strategy: b?.strategy || n.strategy }); }
    else goSel("lab", n);
  });
  if (!STATIC) $("#runAll").onclick = async () => { await api("/api/research/run", { method: "POST", body: {} }); toast("เริ่มวิเคราะห์ใหม่แล้ว"); pollResearch(); };

  const showTab = (t) => {
    tab = t;
    ["steps", "ov", "notes", "man"].forEach((k) => { const el = $(`#${k}Pane`); if (el) el.hidden = !(t === k || (t === "overview" && k === "ov") || (t === "manual" && k === "man")); });
    if (t === "manual" && !$("#manPane").dataset.done) { $("#manPane").dataset.done = 1; renderLabManual($("#manPane")); }
  };
  bindTabs(view, "labTab2", showTab);
  showTab(tab);

  // ---- overview of the 8 symbols
  const syms = Object.values(res.symbols || {});
  $("#ovPane").innerHTML = `<div class="pane-hd"><b>ผลวิเคราะห์ล่าสุดของทุกสินทรัพย์</b><span class="muted small">${res.generated ? fdt(res.generated, true) : "ยังไม่เคยวิเคราะห์"} · ${S.strategies.length} ระบบ × ${(res.settings?.timeframes || []).join("/")}</span></div>
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th>สินทรัพย์</th><th>สถานะ</th><th>ระบบที่ดีที่สุด</th><th>TF</th><th class="num">ข้อมูล</th><th class="num">OOS ไม้</th><th class="num">Exp</th><th class="num">t / ต้องการ</th><th class="num">Robust</th></tr></thead><tbody>
    ${syms.map((s) => { const o = s.oos || {}; const sx = stratById(s.strategy); return `<tr class="click" data-go="${s.symbol}"><td><b>${s.symbol}</b></td><td>${statusBadge(s.status)}</td><td>${sx ? `<b>#${sx.no}</b> ${esc(sx.name)}` : "–"}</td><td>${s.tf || "–"}</td>
      <td class="num">${s.data_from ? s.data_from.slice(0, 4) : "–"}</td><td class="num">${o.trades ?? "–"}</td><td class="num ${cls(o.expectancy)}">${o.expectancy != null ? fnum(o.expectancy, 3) : "–"}</td>
      <td class="num">${fnum(o.t_stat, 2)} / ${fnum(s.t_needed, 2)}</td><td class="num">${s.robustness?.score ?? "–"}</td></tr>`; }).join("")}</tbody></table></div>
    <div class="hint pad-x">กดแถวเพื่อดูผลทดสอบทีละขั้นของระบบที่ดีที่สุดของสินทรัพย์นั้น</div>
    ${portfolioHtml(res.portfolio)}`;
  $("#ovPane").onclick = (e) => {
    const ps = e.target.closest("[data-go-sys]");
    if (ps) { const [strategy, ptf] = ps.dataset.goSys.split("|"); store.set("labTab2", "steps"); goSel("lab", { symbol: sym, tf: ptf, strategy }); return; }
    const r = e.target.closest("[data-go]"); if (!r) return; const s = res.symbols[r.dataset.go]; store.set("labTab2", "steps"); goSel("lab", { symbol: s.symbol, tf: s.tf || "H1", strategy: s.strategy || sid }); };
  $("#notesPane").innerHTML = notes ? md(notes) : '<div class="hint">ยังไม่มีบันทึก</div>';

  // ---- step by step
  const [sp, full, cmpAll, dq] = await Promise.all([api(`/api/system?symbol=${sym}&tf=${tf}&strategy=${sid}`).catch(() => null),
    (S.candCache[sym] ||= api(`/api/research/candidates?symbol=${sym}`).catch(() => [])),
    (S.compare ||= api("/api/compare").catch(() => ({}))), (S.dq ||= api("/api/data_quality").catch(() => ({})))]);
  if (route() !== "lab" || S.labToken !== token) return;
  const cand = (full || []).find((c) => c.strategy === sid && c.tf === tf) || (R.candidates || []).find((c) => c.strategy === sid && c.tf === tf);
  const ranked = (R.candidates || []).filter((c) => c.oos?.trades >= 10).sort((a, b) => b.oos.t_stat - a.oos.t_stat);
  const rank = cand ? ranked.findIndex((c) => c === cand) + 1 : 0;
  const o = cand?.oos, m = sp?.metrics, G = res.settings?.gates || S.settings?.research?.gates || {};
  const rOos = (sp?.oos?.equity || []).map((p) => p[1]);
  const risk = effRisk(sysOf(sym).risk_pct || 0.5) || 0.5;
  const mc = mcJs(rOos, Math.max(10, o?.trades_per_year || 10), risk);
  const mcFull = mcJs(rOos, rOos.length, 1, 2000);
  const tNeed = R.t_needed;
  const gates = cand?.gates ? cand.gates : isBest && R.gates ? R.gates : o ? [
    { name: "OOS trades", value: o.trades, need: G.min_oos_trades, pass: o.trades >= G.min_oos_trades },
    { name: "OOS expectancy (R)", value: o.expectancy, need: G.min_expectancy, pass: o.expectancy >= G.min_expectancy },
    { name: "OOS t-stat", value: o.t_stat, need: G.min_t, pass: o.t_stat >= G.min_t },
    { name: `t ปรับตามจำนวนการทดสอบอิสระ (~${R.n_candidates ?? "?"})`, value: o.t_stat, need: tNeed, pass: tNeed != null && o.t_stat >= tNeed },
    { name: "MC PF @95%", value: mcFull ? +mcFull.pf5.toFixed(3) : null, need: G.min_mc_pf_p5, pass: !!mcFull && mcFull.pf5 >= G.min_mc_pf_p5 },
    { name: "Parameter plateau", value: cand.plateau, need: G.min_plateau, pass: cand.plateau >= G.min_plateau },
    { name: "ชนะการสุ่มจุดเข้า / Robustness", value: null, need: "ทดสอบเฉพาะตัวที่ดีที่สุด", pass: null },
  ] : [];
  const passed = gates.filter((g) => g.pass).length, judged = gates.filter((g) => g.pass !== null).length;
  const rb = cand?.random_benchmark ?? (isBest ? R.random_benchmark : null), rob = cand?.robustness ?? (isBest ? R.robustness : null);
  const oosChart = rOos.length ? `<div class="mini-chart" id="oosMini"></div>` : "";
  $("#stepsPane").innerHTML = `<div class="steps">
    ${step(1, "ข้อมูลและกติกา", `<div class="kv2">${[["ข้อมูล", `${sp?.from ? fdt(sp.from, true).split(",")[0] : "–"} → ปัจจุบัน (${fnum(sp?.years, 1)} ปี)`], ["แหล่งข้อมูล", R.data_source || "–"],
      ["เข้า", "อ่านสัญญาณตอนแท่งปิด เข้าที่ราคาเปิดแท่งถัดไป"], ["ออก", "ชน SL / ชน TP / มีสัญญาณตรงข้าม (กลับทิศ)"], ["ต้นทุน", "หัก spread ตามชั่วโมงที่เข้า + slippage × 2 + ค่าคอม ทุกไม้ (swap: ไม่หัก เพราะบัญชีไม่มี swap · มีรอบเทียบแบบหักในข้อ 8)"]].map(([k, v]) => `<span class="k">${k}</span><span>${v}</span>`).join("")}</div>`)}
    ${step(2, "Backtest ทั้งช่วง (พารามิเตอร์ปัจจุบัน)", m ? `<div class="kpis">${kpi("เทรด", m.trades, fnum(m.trades_per_year, 0) + "/ปี")}${kpi("Expectancy", fr(m.expectancy, 3), "", cls(m.expectancy))}${kpi("t-stat", fnum(m.t_stat, 2))}${kpi("PF", fnum(m.pf, 2))}${kpi("Win rate", m.win_rate + "%")}${kpi("Max DD", fnum(m.max_dd_r, 1) + "R", "", "neg")}</div>
      <div class="hint">⚠️ เป็นผล in-sample — พารามิเตอร์ถูกเลือกจากข้อมูลบางส่วนในช่วงนี้เอง จึงมักดูดีเกินจริง ใช้ดูภาพรวมเท่านั้น</div>` : '<div class="hint">ไม่มีข้อมูล</div>')}
    ${step(3, "Walk-Forward (ข้อสอบที่ระบบไม่เคยเห็น)", o ? `<div class="kpis">${kpi("OOS เทรด", o.trades, `${fnum(cand.oos_years, 1)} ปี`)}${kpi("Expectancy", fr(o.expectancy, 3), "", cls(o.expectancy))}${kpi("t-stat", fnum(o.t_stat, 2), o.t_stat >= 2 ? "มีนัยสำคัญ" : "ยังไม่มีนัยสำคัญ", o.t_stat >= 2 ? "pos" : "")}${kpi("PF", fnum(o.pf, 2))}${kpi("WF Efficiency", cand.wfe ?? "–", "OOS ÷ IS")}${kpi("Plateau", fnum(cand.plateau * 100, 0) + "%", "พารามิเตอร์ข้างเคียงที่กำไร")}</div>
      ${oosChart}<div class="hint">${((w) => `ทุก ${w.oos_months / 12 >= 1 ? w.oos_months / 12 + " ปี" : w.oos_months + " เดือน"} เลือกพารามิเตอร์จาก${w.is_mode === "expanding" ? `ข้อมูลทั้งหมดก่อนหน้า (อย่างน้อย ${w.is_months / 12} ปี)` : `ข้อมูล ${w.is_months / 12} ปีก่อนหน้า`} (${tf})`)(res.settings?.wfa?.[tf] || { is_mode: res.settings?.is_mode || "expanding", is_months: res.settings?.is_months || 24, oos_months: res.settings?.oos_months || 3 })} โดยเลือกชุดที่อยู่บน “ที่ราบ” (ชุดข้างเคียงก็ดี) และเปลี่ยนชุดเฉพาะเมื่อดีกว่าเดิมชัดเจน แล้วเทรดช่วงถัดไปโดยห้ามแก้ · เปลี่ยนพารามิเตอร์ ${cand.param_switches} ครั้ง</div>` : `<div class="hint">ระบบนี้บน ${tf} ไม่ได้อยู่ในการวิจัย หรือมีเทรดไม่พอ</div>`, o ? (o.expectancy > 0 ? "ok" : "no") : "na")}
    ${step(4, "คัดเลือก + หักค่าโชค", o ? `<p>ระบบนี้อยู่อันดับ <b>${rank || "–"}</b> จาก <b>${ranked.length}</b> ผู้สมัครที่มีเทรดพอของ ${sym} (เรียงตาม OOS t-stat) · ตัวที่ดีที่สุดคือ <b>${stratLabel(best.strategy)}</b> ${best.tf || ""}</p>
      ${verdict(tNeed != null ? o.t_stat >= tNeed : null, `t-stat ${fnum(o.t_stat, 2)} ${tNeed != null ? (o.t_stat >= tNeed ? "≥" : "<") + " เกณฑ์ " + fnum(tNeed, 2) : ""} (ยิ่งเทียบหลายระบบ เกณฑ์ยิ่งสูง เพื่อตัดตัวที่ดีเพราะโชค)`)}` : '<div class="hint">–</div>')}
    ${step(5, "เทียบกับการสุ่มจุดเข้า", rb ? `${verdict(rb.percentile >= G.min_random_pct, `ชนะการสุ่ม ${rb.percentile}% ของ ${rb.sims} รอบ (ต้อง ≥ ${G.min_random_pct}%) · สุ่มเฉลี่ย ${fr(rb.random_mean, 3)} vs ระบบ ${fr(rb.actual_exp, 3)}`)}`
      : '<div class="hint">ยังไม่มีผล — รอการวิเคราะห์รอบถัดไป (ทุกระบบผ่านทุกขั้นเหมือนกัน)</div>', rb ? (rb.percentile >= G.min_random_pct ? "ok" : "no") : "na")}
    ${step(6, "Robustness — พยายามทำให้ระบบพัง", rob?.tests?.length ? `${robustBar(rob)}<div class="tbl-wrap" style="margin-top:10px"><table class="tbl"><tbody>${rob.tests.map((t) => `<tr><td>${t.pass === null ? '<span class="faint">–</span>' : t.pass ? '<span class="pos">✓</span>' : '<span class="neg">✗</span>'}</td><td class="wrap">${esc(t.name)}${t.note ? `<div class="tiny faint">${esc(t.note)}</div>` : ""}</td><td class="num">${esc(t.value ?? "–")}</td><td class="muted small wrap">${esc(t.need)}</td></tr>`).join("")}</tbody></table></div>`
      : '<div class="hint">ยังไม่มีผล หรือมีเทรด OOS ไม่พอ (ต้อง ≥ 10 ไม้)</div>', rob ? (rob.score >= (G.min_robust_score ?? 60) ? "ok" : "no") : "na")}
    ${step(7, "Monte Carlo (สุ่มลำดับไม้ OOS 3,000 รอบ)", mc ? `<div class="kpis">${kpi("ผลตอบแทน 1 ปี (กลาง)", fnum(mc.ret50, 1) + "%", `${mc.n} ไม้ · เสี่ยง ${risk}%`, cls(mc.ret50))}${kpi("กรณีแย่ 5%", fnum(mc.ret5, 1) + "%", "", cls(mc.ret5))}${kpi("DD 95%", fnum(mc.dd95, 1) + "%", "", "neg")}${kpi("โอกาสกำไรใน 1 ปี", fnum(mc.prob, 0) + "%")}${kpi("PF ที่ 95%", fnum(mcFull?.pf5, 2), "ทั้งชุด OOS", mcFull?.pf5 >= 1 ? "pos" : "neg")}</div>
      ${streakHtml(rOos, o?.trades_per_year)}
      <div class="hint">Monte Carlo บอกว่า “ถ้าโชคไม่ดี” จะแย่ได้แค่ไหน แต่ตรวจ overfitting ไม่ได้ จึงใช้เฉพาะผล OOS</div>` : '<div class="hint">มีเทรด OOS ไม่พอ</div>', mcFull ? (mcFull.pf5 >= 1 ? "ok" : "no") : "na")}
    ${step(8, "เทียบเพิ่มเติม: ฝั่ง Buy/Sell · ซื้อแล้วถือ · ถ้าถูกหัก swap", extraHtml(cand, cmpAll, `${sym}:${sid}:${tf}`, dq?.symbols?.[sym]))}
    ${step(9, "ผลตัดสิน", `${gateChips(gates)}<p class="verdict-line">${isBest ? `สถานะ ${statusBadge(R.status)} — ${R.status === "ACTIVE" ? (cand?.kept && passed < judged ? `<b class="neg">ไม่ผ่านเกณฑ์รอบล่าสุด (${passed}/${judged})</b> แต่ยังเทรดอยู่เพราะกติกา “คงไว้จนจบไตรมาส” (ตกแค่ด่านหักโชค)` : "ผ่านทุกเกณฑ์ ใช้ส่งแจ้งเตือนจริง") : R.status === "WATCH" ? "ผลบวกแต่ยังพิสูจน์ไม่พอ เฝ้าดูผลจริง ไม่แจ้งเตือน" : "ไม่พบความได้เปรียบ ไม่เทรด"}` : `${cand?.status ? `สถานะของระบบนี้ ${statusBadge(cand.status)} · ` : ""}ผ่าน ${passed}/${judged} เกณฑ์ — ระบบที่ใช้จริงของ ${sym} คือ ${stratLabel(best.strategy)} ${best.tf || ""}`}</p>`)}
  </div>`;
  if (rOos.length) {
    let cum = 0; const pts = (sp.oos.equity || []).map(([t, r]) => ({ time: shiftT(t), value: +(cum += r).toFixed(2) }));
    lineChart($("#oosMini"), [{ data: dedupeTimes(pts), color: "#60a5fa", last: true }]);
  }
  $("#wsFoot").innerHTML = `<span class="fi strat"><b>#${st.no}</b> ${esc(st.name)} <span class="muted">· ${esc(paramText(cand?.current_params || sp?.params))}</span></span>
    ${footItem("สถานะ", statusBadge(cand?.status || R.status))}${footItem("เกณฑ์", `${passed}/${judged}`)}
    ${R.quarter ? `<span class="fi" data-tip="ทุกระบบสอบด้วยข้อมูลถึงวันตัดรอบเดียวกัน ระบบที่ผ่านเทรดด้วยพารามิเตอร์เดิมจนจบไตรมาส">ตัดข้อมูล <b>${fdt(Date.parse(R.quarter.cutoff) / 1000, true).split(",")[0]}</b> · ใช้ถึง <b>${fdt(Date.parse(R.quarter.until) / 1000, true).split(",")[0]}</b></span>` : ""}
    ${cand?.kept ? '<span class="fi warn" data-tip="ACTIVE ตั้งแต่ต้นไตรมาส และตกแค่ด่านหักโชคที่สูงขึ้นเพราะเพิ่มระบบ จึงใช้ต่อจนจบไตรมาส">คงไว้ถึงสิ้นไตรมาส</span>' : ""}
    ${footItem("วิเคราะห์ล่าสุด", res.generated ? ago(res.generated) : "–")}<span class="fi faint">เส้น OOS equity ในข้อ 3 เป็นหน่วย R สะสม</span>`;
}

// ------------------------------------------------------------------ LAB (manual, local server)
function paramInputs(strat, values) {
  return strat.params.map((p) => `<label class="f">${esc(p.label)}<input class="inp num" type="number" data-p="${p.key}" value="${values?.[p.key] ?? p.default}" step="${p.step}" min="${p.min}" max="${p.max}"></label>`).join("");
}

async function renderLabManual(view) {   // local server only: free experiments with your own parameters
  const q = qs();
  const saved = store.get("lab", {});
  const sym = q.symbol || saved.symbol || "USDJPY", tf = q.tf || saved.tf || "H4", sid = q.strategy || saved.strategy || S.strategies[0].id;
  const params = q.params ? JSON.parse(q.params) : saved.symbol === sym && saved.strategy === sid ? saved.params : null;
  const sp = specOf(sym);
  const R = S.settings.research;
  view.innerHTML = `<div class="grid" style="grid-template-columns:minmax(0,360px) minmax(0,1fr);align-items:start" id="labGrid">
    <div class="card"><div class="hd"><h3>ตั้งค่าการทดสอบ</h3></div><div class="bd stack" style="gap:12px">
      <button class="btn primary" id="bAutoAll">🤖 วิเคราะห์ใหม่ทั้งหมดทันที (ทุกสินทรัพย์)</button>
      <button class="btn" id="bAuto">🤖 วิเคราะห์อัตโนมัติเฉพาะสินทรัพย์ที่เลือก</button>
      <div class="hint">ระบบเลือกกลยุทธ์ / TF / พารามิเตอร์ / หน้าต่าง WF / Robustness / Monte Carlo / ความเสี่ยงให้เองสำหรับสินทรัพย์ที่เลือก แล้วอัปเดตระบบเทรด · ด้านล่างสำหรับทดลองเอง</div>
      <div class="fgrid" style="grid-template-columns:1fr 1fr">
        <label class="f">สินทรัพย์<select class="inp" id="lSym">${symGroups().map(({ g, list }) => `<optgroup label="${g.label}">${list.map((x) => `<option ${x.symbol === sym ? "selected" : ""}>${x.symbol}</option>`).join("")}</optgroup>`).join("")}</select></label>
        <label class="f">Timeframe<select class="inp" id="lTf">${["H1", "H4", "D1"].map((t) => `<option ${t === tf ? "selected" : ""}>${t}</option>`).join("")}</select></label>
      </div>
      <label class="f">กลยุทธ์<select class="inp" id="lStrat">${S.strategies.map((s) => `<option value="${s.id}" ${s.id === sid ? "selected" : ""}>${esc(s.name)} — ${esc(s.family)}</option>`).join("")}</select></label>
      <div class="hint" id="lRat"></div>
      <div class="formsec">พารามิเตอร์</div><div class="fgrid" style="grid-template-columns:1fr 1fr" id="lParams"></div>
      <div class="formsec">ต้นทุน (หน่วยราคา) · ความเสี่ยง</div>
      <div class="fgrid" style="grid-template-columns:1fr 1fr 1fr">
        <label class="f">Spread<input class="inp num" type="number" step="any" id="cSpread" value="${sp.spread}"></label>
        <label class="f">Slippage<input class="inp num" type="number" step="any" id="cSlip" value="${sp.slippage}"></label>
        <label class="f">Comm.<input class="inp num" type="number" step="any" id="cComm" value="${sp.commission}"></label>
        <label class="f">Risk %<input class="inp num" type="number" step="0.05" id="lRisk" value="${saved.risk ?? 1}"></label>
        <label class="f">ตั้งแต่<input class="inp" type="date" id="lFrom" value="${saved.from || ""}"></label>
        <label class="f">ถึง<input class="inp" type="date" id="lTo" value="${saved.to || ""}"></label>
      </div>
      <button class="btn primary" id="bBt">▶ Backtest</button>
      <div class="formsec">Walk-Forward</div>
      <div class="fgrid" style="grid-template-columns:1fr 1fr 1fr">
        <label class="f">IS (เดือน)<input class="inp num" type="number" id="wIs" value="${R.is_months}"></label>
        <label class="f">OOS (เดือน)<input class="inp num" type="number" id="wOos" value="${R.oos_months}"></label>
        <label class="f">IS ขั้นต่ำ<input class="inp num" type="number" id="wMin" value="${R.min_is_trades}"></label>
      </div>
      <div class="hint">ใช้ grid ของกลยุทธ์ (<span id="gridSize"></span> ชุด) — เลือกด้วย t-stat ใน IS แล้วเทรดใน OOS</div>
      <button class="btn" id="bWf">⇆ Walk-Forward</button>
      <div class="formsec">Monte Carlo</div>
      <div class="fgrid" style="grid-template-columns:1fr 1fr">
        <label class="f">วิธี<select class="inp" id="mMethod"><option value="bootstrap">Bootstrap</option><option value="shuffle">Shuffle (ลำดับ)</option></select></label>
        <label class="f">จำนวนรอบ<input class="inp num" type="number" id="mSims" value="5000"></label>
        <label class="f">จำนวนไม้/รอบ<input class="inp num" type="number" id="mN" placeholder="เท่าข้อมูล"></label>
        <label class="f">DD ที่รับได้ %<input class="inp num" type="number" id="mDd" value="${S.settings.target_max_dd_pct}"></label>
      </div>
      <button class="btn" id="bMc">🎲 Monte Carlo</button>
      <div class="hint">Monte Carlo ใช้เทรด OOS จาก Walk-Forward (ถ้ามี) — ไม่ใช่ผล backtest ที่ optimize แล้ว เพราะ MC ตรวจ overfitting ไม่ได้</div>
    </div></div>
    <div class="stack" style="min-width:0">
      <div class="tabs" id="labTabs"><button data-t="bt" class="active">Backtest</button><button data-t="wf">Walk-Forward</button><button data-t="mc">Monte Carlo</button></div>
      <div id="labOut"></div>
    </div></div>`;
  if (window.matchMedia("(max-width: 1100px)").matches) $("#labGrid").style.gridTemplateColumns = "1fr";
  const strat = () => S.strategies.find((s) => s.id === $("#lStrat").value);
  const drawParams = (vals) => {
    const s = strat(); $("#lParams").innerHTML = paramInputs(s, vals);
    $("#lRat").textContent = s.rationale; $("#gridSize").textContent = s.grid_size;
  };
  drawParams(params);
  $("#lStrat").onchange = () => drawParams(null);
  $("#lSym").onchange = () => { const x = specOf($("#lSym").value); $("#cSpread").value = x.spread; $("#cSlip").value = x.slippage; $("#cComm").value = x.commission; };
  const form = () => {
    const p = {}; $$("#lParams [data-p]").forEach((i) => p[i.dataset.p] = parseFloat(i.value));
    const f = { symbol: $("#lSym").value, tf: $("#lTf").value, strategy: $("#lStrat").value, params: p, risk_pct: parseFloat($("#lRisk").value) || 1,
      costs: { spread: parseFloat($("#cSpread").value), slippage: parseFloat($("#cSlip").value), commission: parseFloat($("#cComm").value) },
      start: $("#lFrom").value || null, end: $("#lTo").value || null };
    store.set("lab", { symbol: f.symbol, tf: f.tf, strategy: f.strategy, params: p, risk: f.risk_pct, from: f.start, to: f.end });
    return f;
  };
  let tab = store.get("labTab", "bt");
  const show = (t) => {
    tab = t; store.set("labTab", t);
    $$("#labTabs button").forEach((b) => b.classList.toggle("active", b.dataset.t === t));
    S.charts.forEach((c) => { try { c.remove(); } catch { /* removed */ } }); S.charts = [];
    const out = $("#labOut"), r = S.lab[t];
    if (!r) { out.innerHTML = `<div class="card"><div class="empty"><div class="big">${{ bt: "▶", wf: "⇆", mc: "🎲" }[t]}</div>ยังไม่มีผล — ตั้งค่าทางซ้ายแล้วกดปุ่ม</div></div>`; return; }
    ({ bt: showBt, wf: showWf, mc: showMc })[t](out, r);
  };
  $("#labTabs").onclick = (e) => { const b = e.target.closest("[data-t]"); if (b) show(b.dataset.t); };
  const busy = async (btn, fn) => { const t = btn.innerHTML; btn.disabled = true; btn.innerHTML = `<span class="spinner"></span> กำลังคำนวณ…`; try { await fn(); } catch (e) { toast("ผิดพลาด: " + e.message, 4000); } finally { btn.disabled = false; btn.innerHTML = t; } };
  $("#bAutoAll").onclick = (e) => autoRun(e.currentTarget, null);
  $("#bAuto").onclick = (e) => autoRun(e.currentTarget, $("#lSym").value);
  const autoRun = async (btn, sym) => {
    const r = await api("/api/research/run", { method: "POST", body: { symbols: sym ? [sym] : [] } });
    if (!r.started) return toast("มีการวิเคราะห์กำลังรันอยู่");
    btn.disabled = true;
    const tick = async () => {
      const st = await api("/api/research/state");
      if (st.running) { btn.innerHTML = `<span class="spinner"></span> ${esc(st.progress || "กำลังวิเคราะห์…")}`; return setTimeout(tick, 2000); }
      S.symbols = sortSyms(await api("/api/symbols")); toast(`วิเคราะห์ ${sym || "ทุกสินทรัพย์"} เสร็จแล้ว`); go("#/systems");
    };
    tick();
  };
  $("#bBt").onclick = (e) => busy(e.currentTarget, async () => { S.lab.bt = await api("/api/backtest", { method: "POST", body: form() }); show("bt"); });
  $("#bWf").onclick = (e) => busy(e.currentTarget, async () => {
    const f = form();
    S.lab.wf = await api("/api/walkforward", { method: "POST", body: { ...f, is_months: +$("#wIs").value, oos_months: +$("#wOos").value, min_is_trades: +$("#wMin").value, max_hold_h: f.params.max_hold_h } });
    show("wf");
  });
  $("#bMc").onclick = (e) => busy(e.currentTarget, async () => {
    const src = S.lab.wf?.r?.length ? S.lab.wf : S.lab.bt;
    if (!src?.r?.length) throw new Error("รัน Walk-Forward หรือ Backtest ก่อน");
    S.lab.mc = await api("/api/montecarlo", { method: "POST", body: { r: src.r, risk_pct: parseFloat($("#lRisk").value) || 1, sims: +$("#mSims").value, method: $("#mMethod").value, n_trades: +$("#mN").value || null, dd_limit_pct: +$("#mDd").value } });
    S.lab.mc.source = src === S.lab.wf ? "Walk-Forward OOS" : "Backtest (in-sample — ระวังมองโลกดีเกินจริง)";
    show("mc");
  });
  show(tab);
}

function metricKpis(m) {
  return `<div class="kpis">${kpi("Trades", m.trades, m.trades_per_year + "/ปี")}${kpi("Net", fr(m.net_r, 1), "", cls(m.net_r))}${kpi("Expectancy", fr(m.expectancy, 3), m.gross_expectancy !== null && m.gross_expectancy !== undefined ? "ก่อนต้นทุน " + fr(m.gross_expectancy, 3) : "", cls(m.expectancy))}
    ${kpi("t-stat", fnum(m.t_stat, 2), Math.abs(m.t_stat) >= 2 ? "มีนัยสำคัญ" : "ไม่มีนัยสำคัญ", m.t_stat >= 2 ? "pos" : "")}${kpi("Profit factor", fnum(m.pf, 2))}${kpi("Win rate", m.win_rate + "%", `W ${fnum(m.avg_win, 2)}R / L ${fnum(m.avg_loss, 2)}R`)}
    ${kpi("Max DD", fnum(m.max_dd_r, 1) + "R", fnum(m.max_dd_pct, 1) + "% @" + m.risk_pct + "%", "neg")}${kpi("CAGR", fnum(m.cagr_pct, 1) + "%", "MAR " + fnum(m.mar, 2), cls(m.cagr_pct))}${kpi("แพ้ติดกัน", m.max_consec_loss, "สูงสุด")}${kpi("ถือเฉลี่ย", m.avg_bars ?? "–", "แท่ง")}</div>`;
}
function yearlyTable(y) {
  return `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>ปี</th><th class="num">Trades</th><th class="num">Net R</th><th class="num">Win %</th></tr></thead><tbody>${(y || []).map((r) => `<tr><td>${r.year}</td><td class="num">${r.trades}</td><td class="num ${cls(r.net_r)}">${fr(r.net_r, 1)}</td><td class="num">${r.win}</td></tr>`).join("")}</tbody></table></div>`;
}
function tradesTable(tr, digits, fold = false) {
  return `<div class="tbl-wrap tbl-scroll"><table class="tbl"><thead><tr><th>เข้า</th>${fold ? "<th>Fold</th>" : ""}<th>ฝั่ง</th><th class="num">Entry</th><th class="num">SL</th><th class="num">TP</th><th class="num">Exit</th><th>ออก</th><th class="num">แท่ง</th><th class="num">R</th></tr></thead><tbody>
    ${tr.map((t) => `<tr><td>${fdt(t.entry_time, true)}</td>${fold ? `<td>${t.fold + 1}</td>` : ""}<td>${t.dir > 0 ? '<span class="pos">LONG</span>' : '<span class="neg">SHORT</span>'}</td><td class="num">${fpx(t.entry, digits)}</td><td class="num">${fpx(t.sl, digits)}</td><td class="num">${fpx(t.tp, digits)}</td><td class="num">${fpx(t.exit, digits)}</td><td>${reasonTh(t.reason)}</td><td class="num">${t.bars}</td><td class="num ${cls(t.r)}">${fr(t.r)}</td></tr>`).join("")}</tbody></table></div>`;
}

function showBt(out, r) {
  const m = r.metrics, d = specOf(r.symbol).digits;
  out.innerHTML = `<div class="stack">
    ${r.metrics.trades && r.metrics.t_stat < 2 ? `<div class="banner"><span class="ic">⚠️</span><div>นี่คือผล <b>in-sample</b> ที่ใช้พารามิเตอร์ที่คุณเลือกเอง — t-stat ${fnum(m.t_stat, 2)} ${m.t_stat < 2 ? "ยังแยกไม่ออกจากโชค" : ""} ตรวจด้วย Walk-Forward ก่อนเชื่อผลนี้</div></div>` : ""}
    <div class="card"><div class="hd"><h3>${r.symbol} · ${r.tf} · ${esc(stratName(r.strategy))}</h3><div class="right small muted">${fdt(r.from, true).split(",")[0]} → ${fdt(r.to, true).split(",")[0]} (${r.years} ปี)</div></div>
      <div class="bd stack">${metricKpis(m)}
        <div class="row small muted">Long: ${m.long.trades} ไม้ ${fr(m.long.expectancy, 3)} · Short: ${m.short.trades} ไม้ ${fr(m.short.expectancy, 3)} · ออกด้วย ${Object.entries(r.exit_mix).map(([k, v]) => `${k} ${v}`).join(" / ")}</div></div></div>
    <div class="card"><div class="hd"><h3>Equity (ทบต้น risk ${m.risk_pct}%)</h3></div><div class="chart md" id="btEq"></div></div>
    <div class="grid g2" style="grid-template-columns:minmax(0,1fr) minmax(0,2fr)"><div class="card"><div class="hd"><h3>รายปี</h3></div>${yearlyTable(r.yearly)}</div>
      <div class="card"><div class="hd"><h3>รายการเทรด</h3><span class="badge">${r.trades.length}</span></div>${tradesTable(r.trades, d)}</div></div></div>`;
  if (r.equity.length) lineChart($("#btEq"), [{ data: dedupeTimes(r.equity.map((p) => ({ time: shiftT(p.time), value: p.value }))), color: "#60a5fa", last: true }]);
}

function showWf(out, r) {
  const m = r.oos, rb = r.random_benchmark, d = specOf(r.symbol).digits;
  const verdict = m.trades >= 30 && m.t_stat >= 2 && (rb?.percentile ?? 0) >= 90;
  out.innerHTML = `<div class="stack">
    <div class="banner ${verdict ? "info" : ""}"><span class="ic">${verdict ? "✅" : "⚠️"}</span><div>${verdict ? "OOS มีนัยสำคัญและชนะการสุ่มจุดเข้า — ผ่านการคัดกรองเบื้องต้น ตรวจ Monte Carlo ต่อ"
      : `OOS ${m.trades} ไม้, t = ${fnum(m.t_stat, 2)}, ชนะการสุ่ม ${rb ? rb.percentile + "%" : "–"} — <b>ยังไม่มีหลักฐานว่ามี edge</b>`}</div></div>
    <div class="card"><div class="hd"><h3>Out-of-Sample ที่ต่อกัน (${r.oos_years} ปี, ${r.folds.length} folds)</h3><div class="right small muted">IS ${r.is_months}m / OOS ${r.oos_months}m · ${r.n_combos} ชุดพารามิเตอร์</div></div>
      <div class="bd stack">${metricKpis(m)}
      <div class="kpis">${kpi("WF Efficiency", r.wfe ?? "–", "OOS ÷ IS ต่อเดือน", r.wfe >= 0.5 ? "pos" : "neg")}${kpi("Plateau", fnum(r.plateau * 100, 0) + "%", "ชุดพารามิเตอร์ที่เป็นบวก")}${kpi("เปลี่ยนพารามิเตอร์", r.param_switches, `${r.distinct_params} ชุดที่ต่างกัน`)}
        ${rb ? kpi("vs สุ่มจุดเข้า", rb.percentile + "%", `สุ่ม ${rb.sims} รอบ เฉลี่ย ${fr(rb.random_mean, 3)}`, rb.percentile >= 90 ? "pos" : "neg") : ""}${kpi("พารามิเตอร์ล่าสุด", r.current_params ? "พร้อม" : "ไม่มี", r.current_params ? "t IS " + r.current_is_t : "IS ไม่เป็นบวก")}</div>
      ${r.current_params ? `<div class="small mono muted">ใช้ในรอบปัจจุบัน (ล็อกถึงสิ้นรอบ 3 เดือน): ${esc(paramText(r.current_params))}</div>` : ""}</div></div>
    <div class="card"><div class="hd"><h3>OOS Equity</h3></div><div class="chart md" id="wfEq"></div></div>
    <div class="card"><div class="hd"><h3>Folds</h3></div><div class="tbl-wrap tbl-scroll"><table class="tbl"><thead><tr><th>#</th><th>IS</th><th>OOS</th><th>พารามิเตอร์ที่เลือก</th><th class="num">IS n</th><th class="num">IS Exp</th><th class="num">OOS n</th><th class="num">OOS R</th></tr></thead><tbody>
      ${r.folds.map((f, i) => `<tr><td>${i + 1}</td><td class="small">${f.is_start.slice(0, 7)} → ${f.is_end.slice(0, 7)}</td><td class="small">${f.oos_start.slice(0, 7)}${f.partial ? "*" : ""}</td><td class="small mono">${f.params ? esc(paramText(f.params)) : '<span class="faint">ไม่เทรด</span>'}</td><td class="num">${f.is_trades}</td><td class="num">${f.is_exp ?? "–"}</td><td class="num">${f.oos_trades}</td><td class="num ${cls(f.oos_r)}">${fnum(f.oos_r, 2)}</td></tr>`).join("")}</tbody></table></div></div>
    <div class="grid g2" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr)">
      <div class="card"><div class="hd"><h3>ทุกชุดพารามิเตอร์ (ทั้งช่วง)</h3></div><div class="tbl-wrap tbl-scroll"><table class="tbl"><thead><tr><th>พารามิเตอร์</th><th class="num">n</th><th class="num">Exp</th></tr></thead><tbody>
        ${r.combos_full.slice().sort((a, b) => (b.expectancy ?? -9) - (a.expectancy ?? -9)).map((c) => `<tr><td class="small mono">${esc(paramText(c.params))}</td><td class="num">${c.trades}</td><td class="num ${cls(c.expectancy)}">${c.expectancy ?? "–"}</td></tr>`).join("")}</tbody></table></div></div>
      <div class="card"><div class="hd"><h3>OOS รายปี</h3></div>${yearlyTable(r.yearly)}</div></div>
    <div class="card"><div class="hd"><h3>เทรด OOS</h3><span class="badge">${r.trades.length}</span></div>${tradesTable(r.trades, d, true)}</div></div>`;
  if (r.equity.length) lineChart($("#wfEq"), [{ data: dedupeTimes(r.equity.map((p) => ({ time: shiftT(p.time), value: p.value }))), color: "#16c784", last: true }]);
}

function showMc(out, r) {
  if (r.error) { out.innerHTML = `<div class="banner"><span class="ic">⚠️</span><div>${esc(r.error)}</div></div>`; return; }
  const maxH = Math.max(...r.dd_hist, 1);
  out.innerHTML = `<div class="stack">
    <div class="card"><div class="hd"><h3>Monte Carlo · ${r.method} · ${r.sims.toLocaleString()} รอบ × ${r.n_trades} ไม้ · risk ${r.risk_pct}%</h3><div class="right small muted">${esc(r.source || "")}</div></div>
      <div class="bd stack"><div class="kpis">
        ${kpi("ผลตอบแทนกลาง", fnum(r.return_pct["50"], 1) + "%", "", cls(r.return_pct["50"]))}${kpi("แย่ 5%", fnum(r.return_pct["5"], 1) + "%", "", cls(r.return_pct["5"]))}${kpi("ดี 95%", fnum(r.return_pct["95"], 1) + "%")}
        ${kpi("DD กลาง", fnum(r.max_dd_pct["50"], 1) + "%")}${kpi("DD 95%", fnum(r.max_dd_pct["95"], 1) + "%", "", "neg")}${kpi("โอกาสกำไร", r.prob_profit + "%")}
        ${kpi("PF @95%", fnum(r.pf_p5, 2), "", r.pf_p5 >= 1 ? "pos" : "neg")}${kpi(`DD > ${r.dd_limit_pct}%`, r.prob_dd_over_limit + "%", "ความน่าจะเป็น")}${kpi(`Risk of ruin`, r.risk_of_ruin + "%", `DD ≥ ${r.ruin_pct}%`)}
        ${kpi("Risk แนะนำ", r.suggested_risk_pct ? fnum(r.suggested_risk_pct, 2) + "%" : "–", `ให้ DD95 ≈ ${r.dd_limit_pct}%`, "pos")}</div></div></div>
    <div class="card"><div class="hd"><h3>Fan chart — ผลตอบแทนสะสม (%) ตามจำนวนไม้</h3><div class="right legend"><span><i style="background:#16c784"></i>95%</span><span><i style="background:#60a5fa"></i>75%</span><span><i style="background:#e6e9ef"></i>50%</span><span><i style="background:#f5a524"></i>25%</span><span><i style="background:#ef4444"></i>5%</span></div></div><div class="chart md" id="mcFan"></div></div>
    <div class="grid g2"><div class="card"><div class="hd"><h3>การกระจาย Max Drawdown</h3></div><div class="bd"><div class="hist">${r.dd_hist.map((v, i) => `<div class="${r.dd_edges[i] >= r.dd_limit_pct ? "over" : ""}" style="height:${(v / maxH) * 100}%" title="${r.dd_edges[i]}–${r.dd_edges[i + 1]}%: ${v}"></div>`).join("")}</div>
        <div class="row small muted" style="justify-content:space-between"><span>${r.dd_edges[0]}%</span><span>แดง = เกิน ${r.dd_limit_pct}%</span><span>${r.dd_edges[r.dd_edges.length - 1]}%</span></div></div></div>
      <div class="card"><div class="hd"><h3>ระดับความเชื่อมั่น</h3></div><div class="tbl-wrap"><table class="tbl"><thead><tr><th>Conf.</th><th class="num">Return %</th><th class="num">Max DD %</th><th class="num">PF</th><th class="num">Exp R</th></tr></thead><tbody>
        ${r.confidence.map((c) => `<tr><td>${c.confidence}%</td><td class="num ${cls(c.return_pct)}">${fnum(c.return_pct, 1)}</td><td class="num">${fnum(c.max_dd_pct, 1)}</td><td class="num">${fnum(c.pf, 2)}</td><td class="num ${cls(c.expectancy)}">${fnum(c.expectancy, 3)}</td></tr>`).join("")}</tbody></table></div></div></div></div>`;
  // fan uses a synthetic daily time axis: one point per sampled trade step
  const base = Math.floor(Date.UTC(2000, 0, 1) / 1000);
  const mk = (arr) => arr.map((v, i) => ({ time: base + i * 86400, value: v }));
  lineChart($("#mcFan"), [
    { data: mk(r.fan["95"]), color: "#16c784", width: 1 }, { data: mk(r.fan["75"]), color: "#60a5fa", width: 1 },
    { data: mk(r.fan["50"]), color: cssVar("--text"), width: 2, last: true }, { data: mk(r.fan["25"]), color: "#f5a524", width: 1 },
    { data: mk(r.fan["5"]), color: "#ef4444", width: 1 }], { pct: true });
  S.charts[S.charts.length - 1].applyOptions({ timeScale: { tickMarkFormatter: (t) => String(Math.round(((t - base) / 86400) * r.fan_step)) } });
}

// ------------------------------------------------------------------ SETTINGS (personal, this device): display · live prices · backup · about
function personalSettingsHtml() {
  const P = prefs.get(), th = store.get("theme", "dark");
  const map = { ...LIVE_DEFAULT, ...(P.liveMap || {}) };
  return `<div class="set-grid">
    <div class="card"><div class="hd"><h3><span class="ic">🎨</span>การแสดงผล<span class="desc">เฉพาะเครื่องนี้</span></h3></div><div class="bd stack" style="gap:14px">
      <div class="fgrid" style="grid-template-columns:1fr 1fr">
        <label class="f">ธีม<select class="inp" id="dTheme"><option value="dark" ${th === "dark" ? "selected" : ""}>มืด</option><option value="light" ${th === "light" ? "selected" : ""}>สว่าง</option></select></label>
        <label class="f">หน้าแรกเมื่อเปิดแอป<select class="inp" id="dHome">${NAV_ORDER.map((k) => `<option value="${k}" ${P.home === k ? "selected" : ""}>${ROUTES[k].title}</option>`).join("")}</select></label>
        <label class="f">สีแท่งเทียน<select class="inp" id="dCandle">${Object.entries(CANDLE).map(([k, v]) => `<option value="${k}" ${P.candle === k ? "selected" : ""}>${v.name}</option>`).join("")}</select></label>
        <label class="f">จำนวนแท่งที่เห็นเมื่อเปิดกราฟ<input class="inp num" type="number" min="40" max="1000" step="10" id="dBars" value="${P.bars}"></label>
      </div>
      <div class="row"><span class="candle-prev" id="dPrev"></span><span class="hint">แท่งล่าสุดจะอยู่กลางกราฟเสมอ · ซูมด้วยสองนิ้วได้</span></div>
    </div></div>
    <div class="card"><div class="hd"><h3><span class="ic">⚡</span>ราคาสด (ขยับทุก 1 วินาที)<span class="desc">ใช้แสดงแท่งที่กำลังก่อตัวบนกราฟเท่านั้น — ระบบยังตัดสินจากแท่งที่ปิดแล้ว</span></h3></div><div class="bd stack" style="gap:12px">
      <label class="check"><input type="checkbox" id="lOn" ${P.live ? "checked" : ""}> เปิดราคาสดบนหน้ากราฟ</label>
      <div class="hint"><b>BTC / ETH:</b> ใช้ Binance ฟรี ไม่ต้องตั้งค่า · <b>FX / ทอง / ดัชนี:</b> ใส่ API key ฟรีจาก <a href="https://finnhub.io/register" target="_blank" rel="noopener">finnhub.io</a> (สมัครด้วยอีเมล → Dashboard → API Key)</div>
      <label class="f">Finnhub API key <span class="muted small">(ราคาวิ่งทุกวินาที)</span><input class="inp mono" id="lKey" value="${esc(P.finnhub)}" placeholder="วาง API key ที่นี่" autocomplete="off"></label>
      <label class="f">Twelve Data API key <span class="muted small">(ไม่บังคับ: แท่งเทียนจริงย้อนหลัง ~40 วัน ของ FX / ทอง)</span><input class="inp mono" id="lKey2" value="${esc(P.twelve)}" placeholder="วาง API key ที่นี่" autocomplete="off"></label>
      <div class="hint">ไม่มี Twelve Data key: แท่งเก่าของ FX / ทอง / ดัชนีมาจาก Yahoo (ทองและดัชนีเป็นสัญญาฟิวเจอร์ส) รูปร่างจึงต่างจาก MT5 เล็กน้อย · สมัครฟรีที่ <a href="https://twelvedata.com/register" target="_blank" rel="noopener">twelvedata.com</a> → API Keys · BTC / ETH ใช้แท่งจริงจาก Binance อัตโนมัติ</div>
      <details><summary class="small muted">ชื่อสัญลักษณ์ราคาสด (ขั้นสูง)</summary><div class="fgrid" style="grid-template-columns:repeat(3,minmax(0,1fr));margin-top:8px">
        ${Object.keys(LIVE_DEFAULT).map((k) => `<label class="f">${k}<input class="inp mono" data-live="${k}" value="${esc(map[k])}"></label>`).join("")}</div></details>
      <div class="hint">ราคาสดมาจากผู้ให้บริการคนละรายกับโบรก (ต่างกันเล็กน้อย โดยเฉพาะทองและดัชนี) — ราคาเข้าออเดอร์จริงให้ดูใน MT5</div>
    </div></div>
    <div class="card" id="syncCard"><div class="hd"><h3><span class="ic">🔗</span>ซิงค์ทุกเครื่อง<span class="desc">API key ทุนในบัญชี ความเสี่ยง และการแสดงผล ใช้ร่วมกันทุกเครื่อง (เข้ารหัสก่อนเก็บบนคลาวด์)</span></h3></div><div class="bd stack" style="gap:12px" id="syncBody"></div></div>
    <div class="card"><div class="hd"><h3><span class="ic">💾</span>สำรอง / ย้ายการตั้งค่า<span class="desc">ใช้ย้ายค่าที่ตั้งไว้ไปเครื่องอื่น (เช่น จาก iPad ไปมือถือ)</span></h3></div><div class="bd stack" style="gap:10px">
      <div class="row"><button class="btn sm" id="bkCopy">📋 คัดลอกรหัสการตั้งค่า</button><button class="btn sm" id="bkLoad">📥 นำเข้า</button><button class="btn sm ghost" id="bkReset">↺ คืนค่าเริ่มต้น</button></div>
      <textarea class="inp" id="bkText" rows="3" placeholder="วางรหัสการตั้งค่าจากอีกเครื่องที่นี่ แล้วกด นำเข้า"></textarea>
    </div></div>
    <div class="card"><div class="hd"><h3><span class="ic">ℹ️</span>เกี่ยวกับระบบ</h3></div><div class="bd"><div class="kv2" id="aboutBox"></div></div></div>
  </div>`;
}
// fund-style basket test: one system on all 8 symbols, judged on the pooled monthly OOS result
// losing streaks (user, 2 Nov 2026: know in advance how many losses in a row are normal, so a streak never scares you
// into stopping a system at the worst moment): worst run in the OOS history + the run a year of trading reaches 95% of the time
// step 8 of the Lab (user, 3 Nov 2026): is the edge just the market's drift, and does it survive swap?
function extraHtml(c, cmp, key, dq) {
  if (!c) return '<div class="hint">–</div>';
  const sd = c.sides || {}, bh = c.bh, L = sd.long || {}, Sh = sd.short || {};
  const side = (x, name) => `${kpi(name, x.n ? fr(x.exp, 3) : "–", x.n ? `${x.n} ไม้ · รวม ${fnum(x.net, 1)}R` : "ไม่มีไม้", cls(x.exp))}`;
  let note = "";
  if (bh && L.n && Sh.n != null) {
    if (bh.pct > 0 && (L.net || 0) > 0 && (Sh.net || 0) <= 0) note = "กำไรมาจากฝั่ง Buy ในช่วงที่ตลาดขึ้น — อาจแค่ตามตลาด ไม่ใช่ฝีมือระบบ (การเทียบสุ่มจุดเข้าในข้อ 5 ช่วยตรวจเรื่องนี้)";
    else if (bh.pct < 0 && (Sh.net || 0) > 0 && (L.net || 0) <= 0) note = "กำไรมาจากฝั่ง Sell ในช่วงที่ตลาดลง — อาจแค่ตามตลาด";
    else if ((L.net || 0) > 0 && (Sh.net || 0) > 0) note = "ทำกำไรได้ทั้งสองฝั่ง — ไม่ได้พึ่งทิศทางตลาดอย่างเดียว";
  }
  const x = (cmp && cmp.candidates || {})[key];
  const swap = !cmp || !cmp.generated ? '<div class="hint">ยังไม่มีรอบทดสอบแบบหัก swap</div>' : !x ? '<div class="hint">ระบบนี้ไม่มีผลเด่นในทั้งสองรอบ</div>'
    : `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>รอบ</th><th>สถานะ</th><th class="num">t รายเดือน</th><th class="num">Exp (R)</th></tr></thead><tbody>
      ${[["ไม่หัก swap (ใช้จริง)", x.free], ["หัก swap (เทียบ)", x.swap]].map(([n, v]) => `<tr><td>${n}</td><td>${v[0] ? statusBadge(v[0]) : "–"}</td><td class="num">${fnum(v[1], 2)}</td><td class="num ${cls(v[2])}">${v[2] != null ? fnum(v[2], 3) : "–"}</td></tr>`).join("")}
      </tbody></table></div>${x.free[0] === "ACTIVE" && x.swap[0] !== "ACTIVE" ? '<div class="hint neg">ผ่านเพราะบัญชีไม่มี swap — ถ้าย้ายไปบัญชีที่มี swap ระบบนี้จะไม่ผ่าน</div>' : ""}`;
  const poor = dq && dq.poor_years && dq.poor_years.length ? `<div class="hint neg">ข้อมูลราคาปี ${dq.poor_years.join(", ")} มีช่วงขาดหรือราคากระโดดผิดปกติ</div>` : "";
  return `<div class="kpis">${side(L, "ฝั่ง Buy")}${side(Sh, "ฝั่ง Sell")}${bh ? kpi("ซื้อแล้วถือ ช่วงเดียวกัน", (bh.pct > 0 ? "+" : "") + fnum(bh.pct, 1) + "%", bh.pct_year != null ? `${bh.pct_year > 0 ? "+" : ""}${fnum(bh.pct_year, 1)}%/ปี` : "", cls(bh.pct)) : ""}</div>
    ${note ? `<div class="hint">${note}</div>` : ""}<div style="margin-top:10px"><b class="small">ถ้าถูกหัก swap</b></div>${swap}${poor}`;
}
function streakHtml(r, perYear) {
  if (!r || r.length < 20) return "";
  let worst = 0, cur = 0;
  for (const x of r) { cur = x < 0 ? cur + 1 : 0; worst = Math.max(worst, cur); }
  const p = r.filter((x) => x < 0).length / r.length, n = Math.max(10, Math.round(perYear || 20)), runs = [];
  for (let k = 0; k < 2000; k++) { let c = 0, w = 0; for (let i = 0; i < n; i++) { c = Math.random() < p ? c + 1 : 0; if (c > w) w = c; } runs.push(w); }
  runs.sort((a, b) => a - b);
  const typ = runs[Math.floor(runs.length * 0.5)], bad = runs[Math.floor(runs.length * 0.95)];
  return `<div class="kpis" style="margin-top:10px">${kpi("แพ้ติดกันมากสุด (อดีต)", worst + " ไม้", `จาก ${r.length} ไม้ OOS`, "neg")}${kpi("แพ้ติดกันใน 1 ปี (ปกติ)", typ + " ไม้", `${n} ไม้/ปี · แพ้ ${Math.round(p * 100)}%`)}${kpi("แพ้ติดกันใน 1 ปี (แย่ 5%)", bad + " ไม้", "เจอได้โดยระบบไม่ได้เสีย", "neg")}</div>
    <div class="hint">ถ้าแพ้ติดกันไม่เกินตัวเลขนี้ = ยังปกติของระบบ อย่าหยุดเพราะใจ · ระบบจะหยุดเองเมื่อขาดทุนสะสมเกิน ${S.settings?.kill_dd_mult ?? 2} เท่าของที่แย่ที่สุดในการทดสอบ</div>`;
}
function portfolioHtml(P) {
  if (!P?.candidates?.length) return "";
  const top = P.candidates.filter((c) => c.status !== "OFF").slice(0, 15);
  const rows = (top.length ? top : P.candidates.slice(0, 8)).map((c) => { const st = stratById(c.strategy);
    return `<tr class="click" data-go-sys="${c.strategy}|${c.tf}"><td>${statusBadge(c.status)}${c.kept ? ' <span class="badge ghost">คงไว้</span>' : ""}</td>
      <td>${st ? `<b>#${st.no}</b> ` : ""}${esc(c.name)}</td><td>${c.tf}</td><td class="num">${c.months}</td><td class="num">${c.trades}</td>
      <td class="num ${cls(c.exp)}">${fr(c.exp, 3)}</td><td class="num">${fnum(c.t_month, 2)} / ${fnum(P.t_needed, 2)}</td>
      <td class="num">${Math.round(c.breadth * 100)}%</td><td class="num neg">${fnum(c.max_dd_r, 1)}R</td></tr>`; }).join("");
  return `<div class="pane-hd" style="margin-top:18px"><b>ทดสอบแบบพอร์ต (แบบกองทุน)</b><span class="muted small">หนึ่งระบบเทรดทุกสินทรัพย์พร้อมกัน ตัดสินจากผลรวมรายเดือน · ผู้สมัคร ${P.n_candidates} · ต้องได้ t ≥ ${fnum(P.t_needed, 2)}</span></div>
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th>สถานะ</th><th>ระบบ</th><th>TF</th><th class="num">เดือน</th><th class="num">ไม้</th><th class="num">Exp/ไม้</th><th class="num">t เดือน / ต้องการ</th><th class="num">สินทรัพย์บวก</th><th class="num">Max DD</th></tr></thead>
    <tbody>${rows}</tbody></table></div>
    <div class="hint pad-x">ระบบพอร์ตที่ ACTIVE จะเทรดทุกสินทรัพย์ (★ บนกราฟ) · เสี่ยง ${S.settings?.portfolio_risk_pct ?? 0.5}%/ไม้ และยังจำกัดรวมต่อสินทรัพย์ ${S.settings?.max_symbol_risk_pct ?? 1}% · กดแถวเพื่อดูผลของระบบนั้นบนสินทรัพย์ที่เลือกอยู่</div>`;
}
function renderSyncCard() {
  const box = $("#syncBody"); if (!box) return;
  const dv = Sync.dev();
  if (!dv.pass) {
    box.innerHTML = `<div class="hint">ใส่ <b>รหัสซิงค์</b> ครั้งเดียวต่อเครื่อง แล้ว API key และการตั้งค่าจะมาเองทุกครั้งที่เปิดแอป</div>
      <div class="row"><input class="inp mono" id="syPass" placeholder="เช่น abcd-efgh-jkmn-pqrs" autocomplete="off" autocapitalize="off" spellcheck="false" style="flex:1"><button class="btn sm primary" id="syLink">เชื่อมเครื่องนี้</button></div>`;
    $("#syLink").onclick = async (e) => {
      e.currentTarget.disabled = true; e.currentTarget.textContent = "กำลังถอดรหัส…";
      try { await Sync.link($("#syPass").value); toast("เชื่อมแล้ว — โหลดการตั้งค่าจากคลาวด์", 2000); navigate(); }
      catch (err) { toast(err.message, 3500); renderSyncCard(); }
    };
    return;
  }
  box.innerHTML = `<div class="row"><span class="live-dot">เชื่อมแล้ว</span><span class="muted small">ข้อมูลล่าสุด ${dv.applied ? fdt(dv.applied, true) : "–"}${dv.error ? ` · <span class="neg">${esc(dv.error)}</span>` : ""}</span></div>
    <div class="hint">${dv.token ? "เปลี่ยนค่าในเครื่องไหนก็ได้ ระบบจะส่งไปเครื่องอื่นให้อัตโนมัติ (เครื่องอื่นได้รับเมื่อเปิดแอปครั้งถัดไป)"
      : "เครื่องนี้ <b>รับ</b> การตั้งค่าได้แล้ว · ถ้าอยากให้แก้จากเครื่องไหนก็ได้แล้วส่งไปทุกเครื่อง ใส่ GitHub token ด้านล่าง (ทำครั้งเดียว ทุกเครื่องจะได้ token ไปด้วย)"}</div>
    <div class="row"><button class="btn sm" id="syPull">⬇️ ดึงล่าสุด</button>${dv.token ? '<button class="btn sm" id="syPush">⬆️ ส่งค่าของเครื่องนี้</button>' : ""}<button class="btn sm ghost" id="syOff">ยกเลิกการเชื่อม</button></div>
    <details><summary class="small muted">GitHub token (สำหรับส่งการเปลี่ยนแปลง)</summary><div class="stack" style="gap:8px;margin-top:8px">
      <div class="hint">GitHub → Settings → Developer settings → Fine-grained tokens → Generate · Repository access: เฉพาะ <b>${esc(Sync.repo())}</b> · Permissions → Contents: <b>Read and write</b></div>
      <div class="row"><input class="inp mono" id="syTok" value="${esc(dv.token || "")}" placeholder="github_pat_…" autocomplete="off" style="flex:1"><button class="btn sm primary" id="sySaveTok">บันทึกและส่ง</button></div></div></details>`;
  $("#syPull").onclick = async () => { Sync.setDev({ applied: "" }); const ch = await Sync.pull(); toast(ch ? "อัปเดตจากคลาวด์แล้ว" : (Sync.dev().error || "เป็นค่าล่าสุดอยู่แล้ว"), 2000); navigate(); };
  if ($("#syPush")) $("#syPush").onclick = () => Sync.push().then(() => { toast("ส่งแล้ว", 1400); renderSyncCard(); }).catch((e) => toast(e.message, 4000));
  $("#syOff").onclick = () => { if (confirm("ยกเลิกการเชื่อมเครื่องนี้? (ค่าที่ตั้งไว้ในเครื่องยังอยู่)")) { store.set("sync", {}); renderSyncCard(); } };
  $("#sySaveTok").onclick = () => { Sync.setDev({ token: $("#syTok").value.trim() }); Sync.push().then(() => { toast("บันทึก token และส่งการตั้งค่าแล้ว", 2000); renderSyncCard(); }).catch((e) => toast(e.message, 5000)); };
}
function bindPersonalSettings(view) {
  const save = () => {
    const liveMap = Object.fromEntries($$("[data-live]", view).map((i) => [i.dataset.live, i.value.trim()]).filter(([k, v]) => v && v !== LIVE_DEFAULT[k]));
    prefs.set({ home: $("#dHome").value, candle: $("#dCandle").value, bars: Math.min(1000, Math.max(40, +$("#dBars").value || 220)),
      live: $("#lOn").checked, finnhub: $("#lKey").value.trim(), twelve: $("#lKey2").value.trim(), liveMap });
    S.recent = {};
    const t = $("#dTheme").value; if (t !== store.get("theme", "dark")) { store.set("theme", t); document.documentElement.dataset.theme = t; }
    prev(); toast("บันทึกแล้ว (เครื่องนี้)", 1400);
  };
  const prev = () => { const c = CANDLE[$("#dCandle").value] || CANDLE.classic; $("#dPrev").innerHTML = [1, 0, 1, 1, 0, 1].map((u, i) => `<i style="background:${u ? c.up : c.down};height:${14 + (i * 7) % 16}px"></i>`).join(""); };
  $$("#dTheme, #dHome, #dCandle, #dBars, #lOn, #lKey, #lKey2, [data-live]", view).forEach((i) => i.addEventListener("change", save));
  prev();
  const KEYS = ["prefs", "personal", "theme", "sel", "labSel", "calc", "chartOverlay"];
  $("#bkCopy").onclick = async () => {
    const dump = Object.fromEntries(KEYS.map((k) => [k, store.get(k, null)]).filter(([, v]) => v !== null));
    const code = btoa(unescape(encodeURIComponent(JSON.stringify(dump))));
    $("#bkText").value = code;
    try { await navigator.clipboard.writeText(code); toast("คัดลอกรหัสแล้ว — นำไปวางในอีกเครื่อง"); } catch { toast("คัดลอกจากกล่องข้อความด้านล่าง"); }
  };
  $("#bkLoad").onclick = () => {
    try { const d = JSON.parse(decodeURIComponent(escape(atob($("#bkText").value.trim())))); Object.entries(d).forEach(([k, v]) => KEYS.includes(k) && store.set(k, v)); toast("นำเข้าแล้ว"); setTimeout(() => location.reload(), 600); }
    catch { toast("รหัสไม่ถูกต้อง"); }
  };
  renderSyncCard();
  $("#bkReset").onclick = () => { if (!confirm("คืนค่าการตั้งค่าในเครื่องนี้ทั้งหมด?")) return; KEYS.forEach((k) => { try { localStorage.removeItem("tl." + k); } catch { /* */ } }); location.reload(); };
  const st = S.status || {};
  const rows = [["เวอร์ชันเว็บ", STATIC?.version || "เซิร์ฟเวอร์บนคอม"], ["สร้างเว็บล่าสุด", STATIC?.built ? fdt(STATIC.built, true) : "–"],
    ["สแกนสัญญาณล่าสุด", st.scanner?.last_scan ? `${fdt(st.scanner.last_scan, true)} (${ago(st.scanner.last_scan)})` : "–"],
    ["วิเคราะห์ระบบล่าสุด", st.research_generated ? `${fdt(st.research_generated, true)} (${ago(st.research_generated)})` : "–"],
    ["ระบบในคลัง", `${S.strategies.length} ระบบ × H1 / H4 / D1`],
    ["ราคา", "ย้อนหลัง: MT5 ของโบรก (FX ทอง ดัชนี) · ใหม่ทุกชั่วโมง: Yahoo + Binance · ราคาสด: Binance / Finnhub · แท่งจริงล่าสุด: Binance / Twelve Data"]];
  $("#aboutBox").innerHTML = rows.map(([k, v]) => `<span class="k">${k}</span><span>${esc(v)}</span>`).join("");
}

// ------------------------------------------------------------------ SETTINGS
async function renderSettingsStatic(view) {
  const s = await api("/api/settings"); S.settings = s;
  const P = local.get(), G = s.research.gates;
  view.innerHTML = `<div class="page narrow">
    <div class="set-grid">
      <div class="card"><div class="hd"><h3><span class="ic">💼</span>บัญชีของคุณ<span class="desc">บันทึกในเครื่องนี้ (มือถือ / iPad / คอม ตั้งแยกกัน)</span></h3></div><div class="bd stack" style="gap:12px">
        <div class="fgrid" style="grid-template-columns:1fr 1fr">
          <label class="f">ทุนในบัญชี (USD)<input class="inp num" type="number" id="pBal" value="${P.account_balance}"></label>
          <label class="f">ความเสี่ยงต่อไม้ (% ของทุน)<input class="inp num" type="number" step="0.05" id="pRisk" value="${P.risk_pct || s.risk_per_trade_pct || ""}"></label>
        </div>
        <div class="hint">Lot บนการ์ดสัญญาณและบนกราฟคำนวณให้อัตโนมัติจากค่านี้ — ตอนนี้ ${fnum(P.account_balance, 0)} USD × ${P.risk_pct || s.risk_per_trade_pct}% = เสี่ยงไม้ละ <b>$${fnum(P.account_balance * (P.risk_pct || s.risk_per_trade_pct) / 100, 2)}</b> (ถ้าชน SL)
          · ข้อความแจ้งเตือนบนมือถือใช้ทุน ${fnum(STATIC.account_balance || 5000, 0)} USD และ ${s.risk_per_trade_pct}% ตามที่ตั้งไว้บนคลาวด์</div>
        <div class="formsec">ชื่อสัญลักษณ์ใน MT5 ของโบรกเกอร์คุณ</div>
        <div class="fgrid symmap">${S.symbols.map((x) => `<label class="f">${x.symbol}<input class="inp" data-map="${x.symbol}" value="${esc(P.mt5_symbol_map[x.symbol] || s.mt5_symbol_map[x.symbol] || x.symbol)}"></label>`).join("")}</div>
        <div class="row" style="justify-content:flex-end"><button class="btn primary" id="pSave">💾 บันทึก</button></div>
      </div></div>
      <div class="card"><div class="hd"><h3><span class="ic">🔔</span>การแจ้งเตือนบนมือถือ<span class="desc">ผ่านแอป ntfy · ส่งจากคลาวด์ทุกชั่วโมง</span></h3></div><div class="bd md">${md(`
- คลาวด์ (GitHub Actions) สแกนทุกชั่วโมง แล้วส่งแจ้งเตือนผ่านแอป **ntfy** เมื่อระบบ **ACTIVE**${s.alerts.watch ? " และ WATCH" : ""} มีสัญญาณ
- ติดตั้งแอป **ntfy** → กด **+** → Subscribe topic ที่ตั้งไว้ใน GitHub secret \`NTFY_TOPIC\`
- topic ไม่แสดงบนเว็บนี้ เพื่อไม่ให้คนอื่นส่งแจ้งเตือนปลอมเข้ามือถือคุณได้
- **ทดสอบเสียงแจ้งเตือน:** GitHub → Actions → TradingLab → **Run workflow** → เลือก \`test\` → Run
- ถ้าเด้งแต่ไม่มีเสียง: ตรวจ การตั้งค่า → การแจ้งเตือน → ntfy → เสียง, สวิตช์ปิดเสียง และโหมดโฟกัส`)}</div></div>
    </div>
    ${personalSettingsHtml()}
    <div class="card"><div class="hd"><h3><span class="ic">🤖</span>การวิเคราะห์อัตโนมัติ<span class="desc">ระบบจัดการเองทั้งหมด — ดูอย่างเดียว</span></h3><div class="right"><a class="btn sm" href="${actionsUrl()}" target="_blank" rel="noopener">GitHub Actions ↗</a></div></div><div class="bd stack">
      <div class="kpis">${kpi("วิเคราะห์ใหม่", "ทุกเสาร์", "09:30 น. + ปุ่มเดียว")}${kpi("Walk-Forward", `${s.research.is_months}/${s.research.oos_months} m`, "+ ทดสอบอีก 4 หน้าต่าง")}${kpi("TF", s.research.timeframes.join(" / "))}${kpi("ปิดออเดอร์", "TP·SL·กลับทิศ", "ไม่จำกัดเวลาถือ")}${kpi("DD เป้าหมาย", s.target_max_dd_pct + "%", "MC 95%")}</div>
      <div class="formsec">เกณฑ์ ACTIVE (ห้ามลดเพื่อให้มีสัญญาณ)</div>
      <div class="gates">${[["OOS ไม้", G.min_oos_trades], ["Expectancy", G.min_expectancy + "R"], ["t-stat", G.min_t], ["MC PF@95%", G.min_mc_pf_p5], ["ชนะสุ่ม", G.min_random_pct + "%"], ["Plateau", G.min_plateau], ["Robustness", G.min_robust_score]].map(([k, v]) => `<span class="gate p">${k} ≥ ${v}</span>`).join("")}</div>
      ${s.force_off?.length ? `<div class="hint">ปิดโดย AI/ผู้ใช้: ${s.force_off.join(", ")}</div>` : ""}
    </div></div></div>`;
  bindPersonalSettings(view);
  $("#pSave").onclick = () => {
    const risk = parseFloat($("#pRisk").value);
    local.set({ account_balance: parseFloat($("#pBal").value) || 5000, risk_pct: risk > 0 ? risk : null,
      mt5_symbol_map: Object.fromEntries($$("[data-map]", view).map((i) => [i.dataset.map, i.value.trim()])) });
    toast("บันทึกในเครื่องนี้แล้ว"); navigate();
  };
}

async function renderSettings(view) {
  if (STATIC) return renderSettingsStatic(view);
  const s = await api("/api/settings"); S.settings = s;
  const R = s.research, G = R.gates;
  const ov = s.symbol_overrides || {};
  view.innerHTML = `<div class="page narrow" id="setForm">
    <div class="grid g2" style="align-items:start">
      <div class="card"><div class="hd"><h3>บัญชีและความเสี่ยง</h3></div><div class="bd fgrid" style="grid-template-columns:1fr 1fr">
        <label class="f">ทุนในบัญชี (USD)<input class="inp num" type="number" data-k="account_balance" value="${s.account_balance}"></label>
        <label class="f">ความเสี่ยงต่อไม้ % (คงที่ทุกสัญญาณ · 0 = ตาม Monte Carlo)<input class="inp num" type="number" step="0.05" data-k="risk_per_trade_pct" value="${s.risk_per_trade_pct ?? 0}"></label>
        <label class="f">Risk ตั้งต้น % (ระบบ WATCH ใช้ครึ่งหนึ่ง)<input class="inp num" type="number" step="0.05" data-k="default_risk_pct" value="${s.default_risk_pct}"></label>
        <label class="f">Risk สูงสุด % ต่อไม้<input class="inp num" type="number" step="0.05" data-k="max_risk_pct" value="${s.max_risk_pct}"></label>
        <label class="f">DD เป้าหมาย % (MC 95%)<input class="inp num" type="number" data-k="target_max_dd_pct" value="${s.target_max_dd_pct}"></label>
      </div></div>
      <div class="card"><div class="hd"><h3>การแจ้งเตือนบนมือถือ</h3><div class="right"><button class="btn sm" id="testNotify">ส่งทดสอบ</button></div></div><div class="bd stack" style="gap:12px">
        <div class="hint">1) ติดตั้งแอป <b>ntfy</b> (iOS/Android) 2) กด + แล้ว Subscribe topic ด้านล่าง 3) กด “ส่งทดสอบ”</div>
        <div class="fgrid" style="grid-template-columns:2fr 1fr"><label class="f">ntfy topic (ตั้งให้เดายาก)<input class="inp mono" data-k="notify.ntfy_topic" value="${esc(s.notify.ntfy_topic)}"></label>
          <label class="f">ntfy server<input class="inp" data-k="notify.ntfy_server" value="${esc(s.notify.ntfy_server)}"></label></div>
        <div class="fgrid" style="grid-template-columns:2fr 1fr"><label class="f">Telegram bot token (ไม่บังคับ)<input class="inp mono" type="password" data-k="notify.telegram_token" value="${esc(s.notify.telegram_token)}"></label>
          <label class="f">Telegram chat id<input class="inp mono" data-k="notify.telegram_chat_id" value="${esc(s.notify.telegram_chat_id)}"></label></div>
        <div class="row"><label class="check"><input type="checkbox" data-k="alerts.active" ${s.alerts.active ? "checked" : ""}> แจ้งเตือนระบบ ACTIVE</label>
          <label class="check"><input type="checkbox" data-k="alerts.watch" ${s.alerts.watch ? "checked" : ""}> แจ้งเตือนระบบ WATCH ด้วย (forward test)</label></div>
        <label class="f">URL ของเว็บนี้สำหรับกดจากการแจ้งเตือน<input class="inp" data-k="public_url" placeholder="http://${location.host}" value="${esc(s.public_url)}"></label>
      </div></div>
    </div>
    <div class="grid g2" style="align-items:start">
      <div class="card"><div class="hd"><h3>การวิจัยอัตโนมัติ</h3></div><div class="bd stack" style="gap:12px">
        <div class="fgrid" style="grid-template-columns:1fr 1fr 1fr">
          <label class="f">รอบอัปเดตระบบ<select class="inp" data-k="research.schedule">${["daily", "weekly", "monthly", "off"].map((x) => `<option ${R.schedule === x ? "selected" : ""} value="${x}">${{ daily: "ทุกวัน", weekly: "ทุกสัปดาห์ (เสาร์)", monthly: "ทุกเดือน", off: "ปิด" }[x]}</option>`).join("")}</select></label>
          <label class="f">เวลา (ชม. ไทย)<input class="inp num" type="number" data-k="research.schedule_hour_bkk" value="${R.schedule_hour_bkk}"></label>
          <label class="f">IS (เดือน)<input class="inp num" type="number" data-k="research.is_months" value="${R.is_months}"></label>
          <label class="f">OOS (เดือน)<input class="inp num" type="number" data-k="research.oos_months" value="${R.oos_months}"></label>
          <label class="f">IS ขั้นต่ำ (ไม้)<input class="inp num" type="number" data-k="research.min_is_trades" value="${R.min_is_trades}"></label>
        </div>
        <div class="row">${["H1", "H4", "D1"].map((t) => `<label class="check"><input type="checkbox" data-tf="${t}" ${R.timeframes.includes(t) ? "checked" : ""}> ${t}</label>`).join("")}</div>
        <div class="formsec">เกณฑ์ ACTIVE</div>
        <div class="fgrid" style="grid-template-columns:1fr 1fr 1fr">
          <label class="f">OOS ไม้ขั้นต่ำ<input class="inp num" type="number" data-k="research.gates.min_oos_trades" value="${G.min_oos_trades}"></label>
          <label class="f">Expectancy ≥ (R)<input class="inp num" type="number" step="0.01" data-k="research.gates.min_expectancy" value="${G.min_expectancy}"></label>
          <label class="f">t-stat ≥<input class="inp num" type="number" step="0.1" data-k="research.gates.min_t" value="${G.min_t}"></label>
          <label class="f">MC PF@95% ≥<input class="inp num" type="number" step="0.05" data-k="research.gates.min_mc_pf_p5" value="${G.min_mc_pf_p5}"></label>
          <label class="f">ชนะสุ่ม ≥ (%)<input class="inp num" type="number" data-k="research.gates.min_random_pct" value="${G.min_random_pct}"></label>
          <label class="f">Plateau ≥<input class="inp num" type="number" step="0.05" data-k="research.gates.min_plateau" value="${G.min_plateau}"></label>
          <label class="f">Robustness ≥<input class="inp num" type="number" step="5" data-k="research.gates.min_robust_score" value="${G.min_robust_score ?? 60}"></label>
        </div>
        <div class="hint">อย่าลดเกณฑ์เพื่อให้มีสัญญาณ — นั่นคือการหลอกตัวเองแบบเดียวกับการ optimize เกินจริง</div>
      </div></div>
      <div class="card"><div class="hd"><h3>แหล่งข้อมูลและ MT5</h3></div><div class="bd stack" style="gap:12px">
        <div class="fgrid" style="grid-template-columns:1fr 2fr">
          <label class="f">แหล่งข้อมูล<select class="inp" data-k="data_source">${["auto", "mt5", "free"].map((x) => `<option ${s.data_source === x ? "selected" : ""} value="${x}">${{ auto: "อัตโนมัติ (MT5 ถ้าเปิดอยู่)", mt5: "MT5 เท่านั้น", free: "ฟรี (Yahoo/Binance)" }[x]}</option>`).join("")}</select></label>
          <label class="f">MT5 terminal64.exe<input class="inp mono small" data-k="mt5_path" value="${esc(s.mt5_path)}"></label></div>
        <div class="hint">สถานะ MT5: ${S.status?.mt5?.connected ? `<span class="pos">เชื่อมต่อ ${esc(S.status.mt5.account?.server || "")}</span>` : `<span class="warn">ไม่ได้เชื่อมต่อ</span> ${esc(S.status?.mt5?.error || "")}`} — ถ้าใช้ MT5 ราคา/สเปรด/มูลค่าต่อ lot จะตรงกับโบรกเกอร์ของคุณ</div>
        <div class="formsec">ชื่อสัญลักษณ์ใน MT5 และต้นทุน</div>
        <div class="tbl-wrap"><table class="tbl"><thead><tr><th>สินทรัพย์</th><th>ชื่อใน MT5</th><th class="num">Contract</th><th class="num">Spread</th><th class="num">Slip</th><th class="num">Comm</th></tr></thead><tbody>
        ${S.symbols.map((x) => `<tr><td><b>${x.symbol}</b></td><td><input class="inp" style="height:32px;min-width:90px" data-map="${x.symbol}" value="${esc(s.mt5_symbol_map[x.symbol] || x.symbol)}"></td>
          ${["contract", "spread", "slippage", "commission"].map((k) => `<td><input class="inp num" style="height:32px;min-width:80px" type="number" step="any" data-ov="${x.symbol}.${k}" value="${ov[x.symbol]?.[k] ?? x[k]}"></td>`).join("")}</tr>`).join("")}</tbody></table></div>
        <div class="hint">Exness บัญชี Standard มักมี “m” ต่อท้าย เช่น USDJPYm · NAS100 ใน Exness = USTEC · สเปรดเป็นหน่วยราคา (EURUSD 0.0001 = 1 pip)</div>
      </div></div>
    </div>
    ${personalSettingsHtml()}
    <div class="card"><div class="hd"><h3>ความปลอดภัย</h3></div><div class="bd fgrid" style="grid-template-columns:1fr 1fr 1fr">
      <label class="f">PIN สำหรับเข้าเว็บ (ว่าง = ไม่ใช้)<input class="inp" type="password" data-k="access_pin" value="${esc(s.access_pin)}"></label>
      <label class="f">สแกนทุก (วินาที)<input class="inp num" type="number" data-k="scan_interval_sec" value="${s.scan_interval_sec}"></label>
      <div class="hint" style="align-self:end">ถ้าเปิดให้เข้าจากนอกบ้าน (Tailscale/Cloudflare) ควรตั้ง PIN</div></div></div>
    <div class="row" style="position:sticky;bottom:12px;justify-content:flex-end"><button class="btn primary" id="saveSet">💾 บันทึกการตั้งค่า</button></div>
  </div>`;
  const setPath = (obj, path, val) => { const ks = path.split("."); let o = obj; ks.slice(0, -1).forEach((k) => { o[k] = o[k] || {}; o = o[k]; }); o[ks[ks.length - 1]] = val; };
  $("#saveSet").onclick = async () => {
    const patch = {};
    $$("[data-k]", view).forEach((i) => {
      let v = i.type === "checkbox" ? i.checked : i.value;
      if (i.type === "number") v = parseFloat(v);
      setPath(patch, i.dataset.k, v);
    });
    setPath(patch, "research.timeframes", $$("[data-tf]", view).filter((i) => i.checked).map((i) => i.dataset.tf));
    patch.mt5_symbol_map = Object.fromEntries($$("[data-map]", view).map((i) => [i.dataset.map, i.value.trim()]));
    const ovs = {};
    $$("[data-ov]", view).forEach((i) => { const [sym, k] = i.dataset.ov.split("."); const v = parseFloat(i.value); if (!Number.isNaN(v)) (ovs[sym] = ovs[sym] || {})[k] = v; });
    patch.symbol_overrides = ovs;
    S.settings = await api("/api/settings", { method: "POST", body: patch });
    if (patch.access_pin !== undefined) store.set("pin", patch.access_pin);
    S.symbols = sortSyms(await api("/api/symbols"));
    toast("บันทึกแล้ว");
  };
  bindPersonalSettings(view);
  $("#testNotify").onclick = async () => { const r = await api("/api/notify/test", { method: "POST" }); toast("ผลส่ง: " + JSON.stringify(r)); };
}

// ------------------------------------------------------------------ GUIDE
// The user manual: one card per chapter, with a table of contents at the top.
function manualChapters() {
  const bal = myBalance() || 5000, risk = effRisk(0.5), riskUsd = bal * risk / 100;
  const lotsFor = (dist, vpu) => Math.floor(riskUsd / (dist * vpu) / 0.01 + 1e-9) * 0.01;
  const ex = (name, slText, dist, vpu) => { const l = lotsFor(dist, vpu);
    return `- **${name}** SL ${slText} → ${fnum(riskUsd, 2)} ÷ (${slText} × มูลค่าต่อ lot) = ${l >= 0.01 ? `**${l.toFixed(2)} lot**` : `ต่ำกว่า 0.01 → ถ้าเปิด 0.01 lot จะเสี่ยง $${fnum(0.01 * dist * vpu, 2)} (${fnum(100 * 0.01 * dist * vpu / bal, 2)}%)`}`; };
  return [
    ["overview", "1. ภาพรวม: อะไรทำเอง อะไรคุณต้องทำ", `
เปรียบเหมือน **ร้านอาหารที่มีพ่อครัว 2 คน**: พ่อครัวหลักอยู่ที่คอมของคุณ (เร็ว ทำอาหารจริง = เปิดออเดอร์) ส่วนพ่อครัวสำรองอยู่บนคลาวด์ (ช้ากว่า แต่คอยรับออเดอร์แทนเมื่อพ่อครัวหลักไม่อยู่)
### 💻 1) คอมของคุณ — ตัวหลัก (โปรแกรม autotrade.bat เปิดเองเมื่อเปิดคอม)
- **ทุกต้นชั่วโมง + 5 วินาที:** อ่านแท่งเทียนจาก MT5 (ราคา Exness จริง) → หาสัญญาณของทุกระบบ ACTIVE → **ส่งแจ้งเตือนเข้ามือถือ + เปิดออเดอร์ใน MT5 ทันที** → ส่งสัญญาณขึ้นเว็บ
- วาง **SL / TP ราคาเดียวกับในแจ้งเตือนเป๊ะ** · Lot คำนวณจากยอดเงินจริงในบัญชี MT5 (ต่ำกว่า 0.01 → เปิด 0.01)
- ทุก 2 นาที: ตรวจว่าคลาวด์มีสัญญาณที่คอมยังไม่ได้เปิดไหม (เช่น ตอนคอมเพิ่งเปิดเครื่อง) → ตามเปิดให้ภายใน 4 แท่ง แต่ **ไม่ไล่ราคา** ถ้าราคาวิ่งไปเกิน 0.5R แล้ว
- เมื่อออเดอร์ชน SL / TP หรือถูกปิดเพราะสัญญาณกลับทิศ → แจ้งเตือนเข้ามือถือ และอัปเดตหน้า **พอร์ต MT5**
- ป้องกันพอร์ตพัง: ขาดทุนรวม 25% หยุดเปิดใหม่ · 30% ปิดทุกออเดอร์ · ไม่ยอมเทรดบัญชีจริงจนกว่าคุณจะอนุญาต
### ☁️ 2) คลาวด์ GitHub — ตัวสำรอง (ทำงานเองตลอด ไม่ต้องเปิดคอม)
- **ทุก ~10–50 นาที:** ใช้ราคาฟรี (Yahoo / Binance / Twelve Data) หาสัญญาณแบบเดียวกัน → ถ้าคอมยังไม่ได้แจ้ง จะส่งแจ้งเตือนแทน (**แต่เปิดออเดอร์ใน MT5 ไม่ได้**) → สร้างเว็บนี้ใหม่
- **ทุกต้นไตรมาส (1 ม.ค. / 1 เม.ย. / 1 ก.ค. / 1 ต.ค.):** วิจัยใหม่ทั้งหมดด้วยวิธี **QWG** (บทที่ 6) — ${S.strategies.length || 109} ระบบ × ${S.symbols.length || 20} สินทรัพย์ × 3 TF ด้วยข้อมูลถึงวันตัดรอบเดียวกัน
  - ทุกระบบที่ผ่านเกณฑ์เป็น **ACTIVE** และเทรดพร้อมกันได้ (หลายระบบต่อสินทรัพย์) ใช้พารามิเตอร์เดิมตลอดไตรมาส
  - กดวิจัยใหม่กลางไตรมาส (เช่นเพิ่มระบบ) ยังตัดข้อมูลวันเดิม → ระบบใหม่ที่ผ่าน ACTIVE ได้ทันที ระบบเดิม **ใช้ต่อจนจบไตรมาสเฉพาะเมื่อตกแค่ด่านหักโชค** (เกณฑ์นี้สูงขึ้นทุกครั้งที่เพิ่มระบบ) และราคาที่ใช้ทดสอบยังเป็นชุดเดิม — ถ้าตกด่านอื่น หรือเปลี่ยนแหล่งราคา (เช่น เปลี่ยนมาใช้ราคา Exness) จะตัดสินใหม่ทั้งหมด
  - หยุดระบบก่อนจบไตรมาสเฉพาะเมื่อผลจริงแย่กว่าที่ทดสอบไว้ชัดเจน (ตรวจวันจันทร์กับ Claude)
### 💰 ความเสี่ยง (ตั้งเมื่อ 2 พ.ย. 2026)
- **ทุกสัญญาณเสี่ยง ${S.settings?.risk_per_trade_pct ?? 0.5}% ของทุน** เท่ากันทุกสินทรัพย์ · **รวมไม่เกิน ${S.settings?.max_symbol_risk_pct ?? 1}% ต่อสินทรัพย์** — ถ้าเต็มเพดาน สัญญาณใหม่ของสินทรัพย์นั้นจะถูกข้าม (บันทึกผลแต่ไม่แจ้งเตือน)
### 🙋 ส่วนที่คุณทำ
- **เปิดคอมทิ้งไว้ + เปิด MT5 ล็อกอินบัญชี** (โปรแกรมเทรดเปิดเองตอนเปิดเครื่อง) · ดูผลที่หน้า **พอร์ต MT5** หรือมือถือ
- ถ้าคอมปิด: แจ้งเตือนจากคลาวด์ยังมา → เปิดออเดอร์เองตามการ์ดในหน้า **สัญญาณ** (บทที่ 3)
- **วันจันทร์ (ถ้าต้องการ):** บอก Claude ว่า “อัปเดตระบบเทรด” → ตรวจผลจริงเทียบผลวิจัย ปิดระบบที่เริ่มเสีย เขียนบันทึก
### 🔘 ปุ่ม “สั่งวิเคราะห์ใหม่ทันที” (หน้า **Strategy Lab**)
- ทางลัดที่ไม่บังคับ — เปิดหน้า **GitHub Actions** ✅ เขียว = สำเร็จ ❌ แดง = ล้มเหลว · ใช้เวลา ~2–4 ชั่วโมง
- ถ้าไม่กดเลย ระบบก็ยังทำงานครบตามตารางข้างบน`],
    ["setup", "2. ตั้งค่าครั้งแรก (ทำครั้งเดียวต่อเครื่อง)", `
- **ติดตั้งเป็นแอปบนมือถือ/iPad:** Safari → ปุ่มแชร์ → **เพิ่มไปยังหน้าจอโฮม** · Android Chrome → ⋮ → **ติดตั้งแอป**
- **เชื่อมเครื่อง (ครั้งเดียวต่อเครื่อง):** หน้า **ตั้งค่า → 🔗 ซิงค์ทุกเครื่อง** → ใส่ **รหัสซิงค์** → กด **เชื่อมเครื่องนี้** — API key (Finnhub / Twelve Data), ทุน, ความเสี่ยง และการแสดงผลจะมาเองทุกเครื่อง
  - ข้อมูลเก็บบนคลาวด์แบบ **เข้ารหัส** (เว็บนี้เป็นสาธารณะ ใครเปิดไฟล์ก็อ่านไม่ออกถ้าไม่มีรหัส) · เก็บรหัสซิงค์ไว้ให้ดี อย่าแชร์
  - อยากแก้ค่าจากเครื่องไหนก็ได้แล้วส่งไปทุกเครื่อง: ใส่ GitHub token ครั้งเดียวในการ์ดเดียวกัน
- **หน้า ตั้งค่า:** ใส่ **ทุน (USD)** และ **ความเสี่ยงต่อไม้ %** แล้วกด **บันทึก**
- **ชื่อสัญลักษณ์ใน MT5:** แก้ให้ตรงกับโบรกเกอร์ เช่น Exness อาจเป็น \`XAUUSDm\` และ NAS100 = \`USTEC\` ชื่อนี้จะแสดงบนการ์ดสัญญาณ
- **แจ้งเตือน:** ติดตั้งแอป **ntfy** (App Store / Play Store ฟรี ไม่ต้องสมัคร) → กด **+** → ใส่ชื่อ topic ที่ตั้งเป็น \`NTFY_TOPIC\` ใน GitHub → Subscribe
  - บน iPhone ต้องอนุญาตการแจ้งเตือนของแอป ntfy ด้วย
- **ทุนในข้อความแจ้งเตือน:** ข้อความที่เด้งใช้ทุนและความเสี่ยงที่ตั้งบนคลาวด์ (ตอนนี้ ${fnum(STATIC?.account_balance || bal, 0)} USD × ${S.settings?.risk_per_trade_pct ?? risk}%)
  - ถ้าทุนเปลี่ยนมาก แก้ได้ที่ GitHub → Settings → Secrets and variables → Actions → Variables → \`ACCOUNT_BALANCE\`
  - หรือบอก Claude ในวันจันทร์`],
    ["signal", "3. เมื่อมีสัญญาณ: เปิดออเดอร์ทีละขั้น", `
> ถ้าคอมเปิดอยู่ โปรแกรมเปิดออเดอร์ให้แล้ว (แจ้งเตือนจะบอก “💻 คอม”) — บทนี้ใช้ตอน **เทรดเอง** เช่น คอมปิด หรือเทรดอีกบัญชี
- **1.** มือถือเด้งแจ้งเตือน เช่น “🟢 BUY USTEC H1 — เปิด 0.12 lot” ในข้อความมี Lot, Entry, SL และ TP, R:R, กำไรเป้า
- **2.** เปิดเว็บ หน้า **สัญญาณ** → การ์ดสีเขียวคือ LONG/BUY สีแดงคือ SHORT/SELL
- **3.** เปิด **MT5** → สินทรัพย์เดียวกัน → New Order แบบ **Market Execution**
- **4.** ถ้าราคาใน MT5 ต่างจาก Entry ในการ์ด (ปกติต่างเล็กน้อย):
  - กด **🎯 ปรับตามราคา MT5** → ใส่ราคาที่จะเข้าจริง → **คำนวณใหม่**
  - เว็บจะเลื่อน SL/TP ให้ระยะเท่าเดิม และคำนวณ Lot ใหม่
- **5.** ใส่ **Volume = Lot**, **Stop Loss**, **Take Profit** ตามการ์ด → กด Buy/Sell
- **6.** กลับมากด **✋ ฉันเปิดเอง** หรือ **ข้าม** เพื่อบันทึก (สำหรับบัญชีที่เทรดมือเท่านั้น)
- **7.** ปล่อยให้ออเดอร์ชน **SL** หรือ **TP** เอง ระบบไม่มีกำหนดเวลาปิด
- **8.** ถ้ามีแจ้งเตือน **🔁 สัญญาณกลับทิศ** ของสินทรัพย์เดียวกัน: **ปิดออเดอร์เดิมทันที** แล้วเปิดไม้ใหม่ตามการ์ด (ระบบทดสอบด้วยกติกานี้)
### กติกาเวลาเข้า
- สัญญาณเกิดตอน **แท่งเทียนปิด** แล้วเข้าที่ราคาเปิดของแท่งถัดไป
- การ์ดบอก **“เข้าได้ถึง”** ถ้าเลยเวลานี้ ราคามักวิ่งไปไกลแล้ว ไม่ควรไล่ราคา
- **📋 คัดลอก** ใช้ส่งรายละเอียดออเดอร์ไปจดหรือแชทได้`],
    ["lot", "4. Lot size คำนวณอย่างไร", `
- สูตร: **Lot = (ทุน × ความเสี่ยง%) ÷ (ระยะ SL × มูลค่าต่อ 1 lot ต่อ 1 หน่วยราคา)** แล้วปัดลงทีละ 0.01
- ตอนนี้: ${fnum(bal, 0)} USD × ${risk}% = เสี่ยงไม้ละ **$${fnum(riskUsd, 2)}** ถ้าชน SL (ถ้าชน TP ได้ $${fnum(riskUsd, 2)} × R:R)
- **ตัวอย่าง:**
${ex("EURUSD", "0.0030 (30 pips)", 0.003, 100000)}
${ex("NAS100", "100 จุด", 100, 1)}
${ex("XAUUSD", "$40", 40, 100)} ← ระยะ SL ปกติของระบบทอง H4
- **คำนวณให้อัตโนมัติทุกที่:** ข้อความแจ้งเตือน, การ์ดสัญญาณ, ป้าย **ENTRY … · x.xx lot** บนกราฟ และเครื่องคำนวณ
- **ถ้า Lot ต่ำกว่า 0.01:** โบรกส่วนใหญ่เปิดได้ขั้นต่ำ 0.01 lot ระบบจะเตือนว่าถ้าเปิด 0.01 จะเสี่ยงกี่ % คุณเลือกเองได้ว่าจะข้ามหรือรับความเสี่ยงที่สูงกว่า
  - ทองคำ / เงิน / น้ำมัน (SL กว้าง) มักเจอกรณีนี้กับทุนน้อย · โปรแกรมเทรดบนคอมเปิด 0.01 lot ให้เสมอ (ยอมเสี่ยงเกินเล็กน้อย)
- **ข้อควรตรวจ 1 ครั้ง:** ขนาดสัญญาตั้งตาม Exness แล้ว (หุ้น 1 lot = 100 หุ้น, น้ำมัน 1,000 บาร์เรล, เงิน 5,000 ออนซ์) แต่โบรกอื่นอาจต่าง
  - วิธีตรวจ: ใน MT5 ตั้ง SL ตามการ์ด แล้วดูว่าขาดทุนถ้าชน SL ≈ $${fnum(riskUsd, 2)} หรือไม่
  - ถ้าไม่ตรง บอก Claude ให้แก้ค่า contract ของสินทรัพย์นั้น`],
    ["pages", "5. หน้าต่าง ๆ ในเว็บ", `
เมนูอยู่ทางซ้าย (มือถืออยู่ด้านบน) แบ่งเป็น **3 กลุ่ม** เหมือนห้องในบ้าน: 🔍 ห้องวิเคราะห์ → 💼 ห้องเทรด → ⚙️ ห้องเก็บของ — เปิดเว็บมาจะเข้าหน้า **กราฟ** ของสินทรัพย์ / TF / ระบบที่ดูล่าสุดเสมอ
### สินทรัพย์ ${S.symbols.length || 20} ตัว แบ่ง 5 กลุ่ม (เรียงแบบนี้ทุกหน้า)
${symGroups().map(({ g, list }) => `- ${g.icon} **${g.label} (${g.short})**: ${list.map((x) => `${ABBR[x.symbol] || x.symbol} = ${x.symbol}`).join(", ")}`).join("\n")}
### 🔍 กลุ่มวิเคราะห์
- **📈 กราฟ:** ปุ่ม **⚡ ด่วน** = ระบบ ACTIVE / WATCH ทุกสินทรัพย์ กดครั้งเดียวเปลี่ยนสินทรัพย์ + TF + ระบบ · แถบขวา: **H1 / H4 / D1** → **เลขระบบ** (★ = ระบบจริง) → **สินทรัพย์ตามกลุ่ม** (FX / CMD / IDX / STK / CRY)
  - จุดบนปุ่มสินทรัพย์: 🟢 ACTIVE · 🟠 WATCH · ไม่มีจุด = OFF · สี่เหลี่ยมฟ้า = กำลังถือออเดอร์
  - เส้น **ENTRY (ฟ้า) · SL (แดง) · TP (เขียว)** พร้อม Lot · เลื่อนซ้ายเพื่อดูย้อนหลัง · ป้าย **LIVE** = ราคาสดทุก 1 วินาที
  - เลือกระบบที่ไม่มี ★ = **ดูอย่างเดียว** ไม่ส่งแจ้งเตือน
- **🧠 ระบบเทรด:** Equity curve (WF OOS ใช้ตัดสิน / Backtest ทั้งช่วง) · รายละเอียด indicator และกติกา · รายชื่อทั้ง ${S.strategies.length || 109} ระบบ
- **🧪 Strategy Lab:** ผลทดสอบทีละขั้นของระบบเดียว (ข้อมูล → Backtest → Walk-Forward → หักค่าโชค → สุ่ม → Robustness → Monte Carlo → ผลตัดสิน) · ปุ่ม **▶ สั่งวิเคราะห์ใหม่ทันที**
### 💼 กลุ่มเทรด
- **🔔 สัญญาณ:** สัญญาณที่ยังเข้าได้ (Lot / SL / TP) สำหรับเทรดมือ · สัญญาณที่กำลังติดตาม · ประวัติและผล forward test
- **💼 พอร์ต MT5:** ยอดเงิน / equity ของบัญชีที่โปรแกรมเทรดให้ · ความเสี่ยงที่เปิดอยู่แยก 5 กลุ่มสินทรัพย์ · ออเดอร์ที่ถือ (กดแถวเพื่อไปกราฟ) · ออเดอร์ที่ปิด · ระบบของแต่ละสินทรัพย์
- **🧮 เครื่องคำนวณ:** เลือกสินทรัพย์ (แยกกลุ่ม) และฝั่ง ใส่ราคาเข้า + SL → ได้ Lot ทันที
### ⚙️ กลุ่มแอป
- **ตั้งค่า:** ทุน ความเสี่ยงต่อไม้ ชื่อสัญลักษณ์ใน MT5 การแสดงผล และซิงค์ทุกเครื่อง · **คู่มือ:** หน้านี้
- ปุ่ม 🔄 มุมขวาบนใช้โหลดข้อมูลใหม่ทั้งหมด`],
    ["method", "6. ระบบทดสอบกลยุทธ์อย่างไร — QWG (ทีละขั้น)", `
ชื่อวิธีนี้: **QWG — Quarterly Walk-Forward Gauntlet** (ด่านคัดระบบ 5 ชั้น แบบเดินหน้ารายไตรมาส): ① Walk-Forward ทีละไตรมาส → ② เทียบกับการเข้าแบบสุ่ม → ③ ทดสอบความทนทาน 8 แบบ → ④ Monte Carlo → ⑤ ด่านหักโชค (Šidák) · ตั้งแต่ 2 พ.ย. 2026 ทดสอบ **ระบบตามเทรนด์ 100 ระบบ** บน **20 สินทรัพย์** ด้วย **ราคาย้อนหลังของ Exness** (โบรกเกอร์ที่คุณเทรดจริง)
เปรียบเหมือน **คัดนักกีฬาเข้าทีมชาติ**: ไม่ได้ดูแค่ผลซ้อม (backtest) แต่ให้ลงแข่งสนามจริงที่ไม่เคยเห็น (walk-forward) แล้วเทียบกับคนที่ “เดาสุ่ม” และลองให้เล่นในสภาพแย่ ๆ (robustness) ก่อนจะเรียกติดทีม
### ขั้นที่ 1 — เตรียมข้อมูลและกติกาการเทรด
- ใช้แท่งเทียน H1 (H4 / D1 สร้างจาก H1) จาก **MT5 ของ Exness** (คอมส่งราคาย้อนหลังขึ้น GitHub ให้) ต่อท้ายด้วยราคาล่าสุดจาก Twelve Data / Yahoo / Binance
- **ตัดสินใจตอนแท่งปิด แล้วเข้าที่ราคาเปิดแท่งถัดไป** — ไม่แอบดูอนาคต (ทดสอบแล้วกับกราฟสุ่มล้วน ผลออกมาใกล้ 0R ทุกกลยุทธ์ แปลว่าไม่มีการแอบดูข้อมูลล่วงหน้า)
- ทางออกเหมือนกันทุกกลยุทธ์: **ชน SL (= k × ATR14)**, **ชน TP (= R:R × ระยะ SL)** หรือ **มีสัญญาณตรงข้าม** → ปิดที่ราคาเปิดแท่งถัดไปแล้วกลับทิศ · ไม่จำกัดเวลาถือ
  - ระบบตามเทรนด์ / เบรกเอาต์ / โมเมนตัม ลองเพิ่มอีกแบบ: **ไม่มี TP + SL เลื่อนตาม (trailing) 3 × ATR** — SL ขยับตามราคาดีที่สุดของแท่งที่ปิดแล้ว ล็อกกำไรเมื่อเทรนด์วิ่งไกล (แบบ Turtle / Clenow) · Walk-Forward เลือกเองว่าจะใช้หรือไม่
  - Backtest, Walk-Forward, การสุ่มจุดเข้า และ forward test ใช้กฎนี้เหมือนกันทั้งหมด
- ถ้าแท่งเดียวแตะทั้ง SL และ TP ให้ถือว่า **ชน SL ก่อน** (คิดแบบร้ายไว้ก่อน) และถ้าราคากระโดดข้าม SL จะออกที่ราคาเปิด
- **หักต้นทุนทุกไม้ด้วยตัวเลขจริงของ Exness:** spread **ตามชั่วโมงที่เข้าเทรด** (ค่ากลางค่อนแพงของแท่ง H1 ย้อนหลัง ~3 ปี แยก 24 ชั่วโมง — ช่วงเปลี่ยนวันตีสี่ถึงหกโมงเช้าเวลาไทยแพงกว่าหลายเท่า) + slippage × 2 + ค่าคอม
- **ค่า swap:** ตรวจจากประวัติจริงใน MT5 (3 พ.ย. 2026) แล้ว **บัญชี Pro ของคุณไม่ถูกหัก swap** การทดสอบจึงไม่หัก swap · มี **รอบเทียบแบบหัก swap** ด้วยวิธีเดียวกันทุกอย่าง (ดูในหน้า Lab ข้อ 8) เพื่อให้เห็นว่าระบบไหนพึ่งการไม่มี swap
  - โปรแกรมบนคอมตรวจ swap ที่ถูกหักจริงทุกวัน **ถ้าวันไหนเริ่มถูกหัก จะแจ้งเตือน และสลับการวิจัยกลับไปหัก swap พร้อมเริ่มวิจัยใหม่ให้อัตโนมัติ**
- **slippage:** ตอนนี้ใช้ค่าประมาณ · โปรแกรมบันทึกราคาที่ได้จริงทุกไม้ เมื่อครบ 50 ไม้ (สินทรัพย์ละ ≥ 10) การทดสอบจะใช้ค่าที่วัดได้จริงแทนอัตโนมัติ (หน้า พอร์ต MT5)
- **ตรวจคุณภาพข้อมูลราคา** (\`tools/check_history.py\`): หาช่วงที่ข้อมูลขาด ราคากระโดดแล้วเด้งกลับ (ราคาผิด) แท่งแบน และแท่งผิดรูป ทุกปีทุกสินทรัพย์ — ผล 3 พ.ย. 2026: FX / ทอง / ดัชนี / หุ้น สะอาด · **BTCUSD ปี 2014–ต้น 2018 ข้อมูลขาดเป็นส่วนใหญ่** จึงเริ่มใช้ตั้งแต่ ก.พ. 2018 · กติกาทั่วไป: เริ่มหลังเดือนสุดท้ายที่ข้อมูลบาง และหลังช่วงที่หายเกิน 7 วัน
- วัดผลเป็น **R** (1R = เงินที่เสี่ยงต่อไม้) จึงเทียบข้ามสินทรัพย์ได้
### ขั้นที่ 2 — Backtest ทุกชุดพารามิเตอร์ (ชุดเล็ก ๆ)
- ${S.strategies.length || 50} กลยุทธ์ × ${(S.settings?.research?.timeframes || ["H1", "H4", "D1"]).join(" / ")} แต่ละกลยุทธ์มีพารามิเตอร์ให้เลือก 8–32 ชุด (ตัวแปรสัญญาณ 1–2 ตัว × ระยะ SL × TP)
- **500 ระบบ (3 พ.ย. 2026):** ตามเทรนด์ / เบรกเอาต์ / โมเมนตัม 100 · อื่น ๆ 9 · **ย่อตัว / สวนสั้น 391** = ตัวจับการยืดตัว 24 แบบ (RSI 2, Connors RSI, Bollinger, IBS, ปิดลงติดกัน, Hammer ฯลฯ) × ตัวกรองสภาพตลาด 17 แบบ (เหนือ SMA200, ADX ต่ำ, ตลาดเงียบ ฯลฯ) — มีตัวกรองแนวโน้ม = ซื้อตอนย่อในขาขึ้น · ตัวกรองกรอบ = สวนเมื่อตลาดไม่มีเทรนด์
- ⚠️ ทุกระบบที่เพิ่มถูกนับในด่านหักโชค (บัญชีการทดสอบถาวร) — ยิ่งเพิ่ม เกณฑ์ t ยิ่งสูง
- ทุกกลยุทธ์ผ่านการตรวจโค้ดอัตโนมัติ (\`tests/check_strategies.py\`): เปลี่ยนราคาในอนาคตแล้วสัญญาณในอดีตต้องไม่เปลี่ยน และบนกราฟสุ่มต้องได้ผลใกล้ 0R
- ตั้งใจให้ชุดเล็ก เพราะยิ่งลองมากชุด ยิ่งมีโอกาสเจอ “ชุดที่ดีเพราะโชค”
### ขั้นที่ 3 — Walk-Forward แบบเลื่อนหน้าต่าง (หัวใจของการทดสอบ)
- ช่วงสอบเป็น **ไตรมาสตามปฏิทิน** (ม.ค.–มี.ค., เม.ย.–มิ.ย., …) และข้อมูลของทุกระบบถูกตัดที่ **ต้นไตรมาสปัจจุบัน** เหมือนกันหมด
- **ช่วงฝึก / ช่วงสอบแยกตาม TF (ผู้ใช้กำหนด 3 พ.ย. 2026):** H1 = ฝึก 2 ปี · สอบ 1 ปี · H4 และ D1 = ฝึก 3 ปี · สอบ 1 ปี · ขยับทีละ 1 ปี นับย้อนจากวันตัดรอบ (ช่วงสอบสุดท้ายจบที่ต้นไตรมาสปัจจุบันพอดี)
- **1.** ดูข้อมูลช่วงฝึกก่อนแต่ละปีที่สอบ แล้วเลือกพารามิเตอร์ที่อยู่บน **“ที่ราบสูง”** = คะแนนครึ่งหนึ่งจาก t-stat ของชุดนั้น อีกครึ่งจากค่าเฉลี่ยของชุดข้างเคียง (ไม่หยิบยอดแหลมที่ขยับนิดเดียวก็พัง) · ต้องมี ≥ 20 ไม้และเป็นบวก
  - **เปลี่ยนชุดเฉพาะเมื่อดีกว่าเดิมชัดเจน** (คะแนนดีกว่า ≥ 0.5) ไม่ไล่ตามชุดที่เพิ่งโชคดี — แบบเดียวกับที่กองทุนใช้พารามิเตอร์คงที่
- **2.** เอาพารามิเตอร์นั้นไปเทรด **ปีถัดไป** ที่ระบบไม่เคยเห็น (Out-of-Sample) โดยห้ามแก้
- **3.** เลื่อนไป 1 ปี แล้วทำซ้ำจนถึงวันตัดรอบ
- **ไตรมาสปัจจุบัน = ช่วงสอบรอบใหม่ที่กำลังเกิดขึ้นจริง:** พารามิเตอร์ที่ใช้เทรด คือชุดที่เลือกจากข้อมูลทั้งหมดก่อนวันตัดรอบ (กติกาเดียวกัน) ใช้เหมือนเดิมจนจบไตรมาส
- **4.** เอาผล OOS ทุกช่วงมาต่อกัน **ใช้ผลนี้เท่านั้นในการตัดสิน** (เหมือนข้อสอบที่ไม่เคยเห็น)
- ช่วงไหน In-Sample ไม่มีชุดที่เป็นบวก ระบบจะไม่เทรดช่วงนั้น
### ขั้นที่ 4 — เทียบกับการสุ่มจุดเข้า
- สุ่มจุดเข้า 300 รอบ โดยใช้ SL/TP/จำนวนไม้/สัดส่วน Long-Short เท่ากัน
- ต้องชนะการสุ่มอย่างน้อย 90% — ถ้าไม่ชนะ แปลว่ากำไรมาจากตลาดวิ่งทางเดียว ไม่ใช่จากสัญญาณ
- หน้า Lab ข้อ 8 แสดงเพิ่ม: **ผลฝั่ง Buy / Sell แยกกัน** และ **“ซื้อแล้วถือ” ในช่วงเดียวกัน** — ถ้ากำไรมาจากฝั่ง Buy อย่างเดียวในช่วงที่ตลาดขึ้น อาจแค่ตามตลาด
### ขั้นที่ 5 — Robustness 8 แบบ (พยายามทำให้ระบบพัง)
- ตัดข้อมูล Walk-Forward แบบอื่นอีก 4 แบบ (ช่วงฝึกยาวขึ้น 1 ปี · สั้นลง 1 ปี · ช่วงสอบครึ่งปี · ใช้ข้อมูลทั้งหมดก่อนหน้าเป็นช่วงฝึก), ต้นทุน × 2, เข้าช้า 1 แท่ง, ใส่ noise ในราคา 30 รอบ (เดิม 10 รอบ ผลแกว่งเกินไป)
- ฝั่ง Long/Short ไม่มีฝั่งที่ขาดทุนชัด, กำไรสม่ำเสมอรายครึ่งปี, ตัด 5% ไม้ที่ดีที่สุดออกแล้วยังบวก, พารามิเตอร์ข้างเคียงก็กำไร
### ขั้นที่ 6 — Monte Carlo
- สุ่มไม้ OOS ซ้ำ 5,000 รอบ **เป็นก้อน ๆ ละประมาณ 1 เดือน** (ไม้แพ้มักมาเป็นช่วง การสุ่มทีละไม้จะทำให้ DD ดูตื้นกว่าจริง) เพื่อดูว่า **ถ้าโชคไม่ดี** drawdown จะลึกแค่ไหน
- ใช้หา Profit factor ที่ระดับแย่ 5% และความเสี่ยงต่อไม้ที่ทำให้ DD 95% ไม่เกินเป้า
- Monte Carlo **ตรวจ overfitting ไม่ได้** จึงใช้กับผล OOS เท่านั้น
### ขั้นที่ 7 — “หักค่าโชค” แล้วตัดสินทุกระบบด้วยเกณฑ์เดียวกัน
- **ทุกระบบ × ทุก TF × ทุกสินทรัพย์ ผ่านขั้น 1–6 ครบเหมือนกันหมด** (บนคลาวด์แบ่งเป็น 24 งานรันพร้อมกัน ใช้เวลาราว 1–2 ชม.)
- ทุกสินทรัพย์มีผู้สมัครประมาณ ${(S.settings?.research?.timeframes || [1, 2, 3]).length * (S.strategies.length || 50)} ตัว (กลยุทธ์ × TF) ทุกตัวได้สถานะของตัวเอง และ **ทุกตัวที่ ACTIVE ถูกนำไปเทรด** (หน้ากราฟ / Lab เปิดมาที่ตัวที่ t สูงสุด)
- ปัญหา: **ถ้าให้คนจำนวนมากโยนเหรียญ คนที่ได้หัวมากที่สุดก็ดูเก่งเพราะโชค** จึงต้องตั้งเกณฑ์สูงขึ้นตามจำนวนผู้สมัคร (จำนวนไม้ของแต่ละระบบอยู่ในสูตร t อยู่แล้ว ส่วนนี้แก้อีกปัญหาหนึ่ง คือการ “หยิบตัวที่ดีที่สุดจากหลายตัว”)
- **นับเฉพาะการทดสอบที่เป็นอิสระจริง** (ปรับ 2 พ.ย. 2026): ระบบที่ผลขึ้นลงเหมือนกันนับเป็นตัวเดียว เหมือนคนหลายคนโยนเหรียญเหรียญเดียวกัน — วัดจากความสัมพันธ์ของผล OOS รายสัปดาห์ทุกผู้สมัคร (วิธี eigenvalue ของ Li & Ji 2005 · หลักการเดียวกับ Deflated Sharpe Ratio ของ Bailey & López de Prado)
- แล้วใช้ Šidák correction กับจำนวนนั้น: 20 การทดสอบ ≈ t 2.8 · 50 ≈ t 3.1 · 100 ≈ t 3.3 · 300 ≈ t 3.6 (ข้อเสนอของ Harvey, Liu & Zhu คือ t > 3)
- **ยิ่งเพิ่มกลยุทธ์ที่ต่างจากเดิม เกณฑ์ยิ่งสูง** — การมีกลยุทธ์มากขึ้นไม่ได้ทำให้มีระบบผ่านง่ายขึ้น
### ขั้นที่ 8 — ตัดสินสถานะ
- **ACTIVE:** ผ่านทุกเกณฑ์ → ส่งแจ้งเตือนจริงตลอดไตรมาส (★ ในหน้าเว็บ)
- **WATCH:** OOS เป็นบวก, t ≥ 1 และมีอย่างน้อย 15 ไม้ แต่ยังไม่ผ่านบางเกณฑ์ → ไม่เทรด ไม่แจ้งเตือน (ดูผลได้ในปุ่ม ⚡ ด่วน)
- **ค่า t ใช้ผลรวมรายเดือน** (เดือนที่ไม่มีไม้ = 0) ไม่ใช่รายไม้ เพราะไม้ที่เกิดติด ๆ กันมักได้ผลคล้ายกัน นับรายไม้จะทำให้ t สูงเกินจริง
- **ข้อสอบปิดผนึก:** เกณฑ์ทั้งหมดตัดสินจากผล OOS **ก่อน** 12 เดือนล่าสุด แล้วใช้ 12 เดือนล่าสุดเป็นข้อสอบสุดท้ายแยกต่างหาก (ต้องไม่ขาดทุน) — เหมือนเก็บข้อสอบจริงไว้ในซองที่เปิดครั้งเดียว
- **OFF:** ไม่พบ edge → ไม่เทรด
- **TRIAL (ทดลองบน demo):** WATCH ที่ผ่านทุกด่าน **ยกเว้นด่านหักโชค** (สูงสุด ${S.settings?.trials?.max_per_symbol ?? 2} ระบบต่อสินทรัพย์ เน้น H4/D1) — โปรแกรมเปิดให้ **เฉพาะบัญชี demo** ที่ความเสี่ยง ${S.settings?.trials?.risk_pct ?? 0.1}% ไม่แจ้งเตือน เพื่อเก็บหลักฐานจากตลาดจริง
- หลังเปิดใช้: ถ้า forward test ครบ 20 ไม้แล้ว expectancy ต่ำกว่าระดับแย่ 5% ของ Monte Carlo → สั่ง OFF
- **หยุดอัตโนมัติ (ตกลงกันไว้ล่วงหน้า):** ถ้าขาดทุนสะสมจริงของระบบใดเกิน ${S.settings?.kill_dd_mult ?? 2} เท่าของ drawdown ที่แย่ที่สุดใน OOS → หยุดเปิดออเดอร์ใหม่ทันทีและแจ้งเตือน (ไม่ต้องตัดสินใจตอนกำลังขาดทุน)
- **คงไว้จนจบไตรมาส** เฉพาะเมื่อตกแค่ด่านหักโชคและราคาที่ใช้ทดสอบยังเป็นชุดเดิม
### ขั้นที่ 9 — ทดสอบแบบพอร์ต (แบบกองทุน)
- กองทุนตามแนวโน้ม (CTA) ไม่ได้ดูระบบทีละตลาด แต่ใช้ระบบเดียวกับหลายตลาดพร้อมกันแล้วดูผลรวม — แต่ละตลาดเดี่ยว ๆ กำไรน้อยและแกว่ง แต่รวมกันแล้วเรียบขึ้นมาก (Moskowitz, Ooi & Pedersen 2012; Hurst, Ooi & Pedersen 2017)
- ระบบจึงนำผล Walk-Forward OOS ของ **ระบบเดียวกันบนทุกสินทรัพย์** มารวมเป็นผลรายเดือน (ทุกไม้เสี่ยงเท่ากัน) แล้วตัดสินด้วยเกณฑ์: OOS ≥ 36 เดือน · t รายเดือน ≥ เกณฑ์หักค่าโชค (นับผู้สมัครทุกระบบ × ทุก TF รวมระบบที่เลิกใช้) · สินทรัพย์ที่เป็นบวก ≥ 60% · MC PF@95% ≥ 1 · ชนะการสุ่มจุดเข้า (มัธยฐาน) ≥ 75% · Robustness เฉลี่ย ≥ 50
- ระบบพอร์ตที่ ACTIVE **เทรดทุกสินทรัพย์** (★ พอร์ต บนกราฟ) เสี่ยง **${S.settings?.portfolio_risk_pct ?? 0.5}%/ไม้** เท่ากับระบบเดี่ยว (ผู้ใช้เลือก 2 พ.ย. 2026) และรวมไม่เกิน ${S.settings?.max_symbol_risk_pct ?? 1}% ต่อสินทรัพย์ · ดูตารางในหน้า Strategy Lab → ภาพรวมทุกสินทรัพย์
### คลังระบบ (ปรับ ต.ค. 2026)
- เลิกใช้ 17 ระบบที่ขาดทุนแทบทุกสินทรัพย์ (ส่วนใหญ่เป็นระบบสวนเทรนด์ เช่น Z-score, Bollinger Fade, RSI 2) — **แต่ยังนับรวมในเกณฑ์หักค่าโชค** การตัดตัวแพ้ทิ้งจึงไม่ทำให้เกณฑ์ต่ำลง
- เพิ่ม 7 ระบบจากงานวิจัย/กองทุน: EWMAC หลายความเร็ว (Carver), Time-Series Momentum 1/3/12 เดือน (AQR), Turtle, Faber 10-month SMA, Clenow Trend, Volatility Breakout (Crabel), 52-Week High (George & Hwang) — เลขระบบใหม่ #51–57 (เลขเดิมไม่เปลี่ยน)
- ระบบตามเทรนด์ / เบรกเอาต์ ทดสอบทั้ง **มี TP และไม่มี TP** (ออกเมื่อชน SL หรือมีสัญญาณกลับทิศ แบบที่กองทุนตามเทรนด์ปล่อยกำไรวิ่ง) — Walk-Forward เลือกเองจากช่วงฝึก`],
    ["watch", "7. ป้าย ACTIVE / WATCH / OFF คืออะไร", `
เปรียบเหมือน **นักฟุตบอลในทีม**:
- **ACTIVE (ตัวจริง)** — ผ่านการคัดเลือกทุกด่าน ลงเล่นจริงทั้งไตรมาส → ระบบส่งแจ้งเตือนให้คุณเปิดออเดอร์ · มีตัวจริงได้หลายคนพร้อมกัน (หลายระบบต่อสินทรัพย์) — บัญชี Hedging จึงอาจมีทั้ง Buy และ Sell ของสินทรัพย์เดียวกันจากคนละระบบ
- **WATCH (ตัวสำรองที่ฟอร์มดี)** — ผล Out-of-Sample เป็นบวก แต่หลักฐานยังไม่พอ เช่น t-stat ต่ำกว่าเกณฑ์ หรือ Monte Carlo ยังไม่มั่นใจ
  - ตัวสำรองที่ตกแค่ด่านหักโชค = **TRIAL** ลงซ้อมในบัญชี demo ด้วยเงินเดิมพันเล็ก ๆ (ป้ายสีฟ้า) เพื่อดูฟอร์มจริง
  - **ไม่ส่งแจ้งเตือน** และไม่นับเป็นระบบเทรด · ถ้าข้อมูลไตรมาสถัดไปทำให้หลักฐานพอ รอบวิจัยหน้าอาจเลื่อนเป็น ACTIVE เอง
  - **ไม่ควรเทรดด้วยเงินจริง** ใช้ดูเพื่อเรียนรู้หรือเทรด demo
- **OFF (ไม่ติดทีม)** — ไม่พบความได้เปรียบที่แยกออกจากโชคได้ → ไม่เทรด
- กดการ์ดในหน้า **ระบบเทรด** จะเห็นว่าเกณฑ์ไหน ✓ ผ่าน ✗ ไม่ผ่าน`],
    ["trust", "8. วิธีนี้น่าเชื่อถือแค่ไหน และอ้างอิงจากไหน", `
### วิธีที่ใช้มาจากงานวิจัยและตำราที่มืออาชีพใช้ (ไม่ได้คิดขึ้นเอง)
- **Walk-Forward Analysis และ WF Efficiency:** Robert Pardo, *The Evaluation and Optimization of Trading Strategies* (Wiley, 2008) — [Wiley](https://www.wiley-vch.de/en/areas-interest/finance-economics-law/the-evaluation-and-optimization-of-trading-strategies-978-0-470-12801-5)
- **เทียบกับการสุ่ม / data-mining bias:** David Aronson, *Evidence-Based Technical Analysis* (Wiley, 2006) — [StockCharts](https://store.stockcharts.com/products/evidence-based-technical-analysis-applying-the-scientific-method-and-statistical-inference-to-trading-signals)
- **หักค่าโชคเมื่อเลือกจากหลายระบบ:** Bailey & López de Prado, *The Deflated Sharpe Ratio* (2014) — [SSRN](https://papers.ssrn.com/abstract=2460551) และ Harvey, Liu & Zhu, *…and the Cross-Section of Expected Returns* (2016) — [NBER](https://nber.org/papers/w20592)
- **เหตุผลที่กลยุทธ์ตามแนวโน้มอาจมี edge:** Moskowitz, Ooi & Pedersen, *Time Series Momentum* (Journal of Financial Economics, 2012) — [SSRN](https://papers.ssrn.com/abstract=2089463)
- **วัดผลเป็น R และ Monte Carlo ของลำดับเทรด:** แนวทางของ Van K. Tharp และ Kevin Davey (*Building Winning Algorithmic Trading Systems*, 2014)
### สิ่งที่ทำถูกต้องแล้ว
- ไม่แอบดูอนาคต (ตรวจกับกราฟสุ่มแล้ว), หักต้นทุน, ชน SL ก่อนเมื่อไม่แน่ใจ, ตัดสินจาก OOS เท่านั้น, ชุดพารามิเตอร์เล็ก
### ข้อจำกัดที่ต้องรู้ (ตรงไปตรงมา)
- **ข้อมูลย้อนหลังมาจาก MT5 ของโบรก** (FX ตั้งแต่ 1999, ทอง 2004, US500 2017, NAS100 2020) ต่อด้วย Yahoo — ดัชนียังมีข้อมูลสั้นกว่าตัวอื่น
- **ราคาใหม่ทุกชั่วโมงของทอง/ดัชนีเป็น futures จาก Yahoo** ระบบปรับระดับให้ต่อกับราคาโบรกแล้ว แต่ยังต่างจากราคาโบรกเล็กน้อย
- **Šidák ถือว่าผู้สมัครเป็นอิสระต่อกัน** ซึ่งจริง ๆ กลยุทธ์คล้ายกันหลายตัว เกณฑ์จึงค่อนข้างเข้ม (เข้มดีกว่าหลวม)
- **Monte Carlo สมมติว่าแต่ละไม้ไม่เกี่ยวกัน** ช่วงตลาดเปลี่ยนนิสัยยาว ๆ อาจแย่กว่าที่ MC แสดง
- **ไม่มีวิธีไหนพิสูจน์ได้ว่าจะกำไรในอนาคต** — การทดสอบนี้ทำได้แค่ “คัดระบบที่น่าจะเป็นโชคออก” ผลจริง (forward test) คือบทพิสูจน์สุดท้าย
- เว็บนี้เป็นเครื่องมือวิจัย ไม่ใช่คำแนะนำการลงทุน`],
    ["price", "9. ราคามาจากไหน, ราคาสด และ MT5", `
### ข้อมูลราคาแต่ละส่วนมาจากไหน
- **ข้อมูลย้อนหลังหลายปี (ทุกสินทรัพย์):** โปรแกรมบนคอมดึงจาก MT5 Exness แล้วส่งขึ้น GitHub ให้เอง (\`data/history_exness\`)
  - เวลาในไฟล์ MT5 เป็นเวลาเซิร์ฟเวอร์โบรก (GMT+2 / GMT+3) ระบบแปลงเป็นเวลาสากลให้อัตโนมัติ
- **ราคาใหม่ (สแกนทุก 10 นาที):** ทอง + FX จาก **Twelve Data** (ราคา spot เดียวกับกราฟ เมื่อใส่ secret \`TWELVE_DATA_KEY\` ใน GitHub แล้ว ไม่งั้นใช้ Yahoo) · ดัชนีจาก Yahoo (futures ปรับระดับให้ต่อกับราคาโบรก) · BTC, ETH จาก Binance
- **ราคาสดบนกราฟ (ขยับทุก 1 วินาที):** BTC/ETH จาก Binance ฟรี · FX/ทอง/ดัชนี ต้องใส่ Finnhub API key ฟรีในหน้า **ตั้งค่า**
- **แท่งเทียนให้เหมือนของจริง:** BTC/ETH ดึงแท่งจริง ~40 วันล่าสุดจาก Binance ทุกครั้งที่เปิดกราฟ · FX/ทอง ใส่ **Twelve Data key** (ฟรี, ไม่บังคับ) ในหน้าตั้งค่าเพื่อดึงแท่งจริงล่าสุด · ดัชนียังใช้ Yahoo futures
  - แท่ง H4 และ D1 ตัดตามเวลาเซิร์ฟเวอร์โบรก (H4 = 00/04/08… เวลา MT5, 1 วัน = ปิดตลาดนิวยอร์ก) จึงรูปร่างตรงกับ MT5
  - แท่งที่สร้างจากราคาสดระหว่างเปิดหน้าไว้ เปิดที่ราคาแรกของแท่งนั้นจริง ๆ ไม่ลากจากราคาเก่า
### ระบบที่ทดสอบกับราคาโบรก ใช้กับราคาจากแหล่งอื่นได้ไหม
- ได้ — ระบบดูรูปแบบของแท่ง H1/H4/D1 ซึ่งราคาจากแต่ละแหล่งต่างกันน้อยมาก (FX ไม่ถึง 1 pip) ส่วนทอง/ดัชนี futures ต่างระดับนิดหน่อยแต่ปรับให้แล้ว
- สิ่งที่ต้องใช้ให้ตรงคือ **ระยะ** SL/TP (pips/จุด) — ใช้ปุ่ม **ปรับตามราคา MT5** บนการ์ดสัญญาณก่อนส่งออเดอร์
### อื่น ๆ
- **แจ้งเตือนยังทำงานแม้คอมปิด** — คลาวด์สแกนแทน (GitHub อาจช้า 10–50 นาที) แต่ **การเปิดออเดอร์อัตโนมัติต้องเปิดคอม + MT5**
- **ราคาย้อนหลังที่ใช้ทดสอบ:** ดึงจาก MT5 ของ **Exness** โดยตรง (FX ตั้งแต่ปี 2002, ทอง/เงิน/น้ำมัน 2017, ดัชนี 2019, หุ้น 2019, BTC 2014, ETH 2018)
- ระบบตัดสินใจจากแท่งที่ **ปิดแล้ว** เท่านั้น ราคาสดมีไว้ดูเฉย ๆ
- **ถ้าเปิด MT5 บนคอมพร้อม \`start_server.bat\`:** เวอร์ชันบนคอมจะใช้ราคาและสเปกของโบรกเกอร์คุณโดยตรง`],
    ["autotrade", "10. เทรดอัตโนมัติบนคอม + ทุกอย่างให้ตรงกัน", `
โปรแกรม **TradingLab auto-trader** (autotrade.bat) รันบนคอมคู่กับ MT5 — ไม่ใช่ EA แต่เป็นโปรแกรมที่สั่ง MT5 จากภายนอก แล้วเปิด/ปิดออเดอร์ตามระบบ ACTIVE ให้เอง
### เปรียบเทียบ
เหมือนมี “กรรมการหลักข้างสนาม” (คอม) กับ “กรรมการสำรองดูถ่ายทอดสด” (คลาวด์) — ปกติคอมตัดสินคนเดียว คลาวด์ช่วยเฉพาะตอนคอมไม่อยู่ ทุกฝ่ายจึงได้คำตัดสินเดียวกัน
### ลำดับการทำงานทุกต้นชั่วโมง (นาทีที่ 0 + ~5 วินาที)
1. คอมดึงรายชื่อระบบ ACTIVE + รายการสัญญาณล่าสุดจาก GitHub
2. ตรวจสัญญาณด้วย **แท่งเทียนของโบรกเกอร์ใน MT5** (เวลาเซิร์ฟเวอร์วัดจาก MT5 เอง — Exness = UTC)
3. มีสัญญาณ → แจ้งเตือน “💻 จากคอม” เข้ามือถือ **ทันที** + ส่งขึ้นเว็บ + เปิดออเดอร์ใน MT5 → แจ้ง “🤖 … เปิดแล้ว”
4. ส่ง “สัญญาณชีพ” + รายการออเดอร์ใน MT5 ขึ้น GitHub → คลาวด์ไม่สร้างสัญญาณซ้ำ/ขัดแย้งในแท่งที่คอมตรวจแล้ว
- **คอมปิด/หลุด:** คลาวด์แจ้งแทน “☁️ จากคลาวด์” (ช้า 10–60 นาที เพราะ GitHub เลื่อนรอบเอง, ไม่มีการเปิดออเดอร์อัตโนมัติ)
- **รอบไหนพลาด:** ย้อนดู 1–2 แท่ง ถ้าเทรดของสัญญาณนั้นยังไม่จบ จะแจ้ง “แจ้งช้า … นาที” · โปรแกรมเปิดตามได้ถ้าราคายังไม่วิ่งเกิน 0.5R (ไม่ไล่ราคา)
### ราคาเดียวกันทุกที่
- ออเดอร์ใน MT5 ใช้ **SL / TP ราคาเดียวกับในแจ้งเตือนและบนเว็บ** (Lot คำนวณจากระยะจริง) → ชน SL ที่ราคาเดียวกัน
- การติดตามผลบนเว็บใช้ SL / TP ตามแจ้งเตือน · เส้นบนกราฟแสดงระดับของออเดอร์ที่แจ้งเตือนจริง
- SL / TP ฝากไว้ที่โบรกเกอร์ — ราคาแตะเมื่อไหร่ **โบรกเกอร์ปิดให้ทันที** แม้คอมดับ · ระบบออกด้วยสัญญาณกลับทิศ → โปรแกรมปิดให้ตอนต้นชั่วโมง
### บนเว็บ (หน้า “สัญญาณ”)
- 💻 คอมออนไลน์ / ☁️ คอมออฟไลน์ · **🤖 ถือใน MT5** = ออเดอร์จริงที่โปรแกรมถืออยู่ + กำไร/ขาดทุน · ตาราง **MT5 ปิดล่าสุด** (ปิดด้วย SL / TP / กลับทิศ)
- การ์ดแต่ละสัญญาณมีแถบ 🤖 บอกว่าโปรแกรมถืออยู่หรือปิดแล้ว · ถ้าคอมสแกนบางระบบไม่สำเร็จ จะมีแถบ ⚠️ เตือน
- ปุ่ม **✋ ฉันเปิดเอง / ข้าม** ไม่ต้องกด — มีไว้จดเฉพาะบัญชีที่คุณเทรดมือ (เช่น The5ers)
- “สัญญาณที่ยังเข้าได้ตอนนี้” = สัญญาณที่ยังทันเข้าแบบเทรดมือ (ภายใน 2 แท่ง) ไม่ใช่จำนวนออเดอร์ที่ถืออยู่
### เริ่มใช้งาน (ทำครั้งเดียว)
1. ล็อกอินบัญชีใน MT5 → กดปุ่ม **Algo Trading** ให้เป็นสีเขียว
2. data/local.json: ntfy topic = ชื่อเดียวกับแอป ntfy บนมือถือ · account_balance = ทุนที่ใช้คำนวณ Lot ในแจ้งเตือน (บัญชีเทรดมือ)
3. ดับเบิลคลิก **autotrade.bat** → ต้องเห็น [OK] MT5, รายชื่อสินทรัพย์ไม่มี [NOT FOUND], ntfy topic ถูก, Website: [OK]
4. เปิดคอม + MT5 + หน้าต่างดำ + แอป Claude บนคอม ทิ้งไว้ (ตั้งคอมไม่ให้หลับ) — โปรแกรม **อัปเดตตัวเอง** เมื่อมีโค้ดใหม่บน GitHub
### กติกาความเสี่ยง
- ความเสี่ยง **0.5% ต่อไม้ เท่ากันทุกระบบทุกสินทรัพย์** (ตามที่คุณเลือก 2 พ.ย. 2026) คิดจาก **ทุนจริงใน MT5** · Lot ต่ำกว่าขั้นต่ำ → เปิด Lot ต่ำสุด
- สัญญาณที่แจ้งเตือน “ข้าม” (ความเสี่ยงที่เปิดอยู่ของสินทรัพย์เดียวกันจะเกิน 1%) → โปรแกรมไม่เปิดใหม่เช่นกัน (แต่ยังปิดออเดอร์เดิมถ้าเป็นสัญญาณกลับทิศ)
- กันล้างพอร์ต: ขาดทุนรวม ≥ 25% หยุดเปิดใหม่ · ≥ 30% ปิดทุกออเดอร์ของโปรแกรม
- บัญชีเงินจริง: โปรแกรมไม่ส่งคำสั่งจนกว่าจะตั้ง \`"allow_live": true\` ใน data/local.json → "autotrade"
### ปรับค่าได้ใน data/local.json → "autotrade"
\`risk_scale\`, \`pause_total_dd_pct\`, \`close_total_dd_pct\`, \`pause_daily_dd_pct\`, \`close_daily_dd_pct\` (0 = ปิด), \`min_lot_always\`, \`follow_cap\`, \`alert_levels\`, \`max_chase_r\`, \`allow_live\`, \`dry_run\`, \`enabled\` · บันทึกการทำงาน: data/autotrade_log.jsonl
`],
    ["monday", "11. วันจันทร์กับ Claude", `
- เปิดคอม → เปิด Claude ในโฟลเดอร์ \`D:\\Backtest\` → พิมพ์ เช่น “อัปเดตระบบเทรด” หรือ “ทุนเปลี่ยนเป็น 6000”
- Claude จะ:
  - ดึงผลล่าสุดจากคลาวด์
  - เทียบผลจริง (forward test) กับผลวิจัย
  - ปิดระบบที่ผลจริงแย่กว่าช่วงที่ Monte Carlo คาดไว้
  - เขียนบันทึก (แสดงในหน้า **ระบบเทรด**)
  - ส่งขึ้นคลาวด์ให้เสร็จในวันนั้น
- กติกาที่ Claude ต้องทำตาม: **ไม่ลดเกณฑ์เพื่อให้มีสัญญาณ**, ตัดสินจากผล Out-of-Sample เท่านั้น, ปิดออเดอร์ด้วย TP / SL / สัญญาณกลับทิศเท่านั้น`],
    ["faq", "12. คำถามที่พบบ่อย / ข้อควรรู้", `
- **เว็บมีเวอร์ชันใหม่ หรือข้อมูลใหม่:** จะมีแถบสีเขียว “🆕 มีเว็บเวอร์ชันใหม่” หรือแถบสีฟ้า “มีข้อมูลใหม่” ขึ้นด้านบน → กด **อัปเดตเลย** / **โหลดข้อมูลใหม่**
  - ปุ่ม **🔄 รีเฟรชทั้งหมด** มุมขวาบน ใช้ได้ทุกเมื่อ (เหมาะกับแอปบน iPad ที่ไม่มีปุ่มรีโหลด) ค่าที่ตั้งไว้ในเครื่อง เช่น ทุน จะไม่หาย
- **ทำไมไม่มีสัญญาณเลยหลายวัน?** ส่งแจ้งเตือนเฉพาะระบบ ACTIVE และเฉพาะตอนไม่มีออเดอร์ค้าง ถ้า ACTIVE มีน้อย สัญญาณก็น้อย ซึ่งดีกว่าเทรดระบบที่ไม่ผ่านการทดสอบ
- **กดปุ่มวิเคราะห์แล้วเจอหน้า GitHub ไม่มีปุ่ม Run workflow?** ต้องกด **Sign in** มุมขวาบนของ GitHub ก่อน → เมนูซ้ายเลือก **TradingLab** → ปุ่ม **Run workflow** → เลือก \`research\` → Run
- **ไม่มีแจ้งเตือนเด้ง?** ตรวจว่าแอป ntfy subscribe topic ถูกตัว และอนุญาตการแจ้งเตือนในมือถือ ดูได้ที่ GitHub Actions ว่ารอบล่าสุดเป็น ✅
- **ความเสี่ยงต่อไม้ควรเท่าไร?** ระบบใช้ค่าที่คุณตั้ง (${risk}%) สำหรับทุกสัญญาณ Monte Carlo ในหน้าระบบเทรดแสดงว่า drawdown แย่ ๆ อาจลึกแค่ไหน ใช้ประกอบการตัดสินใจของคุณเอง
- ผลวิจัยมาจากอดีต **ไม่รับประกันอนาคต** — แนะนำเริ่มด้วยบัญชี demo หรือความเสี่ยงต่ำ จนกว่าผล forward test จะยืนยัน
- เว็บนี้เป็นเครื่องมือวิจัย **ไม่ใช่คำแนะนำการลงทุน** การตัดสินใจเทรดเป็นของคุณ`],
  ];
}

// ------------------------------------------------------------------ SIGNALS
// one tile per symbol: what it trades this quarter, what it holds now (signals page + portfolio page)
function symTiles(list, sigs) {
  const now = new Date();
  const openOf = (sym) => sigs.find((s) => s.symbol === sym && s.outcome === "open");
  const tile = (x) => {
    // what this symbol really trades this quarter (single-symbol + portfolio systems); none -> best candidate, muted
    const sy = x.systems || [], main = sy.find((y) => y.source !== "portfolio") || sy[0];
    const st = stratById(main ? main.strategy : x.strategy), tf = main ? main.tf : x.tf, op = openOf(x.symbol);
    const more = sy.length > 1 ? ` <span class="badge ghost" title="${esc(sy.map((y) => "#" + (stratById(y.strategy)?.no ?? "?") + " " + y.tf + (y.source === "portfolio" ? " (พอร์ต)" : "")).join(" · "))}">+${sy.length - 1} ระบบ</span>` : "";
    const exp = main ? main.exp : x.oos?.expectancy;
    return `<a class="sys-tile ${x.status}" href="#/chart?symbol=${x.symbol}&tf=${tf || "H1"}${st ? "&strategy=" + st.id : ""}">
      <div class="t-top"><span class="ab">${ABBR[x.symbol] || ""}</span><b>${x.symbol}</b>${statusBadge(x.status)}</div>
      <div class="t-sys">${st ? `<b>#${st.no}</b> ${esc(st.name)}` : '<span class="muted">ไม่มีระบบ</span>'} <span class="muted">${tf || ""}</span>${main?.source === "portfolio" ? ' <span class="badge ghost">พอร์ต</span>' : ""}${more}
        ${!sy.length ? '<div class="tiny muted">ยังไม่มีระบบผ่านเกณฑ์ — แสดงตัวที่ดีที่สุด ไม่เทรด</div>' : ""}</div>
      ${heldOf(x.symbol).length ? `<div class="t-held">${heldOf(x.symbol).map((h) => `<div class="hrow"><span class="held ${h.dir > 0 ? "long" : "short"}">${h.dir > 0 ? "▲" : "▼"} ${h.side}</span>
        <b>#${stratById(h.strategy)?.no ?? "?"}</b> <span class="muted">${h.tf}</span> <span class="mono">@ ${h.entry}</span>${h.source === "portfolio" ? ' <span class="badge ghost">พอร์ต</span>' : ""}<span class="tiny ${h.user_status === "taken" ? "pos" : "muted"}" style="margin-left:auto">${h.user_status === "taken" ? "เปิดแล้ว" : "ยังไม่ยืนยัน"}</span></div>`).join("")}</div>` : ""}
      <div class="t-foot">${heldOf(x.symbol).length ? `<span class="pos">● ถืออยู่ ${heldOf(x.symbol).length} ออเดอร์</span>` : op ? `<span class="${op.dir > 0 ? "pos" : "neg"}">● ${op.side}</span> ${op.user_status === "new" && new Date(op.valid_until) > now ? "รอเข้า" : "กำลังติดตาม"}` : sy.length ? '<span class="muted">○ รอสัญญาณ</span>' : '<span class="muted">— ไม่เทรด</span>'}
        ${exp != null ? `<span class="mono muted">${fr(exp, 2)}/ไม้</span>` : ""}</div></a>`;
  };
  return list.map(tile).join("");
}
function groupedTiles(list, sigs) {
  return symGroups(list).map(({ g, list: l }) => `<div class="grp-h"><span>${g.icon} ${g.label}</span><span class="muted small">${l.filter((x) => x.status === "ACTIVE").length}/${l.length} มีระบบ ACTIVE</span></div>
    <div class="tiles">${symTiles(l, sigs)}</div>`).join("");
}
async function renderSignals(view) {
  const [sigs, syms, fwd, pc] = await Promise.all([api("/api/signals"), api("/api/symbols"), api("/api/forward"), STATIC ? pcStatus() : null]);
  S.pc = pc;
  const pcAge = pc && pc.time ? (Date.now() - new Date(pc.time)) / 60000 : null;
  const rpos = (pc && pc.positions) || [], rpnl = rpos.reduce((a, x) => a + (+x.profit || 0), 0);
  const pcErr = pc && pc.scan && (pc.scan.errors || []).length && pcAge !== null && pcAge < 70
    ? `<div class="banner" style="margin-top:10px"><span class="ic">⚠️</span><div><b>คอมสแกนบางระบบไม่สำเร็จ</b> (${pc.scan.errors.length}) — ${pc.scan.errors.slice(0, 3).map(esc).join(" · ")}</div></div>` : "";
  const rclosed = ((pc && pc.closed) || []).slice(0, 8);
  const pcLine = !STATIC ? "" : pcAge !== null && pcAge < 70
    ? `<div class="hero-s"><span class="pos">💻 คอมออนไลน์</span> · สแกนราคา MT5 ทุกต้นชั่วโมง · ล่าสุด ${ago(pc.time)} — แจ้งเตือน + เปิดออเดอร์ภายในไม่กี่วินาที</div>`
    : `<div class="hero-s"><span class="neg">☁️ คอมออฟไลน์</span> · ใช้คลาวด์แทน (แจ้งช้า 10–60 นาที, ไม่เปิดออเดอร์อัตโนมัติ)${pc && pc.time ? ` · คอมสแกนล่าสุด ${ago(pc.time)}` : ""}</div>`;
  S.sigs = sigs;
  S.symbols = sortSyms(syms);
  const now = new Date();
  const trials = sigs.filter((s) => s.system_status === "TRIAL" && s.outcome === "open");
  const actionable = sigs.filter((s) => s.outcome === "open" && s.user_status === "new" && new Date(s.valid_until) > now && s.system_status !== "TRIAL");
  const tracking = sigs.filter((s) => s.outcome === "open" && !actionable.includes(s) && s.system_status !== "TRIAL");
  const closed = sigs.filter((s) => s.outcome !== "open");
  S.newCount = actionable.length; renderNav(S.newCount);
  const active = syms.filter((x) => x.status === "ACTIVE");
  const fa = fwd.all;
  view.innerHTML = `<div class="page">
    <div class="hero">
      <div class="hero-main"><div class="hero-k">สัญญาณที่ยังเข้าได้ตอนนี้ (สำหรับเทรดมือ)</div><div class="hero-v">${actionable.length}</div>${pcLine}
        <div class="hero-s">${STATIC ? `☁️ คลาวด์ (สำรอง) สแกนล่าสุด ${STATIC.last_scan ? ago(STATIC.last_scan) : "–"} · ทำงานแทนเมื่อคอมออฟไลน์` : `สแกนทุก ${S.settings?.scan_interval_sec || 120} วินาที`}</div>${pcErr}</div>
      <div class="hero-kpis">${kpi("ระบบ ACTIVE", active.reduce((n, x) => n + Math.max(1, (x.systems || []).length), 0), active.map((x) => x.symbol + ((x.systems || []).length > 1 ? ` ×${x.systems.length}` : "")).join(", ") || "ยังไม่มี", active.length ? "pos" : "")}
        ${STATIC ? kpi("🤖 ถือใน MT5", `<a href="#/port">${rpos.length}</a>`, pc && pc.time ? `กำไร/ขาดทุนตอนนี้ ${fusd(rpnl)} · อัปเดต ${ago(pc.time)}` : "ยังไม่มีข้อมูลจากคอม", rpos.length ? cls(rpnl) : "") : ""}
        ${kpi("กำลังติดตาม", tracking.length, "สัญญาณที่ยังไม่จบ")}
        ${kpi("Forward test", fa ? fr(fa.net_r, 1) : "–", fa ? `${fa.trades} ไม้ · ชนะ ${fa.win_rate}%` : "ยังไม่มีไม้ที่จบ", fa ? cls(fa.net_r) : "")}
        <div class="kpi act"><button class="btn sm" id="scanBtn">${ICONS.refresh} ${STATIC ? "โหลดใหม่" : "สแกนตอนนี้"}</button></div></div>
    </div>
    ${actionable.length ? `<div class="sec-h"><h2>🔔 เข้าได้ตอนนี้</h2></div><div class="sig-grid">${actionable.map((s) => signalCard(s)).join("")}</div>`
      : `<div class="calm"><div class="big">🔕</div><div><b>ยังไม่มีสัญญาณใหม่</b><div class="muted small">ระบบกำลังเฝ้าตลาดให้ — เมื่อมีสัญญาณ มือถือจะเด้งแจ้งเตือนพร้อม Lot / SL / TP</div></div></div>`}
    ${STATIC && (rpos.length || rclosed.length) ? `<a class="banner ok link" href="#/port" style="margin-top:14px"><span class="ic">🤖</span><div><b>MT5 ถือ ${rpos.length} ออเดอร์</b> · กำไร/ขาดทุนตอนนี้ <span class="${cls(rpnl)}">${fusd(rpnl)}</span> — ดูพอร์ต ความเสี่ยงแยกกลุ่ม และออเดอร์ที่ปิดแล้วในหน้า <b>พอร์ต MT5 →</b></div></a>` : ""}
    ${trials.length ? `<div class="sec-h"><h2>🧪 ทดลองบนบัญชี DEMO</h2><span class="badge">${trials.length}</span><span class="muted small" style="margin-left:auto">ระบบที่เกือบผ่าน · เสี่ยง ${S.settings?.trials?.risk_pct ?? 0.1}% · โปรแกรมเปิดเฉพาะบัญชี demo · ไม่แจ้งเตือน ไม่ต้องเปิดเอง</span></div><div class="sig-grid">${trials.map((s) => signalCard(s)).join("")}</div>` : ""}
    ${tracking.length ? `<div class="sec-h"><h2>กำลังติดตาม</h2><span class="badge">${tracking.length}</span></div><div class="sig-grid">${tracking.map((s) => signalCard(s)).join("")}</div>` : ""}
    <div class="sec-h"><h2>ประวัติสัญญาณ</h2><span class="badge">${closed.length}</span>${fa ? `<span class="muted small" style="margin-left:auto">Expectancy ${fr(fa.expectancy, 3)} · PF ${fnum(fa.pf, 2)} · Max DD ${fnum(fa.max_dd_r, 1)}R</span>` : ""}</div>
    <div class="card"><div class="tbl-wrap tbl-scroll"><table class="tbl"><thead><tr><th>เวลา</th><th>สินทรัพย์</th><th>ฝั่ง</th><th>ระบบ</th><th class="num">Lot</th><th>ผล</th><th class="num">R</th><th>คุณ</th></tr></thead><tbody>
      ${closed.length ? closed.map((s) => `<tr><td>${fdt(s.bar_time)}</td><td><b>${s.symbol}</b> <span class="muted">${s.tf}</span></td><td>${statusBadge(s.side)}</td><td class="small">${stratById(s.strategy) ? "#" + stratById(s.strategy).no + " " : ""}${esc(s.strategy_name || "")}</td>
        <td class="num">${fnum(s.lots, 2)}</td><td><span class="badge ${s.outcome}">${s.exit_reason && !["SL", "TP"].includes(s.exit_reason) ? reasonTh(s.exit_reason) : { win: "TP", loss: "SL", time: "หมดเวลา" }[s.outcome]}</span></td><td class="num ${cls(s.outcome_r)}">${fr(s.outcome_r)}</td>
        <td>${s.user_status === "new" ? '<span class="faint">–</span>' : `<span class="badge ${s.user_status}">${s.user_status === "taken" ? "เปิด" : "ข้าม"}</span>`}</td></tr>`).join("")
        : `<tr><td colspan="8" class="muted" style="text-align:center;padding:22px">ยังไม่มีประวัติ — ทุกสัญญาณจะถูกติดตามผลด้วยกติกาเดียวกับ backtest</td></tr>`}</tbody></table></div></div>
  </div>`;
  bindSignalCards(view, sigs);
  $("#scanBtn").onclick = async (e) => { if (STATIC) { hardRefresh(); return; } e.currentTarget.disabled = true; const r = await api("/api/scan", { method: "POST" }); toast(r.new.length ? `พบ ${r.new.length} สัญญาณใหม่` : "สแกนแล้ว ไม่มีสัญญาณใหม่"); navigate(); };
}

// ------------------------------------------------------------------ PORTFOLIO: what the PC robot holds in MT5, risk by asset class, systems per symbol
// execution quality: how much the real fills cost compared with the quote and with the signal's entry
function fillsHtml(f) {
  if (!f.length) return "";
  const by = {};
  f.forEach((x) => { (by[x.symbol] ||= []).push(x); });
  const avg = (a, k) => { const v = a.map((x) => +x[k]).filter((x) => Number.isFinite(x)); return v.length ? v.reduce((p, q) => p + q, 0) / v.length : null; };
  return `<div class="sec-h"><h2>คุณภาพการเปิดออเดอร์</h2><span class="badge">${f.length}</span><span class="muted small" style="margin-left:auto">ราคาที่ได้จริงเทียบราคาที่สั่ง และเทียบราคาเข้าในสัญญาณ</span></div>
    <div class="card"><div class="tbl-wrap"><table class="tbl"><thead><tr><th>สินทรัพย์</th><th class="num">ออเดอร์</th><th class="num">Slippage เฉลี่ย (จุด)</th><th class="num">ห่างจากราคาสัญญาณ (R)</th><th class="num">เปิดหลังแท่งปิด (วินาที)</th></tr></thead><tbody>
    ${Object.entries(by).map(([k, a]) => { const sl = avg(a, "slip_pts"), dr = avg(a, "drift_r"), dl = avg(a, "delay_s");
      return `<tr><td><b>${esc(k)}</b></td><td class="num">${a.length}</td><td class="num ${sl > 0 ? "neg" : "pos"}">${sl == null ? "–" : fnum(sl, 1)}</td><td class="num ${dr > 0.05 ? "neg" : ""}">${dr == null ? "–" : (dr > 0 ? "+" : "") + fnum(dr, 3)}</td><td class="num">${dl == null ? "–" : fnum(dl, 0)}</td></tr>`; }).join("")}
    </tbody></table></div><div class="hint pad-x" style="padding:8px 14px 12px">+ = ได้ราคาแย่กว่า · ถ้า "ห่างจากราคาสัญญาณ" เกิน ~0.05R เป็นประจำ ต้นทุนจริงสูงกว่าที่ใช้ทดสอบ ต้องปรับต้นทุนในการทดสอบ</div></div>`;
}
// slippage the tests use vs the robot's real fills (app/fills.py): measured value replaces the estimate once there are enough
function slipHtml(c) {
  const rows = Object.entries((c && c.symbols) || {});
  const head = `<div class="sec-h"><h2>ปรับ slippage จากของจริง</h2><span class="badge">${c?.total ?? 0}/${c?.min_total ?? 50} ไม้</span></div>`;
  if (!rows.length) return head + `<div class="card"><div class="hint pad-x" style="padding:12px 14px">ยังไม่มีออเดอร์จริงพอ — เมื่อครบ ${c?.min_total ?? 50} ไม้ (และสินทรัพย์ละ ${c?.min_per_symbol ?? 10} ไม้) การทดสอบจะใช้ slippage ที่วัดได้แทนค่าประมาณ</div></div>`;
  return head + `<div class="card"><div class="tbl-wrap"><table class="tbl"><thead><tr><th>สินทรัพย์</th><th class="num">ไม้</th><th class="num">slippage กลาง (จุด)</th><th class="num">p75 (จุด)</th><th class="num">ค่าที่ใช้ทดสอบตอนนี้</th></tr></thead><tbody>
    ${rows.map(([k, x]) => `<tr><td><b>${esc(k)}</b></td><td class="num">${x.n}</td><td class="num">${fnum(x.median_pts, 1)}</td><td class="num">${fnum(x.p75_pts, 1)}</td><td class="num">${x.use != null ? `<b>${x.use}</b> (วัดจริง)` : `${x.estimate_price} (ประมาณ)`}</td></tr>`).join("")}
    </tbody></table></div><div class="hint pad-x" style="padding:8px 14px 12px">ใช้ค่าเปอร์เซ็นไทล์ที่ 75 ของไม้จริง (นับเฉพาะฝั่งที่เสียเปรียบ) และไม่ต่ำกว่าครึ่งหนึ่งของค่าประมาณเดิม · ปรับให้อัตโนมัติในการวิจัยรอบถัดไป</div></div>`;
}
// markets that move together share one risk budget (same groups as app/scanner.py CLUSTERS)
const CLUSTERS = { usd_fx: ["คู่เงิน USD", ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"]], jpy_cross: ["คู่เงิน JPY", ["GBPJPY"]],
  metals: ["โลหะ", ["XAUUSD", "XAGUSD"]], energy: ["พลังงาน", ["USOIL"]], us_equity: ["หุ้นสหรัฐ", ["NAS100", "US500", "NVDA", "MSFT", "AMZN", "META", "AAPL"]],
  world_equity: ["ดัชนียุโรป/ญี่ปุ่น", ["DE30", "JP225"]], crypto: ["คริปโต", ["BTCUSD", "ETHUSD"]] };
const clusterCap = (k) => { const c = S.settings?.cluster_risk_pct || {}; return +(c[k] ?? c.default ?? 1.5); };
async function renderPort(view) {
  const [sigs, syms, pc, killed, slip] = await Promise.all([api("/api/signals"), api("/api/symbols"), pcStatus(), ghLive("data/research/auto_off.json"),
    api("/api/slippage").catch(() => ({}))]);
  S.pc = pc || S.pc; S.sigs = sigs; S.symbols = sortSyms(syms);
  const age = pc && pc.time ? (Date.now() - new Date(pc.time)) / 60000 : null, online = age !== null && age < 70;
  const pos = (pc && pc.positions) || [], closed = (pc && pc.closed) || [];
  const bal = +(pc && pc.balance) || 0, eq = +(pc && pc.equity) || bal, start = +(pc && pc.start_balance) || 0;
  const pnl = pos.reduce((a, x) => a + (+x.profit || 0), 0), cpnl = closed.reduce((a, x) => a + (+x.profit || 0), 0);
  const cap = +(S.settings?.max_symbol_risk_pct) || 1;
  // money lost if SL is hit, from the real position (lots × distance × value per point); null = no SL
  const riskOf = (x) => x.sl && bal ? Math.abs(x.price_open - x.sl) * x.lots * valuePerUnit(x.symbol, x.price_open) / bal * 100 : null;
  const bySym = {}; pos.forEach((x) => { const r = riskOf(x); bySym[x.symbol] = (bySym[x.symbol] || 0) + (r || 0); });
  const totalRisk = Object.values(bySym).reduce((a, b) => a + b, 0);
  const groupCard = ({ g, list }) => {
    const ps = pos.filter((x) => list.some((y) => y.symbol === x.symbol)), r = ps.reduce((a, x) => a + (riskOf(x) || 0), 0);
    const act = list.filter((x) => x.status === "ACTIVE").length;
    return `<div class="gcard"><div class="gc-t">${g.icon} ${g.label}</div>
      <div class="gc-v">${ps.length} <small>ออเดอร์</small></div>
      <div class="meter"><i style="width:${Math.min(100, r / (cap * list.length) * 100).toFixed(0)}%"></i></div>
      <div class="gc-s"><span>เสี่ยงรวม <b class="${r > 0 ? "neg" : ""}">${fnum(r, 2)}%</b></span><span class="muted">${act}/${list.length} ACTIVE</span></div></div>`;
  };
  const dg = (sym) => specOf(sym).digits;
  view.innerHTML = `<div class="page">
    <div class="hero">
      <div class="hero-main"><div class="hero-k">พอร์ตในMT5 ที่โปรแกรมเทรดให้ ${pc && pc.account ? `<span class="badge ${pc.account === "DEMO" ? "ghost" : "ACTIVE"}">${esc(pc.account)}</span>` : ""}</div>
        <div class="hero-v">${bal ? "$" + fnum(eq, 2) : "–"}</div>
        <div class="hero-s">${online ? `<span class="pos">💻 คอมออนไลน์</span> · อัปเดต ${ago(pc.time)}` : `<span class="neg">💻 คอมออฟไลน์</span>${pc && pc.time ? ` · ข้อมูลล่าสุด ${ago(pc.time)}` : ""} — ระหว่างนี้ไม่มีการเปิดออเดอร์อัตโนมัติ`}</div>
        ${pc && pc.scan && (pc.scan.errors || []).length && online ? `<div class="banner" style="margin-top:10px"><span class="ic">⚠️</span><div><b>คอมสแกนบางระบบไม่สำเร็จ</b> — ${pc.scan.errors.slice(0, 3).map(esc).join(" · ")}</div></div>` : ""}</div>
      <div class="hero-kpis">
        ${kpi("Balance", bal ? "$" + fnum(bal, 2) : "–", start ? `เริ่ม $${fnum(start, 0)} · ${bal >= start ? "+" : ""}${fnum((bal / start - 1) * 100, 2)}%` : "ยอดหลังปิดออเดอร์")}
        ${kpi("กำไร/ขาดทุนที่ถืออยู่", fusd(pnl), `${pos.length} ออเดอร์`, pos.length ? cls(pnl) : "")}
        ${kpi("ความเสี่ยงที่เปิดอยู่", fnum(totalRisk, 2) + "%", `ถ้าชน SL ทุกตัว ≈ -$${fnum(bal * totalRisk / 100, 2)} · เพดาน ${cap}%/สินทรัพย์`, totalRisk ? "neg" : "")}
        ${kpi("ปิดล่าสุด", fusd(cpnl), `${closed.length} ออเดอร์ล่าสุด`, closed.length ? cls(cpnl) : "")}</div>
    </div>
    ${!pc ? `<div class="banner" style="margin-top:14px"><span class="ic">ℹ️</span><div>ยังไม่มีข้อมูลจากคอม — หน้านี้แสดงผลเมื่อโปรแกรมเทรดอัตโนมัติ (autotrade.bat) ทำงานบนคอมและส่งสถานะขึ้น GitHub</div></div>` : ""}
    <div class="sec-h"><h2>ความเสี่ยงแยกตามกลุ่มสินทรัพย์</h2><span class="muted small" style="margin-left:auto">ทุกไม้เสี่ยง ${S.settings?.risk_per_trade_pct ?? 0.5}% · รวมไม่เกิน ${cap}% ต่อสินทรัพย์</span></div>
    <div class="gcards">${symGroups().map(groupCard).join("")}</div>
    <div class="card" style="margin-top:4px"><div class="tbl-wrap"><table class="tbl"><thead><tr><th>กลุ่มที่เคลื่อนไหวไปด้วยกัน</th><th>สินทรัพย์</th><th class="num">สัญญาณ ACTIVE ที่เปิดอยู่</th><th class="num">เพดาน</th></tr></thead><tbody>
      ${Object.entries(CLUSTERS).map(([k, [name, list]]) => { const r = sigs.filter((x) => list.includes(x.symbol) && x.outcome === "open" && !x.capped && x.system_status === "ACTIVE").reduce((a, x) => a + (+x.risk_pct || 0), 0);
        return `<tr><td><b>${name}</b></td><td class="small muted">${list.map((x) => ABBR[x] || x).join(" · ")}</td><td class="num ${r > 0 ? "neg" : ""}">${fnum(r, 2)}%</td><td class="num">${fnum(clusterCap(k), 1)}%</td></tr>`; }).join("")}
    </tbody></table></div><div class="hint pad-x" style="padding:8px 14px 12px">ตลาดที่มักขึ้นลงพร้อมกันใช้งบความเสี่ยงร่วมกัน — ถ้าเต็มเพดาน สัญญาณใหม่ของกลุ่มนั้นจะถูกข้าม (บันทึกผลแต่ไม่แจ้งเตือน ไม่เปิดออเดอร์)</div></div>
    ${killed && Object.keys(killed).length ? `<div class="banner" style="margin-top:12px"><span class="ic">⛔</span><div><b>ระบบที่หยุดอัตโนมัติ</b> (ขาดทุนสะสมจริงเกิน ${S.settings?.kill_dd_mult ?? 2} เท่าของที่แย่ที่สุดในการทดสอบ): ${Object.entries(killed).map(([k, v]) => { const [sy, sid, tf] = k.split(":"); return `${sy} #${stratById(sid)?.no ?? sid} ${tf} (−${fnum(v.dd_r, 1)}R จากเพดาน ${fnum(v.limit_r, 1)}R)`; }).join(" · ")}</div></div>` : ""}
    <div class="sec-h"><h2>🤖 ออเดอร์ที่ถืออยู่</h2><span class="badge">${pos.length}</span></div>
    <div class="card"><div class="tbl-wrap"><table class="tbl"><thead><tr><th>สินทรัพย์</th><th>ระบบ</th><th>ฝั่ง</th><th class="num">Lot</th><th class="num">ราคาเข้า</th><th class="num">ตอนนี้</th><th class="num">SL</th><th class="num">TP</th><th class="num">เสี่ยง</th><th class="num">กำไร</th></tr></thead><tbody>
      ${pos.length ? sortSyms(pos).map((x) => `<tr class="click" data-href="#/chart?symbol=${x.symbol}&tf=${(robotTag(x).split("-")[1]) || "H1"}"><td><b>${esc(x.symbol)}</b></td><td class="mono">${esc(robotTag(x))}</td><td>${statusBadge(x.side === "BUY" ? "LONG" : "SHORT")}</td><td class="num">${fnum(x.lots, 2)}</td>
        <td class="num">${fpx(x.price_open, dg(x.symbol))}</td><td class="num">${x.price != null ? fpx(x.price, dg(x.symbol)) : "–"}</td><td class="num neg">${x.sl ? fpx(x.sl, dg(x.symbol)) : "–"}</td><td class="num pos">${x.tp ? fpx(x.tp, dg(x.symbol)) : "ไม่ตั้ง"}</td>
        <td class="num">${riskOf(x) != null ? fnum(riskOf(x), 2) + "%" : "–"}</td><td class="num ${cls(x.profit)}">${fusd(x.profit)}</td></tr>`).join("")
        : `<tr><td colspan="10" class="muted" style="text-align:center;padding:20px">ไม่มีออเดอร์เปิดอยู่</td></tr>`}
    </tbody></table></div></div>
    <div class="sec-h"><h2>ปิดล่าสุด</h2><span class="badge">${closed.length}</span></div>
    <div class="card"><div class="tbl-wrap"><table class="tbl"><thead><tr><th>เวลา</th><th>สินทรัพย์</th><th>ระบบ</th><th>ฝั่ง</th><th>ปิดด้วย</th><th class="num">ราคาเข้า</th><th class="num">ราคาปิด</th><th class="num">กำไร</th></tr></thead><tbody>
      ${closed.length ? closed.map((x) => `<tr><td>${fdt(x.time)}</td><td><b>${esc(x.symbol || "")}</b></td><td class="mono">${esc(robotTag(x))}</td><td>${x.side ? statusBadge(x.side === "BUY" ? "LONG" : "SHORT") : "–"}</td><td>${esc(reasonTh(x.reason) || x.reason || "")}</td>
        <td class="num">${x.price_open != null ? fpx(x.price_open, dg(x.symbol)) : "–"}</td><td class="num">${x.price != null ? fpx(x.price, dg(x.symbol)) : "–"}</td><td class="num ${cls(x.profit)}">${fusd(x.profit)}</td></tr>`).join("")
        : `<tr><td colspan="8" class="muted" style="text-align:center;padding:20px">ยังไม่มีออเดอร์ที่ปิด</td></tr>`}
    </tbody></table></div></div>
    ${fillsHtml((pc && pc.fills) || [])}
    ${slipHtml(slip)}
    <div class="sec-h"><h2>ระบบของแต่ละสินทรัพย์</h2><a class="muted small" href="#/systems" style="margin-left:auto">รายละเอียดระบบ →</a></div>
    ${groupedTiles(S.symbols, sigs)}
  </div>`;
  $$("[data-href]", view).forEach((r) => r.onclick = () => go(r.dataset.href));
}

// ------------------------------------------------------------------ CALCULATOR
async function renderCalc(view) {
  const saved = store.get("calc", {});
  const sym = saved.symbol || "XAUUSD";
  const side = +(saved.side || 1);
  view.innerHTML = `<div class="page narrow"><div class="calc">
    <div class="card"><div class="hd"><h3>ข้อมูลออเดอร์</h3></div><div class="bd stack" style="gap:16px">
      <div><div class="lbl">สินทรัพย์</div><div id="kSym" class="pgroups">${symGroups().map(({ g, list }) => `<div class="pg"><div class="pg-l">${g.icon} ${g.label}</div><div class="pills">${list.map((x) => `<button class="pill ${x.symbol === sym ? "on" : ""}" data-v="${x.symbol}">${ABBR[x.symbol] || ""} <small>${x.symbol}</small></button>`).join("")}</div></div>`).join("")}</div></div>
      <div><div class="lbl">ฝั่ง</div><div class="seg wide" id="kSide"><button data-v="1" class="${side > 0 ? "active long" : ""}">▲ BUY / LONG</button><button data-v="-1" class="${side < 0 ? "active short" : ""}">▼ SELL / SHORT</button></div></div>
      <div class="fgrid" style="grid-template-columns:1fr 1fr">
        <label class="f">ทุน (USD)<input class="inp num" type="number" id="kBal" value="${saved.balance ?? myBalance()}"></label>
        <label class="f">ความเสี่ยงต่อไม้ (%)<input class="inp num" type="number" step="0.05" id="kRisk" value="${saved.risk ?? effRisk(0.5)}"></label>
        <label class="f full">ราคาเข้า (Entry)<input class="inp num big" type="number" step="any" id="kEntry" value="${saved.entry ?? ""}" placeholder="เช่น 4008.50"></label>
        <label class="f">Stop Loss <select class="inp mini" id="kSlMode"><option value="price">ราคา</option><option value="pips" ${saved.slMode === "pips" ? "selected" : ""}>pips</option></select><input class="inp num" type="number" step="any" id="kSl" value="${saved.sl ?? ""}"></label>
        <label class="f">Take Profit <select class="inp mini" id="kTpMode"><option value="rr">R:R</option><option value="price" ${saved.tpMode === "price" ? "selected" : ""}>ราคา</option></select><input class="inp num" type="number" step="any" id="kTp" value="${saved.tp ?? 2}"></label>
      </div>
      <div class="hint" id="kSpec"></div>
    </div></div>
    <div class="card result"><div class="hd"><h3>ผลลัพธ์สำหรับ MT5</h3></div><div class="bd" id="kOut"><div class="calm"><div class="big">🧮</div><div class="muted">กรอกราคาเข้าและ Stop Loss</div></div></div></div>
  </div></div>`;
  let curSym = sym, curSide = side;
  $("#kSym").onclick = (e) => { const b = e.target.closest("[data-v]"); if (!b) return; curSym = b.dataset.v; $$("#kSym .pill").forEach((x) => x.classList.toggle("on", x === b)); calc(); };
  $("#kSide").onclick = (e) => { const b = e.target.closest("[data-v]"); if (!b) return; curSide = +b.dataset.v; $$("#kSide button").forEach((x) => x.className = x === b ? `active ${curSide > 0 ? "long" : "short"}` : ""); calc(); };
  const calc = debounce(async () => {
    const s = curSym, sp = specOf(s), dir = curSide, e = parseFloat($("#kEntry").value);
    $("#kSpec").textContent = `${s}: 1 pip = ${sp.pip} · 1 lot = ${sp.contract} หน่วย · ${sp.digits} ตำแหน่ง · ชื่อใน MT5: ${sp.mt5_symbol || s}`;
    const slIn = parseFloat($("#kSl").value), tpIn = parseFloat($("#kTp").value);
    store.set("calc", { symbol: s, side: dir, balance: $("#kBal").value, risk: $("#kRisk").value, entry: $("#kEntry").value, sl: $("#kSl").value, tp: $("#kTp").value, slMode: $("#kSlMode").value, tpMode: $("#kTpMode").value });
    if (!e || !slIn) return;
    const sl = $("#kSlMode").value === "pips" ? e - dir * slIn * sp.pip : slIn;
    if ((e - sl) * dir <= 0) { $("#kOut").innerHTML = `<div class="banner"><span class="ic">⚠️</span><div>SL ต้องอยู่${dir > 0 ? "ต่ำกว่า" : "สูงกว่า"}ราคาเข้า</div></div>`; return; }
    const tp = $("#kTpMode").value === "rr" ? e + dir * Math.abs(e - sl) * (tpIn || 0) : tpIn;
    const r = await api(`/api/size?symbol=${s}&entry=${e}&sl=${sl}&tp=${tp || ""}&risk_pct=${$("#kRisk").value}&balance=${$("#kBal").value}`);
    const span = Math.abs((tp || e) - sl) || 1, pos = (v) => (Math.abs(v - sl) / span * 100).toFixed(1);
    $("#kOut").innerHTML = `<div class="lot-big"><div class="k">LOT ที่ต้องใส่ใน MT5</div><div class="v">${fnum(r.lots, 2)}</div><div class="s">${dir > 0 ? "BUY" : "SELL"} ${esc(sp.mt5_symbol || s)} · เสี่ยงจริง $${fnum(r.actual_risk_usd, 2)} (เป้า $${fnum(r.risk_usd, 2)})</div></div>
      ${tp ? `<div class="ladder"><div class="bar"><i class="sl" style="width:${pos(e)}%"></i><i class="tp" style="left:${pos(e)}%;width:${100 - pos(e)}%"></i></div>
        <div class="lbls"><span class="neg">SL ${fpx(sl, sp.digits)}</span><span>Entry ${fpx(e, sp.digits)}</span><span class="pos">TP ${fpx(tp, sp.digits)}</span></div></div>` : ""}
      <div class="kpis">${kpi("Stop Loss", fnum(r.sl_pips, 1) + " pips", "-$" + fnum(r.actual_risk_usd, 2), "neg")}${kpi("Take Profit", r.tp_pips ? fnum(r.tp_pips, 1) + " pips" : "–", r.reward_usd ? "+$" + fnum(r.reward_usd, 2) : "", "pos")}${kpi("R:R", r.rr ? "1 : " + fnum(r.rr, 2) : "–")}${kpi("$ ต่อจุดต่อ 1 lot", fnum(r.value_per_point_per_lot, 2), r.mt5 ? "จาก MT5" : "ค่ามาตรฐาน")}</div>
      ${r.below_min_lot ? `<div class="banner" style="margin-top:12px"><span class="ic">⚠️</span><div>ต่ำกว่า 0.01 lot — ถ้าเปิด 0.01 lot จะเสี่ยง $${fnum(r.min_lot_risk_usd, 2)} · เพิ่มทุน/ความเสี่ยง หรือขยับ SL ให้ใกล้ขึ้น</div></div>` : ""}
      <div class="hint" style="margin-top:12px">สูตร: Lot = (ทุน × ความเสี่ยง%) ÷ (ระยะ SL × มูลค่าต่อ 1 หน่วยราคาต่อ 1 lot) แล้วปัดลงทีละ 0.01${s.endsWith("JPY") ? " · คู่ JPY แปลงเป็น USD ด้วยการหารราคา USDJPY" : ""}</div>`;
  }, 250);
  $$("#view input, #view select").forEach((i) => i.addEventListener("input", calc));
  calc();
}

// ------------------------------------------------------------------ GUIDE: sticky table of contents + chapters, the contents follow the scroll
async function renderGuide(view) {
  const ch = manualChapters();
  view.innerHTML = `<div class="page guide">
    <nav class="toc" id="toc"><div class="toc-h">📘 คู่มือ TradingLab</div>${ch.map(([id, title]) => `<a href="javascript:void 0" data-go="${id}">${esc(title)}</a>`).join("")}
      <div class="toc-note">ตัวเลขในคู่มือคำนวณจากทุนและความเสี่ยงที่ตั้งไว้ในเครื่องนี้</div></nav>
    <div class="chapters">${ch.map(([id, title, body]) => `<article class="chapter" id="man-${id}"><h2>${esc(title)}</h2><div class="md">${md(body)}</div></article>`).join("")}</div>
  </div>`;
  const links = $$("[data-go]", view);
  links.forEach((b) => b.onclick = () => $(`#man-${b.dataset.go}`).scrollIntoView({ behavior: "smooth", block: "start" }));
  const io = new IntersectionObserver((ents) => {
    ents.forEach((en) => { if (en.isIntersecting) links.forEach((a) => a.classList.toggle("on", "man-" + a.dataset.go === en.target.id)); });
  }, { root: view, rootMargin: "0px 0px -70% 0px" });
  $$(".chapter", view).forEach((c) => io.observe(c));
  links[0]?.classList.add("on");
}

// ------------------------------------------------------------------ updates & full refresh
// The cloud writes version.json on every build: `version` changes only when the web app itself changes
// (code / layout), `last_scan` / `research` change with new data. A new version shows a bar with an update button.
async function hardRefresh() {
  toast("กำลังโหลดใหม่ทั้งหมด…", 1500);
  try { if (window.caches) for (const k of await caches.keys()) await caches.delete(k); } catch { /* no cache storage */ }
  const u = new URL(location.href); u.searchParams.set("r", Date.now());
  location.replace(u.toString());
}
async function checkUpdate() {
  if (!STATIC) return;
  try {
    const v = await (await fetch(`version.json?t=${Date.now()}`, { cache: "no-store" })).json();
    const bar = $("#updateBar");
    if (v.version && STATIC.version && v.version !== STATIC.version) {
      bar.innerHTML = `<div class="update-bar"><span>🆕 <b>มีเว็บเวอร์ชันใหม่</b> (อัปเดต ${fdt(v.built)}) — กดอัปเดตเพื่อใช้ฟีเจอร์ล่าสุด</span><button class="btn sm primary" id="doUpdate">อัปเดตเลย</button></div>`;
    } else if ((v.last_scan && v.last_scan !== STATIC.last_scan) || (v.research && v.research !== STATIC.research_generated)) {
      bar.innerHTML = `<div class="update-bar" style="border-color:rgba(59,130,246,.4);background:var(--accent-bg)"><span>🔄 มีข้อมูลใหม่${v.research !== STATIC.research_generated ? " (ผลวิเคราะห์ระบบใหม่)" : ""} · สแกนล่าสุด ${fdt(v.last_scan)}</span><button class="btn sm" id="doUpdate">โหลดข้อมูลใหม่</button></div>`;
    } else { bar.innerHTML = ""; return; }
    $("#doUpdate").onclick = hardRefresh;
  } catch { /* offline: try again later */ }
}

// ------------------------------------------------------------------ boot
// iPad / iPhone: pinch-zoom is for charts only. A pinch that starts outside a chart (sidebar, cards, the
// bar between panels) is swallowed so the whole web app never zooms; charts handle their own pinch.
function lockPageZoom() {
  const inChart = (e) => !!(e.target && e.target.closest && e.target.closest(".chart"));
  ["gesturestart", "gesturechange", "gestureend"].forEach((t) =>
    document.addEventListener(t, (e) => { if (!inChart(e)) e.preventDefault(); }, { passive: false }));
  document.addEventListener("touchmove", (e) => { if (e.touches.length > 1 && !inChart(e)) e.preventDefault(); }, { passive: false });
  let lastTap = 0;  // double-tap zoom on older iOS that ignores touch-action
  document.addEventListener("touchend", (e) => {
    const now = Date.now();
    if (now - lastTap < 300 && !inChart(e) && !e.target.closest("input,select,textarea,button,a,label")) e.preventDefault();
    lastTap = now;
  }, { passive: false });
}

async function boot() {
  lockPageZoom();
  const theme = store.get("theme", "dark");
  document.documentElement.dataset.theme = theme;
  $("#refreshBtn").onclick = hardRefresh;
  $("#themeBtn").onclick = () => { const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; document.documentElement.dataset.theme = t; store.set("theme", t); navigate(); };
  try {
    [S.symbols, S.strategies, S.settings] = await Promise.all([api("/api/symbols"), api("/api/strategies"), api("/api/settings")]);
    S.symbols = sortSyms(S.symbols);
    await loadHeld();
  } catch (e) { $("#view").innerHTML = `<div class="banner"><span class="ic">⚠️</span><div>${STATIC ? "โหลดข้อมูลไม่ได้" : "เชื่อมต่อเซิร์ฟเวอร์ไม่ได้"}: ${esc(e.message)}</div></div>`; return; }
  await refreshStatus();
  window.addEventListener("hashchange", navigate);
  navigate();
  Sync.pull().then((changed) => { if (changed) { toast("โหลดการตั้งค่าจากเครื่องอื่นแล้ว", 1800); navigate(); } });
  if (STATIC) {  // the cloud build changes once an hour: check for a new web version / new data every 3 minutes and on return to the app
    checkUpdate(); setInterval(checkUpdate, 180000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) checkUpdate(); });
    return;
  }
  setInterval(refreshStatus, 30000);
  setInterval(async () => {
    if (route() !== "signals" || document.hidden) return;
    const sigs = await api("/api/signals?limit=20");
    const n = sigs.filter((s) => s.outcome === "open" && s.user_status === "new" && new Date(s.valid_until) > new Date()).length;
    if (n !== S.newCount) navigate();
  }, 60000);
}
boot();
