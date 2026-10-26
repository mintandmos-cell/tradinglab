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
const S = { symbols: [], strategies: [], settings: null, status: null, lab: { bt: null, wf: null, mc: null }, charts: [] };

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
  set(v) { store.set("personal", { ...local.get(), ...v }); },
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
function lotLabel(sym, entry, sl, sysRisk) {  // lots for an open position on the chart, same maths as the signal cards
  const z = sizeLocal(sym, entry, sl, effRisk(sysRisk), myBalance());
  return z.below_min_lot ? "<0.01 lot" : `${z.lots.toFixed(2)} lot`;
}

async function staticGet(file, text = false) {
  const res = await fetch(`data/${file}?v=${Math.floor(Date.now() / 120000)}`, { cache: "no-cache" });  // the cloud rebuilds hourly
  if (!res.ok) throw new Error(`${file}: ${res.status}`);
  return text ? res.text() : res.json();
}
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
  if (p === "/api/research/notes") return staticGet("notes.md", true);
  if (p === "/api/research/history") return staticGet("history.json");
  if (p === "/api/research/state") return { running: false };
  if (p === "/api/forward") return staticGet("forward.json");
  if (p === "/api/scan") return { new: [] };
  if (p === "/api/candles") return staticGet(`chart/${qp.symbol}_${qp.tf}.json`);
  if (p === "/api/size") return sizeLocal(qp.symbol, +qp.entry, +qp.sl, +qp.risk_pct, +(qp.balance || P.account_balance), qp.tp ? +qp.tp : null);
  if (p.startsWith("/api/signals/") && post) {  // taken / skipped is remembered on this device only
    const marks = store.get("sigMarks", {}); marks[decodeURIComponent(p.slice(13))] = body; store.set("sigMarks", marks); return body;
  }
  if (p === "/api/signals") {
    const marks = store.get("sigMarks", {}), sigs = await staticGet("signals.json");
    return sigs.slice(0, +(qp.limit || 200)).map((s) => {
      const risk = effRisk(s.risk_pct), z = sizeLocal(s.symbol, s.entry, s.sl, risk, P.account_balance, s.tp);
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
const paramText = (p) => (p ? Object.entries(p).filter(([k]) => k !== "max_hold_h").map(([k, v]) => `${k}=${v}`).join(" · ") + (p.max_hold_h ? ` · hold≤${p.max_hold_h}h` : "") : "–");
const kpi = (k, v, s = "", c = "") => `<div class="kpi"><div class="k">${k}</div><div class="v ${c}">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`;
const statusBadge = (s) => `<span class="badge ${esc(s)}">${esc(s || "–")}</span>`;
function qs() { const q = location.hash.split("?")[1] || ""; return Object.fromEntries(new URLSearchParams(q)); }
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
function md(text) {
  const lines = esc(text).split("\n"); let html = "", inList = false;
  for (const l of lines) {
    const inline = (s) => s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`(.+?)`/g, "<code>$1</code>");
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

// ------------------------------------------------------------------ router / shell
const ROUTES = {
  signals: { title: "สัญญาณ", sub: "สัญญาณเข้าเทรดล่าสุด พร้อม Lot / SL / TP สำหรับ MT5", ic: "🔔", render: renderSignals, primary: true },
  systems: { title: "ระบบเทรด", sub: "ผลวิจัย Walk-Forward + Monte Carlo ของแต่ละสินทรัพย์", ic: "🧠", render: renderSystems, primary: true },
  chart: { title: "กราฟ", sub: "แท่งเทียน พร้อมสัญญาณและเทรดของระบบ", ic: "📈", render: renderChart, primary: true },
  lab: { title: "Strategy Lab", sub: "Backtest · Walk-Forward · Monte Carlo", ic: "🧪", render: renderLab, primary: true },
  calc: { title: "เครื่องคำนวณ", sub: "Position size · R:R · SL/TP", ic: "🧮", render: renderCalc },
  settings: { title: "ตั้งค่า", sub: "บัญชี ความเสี่ยง ข้อมูล การแจ้งเตือน", ic: "⚙️", render: renderSettings },
  guide: { title: "คู่มือ", sub: "วิธีใช้งานและเชื่อมต่อมือถือ", ic: "📘", render: renderGuide },
};
function route() { return (location.hash.replace(/^#\/?/, "").split("?")[0] || "signals"); }

function renderNav(newCount = 0) {
  const cur = route();
  const item = (k, r) => `<a href="#/${k}" class="${cur === k ? "active" : ""}"><span class="ic">${r.ic}</span><span class="lbl">${r.title}</span>${k === "signals" && newCount ? `<span class="count">${newCount}</span>` : ""}</a>`;
  $("#nav").innerHTML = `<div class="nav-sec">เทรด</div>${["signals", "systems", "chart"].map((k) => item(k, ROUTES[k])).join("")}
    <div class="nav-sec">วิจัย</div>${["lab", "calc"].map((k) => item(k, ROUTES[k])).join("")}
    <div class="nav-sec">ระบบ</div>${["settings", "guide"].map((k) => item(k, ROUTES[k])).join("")}`;
  const more = ["calc", "settings", "guide"].includes(cur);
  $("#bottomnav").innerHTML = ["signals", "systems", "chart", "lab"].map((k) => {
    const r = ROUTES[k];
    return `<a href="#/${k}" class="${cur === k ? "active" : ""}"><span class="ic">${r.ic}</span>${k === "lab" ? "Lab" : r.title}${k === "signals" && newCount ? `<span class="count">${newCount}</span>` : ""}</a>`;
  }).join("") + `<a href="javascript:void 0" id="moreBtn" class="${more ? "active" : ""}"><span class="ic">☰</span>เพิ่มเติม</a>`;
  $("#moreBtn").onclick = () => {
    const bg = document.createElement("div"); bg.className = "sheet-bg";
    const sh = document.createElement("div"); sh.className = "sheet";
    sh.innerHTML = `<div class="grabber"></div>${["calc", "settings", "guide"].map((k) => `<a href="#/${k}"><span>${ROUTES[k].ic}</span>${ROUTES[k].title}<span class="muted small" style="margin-left:auto">${ROUTES[k].sub}</span></a>`).join("")}`;
    const close = () => { bg.remove(); sh.remove(); };
    bg.onclick = close; sh.onclick = (e) => { if (e.target.closest("a")) close(); };
    document.body.append(bg, sh);
  };
}

async function refreshStatus() {
  try {
    S.status = await api("/api/status");
    const st = S.status;
    const mt5 = st.static ? `<span class="dot on"></span> ☁️ คลาวด์ (สแกนทุกชั่วโมง)`
      : st.mt5.connected ? `<span class="dot on"></span> MT5 ${esc(st.mt5.account?.server || "")}` : `<span class="dot mid"></span> ข้อมูลฟรี (Yahoo/Binance)`;
    const scan = st.scanner.last_scan ? `<span class="dot on"></span> สแกน ${ago(st.scanner.last_scan)}` : `<span class="dot off"></span> ยังไม่สแกน`;
    $("#statusLine").innerHTML = `${mt5} <span class="faint">·</span> ${scan}${st.research.running ? ` <span class="faint">·</span> <span class="spinner"></span> วิจัย ${esc(st.research.progress)}` : ""}`;
    $("#sideFoot").innerHTML = `<div>${mt5}</div><div>${scan}</div><div>วิจัยล่าสุด: ${ago(st.research_generated)}</div>`;
  } catch (e) { $("#statusLine").innerHTML = `<span class="dot off"></span> เชื่อมต่อเซิร์ฟเวอร์ไม่ได้`; }
}

async function navigate() {
  const r = ROUTES[route()] || ROUTES.signals;
  S.charts.forEach((c) => { try { c.remove(); } catch { /* already gone */ } }); S.charts = [];
  $("#pageTitle").textContent = r.title; $("#pageSub").textContent = r.sub;
  document.title = `${r.title} · TradingLab`;
  renderNav(S.newCount || 0);
  window.scrollTo(0, 0);
  const view = $("#view");
  view.innerHTML = `<div class="empty"><div class="spinner"></div></div>`;
  try { await r.render(view); } catch (e) { view.innerHTML = `<div class="banner"><span class="ic">⚠️</span><div>เกิดข้อผิดพลาด: ${esc(e.message)}</div></div>`; console.error(e); }
}

// ------------------------------------------------------------------ SIGNALS
function signalCard(s, compact = false) {
  const sp = specOf(s.symbol), d = sp.digits;
  const expired = new Date(s.valid_until) < new Date();
  const done = s.outcome !== "open" || ["taken", "skipped"].includes(s.user_status);
  const outcome = s.outcome !== "open" ? `<span class="badge ${s.outcome}">${{ win: "TP", loss: "SL", time: "หมดเวลา" }[s.outcome]} ${fr(s.outcome_r)}</span>` : `<span class="badge open">${expired ? "กำลังติดตาม" : "รอเข้า"}</span>`;
  return `<div class="card sig ${s.dir < 0 ? "short" : ""} ${done ? "done" : ""}" data-id="${esc(s.id)}">
    <div class="top">
      <span class="side">${s.side}</span>
      <div><div class="sym">${esc(s.mt5_symbol)}</div><div class="meta">${s.tf} · ${esc(s.strategy_name || stratName(s.strategy))}</div></div>
      <div style="margin-left:auto;text-align:right" class="row">${statusBadge(s.system_status)} ${outcome}</div>
    </div>
    <div class="meta" style="padding:0 16px 8px 18px">สัญญาณ ${fdt(s.bar_time)} · ${expired ? "เลยเวลาเข้าแล้ว" : "เข้าได้ถึง " + fdt(s.valid_until)} · ปิดภายใน ${fdt(s.close_by)}</div>
    <div class="levels">
      <div class="lv"><div class="k">ENTRY ≈</div><div class="v" data-f="entry">${fpx(s.entry, d)}</div><div class="s">${s.price_source}</div></div>
      <div class="lv sl"><div class="k">STOP LOSS</div><div class="v" data-f="sl">${fpx(s.sl, d)}</div><div class="s">${fnum(s.sl_pips, 1)} pips</div></div>
      <div class="lv tp"><div class="k">TAKE PROFIT</div><div class="v" data-f="tp">${fpx(s.tp, d)}</div><div class="s">${fnum(s.tp_pips, 1)} pips</div></div>
    </div>
    <div class="lotbar">
      <div><div class="lbl">LOT (MT5)</div><div class="lot" data-f="lots">${fnum(s.lots, 2)}</div></div>
      <div><div class="lbl">ความเสี่ยง</div><div class="mono" data-f="risk">$${fnum(s.risk_usd, 2)} <span class="muted">(${s.risk_pct}%)</span></div></div>
      <div><div class="lbl">R:R</div><div class="mono">1 : ${fnum(s.rr, 2)}</div></div>
      <div class="hide-sm"><div class="lbl">กำไรเป้า</div><div class="mono pos">$${fnum(s.reward_usd, 2)}</div></div>
    </div>
    ${s.below_min_lot ? `<div class="banner" style="margin:0 16px 12px 18px"><span class="ic">⚠️</span><div>Lot ที่คำนวณได้ ${s.raw_lots ?? "–"} ต่ำกว่าขั้นต่ำ 0.01 —
      ถ้าเปิด 0.01 lot จะเสี่ยง <b>$${fnum(s.min_lot_risk_usd, 2)}${s.min_lot_risk_pct ? ` (${s.min_lot_risk_pct}%)` : ""}</b> มากกว่าที่ตั้งไว้ ${s.risk_pct}% · ข้ามไม้นี้ หรือยอมรับความเสี่ยงที่สูงขึ้นเอง</div></div>` : ""}
    <div class="adj">
      <label class="f" style="flex:1">ราคาจริงใน MT5 ตอนเข้า<input class="inp num" type="number" step="any" placeholder="${fpx(s.entry, d)}"></label>
      <button class="btn sm primary" data-a="recalc">คำนวณใหม่</button>
    </div>
    ${compact ? "" : `<div class="acts">
      <button class="btn sm" data-a="copy">📋 คัดลอก</button>
      <button class="btn sm" data-a="adjust">🎯 ปรับตามราคา MT5</button>
      <a class="btn sm" href="#/chart?symbol=${s.symbol}&tf=${s.tf}">📈 กราฟ</a>
      ${s.user_status === "new" ? `<button class="btn sm long" data-a="taken">✓ เปิดแล้ว</button><button class="btn sm ghost" data-a="skipped">ข้าม</button>` : `<span class="badge ${s.user_status}">${s.user_status === "taken" ? "เปิดแล้ว" : "ข้าม"}</span>`}
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
        const sl = p - s.dir * s.sl_dist, tp = p + s.dir * s.tp_dist;
        const r = await api(`/api/size?symbol=${s.symbol}&entry=${p}&sl=${sl}&tp=${tp}&risk_pct=${s.risk_pct}`);
        $("[data-f=entry]", card).textContent = fpx(p, d); $("[data-f=sl]", card).textContent = fpx(sl, d);
        $("[data-f=tp]", card).textContent = fpx(tp, d); $("[data-f=lots]", card).textContent = fnum(r.lots, 2);
        $("[data-f=risk]", card).innerHTML = `$${fnum(r.actual_risk_usd, 2)} <span class="muted">(${s.risk_pct}%)</span>`;
        toast("คำนวณ SL/TP/Lot ใหม่จากราคา MT5 แล้ว (ระยะ SL/TP เท่าเดิม)");
      } else if (a === "taken" || a === "skipped") {
        await api(`/api/signals/${encodeURIComponent(s.id)}`, { method: "POST", body: { user_status: a } });
        toast(a === "taken" ? "บันทึก: เปิดออเดอร์แล้ว" : "บันทึก: ข้ามสัญญาณนี้"); navigate();
      }
    });
  });
}

async function renderSignals(view) {
  const [sigs, syms, fwd] = await Promise.all([api("/api/signals"), api("/api/symbols"), api("/api/forward")]);
  S.symbols = syms;
  const now = new Date();
  const actionable = sigs.filter((s) => s.outcome === "open" && s.user_status === "new" && new Date(s.valid_until) > now);
  const tracking = sigs.filter((s) => s.outcome === "open" && !actionable.includes(s));
  const closed = sigs.filter((s) => s.outcome !== "open");
  S.newCount = actionable.length; renderNav(S.newCount);
  const active = syms.filter((x) => x.status === "ACTIVE"), watch = syms.filter((x) => x.status === "WATCH");
  const fa = fwd.all;
  view.innerHTML = `<div class="stack">
    ${!active.length ? `<div class="banner"><span class="ic">🧭</span><div><b>ตอนนี้ยังไม่มีระบบไหนผ่านเกณฑ์ ACTIVE</b> — ระบบสถานะ WATCH (${watch.length}) จะถูกติดตามแบบ forward test อัตโนมัติ
      ${S.settings?.alerts?.watch ? "และส่งแจ้งเตือนด้วย" : "แต่ยังไม่ส่งแจ้งเตือน (เปิดได้ในตั้งค่า)"} ใช้บัญชี demo จนกว่าผลจริงจะยืนยัน · <a href="#/systems">ดูเหตุผลที่หน้า ระบบเทรด →</a></div></div>` : ""}
    <div class="card"><div class="hd"><h3>สัญญาณที่เข้าได้ตอนนี้</h3><span class="badge open">${actionable.length}</span>
      <div class="right"><button class="btn sm" id="scanBtn">${STATIC ? "↻ โหลดใหม่" : "↻ สแกนตอนนี้"}</button></div></div>
      <div class="bd">${actionable.length ? `<div class="sig-grid">${actionable.map((s) => signalCard(s)).join("")}</div>`
        : `<div class="empty"><div class="big">🔕</div>ยังไม่มีสัญญาณใหม่ · ${STATIC ? `คลาวด์สแกนทุกชั่วโมง (ล่าสุด ${ago(STATIC.last_scan)})` : `ระบบจะสแกนทุก ${S.settings?.scan_interval_sec || 120} วินาที`} และแจ้งเตือนบนมือถือเมื่อมีสัญญาณ</div>`}</div></div>
    <div class="grid g2">
      <div class="card"><div class="hd"><h3>ระบบที่ใช้อยู่ในแต่ละสินทรัพย์</h3><div class="right"><a class="btn sm ghost" href="#/systems">รายละเอียด →</a></div></div>
        <div class="tbl-wrap"><table class="tbl"><thead><tr><th>สินทรัพย์</th><th>สถานะ</th><th>ระบบ</th><th>TF</th><th class="num">Risk</th><th class="num">OOS Exp</th><th class="num">Robust</th></tr></thead><tbody>
        ${syms.map((x) => `<tr class="click" onclick="location.hash='#/chart?symbol=${x.symbol}&tf=${x.tf || "H1"}'"><td><b>${x.symbol}</b> <span class="faint small">${esc(x.mt5_symbol)}</span></td><td>${statusBadge(x.status)}</td>
          <td class="small">${esc(x.strategy_name || "–")}</td><td>${x.tf || "–"}</td><td class="num">${x.risk_pct ? effRisk(x.risk_pct) + "%" : "–"}</td>
          <td class="num ${cls(x.oos?.expectancy)}">${x.oos ? fr(x.oos.expectancy, 3) : "–"}</td>
          <td class="num ${x.robust_score >= 80 ? "pos" : x.robust_score >= 60 ? "warn" : x.robust_score != null ? "neg" : ""}">${x.robust_score ?? "–"}</td></tr>`).join("")}</tbody></table></div></div>
      <div class="card"><div class="hd"><h3>Forward test (ผลจริงหลังสัญญาณ)</h3></div><div class="bd stack">
        ${fa ? `<div class="kpis">${kpi("สัญญาณที่จบแล้ว", fa.trades)}${kpi("รวม", fr(fa.net_r, 1), "", cls(fa.net_r))}${kpi("Expectancy", fr(fa.expectancy, 3), "", cls(fa.expectancy))}${kpi("Win rate", fa.win_rate + "%")}${kpi("PF", fnum(fa.pf, 2))}${kpi("Max DD", fnum(fa.max_dd_r, 1) + "R")}</div>`
          : `<div class="hint">ยังไม่มีสัญญาณที่จบ — ทุกสัญญาณจะถูกติดตามด้วยกฎเดียวกับ backtest (SL/TP/หมดเวลา) เพื่อเทียบผลจริงกับผลวิจัย</div>`}
        ${fwd.taken ? `<div class="hint">ไม้ที่คุณเปิดจริง: ${fwd.taken.trades} ไม้ · ${fr(fwd.taken.net_r, 1)}</div>` : ""}
      </div></div>
    </div>
    ${tracking.length ? `<div class="card"><div class="hd"><h3>กำลังติดตาม</h3><span class="badge">${tracking.length}</span></div><div class="bd"><div class="sig-grid">${tracking.map((s) => signalCard(s)).join("")}</div></div></div>` : ""}
    <div class="card"><div class="hd"><h3>ประวัติสัญญาณ</h3><span class="badge">${closed.length}</span></div>
      <div class="tbl-wrap tbl-scroll"><table class="tbl"><thead><tr><th>เวลา</th><th>สินทรัพย์</th><th>ฝั่ง</th><th>TF</th><th>ระบบ</th><th class="num">Lot</th><th>ผล</th><th class="num">R</th><th>คุณ</th></tr></thead><tbody>
      ${closed.length ? closed.map((s) => `<tr><td>${fdt(s.bar_time)}</td><td><b>${s.symbol}</b></td><td>${statusBadge(s.side)}</td><td>${s.tf}</td><td class="small">${esc(s.strategy_name || "")} ${statusBadge(s.system_status)}</td>
        <td class="num">${fnum(s.lots, 2)}</td><td><span class="badge ${s.outcome}">${{ win: "TP", loss: "SL", time: "Time" }[s.outcome]}</span></td><td class="num ${cls(s.outcome_r)}">${fr(s.outcome_r)}</td><td>${s.user_status === "new" ? '<span class="faint">–</span>' : `<span class="badge ${s.user_status}">${s.user_status === "taken" ? "เปิด" : "ข้าม"}</span>`}</td></tr>`).join("")
        : `<tr><td colspan="9" class="muted" style="text-align:center;padding:22px">ยังไม่มีประวัติ</td></tr>`}</tbody></table></div></div>
  </div>`;
  bindSignalCards(view, sigs);
  $("#scanBtn").onclick = async (e) => { if (STATIC) { location.reload(); return; } e.target.disabled = true; const r = await api("/api/scan", { method: "POST" }); toast(r.new.length ? `พบ ${r.new.length} สัญญาณใหม่` : "สแกนแล้ว ไม่มีสัญญาณใหม่"); navigate(); };
}

// ------------------------------------------------------------------ SYSTEMS (research)
function gateChips(g) { return `<div class="gates">${(g || []).map((x) => `<span class="gate ${x.pass ? "p" : "f"}" title="ต้องการ ${x.need}">${x.pass ? "✓" : "✗"} ${esc(x.name)} ${x.value === null || x.value === undefined ? "–" : fnum(x.value, typeof x.value === "number" && Math.abs(x.value) < 10 ? 2 : 0)}</span>`).join("")}</div>`; }

async function renderSystems(view) {
  const [res, notes, hist] = await Promise.all([api("/api/research"), api("/api/research/notes"), api("/api/research/history")]);
  const st = S.status?.research || {};
  const syms = Object.values(res.symbols || {});
  const counts = { ACTIVE: 0, WATCH: 0, OFF: 0 }; syms.forEach((s) => counts[s.status] = (counts[s.status] || 0) + 1);
  view.innerHTML = `<div class="stack">
    <div class="card"><div class="bd row" style="gap:16px">
      <div class="kpis" style="flex:1;min-width:280px">${kpi("ACTIVE", counts.ACTIVE, "แจ้งเตือนจริง", "pos")}${kpi("WATCH", counts.WATCH, "forward test")}${kpi("OFF", counts.OFF, "ไม่มี edge", "neg")}${kpi("วิจัยล่าสุด", res.generated ? ago(res.generated) : "ยังไม่เคย", res.generated ? fdt(res.generated, true) : "")}${kpi("รอบอัปเดต", S.settings?.research?.schedule || "–", "IS " + (S.settings?.research?.is_months || "-") + "m / OOS " + (S.settings?.research?.oos_months || "-") + "m")}</div>
      <div class="stack" style="gap:8px">${STATIC
        ? `<span class="hint"><b>✅ วิเคราะห์ใหม่เองอัตโนมัติทุกวันเสาร์ 09:30 น.</b> — ไม่ต้องกดอะไร<br>สแกนหาสัญญาณเองทุกชั่วโมง</span>
           <a class="btn" href="${actionsUrl()}" target="_blank" rel="noopener">▶️ สั่งวิเคราะห์ใหม่ทันที (ไม่บังคับ)</a>
           <span class="hint">ต้อง <b>Sign in</b> GitHub ก่อน จึงจะเห็นปุ่ม <b>Run workflow</b> → เลือก research → Run · รอ ~5–10 นาที · ดูวิธีในหน้า <a href="#/guide">คู่มือ</a></span>`
        : `<button class="btn primary" id="runResearch" ${st.running ? "disabled" : ""}>${st.running ? `<span class="spinner"></span> กำลังวิจัย…` : "🤖 วิเคราะห์อัตโนมัติใหม่ทั้งหมด"}</button>
           <span class="hint" id="resProg">${st.running ? esc(st.progress) : "WF + Robustness + Monte Carlo · ~2–4 นาที"}</span>`}</div>
    </div></div>
    <div class="banner info"><span class="ic">ℹ️</span><div><b>วิธีตัดสิน:</b> ทุกสินทรัพย์ถูกทดสอบ ${S.strategies.length} กลยุทธ์ × ${(S.settings?.research?.timeframes || []).join("/")} ด้วย Walk-Forward แบบ rolling
      (เลือกพารามิเตอร์จาก In-Sample แล้วเทรดจริงใน Out-of-Sample ที่ระบบไม่เคยเห็น) → เลือกตัวที่ OOS ดีที่สุด → ทดสอบกับการสุ่มจุดเข้า,
      <b>Robustness</b> 8 แบบ (หน้าต่าง WF อื่น, ต้นทุน ×2, เข้าช้า 1 แท่ง, noise ในราคา, Long/Short, รายครึ่งปี, ตัดไม้ที่ดีที่สุด, พารามิเตอร์ข้างเคียง) และ Monte Carlo — ทุกค่าตั้งอัตโนมัติ ไม่ต้องปรับเอง
      · <b>ACTIVE</b> = ผ่านทุกเกณฑ์ · <b>WATCH</b> = OOS เป็นบวกแต่ยังพิสูจน์ไม่ได้ · <b>OFF</b> = ไม่พบ edge → ไม่เทรด</div></div>
    <div class="sys-grid">${syms.length ? syms.map((s) => systemCard(s)).join("") : `<div class="card"><div class="empty"><div class="big">🧪</div>ยังไม่มีผลวิจัย — กด “รันวิจัยใหม่ทั้งหมด”</div></div>`}</div>
    <div class="card"><div class="hd"><h3>บันทึกจาก AI (Claude)</h3><span class="muted small">data/research/ai_notes.md</span></div><div class="bd md">${notes ? md(notes) : '<span class="hint">ยังไม่มีบันทึก — เมื่อ Claude ตรวจผลวิจัยรอบถัดไป จะเขียนข้อสังเกตและการปรับปรุงไว้ที่นี่</span>'}</div></div>
    <div class="card"><div class="hd"><h3>ประวัติการอัปเดตระบบ</h3></div><div class="tbl-wrap tbl-scroll"><table class="tbl"><thead><tr><th>เวลา</th>${Object.keys(res.symbols || {}).map((k) => `<th>${k}</th>`).join("")}</tr></thead><tbody>
      ${hist.map((h) => `<tr><td>${fdt(h.generated, true)}</td>${Object.keys(res.symbols || {}).map((k) => { const v = h.symbols[k]; return `<td>${v ? `${statusBadge(v.status)} <span class="small muted">${v.tf || ""}</span>` : "–"}</td>`; }).join("")}</tr>`).join("") || '<tr><td class="muted">–</td></tr>'}</tbody></table></div></div>
  </div>`;
  if (!STATIC) $("#runResearch").onclick = async () => { await api("/api/research/run", { method: "POST", body: {} }); toast("เริ่มวิจัยแล้ว"); pollResearch(); navigate(); };
  $$(".sys [data-a=detail]", view).forEach((b) => b.onclick = () => toggleDetail(b.closest(".sys"), res.symbols[b.dataset.sym]));
  if (st.running) pollResearch();
}

function systemCard(s) {
  const o = s.oos || {};
  const rb = s.random_benchmark;
  return `<div class="card sys" data-sym="${s.symbol}"><div class="hd"><span class="sym">${s.symbol}</span>${statusBadge(s.status)}
      <div class="right"><span class="small muted">${s.tf || ""}</span></div></div>
    <div class="bd stack" style="gap:12px">
      ${s.strategy ? `<div><div style="font-weight:650">${esc(s.strategy_name)}</div><div class="small muted mono" style="margin-top:3px">${esc(paramText(s.params))}</div></div>` : `<div class="hint">${esc(s.reason || "")}</div>`}
      <div class="kpis" style="grid-template-columns:repeat(3,1fr)">
        ${kpi("OOS trades", o.trades ?? "–", (o.trades_per_year ?? "–") + "/ปี")}${kpi("Expectancy", fr(o.expectancy, 3), "t = " + fnum(o.t_stat, 2), cls(o.expectancy))}${kpi("Risk แนะนำ", s.risk_pct ? s.risk_pct + "%" : "–", "MC DD95 ≤ " + (S.settings?.target_max_dd_pct || 10) + "%")}
        ${kpi("Profit factor", fnum(o.pf, 2), "MC@95%: " + fnum(s.mc_pf_p5, 2))}${kpi("Win rate", (o.win_rate ?? "–") + "%", "R:R " + (s.params?.rr ?? "–"))}${kpi("vs สุ่ม", rb ? rb.percentile + "%" : "–", "percentile")}
      </div>
      ${robustBar(s.robustness)}
      ${gateChips(s.gates)}
      <div class="row small muted">ข้อมูล ${esc(s.data_source || "")} · ${s.data_from ? fdt(s.data_from, true).split(",")[0] : ""} → ${s.data_to ? fdt(s.data_to, true).split(",")[0] : ""} · ทดสอบทั้งหมด ${s.trials || 0} ชุด</div>
      ${s.previous && (s.previous.strategy !== s.strategy || s.previous.tf !== s.tf || s.previous.status !== s.status) ? `<div class="hint">เปลี่ยนจากรอบก่อน: ${statusBadge(s.previous.status)} ${esc(stratName(s.previous.strategy))} ${s.previous.tf || ""}</div>` : ""}
      <div class="row"><button class="btn sm" data-a="detail" data-sym="${s.symbol}">รายละเอียด ▾</button>
        ${s.strategy ? `<a class="btn sm" href="#/lab?symbol=${s.symbol}&tf=${s.tf}&strategy=${s.strategy}&params=${encodeURIComponent(JSON.stringify(s.params || {}))}">🧪 เปิดใน Lab</a><a class="btn sm ghost" href="#/chart?symbol=${s.symbol}&tf=${s.tf}">📈</a>` : ""}</div>
      <div class="detail hidden"></div>
    </div></div>`;
}

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

function toggleDetail(card, s) {
  const box = $(".detail", card);
  if (!box.classList.contains("hidden")) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  const d = specOf(s.symbol).digits;
  box.innerHTML = `<div class="formsec">OOS equity (risk 1%/ไม้, เริ่ม 10,000)</div><div class="chart sm" id="eq-${s.symbol}"></div>
    ${robustTable(s.robustness)}
    <div class="formsec">ผู้สมัครทั้งหมด (Walk-Forward OOS)</div>
    <div class="tbl-wrap"><table class="tbl"><thead><tr><th>กลยุทธ์</th><th>TF</th><th class="num">n</th><th class="num">Exp</th><th class="num">t</th><th class="num">PF</th><th class="num">WFE</th><th class="num">Plateau</th></tr></thead><tbody>
    ${(s.candidates || []).slice().sort((a, b) => b.oos.t_stat - a.oos.t_stat).map((c) => `<tr><td class="small">${esc(c.name)}</td><td>${c.tf}</td><td class="num">${c.oos.trades}</td><td class="num ${cls(c.oos.expectancy)}">${fnum(c.oos.expectancy, 3)}</td><td class="num">${fnum(c.oos.t_stat, 2)}</td><td class="num">${fnum(c.oos.pf, 2)}</td><td class="num">${c.wfe ?? "–"}</td><td class="num">${fnum(c.plateau, 2)}</td></tr>`).join("")}</tbody></table></div>
    <div class="formsec">Walk-forward folds ของระบบที่เลือก</div>
    <div class="tbl-wrap tbl-scroll"><table class="tbl"><thead><tr><th>OOS</th><th>พารามิเตอร์ที่เลือก (IS)</th><th class="num">IS t</th><th class="num">OOS n</th><th class="num">OOS R</th></tr></thead><tbody>
    ${(s.folds || []).map((f) => `<tr><td>${f.oos_start.slice(0, 7)}</td><td class="small mono">${f.params ? esc(paramText(f.params)) : '<span class="faint">ไม่เทรด (IS ไม่เป็นบวก)</span>'}</td><td class="num">${f.is_t ?? "–"}</td><td class="num">${f.oos_trades}</td><td class="num ${cls(f.oos_r)}">${fnum(f.oos_r, 2)}</td></tr>`).join("")}</tbody></table></div>
    ${s.mc_1y?.max_dd_pct ? `<div class="formsec">Monte Carlo 1 ปี (${s.mc_1y.n_trades} ไม้, risk 1%)</div><div class="kpis">${kpi("ผลตอบแทนกลาง", fnum(s.mc_1y.return_pct["50"], 1) + "%")}${kpi("แย่ 5%", fnum(s.mc_1y.return_pct["5"], 1) + "%", "", "neg")}${kpi("DD กลาง", fnum(s.mc_1y.max_dd_pct["50"], 1) + "%")}${kpi("DD 95%", fnum(s.mc_1y.max_dd_pct["95"], 1) + "%", "", "neg")}${kpi("โอกาสกำไร", s.mc_1y.prob_profit + "%")}</div>` : ""}`;
  if (s.oos_equity?.length) lineChart($(`#eq-${s.symbol}`), [{ data: dedupeTimes(s.oos_equity.map((p) => ({ time: shiftT(p.time), value: p.value }))), color: "#60a5fa", last: true }]);
  void d;
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
  // the system's own running position (exact backtest rules) first, else a fresh signal waiting for its entry bar
  if (d.open_trade) return { ...d.open_trade, from: d.open_trade.entry_time, pending: false };
  const s = openSigs[0];
  return s ? { dir: s.dir, entry: s.entry, sl: s.sl, tp: s.tp, from: Math.floor(new Date(s.bar_time).getTime() / 1000), close_by: Math.floor(new Date(s.close_by).getTime() / 1000), pending: true } : null;
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
    [[t.entry, "rgba(96,165,250,.55)", 2], [t.sl, "rgba(239,68,68,.55)", 0], [t.tp, "rgba(22,199,132,.55)", 0]].forEach(([v, color, style]) =>
      line(color, style, 1).setData(times.slice(a, b + 1).map((x) => ({ time: shiftT(x), value: v }))));
  });
  (d.trades || []).forEach((t) => {
    if (tset.has(t.entry_time)) markers.push({ time: shiftT(t.entry_time), position: t.dir > 0 ? "belowBar" : "aboveBar", color: t.dir > 0 ? "#16c784" : "#ef4444", shape: t.dir > 0 ? "arrowUp" : "arrowDown", text: t.dir > 0 ? "L" : "S" });
    if (tset.has(t.exit_time)) markers.push({ time: shiftT(t.exit_time), position: t.dir > 0 ? "aboveBar" : "belowBar", color: t.r > 0 ? "#60a5fa" : "#f5a524", shape: "circle", text: `${t.reason} ${t.r > 0 ? "+" : ""}${t.r.toFixed(1)}R` });
  });
  if (open) {
    // running position: Entry / SL / TP from the entry candle to the latest candle, sticking out to the right with labels;
    // every new candle makes them longer until the trade hits SL, TP or its time limit
    const a = Math.min(idx(open.from), n - 1), pts = [];
    for (let i = a; i < n; i++) pts.push(times[i]);
    for (let j = 1; j <= 6; j++) pts.push(times[n - 1] + j * step);
    const lv = [["ENTRY", open.entry, "#60a5fa", 2, ` · ${open.lot}`], ["SL", open.sl, "#ef4444", 0, ""], ["TP", open.tp, "#16c784", 0, ""]];
    lv.forEach(([title, v, color, style, extra]) => line(color, style, 2, `${title} ${fpx(v, d.digits)}${extra}`).setData(pts.map((t) => ({ time: shiftT(t), value: v }))));
    const side = open.dir > 0 ? "BUY" : "SELL";
    markers.push({ time: shiftT(times[a]), position: open.dir > 0 ? "belowBar" : "aboveBar", color: open.dir > 0 ? "#16c784" : "#ef4444",
      shape: open.dir > 0 ? "arrowUp" : "arrowDown", text: `${open.pending ? "สัญญาณ " : ""}${side} ${open.lot} @ ${fpx(open.entry, d.digits)}` });
  }
  return markers;
}

