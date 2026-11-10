import hashlib
import hmac
import math

import pytest

from ctb import indicators as ind
from ctb.config import Settings
from ctb.evaluation import metrics
from ctb.evaluation.labeler import bracket_outcome, label_decision, realized_r, return_bin
from ctb.features import mirror_state
from ctb.kairos import news
from ctb.kairos.parse import validate
from ctb.kairos.questions import build_questions
from ctb.policy import decide


def good_answers():
    return {
        "long_bracket": {"type": "choice", "choice": "target_first", "probabilities": {"target_first": 0.55, "stop_first": 0.30, "neither": 0.15}, "confidence": 0.4},
        "short_bracket": {"type": "choice", "choice": "stop_first", "probabilities": {"target_first": 0.25, "stop_first": 0.6, "neither": 0.15}, "confidence": 0.4},
        "return_distribution": {"type": "choice", "choice": "bin_4", "probabilities": {f"bin_{i}": p for i, p in enumerate([0.02, 0.05, 0.13, 0.2, 0.3, 0.2, 0.1])}, "confidence": 0.2},
        "regime": {"type": "choice", "choice": "uptrend", "probabilities": {"uptrend": 0.5, "downtrend": 0.1, "range": 0.3, "volatile_chop": 0.1}, "confidence": 0.3},
        "setup_quality": {"type": "score", "score": 2.5, "legend": [], "probabilities": [0.0, 0.1, 0.4, 0.4, 0.1], "confidence": 0.3},
        "crowding": {"type": "choice", "choice": "balanced", "probabilities": {"longs_crowded": 0.2, "shorts_crowded": 0.2, "balanced": 0.6}, "confidence": 0.3},
        "mean_reversion": {"type": "noul", "noul": 0.3},
        "data_sufficient": {"type": "noul", "noul": 0.8},
    }


Q = build_questions(60, 100, 120)
META = {"sl_bp": 60.0, "tp_bp": 100.0, "cost_bp": 15.0, "price": 100.0}


def test_questions_within_kairos_limits():
    assert 1 <= len(Q) <= 8
    for q in Q.values():
        if q["type"] == "choice":
            assert 2 <= len(q["criteria"]) <= 26
        if q["type"] == "score":
            assert 2 <= len(q["criteria"]) <= 10


def test_validate_accepts_contract_and_preserves_zero():
    parsed, issues = validate(good_answers(), Q)
    assert issues == []
    assert parsed["setup_quality"]["probs"][0] == 0.0
    assert set(parsed) == set(Q)


def test_validate_flags_bad_answers_without_repairing():
    a = good_answers()
    a["long_bracket"]["probabilities"]["neither"] = 0.6  # sum 1.45
    del a["data_sufficient"]
    a["mean_reversion"]["noul"] = float("nan")
    parsed, issues = validate(a, Q)
    assert "long_bracket" not in parsed and "data_sufficient" not in parsed and "mean_reversion" not in parsed
    assert any("sum" in i for i in issues) and any("missing answer" in i for i in issues)


def test_policy_ev_math_and_gates():
    parsed, _ = validate(good_answers(), Q)
    s = Settings()
    d = decide(parsed, META, s, has_position=False)
    rr, cost_r = 100 / 60, 15 / 60
    assert d.ev_long_r == pytest.approx(round(0.55 * rr - 0.30 - cost_r, 4))
    assert d.action == "long"
    d2 = decide(parsed, META, s, has_position=True)
    assert d2.action == "flat" and "already in a position" in d2.reason


def test_policy_missing_answer_is_error_not_flat_judgement():
    parsed, _ = validate(good_answers(), Q)
    del parsed["long_bracket"]
    d = decide(parsed, META, Settings(), has_position=False)
    assert d.action == "flat" and "treated as error" in d.reason


def test_bracket_labels_and_ambiguity():
    bars = [(0, 100, 100.2, 99.9, 100.1), (60000, 100.1, 101.1, 100.0, 101.0)]
    assert bracket_outcome(bars, 100, "long", 100, 60)["outcome"] == "target_first"
    assert bracket_outcome(bars, 100, "short", 100, 60)["outcome"] == "stop_first"
    wide = [(0, 100, 101.5, 99.0, 100)]
    o = bracket_outcome(wide, 100, "long", 100, 60)
    assert o["outcome"] == "stop_first" and o["ambiguous"]
    assert realized_r("target_first", 100 / 60, 0, 60, "long", 15) == pytest.approx(100 / 60 - 0.25)
    assert return_bin(0.0, 60) == 3 and return_bin(-5, 60) == 0 and return_bin(5, 60) == 6


