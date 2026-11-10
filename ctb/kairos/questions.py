"""Versioned Kairos question set for perpetual trading decisions.

Principles (from the Kairos contract and from what went wrong with the Jev schema):
- Max 8 questions per request (Kairos V4). Jev's schema sent 15.
- Ask OBSERVABLE, LABELLABLE questions ("which is hit first, +x% or -y%, within N minutes"),
  not "what should I do". Code turns probabilities into decisions, and every answer can be
  scored against what actually happened afterwards, whether or not we traded.
- Each question reads the state independently; none relies on another answer.
- An explicit applicability question (data_sufficient) is the no-match path.

Bump QUESTION_SET_VERSION on ANY wording change so evaluation never mixes versions.
"""

from __future__ import annotations

from typing import Any

QUESTION_SET_VERSION = "perp-v1.0"

BRACKET_IDS = ["target_first", "stop_first", "neither"]
RETURN_BIN_IDS = [f"bin_{i}" for i in range(7)]
REGIME_IDS = ["uptrend", "downtrend", "range", "volatile_chop"]
CROWDING_IDS = ["longs_crowded", "shorts_crowded", "balanced"]
SETUP_LEVELS = 5

# Return bins in multiples of the stop distance d: (-inf,-2d) [-2d,-d) [-d,-d/3) [-d/3,d/3) [d/3,d) [d,2d) [2d,inf)
RETURN_BIN_EDGES_IN_D = [-2.0, -1.0, -1 / 3, 1 / 3, 1.0, 2.0]


def _fmt(x: float) -> str:
    return f"{x:.3f}".rstrip("0").rstrip(".")


def return_bin_edges_pct(sl_bp: float) -> list[float]:
    d = sl_bp / 100
    return [e * d for e in RETURN_BIN_EDGES_IN_D]


def build_questions(sl_bp: float, tp_bp: float, horizon_min: int) -> dict[str, Any]:
    sl, tp = _fmt(sl_bp / 100), _fmt(tp_bp / 100)
    edges = return_bin_edges_pct(sl_bp)
    lo = ["-infinity"] + [_fmt(e) for e in edges]
    hi = [_fmt(e) for e in edges] + ["+infinity"]
    return_criteria = {
        RETURN_BIN_IDS[i]: f"Price change from last_price to the price {horizon_min} minutes later is in [{lo[i]}%, {hi[i]}%)."
        for i in range(7)
    }
    return {
        "long_bracket": {
            "type": "choice",
            "instructions": (
                f"A LONG is opened at last_price now, with a stop {sl}% below entry and a target {tp}% above entry "
                f"(see trade_plan). Using all the evidence in the state, which is touched first within the next {horizon_min} minutes?"
            ),
            "criteria": {
                "target_first": f"Price rises {tp}% above entry before it falls {sl}% below entry, within {horizon_min} minutes.",
                "stop_first": f"Price falls {sl}% below entry before it rises {tp}% above entry, within {horizon_min} minutes.",
                "neither": f"Price stays between -{sl}% and +{tp}% of entry for the full {horizon_min} minutes.",
            },
        },
        "short_bracket": {
            "type": "choice",
            "instructions": (
                f"A SHORT is opened at last_price now, with a stop {sl}% above entry and a target {tp}% below entry "
                f"(see trade_plan). Using all the evidence in the state, which is touched first within the next {horizon_min} minutes?"
            ),
            "criteria": {
                "target_first": f"Price falls {tp}% below entry before it rises {sl}% above entry, within {horizon_min} minutes.",
                "stop_first": f"Price rises {sl}% above entry before it falls {tp}% below entry, within {horizon_min} minutes.",
                "neither": f"Price stays between -{tp}% and +{sl}% of entry for the full {horizon_min} minutes.",
            },
        },
        "return_distribution": {
            "type": "choice",
            "instructions": (
                f"Forecast the price change of the instrument over the next {horizon_min} minutes, measured from last_price. "
                "Probabilities across the intervals represent a predictive distribution, not a trade choice."
            ),
            "criteria": return_criteria,
        },
        "regime": {
            "type": "choice",
            "instructions": f"Which description best fits how this market is likely to behave over the next {horizon_min} minutes?",
            "criteria": {
                "uptrend": "Persistent upward drift with shallow pullbacks; higher highs and higher lows.",
                "downtrend": "Persistent downward drift with shallow bounces; lower highs and lower lows.",
                "range": "Oscillation inside a defined range with no sustained direction and contained volatility.",
                "volatile_chop": "Large swings in both directions without a sustained trend; stops on both sides are at risk.",
            },
        },
        "setup_quality": {
            "type": "score",
            "instructions": (
                "Independent of direction, how clear and tradeable is the current setup for a position held up to "
                f"{horizon_min} minutes, given the trend structure across timeframes, order flow, positioning, and costs in trade_plan?"
            ),
            "criteria": [
                "No edge: evidence is contradictory or the move is noise relative to costs.",
                "Weak: a slight lean that most traders would not act on.",
                "Moderate: a clear lean with some conflicting signals.",
                "Strong: most timeframes, flow and positioning agree on a direction.",
                "Exceptional: near-unanimous agreement across trend, flow, positioning and context.",
            ],
        },
        "crowding": {
            "type": "choice",
            "instructions": (
                "Using funding, open interest change, accounts_long_share, liquidations and mark-index basis in derivatives, "
                "is one side of the perpetual market crowded?"
            ),
            "criteria": {
                "longs_crowded": "Longs are crowded: positive or rising funding, rising open interest into strength, high long share; vulnerable to a long squeeze.",
                "shorts_crowded": "Shorts are crowded: negative or falling funding, rising open interest into weakness, low long share; vulnerable to a short squeeze.",
                "balanced": "Neither side is clearly crowded.",
            },
        },
        "mean_reversion": {
            "type": "noul",
            "instructions": (
                "Is price stretched far enough from its short-term averages that, within the next "
                f"{horizon_min} minutes, a move back toward the 1h EMA20 is more likely than continuation away from it?"
            ),
            "criteria": {
                "true": "Price is stretched and a reversion toward the 1h EMA20 is more likely than continuation.",
                "false": "Continuation away from the 1h EMA20, or no meaningful stretch.",
            },
        },
        "data_sufficient": {
            "type": "noul",
            "instructions": (
                "Does the state contain enough consistent, non-missing evidence (trend, flow, derivatives, order book) "
                "to make a directional judgment about the next "
                f"{horizon_min} minutes?"
            ),
            "criteria": {
                "true": "Enough consistent evidence is present to judge direction.",
                "false": "Key evidence is missing, stale or contradictory, so any directional call would be a guess.",
            },
        },
    }


EXPECTED_TYPES = {
    "long_bracket": ("choice", BRACKET_IDS),
    "short_bracket": ("choice", BRACKET_IDS),
    "return_distribution": ("choice", RETURN_BIN_IDS),
    "regime": ("choice", REGIME_IDS),
    "setup_quality": ("score", SETUP_LEVELS),
    "crowding": ("choice", CROWDING_IDS),
    "mean_reversion": ("noul", None),
    "data_sufficient": ("noul", None),
}
