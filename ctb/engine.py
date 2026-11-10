"""Orchestration: market data -> state -> Kairos -> validation -> policy -> execution -> records."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any

import aiohttp

from .broker.bybit_private_ws import BybitPrivateStream
from .broker.bybit_rest import BybitTestnet
from .broker.executor import Executor
from .config import Settings, save_env
from .db import DB
from .evaluation.labeler import Labeler
from .features import build_state, mirror_state
from .kairos import news as news_profile
from .kairos.client import KairosClient, KairosError, RateBudget
from .kairos.parse import validate
from .kairos.questions import QUESTION_SET_VERSION, build_questions
from .market.bybit_public import BybitPublicFeed
from .market.context import ContextFeed
from .market.store import MarketStore
from .policy import decide

log = logging.getLogger("ctb.engine")


class Engine:
    def __init__(self, s: Settings):
        self.s = s
        self.db = DB(s.db_path)
        self.store = MarketStore(s.symbols)
        self.subscribers: set[asyncio.Queue] = set()
        self.session: aiohttp.ClientSession | None = None
        self.executor: Executor | None = None
        self.budget = RateBudget(s.kairos_max_requests_per_hour)
        self.news_budget = RateBudget(s.kairos_news_max_per_hour)
        self.inflight: set[str] = set()
        self.latest_state: dict[str, dict[str, Any]] = {}
        self.probe_rate = 0.05
        self.started_at = time.time()
        self.tasks: list[asyncio.Task] = []
        self.last_decision_error: str | None = None

    # ------------------------------------------------------------------ pub/sub
    def emit(self, kind: str, data: dict[str, Any]) -> None:
        msg = json.dumps({"kind": kind, "data": data}, default=str)
        for q in list(self.subscribers):
            if q.qsize() < 500:
                q.put_nowait(msg)

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self.session = aiohttp.ClientSession()
        self.feed = BybitPublicFeed(self.store, self.session)
        self.context = ContextFeed(self.store, self.session)
        self.kairos = KairosClient(self.s.kairos_base_url, lambda: self.s.kairos_api_key, self.session,
                                   model=self.s.kairos_model, timeout_s=self.s.kairos_timeout_s)
        self.labeler = Labeler(self.db, self.session, self.emit)
        self.feed.on_bar_close.append(self.on_bar_close)
        self.context.on_news.append(self.on_news)
        # the hourly budget must survive restarts, so seed it from what was actually sent
        hour_ago = time.time() - 3600
        for r in self.db.query("SELECT ts FROM decisions WHERE ts > ? ORDER BY ts", (hour_ago,)):
            self.budget.calls.append(r["ts"])
        for r in self.db.query("SELECT ts FROM news WHERE ts > ? ORDER BY ts", (hour_ago,)):
            self.news_budget.calls.append(r["ts"])
        await self.feed.warmup()
        if self.s.bybit_api_key and self.s.bybit_api_secret:
            await self._start_executor()
        self.tasks = [
            asyncio.create_task(self.feed.ws_loop(), name="ws"),
            asyncio.create_task(self.feed.poll_loop(), name="poll"),
            asyncio.create_task(self.context.loop(), name="context"),
            asyncio.create_task(self.labeler.loop(), name="labeler"),
            asyncio.create_task(self._preview_loop(), name="preview"),
        ]
        self.db.event("info", "started", f"engine started; kairos={'on' if self.kairos.configured else 'NOT CONFIGURED'}; "
                                         f"trading={'on' if self.s.trading_enabled else 'observe-only'}")

    async def _start_executor(self) -> None:
        self.executor = Executor(self.s, self.db, BybitTestnet(self.s.bybit_api_key, self.s.bybit_api_secret, self.session), self.emit)
        try:
            await self.executor.start()
            for sym, inst in self.executor.instruments.items():
                self.store[sym].instrument = inst
        except Exception as exc:  # noqa: BLE001
            self.db.event("error", "executor_start", str(exc))
            self.executor.sync_error = str(exc)
        self._reconcile_task = asyncio.create_task(self.executor.reconcile_loop(), name="reconcile")
        old = getattr(self, "_private_stream", None)
        if old:
            old.stop()
            self._private_task.cancel()
        self._private_stream = BybitPrivateStream(self.s.bybit_api_key, self.s.bybit_api_secret, self.session, self.executor.on_private)
        self.executor.stream = self._private_stream
        self._private_task = asyncio.create_task(self._private_stream.run(), name="private_ws")

    # ------------------------------------------------------------------ keys (set from the dashboard)
    async def set_bybit_keys(self, api_key: str, api_secret: str) -> dict[str, Any]:
        """Validate testnet keys against the account, then persist and hot-swap the executor."""
        if self.db.one("SELECT id FROM trades WHERE status IN ('open','opening') LIMIT 1"):
            raise ValueError("the bot has open trades on the current account; close them (or use the kill switch) before switching keys")
        probe = BybitTestnet(api_key, api_secret, self.session)
        await probe.sync_time()
        wallet = await probe.wallet()  # raises BybitError on bad key / wrong environment (10003)
        await probe.positions()  # needs Contract read permission
        old_task = getattr(self, "_reconcile_task", None)
        if old_task:
            old_task.cancel()
        save_env({"BYBIT_API_KEY": api_key, "BYBIT_API_SECRET": api_secret})
        self.s.bybit_api_key, self.s.bybit_api_secret = api_key, api_secret
        self.s.trading_enabled = False  # new account starts observe-only
        await self._start_executor()
        self.emit("event", self.db.event("warn", "keys", "Bybit testnet keys updated from dashboard; trading set to observe-only"))
        return {"ok": True, "equity": wallet.get("equity")}

    async def set_kairos_key(self, api_key: str, base_url: str | None = None) -> dict[str, Any]:
        updates = {"KAIROS_API_KEY": api_key}
        if base_url:
            base_url = base_url.strip().rstrip("/")
            if not base_url.startswith("https://"):
                raise ValueError("KAIROS_BASE_URL must be an https:// origin without /v1")
            updates["KAIROS_BASE_URL"] = base_url
        save_env(updates)
        if base_url:
            self.s.kairos_base_url = base_url
            self.kairos.url = base_url + "/v1/systemone"
        self.last_decision_error = None
        self.emit("event", self.db.event("info", "keys", "Kairos key updated from dashboard"))
        return await self.test_kairos()

    async def test_kairos(self) -> dict[str, Any]:
        """One tiny live request (1 noul question, counts against the hourly budget)."""
        if not self.kairos.configured:
            return {"ok": False, "error": "KAIROS_API_KEY is not set"}
        if not self.budget.try_take():
            return {"ok": False, "error": "hourly Kairos budget exhausted"}
        try:
            res = await self.kairos.ask({"message": "Connectivity check from classifier-trading-bot."},
                                        {"is_check": {"type": "noul", "instructions": "Does the message say it is a connectivity check?"}})
        except KairosError as exc:
            return {"ok": False, "error": f"{exc.kind}: {exc.message}", "request_id": exc.request_id}
        ans = res.answers.get("is_check", {})
        return {"ok": True, "latency_ms": round(res.latency_ms), "answer_noul": ans.get("noul"), "model": res.metadata.get("model")}

    async def stop(self) -> None:
        self.feed.stop()
        self.context.stop()
        if getattr(self, "_reconcile_task", None):
            self._reconcile_task.cancel()
        if getattr(self, "_private_stream", None):
            self._private_stream.stop()
            self._private_task.cancel()
        for t in self.tasks:
            t.cancel()
        if self.session:
            await self.session.close()

    async def _preview_loop(self) -> None:
        """Keep the latest would-be Kairos state per symbol fresh for the dashboard."""
        while True:
            for sym in self.s.symbols:
                try:
                    pos = self.executor.position_summary(sym) if self.executor else None
                    built = build_state(self.store, sym, self.s, position=pos)
                    if built:
                        self.latest_state[sym] = {"state": built[0], "meta": built[1], "ts": time.time()}
                except Exception as exc:  # noqa: BLE001
                    self.store.record_error(f"preview:{sym}", repr(exc))
            await asyncio.sleep(5)

    # ------------------------------------------------------------------ decisions
    async def on_bar_close(self, symbol: str, tf: str) -> None:
        if tf == "1":
            self.emit("bar", {"symbol": symbol})
        if tf != self.s.decision_tf:
            return
        await asyncio.sleep(self.s.bar_settle_s)  # let the last trades of the closed bar arrive
        await self.decide(symbol)

    async def decide(self, symbol: str, *, force_probe: bool = False) -> dict[str, Any] | None:
        if symbol in self.inflight:
            return None
        if not self.kairos.configured:
            return None
        if not self.budget.try_take():
            self.emit("event", self.db.event("warn", "budget", f"Kairos hourly budget ({self.s.kairos_max_requests_per_hour}) exhausted; skipped {symbol}", symbol))
            return None
        self.inflight.add(symbol)
        try:
            pos = self.executor.position_summary(symbol) if self.executor else None
            built = build_state(self.store, symbol, self.s, position=pos)
            if not built:
                self.db.event("warn", "state", "not enough data to build state", symbol)
                return None
            state, meta = built
            if meta.get("testnet_price"):
                meta["venue_divergence_bp"] = round(10000 * (meta["testnet_price"] / meta["price"] - 1), 1)
            questions = build_questions(meta["sl_bp"], meta["tp_bp"], meta["horizon_min"])
            row = await self._ask(symbol, state, questions, meta, probe=None)
            if row.get("parsed_json") is not None:
                decision = decide(row["parsed_json"], meta, self.s, has_position=pos is not None)
                dj = {**decision.as_dict(), "cost_bp": meta["cost_bp"], "meta": meta}
                self.db.update("decisions", row["id"], {"decision_json": dj, "action": decision.action})
                row.update(decision_json=dj, action=decision.action)
                self.emit("decision", self._decision_view(row))
                if decision.action in ("long", "short") and self.executor:
                    ev = decision.ev_long_r if decision.action == "long" else decision.ev_short_r
                    res = await self.executor.open(symbol=symbol, side=decision.action, sl_bp=meta["sl_bp"], tp_bp=meta["tp_bp"],
                                                   decision_id=row["id"], ev_r=ev or 0.0, exploration=decision.exploration,
                                                   decision_price=meta["price"])
                    if not res.get("ok"):
                        self.emit("event", self.db.event("info", "signal_not_traded", f"{decision.action} signal not traded: {res.get('reason')}", symbol))
            else:
                self.emit("decision", self._decision_view(row))
            if force_probe or random.random() < self.probe_rate:
                asyncio.create_task(self._probes(symbol, state, questions, meta, row["id"]))
            return row
        finally:
            self.inflight.discard(symbol)

    async def _ask(self, symbol: str, state: dict, questions: dict, meta: dict, *, probe: str | None, probe_of: int | None = None) -> dict[str, Any]:
        row: dict[str, Any] = {
            "ts": time.time(), "symbol": symbol, "bar_time": meta.get("bar_time"), "qset_version": QUESTION_SET_VERSION,
            "probe": probe, "probe_of": probe_of, "decision_tf": self.s.decision_tf,
            "state_json": state, "questions_json": questions, "price": meta["price"],
            "testnet_price": meta.get("testnet_price"), "sl_bp": meta["sl_bp"], "tp_bp": meta["tp_bp"], "horizon_min": meta["horizon_min"],
        }
        try:
            res = await self.kairos.ask(state, questions)
            parsed, issues = validate(res.answers, questions)
            row.update(answers_json=res.answers, parsed_json=parsed, issues_json=issues, usage_json=res.usage,
                       model_meta_json=res.metadata, latency_ms=round(res.latency_ms, 1), attempts=res.attempts,
                       request_bytes=res.request_bytes, request_id=res.metadata.get("request_id"))
            self.last_decision_error = None
        except KairosError as exc:
            row.update(error_kind=exc.kind, error_msg=exc.message[:1000], request_id=exc.request_id)
            self.last_decision_error = f"{exc.kind}: {exc.message[:200]}"
            self.emit("event", self.db.event("error", f"kairos_{exc.kind}", exc.message[:300], symbol))
        row["id"] = self.db.insert("decisions", row)
        return row

    async def _probes(self, symbol: str, state: dict, questions: dict, meta: dict, live_id: int) -> None:
        """Consistency probes (extra budget): identical repeat, and a mirrored market."""
        if not self.budget.try_take():
            return
        await self._ask(symbol, state, questions, meta, probe="repeat", probe_of=live_id)
        if not (self.budget.try_take() and self.budget.try_take()):
            return
        base = {k: v for k, v in state.items() if k not in ("recent_headlines", "open_position")}
        base_row = await self._ask(symbol, base, questions, meta, probe="mirror_base", probe_of=live_id)
        await self._ask(symbol, mirror_state(state), questions, meta, probe="mirror", probe_of=base_row["id"])
        self.emit("probe", {"symbol": symbol, "live_id": live_id})

    # ------------------------------------------------------------------ news
    async def on_news(self, item: dict[str, Any]) -> None:
        if not (self.s.kairos_news_enabled and self.kairos.configured):
            return
        targets = item["symbols"][:2]
        for sym in targets:
            if not self.news_budget.try_take():
                return
            published = item.get("published_at") or datetime.fromtimestamp(item["first_seen_ts"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            body = news_profile.build_request(
                news_text=f"{item['title']}. {item['summary'][:400]}".strip(), target_asset=sym.replace("USDT", ""),
                source_name=item["source"], published_at_utc=published, horizon="15m", round_trip_cost_bp=self.s.round_trip_cost_bp)
            row: dict[str, Any] = {"id": f"{item['id']}:{sym}:15m", "ts": time.time(), "symbol": sym, "source": item["source"],
                                   "title": item["title"], "published_at": published, "horizon": "15m",
                                   "cost_bp": self.s.round_trip_cost_bp, "entry_ts": item["first_seen_ts"]}
            try:
                res = await self.kairos.ask(body["state"], body["questions"], task_profile="news_return")
                probs, issues = news_profile.validate(res.answers)
                row.update(probs_json=probs, issues_json=issues, usage_json=res.usage, latency_ms=round(res.latency_ms, 1),
                           request_id=res.metadata.get("request_id"))
            except KairosError as exc:
                row.update(error_kind=exc.kind, error_msg=exc.message[:500], request_id=exc.request_id)
            self.db.insert("news", row)
            self.emit("news", {"id": row["id"]})

    # ------------------------------------------------------------------ views
    @staticmethod
    def _decision_view(d: dict[str, Any]) -> dict[str, Any]:
        dj = d.get("decision_json") or {}
        return {
            "id": d["id"], "ts": d["ts"], "symbol": d["symbol"], "action": d.get("action"), "reason": dj.get("reason"),
            "ev_long_r": dj.get("ev_long_r"), "ev_short_r": dj.get("ev_short_r"), "setup_quality": dj.get("setup_quality"),
            "data_sufficient": dj.get("data_sufficient"), "coherence": dj.get("coherence"), "expected_move_in_d": dj.get("expected_move_in_d"),
            "latency_ms": d.get("latency_ms"), "error_kind": d.get("error_kind"), "error_msg": d.get("error_msg"),
            "issues": d.get("issues_json"), "parsed": d.get("parsed_json"), "price": d.get("price"), "sl_bp": d.get("sl_bp"),
            "tp_bp": d.get("tp_bp"), "label": d.get("label_json"), "probe": d.get("probe"),
            "exploration": dj.get("exploration", False), "strict_action": dj.get("strict_action"),
        }
