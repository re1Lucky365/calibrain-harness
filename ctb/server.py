"""Dashboard API + SSE + static UI. Binds to 127.0.0.1 by default; no secrets are ever returned."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from .broker.bybit_rest import BybitError
from .config import ROOT, TF_PROFILES, key_hint, load_settings, save_env
from .engine import Engine
from .evaluation import metrics as metrics_mod
from .evaluation import report as report_mod
from .market.store import TIMEFRAMES

log = logging.getLogger("ctb.server")
WEB = ROOT / "web"
NEXT_OUT = ROOT / "web-next" / "out"
PUSH_MS = 100
DEV_ORIGINS = {"localhost:3000", "127.0.0.1:3000"}  # `next dev`


def _allowed_origin(origin: str, host: str) -> bool:
    o = origin.split("://", 1)[-1]
    return o == host or o in DEV_ORIGINS


def j(data: Any, status: int = 200) -> web.Response:
    return web.Response(text=json.dumps(data, default=str), status=status, content_type="application/json")


@web.middleware
async def dev_cors(request: web.Request, handler):
    """Let `next dev` on :3000 call this API while developing the UI. Only those two origins."""
    origin = request.headers.get("Origin", "")
    dev = origin.split("://", 1)[-1] in DEV_ORIGINS
    if dev and request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        resp = await handler(request)
    if dev:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Vary"] = "Origin"
    return resp


@web.middleware
async def same_origin_only(request: web.Request, handler):
    """Block cross-site requests: any web page you visit could otherwise POST to this local
    server (toggle trading, swap keys). Require JSON (forces a CORS preflight we never grant)
    and, when the browser sends an Origin, that it is this server."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("Origin")
        if origin and not _allowed_origin(origin, request.host):
            return j({"error": "cross-origin request refused"}, 403)
        if not request.content_type.startswith("application/json"):
            return j({"error": "Content-Type must be application/json"}, 415)
    return await handler(request)


