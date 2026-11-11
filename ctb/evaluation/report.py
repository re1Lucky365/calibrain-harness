"""Generate a Markdown evaluation report for Minara from live metrics.

Observations are rule-based and conservative: each one states the measurement behind it
and the sample size. Below MIN_N samples a finding is reported as 'insufficient data'.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from ..kairos.questions import QUESTION_SET_VERSION

MIN_N = 100


def _f(x: Any, nd: int = 3) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def observations(m: dict[str, Any]) -> list[str]:
    obs: list[str] = []
    ops, mq, edge, cons = m["ops"], m["model_quality"], m["edge"], m["consistency"]
    if ops["requests"]:
        obs.append(f"Reliability: {ops['ok']}/{ops['requests']} requests succeeded (error rate {_f(ops['error_rate'])}); errors by kind: {ops['errors'] or 'none'}.")
    if ops.get("responses_with_issues"):
        obs.append(f"Contract: {ops['responses_with_issues']} successful responses violated the documented answer contract: {ops['schema_issues']}.")
    if ops.get("latency_ms_p50"):
        obs.append(f"Latency: p50 {ops['latency_ms_p50']:.0f} ms, p90 {_f(ops['latency_ms_p90'], 0)} ms, p99 {_f(ops['latency_ms_p99'], 0)} ms for 8 questions. "
                   "For bar-close decisions on 5m bars this matters only above ~30 s.")
    for qid in ("long_bracket", "short_bracket"):
        b = mq.get(qid, {})
        n = b.get("n", 0)
        if n < MIN_N:
            obs.append(f"{qid}: insufficient data ({n} labelled < {MIN_N}).")
            continue
        sk = b.get("skill_vs_climatology")
        verdict = "adds information beyond base rates" if sk and sk > 0.01 else "does NOT beat simply predicting base rates"
        obs.append(f"{qid}: Brier {_f(b['brier'])} vs climatology {_f(b['brier_climatology'])} (skill {_f(sk)}), AUC(target_first) {_f(b.get('auc_target_first'))}, "
                   f"ECE {_f(b.get('calibration_target_first', {}).get('ece'))}, n={n}: {verdict}.")
        if b.get("prob_spread_std") is not None and b["prob_spread_std"] < 0.03:
            obs.append(f"{qid}: probabilities barely move between very different market states (std of P(target_first) = {_f(b['prob_spread_std'])}); "
                       "the model may be falling back to a prior instead of reading the state.")
    rd = mq.get("return_distribution", {})
    if rd.get("n", 0) >= MIN_N:
        obs.append(f"return_distribution: RPS {_f(rd['rps'])} vs climatology {_f(rd['rps_climatology'])} (skill {_f(rd['skill_vs_climatology'])}); "
                   f"Spearman(expected move, realised) {_f(rd.get('spearman_expected_vs_realised'))}; PIT quintiles {rd.get('pit_histogram_quintiles')} "
                   "(flat = calibrated; U-shape = overconfident; hump = underconfident).")
    if edge.get("n", 0) >= MIN_N:
        obs.append(f"Edge: Spearman(predicted EV, realised R) = {_f(edge.get('spearman_ev_vs_realised_r'))} over {edge['n']} side-decisions. "
                   f"Policy signals: n={edge.get('policy_signals_n')}, mean {_f(edge.get('policy_signals_mean_r'))}R, win rate {_f(edge.get('policy_signals_win_rate'))}. "
                   f"Baselines: always-long {_f(edge.get('baseline_always_long_r'))}R, always-short {_f(edge.get('baseline_always_short_r'))}R.")
    if edge.get("exploration_signals_n"):
        obs.append(f"Exploration (Kairos's preferred side traded despite strict gates): n={edge['exploration_signals_n']}, "
                   f"mean {_f(edge.get('exploration_signals_mean_r'))}R, win rate {_f(edge.get('exploration_signals_win_rate'))}; "
                   f"strict signals: n={edge.get('strict_signals_n')}, mean {_f(edge.get('strict_signals_mean_r'))}R.")
    if cons.get("repeat_probes"):
        obs.append(f"Determinism: identical requests gave identical answers in {_f(cons.get('repeat_identical_share'))} of {cons['repeat_probes']} repeats; "
                   f"mean max abs probability difference {_f(cons.get('repeat_max_abs_prob_diff_mean'))}.")
    if cons.get("mirror_probes"):
        obs.append(f"Symmetry: on {cons['mirror_probes']} mirrored (bullish<->bearish) states, mean |P_long_target(orig) - P_short_target(mirror)| = "
                   f"{_f(cons.get('mirror_abs_error_mean'))}; the long/short lean flipped correctly in {_f(cons.get('mirror_lean_flips_sign_share'))} of cases. "
                   "A large error means a directional bias that is independent of the evidence.")
    if cons.get("both_targets_above_50pct"):
        obs.append(f"Self-contradiction: in {cons['both_targets_above_50pct']} decisions the model gave >50% to BOTH the long target and the short target being hit first.")
    for q, r in (m.get("vs_base_rates") or {}).items():
        if r.get("n", 0) >= 30:
            obs.append(f"{q} vs the base rates Kairos was given (n={r['n']}): Brier Kairos {r['brier_kairos']}, base rates {r['brier_base_rates']}, "
                       f"skill {r['kairos_skill_vs_base']:+.3f}; best blend weight on Kairos {r['best_w']}. "
                       + ("Kairos adds information beyond the base rates." if (r.get("kairos_skill_vs_base") or 0) > 0 else
                          "Kairos does not beat the base rates it was shown."))
    for q in ("direction", "big_move"):
        b = (m.get("model_quality") or {}).get(q) or {}
        if b.get("n", 0) >= 30:
            obs.append(f"{b['name']}: Brier skill vs climatology {_f(b.get('skill_vs_climatology'))}, AUC {_f(b.get('auc'))}, n={b['n']}.")
    tr = m.get("trading", {})
    if tr.get("closed"):
        obs.append(f"Testnet trading (from Bybit's own closed-P&L records): {tr['closed']} closed, net {tr['net_pnl_usdt']:+.2f} USDT "
                   f"after {tr['fees_usdt']:.2f} USDT fees, win rate {_f(tr.get('win_rate'))}, mean {_f(tr.get('avg_r'))}R.")
        ex = tr.get("execution") or {}
        es, ss = ex.get("entry_slip_bp"), ex.get("stop_exit_slip_bp")
        if es:
            obs.append(f"Execution: entry slippage vs best testnet price mean {es['mean']}bp (p90 {es['p90']}bp, n={es['n']}); "
                       + (f"stop exits filled {ss['mean']}bp past the trigger on average (worst {ss['max']}bp); " if ss else "")
                       + f"mean fill ratio {_f(ex.get('fill_ratio_mean'))}; {ex.get('multi_fill_exits', 0)} exits filled in several pieces.")
    nq = m.get("news", {}).get("quality")
    if nq and nq.get("n", 0) >= 30:
        obs.append(f"news_return: RPS skill vs climatology {_f(nq.get('skill_vs_climatology'))}, Spearman(lean, realised) {_f(nq.get('spearman_lean_vs_return'))}, n={nq['n']}.")
    return obs


def render(m: dict[str, Any], settings_view: dict[str, Any]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Model evaluation report: perpetual futures trading",
        "",
        f"Generated {now}. Question set `{QUESTION_SET_VERSION}`. Active model `{settings_view.get('model_label', 'Kairos')}` (`{settings_view.get('model_id', settings_view.get('kairos_model'))}`). "
        f"Metrics cover {'model ' + m['generated_from']['model'] if m.get('generated_from', {}).get('model') else 'every model'}.",
        "",
        "## Setup",
        f"- Instruments: {', '.join(settings_view['symbols'])} (Bybit USDT perpetuals). Signals from mainnet public data; orders on Bybit testnet.",
        f"- One request per closed {settings_view['decision_tf_minutes']}m bar per symbol, 8 questions per request (Kairos V4 limit).",
        f"- Bracket: stop = max({settings_view['sl_atr']} x ATR({settings_view['atr_tf']}m), {settings_view['min_sl_bp']}bp), target = stop x "
        f"{settings_view['tp_atr'] / settings_view['sl_atr']:.2f}, horizon {settings_view['horizon_minutes']} min, round-trip cost {settings_view['round_trip_cost_bp']}bp.",
        "- Every prediction is labelled from realised mainnet 1m bars whether or not it was traded, so model skill is measured separately from execution.",
        "",
        "## Findings",
        "",
        *[f"- {o}" for o in observations(m)],
        "",
        "## Raw metrics",
        "",
        "```json",
        json.dumps(m, indent=2, default=str),
        "```",
    ]
    return "\n".join(lines)