async function renderChart(view) {
  const token = S.chartToken = (S.chartToken || 0) + 1;
  const q = qs();
  const sym = q.symbol || store.get("chartSym", "USDJPY"), tf = q.tf || store.get("chartTf", "H1");
  const overlay = store.get("chartOverlay", true);
  store.set("chartSym", sym); store.set("chartTf", tf);
  view.innerHTML = `<div class="stack">
    <div class="row" style="justify-content:space-between">
      <div class="chips" id="symChips">${S.symbols.map((x) => `<button class="chip ${x.symbol === sym ? "active" : ""}" data-s="${x.symbol}">${x.symbol} ${x.status === "ACTIVE" ? '<span class="dot on"></span>' : x.status === "WATCH" ? '<span class="dot mid"></span>' : ""}</button>`).join("")}</div>
      <div class="row"><div class="seg" id="tfSeg">${["H1", "H4", "D1"].map((t) => `<button class="${t === tf ? "active" : ""}" data-t="${t}">${t}</button>`).join("")}</div>
      <label class="check small"><input type="checkbox" id="ovl" ${overlay ? "checked" : ""}> ระบบ</label></div>
    </div>
    <div class="card"><div class="hd"><h3 id="chTitle">${sym} · ${tf}</h3><div class="right legend" id="chLegend"></div></div><div class="chart" id="mainChart"></div></div>
    <div class="grid g2"><div class="card"><div class="hd"><h3>ระบบบนกราฟนี้</h3></div><div class="bd" id="chInfo"><span class="spinner"></span></div></div>
      <div class="card"><div class="hd"><h3>เทรดล่าสุดของระบบ</h3></div><div class="tbl-wrap tbl-scroll" id="chTrades"></div></div></div>
  </div>`;
  $("#symChips").onclick = (e) => { const b = e.target.closest("[data-s]"); if (b) location.hash = `#/chart?symbol=${b.dataset.s}&tf=${tf}`; };
  $("#tfSeg").onclick = (e) => { const b = e.target.closest("[data-t]"); if (b) location.hash = `#/chart?symbol=${sym}&tf=${b.dataset.t}`; };
  $("#ovl").onchange = (e) => { store.set("chartOverlay", e.target.checked); navigate(); };

  const sys = S.symbols.find((x) => x.symbol === sym);
  let url = `/api/candles?symbol=${sym}&tf=${tf}&limit=1500`;
  if (overlay && sys?.strategy && sys.tf !== tf) url += `&strategy=${sys.strategy}&params=${encodeURIComponent(JSON.stringify(sys.params))}`;
  if (!overlay) url += "&system=false";
  let ch = null, d = null;
  const draw = async (keepRange) => {
    const [dd, sigs] = await Promise.all([api(url), api("/api/signals?limit=50")]);
    if (route() !== "chart" || S.chartToken !== token || !$("#mainChart")) return;
    const range = keepRange && ch ? ch.timeScale().getVisibleLogicalRange() : null;
    if (ch) { S.charts = S.charts.filter((c) => c !== ch); ch.remove(); }
    d = dd;
    ch = makeChart($("#mainChart"), { localization: { priceFormatter: (p) => p.toFixed(d.digits) } });
    const cs = ch.addCandlestickSeries({ upColor: "#16c784", downColor: "#ef4444", borderVisible: false, wickUpColor: "#16c784", wickDownColor: "#ef4444",
      priceFormat: { type: "price", precision: d.digits, minMove: 1 / 10 ** d.digits } });
    cs.setData(d.candles.map((c) => ({ ...c, time: shiftT(c.time) })));
    const legend = [];
    Object.entries(d.lines || {}).forEach(([name, pts], i) => {
      const color = LINE_COLORS[i % 5];
      const ls = ch.addLineSeries({ color, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      ls.setData(pts.map((p) => ({ ...p, time: shiftT(p.time) })));
      legend.push(`<span><i style="background:${color}"></i>${esc(name)}</span>`);
    });
    const open = openPosition(d, sigs.filter((s) => s.symbol === sym && s.tf === tf && s.outcome === "open"));
    if (open) open.lot = lotLabel(sym, open.entry, open.sl, sys?.risk_pct);
    const markers = drawTradeLevels(ch, d, open);
    S.chartOpen = open;
    markers.sort((a, b) => a.time - b.time); cs.setMarkers(markers);
    if (open) legend.push(`<span class="badge open">${open.dir > 0 ? "LONG" : "SHORT"} ${open.lot} · ${fpx(open.entry, d.digits)}</span>`);
    const n = d.candles.length;
    ch.timeScale().setVisibleLogicalRange(range || { from: Math.max(0, n - 220), to: n + 8 });
    $("#chLegend").innerHTML = legend.join("") + `<span class="faint">${d.source}</span>`;
    $("#chTitle").textContent = `${sym} · ${tf} · ${fpx(d.candles[n - 1]?.close, d.digits)}`;
  };
  await draw(false);
  // keep the chart moving: new candles extend the open position's lines until SL / TP / time exit
  const timer = setInterval(() => { if (route() !== "chart" || S.chartToken !== token) return clearInterval(timer); if (!document.hidden) draw(true).catch(() => {}); },
    STATIC ? 300000 : 60000);
  $("#chInfo").innerHTML = d.system ? `<div class="kv"><span class="k">ระบบ</span><span class="v">${esc(d.system.name)}</span><span class="k">พารามิเตอร์</span><span class="v small">${esc(paramText(d.system.params))}</span>
      <span class="k">สถานะวิจัย</span><span class="v">${statusBadge(sys?.status)} ${sys?.tf || ""}</span><span class="k">สัญญาณแท่งล่าสุด</span><span class="v">${d.last_signal > 0 ? '<span class="pos">LONG</span>' : d.last_signal < 0 ? '<span class="neg">SHORT</span>' : "ไม่มี"}</span>
      ${S.chartOpen ? `<span class="k">ออเดอร์ที่เปิดอยู่</span><span class="v">${S.chartOpen.dir > 0 ? '<span class="pos">LONG</span>' : '<span class="neg">SHORT</span>'} <b>${S.chartOpen.lot}</b> @ ${fpx(S.chartOpen.entry, d.digits)}
        · SL <span class="neg">${fpx(S.chartOpen.sl, d.digits)}</span> · TP <span class="pos">${fpx(S.chartOpen.tp, d.digits)}</span>${S.chartOpen.close_by ? ` · ปิดภายใน ${fdt(S.chartOpen.close_by)}` : ""}</span>` : ""}</div>
      ${sys?.tf && sys.tf !== tf ? `<div class="hint" style="margin-top:10px">ระบบจริงของ ${sym} ใช้ TF ${sys.tf} — กราฟนี้แสดงการทดลองบน ${tf}</div>` : ""}`
    : `<div class="hint">${overlay ? "สินทรัพย์นี้ยังไม่มีระบบจากการวิจัย" : "ปิดการแสดงระบบอยู่"}</div>`;
  const tr = (d.trades || []).slice(-30).reverse();
  $("#chTrades").innerHTML = `<table class="tbl"><thead><tr><th>เข้า</th><th>ฝั่ง</th><th class="num">Entry</th><th class="num">Exit</th><th>ออก</th><th class="num">R</th></tr></thead><tbody>
    ${tr.map((t) => `<tr><td>${fdt(t.entry_time)}</td><td>${t.dir > 0 ? '<span class="pos">L</span>' : '<span class="neg">S</span>'}</td><td class="num">${fpx(t.entry, d.digits)}</td><td class="num">${fpx(t.exit, d.digits)}</td><td>${t.reason}</td><td class="num ${cls(t.r)}">${fr(t.r)}</td></tr>`).join("") || '<tr><td colspan="6" class="muted">–</td></tr>'}</tbody></table>`;
}

// ------------------------------------------------------------------ LAB
function paramInputs(strat, values) {
  return strat.params.map((p) => `<label class="f">${esc(p.label)}<input class="inp num" type="number" data-p="${p.key}" value="${values?.[p.key] ?? p.default}" step="${p.step}" min="${p.min}" max="${p.max}"></label>`).join("");
}

async function renderLab(view) {
  if (STATIC) {
    view.innerHTML = `<div class="stack">
      <div class="card"><div class="bd stack">
        <div style="font-size:18px;font-weight:700">🤖 วิเคราะห์อัตโนมัติ — ไม่ต้องตั้งค่าเอง</div>
        <div class="md">${md(`ทุกสินทรัพย์ถูกวิเคราะห์ครบชุดโดยอัตโนมัติ:
- **Backtest + Walk-Forward** ทุกกลยุทธ์ × H1/H4 × ทุกชุดพารามิเตอร์ (IS 12 เดือน / OOS 3 เดือน)
- **Robustness 8 แบบ** รวมถึงทดสอบหน้าต่าง WF อีก 4 ขนาด (6/2, 9/3, 18/3, 24/6 เดือน)
- **สุ่มจุดเข้า 300 รอบ** และ **Monte Carlo 5,000 รอบ** → คำนวณความเสี่ยงต่อไม้ให้ DD 95% ไม่เกินเป้า
- รันเองทุก **วันเสาร์ 09:30 น.** โดยไม่ต้องกดอะไร · ถ้าอยากสั่งทันที: Sign in GitHub → **Run workflow** → research`)}</div>
        <div class="row"><a class="btn primary" href="#/systems">ดูผลวิเคราะห์ →</a><a class="btn" href="${actionsUrl()}" target="_blank" rel="noopener">▶️ สั่งวิเคราะห์ใหม่ทันที</a></div>
        <div class="hint">การทดลองตั้งค่าเอง (Backtest / WF / MC แบบกำหนดพารามิเตอร์) ใช้ได้เมื่อเปิด <code>start_server.bat</code> บนคอม</div>
      </div></div></div>`;
    return;
  }
  const q = qs();
  const saved = store.get("lab", {});
  const sym = q.symbol || saved.symbol || "USDJPY", tf = q.tf || saved.tf || "H4", sid = q.strategy || saved.strategy || S.strategies[0].id;
  const params = q.params ? JSON.parse(q.params) : saved.symbol === sym && saved.strategy === sid ? saved.params : null;
  const sp = specOf(sym);
  const R = S.settings.research;
  view.innerHTML = `<div class="grid" style="grid-template-columns:minmax(0,360px) minmax(0,1fr);align-items:start" id="labGrid">
    <div class="card"><div class="hd"><h3>ตั้งค่าการทดสอบ</h3></div><div class="bd stack" style="gap:12px">
      <button class="btn primary" id="bAuto">🤖 วิเคราะห์อัตโนมัติ (ปุ่มเดียว)</button>
      <div class="hint">ระบบเลือกกลยุทธ์ / TF / พารามิเตอร์ / หน้าต่าง WF / Robustness / Monte Carlo / ความเสี่ยงให้เองสำหรับสินทรัพย์ที่เลือก แล้วอัปเดตระบบเทรด · ด้านล่างสำหรับทดลองเอง</div>
      <div class="fgrid" style="grid-template-columns:1fr 1fr">
        <label class="f">สินทรัพย์<select class="inp" id="lSym">${S.symbols.map((x) => `<option ${x.symbol === sym ? "selected" : ""}>${x.symbol}</option>`).join("")}</select></label>
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
  $("#bAuto").onclick = async (e) => {
    const sym = $("#lSym").value, btn = e.currentTarget;
    const r = await api("/api/research/run", { method: "POST", body: { symbols: [sym] } });
    if (!r.started) return toast("มีการวิเคราะห์กำลังรันอยู่");
    btn.disabled = true;
    const tick = async () => {
      const st = await api("/api/research/state");
      if (st.running) { btn.innerHTML = `<span class="spinner"></span> ${esc(st.progress || "กำลังวิเคราะห์…")}`; return setTimeout(tick, 2000); }
      S.symbols = await api("/api/symbols"); toast(`วิเคราะห์ ${sym} เสร็จแล้ว`); location.hash = "#/systems";
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
    ${tr.map((t) => `<tr><td>${fdt(t.entry_time, true)}</td>${fold ? `<td>${t.fold + 1}</td>` : ""}<td>${t.dir > 0 ? '<span class="pos">LONG</span>' : '<span class="neg">SHORT</span>'}</td><td class="num">${fpx(t.entry, digits)}</td><td class="num">${fpx(t.sl, digits)}</td><td class="num">${fpx(t.tp, digits)}</td><td class="num">${fpx(t.exit, digits)}</td><td>${t.reason}</td><td class="num">${t.bars}</td><td class="num ${cls(t.r)}">${fr(t.r)}</td></tr>`).join("")}</tbody></table></div>`;
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
      ${r.current_params ? `<div class="small mono muted">ใช้ต่อ (12 เดือนล่าสุด): ${esc(paramText(r.current_params))}</div>` : ""}</div></div>
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

// ------------------------------------------------------------------ CALCULATOR
async function renderCalc(view) {
  const saved = store.get("calc", {});
  const sym = saved.symbol || "USDJPY";
  view.innerHTML = `<div class="grid g2" style="align-items:start">
    <div class="card"><div class="hd"><h3>Position size · R:R</h3></div><div class="bd stack" style="gap:12px">
      <div class="fgrid" style="grid-template-columns:1fr 1fr">
        <label class="f">สินทรัพย์<select class="inp" id="kSym">${S.symbols.map((x) => `<option ${x.symbol === sym ? "selected" : ""}>${x.symbol}</option>`).join("")}</select></label>
        <label class="f">ฝั่ง<select class="inp" id="kSide"><option value="1">LONG (Buy)</option><option value="-1" ${saved.side == -1 ? "selected" : ""}>SHORT (Sell)</option></select></label>
        <label class="f">ทุน (USD)<input class="inp num" type="number" id="kBal" value="${saved.balance ?? S.settings.account_balance}"></label>
        <label class="f">ความเสี่ยง %<input class="inp num" type="number" step="0.05" id="kRisk" value="${saved.risk ?? S.settings.default_risk_pct}"></label>
        <label class="f">ราคาเข้า (Entry)<input class="inp num" type="number" step="any" id="kEntry" value="${saved.entry ?? ""}"></label>
        <label class="f">SL แบบ<select class="inp" id="kSlMode"><option value="price">ราคา</option><option value="pips" ${saved.slMode === "pips" ? "selected" : ""}>ระยะ pips</option></select></label>
        <label class="f">Stop Loss<input class="inp num" type="number" step="any" id="kSl" value="${saved.sl ?? ""}"></label>
        <label class="f">TP แบบ<select class="inp" id="kTpMode"><option value="rr">R:R</option><option value="price" ${saved.tpMode === "price" ? "selected" : ""}>ราคา</option></select></label>
        <label class="f">Take Profit<input class="inp num" type="number" step="any" id="kTp" value="${saved.tp ?? 2}"></label>
      </div>
      <div class="hint" id="kSpec"></div>
    </div></div>
    <div class="card"><div class="hd"><h3>ผลลัพธ์สำหรับ MT5</h3></div><div class="bd stack" id="kOut"><div class="hint">กรอกราคาเข้าและ SL</div></div></div></div>`;
  const calc = debounce(async () => {
    const s = $("#kSym").value, sp = specOf(s), dir = +$("#kSide").value, e = parseFloat($("#kEntry").value);
    $("#kSpec").textContent = `${s}: 1 pip = ${sp.pip} · contract ${sp.contract} · ${sp.digits} digits · MT5: ${sp.mt5_symbol}`;
    const slIn = parseFloat($("#kSl").value), tpIn = parseFloat($("#kTp").value);
    store.set("calc", { symbol: s, side: dir, balance: $("#kBal").value, risk: $("#kRisk").value, entry: $("#kEntry").value, sl: $("#kSl").value, tp: $("#kTp").value, slMode: $("#kSlMode").value, tpMode: $("#kTpMode").value });
    if (!e || !slIn) return;
    const sl = $("#kSlMode").value === "pips" ? e - dir * slIn * sp.pip : slIn;
    if ((e - sl) * dir <= 0) { $("#kOut").innerHTML = `<div class="banner"><span class="ic">⚠️</span><div>SL ต้องอยู่${dir > 0 ? "ต่ำกว่า" : "สูงกว่า"}ราคาเข้า</div></div>`; return; }
    const tp = $("#kTpMode").value === "rr" ? e + dir * Math.abs(e - sl) * (tpIn || 0) : tpIn;
    const r = await api(`/api/size?symbol=${s}&entry=${e}&sl=${sl}&tp=${tp || ""}&risk_pct=${$("#kRisk").value}&balance=${$("#kBal").value}`);
    $("#kOut").innerHTML = `<div class="lotbar" style="display:flex;gap:18px;padding:14px;border-radius:10px;background:var(--accent-bg);border:1px solid rgba(59,130,246,.3)">
        <div><div class="small muted">LOT</div><div class="mono" style="font-size:30px;font-weight:800">${fnum(r.lots, 2)}</div></div>
        <div><div class="small muted">เสี่ยงจริง</div><div class="mono" style="font-size:18px">$${fnum(r.actual_risk_usd, 2)}</div><div class="tiny faint">เป้า $${fnum(r.risk_usd, 2)} · ${fnum(r.raw_lots, 3)} lot ก่อนปัด</div></div></div>
      <div class="kpis">${kpi("Stop Loss", fpx(sl, sp.digits), fnum(r.sl_pips, 1) + " pips", "neg")}${kpi("Take Profit", tp ? fpx(tp, sp.digits) : "–", r.tp_pips ? fnum(r.tp_pips, 1) + " pips" : "", "pos")}${kpi("R:R", r.rr ? "1 : " + fnum(r.rr, 2) : "–")}${kpi("กำไรเป้า", r.reward_usd ? "$" + fnum(r.reward_usd, 2) : "–", "", "pos")}${kpi("$/จุด/ล็อต", fnum(r.value_per_point_per_lot, 2), r.mt5 ? "จาก MT5" : "ค่าตั้งต้น")}</div>
      ${r.below_min_lot ? `<div class="banner"><span class="ic">⚠️</span><div>ต่ำกว่า lot ขั้นต่ำ — เพิ่มทุน/ความเสี่ยง หรือขยับ SL ให้ใกล้ขึ้น</div></div>` : ""}
      <div class="hint">สูตร: Lot = (ทุน × risk%) ÷ (|Entry − SL| × มูลค่าต่อ 1 หน่วยราคาต่อ 1 lot) ${s.endsWith("JPY") ? "· คู่ JPY แปลงเป็น USD ด้วย ÷ ราคา USDJPY" : ""}</div>`;
  }, 250);
  $$("#view input, #view select").forEach((i) => i.addEventListener("input", calc));
  calc();
}

// ------------------------------------------------------------------ SETTINGS
async function renderSettingsStatic(view) {
  const s = await api("/api/settings"); S.settings = s;
  const P = local.get(), G = s.research.gates;
  view.innerHTML = `<div class="stack">
    <div class="grid g2" style="align-items:start">
      <div class="card"><div class="hd"><h3>บัญชีของคุณ (บันทึกในเครื่องนี้)</h3></div><div class="bd stack" style="gap:12px">
        <div class="fgrid" style="grid-template-columns:1fr 1fr">
          <label class="f">ทุนในบัญชี (USD)<input class="inp num" type="number" id="pBal" value="${P.account_balance}"></label>
          <label class="f">ความเสี่ยงต่อไม้ (% ของทุน)<input class="inp num" type="number" step="0.05" id="pRisk" value="${P.risk_pct || s.risk_per_trade_pct || ""}"></label>
        </div>
        <div class="hint">Lot บนการ์ดสัญญาณและบนกราฟคำนวณให้อัตโนมัติจากค่านี้ — ตอนนี้ ${fnum(P.account_balance, 0)} USD × ${P.risk_pct || s.risk_per_trade_pct}% = เสี่ยงไม้ละ <b>$${fnum(P.account_balance * (P.risk_pct || s.risk_per_trade_pct) / 100, 2)}</b> (ถ้าชน SL)
          · ข้อความแจ้งเตือนบนมือถือใช้ทุน ${fnum(STATIC.account_balance || 5000, 0)} USD และ ${s.risk_per_trade_pct}% ตามที่ตั้งไว้บนคลาวด์</div>
        <div class="formsec">ชื่อสัญลักษณ์ใน MT5 ของโบรกเกอร์คุณ</div>
        <div class="fgrid" style="grid-template-columns:repeat(4,minmax(0,1fr))">${S.symbols.map((x) => `<label class="f">${x.symbol}<input class="inp" data-map="${x.symbol}" value="${esc(P.mt5_symbol_map[x.symbol] || s.mt5_symbol_map[x.symbol] || x.symbol)}"></label>`).join("")}</div>
        <div class="row" style="justify-content:flex-end"><button class="btn primary" id="pSave">💾 บันทึก</button></div>
      </div></div>
      <div class="card"><div class="hd"><h3>การแจ้งเตือนบนมือถือ</h3></div><div class="bd md">${md(`
- คลาวด์ (GitHub Actions) สแกนทุกชั่วโมง แล้วส่งแจ้งเตือนผ่านแอป **ntfy** เมื่อระบบ **ACTIVE**${s.alerts.watch ? " และ WATCH" : ""} มีสัญญาณ
- ติดตั้งแอป **ntfy** → กด **+** → Subscribe topic ที่ตั้งไว้ใน GitHub secret \`NTFY_TOPIC\`
- topic ไม่แสดงบนเว็บนี้ เพื่อไม่ให้คนอื่นส่งแจ้งเตือนปลอมเข้ามือถือคุณได้
- ทดสอบได้ที่ GitHub → Actions → **Run workflow** (scan)`)}</div></div>
    </div>
    <div class="card"><div class="hd"><h3>การวิเคราะห์อัตโนมัติ (ระบบจัดการเอง)</h3><div class="right"><a class="btn sm" href="${actionsUrl()}" target="_blank" rel="noopener">GitHub Actions ↗</a></div></div><div class="bd stack">
      <div class="kpis">${kpi("วิเคราะห์ใหม่", "ทุกเสาร์", "09:30 น. + ปุ่มเดียว")}${kpi("Walk-Forward", `${s.research.is_months}/${s.research.oos_months} m`, "+ ทดสอบอีก 4 หน้าต่าง")}${kpi("TF", s.research.timeframes.join(" / "))}${kpi("ถือสูงสุด", s.research.max_hold_hours + " ชม.")}${kpi("DD เป้าหมาย", s.target_max_dd_pct + "%", "MC 95%")}</div>
      <div class="formsec">เกณฑ์ ACTIVE (ห้ามลดเพื่อให้มีสัญญาณ)</div>
      <div class="gates">${[["OOS ไม้", G.min_oos_trades], ["Expectancy", G.min_expectancy + "R"], ["t-stat", G.min_t], ["MC PF@95%", G.min_mc_pf_p5], ["ชนะสุ่ม", G.min_random_pct + "%"], ["Plateau", G.min_plateau], ["Robustness", G.min_robust_score]].map(([k, v]) => `<span class="gate p">${k} ≥ ${v}</span>`).join("")}</div>
      ${s.force_off?.length ? `<div class="hint">ปิดโดย AI/ผู้ใช้: ${s.force_off.join(", ")}</div>` : ""}
    </div></div></div>`;
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
  view.innerHTML = `<div class="stack" id="setForm">
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
          <label class="f">ถือสูงสุด (ชม.)<input class="inp num" type="number" max="120" data-k="research.max_hold_hours" value="${R.max_hold_hours}"></label>
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
    <div class="card"><div class="hd"><h3>ความปลอดภัย</h3></div><div class="bd fgrid" style="grid-template-columns:1fr 1fr 1fr">
      <label class="f">PIN สำหรับเข้าเว็บ (ว่าง = ไม่ใช้)<input class="inp" type="password" data-k="access_pin" value="${esc(s.access_pin)}"></label>
      <label class="f">สแกนทุก (วินาที)<input class="inp num" type="number" data-k="scan_interval_sec" value="${s.scan_interval_sec}"></label>
      <div class="hint" style="align-self:end">ถ้าเปิดให้เข้าจากนอกบ้าน (Tailscale/Cloudflare) ควรตั้ง PIN</div></div></div>
    <div class="row" style="position:sticky;bottom:calc(76px + env(safe-area-inset-bottom));justify-content:flex-end"><button class="btn primary" id="saveSet">💾 บันทึกการตั้งค่า</button></div>
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
    S.symbols = await api("/api/symbols");
    toast("บันทึกแล้ว");
  };
  $("#testNotify").onclick = async () => { const r = await api("/api/notify/test", { method: "POST" }); toast("ผลส่ง: " + JSON.stringify(r)); };
}

