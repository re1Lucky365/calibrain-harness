"""Turn raw market state into a compact, self-describing Kairos `state` object.

Design rules (driven by Kairos's 4096-token-per-question context):
- Only relative, unit-labelled numbers (percent, bp, ratios). Raw MA price levels are useless
  to a reader and cost tokens; "price is 0.42% above EMA50" is what a trader actually reads.
- Every field name says its unit. A short glossary is included for derived fields.
- The trade plan (exact stop/target distances, horizon, costs) is in state so every bracket
  question is concrete and its answer can be labelled against what really happened.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any

from . import indicators as ind
from .config import Settings
from .market.store import MarketStore, SymbolState

TF_LABEL = {"1": "1m", "5": "5m", "15": "15m", "60": "1h", "240": "4h"}


def _r(x: float | None, nd: int = 2) -> float | None:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return None
    return round(x, nd)


def tf_block(st: SymbolState, tf: str) -> dict[str, Any] | None:
    bars = st.closed_bars(tf)
    if len(bars) < 60:
        return None
    c = [b.c for b in bars]
    h = [b.h for b in bars]
    l = [b.l for b in bars]
    v = [b.v for b in bars]
    last = c[-1]
    e20, e50, e200 = ind.ema(c, 20), ind.ema(c, 50), ind.ema(c, 200)
    m = ind.macd(c)
    a = ind.adx(h, l, c)
    atr = ind.atr(h, l, c)
    vol_avg = ind.sma(v[:-1], 20)
    return {
        "return_last_bar_pct": _r(ind.pct_change(c, 1), 3),
        "return_last_12_bars_pct": _r(ind.pct_change(c, 12), 3),
        "price_vs_ema20_pct": _r(100 * (last / e20 - 1), 3) if e20 else None,
        "price_vs_ema50_pct": _r(100 * (last / e50 - 1), 3) if e50 else None,
        "price_vs_ema200_pct": _r(100 * (last / e200 - 1), 3) if e200 else None,
        "ema20_vs_ema50_pct": _r(100 * (e20 / e50 - 1), 3) if e20 and e50 else None,
        "rsi14": _r(ind.rsi(c), 1),
        "adx14": _r(a[0], 1) if a else None,
        "di_plus_minus_di": _r(a[1] - a[2], 1) if a else None,
        "macd_hist_pct_of_price": _r(100 * m[2] / last, 4) if m else None,
        "atr14_pct": _r(100 * atr / last, 3) if atr else None,
        "bollinger_position_0_to_1": _r(ind.bollinger_pos(c), 2),
        "bollinger_width_pct": _r(ind.bollinger_width_pct(c), 3),
        "range_position_50_bars_0_to_1": _r(ind.range_position(h, l, last, 50), 2),
        "volume_vs_20bar_avg": _r(v[-1] / vol_avg, 2) if vol_avg else None,
    }


def atr_pct(st: SymbolState, tf: str) -> float | None:
    bars = st.closed_bars(tf)
    if len(bars) < 20:
        return None
    a = ind.atr([b.h for b in bars], [b.l for b in bars], [b.c for b in bars])
    return 100 * a / bars[-1].c if a else None


def bracket(st: SymbolState, s: Settings) -> dict[str, float] | None:
    """Stop/target distances in bp, sized from ATR and clamped to sane bounds."""
    a = atr_pct(st, s.atr_tf)
    if a is None:
        return None
    sl_bp = min(max(a * 100 * s.sl_atr, s.min_sl_bp), s.max_sl_bp)
    tp_bp = sl_bp * (s.tp_atr / s.sl_atr)
    return {"sl_bp": round(sl_bp, 1), "tp_bp": round(tp_bp, 1), "atr_pct": a, "reward_risk": round(tp_bp / sl_bp, 3)}


BASE_RATE_MIN_WINDOWS = 60


def base_rates(bars: list, horizon_bars: int, sl_bp: float, tp_bp: float) -> dict[str, Any] | None:
    """Empirical frequencies of every labelled outcome for THIS bracket and horizon on the
    instrument's own recent history (completed windows only: no look-ahead).

    Each closed bar is a hypothetical entry at its close; the next `horizon_bars` bars are the path.
    A bar touching target and stop together counts as stop first, as in the labeller. Laplace
    smoothing keeps every probability inside (0, 1). This is the forecast a model with no specific
    information should give, so it is both an anchor for Kairos and the baseline it must beat."""
    from .evaluation.labeler import bracket_outcome, return_bin

    n_win = len(bars) - horizon_bars - 1
    if n_win < BASE_RATE_MIN_WINDOWS:
        return None
    cnt = {"long": {"target_first": 0, "stop_first": 0, "neither": 0}, "short": {"target_first": 0, "stop_first": 0, "neither": 0}}
    bins = [0] * 7
    up = move = 0
    d = sl_bp / 10000
    for i in range(n_win):
        entry = bars[i].c
        path = [(b.t, b.o, b.h, b.l, b.c) for b in bars[i + 1: i + 1 + horizon_bars]]
        for side in ("long", "short"):
            cnt[side][bracket_outcome(path, entry, side, tp_bp, sl_bp)["outcome"]] += 1
        ret_pct = 100 * (path[-1][4] / entry - 1)
        bins[return_bin(ret_pct, sl_bp)] += 1
        up += ret_pct > 0
        move += max(b[2] for b in path) >= entry * (1 + d) or min(b[3] for b in path) <= entry * (1 - d)
    sm = lambda k, n, m: round((k + 1) / (n + m), 4)  # noqa: E731
    span_h = (bars[-1].t - bars[0].t) / 3_600_000
    return {
        "lookback_hours": round(span_h, 1),
        "windows": n_win,
        "long_bracket": {k: sm(v, n_win, 3) for k, v in cnt["long"].items()},
        "short_bracket": {k: sm(v, n_win, 3) for k, v in cnt["short"].items()},
        "return_distribution": {f"bin_{i}": sm(b, n_win, 7) for i, b in enumerate(bins)},
        "price_higher_at_horizon": sm(up, n_win, 2),
        "moved_at_least_one_stop_either_way": sm(move, n_win, 2),
    }


def _oi_change(st: SymbolState, minutes: int) -> float | None:
    h = st.oi_history
    if len(h) < 2:
        return None
    last_ts, last = h[-1]
    past = next((oi for ts, oi in reversed(h) if ts <= last_ts - minutes * 60000), None)
    return 100 * (last / past - 1) if past else None


def _ls(st: SymbolState) -> tuple[float | None, float | None]:
    if not st.ls_ratio:
        return None, None
    last = st.ls_ratio[-1]
    past = next((r for r in reversed(st.ls_ratio) if r[0] <= last[0] - 3600_000), None)
    return last[1], (last[1] - past[1]) if past else None


def book_block(st: SymbolState) -> dict[str, Any]:
    if not st.bids or not st.asks or time.time() - st.book_ts > 30:
        return {"available": False}
    bb, ba = st.bids[0][0], st.asks[0][0]
    mid = (bb + ba) / 2

    def depth(levels: list[tuple[float, float]], bp: float) -> float:
        lim = mid * bp / 10000
        return sum(p * q for p, q in levels if abs(p - mid) <= lim)

    b10, a10, b25, a25 = depth(st.bids, 10), depth(st.asks, 10), depth(st.bids, 25), depth(st.asks, 25)
    return {
        "spread_bp": _r(10000 * (ba - bb) / mid, 2),
        "bid_depth_within_10bp_usd": round(b10),
        "ask_depth_within_10bp_usd": round(a10),
        "imbalance_within_25bp": _r((b25 - a25) / (b25 + a25), 3) if b25 + a25 else None,
    }


def build_state(store: MarketStore, symbol: str, s: Settings, *, position: dict[str, Any] | None = None, include_news: bool = True) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return (kairos_state, meta). meta holds numbers the policy/labeler need (not sent)."""
    st = store[symbol]
    dec = st.closed_bars(s.decision_tf)
    br = bracket(st, s)
    if len(dec) < 60 or br is None:
        return None
    price = float(st.ticker.get("lastPrice") or dec[-1].c)
    tk = st.ticker

    def fnum(k: str) -> float | None:
        try:
            return float(tk[k])
        except (KeyError, TypeError, ValueError):
            return None

    mark, index, funding = fnum("markPrice"), fnum("indexPrice"), fnum("fundingRate")
    long_ratio, long_ratio_chg = _ls(st)
    f5, f15, f60 = st.flow_window(5), st.flow_window(15), st.flow_window(60)
    l15, l60 = st.liq_window(15), st.liq_window(60)
    horizon_min = s.horizon_bars * s.decision_tf_minutes

    trend = {TF_LABEL[tf]: blk for tf in ("5", "15", "60", "240") if (blk := tf_block(st, tf))}

    cov = st.coverage_minutes()

    def flow(d: dict[str, float], minutes: int) -> dict[str, Any] | None:
        if cov < minutes:
            return None  # not enough stream history: unknown, not zero
        return {
            "taker_buy_share": _r(d["buy_share"], 3),
            "net_taker_flow_usd": round(d["cvd_usd"]),
            "large_trade_net_usd": round(d["large_buy_usd"] - d["large_sell_usd"]),
        }

    day = st.closed_bars("60")[-24:]
    state: dict[str, Any] = {
        "instrument": f"{symbol} USDT-margined perpetual swap",
        "as_of_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "decision_timeframe": TF_LABEL.get(s.decision_tf, s.decision_tf),
        "last_price": price,
        "change_24h_pct": _r(100 * float(tk["price24hPcnt"]), 2) if tk.get("price24hPcnt") else None,
        "range_position_24h_0_to_1": _r(ind.range_position([b.h for b in day], [b.l for b in day], price, len(day)), 2) if day else None,
        "trend_by_timeframe": trend,
        "order_flow": {"last_5m": flow(f5, 5), "last_15m": flow(f15, 15), "last_60m": flow(f60, 60), "stream_coverage_minutes": int(cov)},
        "derivatives": {
            "funding_rate_per_8h_pct": _r(100 * funding, 4) if funding is not None else None,
            "funding_avg_last_3_pct": _r(100 * sum(f for _, f in st.funding_history[-3:]) / 3, 4) if len(st.funding_history) >= 3 else None,
            "mark_minus_index_bp": _r(10000 * (mark / index - 1), 2) if mark and index else None,
            "open_interest_change_1h_pct": _r(_oi_change(st, 60), 3),
            "open_interest_change_4h_pct": _r(_oi_change(st, 240), 3),
            "accounts_long_share": _r(long_ratio, 3),
            "accounts_long_share_change_1h": _r(long_ratio_chg, 3),
            "long_liquidations_15m_usd": round(l15["long_liq_usd"]) if cov >= 15 else None,
            "short_liquidations_15m_usd": round(l15["short_liq_usd"]) if cov >= 15 else None,
            "long_liquidations_60m_usd": round(l60["long_liq_usd"]) if cov >= 60 else None,
            "short_liquidations_60m_usd": round(l60["short_liq_usd"]) if cov >= 60 else None,
        },
        "order_book": book_block(st),
    }

    # cross-market context
    cross: dict[str, Any] = {}
    if symbol != "BTCUSDT" and "BTCUSDT" in store.state:
        b = store["BTCUSDT"].closes("60")
        b5 = store["BTCUSDT"].closes("5")
        cross["btc_return_1h_pct"] = _r(ind.pct_change(b5, 12), 3)
        cross["btc_return_4h_pct"] = _r(ind.pct_change(b, 4), 3)
    fg = store.context.get("fear_greed")
    if fg:
        cross["fear_greed_index_0_100"] = fg["value"]
        cross["fear_greed_week_ago"] = fg["week_ago"]
    macro = store.context.get("macro") or {}
    for k in ("SPX_fut", "DXY", "VIX"):
        if k in macro:
            cross[f"{k}_change_8h_pct"] = macro[k]["chg_8h_pct"]
    if cross:
        state["cross_market"] = cross

    if include_news:
        now = time.time()
        rel = [n for n in store.context.get("news", [])
               if (symbol in n["symbols"] or n["market_wide"]) and now - (n.get("published_ts") or n["first_seen_ts"]) < 6 * 3600][:3]
        if rel:
            state["recent_headlines"] = [
                {"minutes_ago": int((now - (n.get("published_ts") or n["first_seen_ts"])) / 60), "source": n["source"], "title": n["title"][:160]}
                for n in rel
            ]

    if position:
        state["open_position"] = position

    base = base_rates(dec, s.horizon_bars, br["sl_bp"], br["tp_bp"])
    if base:
        state["historical_base_rates"] = {
            "what_this_is": ("How often each outcome actually happened for this exact bracket and horizon on this instrument "
                             f"over the last {base['lookback_hours']}h ({base['windows']} overlapping windows of {TF_LABEL.get(s.decision_tf, s.decision_tf)} bars). "
                             "A forecast without specific evidence should be close to these frequencies."),
            **{k: v for k, v in base.items() if k not in ("lookback_hours", "windows")},
        }

    state["trade_plan"] = {
        "entry": "market order at last_price now",
        "long_stop_pct_below_entry": round(br["sl_bp"] / 100, 3),
        "long_target_pct_above_entry": round(br["tp_bp"] / 100, 3),
        "short_stop_pct_above_entry": round(br["sl_bp"] / 100, 3),
        "short_target_pct_below_entry": round(br["tp_bp"] / 100, 3),
        "max_holding_minutes": horizon_min,
        "round_trip_cost_pct": round(s.round_trip_cost_bp / 100, 3),
    }
    state["glossary"] = {
        "taker_buy_share": "fraction of aggressive (taker) volume that was buying; 0.5 = balanced",
        "net_taker_flow_usd": "taker buy notional minus taker sell notional",
        "accounts_long_share": "share of Bybit accounts net long",
        "di_plus_minus_di": "+DI minus -DI; positive = upward directional pressure",
        "long_liquidations": "forced closes of long positions (selling pressure)",
        "null": "value unknown (not enough data collected yet), not zero",
        "range_position": "0 = at the range low, 1 = at the high; values outside 0..1 mean price broke out of the range",
    }

    meta = {
        "symbol": symbol,
        "price": price,
        "testnet_price": float(st.testnet_ticker["lastPrice"]) if st.testnet_ticker.get("lastPrice") else None,
        "bar_time": dec[-1].t,
        "sl_bp": br["sl_bp"],
        "tp_bp": br["tp_bp"],
        "atr_pct": br["atr_pct"],
        "horizon_min": horizon_min,
        "cost_bp": s.round_trip_cost_bp,
        "base_rates": base,
    }
    return state, meta


