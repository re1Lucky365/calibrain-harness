"""Online calibration: blend Kairos's probabilities with the instrument's own base rates.

    p_used = w * p_kairos + (1 - w) * p_base            (per question, 0 <= w <= 1)

p_base is the empirical frequency of each outcome for the exact bracket and horizon, measured on
the instrument's recent history at decision time (features.base_rates), so it is known before the
outcome and involves no look-ahead. w is chosen on labelled live decisions to minimise the Brier
score. If Kairos adds no information beyond the base rates, the fitted w goes to 0 and the policy
stops trading on its answers; if it does, w rises and its view drives decisions.

Until a question has MIN_FIT labelled decisions, w = PRIOR_W: the model is trusted halfway, which
also caps how far a single over-confident answer can move expected value.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .db import DB
from .kairos.questions import BRACKET_IDS, RETURN_BIN_IDS

MIN_FIT = 100
PRIOR_W = 0.5
GRID = [i / 20 for i in range(21)]
CALIBRATED = ("long_bracket", "short_bracket", "return_distribution", "direction", "big_move")


def blend(p: dict[str, float], base: dict[str, float], w: float) -> dict[str, float]:
    out = {k: w * p[k] + (1 - w) * base[k] for k in p}
    s = sum(out.values())
    return {k: v / s for k, v in out.items()}


def blend_p(p: float, base: float, w: float) -> float:
    return w * p + (1 - w) * base


def _brier(vec: list[float], k: int) -> float:
    return sum((x - (1.0 if i == k else 0.0)) ** 2 for i, x in enumerate(vec))


def rows_for(question: str, decisions: list[dict[str, Any]]) -> list[tuple[list[float], list[float], int]]:
    """(kairos vector, base vector, realised index) for every labelled decision that has both."""
    out = []
    for d in decisions:
        parsed, lab = d.get("parsed_json") or {}, d.get("label_json") or {}
        base = ((d.get("decision_json") or {}).get("meta") or {}).get("base_rates")
        if question not in parsed or not base or not lab or lab.get("unlabelable"):
            continue
        if question in ("long_bracket", "short_bracket"):
            side = question.split("_")[0]
            ids = BRACKET_IDS
            out.append(([parsed[question]["probs"][i] for i in ids], [base[question][i] for i in ids], ids.index(lab[side]["outcome"])))
        elif question == "return_distribution":
            out.append(([parsed[question]["probs"][i] for i in RETURN_BIN_IDS], [base[question][i] for i in RETURN_BIN_IDS], lab["return_bin"]))
        elif question == "direction":
            p, b = parsed[question]["p"], base["price_higher_at_horizon"]
            out.append(([p, 1 - p], [b, 1 - b], 0 if lab["return_pct"] > 0 else 1))
        elif question == "big_move":
            p, b = parsed[question]["p"], base["moved_at_least_one_stop_either_way"]
            d_pct = d["sl_bp"] / 100
            moved = lab["max_up_pct"] >= d_pct or -lab["max_down_pct"] >= d_pct
            out.append(([p, 1 - p], [b, 1 - b], 0 if moved else 1))
    return out


def fit(rows: list[tuple[list[float], list[float], int]]) -> dict[str, Any]:
    """Grid-search w on the Brier score. Reports Kairos alone, base rates alone and the blend."""
    n = len(rows)
    if not n:
        return {"n": 0, "w": PRIOR_W, "fitted": False}
    def score(w: float) -> float:
        return sum(_brier([w * a + (1 - w) * b for a, b in zip(k, bs)], y) for k, bs, y in rows) / n
    best = min(GRID, key=score)
    res = {"n": n, "brier_kairos": round(score(1.0), 4), "brier_base_rates": round(score(0.0), 4),
           "brier_best_blend": round(score(best), 4), "best_w": best}
    res["kairos_skill_vs_base"] = round(1 - res["brier_kairos"] / res["brier_base_rates"], 4) if res["brier_base_rates"] else None
    res["fitted"] = n >= MIN_FIT
    res["w"] = best if res["fitted"] else PRIOR_W
    return res


class Calibrator:
    """Weights are fitted per trading timeframe: a 1m bar with a 30 min horizon is a different
    question from a 5m bar with a 2 h horizon, so one timeframe's weights never drive another's."""

    def __init__(self, db: DB, qset_prefix: str = "perp-v2", tf_fn: Callable[[], str] | None = None,
                 provider_fn: Callable[[], str] | None = None):
        self.db = db
        self.qset_prefix = qset_prefix
        self.tf_fn = tf_fn
        self.provider_fn = provider_fn  # weights are per model too: Jev never inherits Kairos's
        self.tf: str | None = None
        self.provider: str | None = None
        self.report: dict[str, Any] = {}
        self.weights: dict[str, float] = {q: PRIOR_W for q in CALIBRATED}
        self.fitted_at = 0.0

    def refit(self) -> dict[str, float]:
        self.tf = self.tf_fn() if self.tf_fn else None
        self.provider = self.provider_fn() if self.provider_fn else None
        sql = ("SELECT parsed_json, label_json, decision_json, sl_bp FROM decisions WHERE probe IS NULL AND error_kind IS NULL "
               "AND label_json IS NOT NULL AND qset_version LIKE ?")
        params: list[Any] = [self.qset_prefix + "%"]
        if self.tf:
            sql += " AND decision_tf=?"
            params.append(self.tf)
        if self.provider:
            sql += " AND model_provider=?"
            params.append(self.provider)
        decs = self.db.query(sql, params)
        self.report = {q: fit(rows_for(q, decs)) for q in CALIBRATED}
        self.weights = {q: r["w"] for q, r in self.report.items()}
        self.fitted_at = time.time()
        return self.weights

    def maybe_refit(self, every_s: float = 900) -> dict[str, float]:
        tf_changed = self.tf_fn is not None and self.tf_fn() != self.tf
        model_changed = self.provider_fn is not None and self.provider_fn() != self.provider
        if tf_changed or model_changed or time.time() - self.fitted_at > every_s:
            self.refit()
        return self.weights