// ------------------------------------------------------------------ GUIDE
// The user manual: one card per chapter, with a table of contents at the top.
function manualChapters() {
  const bal = myBalance() || 5000, risk = effRisk(0.25), riskUsd = bal * risk / 100;
  const lotsFor = (dist, vpu) => Math.floor(riskUsd / (dist * vpu) / 0.01 + 1e-9) * 0.01;
  const ex = (name, slText, dist, vpu) => { const l = lotsFor(dist, vpu);
    return `- **${name}** SL ${slText} → ${fnum(riskUsd, 2)} ÷ (${slText} × มูลค่าต่อ lot) = ${l >= 0.01 ? `**${l.toFixed(2)} lot**` : `ต่ำกว่า 0.01 → ถ้าเปิด 0.01 lot จะเสี่ยง $${fnum(0.01 * dist * vpu, 2)} (${fnum(100 * 0.01 * dist * vpu / bal, 2)}%)`}`; };
  return [
    ["overview", "1. ภาพรวม: อะไรทำเอง อะไรคุณต้องทำ", `
TradingLab มี 3 ส่วนที่ทำงานแยกกัน และ **คอมของคุณไม่ต้องเปิดไว้**:
### 🤖 ส่วนที่ทำงานเองทั้งหมด (บน GitHub ฟรี)
- **ทุกชั่วโมง (นาทีที่ ~4–20):** ดึงราคาล่าสุด → ตรวจว่าระบบ ACTIVE มีสัญญาณใหม่ไหม → ถ้ามี ส่งแจ้งเตือนเข้ามือถือพร้อม **Lot / Entry / SL / TP** → อัปเดตเว็บนี้ (กราฟ เส้น Entry/SL/TP, ผล forward test)
- **ทุกวันเสาร์ 09:30 น.:** วิเคราะห์ใหม่ทั้งหมดทุกสินทรัพย์ (Backtest + Walk-Forward + Robustness 8 แบบ + Monte Carlo) แล้วเลือกระบบ / Timeframe / พารามิเตอร์ใหม่ให้เอง ตลาด FX ปิดวันเสาร์ จึงไม่กระทบการเทรด
- ทุกพารามิเตอร์ของการวิเคราะห์ (ช่วง IS/OOS, ชุดค่าที่ทดสอบ, เกณฑ์ผ่าน) **ตั้งไว้แล้ว ไม่ต้องปรับเอง**
### 🙋 ส่วนที่คุณทำ
- **เมื่อมีแจ้งเตือน:** เปิด MT5 แล้วส่งคำสั่งซื้อขายตาม Lot / SL / TP ในข้อความ (ระบบ **ไม่ได้** ส่งออเดอร์ให้เอง)
- **วันจันทร์ (ถ้าต้องการ):** เปิดคอม พิมพ์บอก Claude ว่า “อัปเดตระบบเทรด” → Claude ตรวจผลจริงเทียบผลวิจัย ปิดระบบที่เริ่มเสีย เขียนบันทึก และส่งขึ้นคลาวด์ให้
### 🔘 ปุ่ม “สั่งวิเคราะห์ใหม่ทันที” คืออะไร
- เป็น **ทางลัดที่ไม่บังคับ** ใช้เมื่ออยากให้วิเคราะห์ใหม่ก่อนถึงวันเสาร์ เช่น หลังแก้ตั้งค่า
- กดแล้วจะเปิดหน้า **GitHub Actions** ซึ่งเป็นรายการงานที่คลาวด์ทำไปแล้ว ✅ สีเขียว = สำเร็จ ❌ สีแดง = ล้มเหลว
  - “Scheduled” คือรอบสแกนรายชั่วโมงที่รันเอง
  - “Commit … pushed by …” คือรอบที่สร้างเว็บใหม่หลังมีการแก้โค้ด
- ถ้าไม่กดเลย ระบบก็ยังทำงานครบตามตารางข้างบน`],
    ["setup", "2. ตั้งค่าครั้งแรก (ทำครั้งเดียวต่อเครื่อง)", `
- **ติดตั้งเป็นแอปบนมือถือ/iPad:** Safari → ปุ่มแชร์ → **เพิ่มไปยังหน้าจอโฮม** · Android Chrome → ⋮ → **ติดตั้งแอป**
- **หน้า ตั้งค่า:** ใส่ **ทุน (USD)** และ **ความเสี่ยงต่อไม้ %** แล้วกด **บันทึก** เว็บจะจำค่าไว้ในเครื่องนั้น (มือถือ, iPad, คอม ต้องตั้งแยกกัน)
- **ชื่อสัญลักษณ์ใน MT5:** แก้ให้ตรงกับโบรกเกอร์ เช่น Exness อาจเป็น \`XAUUSDm\` และ NAS100 = \`USTEC\` ชื่อนี้จะแสดงบนการ์ดสัญญาณ
- **แจ้งเตือน:** ติดตั้งแอป **ntfy** (App Store / Play Store ฟรี ไม่ต้องสมัคร) → กด **+** → ใส่ชื่อ topic ที่ตั้งเป็น \`NTFY_TOPIC\` ใน GitHub → Subscribe
  - บน iPhone ต้องอนุญาตการแจ้งเตือนของแอป ntfy ด้วย
- **ทุนในข้อความแจ้งเตือน:** ข้อความที่เด้งใช้ทุนและความเสี่ยงที่ตั้งบนคลาวด์ (ตอนนี้ ${fnum(STATIC?.account_balance || bal, 0)} USD × ${S.settings?.risk_per_trade_pct ?? risk}%)
  - ถ้าทุนเปลี่ยนมาก แก้ได้ที่ GitHub → Settings → Secrets and variables → Actions → Variables → \`ACCOUNT_BALANCE\`
  - หรือบอก Claude ในวันจันทร์`],
    ["signal", "3. เมื่อมีสัญญาณ: เปิดออเดอร์ทีละขั้น", `
- **1.** มือถือเด้งแจ้งเตือน เช่น “🟢 BUY USTEC H1 — เปิด 0.12 lot” ในข้อความมี Lot, Entry, SL, TP, R:R, กำไรเป้า และเวลา “ปิดภายใน”
- **2.** เปิดเว็บ หน้า **สัญญาณ** → การ์ดสีเขียวคือ LONG/BUY สีแดงคือ SHORT/SELL
- **3.** เปิด **MT5** → สินทรัพย์เดียวกัน → New Order แบบ **Market Execution**
- **4.** ถ้าราคาใน MT5 ต่างจาก Entry ในการ์ด (ปกติต่างเล็กน้อย):
  - กด **🎯 ปรับตามราคา MT5** → ใส่ราคาที่จะเข้าจริง → **คำนวณใหม่**
  - เว็บจะเลื่อน SL/TP ให้ระยะเท่าเดิม และคำนวณ Lot ใหม่
- **5.** ใส่ **Volume = Lot**, **Stop Loss**, **Take Profit** ตามการ์ด → กด Buy/Sell
- **6.** กลับมากด **✓ เปิดแล้ว** หรือ **ข้าม** เพื่อบันทึก
- **7.** ปล่อยให้ออเดอร์ชน SL หรือ TP เอง ถ้าถึงเวลา **ปิดภายใน** แล้วยังไม่ชน ให้ปิดเองด้วยมือ (ระบบถือไม่เกิน ~5 วัน)
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
  - ทองคำ (SL กว้าง) มักเจอกรณีนี้กับทุน 5,000 USD ที่ 0.25%
- **ข้อควรตรวจ 1 ครั้ง:** ขนาดสัญญาของโบรกอาจต่างจากค่ามาตรฐาน เช่น USTEC บางโบรก 1 lot = $1 หรือ $10 ต่อจุด
  - วิธีตรวจ: ใน MT5 ตั้ง SL ตามการ์ด แล้วดูว่าขาดทุนถ้าชน SL ≈ $${fnum(riskUsd, 2)} หรือไม่
  - ถ้าไม่ตรง บอก Claude ให้แก้ค่า contract ของสินทรัพย์นั้น`],
    ["pages", "5. หน้าต่าง ๆ ในเว็บ", `
- **📡 สัญญาณ:** สัญญาณที่เข้าได้ตอนนี้, ตารางระบบที่ใช้ในแต่ละสินทรัพย์, ผล forward test, ประวัติสัญญาณ
- **🧪 ระบบเทรด:** ผลวิเคราะห์ล่าสุดของทั้ง 8 สินทรัพย์
  - **ACTIVE** = ผ่านทุกเกณฑ์ → ส่งแจ้งเตือนจริง
  - **WATCH** = ผลบวกแต่ยังพิสูจน์ไม่พอ → ติดตามเงียบ ๆ ไม่แจ้งเตือน
  - **OFF** = ไม่พบความได้เปรียบ → ไม่เทรด
  - กดการ์ดเพื่อดูรายละเอียด: เกณฑ์ที่ผ่าน/ไม่ผ่าน, Robustness 8 ข้อ, Walk-Forward แต่ละช่วง, Monte Carlo และ **บันทึกจาก AI**
- **📈 กราฟ:** แท่งเทียน H1/H4/D1 พร้อม indicator ของระบบ, ลูกศรจุดเข้า และวงกลมจุดออกของไม้ในอดีต
  - ออเดอร์ที่ระบบถืออยู่จะมีเส้น **ENTRY (ฟ้า) · SL (แดง) · TP (เขียว)** พร้อมป้ายราคาและ Lot ด้านขวา
  - เส้นยืดออกไปตามแท่งใหม่จนกว่าจะชน SL/TP หรือหมดเวลา
- **🔬 Strategy Lab:** บนเว็บออนไลน์จะแสดงสรุปการวิเคราะห์อัตโนมัติ ส่วนการทดลองตั้งพารามิเตอร์เองใช้ได้เมื่อเปิด \`start_server.bat\` บนคอม
- **🧮 เครื่องคำนวณ:** คำนวณ Lot / R:R / SL-TP เองสำหรับเทรดนอกระบบ
- **⚙️ ตั้งค่า:** ทุน, ความเสี่ยงต่อไม้, ชื่อสัญลักษณ์ MT5, เกณฑ์ที่ใช้ตัดสิน (ดูอย่างเดียว)`],
    ["price", "6. ราคา, ความสดของข้อมูล และ MT5", `
- **ไม่ต้องเปิด MT5 เพื่อให้ระบบทำงาน** คลาวด์ดึงราคาฟรีเอง:
  - Yahoo สำหรับ FX, ทอง และดัชนี
  - Binance สำหรับ BTC และ ETH
- MT5 ใช้แค่ตอนที่ **คุณส่งออเดอร์** (ในมือถือหรือคอมก็ได้)
- **เว็บไม่ใช่ราคา real-time:** ข้อมูลอัปเดตชั่วโมงละครั้งหลังแท่งเทียน H1 ปิด และ GitHub อาจช้าไป 5–20 นาที
  - ระบบตัดสินใจจากแท่งที่ **ปิดแล้ว** (H1/H4) เท่านั้น จึงไม่ต้องการราคาทุกวินาที
  - ราคาจริงตอนเข้าให้ดูใน MT5 แล้วใช้ปุ่ม **ปรับตามราคา MT5**
- **ทำไมราคาบนเว็บต่างจาก MT5:** ทองและดัชนีจาก Yahoo เป็นราคา futures ซึ่งต่างจาก CFD ของโบรกประมาณ 0.1–0.5%
  - สิ่งที่ใช้ได้ตรงคือ **ระยะ** SL/TP (pips/จุด) เพราะปุ่มปรับตามราคา MT5 คงระยะไว้
- **ถ้าเปิด MT5 บนคอมพร้อม \`start_server.bat\`:** เวอร์ชันบนคอมจะใช้ราคาและสเปกของโบรกเกอร์คุณโดยตรง (ไม่บังคับ ใช้ตอนวิเคราะห์เชิงลึกกับ Claude)`],
    ["monday", "7. วันจันทร์กับ Claude", `
- เปิดคอม → เปิด Claude ในโฟลเดอร์ \`D:\\Backtest\` → พิมพ์ เช่น “อัปเดตระบบเทรด” หรือ “ทุนเปลี่ยนเป็น 6000”
- Claude จะ:
  - ดึงผลล่าสุดจากคลาวด์
  - เทียบผลจริง (forward test) กับผลวิจัย
  - ปิดระบบที่ผลจริงแย่กว่าช่วงที่ Monte Carlo คาดไว้
  - เขียนบันทึก (แสดงในหน้า **ระบบเทรด**)
  - ส่งขึ้นคลาวด์ให้เสร็จในวันนั้น
- กติกาที่ Claude ต้องทำตาม: **ไม่ลดเกณฑ์เพื่อให้มีสัญญาณ**, ตัดสินจากผล Out-of-Sample เท่านั้น, ถือไม่เกิน 5 วัน`],
    ["faq", "8. คำถามที่พบบ่อย / ข้อควรรู้", `
- **ทำไมไม่มีสัญญาณเลยหลายวัน?** ส่งแจ้งเตือนเฉพาะระบบ ACTIVE และเฉพาะตอนไม่มีออเดอร์ค้าง ถ้า ACTIVE มีน้อย สัญญาณก็น้อย ซึ่งดีกว่าเทรดระบบที่ไม่ผ่านการทดสอบ
- **กดปุ่มวิเคราะห์แล้วเจอหน้า GitHub ไม่มีปุ่ม Run workflow?** ต้องกด **Sign in** มุมขวาบนของ GitHub ก่อน → เมนูซ้ายเลือก **TradingLab** → ปุ่ม **Run workflow** → เลือก \`research\` → Run
- **ไม่มีแจ้งเตือนเด้ง?** ตรวจว่าแอป ntfy subscribe topic ถูกตัว และอนุญาตการแจ้งเตือนในมือถือ ดูได้ที่ GitHub Actions ว่ารอบล่าสุดเป็น ✅
- **ความเสี่ยงต่อไม้ควรเท่าไร?** ระบบใช้ค่าที่คุณตั้ง (${risk}%) สำหรับทุกสัญญาณ Monte Carlo ในหน้าระบบเทรดแสดงว่า drawdown แย่ ๆ อาจลึกแค่ไหน ใช้ประกอบการตัดสินใจของคุณเอง
- ผลวิจัยมาจากอดีต **ไม่รับประกันอนาคต** — แนะนำเริ่มด้วยบัญชี demo หรือความเสี่ยงต่ำ จนกว่าผล forward test จะยืนยัน
- เว็บนี้เป็นเครื่องมือวิจัย **ไม่ใช่คำแนะนำการลงทุน** การตัดสินใจเทรดเป็นของคุณ`],
  ];
}

