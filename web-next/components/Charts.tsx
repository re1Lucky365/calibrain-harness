"use client";
import { useEffect, useRef, useState } from "react";
import {
  createChart, type IChartApi, type ISeriesApi, type SeriesMarker, type Time, type UTCTimestamp,
} from "lightweight-charts";
import { setBarSink, useStore, type Bar } from "@/lib/store";
import { cssVar, decimalsOf, fx, SERIES_C, sg, short, ud, usd } from "@/lib/format";

type Candle = { time: number; open: number; high: number; low: number; close: number; volume: number };

function ema(vals: number[], p: number) {
  const k = 2 / (p + 1);
  const out: (number | null)[] = [];
  let e: number | null = null;
  vals.forEach((v, i) => {
    e = e === null ? v : v * k + e * (1 - k);
    out.push(i >= p - 1 ? e : null);
  });
  return out;
}

const baseOpts = () => ({
  autoSize: true,
  layout: { background: { color: "#000" }, textColor: cssVar("--muted"), fontFamily: cssVar("--mono"), fontSize: 11 },
  grid: { vertLines: { color: "#111" }, horzLines: { color: "#111" } },
  timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#242424", rightOffset: 4 },
  rightPriceScale: { borderColor: "#242424" },
  crosshair: { mode: 0, vertLine: { color: "#5a5a5a", labelBackgroundColor: "#2b1a05" }, horzLine: { color: "#5a5a5a", labelBackgroundColor: "#2b1a05" } },
  localization: { timeFormatter: (t: number) => new Date(t * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) },
});

