"""Policy: turn Kairos probabilities into a trade decision. All arithmetic, thresholds and
risk rules live here in code, never in the model.

Expected value of a bracket, in R (1R = the stop distance):
    EV = P(target first) * RR  -  P(stop first) * 1  +  P(neither) * E[neither]  -  cost / stop
E[neither] is taken as 0 (the time-stop exits near entry on average; measured later in eval).

v2: EV is computed on CALIBRATED probabilities (calibration.blend: Kairos blended with the
instrument's measured base rates, weight fitted on labelled outcomes). The raw-Kairos EV and the
base-rate-only EV are recorded next to it so the evaluation can tell whether Kairos added anything.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .calibration import PRIOR_W, blend, blend_p
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
    # v2: calibrated view (what the strict policy acts on) and the two views it is blended from
    ev_long_raw_r: float | None = None
    ev_short_raw_r: float | None = None
    ev_long_base_r: float | None = None
    ev_short_base_r: float | None = None
    p_up: float | None = None  # calibrated P(price higher at horizon)
    p_up_raw: float | None = None
    p_big_move: float | None = None
    weights: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def expected_move_in_d(probs: dict[str, float]) -> float:
    """Mean of the return distribution, in multiples of the stop distance d."""
    e = RETURN_BIN_EDGES_IN_D
    mids = [e[0] * 1.25, (e[0] + e[1]) / 2, (e[1] + e[2]) / 2, 0.0, (e[3] + e[4]) / 2, (e[4] + e[5]) / 2, e[5] * 1.25]
    return sum(mids[i] * probs[f"bin_{i}"] for i in range(7))


def bracket_ev(p_target: float, p_stop: float, rr: float, cost_r: float) -> float:
    return p_target * rr - p_stop * 1.0 - cost_r


def decide(parsed: dict[str, Any], meta: dict[str, Any], s: Settings, *, has_position: bool,
           weights: dict[str, float] | None = None) -> Decision:
    required = ("long_bracket", "short_bracket", "setup_quality")
    missing = [q for q in required if q not in parsed]
    if missing:
        return Decision("flat", f"invalid/missing answers: {', '.join(missing)} (treated as error, not as a 'no')")

    rr = meta["tp_bp"] / meta["sl_bp"]
    cost_r = meta["cost_bp"] / meta["sl_bp"]
    w = {q: PRIOR_W for q in ("long_bracket", "short_bracket", "return_distribution", "direction", "big_move")} | (weights or {})
    base = meta.get("base_rates")
    lb_raw, sb_raw = parsed["long_bracket"]["probs"], parsed["short_bracket"]["probs"]
    ev_l_raw = bracket_ev(lb_raw["target_first"], lb_raw["stop_first"], rr, cost_r)
    ev_s_raw = bracket_ev(sb_raw["target_first"], sb_raw["stop_first"], rr, cost_r)
    if base:
        lb, sb = blend(lb_raw, base["long_bracket"], w["long_bracket"]), blend(sb_raw, base["short_bracket"], w["short_bracket"])
    else:
        lb, sb = lb_raw, sb_raw
    ev_l = bracket_ev(lb["target_first"], lb["stop_first"], rr, cost_r)
    ev_s = bracket_ev(sb["target_first"], sb["stop_first"], rr, cost_r)
    # Coherence: a long's target and a short's stop are both "price goes up". For symmetric-ish
    # brackets these should roughly agree. Large disagreement = the model is not self-consistent.
    coherence = 1 - abs(lb["target_first"] - sb["stop_first"]) if abs(rr - 1) < 1e-9 else 1 - abs(
        (lb["target_first"] - lb["stop_first"]) + (sb["target_first"] - sb["stop_first"])) / 2
    rd = parsed.get("return_distribution", {}).get("probs")
    if rd and base:
        rd = blend(rd, base["return_distribution"], w["return_distribution"])
    em = expected_move_in_d(rd) if rd else None
    p_up_raw = parsed["direction"]["p"] if "direction" in parsed else None
    p_up = blend_p(p_up_raw, base["price_higher_at_horizon"], w["direction"]) if p_up_raw is not None and base else p_up_raw
    p_mv = parsed["big_move"]["p"] if "big_move" in parsed else None
    if p_mv is not None and base:
        p_mv = blend_p(p_mv, base["moved_at_least_one_stop_either_way"], w["big_move"])
    d = Decision(
        "flat", "", ev_long_r=round(ev_l, 4), ev_short_r=round(ev_s, 4),
        p_long_target=round(lb["target_first"], 4), p_long_stop=round(lb["stop_first"], 4),
        p_short_target=round(sb["target_first"], 4), p_short_stop=round(sb["stop_first"], 4),
        expected_move_in_d=round(em, 4) if em is not None else None,
        setup_quality=parsed["setup_quality"]["score"],
        data_sufficient=parsed["data_sufficient"]["p"] if "data_sufficient" in parsed else None,
        coherence=round(coherence, 4),
        ev_long_raw_r=round(ev_l_raw, 4), ev_short_raw_r=round(ev_s_raw, 4),
        p_up=round(p_up, 4) if p_up is not None else None, p_up_raw=p_up_raw,
        p_big_move=round(p_mv, 4) if p_mv is not None else None, weights=w if base else {},
    )
    if base:
        d.ev_long_base_r = round(bracket_ev(base["long_bracket"]["target_first"], base["long_bracket"]["stop_first"], rr, cost_r), 4)
        d.ev_short_base_r = round(bracket_ev(base["short_bracket"]["target_first"], base["short_bracket"]["stop_first"], rr, cost_r), 4)

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
    if not base:
        soft.append("no base rates for this bracket yet (not enough history): probabilities cannot be calibrated")
    if d.p_up is not None:
        need = 0.5 + s.min_direction_margin
        if side == "long" and d.p_up < need:
            soft.append(f"direction P(up) {d.p_up:.2f} < {need:.2f} for a long")
        if side == "short" and d.p_up > 1 - need:
            soft.append(f"direction P(up) {d.p_up:.2f} > {1 - need:.2f} for a short")
    if em is not None and ((side == "long" and em < 0) or (side == "short" and em > 0)):
        soft.append(f"return distribution mean ({em:+.2f}d) disagrees with {side} bracket")
    d.gates = hard + soft
    p_t = lb["target_first"] if side == "long" else sb["target_first"]

    if not d.gates:
        d.action = d.strict_action = side
        d.reason = (f"{side} EV {ev:+.3f}R calibrated (raw {ev_l_raw if side == 'long' else ev_s_raw:+.3f}R, "
                    f"P target {p_t:.2f}, RR {rr:.2f}, cost {cost_r:.2f}R)")
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
