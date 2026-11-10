"""Kairos `news_return` profile: 7-bin gross-return distribution for a headline.

Template vendored from DMindAI/kairos-skill v0.1.5 (MIT), commit 830ad0f, assets/news-return.json.
Rules followed exactly: question id/instructions and candidate ids are preserved; only the
interval descriptions change, derived from round_trip_cost_bp as [-4c,-2c,-c,c,2c,4c].
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

TEMPLATE = json.loads((Path(__file__).parent / "assets" / "news-return.json").read_text(encoding="utf-8"))
CANDIDATES: list[str] = TEMPLATE["candidate_order"]
HORIZONS = set(TEMPLATE["horizons"])


def _fmt(x: float) -> str:
    if float(x).is_integer():
        return str(int(x))
    return f"{x:.6f}".rstrip("0").rstrip(".")


def boundaries(cost_bp: float) -> list[float]:
    c = cost_bp
    return [-4 * c, -2 * c, -c, c, 2 * c, 4 * c]


def build_request(*, news_text: str, target_asset: str, source_name: str, published_at_utc: str, horizon: str,
                  round_trip_cost_bp: float, event_type: str = "crypto news headline") -> dict[str, Any]:
    if horizon not in HORIZONS:
        raise ValueError(f"news_return horizon must be one of {sorted(HORIZONS)}")
    if not (isinstance(round_trip_cost_bp, (int, float)) and math.isfinite(round_trip_cost_bp) and round_trip_cost_bp > 0):
        raise ValueError("round_trip_cost_bp must be a positive finite number")
    body = copy.deepcopy(TEMPLATE["example"])
    body["state"] = {
        "news_text": news_text,
        "target_asset": target_asset,
        "market": "Bybit USDT perpetual swaps (24/7 crypto market)",
        "asset_class": "crypto perpetual swap",
        "event_type": event_type,
        "source_name": source_name,
        "published_at_utc": published_at_utc,
        "horizon": horizon,
        "entry_rule": "First 1-minute bar open at or after first_seen time + 60 seconds; crypto trades continuously so no session rule applies.",
        "round_trip_cost_bp": float(round_trip_cost_bp),
        "availability_basis": "Live RSS poll; first_seen is when this bot fetched the headline, which may lag publication.",
        "return_objective": "Unadjusted target-asset open-to-open gross return in basis points over the supplied holding period; round-trip cost is stated separately.",
    }
    b = boundaries(round_trip_cost_bp)
    lo = ["-infinity"] + [_fmt(x) for x in b]
    hi = [_fmt(x) for x in b] + ["+infinity"]
    crit = body["questions"]["return_distribution"]["criteria"]
    for i, cid in enumerate(CANDIDATES):
        crit[cid] = f"Gross asset return in [{lo[i]}, {hi[i]}) bp."
    return body


def bin_midpoints_bp(cost_bp: float) -> list[float]:
    """Representative value per bin, for an expected-return summary (code-side choice, documented)."""
    b = boundaries(cost_bp)
    return [b[0] * 1.5, (b[0] + b[1]) / 2, (b[1] + b[2]) / 2, 0.0, (b[3] + b[4]) / 2, (b[4] + b[5]) / 2, b[5] * 1.5]


def bin_index(return_bp: float, cost_bp: float) -> int:
    b = boundaries(cost_bp)
    for i, edge in enumerate(b):
        if return_bp < edge:
            return i
    return 6


def validate(answers: dict[str, Any]) -> tuple[list[float] | None, list[str]]:
    issues: list[str] = []
    a = answers.get("return_distribution")
    if not isinstance(a, dict) or not isinstance(a.get("probabilities"), dict):
        return None, ["return_distribution: missing probabilities"]
    probs = a["probabilities"]
    missing = [c for c in CANDIDATES if c not in probs]
    if missing:
        return None, [f"return_distribution: missing candidates {missing}"]
    vals = []
    for c in CANDIDATES:
        v = probs[c]
        if not (isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 1):
            return None, [f"return_distribution: bad probability for {c}: {v!r}"]
        vals.append(float(v))
    if abs(sum(vals) - 1) > 0.02:
        issues.append(f"return_distribution: probabilities sum to {sum(vals):.4f}")
        return None, issues
    return vals, issues