export function PriceChart() {
  const host = useRef<HTMLDivElement>(null);
  const api = useRef<{ chart: IChartApi; candles: ISeriesApi<"Candlestick">; e20: ISeriesApi<"Line">; e50: ISeriesApi<"Line">; vol: ISeriesApi<"Histogram"> } | null>(null);
  const bars = useRef<Candle[]>([]);
  const byTime = useRef(new Map<number, Candle>());
  const key = useRef("");
  const [ohlc, setOhlc] = useState<Candle | null>(null);
  const [pd, setPd] = useState(2);
  const mkt = useStore((s) => s.mkt);
  const decisions = useStore((s) => s.decisions);

  useEffect(() => {
    const chart = createChart(host.current!, baseOpts() as any);
    const up = cssVar("--up"), dn = cssVar("--down");
    const candles = chart.addCandlestickSeries({ upColor: up, downColor: dn, borderVisible: false, wickUpColor: up, wickDownColor: dn, priceLineColor: cssVar("--amber") });
    const lineOpts = { lineWidth: 1 as const, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };
    const e20 = chart.addLineSeries({ color: cssVar("--amber"), ...lineOpts });
    const e50 = chart.addLineSeries({ color: cssVar("--s1"), ...lineOpts });
    const vol = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
    chart.subscribeCrosshairMove((p) => {
      const b = p?.time ? byTime.current.get(p.time as number) : null;
      setOhlc(b || bars.current[bars.current.length - 1] || null);
    });
    api.current = { chart, candles, e20, e50, vol };
    return () => {
      setBarSink(null);
      chart.remove();
      api.current = null;
    };
  }, []);

  const drawEmas = () => {
    const a = api.current!, b = bars.current, closes = b.map((x) => x.close);
    const f = (vals: (number | null)[]) => b.map((x, i) => (vals[i] === null ? { time: x.time as UTCTimestamp } : { time: x.time as UTCTimestamp, value: vals[i] as number }));
    a.e20.setData(f(ema(closes, 20)) as any);
    a.e50.setData(f(ema(closes, 50)) as any);
  };
  const volPoint = (x: Candle) => ({ time: x.time as UTCTimestamp, value: x.volume, color: x.close >= x.open ? "rgba(58,208,138,.35)" : "rgba(255,92,92,.35)" });

  // full reload when the symbol/timeframe (or its REST snapshot) changes
  useEffect(() => {
    const a = api.current;
    if (!a || !mkt?.bars?.length) return;
    const k = `${mkt.symbol}:${mkt.tf}`;
    const reset = k !== key.current;
    key.current = k;
    const d = Math.max(decimalsOf(mkt.ticker.lastPrice), 0);
    setPd(d);
    const pf = { type: "price" as const, precision: d, minMove: Math.pow(10, -d) };
    a.candles.applyOptions({ priceFormat: pf });
    a.e20.applyOptions({ priceFormat: pf });
    a.e50.applyOptions({ priceFormat: pf });
    // keep any newer live bar the socket already delivered
    const live = !reset ? bars.current[bars.current.length - 1] : undefined;
    let b: Candle[] = mkt.bars;
    if (live && b.length && live.time >= b[b.length - 1].time) b = [...b.filter((x) => x.time < live.time), live];
    bars.current = b;
    byTime.current = new Map(b.map((x) => [x.time, x]));
    a.candles.setData(b.map((x) => ({ time: x.time as UTCTimestamp, open: x.open, high: x.high, low: x.low, close: x.close })));
    a.vol.setData(b.map(volPoint));
    drawEmas();
    if (reset) a.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, b.length - 140), to: b.length + 4 });
    setOhlc(b[b.length - 1]);

    setBarSink((bar: Bar) => {
      if (bar.symbol !== mkt.symbol || bar.tf !== mkt.tf || !api.current) return;
      const arr = bars.current, last = arr[arr.length - 1];
      if (last && bar.time < last.time) return;
      const c: Candle = { time: bar.time, open: bar.open, high: bar.high, low: bar.low, close: bar.close, volume: bar.volume };
      const isNew = !last || bar.time > last.time;
      if (isNew) arr.push(c);
      else arr[arr.length - 1] = c;
      byTime.current.set(c.time, c);
      api.current.candles.update({ time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close });
      api.current.vol.update(volPoint(c));
      if (isNew) drawEmas();
      setOhlc((o) => (!o || o.time === c.time || o.time === last?.time ? c : o));
    });
  }, [mkt]);

  // Kairos decision markers: a decision made just after a bar close describes that bar
  useEffect(() => {
    const a = api.current;
    if (!a || !mkt?.bars?.length) return;
    const tfs = Number(mkt.tf) * 60, first = mkt.bars[0].time;
    const up = cssVar("--up"), dn = cssVar("--down");
    const marks: SeriesMarker<Time>[] = decisions
      .filter((d) => d.symbol === mkt.symbol && !d.error_kind && !d.probe && d.ts >= first)
      .map((d) => ({
        time: (Math.floor((d.ts - 10) / tfs) * tfs) as UTCTimestamp,
        position: d.action === "short" ? "aboveBar" : "belowBar",
        color: d.action === "long" ? up : d.action === "short" ? dn : "#6a6a6a",
        shape: d.action === "long" ? "arrowUp" : d.action === "short" ? "arrowDown" : "circle",
        size: d.action === "flat" ? 0.35 : 1.1,
        text: d.action === "flat" ? "" : d.action.toUpperCase(),
      }) as SeriesMarker<Time>)
      .filter((m) => byTime.current.has(m.time as number))
      .sort((x, y) => (x.time as number) - (y.time as number));
    const dedup: SeriesMarker<Time>[] = [];
    for (const m of marks) {
      if (dedup.length && dedup[dedup.length - 1].time === m.time) dedup[dedup.length - 1] = m;
      else dedup.push(m);
    }
    a.candles.setMarkers(dedup);
  }, [decisions, mkt]);

  const chg = ohlc ? ohlc.close - ohlc.open : 0;
  return (
    <div className="pb chart-wrap">
      <div ref={host} className="chart-host" />
      {ohlc && (
        <div className="ohlc">
          <b>O</b> {fx(ohlc.open, pd)} <b>H</b> {fx(ohlc.high, pd)} <b>L</b> {fx(ohlc.low, pd)} <b>C</b> <span className={ud(chg)}>{fx(ohlc.close, pd)}</span> <b>V</b> {usd(ohlc.volume)}{" "}
          <span className="muted">{new Date(ohlc.time * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</span>
        </div>
      )}
    </div>
  );
}

export function PerfChart() {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<Record<string, ISeriesApi<"Line">>>({});
  const perf = useStore((s) => s.perf);
  const order = usePerfOrder();

  useEffect(() => {
    chart.current = createChart(host.current!, { ...(baseOpts() as any), handleScroll: false, handleScale: false });
    return () => {
      chart.current?.remove();
      chart.current = null;
      series.current = {};
    };
  }, []);
  useEffect(() => {
    if (!chart.current) return;
    for (const [sym, ser] of Object.entries(series.current)) {
      if (!order.includes(sym)) { chart.current.removeSeries(ser); delete series.current[sym]; }
    }
    order.forEach((sym, i) => {
      if (!perf[sym]) return;
      if (!series.current[sym])
        series.current[sym] = chart.current!.addLineSeries({
          color: cssVar(SERIES_C[i % 5]), lineWidth: 2, title: short(sym), priceLineVisible: false, crosshairMarkerRadius: 3,
          priceFormat: { type: "custom", minMove: 0.01, formatter: (v: number) => sg(v, 2, "%") },
        });
      series.current[sym].setData(perf[sym].map(([t, v]) => ({ time: t as UTCTimestamp, value: v })));
    });
    chart.current.timeScale().fitContent();
  }, [perf, order]);
  return <div className="pb chart-wrap"><div ref={host} className="chart-host" /></div>;
}

/** At most 5 series (the validated palette has 5 hues; colours are never cycled):
 * the loaded symbol plus the first others in the universe. */
function usePerfOrder(): string[] {
  const all: string[] = useStore((s) => s.ov?.settings.symbols) || [];
  const sel = useStore((s) => s.symbol);
  const key = `${sel}|${all.join(",")}`;
  const ref = useRef<{ key: string; v: string[] }>({ key: "", v: [] });
  if (ref.current.key !== key) {
    const rest = all.filter((x) => x !== sel);
    ref.current = { key, v: (sel && all.includes(sel) ? [sel, ...rest] : rest).slice(0, 5).sort((a, b) => all.indexOf(a) - all.indexOf(b)) };
  }
  return ref.current.v;
}

export function PerfLegend() {
  const perf = useStore((s) => s.perf);
  const order = usePerfOrder();
  return (
    <span className="ph-r">
      {order.map((s, i) => {
        const v = perf[s]?.[perf[s].length - 1]?.[1];
        return (
          <span key={s}>
            <span style={{ color: `var(${SERIES_C[i % 5]})` }}>■</span><span className="w">{short(s)}</span>&nbsp;<span className={ud(v)}>{sg(v, 2, "%")}</span>&nbsp;&nbsp;
          </span>
        );
      })}
    </span>
  );
}
