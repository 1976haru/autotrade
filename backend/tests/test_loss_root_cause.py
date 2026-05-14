"""Loss Root Cause Tagging (#96) — evaluator + summary + API + 정적 가드.

본 테스트는 #79 loss_tagging 와는 *별개* 모듈 검증.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.analytics.loss_root_cause import (
    LossRootCauseInput,
    LossRootCauseResult,
    LossRootCauseSummary,
    RootCauseCategory,
    RootCauseSeverity,
    RootCauseTag,
    RootCauseTagAssignment,
    RootCauseThresholds,
    category_for_tag,
    evaluate_loss_root_cause,
    summarize_root_causes,
)


# ====================================================================
# DTO invariants
# ====================================================================


def test_result_rejects_is_estimated_false():
    with pytest.raises(ValueError, match="is_estimated"):
        LossRootCauseResult(symbol="x", is_estimated=False)


def test_result_rejects_is_order_signal_true():
    with pytest.raises(ValueError, match="is_order_signal"):
        LossRootCauseResult(symbol="x", is_order_signal=True)


def test_result_rejects_auto_apply_allowed_true():
    with pytest.raises(ValueError, match="auto_apply_allowed"):
        LossRootCauseResult(symbol="x", auto_apply_allowed=True)


def test_result_rejects_is_investment_advice_true():
    with pytest.raises(ValueError, match="is_investment_advice"):
        LossRootCauseResult(symbol="x", is_investment_advice=True)


def test_input_rejects_empty_symbol():
    with pytest.raises(ValueError, match="symbol"):
        LossRootCauseInput(symbol="")


def test_summary_rejects_is_estimated_false():
    with pytest.raises(ValueError, match="is_estimated"):
        LossRootCauseSummary(is_estimated=False)


def test_summary_rejects_is_order_signal_true():
    with pytest.raises(ValueError, match="is_order_signal"):
        LossRootCauseSummary(is_order_signal=True)


# ====================================================================
# enum / category coverage — 16 tags × 5 + unknown
# ====================================================================


def test_tag_no_buy_sell_hold():
    values = {v.value for v in RootCauseTag}
    for banned in ("BUY", "SELL", "HOLD", "PLACE_ORDER", "buy", "sell", "hold"):
        assert banned not in values


def test_category_no_buy_sell_hold():
    values = {v.value for v in RootCauseCategory}
    for banned in ("BUY", "SELL", "HOLD"):
        assert banned not in values


def test_severity_no_buy_sell_hold():
    values = {v.value for v in RootCauseSeverity}
    for banned in ("BUY", "SELL", "HOLD"):
        assert banned not in values


def test_tags_contain_required_16_names():
    values = {v.name for v in RootCauseTag}
    for required in (
        "LATE_ENTRY", "LATE_EXIT", "STALE_SIGNAL", "AGENT_OVERRULED",
        "HIGH_CORRELATION", "RISK_GATE_REJECTED",
        "HIGH_VOLATILITY", "BAD_REGIME", "NEWS_RISK",
        "LOW_LIQUIDITY", "SLIPPAGE", "SPREAD_TOO_WIDE",
        "STOP_LOSS_HIT", "TIME_STOP_HIT", "KIMP_CONVERGENCE_FAIL",
        "UNKNOWN",
    ):
        assert required in values, f"태그 {required} 누락"


def test_categories_5_plus_unknown():
    values = {v.value for v in RootCauseCategory}
    assert values == {
        "decision", "risk", "market", "execution", "strategy", "unknown",
    }


def test_every_tag_has_category_mapping():
    for tag in RootCauseTag:
        cat = category_for_tag(tag)
        assert cat in RootCauseCategory


# ====================================================================
# classifier logic
# ====================================================================


def test_no_metrics_returns_unknown():
    r = evaluate_loss_root_cause(LossRootCauseInput(symbol="x"))
    assert r.primary_tag is RootCauseTag.UNKNOWN
    assert len(r.tags) == 1


def test_stale_signal_tagged_when_age_exceeds():
    r = evaluate_loss_root_cause(LossRootCauseInput(
        symbol="005930", signal_age_minutes_at_entry=45,
    ))
    assert any(t.tag is RootCauseTag.STALE_SIGNAL for t in r.tags)
    # severity HIGH 인지 확인.
    t = next(x for x in r.tags if x.tag is RootCauseTag.STALE_SIGNAL)
    assert t.severity is RootCauseSeverity.HIGH


def test_high_correlation_tagged():
    r = evaluate_loss_root_cause(LossRootCauseInput(
        symbol="x", portfolio_max_correlation=0.92,
    ))
    assert any(t.tag is RootCauseTag.HIGH_CORRELATION for t in r.tags)


def test_high_correlation_uses_abs_value():
    """음의 상관관계도 |corr| 기준으로 태그."""
    r = evaluate_loss_root_cause(LossRootCauseInput(
        symbol="x", portfolio_max_correlation=-0.90,
    ))
    assert any(t.tag is RootCauseTag.HIGH_CORRELATION for t in r.tags)


def test_late_entry_tagged_when_lag_exceeds():
    r = evaluate_loss_root_cause(LossRootCauseInput(
        symbol="x", entry_lag_seconds=60,
    ))
    assert any(t.tag is RootCauseTag.LATE_ENTRY for t in r.tags)


def test_late_exit_tagged():
    r = evaluate_loss_root_cause(LossRootCauseInput(
        symbol="x", exit_lag_seconds=90,
    ))
    assert any(t.tag is RootCauseTag.LATE_EXIT for t in r.tags)


def test_agent_overruled_tagged():
    r = evaluate_loss_root_cause(LossRootCauseInput(
        symbol="x", operator_overruled_ai=True,
    ))
    assert any(t.tag is RootCauseTag.AGENT_OVERRULED for t in r.tags)


def test_risk_gate_rejected_tagged():
    r = evaluate_loss_root_cause(LossRootCauseInput(
        symbol="x", risk_gate_was_rejected=True,
    ))
    tags_set = {t.tag for t in r.tags}
    assert RootCauseTag.RISK_GATE_REJECTED in tags_set


def test_slippage_tagged_when_exceeds():
    r = evaluate_loss_root_cause(LossRootCauseInput(
        symbol="x", realized_slippage_bps=75.0,
    ))
    assert any(t.tag is RootCauseTag.SLIPPAGE for t in r.tags)


def test_low_liquidity_tagged():
    r = evaluate_loss_root_cause(LossRootCauseInput(
        symbol="x", volume_to_avg_ratio=0.1,
    ))
    assert any(t.tag is RootCauseTag.LOW_LIQUIDITY for t in r.tags)


def test_spread_too_wide_tagged():
    r = evaluate_loss_root_cause(LossRootCauseInput(
        symbol="x", spread_bps_at_entry=150.0,
    ))
    assert any(t.tag is RootCauseTag.SPREAD_TOO_WIDE for t in r.tags)


def test_high_volatility_tagged():
    r = evaluate_loss_root_cause(LossRootCauseInput(
        symbol="x", intraday_volatility=0.06,
    ))
    assert any(t.tag is RootCauseTag.HIGH_VOLATILITY for t in r.tags)


def test_bad_regime_tagged():
    r = evaluate_loss_root_cause(LossRootCauseInput(
        symbol="x", market_regime_unfavorable=True,
        market_regime_at_entry="TREND_DOWN",
    ))
    assert any(t.tag is RootCauseTag.BAD_REGIME for t in r.tags)


def test_news_risk_tagged():
    r = evaluate_loss_root_cause(LossRootCauseInput(
        symbol="x", adverse_news_event=True,
    ))
    assert any(t.tag is RootCauseTag.NEWS_RISK for t in r.tags)


def test_stop_loss_hit_tagged():
    r = evaluate_loss_root_cause(LossRootCauseInput(
        symbol="x", hit_stop_loss=True,
    ))
    assert any(t.tag is RootCauseTag.STOP_LOSS_HIT for t in r.tags)


def test_time_stop_hit_tagged():
    r = evaluate_loss_root_cause(LossRootCauseInput(
        symbol="x", hit_time_stop=True,
    ))
    assert any(t.tag is RootCauseTag.TIME_STOP_HIT for t in r.tags)


def test_multiple_tags_assigned_in_complex_case():
    """multi-cause 동시 부여."""
    r = evaluate_loss_root_cause(LossRootCauseInput(
        symbol="x",
        signal_age_minutes_at_entry=45,
        portfolio_max_correlation=0.92,
        realized_slippage_bps=75.0,
        hit_stop_loss=True,
    ))
    tag_set = {t.tag for t in r.tags}
    assert RootCauseTag.STALE_SIGNAL in tag_set
    assert RootCauseTag.HIGH_CORRELATION in tag_set
    assert RootCauseTag.SLIPPAGE in tag_set
    assert RootCauseTag.STOP_LOSS_HIT in tag_set
    assert RootCauseTag.UNKNOWN not in tag_set


def test_primary_tag_uses_risk_priority():
    """RISK > DECISION > MARKET > EXECUTION > STRATEGY 순."""
    r = evaluate_loss_root_cause(LossRootCauseInput(
        symbol="x",
        portfolio_max_correlation=0.92,   # risk + HIGH
        hit_stop_loss=True,                # strategy + LOW
    ))
    assert r.primary_category is RootCauseCategory.RISK
    assert r.primary_tag is RootCauseTag.HIGH_CORRELATION


def test_threshold_override():
    """threshold override 시 분류 변화."""
    inp = LossRootCauseInput(
        symbol="x", entry_lag_seconds=20,
    )
    # default(30s) 이면 lag=20 은 LATE_ENTRY 아님.
    r1 = evaluate_loss_root_cause(inp)
    assert not any(t.tag is RootCauseTag.LATE_ENTRY for t in r1.tags)

    # override 5s 면 lag=20 은 LATE_ENTRY.
    r2 = evaluate_loss_root_cause(
        inp, thresholds=RootCauseThresholds(late_entry_seconds=5),
    )
    assert any(t.tag is RootCauseTag.LATE_ENTRY for t in r2.tags)


def test_improvement_advice_present_for_tagged_causes():
    r = evaluate_loss_root_cause(LossRootCauseInput(
        symbol="x",
        signal_age_minutes_at_entry=45,
        portfolio_max_correlation=0.92,
    ))
    # 2개 advice 이상 — stale signal + high correlation 각각.
    assert len(r.improvement_advice) >= 2
    joined = " ".join(r.improvement_advice).lower()
    assert "stale_signal" in joined or "high_correlation" in joined


# ====================================================================
# aggregation
# ====================================================================


def test_summarize_empty_returns_zero():
    s = summarize_root_causes([])
    assert s.total_loss_count == 0
    assert s.by_tag == []


def test_summarize_basic_distribution():
    r1 = evaluate_loss_root_cause(LossRootCauseInput(
        symbol="a", hit_stop_loss=True,
    ))
    r2 = evaluate_loss_root_cause(LossRootCauseInput(
        symbol="b", hit_stop_loss=True,
    ))
    r3 = evaluate_loss_root_cause(LossRootCauseInput(
        symbol="c", portfolio_max_correlation=0.92,
    ))
    s = summarize_root_causes(
        [r1, r2, r3],
        strategy_by_result=["sma", "sma", "vwap"],
    )
    assert s.total_loss_count == 3
    # stop_loss_hit 2회 → top tag.
    assert "stop_loss_hit" in s.top_tags
    # high_correlation HIGH severity.
    assert "high_correlation" in s.high_severity_tags
    # by_strategy 집계.
    assert "sma" in s.by_strategy
    assert "vwap" in s.by_strategy


def test_summarize_rejects_mismatched_strategies():
    r1 = evaluate_loss_root_cause(LossRootCauseInput(symbol="a"))
    with pytest.raises(ValueError, match="length"):
        summarize_root_causes(
            [r1], strategy_by_result=["s1", "s2"],
        )


# ====================================================================
# to_dict — invariants 응답에 carry
# ====================================================================


def test_to_dict_carries_invariants():
    r = evaluate_loss_root_cause(LossRootCauseInput(symbol="x"))
    d = r.to_dict()
    assert d["is_estimated"] is True
    assert d["is_order_signal"] is False
    assert d["auto_apply_allowed"] is False
    assert d["is_investment_advice"] is False


def test_summary_to_dict_carries_invariants():
    s = summarize_root_causes([])
    d = s.to_dict()
    assert d["is_estimated"] is True
    assert d["is_order_signal"] is False
    assert d["auto_apply_allowed"] is False


# ====================================================================
# API
# ====================================================================


def test_api_evaluate_returns_invariants(client):
    body = {
        "symbol": "005930",
        "is_loss": True,
        "trade_pnl": -50000,
        "signal_age_minutes_at_entry": 45,
        "portfolio_max_correlation": 0.92,
    }
    res = client.post("/api/analytics/loss-root-cause/evaluate", json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["is_estimated"] is True
    assert data["is_order_signal"] is False
    assert data["auto_apply_allowed"] is False
    assert data["is_investment_advice"] is False
    assert data["primary_category"] == "risk"
    assert data["primary_tag"] == "high_correlation"


def test_api_evaluate_with_no_metrics_returns_unknown(client):
    body = {"symbol": "x", "is_loss": True}
    res = client.post("/api/analytics/loss-root-cause/evaluate", json=body)
    assert res.status_code == 200
    data = res.json()
    assert data["primary_tag"] == "unknown"


def test_api_evaluate_rejects_empty_symbol(client):
    res = client.post(
        "/api/analytics/loss-root-cause/evaluate", json={"symbol": ""},
    )
    assert res.status_code in (400, 422)


def test_api_summarize_batch(client):
    body = {
        "losses": [
            {"symbol": "a", "hit_stop_loss": True, "strategy": "sma"},
            {"symbol": "b", "hit_stop_loss": True, "strategy": "sma"},
            {"symbol": "c", "portfolio_max_correlation": 0.92,
             "strategy": "vwap"},
        ],
    }
    res = client.post(
        "/api/analytics/loss-root-cause/summarize", json=body,
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["total_loss_count"] == 3
    assert data["is_estimated"] is True
    assert data["is_order_signal"] is False
    assert "stop_loss_hit" in data["top_tags"]


def test_api_does_not_leak_secrets(client):
    body = {"symbol": "x", "is_loss": True}
    res = client.post(
        "/api/analytics/loss-root-cause/evaluate", json=body,
    )
    text = res.text.lower()
    for needle in [
        "kis_app_key=", "kis_app_secret=", "anthropic_api_key=",
        "telegram_bot_token=", "sk-", "bearer ",
    ]:
        assert needle not in text


# ====================================================================
# invariants — static grep guards
# ====================================================================


_MODULE_PATH = Path("backend/app/analytics/loss_root_cause.py")


def _resolve(path: Path) -> Path:
    if path.exists():
        return path
    return Path(__file__).resolve().parents[2] / path


def test_module_does_not_import_broker_or_executor():
    forbidden = [
        "from app.brokers.kis",
        "from app.brokers.mock_broker",
        "from app.execution.order_router",
        "from app.execution.order_executor",
        "from app.execution.executor",
        "from app.execution.paper_trader",
    ]
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in src, f"forbidden import: {needle!r}"


def test_module_does_not_import_ai_or_external_http():
    forbidden = [
        "from app.ai.assist",
        "from app.ai.client",
        "import anthropic",
        "import openai",
        "import httpx",
        "import requests",
    ]
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in src, f"forbidden import: {needle!r}"


def test_module_does_not_read_settings():
    forbidden = [
        "from app.core.config import",
        "get_settings(",
    ]
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in src, f"reads settings: {needle!r}"


def test_module_does_not_call_broker_or_route_order():
    forbidden = [
        "broker.place_order(",
        "broker.cancel_order(",
        "route_order(",
        "OrderExecutor(",
        "submit_candidate(",
    ]
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in src, f"forbidden call: {needle!r}"


def test_module_does_not_mutate_safety_flags():
    forbidden = [
        "settings.enable_live_trading =",
        "settings.enable_ai_execution =",
        "settings.enable_futures_live_trading =",
        "setattr(settings, \"enable_",
        "os.environ[\"ENABLE_LIVE_TRADING\"]",
        "os.environ[\"ENABLE_AI_EXECUTION\"]",
        "os.environ[\"ENABLE_FUTURES_LIVE_TRADING\"]",
    ]
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in src, f"mutates safety flag: {needle!r}"


def test_module_does_not_write_to_db():
    forbidden = [
        "db.add(", "db.add_all(", "db.commit(", "db.flush(",
        "db.delete(", "db.merge(",
        "INSERT INTO", "UPDATE ", "DELETE FROM",
    ]
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in src, f"writes to DB: {needle!r}"
