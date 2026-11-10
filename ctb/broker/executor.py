"""Testnet perpetual execution: entries with server-side TP/SL, verification, time-stops,
reconciliation, kill switch and daily loss limit.

Invariants:
- Every open position has an exchange-side stop loss, confirmed by reading the position back.
  If it cannot be confirmed, the position is flattened immediately.
- TP/SL are re-anchored to the actual testnet fill (testnet prices drift from mainnet).
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


def _floor_step(x: float, step: float) -> float:
    return math.floor(x / step + 1e-9) * step


def _round_tick(x: float, tick: float) -> float:
    return round(round(x / tick) * tick, 10)


def _fmt(x: float, step: float) -> str:
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return f"{x:.{decimals}f}"


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
        self.closed_pnl: list[dict[str, Any]] = []
        self._lev_set: set[str] = set()
        self._sym_locks: dict[str, asyncio.Lock] = {}
        self.halted_reason: str | None = None
        self.last_sync = 0.0
        self.sync_error: str | None = None

    async def start(self) -> None:
        await self.client.sync_time()
        self.instruments = await self.client.instruments(self.s.symbols)
        await self.sync()

    def lock(self, symbol: str) -> asyncio.Lock:
        return self._sym_locks.setdefault(symbol, asyncio.Lock())

    # ------------------------------------------------------------------ state sync
    async def sync(self) -> None:
        try:
            self.wallet = await self.client.wallet()
            self.positions = await self.client.positions()
            self.open_orders = await self.client.open_orders()
            self.last_sync = time.time()
            self.sync_error = None
        except Exception as exc:  # noqa: BLE001
            self.sync_error = str(exc)

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
        if self.position(symbol):
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
                   exploration: bool = False) -> dict[str, Any]:
        from ..policy import size_position

        async with self.lock(symbol):
            blocked = self.check_guards(symbol)
            if blocked:
                return {"ok": False, "reason": blocked}
            inst = self.instruments[symbol]
            tk = await self.client.ticker(symbol)
            p0 = float(tk["lastPrice"])
            equity = float(self.wallet["equity"])
            risk_pct = min(self.s.explore_risk_pct, self.s.risk_per_trade_pct) if exploration else None
            qty_raw, _ = size_position(equity, sl_bp, p0, self.s, risk_pct)
            qty = _floor_step(min(qty_raw, inst["max_qty"]), inst["qty_step"])
            if qty < inst["min_qty"] or qty * p0 < inst["min_notional"]:
                return {"ok": False, "reason": f"size {qty} below exchange minimum"}
            lev = min(self.s.max_leverage, int(inst["max_leverage"]))
            if symbol not in self._lev_set:
                await self.client.set_leverage(symbol, lev)
                self._lev_set.add(symbol)

            sign = 1 if side == "long" else -1
            tick = inst["tick_size"]
            tp0 = _round_tick(p0 * (1 + sign * tp_bp / 10000), tick)
            sl0 = _round_tick(p0 * (1 - sign * sl_bp / 10000), tick)
            link = f"ctb-{decision_id}-{uuid.uuid4().hex[:8]}"
            trade_id = self.db.insert("trades", {
                "decision_id": decision_id, "symbol": symbol, "side": side, "qty": qty, "notional": qty * p0,
                "entry_link_id": link, "tp_price": tp0, "sl_price": sl0, "protection": "pending",
                "opened_ts": time.time(), "deadline_ts": time.time() + self.s.horizon_bars * self.s.decision_tf_minutes * 60,
                "status": "opening", "ev_r": ev_r, "risk_usd": qty * p0 * sl_bp / 10000,
                "notes": "exploration" if exploration else "strict",
            })
            try:
                res = await self.client.market_order(symbol, "buy" if side == "long" else "sell", _fmt(qty, inst["qty_step"]),
                                                     link_id=link, take_profit=_fmt(tp0, tick), stop_loss=_fmt(sl0, tick))
            except BybitError as exc:
                self.db.update("trades", trade_id, {"status": "error", "notes": str(exc)})
                self.emit("event", self.db.event("error", "entry_rejected", str(exc), symbol))
                return {"ok": False, "reason": str(exc)}

            # Fill price from order history (testnet may take a moment to post it)
            fill = None
            for _ in range(10):
                await asyncio.sleep(0.5)
                o = await self.client.order_history(symbol, link)
                if o and o.get("orderStatus") in ("Filled", "PartiallyFilledCanceled") and float(o.get("avgPrice") or 0) > 0:
                    fill = o
                    break
            await self.sync()
            pos = self.position(symbol)
            if not pos:
                self.db.update("trades", trade_id, {"status": "error", "notes": f"no position after entry; order={fill}"})
                return {"ok": False, "reason": "entry did not produce a position"}

            entry = float(pos["avgPrice"])
            filled_qty = float(pos["size"])
            tp = _round_tick(entry * (1 + sign * tp_bp / 10000), tick)
            sl = _round_tick(entry * (1 - sign * sl_bp / 10000), tick)
            protection = "attached"
            if abs(tp - tp0) >= tick or abs(sl - sl0) >= tick:
                try:
                    await self.client.set_trading_stop(symbol, take_profit=_fmt(tp, tick), stop_loss=_fmt(sl, tick))
                    protection = "attached+reanchored"
                except BybitError as exc:
                    if exc.ret_code != 34040:  # not modified
                        protection = f"attached (reanchor failed: {exc.ret_code})"
                        tp, sl = tp0, sl0
            # verify the stop really is on the position
            await self.sync()
            pos = self.position(symbol)
            sl_live = float(pos.get("stopLoss") or 0) if pos else 0
            if pos and sl_live <= 0:
                try:
                    await self.client.set_trading_stop(symbol, take_profit=_fmt(tp, tick), stop_loss=_fmt(sl, tick))
                    await self.sync()
                    pos = self.position(symbol)
                    sl_live = float(pos.get("stopLoss") or 0) if pos else 0
                    protection = "trading_stop_fallback"
                except BybitError:
                    pass
            if pos and sl_live <= 0:
                await self._flatten(symbol, pos, "unprotected")
                self.db.update("trades", trade_id, {"status": "error", "protection": "failed", "notes": "stop not confirmed; flattened"})
                self.emit("event", self.db.event("error", "unprotected_flattened", "stop loss could not be confirmed; position flattened", symbol))
                return {"ok": False, "reason": "stop not confirmed; flattened"}

            self.db.update("trades", trade_id, {
                "status": "open", "entry_order_id": (res or {}).get("orderId"), "entry_price": entry, "qty": filled_qty,
                "notional": filled_qty * entry, "tp_price": float(pos.get("takeProfit") or tp), "sl_price": sl_live or sl,
                "protection": protection, "risk_usd": filled_qty * entry * sl_bp / 10000,
            })
            msg = f"{'EXPLORE ' if exploration else ''}{side.upper()} {filled_qty} @ {entry} | TP {pos.get('takeProfit')} SL {pos.get('stopLoss')} ({protection})"
            self.emit("event", self.db.event("trade", "opened", msg, symbol))
            self.emit("trade", {"id": trade_id})
            return {"ok": True, "trade_id": trade_id, "entry": entry, "qty": filled_qty}

    async def _flatten(self, symbol: str, pos: dict[str, Any], reason: str) -> None:
        side = "sell" if pos["side"] == "Buy" else "buy"
        await self.client.market_order(symbol, side, pos["size"], link_id=f"ctb-x-{reason[:6]}-{uuid.uuid4().hex[:8]}", reduce_only=True)
        await self.sync()

    async def close(self, symbol: str, reason: str) -> bool:
        async with self.lock(symbol):
            await self.sync()
            pos = self.position(symbol)
            if not pos:
                return False
            await self._flatten(symbol, pos, reason)
            t = self.db.one("SELECT id FROM trades WHERE symbol=? AND status='open' ORDER BY id DESC LIMIT 1", (symbol,))
            if t:
                self.db.update("trades", t["id"], {"close_reason": reason})
            self.emit("event", self.db.event("trade", "close_requested", f"closing {symbol}: {reason}", symbol))
            return True

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

    # ------------------------------------------------------------------ reconcile loop
    async def reconcile_loop(self) -> None:
        while True:
            await asyncio.sleep(5)
            try:
                await self.sync()
                if self.s.kill_file.exists() and not self.halted_reason:
                    await self.kill("KILL file")
                open_trades = self.db.query("SELECT * FROM trades WHERE status='open'")
                live = {p["symbol"]: p for p in self.positions}
                for t in open_trades:
                    if t["symbol"] not in live:
                        await self._record_close(t)
                    elif time.time() >= (t["deadline_ts"] or 1e18):
                        await self.close(t["symbol"], "time_stop")
                # positions we did not open (manual on the testnet UI) are reported, not touched
                self.emit("account", self.account_view())
            except Exception as exc:  # noqa: BLE001
                self.sync_error = str(exc)

    async def _record_close(self, t: dict[str, Any]) -> None:
        start_ms = int(t["opened_ts"] * 1000) - 5000
        rows = await self.client.closed_pnl(limit=50, start_ms=start_ms)
        rows = [r for r in rows if r["symbol"] == t["symbol"]]
        if not rows:
            return  # not posted yet; try next cycle
        r = max(rows, key=lambda x: int(x["updatedTime"]))
        exit_px = float(r["avgExitPrice"])
        pnl = float(r["closedPnl"])
        fees = 0.0
        try:
            execs = await self.client.executions(t["symbol"], limit=50, start_ms=start_ms)
            fees = sum(float(e.get("execFee") or 0) for e in execs)
        except Exception:  # noqa: BLE001
            pass
        reason = t.get("close_reason")
        if not reason:
            tol = 0.0015
            if t["side"] == "long":
                reason = "take_profit" if exit_px >= t["tp_price"] * (1 - tol) else "stop_loss" if exit_px <= t["sl_price"] * (1 + tol) else "external"
            else:
                reason = "take_profit" if exit_px <= t["tp_price"] * (1 + tol) else "stop_loss" if exit_px >= t["sl_price"] * (1 - tol) else "external"
        self.db.update("trades", t["id"], {"status": "closed", "closed_ts": int(r["updatedTime"]) / 1000, "exit_price": exit_px,
                                           "realized_pnl": pnl, "fees": fees, "close_reason": reason})
        r_mult = pnl / t["risk_usd"] if t.get("risk_usd") else None
        self.emit("event", self.db.event("trade", "closed", f"{t['side']} closed by {reason}: pnl {pnl:+.2f} USDT"
                                         + (f" ({r_mult:+.2f}R)" if r_mult is not None else ""), t["symbol"]))
        self.emit("trade", {"id": t["id"]})

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
        }
