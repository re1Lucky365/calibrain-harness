"""Strict validation of Kairos answers. Anything off-contract is recorded as a schema error,
never silently repaired, so the evaluation can report it back to Minara."""

from __future__ import annotations

import math
from typing import Any

from .questions import EXPECTED_TYPES

PROB_SUM_TOL = 0.02


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def validate(answers: dict[str, Any], questions: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return (parsed, issues). parsed[qid] is normalised; qids with fatal issues are omitted."""
    parsed: dict[str, Any] = {}
    issues: list[str] = []
    for qid in questions:
        a = answers.get(qid)
        if not isinstance(a, dict):
            issues.append(f"{qid}: missing answer")
            continue
        exp_type, exp_opts = EXPECTED_TYPES.get(qid, (questions[qid]["type"], None))
        if a.get("type") not in (None, exp_type):
            issues.append(f"{qid}: type {a.get('type')!r} != {exp_type!r}")
        if exp_type == "noul":
            p = a.get("noul")
            if not _finite(p) or not 0 <= p <= 1:
                issues.append(f"{qid}: noul not a probability: {p!r}")
                continue
            parsed[qid] = {"p": float(p)}
        elif exp_type == "choice":
            opts = exp_opts or list(questions[qid]["criteria"])
            probs = a.get("probabilities")
            if not isinstance(probs, dict):
                issues.append(f"{qid}: probabilities missing")
                continue
            extra = set(probs) - set(opts)
            missing = [o for o in opts if o not in probs]
            if extra:
                issues.append(f"{qid}: unknown options {sorted(extra)}")
            if missing:
                issues.append(f"{qid}: missing options {missing}")
                continue
            if not all(_finite(probs[o]) and 0 <= probs[o] <= 1 for o in opts):
                issues.append(f"{qid}: non-finite or out-of-range probability")
                continue
            total = sum(probs[o] for o in opts)
            if abs(total - 1) > PROB_SUM_TOL:
                issues.append(f"{qid}: probabilities sum to {total:.4f}")
                continue
            choice = a.get("choice")
            if choice not in opts:
                issues.append(f"{qid}: choice {choice!r} not an option")
            argmax = max(opts, key=lambda o: probs[o])  # ties -> first input candidate, per contract
            if choice in opts and probs[choice] + 1e-9 < probs[argmax]:
                issues.append(f"{qid}: choice {choice!r} is not the argmax ({argmax!r})")
            conf = a.get("confidence")
            parsed[qid] = {"choice": choice if choice in opts else argmax, "probs": {o: float(probs[o]) for o in opts},
                           "confidence": float(conf) if _finite(conf) else None}
        elif exp_type == "score":
            score = a.get("score")
            n = exp_opts or len(questions[qid]["criteria"])
            if not _finite(score) or not 0 <= score <= n - 1:
                issues.append(f"{qid}: score out of range: {score!r}")
                continue
            probs = a.get("probabilities")
            plist: list[float] | None = None
            if isinstance(probs, list) and len(probs) == n and all(_finite(x) for x in probs):
                plist = [float(x) for x in probs]
            elif isinstance(probs, dict):
                try:
                    plist = [float(probs[k]) for k in sorted(probs, key=lambda k: int(k))]
                except (ValueError, TypeError):
                    plist = None
            if plist is not None:
                exp_mean = sum(i * p for i, p in enumerate(plist))
                if abs(exp_mean - score) > 0.05:
                    issues.append(f"{qid}: score {score:.3f} != prob-weighted mean {exp_mean:.3f}")
            conf = a.get("confidence")
            parsed[qid] = {"score": float(score), "probs": plist, "confidence": float(conf) if _finite(conf) else None}
    for qid in answers:
        if qid not in questions:
            issues.append(f"unexpected answer id {qid!r}")
    return parsed, issues