def test_label_decision_no_lookahead():
    ts = 1_000_000.5  # mid-minute
    start = (int(ts * 1000) // 60000 + 1) * 60000
    bars = [(start - 60000, 100, 200, 1, 100)]  # decision minute: huge range must be ignored
    bars += [(start + i * 60000, 100, 100.05, 99.95, 100) for i in range(120)]
    d = {"ts": ts, "price": 100.0, "sl_bp": 60.0, "tp_bp": 100.0, "horizon_min": 120, "decision_json": {"cost_bp": 15}, "state_json": {}}
    lab = label_decision(d, bars)
    assert lab["long"]["outcome"] == "neither" and lab["short"]["outcome"] == "neither"


def test_news_boundaries_follow_template_rule():
    body = news.build_request(news_text="x", target_asset="BTC", source_name="s", published_at_utc="2026-11-10T00:00:00Z", horizon="15m", round_trip_cost_bp=15)
    crit = body["questions"]["return_distribution"]["criteria"]
    assert crit["return_bin_0"] == "Gross asset return in [-infinity, -60) bp."
    assert crit["return_bin_3"] == "Gross asset return in [-15, 15) bp."
    assert crit["return_bin_6"] == "Gross asset return in [60, +infinity) bp."
    assert body["task_profile"] == "news_return" and body["model"] == "DMIND/Kairos"
    assert len(body["state"]) == 12
    with pytest.raises(ValueError):
        news.build_request(news_text="x", target_asset="BTC", source_name="s", published_at_utc="t", horizon="2h", round_trip_cost_bp=15)


def test_mirror_state_inverts_directional_fields():
    st = {"last_price": 100, "trend_by_timeframe": {"5m": {"rsi14": 70.0, "price_vs_ema50_pct": 0.5, "bollinger_position_0_to_1": 0.9}},
          "derivatives": {"long_liquidations_15m_usd": 10, "short_liquidations_15m_usd": 99, "accounts_long_share": 0.6},
          "recent_headlines": [{"title": "x"}]}
    m = mirror_state(st)
    b = m["trend_by_timeframe"]["5m"]
    assert b["rsi14"] == 30.0 and b["price_vs_ema50_pct"] == -0.5 and b["bollinger_position_0_to_1"] == pytest.approx(0.1)
    assert m["derivatives"]["long_liquidations_15m_usd"] == 99 and m["derivatives"]["accounts_long_share"] == pytest.approx(0.4)
    assert "recent_headlines" not in m
    assert mirror_state(m)["trend_by_timeframe"] == st["trend_by_timeframe"]


def test_indicators_sane():
    closes = [100 + math.sin(i / 5) * 2 + i * 0.05 for i in range(300)]
    highs = [c + 0.3 for c in closes]
    lows = [c - 0.3 for c in closes]
    assert 0 <= ind.rsi(closes) <= 100
    assert ind.atr(highs, lows, closes) > 0
    a = ind.adx(highs, lows, closes)
    assert a and 0 <= a[0] <= 100
    assert ind.ema([1.0] * 50, 20) == pytest.approx(1.0)


def test_metrics_helpers():
    assert metrics.auc([0.9, 0.8, 0.2, 0.1], [True, True, False, False]) == 1.0
    assert metrics.auc([0.5, 0.5], [True, False]) == 0.5
    assert metrics._rps([0, 0, 1, 0], 2) == 0.0


def test_bybit_signature_matches_v5_spec():
    from ctb.broker.bybit_rest import RECV_WINDOW, BybitTestnet

    class _S: ...
    c = BybitTestnet("key", "secret", _S())  # type: ignore[arg-type]
    expected = hmac.new(b"secret", f"1700000000000key{RECV_WINDOW}category=linear".encode(), hashlib.sha256).hexdigest()
    assert c._sign("1700000000000", "category=linear") == expected
    assert c.host == "https://api-testnet.bybit.com"


def test_save_env_upserts_and_restricts_permissions(tmp_path, monkeypatch):
    import os
    import stat

    from ctb.config import key_hint, save_env

    env = tmp_path / ".env"
    env.write_text("# comment\nBYBIT_API_KEY=old\nCTB_SYMBOLS=BTCUSDT\n")
    monkeypatch.setattr(os, "environ", dict(os.environ))
    save_env({"BYBIT_API_KEY": "newkey123", "KAIROS_API_KEY": "kk"}, path=env)
    text = env.read_text()
    assert "BYBIT_API_KEY=newkey123" in text and "old" not in text
    assert "# comment" in text and "CTB_SYMBOLS=BTCUSDT" in text and "KAIROS_API_KEY=kk" in text
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    with pytest.raises(ValueError):
        save_env({"KAIROS_API_KEY": "a\nEVIL=1"}, path=env)
    assert key_hint("abcdefgh1234") == {"set": True, "hint": "…1234"}
    assert key_hint("") == {"set": False, "hint": ""}


def _weak_answers(l_t, l_s, s_t, s_s):
    a = good_answers()
    a["long_bracket"]["probabilities"] = {"target_first": l_t, "stop_first": l_s, "neither": round(1 - l_t - l_s, 6)}
    a["long_bracket"]["choice"] = max(a["long_bracket"]["probabilities"], key=a["long_bracket"]["probabilities"].get)
    a["short_bracket"]["probabilities"] = {"target_first": s_t, "stop_first": s_s, "neither": round(1 - s_t - s_s, 6)}
    a["short_bracket"]["choice"] = max(a["short_bracket"]["probabilities"], key=a["short_bracket"]["probabilities"].get)
    a["setup_quality"].update(score=1.0, probabilities=[0.1, 0.8, 0.1, 0.0, 0.0])
    a["data_sufficient"]["noul"] = 0.2
    return a


def test_exploration_follows_lean_but_never_bypasses_safety():
    s = Settings()
    s.exploration_mode = True
    parsed, issues = validate(_weak_answers(0.36, 0.47, 0.29, 0.54), Q)  # real-like: long preferred
    assert issues == []
    d = decide(parsed, META, s, has_position=False)
    assert d.action == "long" and d.exploration and d.strict_action == "flat" and d.reason.startswith("EXPLORE")
    s.exploration_mode = False
    assert decide(parsed, META, s, has_position=False).action == "flat"
    s.exploration_mode = True
    assert decide(parsed, META, s, has_position=True).action == "flat"  # hard gate
    assert decide(parsed, {**META, "venue_divergence_bp": 400}, s, has_position=False).action == "flat"
    s.allow_short = False
    p2, _ = validate(_weak_answers(0.25, 0.55, 0.36, 0.45), Q)  # short preferred
    assert decide(p2, META, s, has_position=False).action == "flat"
    s.allow_short = True
    p3, _ = validate(_weak_answers(0.25, 0.52, 0.26, 0.49), Q)  # no meaningful lean
    d3 = decide(p3, META, s, has_position=False)
    assert d3.action == "flat" and any("lean" in g for g in d3.gates)


def test_exploration_sizing_uses_reduced_risk():
    from ctb.policy import size_position
    s = Settings()
    s.max_notional_usdt = 1e12
    full, _ = size_position(100_000, 50, 100, s)
    small, _ = size_position(100_000, 50, 100, s, risk_pct=0.1)
    assert small == pytest.approx(full * 0.1 / s.risk_per_trade_pct)


def test_tf_profiles_move_cadence_horizon_and_stop_together():
    from ctb.config import TF_PROFILES
    s = Settings()
    s.symbols = ["A", "B", "C", "D", "E"]
    upd = s.apply_tf_profile("1")
    assert (s.decision_tf, s.horizon_bars, s.atr_tf, s.decision_tf_minutes) == ("1", 30, "5", 1)
    assert upd == {"CTB_DECISION_TF": "1", "CTB_HORIZON_BARS": "30", "CTB_ATR_TF": "5"}
    assert s.required_rph("1", probe_rate=0) == 300 and s.required_rph("5", probe_rate=0) == 60
    s.apply_tf_profile("60")
    assert s.horizon_bars * s.decision_tf_minutes == 720
    for tf, p in TF_PROFILES.items():  # the stop is always sized from a slower timeframe than decisions
        assert int(p["atr_tf"]) > int(tf)