async function renderGuide(view) {
  const ch = manualChapters();
  view.innerHTML = `<div class="stack">
    <div class="card"><div class="hd"><h3>📘 คู่มือการใช้งาน TradingLab</h3></div><div class="bd stack" style="gap:10px">
      <div class="hint">อ่านข้อ 1–4 ก่อนเริ่มใช้ ที่เหลืออ่านเมื่อสงสัย · ตัวเลขในคู่มือคำนวณจากทุนและความเสี่ยงที่ตั้งไว้ในเครื่องนี้</div>
      <div class="chips">${ch.map(([id, title]) => `<button class="chip" data-go="${id}">${esc(title)}</button>`).join("")}</div></div></div>
    ${ch.map(([id, title, body]) => `<div class="card" id="man-${id}"><div class="hd"><h3>${esc(title)}</h3></div><div class="bd md">${md(body)}</div></div>`).join("")}
  </div>`;
  $$("[data-go]", view).forEach((b) => b.onclick = () => $(`#man-${b.dataset.go}`).scrollIntoView({ behavior: "smooth", block: "start" }));
}

// ------------------------------------------------------------------ boot
async function boot() {
  const theme = store.get("theme", "dark");
  document.documentElement.dataset.theme = theme;
  $("#themeBtn").onclick = () => { const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; document.documentElement.dataset.theme = t; store.set("theme", t); navigate(); };
  try {
    [S.symbols, S.strategies, S.settings] = await Promise.all([api("/api/symbols"), api("/api/strategies"), api("/api/settings")]);
  } catch (e) { $("#view").innerHTML = `<div class="banner"><span class="ic">⚠️</span><div>${STATIC ? "โหลดข้อมูลไม่ได้" : "เชื่อมต่อเซิร์ฟเวอร์ไม่ได้"}: ${esc(e.message)}</div></div>`; return; }
  await refreshStatus();
  window.addEventListener("hashchange", navigate);
  navigate();
  if (STATIC) return;  // the cloud build changes once an hour; the reload button / chart timer pick it up
  setInterval(refreshStatus, 30000);
  setInterval(async () => {
    if (route() !== "signals" || document.hidden) return;
    const sigs = await api("/api/signals?limit=20");
    const n = sigs.filter((s) => s.outcome === "open" && s.user_status === "new" && new Date(s.valid_until) > new Date()).length;
    if (n !== S.newCount) navigate();
  }, 60000);
}
boot();
