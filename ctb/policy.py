"""Policy: turn Kairos probabilities into a trade decision. All arithmetic, thresholds and
risk rules live here in code, never in the model.

Expected value of a bracket, in R (1R = the stop distance):
    EV = P(target first) * RR  -  P(stop first) * 1  +  P(neither) * E[neither]  -  cost / stop
E[neither] is taken as 0 (the time-stop exits near entry on average; measured later in eval).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .config import Settings
from .kairos.questions import RETURN_BIN_EDGES_IN_D


@dataclass
class Decision:
    action: str  # long | short | flat
    reason: str
    ev_long_r: float | None = None
    ev_short_r: float | None = None
    p_long_target: float | None = None
    p_long_stop: float | None = None
    p_short_target: float | None = None
    p_short_stop: float | None = None
    expected_move_in_d: float | None = None
    setup_quality: float | None = None
    data_sufficient: float | None = None
    coherence: float | None = None
    gates: list[str] = field(default_factory=list)
    strict_action: str = "flat"  # what the strict policy alone would have done
    exploration: bool = False  # True when this trade exists only because of exploration mode

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def expected_move_in_d(probs: dict[str, float]) -> float:
    """Mean of the return distribution, in multiples of the stop distance d."""
    e = RETURN_BIN_EDGES_IN_D
    mids = [e[0] * 1.25, (e[0] + e[1]) / 2, (e[1] + e[2]) / 2, 0.0, (e[3] + e[4]) / 2, (e[4] + e[5]) / 2, e[5] * 1.25]
    return sum(mids[i] * probs[f"bin_{i}"] for i in range(7))


def bracket_ev(p_target: float, p_stop: float, rr: float, cost_r: float) -> float:
    return p_target * rr - p_stop * 1.0 - cost_r


def decide(parsed: dict[str, Any], meta: dict[str, Any], s: Settings, *, has_position: bool) -> Decision:
    required = ("long_bracket", "short_bracket", "setup_quality", "data_sufficient")
    missing = [q for q in required if q not in parsed]
    if missing:
        return Decision("flat", f"invalid/missing answers: {', '.join(missing)} (treated as error, not as a 'no')")

    rr = meta["tp_bp"] / meta["sl_bp"]
    cost_r = meta["cost_bp"] / meta["sl_bp"]
    lb, sb = parsed["long_bracket"]["probs"], parsed["short_bracket"]["probs"]
    ev_l = bracket_ev(lb["target_first"], lb["stop_first"], rr, cost_r)
    ev_s = bracket_ev(sb["target_first"], sb["stop_first"], rr, cost_r)
    # Coherence: a long's target and a short's stop are both "price goes up". For symmetric-ish
    # brackets these should roughly agree. Large disagreement = the model is not self-consistent.
    coherence = 1 - abs(lb["target_first"] - sb["stop_first"]) if abs(rr - 1) < 1e-9 else 1 - abs(
        (lb["target_first"] - lb["stop_first"]) + (sb["target_first"] - sb["stop_first"])) / 2
    em = expected_move_in_d(parsed["return_distribution"]["probs"]) if "return_distribution" in parsed else None
    d = Decision(
        "flat", "", ev_long_r=round(ev_l, 4), ev_short_r=round(ev_s, 4),
        p_long_target=lb["target_first"], p_long_stop=lb["stop_first"],
        p_short_target=sb["target_first"], p_short_stop=sb["stop_first"],
        expected_move_in_d=round(em, 4) if em is not None else None,
        setup_quality=parsed["setup_quality"]["score"], data_sufficient=parsed["data_sufficient"]["p"],
        coherence=round(coherence, 4),
    )

    side, ev = ("long", ev_l) if ev_l >= ev_s else ("short", ev_s)
    lean = abs(ev_l - ev_s)
    # hard gates: safety / feasibility, never bypassed
    hard: list[str] = []
    if side == "short" and not s.allow_short:
        hard.append("shorts disabled")
    if has_position:
        hard.append("already in a position on this symbol")
    div = meta.get("venue_divergence_bp")
    if div is not None and abs(div) > s.max_venue_divergence_bp:
        hard.append(f"testnet/mainnet divergence {div:+.0f}bp > {s.max_venue_divergence_bp}bp")
    # quality gates: the strict policy's judgment of whether the edge is real
    soft: list[str] = []
    if ev < s.min_edge_r:
        soft.append(f"best EV {ev:+.3f}R < min edge {s.min_edge_r}R")
    if d.setup_quality < s.min_setup_quality:
        soft.append(f"setup_quality {d.setup_quality:.2f} < {s.min_setup_quality}")
    if d.data_sufficient < 0.5:
        soft.append(f"data_sufficient p={d.data_sufficient:.2f} < 0.5")
    if em is not None and ((side == "long" and em < 0) or (side == "short" and em > 0)):
        soft.append(f"return distribution mean ({em:+.2f}d) disagrees with {side} bracket")
    d.gates = hard + soft
    p_t = lb["target_first"] if side == "long" else sb["target_first"]

    if not d.gates:
        d.action = d.strict_action = side
        d.reason = f"{side} EV {ev:+.3f}R (P target {p_t:.2f}, RR {rr:.2f}, cost {cost_r:.2f}R)"
        return d
    if s.exploration_mode and not hard and lean >= s.explore_min_lean_r:
        # Exploration: follow Kairos's preferred side even though the strict gates say the edge
        # is not proven, at reduced risk, so its directional lean gets tested with real fills.
        d.action, d.exploration = side, True
        d.reason = (f"EXPLORE {side}: Kairos prefers {side} by {lean:.3f}R (EV {ev:+.3f}R, P target {p_t:.2f}); "
                    f"strict gates would block: {'; '.join(soft)}")
        return d
    if s.exploration_mode and not hard:
        d.gates.append(f"explore: lean {lean:.3f}R < {s.explore_min_lean_r}R")
    d.reason = "; ".join(d.gates)
    return d


def size_position(equity: float, sl_bp: float, price: float, s: Settings, risk_pct: float | None = None) -> tuple[float, float]:
    """Risk-based sizing. Returns (qty_base, notional_usdt)."""
    risk_usd = equity * (s.risk_per_trade_pct if risk_pct is None else risk_pct) / 100
    notional = risk_usd / (sl_bp / 10000)
    notional = min(notional, s.max_notional_usdt, equity * s.max_leverage * 0.9)
    return notional / price, notional
