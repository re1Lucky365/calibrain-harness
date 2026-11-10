"""Testnet perpetual execution: entries with server-side TP/SL, verification, time-stops,
reconciliation, kill switch and daily loss limit.

Invariants:
- Entries are IOC limit orders capped at a few bp past the best testnet price. They take only the
  liquidity that exists inside the cap, so an order can never sweep a thin testnet book. The
  quantity actually filled is what the trade records, never the quantity requested.
- Every open position has an exchange-side stop loss around the ACTUAL fill, confirmed by reading
  the position back. If the bracket cannot be placed or confirmed, the position is flattened.
- P&L, fees, exit price and R come from Bybit's own records: every closed-P&L row of the exit is
  summed (one exit can fill in several pieces), and R uses the stop that was really live.
- Exits are reduce-only, so a close can never flip into the opposite side.
- One position per symbol, one-way mode.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from ..config import Settings
from ..db import DB
from .bybit_rest import BybitError, BybitTestnet

log = logging.getLogger("ctb.exec")

TERMINAL_ORDER_STATES = {"Filled", "PartiallyFilledCanceled", "Cancelled", "Rejected", "Deactivated"}
CLOSE_GRACE_S = 90  # how long to wait for Bybit to post every closed-P&L row of an exit


def _floor_step(x: float, step: float) -> float:
    return math.floor(x / step + 1e-9) * step


def _round_tick(x: float, tick: float) -> float:
    return round(round(x / tick) * tick, 10)


def _fmt(x: float, step: float) -> str:
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return f"{x:.{decimals}f}"


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def adverse_bp(side: str, price: float, reference: float, *, entry: bool) -> float:
    """Slippage in bp, positive = worse for us. Entry: paid above (long) / sold below (short) the
    reference. Exit: sold below (long) / bought above (short) the reference (the trigger)."""
    if not reference:
        return 0.0
    move = (price - reference) / reference * 10000
    long_side = side == "long"
    worse_up = long_side if entry else not long_side
    return round(move if worse_up else -move, 2)


def depth_within(levels: list[tuple[float, float]], cap: float, *, buy: bool) -> float:
    """Quantity resting on the book at prices no worse than cap."""
    return sum(q for p, q in levels if (p <= cap if buy else p >= cap))


def aggregate_close_rows(rows: list[dict[str, Any]], qty: float) -> dict[str, Any] | None:
    """Combine the closed-P&L rows of one exit (oldest first) until they cover the position size.
    `complete` is False while Bybit has not yet posted rows for the whole quantity."""
    rows = sorted(rows, key=lambda r: int(r.get("createdTime") or r.get("updatedTime") or 0))
    used, size = [], 0.0
    for r in rows:
        used.append(r)
        size += _f(r.get("closedSize") or r.get("qty"))
        if size >= qty * (1 - 1e-9):
            break
    if not used or size <= 0:
        return None
    exit_px = sum(_f(r.get("closedSize") or r.get("qty")) * _f(r["avgExitPrice"]) for r in used) / size
    has_fees = all("openFee" in r and "closeFee" in r for r in used)
    return {
        "size": size,
        "complete": size >= qty * (1 - 1e-9),
        "pnl": sum(_f(r["closedPnl"]) for r in used),
        "exit_price": exit_px,
        "open_fee": sum(_f(r.get("openFee")) for r in used) if has_fees else None,
        "close_fee": sum(_f(r.get("closeFee")) for r in used) if has_fees else None,
        "closed_ts": max(int(r["updatedTime"]) for r in used) / 1000,
        "order_ids": list(dict.fromkeys(r["orderId"] for r in used)),
        "rows": [{"orderId": r["orderId"], "size": _f(r.get("closedSize") or r.get("qty")), "exit": _f(r["avgExitPrice"]),
                  "pnl": _f(r["closedPnl"]), "open_fee": _f(r.get("openFee")), "close_fee": _f(r.get("closeFee"))} for r in used],
    }


def reason_from_orders(orders: list[dict[str, Any] | None]) -> str | None:
    kinds = set()
    for o in orders:
        if not o:
            continue
        ct, st = o.get("createType") or "", o.get("stopOrderType") or ""
        if "StopLoss" in ct or st in ("StopLoss", "PartialStopLoss", "TrailingStop"):
            kinds.add("stop_loss")
        elif "TakeProfit" in ct or st in ("TakeProfit", "PartialTakeProfit"):
            kinds.add("take_profit")
        elif "Liq" in ct or "Adl" in ct:
            kinds.add("liquidation")
        elif ct:
            kinds.add("external")
    if not kinds:
        return None
    return "+".join(sorted(kinds))


class Executor:
    def __init__(self, s: Settings, db: DB, client: BybitTestnet, emit: Callable[[str, dict], None]):
        self.s = s
        self.db = db
        self.client = client
        self.emit = emit
        self.instruments: dict[str, dict[str, Any]] = {}
        self.wallet: dict[str, Any] = {}
        self.positions: list[dict[str, Any]] = []
        self.open_orders: list[dict[str, Any]] = []
        self._lev_set: set[str] = set()
        self._sym_locks: dict[str, asyncio.Lock] = {}
        self._flat_since: dict[int, float] = {}
        self._closing: set[int] = set()
        self._sync_lock = asyncio.Lock()
        self._sync_pending = False
        self.halted_reason: str | None = None
        self.last_sync = 0.0
        self.sync_error: str | None = None
        self.stream = None  # BybitPrivateStream, attached by the engine

    async def start(self) -> None:
        await self.client.sync_time()
        self.instruments = await self.client.instruments(self.s.symbols)
        await self.sync()

    def lock(self, symbol: str) -> asyncio.Lock:
        return self._sym_locks.setdefault(symbol, asyncio.Lock())

    # ------------------------------------------------------------------ state sync
    async def sync(self) -> None:
        async with self._sync_lock:
            try:
                wallet, positions, orders = await asyncio.gather(
                    self.client.wallet(), self.client.positions(), self.client.open_orders())
                self.wallet, self.positions, self.open_orders = wallet, positions, orders
                self.last_sync = time.time()
                self.sync_error = None
            except Exception as exc:  # noqa: BLE001
                self.sync_error = str(exc)

    async def on_private(self, topic: str, data: list[dict[str, Any]]) -> None:
        """Private-stream push: refresh the account at once and record any trade that went flat."""
        if self._sync_pending:
            return
        self._sync_pending = True
        try:
            await asyncio.sleep(0.05)  # coalesce the burst of order/execution/position/wallet messages
            await self.sync()
            await self._check_closes(time_stops=False)
            self.emit("account", self.account_view())
        finally:
            self._sync_pending = False

    def position(self, symbol: str) -> dict[str, Any] | None:
        return next((p for p in self.positions if p["symbol"] == symbol), None)

    def position_summary(self, symbol: str) -> dict[str, Any] | None:
        p = self.position(symbol)
        if not p:
            return None
        trade = self.db.one("SELECT * FROM trades WHERE symbol=? AND status='open' ORDER BY id DESC LIMIT 1", (symbol,))
        out = {"side": "long" if p["side"] == "Buy" else "short", "entry_price": float(p["avgPrice"]),
               "unrealized_pnl_usd": round(float(p.get("unrealisedPnl") or 0), 2)}
        if trade:
            out["minutes_open"] = int((time.time() - trade["opened_ts"]) / 60)
            out["minutes_until_time_stop"] = max(0, int((trade["deadline_ts"] - time.time()) / 60))
        return out

    # ------------------------------------------------------------------ guards
    def daily_pnl(self) -> float:
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        row = self.db.one("SELECT COALESCE(SUM(realized_pnl),0) AS pnl FROM trades WHERE closed_ts>=?", (start,))
        unreal = sum(float(p.get("unrealisedPnl") or 0) for p in self.positions)
        return float(row["pnl"] if row else 0) + unreal

    def check_guards(self, symbol: str) -> str | None:
        if self.halted_reason:
            return f"halted: {self.halted_reason}"
        if self.s.kill_file.exists():
            return "kill switch file present"
        if not self.s.trading_enabled:
            return "trading disabled (observe-only)"
        if self.sync_error or time.time() - self.last_sync > 60:
            return "account state is stale"
        if self.position(symbol) or self.db.one("SELECT id FROM trades WHERE symbol=? AND status IN ('open','opening')", (symbol,)):
            return "position already open on symbol"
        if len(self.positions) >= self.s.max_positions:
            return f"max positions ({self.s.max_positions}) reached"
        eq = self.wallet.get("equity") or 0
        if eq <= 0:
            return "no equity"
        if self.daily_pnl() <= -eq * self.s.daily_loss_limit_pct / 100:
            self.halted_reason = f"daily loss limit {self.s.daily_loss_limit_pct}% hit"
            return self.halted_reason
        return None

    # ------------------------------------------------------------------ entries
    async def open(self, *, symbol: str, side: str, sl_bp: float, tp_bp: float, decision_id: int, ev_r: float,
                   exploration: bool = False, decision_price: float | None = None) -> dict[str, Any]:
        from ..policy import size_position

        async with self.lock(symbol):
            blocked = self.check_guards(symbol)
            if blocked:
                return {"ok": False, "reason": blocked}
            t0 = time.time()
            inst = self.instruments[symbol]
            tick, step = inst["tick_size"], inst["qty_step"]
            buy = side == "long"
            sign = 1 if buy else -1

            book = await self.client.orderbook(symbol, 50)
            levels = book["asks"] if buy else book["bids"]
            if not levels or not book["bids"] or not book["asks"]:
                return {"ok": False, "reason": "testnet book is empty"}
            ref = levels[0][0]  # best price we can trade at right now
            mid = (book["bids"][0][0] + book["asks"][0][0]) / 2
            spread_bp = (book["asks"][0][0] - book["bids"][0][0]) / mid * 10000
            raw_cap = ref * (1 + sign * self.s.max_entry_slip_bp / 10000)
            cap = round((math.floor(raw_cap / tick + 1e-9) if buy else math.ceil(raw_cap / tick - 1e-9)) * tick, 10)
            depth = depth_within(levels, cap, buy=buy)

            equity = float(self.wallet["equity"])
            risk_pct = min(self.s.explore_risk_pct, self.s.risk_per_trade_pct) if exploration else None
            qty_raw, _ = size_position(equity, sl_bp, ref, self.s, risk_pct)
            qty = _floor_step(min(qty_raw, inst["max_qty"], depth), step)
            if qty < inst["min_qty"] or qty * ref < inst["min_notional"]:
                why = (f"only {depth:g} on the testnet book within {self.s.max_entry_slip_bp:g}bp of {ref}"
                       if depth < qty_raw else f"size {qty} below exchange minimum")
                return {"ok": False, "reason": why}

            lev = min(self.s.max_leverage, int(inst["max_leverage"]))
            if symbol not in self._lev_set:
                await self.client.set_leverage(symbol, lev)
                self._lev_set.add(symbol)

            # provisional bracket valid for any fill inside the cap; re-anchored to the real fill below
            tp0 = _round_tick(cap * (1 + sign * tp_bp / 10000), tick)
            sl0 = _round_tick(cap * (1 - sign * sl_bp / 10000), tick)
            link = f"ctb-{decision_id}-{uuid.uuid4().hex[:8]}"
            mode = "exploration" if exploration else "strict"
            trade_id = self.db.insert("trades", {
                "decision_id": decision_id, "symbol": symbol, "side": side, "qty": qty, "notional": qty * ref,
                "entry_link_id": link, "tp_price": tp0, "sl_price": sl0, "protection": "pending",
                "opened_ts": time.time(), "deadline_ts": time.time() + self.s.horizon_bars * self.s.decision_tf_minutes * 60,
                "status": "opening", "ev_r": ev_r, "risk_usd": qty * ref * sl_bp / 10000, "mode": mode, "notes": mode,
                "requested_qty": qty, "ref_price": ref, "cap_price": cap, "decision_price": decision_price,
                "book_spread_bp": round(spread_bp, 2), "planned_sl_bp": sl_bp, "planned_tp_bp": tp_bp,
            })
            try:
                res = await self.client.market_order(symbol, "buy" if buy else "sell", _fmt(qty, step), link_id=link,
                                                     take_profit=_fmt(tp0, tick), stop_loss=_fmt(sl0, tick), limit_price=_fmt(cap, tick),
                                                     trigger_by=self.s.tpsl_trigger)
            except BybitError as exc:
                self.db.update("trades", trade_id, {"status": "error", "notes": str(exc)})
                self.emit("event", self.db.event("error", "entry_rejected", str(exc), symbol))
                return {"ok": False, "reason": str(exc)}

            order = await self._wait_order(symbol, link)
            filled = _f(order.get("cumExecQty")) if order else 0.0
            if filled <= 0:
                self.db.update("trades", trade_id, {"status": "unfilled", "entry_order_id": (res or {}).get("orderId"),
                                                    "notes": f"IOC got no fill within {self.s.max_entry_slip_bp:g}bp cap ({(order or {}).get('orderStatus')})"})
                self.emit("event", self.db.event("warn", "entry_unfilled", f"{side} IOC got no fill up to {cap}", symbol))
                return {"ok": False, "reason": "no testnet liquidity inside the price cap"}

            entry = _f(order["avgPrice"])
            slip = adverse_bp(side, entry, ref, entry=True)
            tp = _round_tick(entry * (1 + sign * tp_bp / 10000), tick)
            sl = _round_tick(entry * (1 - sign * sl_bp / 10000), tick)
            # Record the fill before touching the bracket: if the stop fires in the next second the
            # reconciler still has a complete open trade to close.
            self.db.update("trades", trade_id, {
                "status": "open", "entry_order_id": order.get("orderId"), "entry_price": entry, "qty": filled,
                "notional": filled * entry, "entry_fee": _f(order.get("cumExecFee")), "entry_slip_bp": slip,
                "risk_usd": filled * abs(entry - sl0), "entry_latency_ms": round((time.time() - t0) * 1000),
            })
            protection, tp_live, sl_live = await self._anchor_bracket(symbol, side, entry, tp, sl, tick)
            if protection == "flattened":
                self.db.update("trades", trade_id, {"protection": "failed"})
                return {"ok": False, "reason": "bracket could not be placed around the fill; flattened", "trade_id": trade_id}
            if protection == "gone":
                self.db.update("trades", trade_id, {"protection": "closed_before_anchor"})
                return {"ok": True, "trade_id": trade_id, "entry": entry, "qty": filled}
            self.db.update("trades", trade_id, {"protection": protection, "tp_price": tp_live, "sl_price": sl_live,
                                                "risk_usd": filled * abs(entry - sl_live)})
            part = "" if filled >= qty - 1e-12 else f" (partial: {filled:g} of {qty:g})"
            msg = (f"{'EXPLORE ' if exploration else ''}{side.upper()} {filled:g}{part} @ {entry} | ref {ref} slip {slip:+.1f}bp "
                   f"| TP {tp_live} SL {sl_live} ({protection})")
            self.emit("event", self.db.event("trade", "opened", msg, symbol))
            self.emit("trade", {"id": trade_id})
            return {"ok": True, "trade_id": trade_id, "entry": entry, "qty": filled}

    async def _wait_order(self, symbol: str, link: str, timeout_s: float = 5.0) -> dict[str, Any] | None:
        deadline = time.time() + timeout_s
        last = None
        while time.time() < deadline:
            await asyncio.sleep(0.15)
            try:
                last = await self.client.order_history(symbol, order_link_id=link)
            except BybitError:
                continue
            if last and last.get("orderStatus") in TERMINAL_ORDER_STATES:
                return last
        return last

    async def _anchor_bracket(self, symbol: str, side: str, entry: float, tp: float, sl: float, tick: float) -> tuple[str, float, float]:
        """Move TP/SL to sit around the actual entry and confirm the stop is live.
        Returns (protection, tp_live, sl_live); protection is 'flattened' or 'gone' on failure/early exit."""
        buy = side == "long"
        protection = "anchored"
        try:
            await self.client.set_trading_stop(symbol, take_profit=_fmt(tp, tick), stop_loss=_fmt(sl, tick), trigger_by=self.s.tpsl_trigger)
        except BybitError as exc:
            if exc.ret_code != 34040:  # 34040 = not modified, already equal
                # Usually the mark has already moved past one of the levels (Bybit rejects a stop on
                # the wrong side of the mark). Decide from the mark instead of keeping a bad bracket.
                tk = await self.client.ticker(symbol)
                mark = _f(tk.get("markPrice" if self.s.tpsl_trigger == "MarkPrice" else "lastPrice"))
                through_stop = mark <= sl if buy else mark >= sl
                through_target = mark >= tp if buy else mark <= tp
                reason = "stop_on_entry" if through_stop else "target_on_entry" if through_target else None
                await self.sync()
                if not self.position(symbol):
                    return "gone", tp, sl
                if reason:
                    await self.close(symbol, reason, locked=True)
                    return "gone", tp, sl
                protection = f"anchor_failed_{exc.ret_code}"
        await self.sync()
        pos = self.position(symbol)
        if not pos:
            return "gone", tp, sl
        sl_live, tp_live = _f(pos.get("stopLoss")), _f(pos.get("takeProfit"))
        stop_ok = sl_live > 0 and (sl_live < entry if buy else sl_live > entry)
        target_ok = tp_live <= 0 or (tp_live > entry if buy else tp_live < entry)
        if not (stop_ok and target_ok):
            await self.close(symbol, "unprotected", locked=True)
            self.emit("event", self.db.event("error", "unprotected_flattened",
                                             f"bracket not live around entry {entry} (SL {sl_live}, TP {tp_live}); flattened", symbol))
            return "flattened", tp_live, sl_live
        return protection, tp_live, sl_live

    async def _flatten(self, symbol: str, pos: dict[str, Any], reason: str) -> None:
        side = "sell" if pos["side"] == "Buy" else "buy"
        await self.client.market_order(symbol, side, pos["size"], link_id=f"ctb-x-{reason[:6]}-{uuid.uuid4().hex[:8]}", reduce_only=True)
        await self.sync()

    async def close(self, symbol: str, reason: str, *, locked: bool = False) -> bool:
        async def _do() -> bool:
            await self.sync()
            pos = self.position(symbol)
            if not pos:
                return False
            t = self.db.one("SELECT id FROM trades WHERE symbol=? AND status='open' ORDER BY id DESC LIMIT 1", (symbol,))
            if t:
                self.db.update("trades", t["id"], {"close_reason": reason})
            await self._flatten(symbol, pos, reason)
            self.emit("event", self.db.event("trade", "close_requested", f"closing {symbol}: {reason}", symbol))
            return True

        if locked:
            return await _do()
        async with self.lock(symbol):
            return await _do()

    async def kill(self, reason: str) -> None:
        self.halted_reason = reason
        self.s.trading_enabled = False
        await self.sync()
        for p in list(self.positions):
            try:
                await self.close(p["symbol"], "kill")
            except Exception as exc:  # noqa: BLE001
                self.emit("event", self.db.event("error", "kill_close_failed", str(exc), p["symbol"]))
        try:
            await self.client.cancel_all()
        except Exception:  # noqa: BLE001
            pass
        self.emit("event", self.db.event("warn", "killed", f"kill switch: {reason}"))

    # ------------------------------------------------------------------ reconcile
    async def reconcile_loop(self) -> None:
        while True:
            await asyncio.sleep(10 if (self.stream and self.stream.connected) else 5)
            try:
                await self.sync()
                if self.s.kill_file.exists() and not self.halted_reason:
                    await self.kill("KILL file")
                await self._check_closes()
                # positions we did not open (manual on the testnet UI) are reported, not touched
                self.emit("account", self.account_view())
            except Exception as exc:  # noqa: BLE001
                self.sync_error = str(exc)

    async def _check_closes(self, *, time_stops: bool = True) -> None:
        live = {p["symbol"]: p for p in self.positions}
        for t in self.db.query("SELECT * FROM trades WHERE status='opening' AND opened_ts < ?", (time.time() - 20,)):
            await self.resolve_opening(t)
        for t in self.db.query("SELECT * FROM trades WHERE status='open'"):
            if self.lock(t["symbol"]).locked() or t["id"] in self._closing:
                continue  # entry or close in progress on this symbol
            if t["symbol"] not in live:
                self._flat_since.setdefault(t["id"], time.time())
                self._closing.add(t["id"])
                try:
                    await self.record_close(t)
                finally:
                    self._closing.discard(t["id"])
                continue
            self._flat_since.pop(t["id"], None)
            if time_stops and time.time() >= (t["deadline_ts"] or 1e18):
                await self.close(t["symbol"], "time_stop")

    async def resolve_opening(self, t: dict[str, Any]) -> None:
        """A trade left in 'opening' (crash or restart between order and bookkeeping): read the entry
        order back from Bybit and either mark it open with its real fill or mark it unfilled."""
        o = await self.client.order_history(t["symbol"], order_link_id=t["entry_link_id"])
        if not o:
            self.db.update("trades", t["id"], {"status": "error", "notes": "entry order not found on Bybit"})
            return
        filled = _f(o.get("cumExecQty"))
        if filled <= 0:
            self.db.update("trades", t["id"], {"status": "unfilled", "entry_order_id": o.get("orderId")})
            return
        entry = _f(o["avgPrice"])
        ref = t.get("ref_price") or entry
        self.db.update("trades", t["id"], {"status": "open", "entry_order_id": o.get("orderId"), "entry_price": entry, "qty": filled,
                                           "notional": filled * entry, "entry_fee": _f(o.get("cumExecFee")),
                                           "entry_slip_bp": adverse_bp(t["side"], entry, ref, entry=True),
                                           "risk_usd": filled * abs(entry - t["sl_price"]) if t.get("sl_price") else t.get("risk_usd"),
                                           "protection": t["protection"] if t.get("protection") not in (None, "pending") else "recovered"})
        self.emit("event", self.db.event("warn", "trade_recovered", f"recovered trade #{t['id']} from Bybit order history", t["symbol"]))

    async def record_close(self, t: dict[str, Any], *, force: bool = False) -> bool:
        """Book a closed trade from Bybit's closed-P&L rows (all of them) and the closing orders."""
        start_ms = int(t["opened_ts"] * 1000) - 2000
        rows = await self.client.closed_pnl(limit=100, start_ms=start_ms, symbol=t["symbol"])
        if t.get("entry_price"):
            rows = [r for r in rows if abs(_f(r.get("avgEntryPrice")) / t["entry_price"] - 1) < 1e-6]
        agg = aggregate_close_rows(rows, t["qty"])
        waited = time.time() - self._flat_since.get(t["id"], time.time())
        if not agg or (not agg["complete"] and not force and waited < CLOSE_GRACE_S):
            return False  # Bybit has not posted the whole exit yet; try again next cycle

        if agg["open_fee"] is not None:
            fees = agg["open_fee"] + agg["close_fee"]
        else:  # older API responses: fall back to the exact executions of the entry and exit orders
            fees = 0.0
            for oid in [t.get("entry_order_id"), *agg["order_ids"]]:
                if oid:
                    fees += sum(_f(e.get("execFee")) for e in await self.client.executions(t["symbol"], order_id=oid))

        reason = t.get("close_reason")
        if not reason:
            orders = [await self.client.order_history(t["symbol"], order_id=oid) for oid in agg["order_ids"]]
            reason = reason_from_orders(orders)
        if not reason:
            tol = 0.0015
            px = agg["exit_price"]
            if t["side"] == "long":
                reason = "take_profit" if px >= t["tp_price"] * (1 - tol) else "stop_loss" if px <= t["sl_price"] * (1 + tol) else "external"
            else:
                reason = "take_profit" if px <= t["tp_price"] * (1 + tol) else "stop_loss" if px >= t["sl_price"] * (1 - tol) else "external"

        trigger = t["sl_price"] if reason == "stop_loss" else t["tp_price"] if reason == "take_profit" else None
        exit_slip = adverse_bp(t["side"], agg["exit_price"], trigger, entry=False) if trigger else None
        entry, sl = t.get("entry_price"), t.get("sl_price")
        risk = t["qty"] * abs(entry - sl) if entry and sl else t.get("risk_usd")
        r_mult = agg["pnl"] / risk if risk else None
        self.db.update("trades", t["id"], {
            "status": "closed", "closed_ts": agg["closed_ts"], "exit_price": agg["exit_price"], "realized_pnl": agg["pnl"],
            "fees": fees, "exit_fee": agg["close_fee"], "close_reason": reason, "exit_slip_bp": exit_slip,
            "risk_usd": risk, "r_mult": r_mult, "exit_fills": len(agg["rows"]), "fills_json": agg["rows"],
            "close_notes": None if agg["complete"] else f"exit rows cover {agg['size']:g} of {t['qty']:g}",
        })
        self._flat_since.pop(t["id"], None)
        self.emit("event", self.db.event("trade", "closed", f"{t['side']} closed by {reason}: pnl {agg['pnl']:+.2f} USDT"
                                         + (f" ({r_mult:+.2f}R)" if r_mult is not None else "")
                                         + (f", {len(agg['rows'])} fills" if len(agg["rows"]) > 1 else "")
                                         + (f", exit slip {exit_slip:+.1f}bp" if exit_slip is not None else ""), t["symbol"]))
        self.emit("trade", {"id": t["id"]})
        return True

    def account_view(self) -> dict[str, Any]:
        return {
            "wallet": self.wallet,
            "positions": [{
                "symbol": p["symbol"], "side": "long" if p["side"] == "Buy" else "short", "size": float(p["size"]),
                "entry": float(p["avgPrice"]), "mark": float(p.get("markPrice") or 0),
                "upnl": float(p.get("unrealisedPnl") or 0), "tp": float(p.get("takeProfit") or 0),
                "sl": float(p.get("stopLoss") or 0), "leverage": p.get("leverage"), "liq_price": p.get("liqPrice"),
                "value": float(p.get("positionValue") or 0),
            } for p in self.positions],
            "open_orders": len(self.open_orders),
            "daily_pnl": round(self.daily_pnl(), 2),
            "halted_reason": self.halted_reason,
            "trading_enabled": self.s.trading_enabled,
            "kill_file": self.s.kill_file.exists(),
            "last_sync": self.last_sync,
            "sync_error": self.sync_error,
            "private_stream": bool(self.stream and self.stream.connected),
        }
