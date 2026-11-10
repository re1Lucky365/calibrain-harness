"use client";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { post } from "@/lib/api";
import { loadOverview, selectSymbol, selectTf } from "@/lib/data";
import { getState, setState, useStore } from "@/lib/store";
import { age, short } from "@/lib/format";

const CITIES: [string, string][] = [["New York", "America/New_York"], ["London", "Europe/London"], ["Dubai", "Asia/Dubai"], ["Mumbai", "Asia/Kolkata"],
  ["Singapore", "Asia/Singapore"], ["Hong Kong", "Asia/Hong_Kong"], ["Tokyo", "Asia/Tokyo"], ["Sydney", "Australia/Sydney"], ["UTC", "UTC"]];

export function useNow(ms = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

export function Clocks() {
  const now = useNow();
  const [fmt] = useState(() => Object.fromEntries(CITIES.map(([, tz]) =>
    [tz, new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" })])));
  const [local, setLocal] = useState("");
  useEffect(() => setLocal(Intl.DateTimeFormat().resolvedOptions().timeZone), []);
  return (
    <div className="clocks">
      {CITIES.map(([c, tz]) => (
        <div key={tz} className={`clock${tz === local ? " local" : ""}`}>
          <div className="t" suppressHydrationWarning>{local ? fmt[tz].format(now) : "--:--:--"}</div>
          <div className="c">{c}{tz === local && <span className="mk">LOCAL</span>}</div>
        </div>
      ))}
    </div>
  );
}

const PLACEHOLDER = "BTC · ETH · SOL · ASK · EVAL · KEYS · HELP";

export function CommandBar() {
  const symbol = useStore((s) => s.symbol);
  const ov = useStore((s) => s.ov);
  const hint = useStore((s) => s.cmdHint);
  const input = useRef<HTMLInputElement>(null);

  const flash = (txt: string) => {
    setState({ cmdHint: txt });
    setTimeout(() => setState({ cmdHint: null }), 2500);
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const raw = (input.current?.value || "").trim().toUpperCase();
    if (input.current) input.current.value = "";
    if (!raw) return;
    const syms: string[] = ov?.settings.symbols || [];
    const sym = syms.find((s) => s === raw || s === raw + "USDT");
    const tfm = ({ "1M": "1", "5M": "5", "15M": "15", "1H": "60", "4H": "240" } as Record<string, string>)[raw];
    const pc = raw.match(/^(?:POS|MAXPOS)\s+(\d+)$/);
    const ttf = raw.match(/^(?:TF|TRADE)\s+(1M|5M|15M|1H)$/);
    const views: Record<string, any> = { MON: "mon", EVAL: "eval", TRD: "trades", TRADES: "trades", NEWS: "news", N: "news", LOG: "log" };
    if (pc) return setPosCap(Number(pc[1]));
    if (ttf) return setState({ modal: { kind: "tf", data: ({ "1M": "1", "5M": "5", "15M": "15", "1H": "60" } as any)[ttf[1]] } });
    if (sym) return selectSymbol(sym);
    if (tfm) return selectTf(tfm);
    if (views[raw]) return setState({ view: views[raw] });
    if (raw === "ASK" || raw === "PRB") return ask(raw === "PRB");
    if (raw === "KEYS") return setState({ modal: { kind: "keys" } });
    if (raw === "UNIV" || raw === "SYMBOLS" || raw === "SYM") return setState({ modal: { kind: "universe" } });
    if (raw === "HELP" || raw === "?") return setState({ modal: { kind: "help" } });
    flash(`UNKNOWN COMMAND "${raw}" · type HELP`);
  };

  const onTrading = async (on: boolean) => {
    if (on && !confirm("Enable AUTO-TRADE: the bot will place orders on your Bybit TESTNET account when Kairos signals pass all gates.")) return;
    try {
      await post("/api/trading", { enabled: on });
    } catch (err: any) {
      if (/halted/.test(err.message) && confirm(err.message + "\n\nClear the halt and resume?")) await post("/api/trading", { enabled: true, clear_halt: true });
      else alert(err.message);
    }
    loadOverview();
  };
  const onExplore = async (on: boolean) => {
    if (on && !confirm(`EXPLORE: trade Kairos's preferred side even when the strict gates say the edge is unproven.\n\nRisk per exploration trade: ${ov?.settings.explore_risk_pct ?? 0.1}% of equity. Safety gates still apply. Orders are only placed while AUTO-TRADE is also on.`)) return;
    try { await post("/api/settings", { exploration_mode: on }); } catch (err: any) { alert(err.message); }
    loadOverview();
  };
  const onKill = async () => {
    if (confirm("KILL: close all bot positions on testnet, cancel orders and stop auto-trade?")) {
      await post("/api/kill");
      loadOverview();
    }
  };

  return (
    <header className="cmdbar">
      <div className="brand"><span className="brand-mark">CTT</span><span className="brand-name">CLASSIFIER TRADING TERMINAL</span></div>
      <form className="cmd" autoComplete="off" onSubmit={onSubmit}>
        <span className="cmd-sec">{symbol ?? "—"} PERP</span>
        <input id="cmd" ref={input} spellCheck={false} placeholder={hint ?? PLACEHOLDER} />
        <button className="go" type="submit">GO</button>
      </form>
      <div className="cmd-right">
        <label className="arm explore" data-tip="Exploration: trade Kairos's preferred side even when strict quality gates block it, at reduced risk. Safety gates still apply.">
          <input type="checkbox" checked={!!ov?.settings.exploration_mode} onChange={(e) => onExplore(e.target.checked)} />
          <span className="arm-box" /><span>EXPLORE</span>
        </label>
        <label className="arm" data-tip="Allow the bot to place orders on your Bybit testnet account">
          <input type="checkbox" checked={!!ov?.settings.trading_enabled} onChange={(e) => onTrading(e.target.checked)} />
          <span className="arm-box" /><span>AUTO-TRADE</span>
        </label>
        <button className="kbtn" onClick={() => setState({ modal: { kind: "keys" } })}>KEYS</button>
        <button className="kbtn red" onClick={onKill} data-tip="Close bot positions, cancel orders, stop trading">KILL</button>
      </div>
    </header>
  );
}

export async function ask(probe: boolean) {
  const sym = getState().symbol;
  if (!sym) return;
  setState({ cmdHint: `ASKING KAIROS · ${sym}${probe ? " + probes" : ""}…` });
  try {
    await post("/api/decide", { symbol: sym, probe });
  } catch (e: any) {
    alert(e.message);
  }
  setState({ cmdHint: null });
}

export async function setPosCap(v?: number) {
  const st = getState().ov?.settings;
  if (!st) return;
  let n = v;
  if (n == null) {
    const r = prompt(`Max open positions (1–50). Now ${st.max_positions}.\nOnly affects new entries; open positions stay as they are.\nOne position per symbol, so with ${st.symbols.length} symbols at most ${st.symbols.length} can be open.`, String(st.max_positions));
    if (r == null) return;
    n = Number(r);
  }
  try {
    await post("/api/settings", { max_positions: n });
    await loadOverview();
  } catch (e: any) {
    alert(e.message);
  }
}

const VIEWS = [["mon", "F1", "MON"], ["eval", "F2", "EVAL"], ["trades", "F3", "TRADES"], ["news", "F4", "NEWS"], ["log", "F5", "LOG"]] as const;

export function FKeys() {
  const view = useStore((s) => s.view);
  const st = useStore((s) => s.ov?.settings);
  return (
    <nav className="fkeys">
      {VIEWS.map(([v, k, l]) => (
        <button key={v} className={view === v ? "on" : ""} onClick={() => setState({ view: v })}><b>{k}</b>{l}</button>
      ))}
      <span className="fk-spacer" />
      {st && (
        <span className="tradetf" data-tip="How often Kairos is asked and trades are decided. The chart buttons only change the view.">
          <span className="tt-l">TRADE TF</span>
          <span className="seg">
            {Object.entries<any>(st.tf_profiles).map(([tf, p]) => (
              <button key={tf} className={tf === st.decision_tf ? "on" : ""} onClick={() => setState({ modal: { kind: "tf", data: tf } })}
                data-tip={`${p.label}: decide every ${p.label} close · hold ≤ ${p.horizon_minutes}m · stop from ${p.atr_tf}m ATR · ~${p.required_rph} Kairos req/h`}>{p.label}</button>
            ))}
          </span>
          <span className="tt-h">hold ≤ {st.horizon_minutes >= 60 ? `${st.horizon_minutes / 60}h` : `${st.horizon_minutes}m`} · stop {st.atr_tf}m ATR</span>
        </span>
      )}
      <span className="venue">VENUE <b>BYBIT TESTNET</b> · SIGNALS <b>BYBIT MAINNET</b> · MODEL <b>DMIND/KAIROS</b></span>
    </nav>
  );
}

export function Alerts() {
  const ov = useStore((s) => s.ov);
  const sock = useStore((s) => s.socket.connected);
  if (!ov) return null;
  const a: React.ReactNode[] = [];
  if (!ov.kairos.configured) a.push(<div key="k" className="alert"><b>KAIROS KEY NOT SET.</b> Market data is live; no decisions are made. <a href="#" onClick={(e) => { e.preventDefault(); setState({ modal: { kind: "keys" } }); }}>Add key ›</a></div>);
  if (ov.kairos.last_error) a.push(<div key="ke" className="alert crit"><b>KAIROS ERROR</b> {ov.kairos.last_error}</div>);
  if (ov.account?.halted_reason) a.push(<div key="h" className="alert crit"><b>TRADING HALTED</b> {ov.account.halted_reason}</div>);
  if (ov.executor_error) a.push(<div key="x" className="alert crit"><b>TESTNET</b> {ov.executor_error}</div>);
  if (!sock) a.push(<div key="s" className="alert"><b>LIVE STREAM RECONNECTING.</b> Prices and the account update again as soon as the socket is back.</div>);
  return <div>{a}</div>;
}

export function StatusLine() {
  const ov = useStore((s) => s.ov);
  const sock = useStore((s) => s.socket);
  const symbol = useStore((s) => s.symbol);
  const mon = useStore((s) => s.mon);
  const now = useNow();
  if (!ov) return <footer className="statusline" />;
  const d = ov.data, k = ov.kairos, a = ov.account, st = ov.settings;
  const row = mon?.rows.find((r: any) => r.symbol === symbol);
  const fund = row?.next_funding_ms ? Math.max(0, row.next_funding_ms - now) / 1000 : null;
  const two = (n: number) => String(n | 0).padStart(2, "0");
  const fundTxt = fund === null ? "—" : `${two(fund / 3600)}:${two((fund % 3600) / 60)}:${two(fund % 60)}`;
  const Dot = ({ c }: { c: string }) => <span className={`dot ${c}`} />;
  const stale = sock.lastMsg ? (now - sock.lastMsg) / 1000 : null;
  return (
    <footer className="statusline">
      <span><Dot c={d.ws_connected ? "ok" : "bad"} />BYBIT <b>{d.ws_connected ? "LIVE" : "DOWN"}</b> {Number(d.ws_messages).toLocaleString()} msgs</span>
      <span data-tip="Browser ⇄ terminal WebSocket. Latency = server push time to screen.">
        <Dot c={sock.connected && stale !== null && stale < 3 ? "ok" : sock.connected ? "warn" : "bad"} />UI SOCKET <b>{sock.connected ? "LIVE" : "DOWN"}</b>
        {sock.latencyMs !== null && <> · <b>{sock.latencyMs.toFixed(0)}</b> ms</>}
      </span>
      <span><Dot c={!k.configured ? "warn" : k.last_error ? "bad" : "ok"} />KAIROS <b>{k.configured ? (k.last_error ? "ERROR" : "READY") : "NO KEY"}</b> {k.requests_last_hour}/{k.budget_per_hour} req/h</span>
      <span><Dot c={ov.executor_error ? "bad" : a?.private_stream ? "ok" : "warn"} />TESTNET <b>{ov.executor_error ? "OFFLINE" : a?.private_stream ? "STREAMING" : "POLLING"}</b>{a?.last_sync ? ` · sync ${age(now / 1000 - a.last_sync)}` : ""}</span>
      {ov.run && <span data-tip={ov.run.name}>RUN <b className="amb">{ov.run.id}</b></span>}
      <span>MODE <b className={a?.trading_enabled ? "up" : ""}>{a?.trading_enabled ? "AUTO-TRADE" : "OBSERVE"}</b>{st.exploration_mode && <b className="amb"> · EXPLORE</b>}</span>
      <span>TRADE TF <b className="amb">{st.tf_profiles[st.decision_tf]?.label || st.decision_tf}</b> · HOLD ≤ <b>{st.horizon_minutes}m</b></span>
      <span>LABELLED <b>{ov.labelled_total}</b></span>
      <span>{short(symbol || "")} FUNDING IN <b>{fundTxt}</b></span>
      <span style={{ marginLeft: "auto" }} suppressHydrationWarning>UTC <b>{new Date(now).toISOString().slice(11, 19)}</b></span>
    </footer>
  );
}