# ---------------------------------------------------------------------------- mirror probe
# A mirrored state describes the same market upside-down: every bullish fact becomes the
# equivalent bearish fact. A coherent model should then swap its long and short answers.
_NEGATE = {
    "return_last_bar_pct", "return_last_12_bars_pct", "price_vs_ema20_pct", "price_vs_ema50_pct",
    "price_vs_ema200_pct", "ema20_vs_ema50_pct", "di_plus_minus_di", "macd_hist_pct_of_price",
    "change_24h_pct", "net_taker_flow_usd", "large_trade_net_usd", "funding_rate_per_8h_pct",
    "funding_avg_last_3_pct", "mark_minus_index_bp", "accounts_long_share_change_1h",
    "imbalance_within_25bp", "btc_return_1h_pct", "btc_return_4h_pct", "SPX_fut_change_8h_pct",
}
_HUNDRED_MINUS = {"rsi14", "fear_greed_index_0_100", "fear_greed_week_ago"}
_ONE_MINUS = {"bollinger_position_0_to_1", "range_position_50_bars_0_to_1", "range_position_24h_0_to_1", "taker_buy_share", "accounts_long_share"}
_SWAP = [("long_liquidations_15m_usd", "short_liquidations_15m_usd"), ("long_liquidations_60m_usd", "short_liquidations_60m_usd"),
         ("bid_depth_within_10bp_usd", "ask_depth_within_10bp_usd")]


def mirror_state(state: dict[str, Any]) -> dict[str, Any]:
    def walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool) and v is not None:
                    if k in _NEGATE:
                        v = -v
                    elif k in _HUNDRED_MINUS:
                        v = round(100 - v, 3)
                    elif k in _ONE_MINUS:
                        v = round(1 - v, 3)
                out[k] = walk(v) if isinstance(v, (dict, list)) else v
            for a, b in _SWAP:
                if a in out and b in out:
                    out[a], out[b] = out[b], out[a]
            return out
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        return obj

    m = walk(state)
    hb = state.get("historical_base_rates")
    if hb:  # the mirrored market's history: long and short swap, up and down swap
        m["historical_base_rates"] = {
            **hb,
            "long_bracket": dict(hb["short_bracket"]), "short_bracket": dict(hb["long_bracket"]),
            "return_distribution": {f"bin_{i}": hb["return_distribution"][f"bin_{6 - i}"] for i in range(7)},
            "price_higher_at_horizon": round(1 - hb["price_higher_at_horizon"], 4),
        }
    m.pop("recent_headlines", None)  # text cannot be mirrored
    m.pop("open_position", None)
    m["last_price"] = state["last_price"]
    return m
