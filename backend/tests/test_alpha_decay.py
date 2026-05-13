"""Alpha Decay Monitor (#77) — evaluator + API + invariants."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.governance.alpha_decay import (
    AlphaDecayInput,
    AlphaDecayKind,
    AlphaDecayResult,
    AlphaDecayStatus,
    AlphaDecayThresholds,
    StrategyMetricsSnapshot,
    compute_alpha_decay_score,
    evaluate_alpha_decay,
)


def _snap(
    *,
    n=50, exp=300.0, pf=1.5, wr=0.55, mdd=200_000, cl=3,
) -> StrategyMetricsSnapshot:
    return StrategyMetricsSnapshot(
        trade_count=n, expectancy=exp,
        profit_factor=pf, win_rate=wr,
        max_drawdown=mdd, max_consecutive_losses=cl,
    )


# ---------- DTO invariants ----------


def test_result_rejects_order_signal_true():
    with pytest.raises(ValueError, match="is_order_signal"):
        AlphaDecayResult(
            strategy_name="x", score=10,
            status=AlphaDecayStatus.HEALTHY, kind=AlphaDecayKind.NONE,
            is_order_signal=True,
        )


def test_result_rejects_auto_disable_true():
    with pytest.raises(ValueError, match="auto_disable"):
        AlphaDecayResult(
            strategy_name="x", score=10,
            status=AlphaDecayStatus.HEALTHY, kind=AlphaDecayKind.NONE,
            auto_disable=True,
        )


def test_result_rejects_auto_apply_allowed_true():
    with pytest.raises(ValueError, match="auto_apply_allowed"):
        AlphaDecayResult(
            strategy_name="x", score=10,
            status=AlphaDecayStatus.HEALTHY, kind=AlphaDecayKind.NONE,
            auto_apply_allowed=True,
        )


def test_to_dict_has_invariant_flags():
    inp = AlphaDecayInput(strategy_name="x", baseline=_snap(), recent=_snap())
    r = evaluate_alpha_decay(inp)
    d = r.to_dict()
    assert d["is_order_signal"] is False
    assert d["auto_disable"] is False
    assert d["auto_apply_allowed"] is False
    assert d["live_flag_changed"] is False
    assert d["mode_changed"] is False


# ---------- score / status ----------


def test_healthy_when_metrics_identical():
    base = _snap()
    rec  = _snap()
    inp = AlphaDecayInput(strategy_name="x", baseline=base, recent=rec)
    r = evaluate_alpha_decay(inp)
    assert r.status is AlphaDecayStatus.HEALTHY
    assert r.score == 0
    assert r.kind is AlphaDecayKind.NONE


def test_expectancy_drop_increases_score():
    base = _snap(exp=300)
    rec  = _snap(exp=100)
    inp = AlphaDecayInput(strategy_name="x", baseline=base, recent=rec)
    r = evaluate_alpha_decay(inp)
    assert r.score > 0
    assert "expectancy_drop" in r.degraded_signals


def test_expectancy_flip_to_negative_triggers_warning_or_higher():
    """expectancy 양수→음수면 큰 가중치 — DECAY_WARNING 이상."""
    base = _snap(exp=300)
    rec  = _snap(exp=-50)
    inp = AlphaDecayInput(strategy_name="x", baseline=base, recent=rec)
    r = evaluate_alpha_decay(inp)
    assert r.status in (
        AlphaDecayStatus.DECAY_WARNING, AlphaDecayStatus.DISABLE_CANDIDATE,
    )
    assert "expectancy_flip_to_negative" in r.degraded_signals


def test_pf_below_min_triggers_signal():
    base = _snap(pf=1.5)
    rec  = _snap(pf=1.0)
    inp = AlphaDecayInput(strategy_name="x", baseline=base, recent=rec)
    r = evaluate_alpha_decay(inp)
    assert "pf_drop" in r.degraded_signals
    assert "pf_below_min" in r.degraded_signals


def test_mdd_worsening_triggers_signal():
    base = _snap(mdd=100_000)
    rec  = _snap(mdd=200_000)   # 2배 악화 (> 1.5)
    inp = AlphaDecayInput(strategy_name="x", baseline=base, recent=rec)
    r = evaluate_alpha_decay(inp)
    assert "mdd_worsen" in r.degraded_signals


def test_consec_losses_increase_triggers_signal():
    base = _snap(cl=2)
    rec  = _snap(cl=5)    # 2.5배
    inp = AlphaDecayInput(strategy_name="x", baseline=base, recent=rec)
    r = evaluate_alpha_decay(inp)
    assert "consec_losses_increase" in r.degraded_signals


def test_winrate_drop_triggers_signal():
    base = _snap(wr=0.60)
    rec  = _snap(wr=0.40)
    inp = AlphaDecayInput(strategy_name="x", baseline=base, recent=rec)
    r = evaluate_alpha_decay(inp)
    assert "winrate_drop" in r.degraded_signals


def test_data_quality_issue_triggers_signal_and_kind():
    base = _snap()
    rec  = _snap()
    inp = AlphaDecayInput(
        strategy_name="x", baseline=base, recent=rec,
        recent_data_quality_score=50.0,    # < 60 → block-level
    )
    r = evaluate_alpha_decay(inp)
    assert "data_quality_issue" in r.degraded_signals
    assert r.kind is AlphaDecayKind.DATA_QUALITY_ISSUE


def test_regime_change_triggers_signal_and_classifies_as_mismatch():
    """regime 변경 + 1지표만 악화 → REGIME_MISMATCH."""
    base = _snap()
    rec  = _snap(exp=200)   # 약간 하락
    inp = AlphaDecayInput(
        strategy_name="x", baseline=base, recent=rec,
        baseline_regime="trend_up", recent_regime="range_bound",
    )
    r = evaluate_alpha_decay(inp)
    assert "regime_change" in r.degraded_signals
    assert r.kind is AlphaDecayKind.REGIME_MISMATCH


def test_structural_decay_when_three_or_more_signals():
    """expectancy + pf + winrate + mdd 동시 악화 → STRUCTURAL_DECAY."""
    base = _snap(exp=300, pf=2.0, wr=0.60, mdd=100_000)
    rec  = _snap(exp=50,  pf=1.1, wr=0.40, mdd=300_000)
    inp = AlphaDecayInput(strategy_name="x", baseline=base, recent=rec)
    r = evaluate_alpha_decay(inp)
    assert r.kind is AlphaDecayKind.STRUCTURAL_DECAY


def test_short_term_drawdown_when_one_signal():
    """단 1개 지표만 악화 + regime 동일 → SHORT_TERM_DRAWDOWN."""
    base = _snap(exp=300, pf=1.5, wr=0.55, mdd=100_000, cl=3)
    rec  = _snap(exp=250, pf=1.5, wr=0.55, mdd=100_000, cl=3)
    inp = AlphaDecayInput(strategy_name="x", baseline=base, recent=rec)
    r = evaluate_alpha_decay(inp)
    # 1개 신호 = expectancy_drop. STRUCTURAL_DECAY 아님.
    assert r.kind is AlphaDecayKind.SHORT_TERM_DRAWDOWN


def test_insufficient_data_when_recent_trades_below_min():
    base = _snap(n=50)
    rec  = _snap(n=5)
    inp = AlphaDecayInput(strategy_name="x", baseline=base, recent=rec)
    r = evaluate_alpha_decay(inp)
    assert r.status is AlphaDecayStatus.INSUFFICIENT_DATA
    assert r.kind is AlphaDecayKind.INSUFFICIENT_DATA
    assert r.score == -1
    assert any("표본 부족" in c for c in r.cautions)


# ---------- score ranges ----------


def test_score_clamps_to_100():
    """모든 지표가 *극단적으로* 악화돼도 score ≤ 100."""
    base = _snap(exp=1000, pf=3.0, wr=0.80, mdd=10_000, cl=1)
    rec  = _snap(exp=-1000, pf=0.5, wr=0.10, mdd=1_000_000, cl=20)
    inp = AlphaDecayInput(
        strategy_name="x", baseline=base, recent=rec,
        recent_data_quality_score=30.0,
        baseline_regime="trend_up", recent_regime="bear_market",
    )
    r = evaluate_alpha_decay(inp)
    assert 0 <= r.score <= 100


def test_compute_score_returns_signals_list():
    base = _snap(exp=300, pf=1.5)
    rec  = _snap(exp=100, pf=1.1)
    score, signals = compute_alpha_decay_score(base, rec)
    assert score > 0
    assert "expectancy_drop" in signals
    assert "pf_drop" in signals
    assert "pf_below_min" in signals


# ---------- thresholds override ----------


def test_threshold_override_can_demote_status():
    """min_recent_trades=100 으로 올리면 표본 부족 처리."""
    base = _snap(n=50)
    rec  = _snap(n=50)   # default 임계 통과
    strict = AlphaDecayThresholds(min_recent_trades=100)
    inp = AlphaDecayInput(strategy_name="x", baseline=base, recent=rec)
    r = evaluate_alpha_decay(inp, strict)
    assert r.status is AlphaDecayStatus.INSUFFICIENT_DATA


# ---------- recommended_action wording ----------


def test_recommendation_mentions_no_auto_disable_when_warning():
    base = _snap(exp=300)
    rec  = _snap(exp=-100)
    inp = AlphaDecayInput(strategy_name="x", baseline=base, recent=rec)
    r = evaluate_alpha_decay(inp)
    assert "자동 비활성" in r.recommended_action or "DISABLE_CANDIDATE" in r.recommended_action


# ---------- API ----------


def test_route_evaluate_returns_healthy(client):
    body = {
        "strategy_name": "sma_cross",
        "baseline": {
            "trade_count": 100, "expectancy": 300.0, "profit_factor": 1.5,
            "win_rate": 0.55, "max_drawdown": 200000, "max_consecutive_losses": 3,
        },
        "recent": {
            "trade_count": 50, "expectancy": 300.0, "profit_factor": 1.5,
            "win_rate": 0.55, "max_drawdown": 200000, "max_consecutive_losses": 3,
        },
    }
    res = client.post("/api/governance/alpha-decay/evaluate", json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "HEALTHY"
    assert data["is_order_signal"] is False
    assert data["auto_disable"] is False
    assert data["auto_apply_allowed"] is False


def test_route_evaluate_returns_disable_candidate(client):
    body = {
        "strategy_name": "weak",
        "baseline": {
            "trade_count": 100, "expectancy": 500.0, "profit_factor": 2.0,
            "win_rate": 0.60, "max_drawdown": 100000, "max_consecutive_losses": 2,
        },
        "recent": {
            "trade_count": 50, "expectancy": -200.0, "profit_factor": 0.7,
            "win_rate": 0.30, "max_drawdown": 800000, "max_consecutive_losses": 8,
        },
        "recent_data_quality_score": 40.0,
        "baseline_regime": "trend_up",
        "recent_regime": "bear_market",
    }
    res = client.post("/api/governance/alpha-decay/evaluate", json=body)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "DISABLE_CANDIDATE"
    assert data["auto_disable"] is False     # invariant


def test_route_response_does_not_leak_secrets(client):
    body = {
        "strategy_name": "x",
        "baseline": {},
        "recent": {},
    }
    res = client.post("/api/governance/alpha-decay/evaluate", json=body)
    text = res.text.lower()
    for needle in [
        "kis_app_key", "kis_app_secret", "anthropic_api_key",
        "telegram_bot_token", "sk-", "bearer ",
    ]:
        assert needle not in text


# ---------- invariants — static grep guards ----------


_MODULE_PATH = Path("backend/app/governance/alpha_decay.py")


def _resolve(p: Path) -> Path:
    return p if p.exists() else Path(__file__).resolve().parents[2] / p


def test_module_does_not_import_broker_or_executor():
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    forbidden = [
        "from app.brokers.kis",
        "from app.brokers.mock_broker",
        "from app.execution.order_router",
        "from app.execution.order_executor",
        "from app.execution.executor",
        "from app.execution.paper_trader",
        "from app.ai.assist",
        "from app.ai.client",
        "import anthropic",
        "import openai",
        "import httpx",
        "import requests",
    ]
    for needle in forbidden:
        assert needle not in src, f"forbidden import: {needle!r}"


def test_module_does_not_call_broker_or_route_order():
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    forbidden = [
        "broker.place_order(",
        "broker.cancel_order(",
        "route_order(",
        ".execute_order(",
        "OrderExecutor(",
        "submit_candidate(",
        "AiClient(",
    ]
    for needle in forbidden:
        assert needle not in src, f"forbidden call: {needle!r}"


def test_module_does_not_mutate_safety_flags():
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    forbidden = [
        "settings.enable_live_trading =",
        "settings.enable_ai_execution =",
        "settings.enable_futures_live_trading =",
        "setattr(settings, \"enable_",
        "os.environ[\"ENABLE_LIVE_TRADING\"]",
        "os.environ[\"ENABLE_AI_EXECUTION\"]",
        "os.environ[\"ENABLE_FUTURES_LIVE_TRADING\"]",
    ]
    for needle in forbidden:
        assert needle not in src, f"mutates safety flag: {needle!r}"


def test_module_does_not_write_to_db():
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    write_patterns = [
        "db.add(", "db.add_all(", "db.commit(", "db.flush(",
        "db.delete(", "db.merge(",
        "INSERT INTO", "UPDATE ", "DELETE FROM",
    ]
    for needle in write_patterns:
        assert needle not in src, f"writes to DB: {needle!r}"


def test_module_does_not_auto_disable_or_mutate_strategy():
    """전략 비활성/삭제/promotion 변경 시도 0건."""
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    forbidden = [
        ".save_params(",
        ".apply_params(",
        ".update_params(",
        "strategy.enabled = False",
        "strategy.disable(",
        "PromotionGate(",
        "evaluate_promotion(",
        ".set_emergency_stop(",
    ]
    for needle in forbidden:
        assert needle not in src, f"mutates strategy state: {needle!r}"


def test_module_does_not_read_settings():
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    forbidden = [
        "from app.core.config import",
        "get_settings(",
    ]
    for needle in forbidden:
        assert needle not in src, f"reads settings directly: {needle!r}"