def make_app(engine: Engine) -> web.Application:
    s = engine.s
    app = web.Application(middlewares=[dev_cors, same_origin_only])
    routes = web.RouteTableDef()

    @routes.get("/")
    async def index(_: web.Request) -> web.FileResponse:
        if (NEXT_OUT / "index.html").exists():
            return web.FileResponse(NEXT_OUT / "index.html")
        return web.FileResponse(WEB / "index.html")

    @routes.get("/api/overview")
    async def overview(_: web.Request) -> web.Response:
        ex = engine.executor
        st = engine.store
        return j({
            "settings": s.public_view(),
            "uptime_s": time.time() - engine.started_at,
            "data": {
                "ws_connected": st.ws_connected, "ws_messages": st.ws_messages, "ws_last_msg_age_s": time.time() - st.ws_last_msg if st.ws_last_msg else None,
                "errors": list(st.data_errors)[-15:], "fear_greed": st.context.get("fear_greed"), "macro": st.context.get("macro"),
            },
            "kairos": {"configured": engine.kairos.configured, "requests_last_hour": engine.budget.used(),
                       "budget_per_hour": engine.budget.per_hour, "news_requests_last_hour": engine.news_budget.used(),
                       "last_error": engine.last_decision_error, "probe_rate": engine.probe_rate, "inflight": sorted(engine.inflight)},
            "account": ex.account_view() if ex else None,
            "executor_error": (ex.sync_error if ex else "no Bybit testnet keys configured"),
            "labelled_total": (engine.db.one("SELECT COUNT(*) n FROM decisions WHERE label_json IS NOT NULL") or {}).get("n", 0),
        })

    @routes.get("/api/market/{symbol}")
    async def market(req: web.Request) -> web.Response:
        sym = req.match_info["symbol"].upper()
        if sym not in engine.store.state:
            return j({"error": "unknown symbol"}, 404)
        tf = req.query.get("tf", s.decision_tf)
        if tf not in TIMEFRAMES:
            tf = s.decision_tf
        st = engine.store[sym]
        bars = [b.as_dict() for b in st.bars[tf][-300:]]
        preview = engine.latest_state.get(sym)
        return j({
            "symbol": sym, "tf": tf, "bars": bars, "ticker": st.ticker, "testnet_ticker": st.testnet_ticker,
            "book": {"bids": st.bids[:15], "asks": st.asks[:15], "age_s": time.time() - st.book_ts if st.book_ts else None},
            "flow": {"5m": st.flow_window(5), "15m": st.flow_window(15), "60m": st.flow_window(60)},
            "liquidations": {"15m": st.liq_window(15), "60m": st.liq_window(60), "recent": list(st.liquidations)[-20:]},
            "oi_history": st.oi_history[-96:], "ls_ratio": st.ls_ratio[-48:], "funding_history": st.funding_history[-21:],
            "instrument": st.instrument, "state_preview": preview,
            "news": [n for n in engine.store.context.get("news", []) if sym in n["symbols"] or n["market_wide"]][:20],
        })

    @routes.get("/api/decisions")
    async def decisions(req: web.Request) -> web.Response:
        sym = req.query.get("symbol")
        limit = min(int(req.query.get("limit", 100)), 1000)
        where = "probe IS NULL" + (" AND symbol=?" if sym else "")
        rows = engine.db.query(f"SELECT id, ts, symbol, action, decision_json, latency_ms, error_kind, error_msg, issues_json, parsed_json, "
                               f"price, sl_bp, tp_bp, label_json, probe FROM decisions WHERE {where} ORDER BY id DESC LIMIT ?",
                               ([sym] if sym else []) + [limit])
        return j([engine._decision_view(r) for r in rows])

    @routes.get("/api/decision/{id}")
    async def decision(req: web.Request) -> web.Response:
        r = engine.db.one("SELECT * FROM decisions WHERE id=?", (int(req.match_info["id"]),))
        if not r:
            return j({"error": "not found"}, 404)
        probes = engine.db.query("SELECT id, probe, parsed_json, error_kind FROM decisions WHERE probe_of=?", (r["id"],))
        r["probes"] = probes
        return j(r)

    @routes.get("/api/trades")
    async def trades(_: web.Request) -> web.Response:
        return j(engine.db.query("SELECT * FROM trades ORDER BY id DESC LIMIT 300"))

    @routes.get("/api/news")
    async def news(_: web.Request) -> web.Response:
        forecasts = engine.db.query("SELECT * FROM news ORDER BY ts DESC LIMIT 200")
        return j({"headlines": engine.store.context.get("news", [])[:60], "forecasts": forecasts})

    @routes.get("/api/events")
    async def events(_: web.Request) -> web.Response:
        return j(engine.db.query("SELECT * FROM events ORDER BY id DESC LIMIT 200"))

    @routes.get("/api/metrics")
    async def metrics(req: web.Request) -> web.Response:
        sym = req.query.get("symbol") or None
        hours = req.query.get("hours")
        tf = req.query.get("tf") or None
        since = time.time() - float(hours) * 3600 if hours else None
        m = await asyncio.to_thread(metrics_mod.compute, engine.db, symbol=sym, since_ts=since, decision_tf=tf)
        m["observations"] = report_mod.observations(m)
        return j(m)

    @routes.get("/api/report")
    async def report(_: web.Request) -> web.Response:
        m = await asyncio.to_thread(metrics_mod.compute, engine.db)
        md = report_mod.render(m, s.public_view())
        out = ROOT / "data" / "kairos_report.md"
        out.write_text(md, encoding="utf-8")
        return web.Response(text=md, content_type="text/markdown")

    # ---------------------------------------------------------------- controls
    @routes.post("/api/trading")
    async def trading(req: web.Request) -> web.Response:
        body = await req.json()
        enabled = bool(body.get("enabled"))
        if enabled and not engine.executor:
            return j({"error": "no Bybit testnet keys configured"}, 400)
        if enabled and engine.executor and engine.executor.halted_reason:
            if not body.get("clear_halt"):
                return j({"error": f"halted: {engine.executor.halted_reason}. Send clear_halt=true to resume."}, 409)
            engine.executor.halted_reason = None
        s.trading_enabled = enabled
        engine.emit("event", engine.db.event("warn", "trading_toggled", f"testnet trading {'ENABLED' if enabled else 'disabled'} from dashboard"))
        return j({"trading_enabled": s.trading_enabled})

    @routes.post("/api/close")
    async def close(req: web.Request) -> web.Response:
        body = await req.json()
        if not engine.executor:
            return j({"error": "no executor"}, 400)
        ok = await engine.executor.close(str(body.get("symbol", "")).upper(), "manual")
        return j({"closed": ok})

    @routes.post("/api/kill")
    async def kill(_: web.Request) -> web.Response:
        if engine.executor:
            await engine.executor.kill("dashboard kill switch")
        s.trading_enabled = False
        return j({"killed": True})

    @routes.post("/api/decide")
    async def decide_now(req: web.Request) -> web.Response:
        body = await req.json()
        sym = str(body.get("symbol", "")).upper()
        if sym not in engine.store.state:
            return j({"error": "unknown symbol"}, 404)
        if not engine.kairos.configured:
            return j({"error": "KAIROS_API_KEY is not set"}, 400)
        row = await engine.decide(sym, force_probe=bool(body.get("probe")))
        return j(engine._decision_view(row) if row else {"skipped": True})

    @routes.post("/api/decision-tf")
    async def decision_tf(req: web.Request) -> web.Response:
        """Switch the trading timeframe live: cadence, horizon and stop sizing change together."""
        body = await req.json()
        tf = str(body.get("tf", ""))
        if tf not in TF_PROFILES:
            return j({"error": f"timeframe must be one of {sorted(TF_PROFILES, key=int)} (minutes)"}, 400)
        old = s.decision_tf
        updates = s.apply_tf_profile(tf)
        need = s.required_rph(tf, engine.probe_rate)
        if body.get("raise_budget") and need > s.kairos_max_requests_per_hour:
            s.kairos_max_requests_per_hour = need
            engine.budget.per_hour = need
            updates["CTB_KAIROS_MAX_RPH"] = str(need)
        save_env(updates)
        p = TF_PROFILES[tf]
        msg = (f"trading timeframe {TF_PROFILES[old]['label'] if old in TF_PROFILES else old} -> {p['label']}: "
               f"horizon {p['horizon_bars'] * int(tf)} min, stop from {p['atr_tf']}m ATR, needs ~{need} req/h "
               f"(budget {s.kairos_max_requests_per_hour}). Open positions keep their original time-stop.")
        engine.emit("event", engine.db.event("warn", "decision_tf", msg))
        engine.emit("settings", {"decision_tf": tf})
        return j(s.public_view())

    @routes.post("/api/settings")
    async def settings(req: web.Request) -> web.Response:
        body = await req.json()
        allowed = {"min_edge_r": float, "min_setup_quality": float, "risk_per_trade_pct": float, "allow_short": bool, "max_positions": int,
                   "exploration_mode": bool, "explore_min_lean_r": float, "explore_risk_pct": float}
        if "max_positions" in body:
            try:
                n = int(body["max_positions"])
            except (TypeError, ValueError):
                return j({"error": "max_positions must be a whole number"}, 400)
            if not 1 <= n <= 50:
                return j({"error": "max_positions must be between 1 and 50"}, 400)
            body["max_positions"] = n
        for k, typ in allowed.items():
            if k in body:
                setattr(s, k, typ(body[k]))
        if "max_positions" in body:
            save_env({"CTB_MAX_POSITIONS": str(s.max_positions)})  # survives restarts; open positions untouched
        if "exploration_mode" in body:
            save_env({"CTB_EXPLORATION": "1" if s.exploration_mode else "0"})  # survives restarts
        if "probe_rate" in body:
            engine.probe_rate = max(0.0, min(float(body["probe_rate"]), 0.5))
        engine.emit("event", engine.db.event("info", "settings", f"settings updated: { {k: body[k] for k in body if k in allowed or k == 'probe_rate'} }"))
        return j(s.public_view())

    # ---------------------------------------------------------------- terminal panels
    @routes.get("/api/monitor")
    async def monitor(_: web.Request) -> web.Response:
        """Everything the always-on panels need, in one poll."""
        rows, tape = [], []
        latest = {r["symbol"]: r for r in engine.db.query(
            "SELECT d.symbol, d.ts, d.action, d.error_kind, d.decision_json FROM decisions d "
            "JOIN (SELECT symbol, MAX(id) mid FROM decisions WHERE probe IS NULL GROUP BY symbol) m ON d.id = m.mid")}
        for sym in s.symbols:
            st = engine.store[sym]
            t = st.ticker
            c15 = st.closes("15")[-96:]  # 24h of 15m closes
            last = t.get("lastPrice")
            tn = st.testnet_ticker.get("lastPrice")
            d = latest.get(sym)
            rows.append({
                "symbol": sym, "last": last, "mark": t.get("markPrice"), "index": t.get("indexPrice"),
                "bid": t.get("bid1Price"), "ask": t.get("ask1Price"),
                "pct24h": 100 * float(t["price24hPcnt"]) if t.get("price24hPcnt") else None,
                "high24h": t.get("highPrice24h"), "low24h": t.get("lowPrice24h"),
                "turnover24h": float(t["turnover24h"]) if t.get("turnover24h") else None,
                "oi_value": float(t["openInterestValue"]) if t.get("openInterestValue") else None,
                "funding": float(t["fundingRate"]) if t.get("fundingRate") else None,
                "next_funding_ms": int(t["nextFundingTime"]) if t.get("nextFundingTime") else None,
                "testnet_last": tn,
                "divergence_bp": round(10000 * (float(tn) / float(last) - 1), 1) if tn and last else None,
                "spark": c15, "age_s": round(time.time() - st.last_update, 1) if st.last_update else None,
                "decision": {"ts": d["ts"], "action": d["action"], "error": d["error_kind"],
                             "ev_long": (d["decision_json"] or {}).get("ev_long_r"), "ev_short": (d["decision_json"] or {}).get("ev_short_r")} if d else None,
            })
            for ts, side, notional in list(st.liquidations)[-40:]:
                tape.append({"ts": ts / 1000, "symbol": sym, "kind": "LIQ", "side": "LONG" if side == "Buy" else "SHORT", "notional": notional})
            for ts, side, qty, px, notional in list(st.large_trades)[-40:]:
                tape.append({"ts": ts / 1000, "symbol": sym, "kind": "BLOCK", "side": "BUY" if side == "Buy" else "SELL", "notional": notional, "price": px, "qty": qty})
        tape.sort(key=lambda x: x["ts"], reverse=True)
        ctx = engine.store.context
        return j({"rows": rows, "tape": tape[:80], "universe": ctx.get("universe", []), "universe_ts": ctx.get("universe_ts"),
                  "news": ctx.get("news", [])[:40], "macro": ctx.get("macro", {}), "fear_greed": ctx.get("fear_greed"), "server_ts": time.time()})

    @routes.get("/api/perf")
    async def perf(req: web.Request) -> web.Response:
        """24h relative performance: % change of each symbol's 5m closes from the first common bar."""
        out = {}
        for sym in s.symbols:
            bars = engine.store[sym].closed_bars("5")[-288:]
            if bars:
                out[sym] = [(b.t // 1000, b.c) for b in bars]
        if not out:
            return j({})
        start = max(v[0][0] for v in out.values())
        res = {}
        for sym, pts in out.items():
            pts = [p for p in pts if p[0] >= start]
            base = pts[0][1]
            res[sym] = [[t, round(100 * (c / base - 1), 4)] for t, c in pts]
        return j(res)

    # ---------------------------------------------------------------- keys
    @routes.get("/api/keys")
    async def keys_status(_: web.Request) -> web.Response:
        return j({
            "kairos": {**key_hint(s.kairos_api_key), "base_url": s.kairos_base_url},
            "bybit": {"api_key": key_hint(s.bybit_api_key), "api_secret": key_hint(s.bybit_api_secret),
                      "connected": bool(engine.executor and not engine.executor.sync_error),
                      "error": engine.executor.sync_error if engine.executor else None},
        })

    @routes.post("/api/keys/bybit")
    async def keys_bybit(req: web.Request) -> web.Response:
        body = await req.json()
        key, secret = str(body.get("api_key", "")).strip(), str(body.get("api_secret", "")).strip()
        if not key or not secret:
            return j({"error": "both API key and secret are required"}, 400)
        try:
            return j(await engine.set_bybit_keys(key, secret))
        except BybitError as exc:
            hint = " (10003 = key not valid on TESTNET; create it at testnet.bybit.com)" if exc.ret_code in (10003, 10004, 33004) else ""
            return j({"error": f"Bybit rejected the keys: {exc.ret_code} {exc.ret_msg}{hint}"}, 400)
        except ValueError as exc:
            return j({"error": str(exc)}, 409)

    @routes.post("/api/keys/kairos")
    async def keys_kairos(req: web.Request) -> web.Response:
        body = await req.json()
        key = str(body.get("api_key", "")).strip()
        if not key:
            return j({"error": "API key is required"}, 400)
        try:
            return j(await engine.set_kairos_key(key, body.get("base_url") or None))
        except ValueError as exc:
            return j({"error": str(exc)}, 400)

    @routes.post("/api/keys/kairos/test")
    async def keys_kairos_test(_: web.Request) -> web.Response:
        return j(await engine.test_kairos())

    @routes.get("/stream")
    async def stream(req: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        await resp.prepare(req)
        q: asyncio.Queue = asyncio.Queue()
        engine.subscribers.add(q)
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    await resp.write(f"data: {msg}\n\n".encode())
                except asyncio.TimeoutError:
                    await resp.write(b": ping\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            engine.subscribers.discard(q)
        return resp

    @routes.get("/ws")
    async def ws_endpoint(req: web.Request) -> web.WebSocketResponse:
        """One socket per tab. Pushes engine events as they happen, plus market ticks, the order
        book and the forming candle for the symbol the client is watching, every PUSH_MS (only
        what changed). Replaces polling, so the UI updates within ~100 ms of Bybit."""
        origin = req.headers.get("Origin")
        if origin and not _allowed_origin(origin, req.host):
            raise web.HTTPForbidden(text="cross-origin websocket refused")
        ws = web.WebSocketResponse(heartbeat=20, compress=True)
        await ws.prepare(req)
        q: asyncio.Queue = asyncio.Queue()
        engine.subscribers.add(q)
        watch = {"symbol": s.symbols[0], "tf": "5"}

        async def reader() -> None:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        m = json.loads(msg.data)
                    except ValueError:
                        continue
                    if m.get("op") == "watch":
                        if m.get("symbol") in s.symbols:
                            watch["symbol"] = m["symbol"]
                        if m.get("tf") in TIMEFRAMES:
                            watch["tf"] = m["tf"]

        async def forward_events() -> None:
            while True:
                await ws.send_str(await q.get())

        async def market() -> None:
            last_sent: dict[str, Any] = {}
            while True:
                await asyncio.sleep(PUSH_MS / 1000)
                ticks = {}
                for sym in s.symbols:
                    st = engine.store[sym]
                    t, tn = st.ticker, st.testnet_ticker
                    row = (t.get("lastPrice"), t.get("markPrice"), t.get("bid1Price"), t.get("ask1Price"), tn.get("lastPrice"))
                    if last_sent.get(sym) != row:
                        last_sent[sym] = row
                        ticks[sym] = {"last": row[0], "mark": row[1], "bid": row[2], "ask": row[3], "testnet_last": row[4],
                                      "pct24h": 100 * float(t["price24hPcnt"]) if t.get("price24hPcnt") else None}
                st = engine.store[watch["symbol"]]
                book = (st.bids[:15], st.asks[:15])
                bars = st.bars.get(watch["tf"]) or []
                bar = bars[-1].as_dict() if bars else None
                out: dict[str, Any] = {}
                if ticks:
                    out["ticks"] = ticks
                if last_sent.get("_book") != (watch["symbol"], book):
                    last_sent["_book"] = (watch["symbol"], book)
                    out["book"] = {"symbol": watch["symbol"], "bids": book[0], "asks": book[1]}
                if bar and last_sent.get("_bar") != (watch["symbol"], watch["tf"], bar):
                    last_sent["_bar"] = (watch["symbol"], watch["tf"], bar)
                    out["bar"] = {"symbol": watch["symbol"], "tf": watch["tf"], **bar}
                if out:
                    out["ts"] = time.time()
                    await ws.send_str(json.dumps({"kind": "market", "data": out}, default=str))

        tasks = [asyncio.create_task(forward_events()), asyncio.create_task(market())]
        try:
            await reader()
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            for t in tasks:
                t.cancel()
            engine.subscribers.discard(q)
        return ws

    app.add_routes(routes)
    if NEXT_OUT.exists():
        # Next.js static export (web-next/out): / and /_next/* served from here, same origin as the API
        async def legacy(_: web.Request) -> web.FileResponse:
            return web.FileResponse(WEB / "index.html")
        app.router.add_get("/legacy", legacy)
        app.router.add_static("/_next", NEXT_OUT / "_next")

        async def favicon(_: web.Request) -> web.FileResponse:
            return web.FileResponse(NEXT_OUT / "favicon.ico")
        app.router.add_get("/favicon.ico", favicon)
    app.router.add_static("/static", WEB)

    async def on_startup(_: web.Application) -> None:
        await engine.start()

    async def on_cleanup(_: web.Application) -> None:
        await engine.stop()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = load_settings()
    engine = Engine(s)
    print(f"classifier-trading-bot dashboard: http://{s.host}:{s.port}  (venue: Bybit TESTNET, "
          f"kairos key {'set' if s.kairos_api_key else 'NOT set'}, trading {'ON' if s.trading_enabled else 'observe-only'})")
    web.run_app(make_app(engine), host=s.host, port=s.port, print=None)


if __name__ == "__main__":
    main()
