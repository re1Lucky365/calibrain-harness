/* Classifier Trading Terminal: front end. Talks only to the local Python service.
   Accuracy rules: exchange price strings are shown as delivered (no float re-rounding);
   unknown values render "—", never 0; every live panel shows its data age. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isNum = (x) => x !== null && x !== undefined && x !== "" && Number.isFinite(Number(x));
const fx = (x, nd = 2) => (isNum(x) ? Number(x).toLocaleString("en-US", { minimumFractionDigits: nd, maximumFractionDigits: nd }) : "—");
const sg = (x, nd = 2, suf = "") => (isNum(x) ? (Number(x) > 0 ? "+" : Number(x) < 0 ? "−" : "") + fx(Math.abs(Number(x)), nd) + suf : "—");
const ud = (x) => (isNum(x) ? (Number(x) > 0 ? "up" : Number(x) < 0 ? "dn" : "") : "");
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const usd = (x) => {
  if (!isNum(x)) return "—";
  const a = Math.abs(x), s = a >= 1e9 ? (a / 1e9).toFixed(2) + "B" : a >= 1e6 ? (a / 1e6).toFixed(2) + "M" : a >= 1e3 ? (a / 1e3).toFixed(1) + "K" : a.toFixed(0);
  return (x < 0 ? "−" : "") + s;
};
/* exchange price string -> thousands separators, original decimals preserved */
const px = (s) => { if (!isNum(s)) return "—"; const [i, f] = String(s).split("."); return Number(i).toLocaleString("en-US") + (f !== undefined ? "." + f : ""); };
const decimalsOf = (s) => (String(s ?? "").split(".")[1] || "").length;
const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
const hhmmss = (ts) => new Date(ts * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const dt = (ts) => new Date(ts * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
const age = (s) => (!isNum(s) ? "—" : s < 1 ? "<1s" : s < 60 ? `${s | 0}s` : s < 3600 ? `${(s / 60) | 0}m` : `${(s / 3600).toFixed(1)}h`);
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
  return r.headers.get("content-type")?.includes("json") ? r.json() : r.text();
};
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
const short = (sym) => sym.replace("USDT", "");
const TFL = { 1: "1M", 5: "5M", 15: "15M", 60: "1H", 240: "4H" };

const S = { ov: null, mon: null, mkt: null, decisions: [], latest: {}, symbol: null, tf: null, prevLast: {}, news: { forecasts: [] }, view: "mon" };

/* ============================================================ tooltip */
const tip = $("#tip");
document.addEventListener("mousemove", (e) => {
  const el = e.target.closest?.("[data-tip]");
  if (!el) { tip.hidden = true; return; }
  tip.hidden = false; tip.innerHTML = el.dataset.tip;
  const w = tip.offsetWidth, h = tip.offsetHeight;
  tip.style.left = Math.min(e.clientX + 14, innerWidth - w - 6) + "px";
  tip.style.top = Math.min(e.clientY + 14, innerHeight - h - 30) + "px";
});

/* ============================================================ clocks */
const CITIES = [["New York", "America/New_York"], ["London", "Europe/London"], ["Dubai", "Asia/Dubai"], ["Mumbai", "Asia/Kolkata"],
  ["Singapore", "Asia/Singapore"], ["Hong Kong", "Asia/Hong_Kong"], ["Tokyo", "Asia/Tokyo"], ["Sydney", "Australia/Sydney"], ["UTC", "UTC"]];
const LOCAL_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;
$("#clocks").innerHTML = CITIES.map(([c, tz]) => `<div class="clock${tz === LOCAL_TZ ? " local" : ""}"><div class="t" data-tz="${tz}">--:--:--</div><div class="c">${c}${tz === LOCAL_TZ ? '<span class="mk">LOCAL</span>' : ""}</div></div>`).join("");
const fmtters = Object.fromEntries(CITIES.map(([, tz]) => [tz, new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" })]));
function tickClocks() { const now = new Date(); $$(".clock .t").forEach((el) => (el.textContent = fmtters[el.dataset.tz].format(now))); renderStatus(); }
setInterval(tickClocks, 1000); tickClocks();

/* ============================================================ views & command line */
function setView(v) {
  S.view = v;
  $$("#fkeys button").forEach((b) => b.classList.toggle("on", b.dataset.view === v));
  $$(".view").forEach((x) => x.classList.toggle("on", x.id === "view-" + v));
  ({ eval: loadEval, trades: loadTrades, news: loadNewsView, log: loadEvents })[v]?.();
  if (v === "mon") requestAnimationFrame(renderMap);
}
$("#fkeys").onclick = (e) => { const b = e.target.closest("button[data-view]"); if (b) setView(b.dataset.view); };
document.addEventListener("keydown", (e) => {
  const fk = { F1: "mon", F2: "eval", F3: "trades", F4: "news", F5: "log" }[e.key];
  if (fk) { e.preventDefault(); setView(fk); return; }
  if ((e.key === "/" || e.key === "`") && document.activeElement !== $("#cmd") && !document.querySelector("dialog[open]") && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement?.tagName)) { e.preventDefault(); $("#cmd").focus(); }
});
$("#cmdForm").onsubmit = (e) => {
  e.preventDefault();
  const raw = $("#cmd").value.trim().toUpperCase(); $("#cmd").value = "";
  if (!raw) return;
  const syms = S.ov?.settings.symbols || [];
  const sym = syms.find((s) => s === raw || s === raw + "USDT");
  const tfm = { "1M": "1", "5M": "5", "15M": "15", "1H": "60", "4H": "240" }[raw];
  const pc = raw.match(/^(?:POS|MAXPOS)\s+(\d+)$/);
  if (pc) { setPosCap(pc[1]); return; }
  const ttf = raw.match(/^(?:TF|TRADE)\s+(1M|5M|15M|1H)$/);
  if (ttf) { openTfSwitch({ "1M": "1", "5M": "5", "15M": "15", "1H": "60" }[ttf[1]]); return; }
  const views = { MON: "mon", EVAL: "eval", TRD: "trades", TRADES: "trades", NEWS: "news", N: "news", LOG: "log" };
  if (sym) selectSymbol(sym);
  else if (tfm) { S.tf = tfm; loadMarket(true); }
  else if (views[raw]) setView(views[raw]);
  else if (raw === "ASK") ask(false);
  else if (raw === "PRB") ask(true);
  else if (raw === "KEYS") openKeys();
  else if (raw === "HELP" || raw === "?") $("#helpModal").showModal();
  else { $("#cmd").placeholder = `UNKNOWN COMMAND "${raw}" · type HELP`; $("#cmd").blur(); setTimeout(() => ($("#cmd").placeholder = "BTC · ETH · SOL · ASK · EVAL · KEYS · HELP"), 2500); }
};
function selectSymbol(sym) {
  S.symbol = sym; $("#cmdSec").textContent = `${sym} PERP`;
  renderWatch(); loadMarket(true); renderKairos(); renderBlotter();
}

/* ============================================================ trading timeframe */
const hrs = (m) => (m >= 60 ? `${m / 60}h` : `${m}m`);
function renderTradeTf() {
  const st = S.ov.settings, cur = st.decision_tf;
  $("#tradeTfSeg").innerHTML = Object.entries(st.tf_profiles).map(([tf, p]) => `<button data-ttf="${tf}" class="${tf === cur ? "on" : ""}" data-tip="${p.label}: decide every ${p.label} close · hold ≤ ${hrs(p.horizon_minutes)} · stop from ${p.atr_tf}m ATR · ~${p.required_rph} Kairos req/h">${p.label}</button>`).join("");
  $("#tradeTfInfo").textContent = `hold ≤ ${hrs(st.horizon_minutes)} · stop ${st.atr_tf}m ATR`;
  $$("#tradeTfSeg button").forEach((b) => (b.onclick = () => openTfSwitch(b.dataset.ttf)));
}
function openTfSwitch(tf) {
  const st = S.ov.settings, cur = st.tf_profiles[st.decision_tf], nxt = st.tf_profiles[tf];
  if (!nxt) return;
  if (tf === st.decision_tf) { $("#cmd").placeholder = `ALREADY TRADING ${nxt.label}`; setTimeout(() => ($("#cmd").placeholder = "BTC · ETH · SOL · ASK · EVAL · KEYS · HELP"), 2000); return; }
  const budget = st.kairos_max_requests_per_hour, over = nxt.required_rph > budget;
  const tok = (rph) => `~${Math.round(rph * 14300 / 1e5) / 10}M`;
  const row = (k, a, b) => `<div class="h">${k}</div><div class="old">${a}</div><div class="new">${b}</div>`;
  $("#tfBody").innerHTML = `<div class="tfgrid">${row("", "NOW · " + cur.label, "NEW · " + nxt.label)}
    ${row("Kairos asked", `every ${cur.label} close`, `every ${nxt.label} close`)}
    ${row("Max hold / time-stop", hrs(cur.horizon_minutes), hrs(nxt.horizon_minutes))}
    ${row("Stop sized from", `${cur.atr_tf}m ATR × ${st.sl_atr} (≥ ${st.min_sl_bp}bp)`, `${nxt.atr_tf}m ATR × ${st.sl_atr} (≥ ${st.min_sl_bp}bp)`)}
    ${row("Kairos requests / hour", `~${cur.required_rph}`, `~${nxt.required_rph}`)}
    ${row("Input tokens / hour", tok(cur.required_rph), tok(nxt.required_rph))}</div>
    <p class="muted" style="margin:8px 0 0">Takes effect at the next ${nxt.label} bar close. Open positions keep their original time-stop. Every decision is stamped with its timeframe, so EVAL can compare them separately. Saved to .env.</p>
    ${over ? `<div class="warnbox"><b class="amb">BUDGET.</b> ${nxt.label} needs ~${nxt.required_rph} requests/hour but the cap is ${budget}. With the current cap, about ${Math.round(100 * (1 - budget / nxt.required_rph))}% of bars would be skipped.</div>` : ""}
    ${tf === "1" ? `<div class="warnbox"><b class="amb">1M NOTE.</b> Kairos answers in ~7–12 s, so entries land ~10 s into the next bar, and the 40bp stop floor means fees are ~0.37R per trade.</div>` : ""}`;
  const go = async (raise) => { try { await post("/api/decision-tf", { tf, raise_budget: raise }); $("#tfModal").close(); S.tf = tf; await loadOverview(); loadMarket(true); } catch (e) { alert(e.message); } };
  $("#tfActions").innerHTML = (over ? `<button class="kbtn amber" id="tfGoRaise">SWITCH + RAISE CAP TO ${nxt.required_rph}/H</button><button class="kbtn" id="tfGo">SWITCH, KEEP CAP ${budget}</button>` : `<button class="kbtn amber" id="tfGo">SWITCH TO ${nxt.label}</button>`) + `<button class="kbtn" id="tfCancel">CANCEL</button>`;
  $("#tfGo").onclick = () => go(false);
  if (over) $("#tfGoRaise").onclick = () => go(true);
  $("#tfCancel").onclick = () => $("#tfModal").close();
  $("#tfModal").showModal();
}

/* ============================================================ overview / status / alerts */
async function loadOverview() {
  S.ov = await api("/api/overview");
  const o = S.ov;
  if (!S.symbol) {
    S.symbol = o.settings.symbols[0]; S.tf = String(o.settings.decision_tf_minutes);
    $("#cmdSec").textContent = `${S.symbol} PERP`;
    $("#evalTf").innerHTML += Object.entries(o.settings.tf_profiles).map(([tf, p]) => `<option value="${tf}">TRADE TF ${p.label}</option>`).join("");
    $("#evalSymbol").innerHTML += o.settings.symbols.map((s) => `<option value="${s}">${s}</option>`).join("");
    $("#tfSeg").innerHTML = Object.entries(TFL).map(([tf, l]) => `<button data-tf="${tf}">${l}</button>`).join("");
    $("#tfSeg").onclick = (e) => { const b = e.target.closest("button"); if (b) { S.tf = b.dataset.tf; loadMarket(true); } };
  }
  renderTradeTf();
  $("#tradingToggle").checked = !!o.settings.trading_enabled;
  $("#exploreToggle").checked = !!o.settings.exploration_mode;
  renderAlerts(); renderAccount(); renderStatus();
}
function renderAlerts() {
  const o = S.ov, a = [];
  if (!o.kairos.configured) a.push(`<div class="alert"><b>KAIROS KEY NOT SET.</b> Market data and the state preview are live; no decisions are made. <a href="#" data-act="keys">Add key ›</a></div>`);
  if (o.kairos.last_error) a.push(`<div class="alert crit"><b>KAIROS ERROR</b> ${esc(o.kairos.last_error)}</div>`);
  if (o.account?.halted_reason) a.push(`<div class="alert crit"><b>TRADING HALTED</b> ${esc(o.account.halted_reason)}</div>`);
  if (o.executor_error) a.push(`<div class="alert crit"><b>TESTNET</b> ${esc(o.executor_error)} <a href="#" data-act="keys">Keys ›</a></div>`);
  const html = a.join("");
  if ($("#alerts").innerHTML !== html) $("#alerts").innerHTML = html;
  $$("#alerts [data-act=keys]").forEach((x) => (x.onclick = (e) => { e.preventDefault(); openKeys(); }));
}
function renderStatus() {
  const o = S.ov; if (!o) return;
  const d = o.data, k = o.kairos, a = o.account;
  const dot = (c) => `<span class="dot ${c}"></span>`;
  const row = S.mon?.rows.find((r) => r.symbol === S.symbol);
  const fund = row?.next_funding_ms ? Math.max(0, row.next_funding_ms - Date.now()) / 1000 : null;
  const fundTxt = fund === null ? "—" : `${String((fund / 3600) | 0).padStart(2, "0")}:${String(((fund % 3600) / 60) | 0).padStart(2, "0")}:${String((fund % 60) | 0).padStart(2, "0")}`;
  $("#statusline").innerHTML = [
    `${dot(d.ws_connected ? "ok" : "bad")}DATA <b>${d.ws_connected ? "LIVE" : "DOWN"}</b> ${d.ws_messages.toLocaleString()} msgs`,
    `${dot(!k.configured ? "warn" : k.last_error ? "bad" : "ok")}KAIROS <b>${k.configured ? (k.last_error ? "ERROR" : "READY") : "NO KEY"}</b> ${k.requests_last_hour}/${k.budget_per_hour} req/h`,
    `${dot(o.executor_error ? "bad" : "ok")}TESTNET <b>${o.executor_error ? "OFFLINE" : "CONNECTED"}</b>`,
    `MODE <b class="${a?.trading_enabled ? "up" : ""}">${a?.trading_enabled ? "AUTO-TRADE" : "OBSERVE"}</b>${o.settings.exploration_mode ? ' <b class="amb">· EXPLORE</b>' : ""}`,
    `TRADE TF <b class="amb">${o.settings.tf_profiles[o.settings.decision_tf]?.label || o.settings.decision_tf}</b> · HOLD ≤ <b>${o.settings.horizon_minutes}m</b>`,
    `LABELLED <b>${o.labelled_total}</b>`,
    `${short(S.symbol || "")} FUNDING IN <b>${fundTxt}</b>`,
    `<span style="margin-left:auto">UTC <b>${new Date().toISOString().slice(11, 19)}</b></span>`,
  ].join("");
}

/* ============================================================ monitor (watchlist, macro, map, tape, news) */
function spark(vals, w = 54, h = 16) {
  if (!vals || vals.length < 2) return "";
  const mn = Math.min(...vals), mx = Math.max(...vals), r = mx - mn || 1;
  const d = vals.map((v, i) => `${((i / (vals.length - 1)) * w).toFixed(1)},${(h - 1 - ((v - mn) / r) * (h - 2)).toFixed(1)}`).join(" ");
  const c = vals[vals.length - 1] >= vals[0] ? css("--up") : css("--down");
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline points="${d}" fill="none" stroke="${c}" stroke-width="1.25"/></svg>`;
}
async function loadMonitor() {
  S.mon = await api("/api/monitor");
  renderWatch(); renderMacro(); renderTape(); renderNewsMini(); renderMap();
  const ages = S.mon.rows.map((r) => r.age_s).filter(isNum);
  $("#monAge").textContent = ages.length ? `upd ${age(Math.max(...ages))} ago` : "";
}
function renderWatch() {
  const m = S.mon; if (!m) return;
  const rows = m.rows.map((r) => {
    const prev = S.prevLast[r.symbol]; let fl = "";
    if (prev && r.last && prev !== r.last) fl = Number(r.last) > Number(prev) ? "flash-up" : "flash-dn";
    S.prevLast[r.symbol] = r.last;
    const d = r.decision;
    const tag = !d ? `<span class="tag flat">—</span>` : d.error ? `<span class="tag err" data-tip="${esc(d.error)}">ERR</span>` : `<span class="tag ${d.action}" data-tip="${esc(d.action)}${d.exploration ? " (explore)" : ""} · ${age(Date.now() / 1000 - d.ts)} ago<br>EV long ${sg(d.ev_long, 3)}R · short ${sg(d.ev_short, 3)}R">${(d.action || "").toUpperCase()}</span>`;
    const tipTxt = `${r.symbol}<br>mark ${px(r.mark)} · index ${px(r.index)}<br>24h H ${px(r.high24h)} L ${px(r.low24h)}<br>turnover $${usd(r.turnover24h)} · OI $${usd(r.oi_value)}<br>testnet ${px(r.testnet_last)} (${sg(r.divergence_bp, 1)} bp)`;
    return `<tr class="click ${r.symbol === S.symbol ? "sel" : ""}" data-s="${r.symbol}" data-tip="${esc(tipTxt)}"><td class="sym">${short(r.symbol)}</td><td class="r ${fl}">${px(r.last)}</td><td class="r ${ud(r.pct24h)}">${sg(r.pct24h, 2, "%")}</td><td>${spark(r.spark)}</td><td class="r ${ud(r.funding)}">${sg(isNum(r.funding) ? r.funding * 100 : null, 4)}</td><td class="r">${tag}</td></tr>`;
  }).join("");
  $("#watch").innerHTML = `<table class="t"><thead><tr><th>Sym</th><th class="r">Last</th><th class="r">24h</th><th>24h·15m</th><th class="r">Fund%</th><th class="r">KRS</th></tr></thead><tbody>${rows}</tbody></table>`;
  $$("#watch tr[data-s]").forEach((tr) => (tr.onclick = () => selectSymbol(tr.dataset.s)));
}
function renderMacro() {
  const m = S.mon; if (!m) return;
  const names = { SPX_fut: "S&P 500 FUT", NDX_fut: "NASDAQ 100 FUT", DXY: "DOLLAR INDEX", Gold: "GOLD FUT", US10Y: "US 10Y YIELD", VIX: "VIX" };
  const rows = Object.entries(m.macro || {}).map(([k, v]) => `<tr><td class="amb">${names[k] || k}</td><td class="r">${fx(v.last, k === "US10Y" ? 3 : 2)}</td><td class="r ${ud(v.chg_8h_pct)}">${sg(v.chg_8h_pct, 2, "%")}</td></tr>`);
  const fg = m.fear_greed;
  if (fg) rows.push(`<tr><td class="amb">CRYPTO FEAR &amp; GREED</td><td class="r">${fg.value}</td><td class="r muted">${esc(fg.label).toUpperCase()} · wk ${fg.week_ago}</td></tr>`);
  $("#macro").innerHTML = `<table class="t"><thead><tr><th>Instrument</th><th class="r">Last</th><th class="r">8h chg</th></tr></thead><tbody>${rows.join("") || `<tr><td colspan="3" class="muted">loading…</td></tr>`}</tbody></table>`;
  $("#macroAge").textContent = "Yahoo · alternative.me";
}
function renderTape() {
  const t = S.mon?.tape || [];
  $("#tapeInfo").textContent = `${t.length} events`;
  $("#tape").innerHTML = t.length ? `<table class="t"><thead><tr><th>Time</th><th>Sym</th><th>Type</th><th>Side</th><th class="r">Notional $</th><th class="r">Price</th></tr></thead><tbody>${t.map((e) => {
    const bull = e.side === "BUY" || e.side === "SHORT"; // a short liquidation is forced buying
    const lab = e.kind === "LIQ" ? `${e.side} LIQ` : `${e.side}`;
    return `<tr><td class="muted">${hhmmss(e.ts)}</td><td class="sym">${short(e.symbol)}</td><td>${e.kind === "LIQ" ? '<span class="tag amb">LIQ</span>' : '<span class="tag flat">BLOCK</span>'}</td><td class="${bull ? "up" : "dn"}">${lab}</td><td class="r">${usd(e.notional)}</td><td class="r muted">${e.price ? px(e.price) : ""}</td></tr>`;
  }).join("")}</tbody></table>` : `<div class="empty">No liquidations or block prints since the stream started</div>`;
}
function renderNewsMini() {
  const n = S.mon?.news || [];
  const fc = {}; for (const f of S.news.forecasts || []) if (f.probs_json) fc[f.id.split(":")[0]] = f;
  const src = { CoinDesk: "CD", Cointelegraph: "CT", Decrypt: "DC" };
  $("#newsMini").innerHTML = n.length ? `<table class="t"><tbody>${n.map((h) => {
    const f = fc[h.id]; const lean = f ? f.probs_json.slice(4).reduce((a, b) => a + b, 0) - f.probs_json.slice(0, 3).reduce((a, b) => a + b, 0) : null;
    const mine = h.symbols.includes(S.symbol);
    return `<tr class="click" data-link="${esc(h.link)}"><td class="muted">${h.published_ts ? hhmm(h.published_ts) : "--:--"}</td><td class="amb">${src[h.source] || "NW"}</td><td class="wrap" style="${mine ? "color:var(--amber)" : ""}">${esc(h.title)} ${h.symbols.map((s) => `<span class="tag flat">${short(s)}</span>`).join(" ")}${f ? ` <span class="tag ${lean > 0 ? "long" : lean < 0 ? "short" : "flat"}" data-tip="Kairos news_return 15m: up−down ${sg(lean, 2)}">K ${sg(lean, 2)}</span>` : ""}</td></tr>`;
  }).join("")}</tbody></table>` : `<div class="empty">Loading headlines…</div>`;
  $$("#newsMini tr[data-link]").forEach((tr) => (tr.onclick = () => tr.dataset.link && window.open(tr.dataset.link, "_blank", "noopener")));
}

/* ---- squarified treemap */
function squarify(items, x, y, w, h) {
  const total = items.reduce((a, b) => a + b.value, 0); if (!total) return [];
  const nodes = items.map((i) => ({ ...i, area: (i.value * w * h) / total }));
  const out = []; let row = [], rect = { x, y, w, h };
  const worst = (r, len) => { const s = r.reduce((a, b) => a + b.area, 0), mx = Math.max(...r.map((q) => q.area)), mn = Math.min(...r.map((q) => q.area)); return Math.max((len * len * mx) / (s * s), (s * s) / (len * len * mn)); };
  const lay = (r, rc) => {
    const s = r.reduce((a, b) => a + b.area, 0);
    if (rc.w >= rc.h) { const cw = s / rc.h; let cy = rc.y; for (const q of r) { const qh = q.area / cw; out.push({ ...q, x: rc.x, y: cy, w: cw, h: qh }); cy += qh; } return { x: rc.x + cw, y: rc.y, w: rc.w - cw, h: rc.h }; }
    const rh = s / rc.w; let cx = rc.x; for (const q of r) { const qw = q.area / rh; out.push({ ...q, x: cx, y: rc.y, w: qw, h: rh }); cx += qw; } return { x: rc.x, y: rc.y + rh, w: rc.w, h: rc.h - rh };
  };
  for (const n of nodes) { const len = Math.min(rect.w, rect.h); if (!row.length || worst([...row, n], len) <= worst(row, len)) row.push(n); else { rect = lay(row, rect); row = [n]; } }
  if (row.length) lay(row, rect);
  return out;
}
function heat(p) {
  // diverging: red ← neutral grey → green, saturates at ±6%; the signed % label carries the value
  const t = Math.min(Math.abs(p) / 6, 1), n = [38, 38, 38], g = [22, 150, 88], r = [196, 52, 52], c = p >= 0 ? g : r;
  return `rgb(${n.map((v, i) => Math.round(v + (c[i] - v) * Math.pow(t, 0.75))).join(",")})`;
}
function renderMap() {
  const el = $("#map"), u = S.mon?.universe || []; if (!u.length) { el.innerHTML = `<div class="empty">Loading universe…</div>`; return; }
  const W = el.clientWidth, H = el.clientHeight; if (!W || !H) return;
  const tracked = new Set(S.ov?.settings.symbols || []);
  const rects = squarify(u.map((x) => ({ ...x, value: Math.sqrt(x.turnover24h) })), 0, 0, W, H);
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}">${rects.map((r) => {
    const big = r.w > 46 && r.h > 28, mid = r.w > 34 && r.h > 15;
    const t = `${esc(r.symbol)} · ${px(r.last)}<br>24h ${sg(r.pct24h, 2, "%")}<br>turnover $${usd(r.turnover24h)} · OI $${usd(r.oi_value)}<br>funding ${sg(r.funding * 100, 4, "%")}`;
    return `<g data-tip="${esc(t)}"><rect class="${tracked.has(r.symbol) ? "trk" : ""}" x="${r.x}" y="${r.y}" width="${Math.max(r.w, 0)}" height="${Math.max(r.h, 0)}" fill="${heat(r.pct24h)}"/>${mid ? `<text x="${r.x + 4}" y="${r.y + 13}">${esc(short(r.symbol))}</text>` : ""}${big ? `<text class="p" x="${r.x + 4}" y="${r.y + 26}">${sg(r.pct24h, 2, "%")}</text>` : ""}</g>`;
  }).join("")}</svg>`;
}
new ResizeObserver(() => renderMap()).observe($("#map"));

/* ============================================================ main chart */
function ema(vals, p) { const k = 2 / (p + 1); const out = []; let e = null; vals.forEach((v, i) => { e = e === null ? v : v * k + e * (1 - k); out.push(i >= p - 1 ? e : null); }); return out; }
function chartOpts() {
  return {
    autoSize: true, layout: { background: { color: "#000" }, textColor: css("--muted"), fontFamily: css("--mono"), fontSize: 11 },
    grid: { vertLines: { color: "#111" }, horzLines: { color: "#111" } },
    timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#242424", rightOffset: 4 },
    rightPriceScale: { borderColor: "#242424" },
    crosshair: { mode: 0, vertLine: { color: "#5a5a5a", labelBackgroundColor: "#2b1a05" }, horzLine: { color: "#5a5a5a", labelBackgroundColor: "#2b1a05" } },
    localization: { timeFormatter: (t) => new Date(t * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) },
  };
}
function buildChart() {
  S.chart = LightweightCharts.createChart($("#chart"), chartOpts());
  S.candles = S.chart.addCandlestickSeries({ upColor: css("--up"), downColor: css("--down"), borderVisible: false, wickUpColor: css("--up"), wickDownColor: css("--down"), priceLineColor: css("--amber") });
  S.ema20 = S.chart.addLineSeries({ color: css("--amber"), lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  S.ema50 = S.chart.addLineSeries({ color: css("--s1"), lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  S.vol = S.chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
  S.chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
  S.chart.subscribeCrosshairMove((p) => {
    const b = p?.time ? S.barsByTime?.get(p.time) : null;
    renderOhlc(b || S.mkt?.bars?.[S.mkt.bars.length - 1]);
  });
}
function renderOhlc(b) {
  if (!b) return; const d = S.pd ?? 2, chg = b.close - b.open;
  $("#ohlc").innerHTML = `<b>O</b> ${fx(b.open, d)} <b>H</b> ${fx(b.high, d)} <b>L</b> ${fx(b.low, d)} <b>C</b> <span class="${ud(chg)}">${fx(b.close, d)}</span> <b>V</b> ${usd(b.volume)} <span class="muted">${new Date(b.time * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</span>`;
}
function drawChart(reset) {
  const m = S.mkt; if (!m || !m.bars.length) return;
  if (!S.chart) buildChart();
  S.pd = Math.max(decimalsOf(m.ticker.lastPrice), 0);
  const pf = { type: "price", precision: S.pd, minMove: Math.pow(10, -S.pd) };
  S.candles.applyOptions({ priceFormat: pf }); S.ema20.applyOptions({ priceFormat: pf }); S.ema50.applyOptions({ priceFormat: pf });
  const bars = m.bars; S.barsByTime = new Map(bars.map((b) => [b.time, b]));
  S.candles.setData(bars.map((b) => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })));
  S.vol.setData(bars.map((b) => ({ time: b.time, value: b.volume, color: b.close >= b.open ? "rgba(58,208,138,.35)" : "rgba(255,92,92,.35)" })));
  const closes = bars.map((b) => b.close), e20 = ema(closes, 20), e50 = ema(closes, 50);
  S.ema20.setData(bars.map((b, i) => (e20[i] === null ? { time: b.time } : { time: b.time, value: e20[i] })));
  S.ema50.setData(bars.map((b, i) => (e50[i] === null ? { time: b.time } : { time: b.time, value: e50[i] })));
  // a decision made just after bar close describes the bar that just closed
  const tfs = Number(m.tf) * 60, first = bars[0].time;
  const marks = S.decisions.filter((d) => d.symbol === m.symbol && !d.error_kind && d.ts >= first).map((d) => ({
    time: Math.floor((d.ts - 10) / tfs) * tfs, position: d.action === "short" ? "aboveBar" : "belowBar",
    color: d.action === "long" ? css("--up") : d.action === "short" ? css("--down") : "#6a6a6a",
    shape: d.action === "long" ? "arrowUp" : d.action === "short" ? "arrowDown" : "circle", size: d.action === "flat" ? 0.35 : 1.1,
    text: d.action === "flat" ? "" : d.action.toUpperCase(),
  })).filter((mk) => S.barsByTime.has(mk.time)).sort((a, b) => a.time - b.time);
  const dedup = []; for (const mk of marks) { if (dedup.length && dedup[dedup.length - 1].time === mk.time) dedup[dedup.length - 1] = mk; else dedup.push(mk); }
  S.candles.setMarkers(dedup);
  if (reset) S.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, bars.length - 140), to: bars.length + 4 });
  renderOhlc(bars[bars.length - 1]);
}

/* ============================================================ selected-symbol market data */
async function loadMarket(reset) {
  if (!S.symbol) return;
  S.mkt = await api(`/api/market/${S.symbol}?tf=${S.tf}`);
  const m = S.mkt, t = m.ticker, pct = isNum(t.price24hPcnt) ? 100 * t.price24hPcnt : null;
  $$("#tfSeg button").forEach((b) => b.classList.toggle("on", b.dataset.tf === m.tf));
  $("#chartTitle").innerHTML = `<span class="w">${m.symbol}</span> <span class="w">${px(t.lastPrice)}</span> <span class="${ud(pct)}">${sg(pct, 2, "%")}</span>`;
  const tn = m.testnet_ticker?.lastPrice;
  const div = tn ? 10000 * (tn / t.lastPrice - 1) : null;
  $("#divergence").innerHTML = tn ? `TESTNET ${px(tn)} <span class="${Math.abs(div) > (S.ov?.settings.max_venue_divergence_bp || 150) ? "dn" : ""}" data-tip="Testnet last vs mainnet last. Brackets are placed relative to the testnet fill.">${sg(div, 1)}bp</span>` : "";
  drawChart(reset);
  renderBook(); renderStats();
  if (reset) renderKairos();
}
function renderBook() {
  const m = S.mkt; if (!m) return; const b = m.book, d = decimalsOf(m.ticker.lastPrice), N = 8;
  if (!b.bids.length || !b.asks.length) { $("#book").innerHTML = `<div class="empty">Book loading…</div>`; return; }
  const asks = b.asks.slice(0, N), bids = b.bids.slice(0, N);
  let ca = 0; const askRows = asks.map(([p, q]) => { ca += p * q; return [p, q, ca]; });
  let cb = 0; const bidRows = bids.map(([p, q]) => { cb += p * q; return [p, q, cb]; });
  const mx = Math.max(ca, cb);
  const row = (side) => ([p, q, c]) => `<tr class="${side}"><td class="${side === "ask" ? "dn" : "up"}">${fx(p, d)}</td><td class="r">${q.toLocaleString("en-US", { maximumFractionDigits: 4 })}</td><td class="r">${usd(c)}<span class="bar" style="width:${(100 * c / mx).toFixed(1)}%"></span></td></tr>`;
  const bb = bids[0][0], ba = asks[0][0], mid = (bb + ba) / 2, spr = 10000 * (ba - bb) / mid;
  const imb = (cb - ca) / (cb + ca);
  $("#book").innerHTML = `<table class="t"><thead><tr><th>Price</th><th class="r">Size</th><th class="r">Cum $</th></tr></thead><tbody>${askRows.reverse().map(row("ask")).join("")}<tr class="mid"><td colspan="3">SPREAD ${fx(ba - bb, d)} · ${fx(spr, 2)} bp · MID ${fx(mid, d + 1)}</td></tr>${bidRows.map(row("bid")).join("")}</tbody></table>
    <div class="depthbar" data-tip="Top-${N} depth: bids $${usd(cb)} vs asks $${usd(ca)}"><span style="width:${(50 + 50 * imb).toFixed(1)}%;background:var(--up)"></span><span style="flex:1;background:var(--down)"></span></div>
    <div class="muted" style="padding:0 8px;font-size:11px">TOP-${N} IMBALANCE ${sg(imb, 3)} (+ = bid heavy)</div>`;
  $("#bookAge").textContent = isNum(b.age_s) ? `upd ${age(b.age_s)} ago` : "";
}
function renderStats() {
  const m = S.mkt; if (!m) return;
  const t = m.ticker, st = m.state_preview?.state || {}, dv = st.derivatives || {}, ob = st.order_book || {}, of = st.order_flow || {};
  $("#statsTitle").textContent = `${m.symbol} · derivatives, flow & trend`;
  const kv = (rows) => `<div class="kv">${rows.map(([k, v, tp]) => `<div class="k"${tp ? ` data-tip="${esc(tp)}"` : ""}>${k}</div><div class="v">${v}</div>`).join("")}</div>`;
  const fr = isNum(t.fundingRate) ? 100 * t.fundingRate : null;
  const flow = (f, lab) => f ? [lab, `<span class="${ud(f.net_taker_flow_usd)}">${sg(f.net_taker_flow_usd / 1e3, 1, "K")}</span> · ${fx(100 * f.taker_buy_share, 1)}% buy`] : [lab, `<span class="muted">collecting…</span>`, "Not enough stream history yet: reported as unknown, never as zero"];
  const liq = (a, b) => (a === null || a === undefined ? `<span class="muted">collecting…</span>` : `<span class="dn">${usd(a)}</span> / <span class="up">${usd(b)}</span>`);
  const trend = st.trend_by_timeframe || {};
  $("#stats").innerHTML = `<div class="kvcols">
    <div><div class="colh">DERIVATIVES</div>${kv([
      ["Funding 8h", `<span class="${ud(fr)}">${sg(fr, 4, "%")}</span>`, "Positive: longs pay shorts"], ["Annualised", sg(isNum(fr) ? fr * 3 * 365 : null, 1, "%")],
      ["Mark − index", sg(dv.mark_minus_index_bp, 2, " bp")], ["Open interest", "$" + usd(Number(t.openInterestValue))],
      ["OI Δ 1h / 4h", `<span class="${ud(dv.open_interest_change_1h_pct)}">${sg(dv.open_interest_change_1h_pct, 2, "%")}</span> / <span class="${ud(dv.open_interest_change_4h_pct)}">${sg(dv.open_interest_change_4h_pct, 2, "%")}</span>`],
      ["Accounts long", isNum(dv.accounts_long_share) ? `${fx(100 * dv.accounts_long_share, 1)}% (${sg(100 * (dv.accounts_long_share_change_1h || 0), 1)} 1h)` : "—"],
    ])}</div>
    <div><div class="colh">TAKER FLOW &amp; LIQUIDATIONS</div>${kv([
      flow(of.last_5m, "Net taker 5m"), flow(of.last_15m, "Net taker 15m"), flow(of.last_60m, "Net taker 60m"),
      ["Liq L/S 15m", liq(dv.long_liquidations_15m_usd, dv.short_liquidations_15m_usd), "Long liquidations (forced selling) / short liquidations (forced buying)"],
      ["Liq L/S 60m", liq(dv.long_liquidations_60m_usd, dv.short_liquidations_60m_usd)],
      ["Stream coverage", `${of.stream_coverage_minutes ?? "—"} min`],
    ])}</div>
    <div><div class="colh">BOOK · VOLATILITY · CONTEXT</div>${kv([
      ["Spread", ob.available === false ? "stale" : `${fx(ob.spread_bp, 2)} bp`], ["Depth ≤10bp B/A", ob.available === false ? "—" : `${usd(ob.bid_depth_within_10bp_usd)} / ${usd(ob.ask_depth_within_10bp_usd)}`],
      ["24h range pos", isNum(st.range_position_24h_0_to_1) ? fx(st.range_position_24h_0_to_1, 2) : "—", "0 = at 24h low, 1 = at high; outside 0..1 = breakout"],
      ["BTC 1h / 4h", st.cross_market?.btc_return_1h_pct !== undefined ? `${sg(st.cross_market.btc_return_1h_pct, 2, "%")} / ${sg(st.cross_market.btc_return_4h_pct, 2, "%")}` : "—"],
      ["Bracket SL / TP", m.state_preview ? `${fx(m.state_preview.meta.sl_bp / 100, 2)}% / ${fx(m.state_preview.meta.tp_bp / 100, 2)}%` : "—", "Stop = max(1.5×ATR15m, 40bp), target = 1.67× stop"],
      ["Horizon / cost", m.state_preview ? `${m.state_preview.meta.horizon_min}m / ${fx(m.state_preview.meta.cost_bp, 1)}bp` : "—"],
    ])}</div></div>
    <table class="t"><thead><tr><th>TF</th><th class="r">Ret 12b %</th><th class="r">vs EMA20</th><th class="r">vs EMA50</th><th class="r">vs EMA200</th><th class="r">RSI14</th><th class="r">ADX14</th><th class="r">+DI − −DI</th><th class="r">ATR %</th><th class="r">BB pos</th><th class="r">Vol ×</th></tr></thead><tbody>${Object.entries(trend).map(([tf, b]) =>
      `<tr><td class="amb">${tf.toUpperCase()}</td><td class="r ${ud(b.return_last_12_bars_pct)}">${sg(b.return_last_12_bars_pct, 2)}</td><td class="r ${ud(b.price_vs_ema20_pct)}">${sg(b.price_vs_ema20_pct, 2)}</td><td class="r ${ud(b.price_vs_ema50_pct)}">${sg(b.price_vs_ema50_pct, 2)}</td><td class="r ${ud(b.price_vs_ema200_pct)}">${sg(b.price_vs_ema200_pct, 2)}</td><td class="r">${fx(b.rsi14, 1)}</td><td class="r">${fx(b.adx14, 1)}</td><td class="r ${ud(b.di_plus_minus_di)}">${sg(b.di_plus_minus_di, 1)}</td><td class="r">${fx(b.atr14_pct, 3)}</td><td class="r">${fx(b.bollinger_position_0_to_1, 2)}</td><td class="r">${fx(b.volume_vs_20bar_avg, 2)}</td></tr>`).join("")}</tbody></table>`;
  if (m.state_preview) { const n = JSON.stringify(m.state_preview.state).length; $("#stateSize").innerHTML = `<a href="#" id="showState">KAIROS STATE ≈${Math.round(n / 3.6)} TOK ›</a>`; $("#showState").onclick = (e) => { e.preventDefault(); showState(); }; }
}
function showState() {
  const p = S.mkt?.state_preview; if (!p) return;
  const n = JSON.stringify(p.state).length;
  $("#modalBody").innerHTML = `<div class="ph"><span class="code">STATE</span>Exact state sent to Kairos · ${S.symbol}</div><p>${n} chars ≈ ${Math.round(n / 3.6)} tokens (Kairos limit 4096 per question incl. instructions). Built ${age(Date.now() / 1000 - p.ts)} ago.</p><div><pre class="code">${esc(JSON.stringify(p.state, null, 1))}</pre></div>`;
  $("#modal").showModal();
}

/* ============================================================ relative performance */
const SERIES_C = ["--s1", "--s2", "--s3", "--s4", "--s5"];
async function loadPerf() {
  const d = await api("/api/perf"); const syms = Object.keys(d); if (!syms.length) return;
  if (!S.perf) {
    S.perf = LightweightCharts.createChart($("#perf"), { ...chartOpts(), handleScroll: false, handleScale: false });
    S.perfSeries = {};
  }
  const order = S.ov?.settings.symbols || syms;
  order.forEach((sym, i) => {
    if (!d[sym]) return;
    if (!S.perfSeries[sym]) S.perfSeries[sym] = S.perf.addLineSeries({ color: css(SERIES_C[i % 5]), lineWidth: 2, title: short(sym), priceLineVisible: false, crosshairMarkerRadius: 3, priceFormat: { type: "custom", minMove: 0.01, formatter: (v) => sg(v, 2, "%") } });
    S.perfSeries[sym].setData(d[sym].map(([t, v]) => ({ time: t, value: v })));
  });
  S.perf.timeScale().fitContent();
  $("#perfLegend").innerHTML = order.map((s, i) => { const v = d[s]?.[d[s].length - 1]?.[1]; return `<span style="color:var(${SERIES_C[i % 5]})">■</span><span class="w">${short(s)}</span>&nbsp;<span class="${ud(v)}">${sg(v, 2, "%")}</span>`; }).join("&nbsp;&nbsp;");
}

/* ============================================================ account */
function renderAccount() {
  const o = S.ov, a = o.account, w = a?.wallet || {};
  if (!a) { $("#account").innerHTML = `<div class="empty">${esc(o.executor_error || "No testnet keys")}</div>`; $("#positions").innerHTML = ""; return; }
  const cells = [["Equity", fx(w.equity), "USDT · UTA"], ["Available", fx(w.available), "USDT"], ["Unrealised", `<span class="${ud(w.unrealized_pnl)}">${sg(w.unrealized_pnl)}</span>`, "USDT"],
    ["Today PnL", `<span class="${ud(a.daily_pnl)}">${sg(a.daily_pnl)}</span>`, `halts at −${o.settings.daily_loss_limit_pct}%`], ["Positions", `${a.positions.length}/${o.settings.max_positions} <button class="mini" id="posCapBtn" data-tip="Change the max number of open positions. Applies to new entries only; open positions are not touched.">CAP</button>`, `${a.open_orders} open orders`],
    ["Risk / trade", `${o.settings.risk_per_trade_pct}%`, `of equity · ≤${o.settings.max_leverage}x`]];
  $("#account").innerHTML = `<div class="acct-grid">${cells.map(([l, n, s]) => `<div class="cell"><div class="l">${l}</div><div class="n">${n}</div><div class="s">${s}</div></div>`).join("")}</div>`;
  const cb = $("#posCapBtn"); if (cb) cb.onclick = () => setPosCap();
  $("#acctAge").textContent = a.last_sync ? `sync ${age(Date.now() / 1000 - a.last_sync)} ago` : "";
  const p = a.positions;
  $("#positions").innerHTML = p.length ? `<table class="t"><thead><tr><th>Sym</th><th>Side</th><th class="r">Size</th><th class="r">Entry</th><th class="r">Mark</th><th class="r">uPnL</th><th class="r">SL</th><th class="r">TP</th><th></th></tr></thead><tbody>${p.map((x) =>
    `<tr><td class="sym">${short(x.symbol)}</td><td><span class="tag ${x.side}">${x.side.toUpperCase()}</span></td><td class="r">${x.size}</td><td class="r">${x.entry}</td><td class="r">${x.mark}</td><td class="r ${ud(x.upnl)}">${sg(x.upnl)}</td><td class="r">${x.sl || '<span class="dn">NONE</span>'}</td><td class="r">${x.tp || "—"}</td><td><button class="mini" data-close="${x.symbol}">CLOSE</button></td></tr>`).join("")}</tbody></table>` : `<div class="empty">FLAT · no open positions</div>`;
  $$("#positions [data-close]").forEach((b) => (b.onclick = async () => { if (confirm(`Market-close ${b.dataset.close} on testnet (reduce-only)?`)) { await post("/api/close", { symbol: b.dataset.close }); loadOverview(); } }));
}

async function setPosCap(v) {
  const cur = S.ov.settings.max_positions;
  if (v == null) { v = prompt(`Max open positions (1–50). Now ${cur}.\nOnly affects new entries; open positions stay as they are.\nNote: one position per symbol, so with ${S.ov.settings.symbols.length} symbols at most ${S.ov.settings.symbols.length} can be open.`, cur); if (v == null) return; }
  try { await post("/api/settings", { max_positions: Number(v) }); await loadOverview(); } catch (e) { alert(e.message); }
}

/* ============================================================ Kairos */
function pbars(probs, labels) {
  const e = Object.entries(probs), mx = Math.max(...e.map((x) => x[1]));
  return e.map(([k, v]) => `<div class="pr"><span class="lb" title="${esc(labels?.[k] || k)}">${esc(labels?.[k] || k)}</span><div class="tr"><div class="fl ${v === mx ? "top" : ""}" style="width:${(100 * v).toFixed(1)}%"></div></div><span class="vv">${(100 * v).toFixed(0)}%</span></div>`).join("");
}
function renderKairos() {
  const d = S.latest[S.symbol], el = $("#kairos"), pv = S.mkt?.symbol === S.symbol ? S.mkt.state_preview : null;
  if (!d) {
    el.innerHTML = `<div class="kv-verdict"><span class="big muted">NO SIGNAL</span><span class="why">${S.ov?.kairos.configured ? `Kairos is asked on every closed ${S.ov.settings.decision_tf_minutes}m bar. Type ASK to ask now.` : `Add a Kairos key (type KEYS) to start receiving signals.`}</span></div>
      ${pv ? `<div class="kv"><div class="k">Next bracket SL</div><div class="v">${fx(pv.meta.sl_bp / 100, 2)}%</div><div class="k">Next bracket TP</div><div class="v">${fx(pv.meta.tp_bp / 100, 2)}%</div><div class="k">Horizon</div><div class="v">${pv.meta.horizon_min} min</div><div class="k">Round-trip cost</div><div class="v">${fx(pv.meta.cost_bp, 1)} bp</div><div class="k">State size</div><div class="v">≈${Math.round(JSON.stringify(pv.state).length / 3.6)} tokens</div></div>` : ""}`;
    return;
  }
  if (d.error_kind) { el.innerHTML = `<div class="kv-verdict"><span class="big dn">ERROR</span><span class="why">${esc(d.error_kind)}: ${esc(d.error_msg)}</span></div><div class="muted">${dt(d.ts)} · recorded as a request failure, never as a "no trade" judgment.</div>`; return; }
  const p = d.parsed || {}, sl = d.sl_bp / 100, tp = d.tp_bp / 100;
  const e = [-2, -1, -1 / 3, 1 / 3, 1, 2].map((x) => x * sl);
  const binL = {}; ["< " + fx(e[0], 2), `${fx(e[0], 2)}…${fx(e[1], 2)}`, `${fx(e[1], 2)}…${fx(e[2], 2)}`, `${fx(e[2], 2)}…${fx(e[3], 2)}`, `${fx(e[3], 2)}…${fx(e[4], 2)}`, `${fx(e[4], 2)}…${fx(e[5], 2)}`, "> " + fx(e[5], 2)].forEach((l, i) => (binL["bin_" + i] = l + "%"));
  const top = (q) => (p[q] ? Object.entries(p[q].probs).sort((a, b) => b[1] - a[1])[0] : null);
  const reg = top("regime"), crowd = top("crowding");
  el.innerHTML = `<div class="kv-verdict"><span class="big ${d.action === "long" ? "up" : d.action === "short" ? "dn" : "muted"}">${(d.action || "").toUpperCase()}</span>${d.exploration ? '<span class="tag xp" data-tip="Exploration trade: strict gates would have stayed flat">EXPLORE</span>' : ""}<span class="why">${esc(d.reason)}</span></div>
    <div class="evrow"><div><div class="l">EV long</div><div class="n ${ud(d.ev_long_r)}">${sg(d.ev_long_r, 3)}R</div></div><div><div class="l">EV short</div><div class="n ${ud(d.ev_short_r)}">${sg(d.ev_short_r, 3)}R</div></div><div><div class="l">Setup 0–4</div><div class="n">${fx(d.setup_quality, 2)}</div></div></div>
    ${["long_bracket", "short_bracket"].map((q) => (p[q] ? `<div class="qb"><div class="qt"><span>${q === "long_bracket" ? "Long" : "Short"} bracket</span><span>first touch ≤ ${S.ov?.settings.horizon_minutes ?? ""}m</span></div>${pbars(p[q].probs, { target_first: `target ${q === "long_bracket" ? "+" : "−"}${fx(tp, 2)}%`, stop_first: `stop ${q === "long_bracket" ? "−" : "+"}${fx(sl, 2)}%`, neither: "neither (time)" })}</div>` : "")).join("")}
    ${p.return_distribution ? `<div class="qb"><div class="qt"><span>Return distribution</span><span>E = ${sg(d.expected_move_in_d, 2)} × stop</span></div>${pbars(p.return_distribution.probs, binL)}</div>` : ""}
    <div class="kfoot"><div><b>Regime</b>${reg ? `${reg[0].replace("_", " ")} ${(100 * reg[1]).toFixed(0)}%` : "—"}</div><div><b>Crowding</b>${crowd ? `${crowd[0].replace("_", " ")} ${(100 * crowd[1]).toFixed(0)}%` : "—"}</div><div><b>Mean rev · data ok</b>${p.mean_reversion ? (100 * p.mean_reversion.p).toFixed(0) + "%" : "—"} · ${p.data_sufficient ? (100 * p.data_sufficient.p).toFixed(0) + "%" : "—"}</div></div>
    <div class="kfoot"><div><b>Asked</b>${hhmmss(d.ts)} (${age(Date.now() / 1000 - d.ts)} ago)</div><div><b>Latency</b>${fx(d.latency_ms, 0)} ms</div><div><b>Coherence · issues</b>${fx(d.coherence, 2)} · <span class="${d.issues?.length ? "dn" : ""}">${d.issues?.length || 0}</span></div></div>`;
}
async function ask(probe) {
  const b = probe ? $("#askProbe") : $("#askNow"); b.disabled = true; const t = b.textContent; b.textContent = "…";
  try { await post("/api/decide", { symbol: S.symbol, probe }); await loadDecisions(); } catch (e) { alert(e.message); }
  b.disabled = false; b.textContent = t;
}
$("#askNow").onclick = () => ask(false); $("#askProbe").onclick = () => ask(true);

/* ============================================================ decisions / blotter */
async function loadDecisions() {
  S.decisions = await api("/api/decisions?limit=400");
  S.latest = {}; for (const d of S.decisions) if (!S.latest[d.symbol]) S.latest[d.symbol] = d;
  renderBlotter(); renderKairos(); drawChart(false);
}
function renderBlotter() {
  const rows = S.decisions.slice(0, 150);
  $("#blotter").innerHTML = rows.length ? `<table class="t"><thead><tr><th>Time</th><th>Sym</th><th>Signal</th><th class="r">Price</th><th class="r">EV long</th><th class="r">EV short</th><th class="r">Setup</th><th class="r">SL/TP %</th><th class="r">ms</th><th>Outcome (labelled)</th><th>Reason / gates</th></tr></thead><tbody>${rows.map((d) => {
    const l = d.label, out = l && !l.unlabelable ? `L ${l.long.outcome.replace("_first", "").toUpperCase()} · S ${l.short.outcome.replace("_first", "").toUpperCase()} · <span class="${ud(l.return_pct)}">${sg(l.return_pct, 2, "%")}</span>` : `<span class="muted">pending · scored after ${S.ov ? S.ov.settings.horizon_minutes + "m" : "horizon"}</span>`;
    return `<tr class="click ${d.symbol === S.symbol ? "sel" : ""}" data-id="${d.id}"><td class="muted">${dt(d.ts)}</td><td class="sym">${short(d.symbol)}</td><td>${d.error_kind ? `<span class="tag err">${esc(d.error_kind)}</span>` : `<span class="tag ${d.action}">${(d.action || "").toUpperCase()}</span>${d.exploration ? ' <span class="tag xp">XP</span>' : ""}`}</td><td class="r">${isNum(d.price) ? String(d.price) : "—"}</td><td class="r ${ud(d.ev_long_r)}">${sg(d.ev_long_r, 3)}</td><td class="r ${ud(d.ev_short_r)}">${sg(d.ev_short_r, 3)}</td><td class="r">${fx(d.setup_quality, 2)}</td><td class="r">${fx(d.sl_bp / 100, 2)}/${fx(d.tp_bp / 100, 2)}</td><td class="r">${fx(d.latency_ms, 0)}</td><td>${out}</td><td class="muted wrap">${esc(d.reason || d.error_msg || "")}</td></tr>`;
  }).join("")}</tbody></table>` : `<div class="empty">No Kairos decisions yet</div>`;
  $$("#blotter tr[data-id]").forEach((tr) => (tr.onclick = () => openDecision(tr.dataset.id)));
}
async function openDecision(id) {
  const d = await api(`/api/decision/${id}`);
  const j = (x) => `<pre class="code">${esc(JSON.stringify(x, null, 1) ?? "—")}</pre>`;
  $("#modalBody").innerHTML = `<div class="ph"><span class="code">DEC</span>Decision #${d.id} · ${d.symbol} · ${dt(d.ts)}</div>
    <p>question set ${esc(d.qset_version)} · ${fx(d.latency_ms, 0)} ms · ${d.request_bytes ?? "—"} bytes · attempts ${d.attempts ?? "—"} · request_id ${esc(d.request_id || "—")}</p>
    ${d.issues_json?.length ? `<p class="dn">Contract issues: ${d.issues_json.map(esc).join("; ")}</p>` : ""}
    <div class="mgrid"><div><h3>State sent</h3>${j(d.state_json)}</div><div><h3>Raw answers</h3>${j(d.answers_json)}<h3>Usage · model metadata</h3>${j({ usage: d.usage_json, meta: d.model_meta_json })}</div>
    <div><h3>Policy decision</h3>${j(d.decision_json)}<h3>Realised label</h3>${j(d.label_json || "pending")}</div><div><h3>Questions</h3>${j(d.questions_json)}${d.probes?.length ? `<h3>Probes</h3>${j(d.probes)}` : ""}</div></div>`;
  $("#modal").showModal();
}

/* ============================================================ evaluation */
function reliability(cal) {
  if (!cal?.bins?.length) return `<div class="empty">Needs labelled decisions (each is scored ${S.ov?.settings.horizon_minutes || 120} min after it is made)</div>`;
  const W = 420, H = 250, P = 34, x = (v) => P + v * (W - 2 * P), y = (v) => H - P - v * (H - 2 * P), mxN = Math.max(...cal.bins.map((b) => b.n));
  const grid = [0, 0.25, 0.5, 0.75, 1].map((t) => `<line x1="${x(t)}" x2="${x(t)}" y1="${y(0)}" y2="${y(1)}" stroke="#151515"/><line x1="${x(0)}" x2="${x(1)}" y1="${y(t)}" y2="${y(t)}" stroke="#151515"/><text x="${x(t)}" y="${H - P + 14}" text-anchor="middle">${t * 100}%</text><text x="${P - 6}" y="${y(t) + 3}" text-anchor="end">${t * 100}%</text>`).join("");
  const pts = cal.bins.map((b) => `<circle cx="${x(b.mean_pred)}" cy="${y(b.observed)}" r="${4 + 6 * Math.sqrt(b.n / mxN)}" fill="${css("--amber")}" stroke="#000" stroke-width="2" data-tip="predicted ${(100 * b.mean_pred).toFixed(1)}% → observed ${(100 * b.observed).toFixed(1)}%<br>n = ${b.n}"/>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" width="100%">${grid}<line x1="${x(0)}" y1="${y(0)}" x2="${x(1)}" y2="${y(1)}" stroke="#5a5a5a" stroke-dasharray="4 4"/>${pts}<text x="${W / 2}" y="${H - 3}" text-anchor="middle">predicted probability → · observed frequency ↑</text></svg><div class="muted">On the dashed line = calibrated · ECE ${fx(cal.ece, 3)} · dot size = sample count</div>`;
}
function hbars(items, vk, lk, unit = "", signed = true) {
  if (!items?.length) return `<div class="empty">Needs labelled decisions</div>`;
  const W = 420, rh = 24, H = items.length * rh + 8, L = 120, R = 56, mx = Math.max(0.01, ...items.map((i) => Math.abs(i[vk])));
  const z = signed ? L + (W - L - R) / 2 : L, sc = (signed ? (W - L - R) / 2 : W - L - R) / mx;
  return `<svg viewBox="0 0 ${W} ${H}" width="100%">${signed ? `<line x1="${z}" x2="${z}" y1="0" y2="${H}" stroke="#5a5a5a"/>` : ""}${items.map((it, i) => {
    const v = it[vk], w = Math.abs(v) * sc, x0 = v >= 0 ? z : z - w, yy = 4 + i * rh;
    return `<text x="${L - 6}" y="${yy + 13}" text-anchor="end">${esc(it[lk])}</text><rect x="${x0}" y="${yy + 3}" width="${Math.max(w, 1)}" height="14" fill="${signed ? (v >= 0 ? css("--up") : css("--down")) : css("--amber")}" data-tip="${esc(it[lk])}: ${fx(v, 3)}${unit}${it.n !== undefined ? ` · n=${it.n}` : ""}${it.win_rate !== undefined ? ` · win ${(100 * it.win_rate).toFixed(0)}%` : ""}"/><text x="${W - 4}" y="${yy + 13}" text-anchor="end">${signed ? sg(v, 2) : fx(v, 0)}${unit}</text>`;
  }).join("")}</svg>`;
}
const kvT = (rows) => `<div class="kv">${rows.map(([k, v, tp]) => `<div class="k"${tp ? ` data-tip="${esc(tp)}"` : ""}>${k}</div><div class="v">${v}</div>`).join("")}</div>`;
async function loadEval() {
  const q = new URLSearchParams(); if ($("#evalTf").value) q.set("tf", $("#evalTf").value); if ($("#evalSymbol").value) q.set("symbol", $("#evalSymbol").value); if ($("#evalHours").value) q.set("hours", $("#evalHours").value);
  const m = await api("/api/metrics?" + q), mq = m.model_quality, ops = m.ops, edge = m.edge, cons = m.consistency;
  const cells = [
    ["Kairos requests", ops.requests, `${ops.ok} ok · err ${fx(100 * (ops.error_rate || 0), 1)}%`], ["Labelled", m.labelled, "scored against reality"],
    ["Latency p50", isNum(ops.latency_ms_p50) ? fx(ops.latency_ms_p50, 0) + " ms" : "—", `p90 ${isNum(ops.latency_ms_p90) ? fx(ops.latency_ms_p90, 0) : "—"} ms`],
    ["Brier skill long", sg(mq.long_bracket?.skill_vs_climatology, 3), "> 0 beats base rates"], ["Brier skill short", sg(mq.short_bracket?.skill_vs_climatology, 3), "> 0 beats base rates"],
    ["AUC target first", `${fx(mq.long_bracket?.auc_target_first, 2)} / ${fx(mq.short_bracket?.auc_target_first, 2)}`, "long / short · 0.50 = random"],
    ["ρ(EV, realised R)", sg(edge.spearman_ev_vs_realised_r, 3), `n = ${edge.n || 0} side-decisions`],
    ["Strict signals R", sg(edge.strict_signals_mean_r, 3), `n = ${edge.strict_signals_n || 0} · win ${isNum(edge.strict_signals_win_rate) ? (100 * edge.strict_signals_win_rate).toFixed(0) + "%" : "—"}`],
    ["Explore signals R", sg(edge.exploration_signals_mean_r, 3), `n = ${edge.exploration_signals_n || 0} · win ${isNum(edge.exploration_signals_win_rate) ? (100 * edge.exploration_signals_win_rate).toFixed(0) + "%" : "—"}`],
  ];
  $("#evalTiles").innerHTML = cells.map(([l, n, s]) => `<div class="cell"><div class="l">${l}</div><div class="n">${n}</div><div class="s">${s}</div></div>`).join("");
  $("#observations").innerHTML = (m.observations || []).map((o) => `<li>${esc(o)}</li>`).join("") || "<li>No findings yet.</li>";
  $("#calLong").innerHTML = reliability(mq.long_bracket?.calibration_target_first);
  $("#calShort").innerHTML = reliability(mq.short_bracket?.calibration_target_first);
  $("#evBuckets").innerHTML = hbars(edge.by_predicted_ev, "mean_realised_r", "ev_bucket", "R") + `<div class="muted">Predicted EV bucket (R) → mean realised bracket R net of costs, over every labelled bar and both sides. Rising top→bottom = the model ranks trades.</div>`;
  const pit = mq.return_distribution?.pit_histogram_quintiles;
  $("#pit").innerHTML = pit ? hbars(pit.map((n, i) => ({ q: `Q${i + 1}`, v: n, n })), "v", "q", "", false) + `<div class="muted">Flat = calibrated · U-shape = overconfident · hump = underconfident</div>` : `<div class="empty">Needs labelled decisions</div>`;
  const qr = (b, k = "brier") => (b && b.n ? `<tr><td class="amb">${esc(b.name)}</td><td class="r">${b.n}</td><td class="r">${fx(b[k], 4)}</td><td class="r">${fx(b[k + "_climatology"], 4)}</td><td class="r">${fx(b[k + "_uniform"], 4)}</td><td class="r ${ud(b.skill_vs_climatology)}">${sg(b.skill_vs_climatology, 3)}</td><td class="r">${fx(b.accuracy_argmax, 3)} / ${fx(b.accuracy_majority_baseline, 3)}</td></tr>` : "");
  const mr = mq.mean_reversion;
  $("#qTable").innerHTML = `<table class="t"><thead><tr><th>Question</th><th class="r">n</th><th class="r">Score</th><th class="r">Climat.</th><th class="r">Uniform</th><th class="r">Skill</th><th class="r">Acc / majority</th></tr></thead><tbody>${qr(mq.long_bracket)}${qr(mq.short_bracket)}${qr(mq.return_distribution, "rps")}${qr(mq.regime)}${mr ? `<tr><td class="amb">mean_reversion</td><td class="r">${mr.n}</td><td class="r">${fx(mr.brier, 4)}</td><td class="r">${fx(mr.brier_climatology, 4)}</td><td class="r">—</td><td class="r ${ud(mr.skill_vs_climatology)}">${sg(mr.skill_vs_climatology, 3)}</td><td class="r">AUC ${fx(mr.auc, 3)}</td></tr>` : ""}</tbody></table><div class="muted" style="padding:4px 8px">Multiclass Brier, or RPS for ordered bins. Lower is better. Skill = 1 − score / climatology.</div>`;
  $("#consistency").innerHTML = kvT([["Repeat probes", cons.repeat_probes], ["Identical answers", fx(cons.repeat_identical_share, 3)], ["Mean max |Δp| on repeat", fx(cons.repeat_max_abs_prob_diff_mean, 4)],
    ["Mirror probes", cons.mirror_probes], ["Mirror abs error", fx(cons.mirror_abs_error_mean, 4), "|P long target (orig) − P short target (mirrored)|; 0 = symmetric"], ["Mirror lean flips", fx(cons.mirror_lean_flips_sign_share, 3)],
    ["Coherence mean", fx(cons.coherence_mean, 3)], ["Both sides EV > 0", cons.both_sides_positive_ev], ["Both targets > 50%", cons.both_targets_above_50pct]]);
  $("#ops").innerHTML = kvT([["Errors", Object.entries(ops.errors || {}).map(([k, v]) => `${k} ${v}`).join(", ") || "none"], ["Contract issues", ops.responses_with_issues],
    ["Latency p99", isNum(ops.latency_ms_p99) ? fx(ops.latency_ms_p99, 0) + " ms" : "—"], ["Input tokens mean / max", `${fx(ops.input_tokens_mean, 0)} / ${ops.input_tokens_max ?? "—"}`],
    ["Request bytes mean", fx(ops.request_bytes_mean, 0)], ["Retried", ops.retried_requests], ["Model versions", Object.keys(ops.model_versions || {}).join(", ") || "—"],
    ["Policy actions", Object.entries(m.policy.actions).map(([k, v]) => `${k} ${v}`).join(", ") || "—"]]);
  const t = m.trading;
  $("#tradingStats").innerHTML = t.closed ? kvT([["Closed trades", t.closed], ["Net PnL (after fees)", `<span class="${ud(t.net_pnl_usdt)}">${sg(t.net_pnl_usdt)} USDT</span>`], ["Fees", `${fx(t.fees_usdt)} USDT`],
    ["Win rate", fx(t.win_rate, 3)], ["Profit factor", fx(t.profit_factor, 2)], ["Avg realised R", sg(t.avg_r, 3)], ["Avg predicted EV", sg(t.avg_predicted_ev_r, 3) + "R"],
    ["Max drawdown", `${fx(t.max_drawdown_usdt)} USDT`], ["By mode", Object.entries(t.by_mode || {}).map(([k, v]) => `${k} ${v.n} · ${sg(v.pnl)} · win ${(100 * v.win_rate).toFixed(0)}%`).join(" | ") || "—"], ["Exit reasons", Object.entries(t.by_reason).map(([k, v]) => `${k} ${v}`).join(", ")]]) : `<div class="empty">No closed testnet trades yet</div>`;
  $("#rawMetrics").textContent = JSON.stringify(m, null, 1);
}
$("#evalRefresh").onclick = loadEval; $("#evalSymbol").onchange = loadEval; $("#evalTf").onchange = loadEval; $("#evalHours").onchange = loadEval;
$("#reportBtn").onclick = async () => { const md = await api("/api/report"); const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([md], { type: "text/markdown" })); a.download = `kairos-report-${new Date().toISOString().slice(0, 16)}.md`; a.click(); };

/* ============================================================ trades / news / log */
async function loadTrades() {
  const ts = await api("/api/trades");
  $("#tradesTable").innerHTML = ts.length ? `<table class="t"><thead><tr><th>Opened</th><th>Sym</th><th>Side</th><th class="r">Qty</th><th class="r">Entry</th><th class="r">TP</th><th class="r">SL</th><th>Mode</th><th>Protection</th><th>Status</th><th>Exit</th><th class="r">Exit px</th><th class="r">PnL</th><th class="r">Fees</th><th class="r">R</th><th class="r">EV R</th><th>Dec</th></tr></thead><tbody>${ts.map((t) => {
    const r = isNum(t.realized_pnl) && t.risk_usd ? t.realized_pnl / t.risk_usd : null;
    return `<tr><td class="muted">${dt(t.opened_ts)}</td><td class="sym">${short(t.symbol)}</td><td><span class="tag ${t.side}">${t.side.toUpperCase()}</span></td><td class="r">${t.qty}</td><td class="r">${t.entry_price ?? "—"}</td><td class="r">${t.tp_price}</td><td class="r">${t.sl_price}</td><td>${t.notes === "exploration" ? '<span class="tag xp">EXPLORE</span>' : t.notes === "strict" ? '<span class="tag amb">STRICT</span>' : ""}</td><td class="muted">${esc(t.protection)}</td><td>${esc(t.status)}</td><td>${esc(t.close_reason || "")}</td><td class="r">${t.exit_price ?? "—"}</td><td class="r ${ud(t.realized_pnl)}">${sg(t.realized_pnl)}</td><td class="r">${fx(t.fees, 3)}</td><td class="r ${ud(r)}">${sg(r)}</td><td class="r">${sg(t.ev_r, 3)}</td><td><a href="#" data-d="${t.decision_id}">#${t.decision_id}</a></td></tr>`;
  }).join("")}</tbody></table>` : `<div class="empty">No trades yet. They appear once a Kairos key is set and AUTO-TRADE is on.</div>`;
  $$("#tradesTable [data-d]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); openDecision(a.dataset.d); }));
}
async function loadNews() { S.news = await api("/api/news"); }
async function loadNewsView() {
  await loadNews(); const n = S.news;
  $("#newsForecasts").innerHTML = n.forecasts.length ? `<table class="t"><thead><tr><th>Time</th><th>Asset</th><th>Headline</th><th>Distribution −4c … +4c</th><th class="r">Up − down</th><th class="r">Realised 15m</th></tr></thead><tbody>${n.forecasts.map((f) => {
    const p = f.probs_json, lean = p ? p.slice(4).reduce((a, b) => a + b, 0) - p.slice(0, 3).reduce((a, b) => a + b, 0) : null;
    const dist = p ? `<svg width="146" height="20">${p.map((v, i) => `<rect x="${i * 21}" y="${20 - v * 20}" width="18" height="${Math.max(v * 20, 1)}" fill="${i < 3 ? css("--down") : i > 3 ? css("--up") : "#6a6a6a"}" data-tip="bin ${i}: ${(100 * v).toFixed(1)}%"/>`).join("")}</svg>` : `<span class="tag err">${esc(f.error_kind || "—")}</span>`;
    return `<tr><td class="muted">${dt(f.ts)}</td><td class="sym">${short(f.symbol)}</td><td class="wrap">${esc(f.title)} <span class="muted">${esc(f.source)}</span></td><td>${dist}</td><td class="r ${ud(lean)}">${sg(lean, 2)}</td><td class="r">${f.label_json ? `${sg(f.label_json.return_bp, 1)} bp · bin ${f.label_json.bin}` : '<span class="muted">pending</span>'}</td></tr>`;
  }).join("")}</tbody></table>` : `<div class="empty">No news forecasts yet. New headlines that mention a traded asset are sent to Kairos news_return.</div>`;
  $("#headlines").innerHTML = `<table class="t"><tbody>${n.headlines.map((h) => `<tr><td class="muted">${h.published_ts ? dt(h.published_ts) : ""}</td><td class="amb">${esc(h.source)}</td><td class="wrap"><a href="${esc(h.link)}" target="_blank" rel="noopener" style="color:var(--text)">${esc(h.title)}</a> ${h.symbols.map((s) => `<span class="tag flat">${short(s)}</span>`).join(" ")}${h.market_wide ? ' <span class="tag amb">MKT</span>' : ""}</td></tr>`).join("")}</tbody></table>`;
}
async function loadEvents() {
  const ev = await api("/api/events");
  $("#events").innerHTML = `<table class="t"><thead><tr><th>Time</th><th>Level</th><th>Kind</th><th>Sym</th><th>Message</th></tr></thead><tbody>${ev.map((e) => `<tr><td class="muted">${dt(e.ts)}</td><td><span class="tag ${e.level === "error" ? "err" : e.level === "trade" ? "long" : e.level === "warn" ? "amb" : "flat"}">${esc(e.level).toUpperCase()}</span></td><td>${esc(e.kind)}</td><td class="sym">${e.symbol ? short(e.symbol) : ""}</td><td class="wrap">${esc(e.message)}</td></tr>`).join("")}</tbody></table>`;
}

/* ============================================================ controls & keys */
$("#tradingToggle").onchange = async (e) => {
  const on = e.target.checked;
  if (on && !confirm("Enable AUTO-TRADE: the bot will place orders on your Bybit TESTNET account when Kairos signals pass all gates.")) { e.target.checked = false; return; }
  try { await post("/api/trading", { enabled: on }); } catch (err) {
    if (/halted/.test(err.message) && confirm(err.message + "\n\nClear the halt and resume?")) await post("/api/trading", { enabled: true, clear_halt: true });
    else { alert(err.message); e.target.checked = false; }
  }
  loadOverview();
};
$("#exploreToggle").onchange = async (e) => {
  const on = e.target.checked;
  if (on && !confirm(`EXPLORE: trade Kairos's preferred side even when the strict gates say the edge is unproven.\n\nRisk per exploration trade: ${S.ov?.settings.explore_risk_pct ?? 0.1}% of equity. Safety gates (one position per symbol, max positions, venue divergence, daily loss limit, kill switch) still apply.\n\nOrders are only placed while AUTO-TRADE is also on.`)) { e.target.checked = false; return; }
  try { await post("/api/settings", { exploration_mode: on }); } catch (err) { alert(err.message); e.target.checked = !on; }
  loadOverview();
};
$("#killBtn").onclick = async () => { if (confirm("KILL: close all bot positions on testnet, cancel orders and stop auto-trade?")) { await post("/api/kill"); loadOverview(); } };

