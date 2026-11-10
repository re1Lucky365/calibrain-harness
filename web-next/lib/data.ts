"use client";
// Loaders (REST, for slow-changing data) and the live socket (/ws, for everything that moves).
import { api, WS_URL } from "./api";
import { getState, pushBar, setState, type Tick } from "./store";

export async function loadOverview() {
  const ov = await api("/api/overview");
  setState((s) => ({
    ov,
    symbol: s.symbol ?? ov.settings.symbols[0],
    tf: s.ov ? s.tf : String(ov.settings.decision_tf_minutes),
  }));
}
export async function loadMonitor() {
  setState({ mon: await api("/api/monitor") });
}
export async function loadMarket() {
  const { symbol, tf } = getState();
  if (!symbol) return;
  const mkt = await api(`/api/market/${symbol}?tf=${tf}`);
  // drop a stale response if the user switched symbol/timeframe meanwhile
  if (getState().symbol === mkt.symbol && getState().tf === mkt.tf) setState({ mkt });
}
export async function loadDecisions() {
  const decisions = await api<any[]>("/api/decisions?limit=400");
  const latest: Record<string, any> = {};
  for (const d of decisions) if (!latest[d.symbol] && !d.probe) latest[d.symbol] = d;
  setState({ decisions, latest });
}
export async function loadPerf() {
  setState({ perf: await api("/api/perf") });
}
export async function loadNews() {
  setState({ news: await api("/api/news") });
}

export function selectSymbol(symbol: string) {
  setState({ symbol, mkt: null });
  sendWatch();
  loadMarket().catch(() => {});
}
export function selectTf(tf: string) {
  setState({ tf });
  sendWatch();
  loadMarket().catch(() => {});
}

let ws: WebSocket | null = null;
function sendWatch() {
  const { symbol, tf } = getState();
  if (ws?.readyState === WebSocket.OPEN && symbol) ws.send(JSON.stringify({ op: "watch", symbol, tf }));
}

const debounced = new Map<string, ReturnType<typeof setTimeout>>();
function soon(key: string, fn: () => Promise<unknown>, ms = 150) {
  clearTimeout(debounced.get(key));
  debounced.set(key, setTimeout(() => fn().catch(() => {}), ms));
}

export function connectSocket() {
  let retry = 500;
  const open = () => {
    ws = new WebSocket(WS_URL());
    ws.onopen = () => {
      retry = 500;
      setState((s) => ({ socket: { ...s.socket, connected: true } }));
      sendWatch();
    };
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      const now = Date.now();
      if (msg.kind === "market") {
        const d = msg.data;
        const latencyMs = d.ts ? Math.max(0, now - d.ts * 1000) : null;
        setState((s) => {
          const patch: any = { socket: { connected: true, lastMsg: now, latencyMs, msgs: s.socket.msgs + 1 } };
          if (d.ticks) {
            const ticks = { ...s.ticks };
            for (const [sym, t] of Object.entries<Tick>(d.ticks)) ticks[sym] = { ...ticks[sym], ...t, ts: now };
            patch.ticks = ticks;
          }
          if (d.book && d.book.symbol === s.symbol) patch.book = { ...d.book, ts: now };
          return patch;
        });
        if (d.bar) pushBar(d.bar);
        return;
      }
      setState((s) => ({ socket: { ...s.socket, lastMsg: now, msgs: s.socket.msgs + 1 } }));
      const kind: string = msg.kind;
      if (kind === "account") setState((s) => (s.ov ? { ov: { ...s.ov, account: msg.data } } : {}));
      else if (kind === "decision" || kind === "probe" || kind === "labels") soon("dec", loadDecisions);
      else if (kind === "trade") {
        soon("ov", loadOverview);
        setState((s) => ({ rev: { ...s.rev, trades: s.rev.trades + 1 } }));
      } else if (kind === "news") soon("news", loadNews, 500);
      else if (kind === "settings") soon("ov", loadOverview);
      else if (kind === "event") setState((s) => ({ rev: { ...s.rev, events: s.rev.events + 1 } }));
    };
    ws.onclose = () => {
      setState((s) => ({ socket: { ...s.socket, connected: false } }));
      setTimeout(open, retry);
      retry = Math.min(retry * 2, 8000);
    };
    ws.onerror = () => ws?.close();
  };
  open();
  return () => {
    if (ws) {
      ws.onclose = null;
      ws.close();
    }
  };
}