const stat = (ok, txt) => `<span class="${ok ? "up" : "dn"}">● ${esc(txt)}</span>`;
function setMsg(sel, ok, txt) { const el = $(sel); el.className = "keymsg " + (ok ? "ok" : "bad"); el.textContent = txt; }
async function busy(btn, fn) { const t = btn.textContent; btn.disabled = true; btn.textContent = "WORKING…"; try { await fn(); } finally { btn.disabled = false; btn.textContent = t; } }
async function loadKeys() {
  const k = await api("/api/keys");
  $("#kairosStatus").innerHTML = k.kairos.set ? stat(true, `SET ${k.kairos.hint}`) : stat(false, "NOT SET");
  $("#kairosUrl").placeholder = k.kairos.base_url;
  const b = k.bybit;
  $("#bybitStatus").innerHTML = b.api_key.set ? stat(b.connected, `KEY ${b.api_key.hint} · ${b.connected ? "CONNECTED" : "ERROR"}`) : stat(false, "NOT SET");
  if (b.error) setMsg("#bybitMsg", false, b.error);
}
function openKeys() { $("#kairosMsg").textContent = $("#bybitMsg").textContent = ""; loadKeys(); $("#keysModal").showModal(); }
const kairosResult = (r) => (r.ok ? setMsg("#kairosMsg", true, `Kairos reachable · ${r.latency_ms} ms · model ${r.model || "DMIND/Kairos"}`) : setMsg("#kairosMsg", false, r.error));
$("#keysBtn").onclick = openKeys;
$("#kairosSave").onclick = (e) => busy(e.target, async () => {
  const key = $("#kairosKey").value.trim(); if (!key) return setMsg("#kairosMsg", false, "Paste a key first");
  try { const r = await post("/api/keys/kairos", { api_key: key, base_url: $("#kairosUrl").value.trim() || null }); $("#kairosKey").value = ""; kairosResult(r); } catch (err) { setMsg("#kairosMsg", false, err.message); }
  loadKeys(); loadOverview();
});
$("#kairosTest").onclick = (e) => busy(e.target, async () => { try { kairosResult(await post("/api/keys/kairos/test")); } catch (err) { setMsg("#kairosMsg", false, err.message); } });
$("#bybitSave").onclick = (e) => busy(e.target, async () => {
  const key = $("#bybitKey").value.trim(), secret = $("#bybitSecret").value.trim();
  if (!key || !secret) return setMsg("#bybitMsg", false, "Both key and secret are required");
  try { const r = await post("/api/keys/bybit", { api_key: key, api_secret: secret }); $("#bybitKey").value = $("#bybitSecret").value = ""; setMsg("#bybitMsg", true, `Saved · testnet equity ${fx(r.equity)} USDT`); } catch (err) { setMsg("#bybitMsg", false, err.message); }
  loadKeys(); loadOverview();
});

/* ============================================================ live loop */
function connect() {
  const es = new EventSource("/stream");
  es.onmessage = (e) => {
    const { kind } = JSON.parse(e.data);
    if (kind === "decision" || kind === "probe" || kind === "labels") loadDecisions();
    else if (kind === "trade") { loadOverview(); if (S.view === "trades") loadTrades(); }
    else if (kind === "news") { loadNews(); if (S.view === "news") loadNewsView(); }
    else if (kind === "settings") loadOverview();
    else if (kind === "event" && S.view === "log") loadEvents();
  };
  es.onerror = () => { es.close(); setTimeout(connect, 3000); };
}
const every = (ms, fn) => setInterval(() => { if (!document.hidden) fn().catch(() => {}); }, ms);
(async function init() {
  await loadOverview();
  await Promise.all([loadMonitor(), loadMarket(true), loadDecisions(), loadPerf(), loadNews()].map((p) => p.catch((e) => console.warn(e))));
  connect();
  every(2000, loadMonitor);
  every(2000, () => (S.view === "mon" ? loadMarket(false) : Promise.resolve()));
  every(5000, loadOverview);
  every(30000, loadPerf);
  every(30000, loadNews);
  every(30000, loadDecisions);
})();
