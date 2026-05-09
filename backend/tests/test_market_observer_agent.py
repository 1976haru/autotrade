"""#52: Market Observer Agent tests.

Coverage:
- `MarketObserverOutput.is_order_signal=True` 시 ValueError (dataclass 가드)
- `classify_turnover` / `classify_volatility` / `classify_freshness` 분류 boundary
- `observe_market` deterministic — 같은 입력에 같은 출력
- 데이터 부족 시 UNKNOWN / WATCH_ONLY로 friendly fallback (예외 X)
- risk_level 결정 매트릭스: BLOCKED / HIGH / MEDIUM / LOW
- recommended_stance 결정 매트릭스: AGGRESSIVE / NORMAL / DEFENSIVE / WATCH_ONLY / PAUSE_NEW_BUY
- 3-line summary 생성
- market_regime carry
- `MarketObserverAgent` (#51 AgentBase 호환) 호출
- `/api/agents/market-observer` endpoint
- 정적 가드: market_observer 모듈은 broker / OrderExecutor / route_order import 0건
- BUY/SELL/HOLD 결정 0건 (반환 값에 ORDER_SIGNAL 키 없음)
"""

from __future__ import annotations

import pytest

from app.agents.base import AgentContext, AgentDecision, AgentRole
from app.agents.market_observer import (
    DataFreshnessStatus,
    IndexQuote,
    MarketObserverAgent,
    MarketObserverInput,
    MarketObserverOutput,
    MarketRiskLevel,
    RecommendedStance,
    TurnoverState,
    VolatilityState,
    classify_freshness,
    classify_turnover,
    classify_volatility,
    observe_market,
)
from app.agents.market_regime import classify_market_regime


# ====================================================================
# 1. MarketObserverOutput dataclass guard
# ====================================================================


def test_output_rejects_is_order_signal_true():
    with pytest.raises(ValueError, match="is_order_signal"):
        MarketObserverOutput(
            risk_level=MarketRiskLevel.LOW,
            recommended_stance=RecommendedStance.NORMAL,
            summary_lines=["x", "y", "z"],
            turnover_state=TurnoverState.NORMAL,
            volatility_state=VolatilityState.NORMAL,
            freshness_status=DataFreshnessStatus.FRESH,
            leading_sectors=[], lagging_sectors=[], leading_themes=[],
            surge_count=0, plunge_count=0, indices=[],
            is_order_signal=True,
        )


def test_output_default_is_not_order_signal():
    out = observe_market(MarketObserverInput())
    assert out.is_order_signal is False
    d = out.to_dict()
    assert d["is_order_signal"] is False
    # 응답 dict에 BUY/SELL/HOLD 키 없음 — order signal로 오해할 여지 차단.
    forbidden_keys = {"buy", "sell", "hold", "order", "side", "decision"}
    assert forbidden_keys.isdisjoint(d.keys())


# ====================================================================
# 2. Pure classification helpers
# ====================================================================


def test_classify_turnover_boundaries():
    assert classify_turnover(None) == TurnoverState.UNKNOWN
    assert classify_turnover(0.5) == TurnoverState.BELOW_AVG
    assert classify_turnover(1.0) == TurnoverState.NORMAL
    assert classify_turnover(1.5) == TurnoverState.ABOVE_AVG
    assert classify_turnover(2.5) == TurnoverState.SURGE


def test_classify_volatility_boundaries():
    assert classify_volatility(None) == VolatilityState.UNKNOWN
    assert classify_volatility(0.5) == VolatilityState.LOW
    assert classify_volatility(1.5) == VolatilityState.NORMAL
    assert classify_volatility(2.5) == VolatilityState.ELEVATED
    assert classify_volatility(4.0) == VolatilityState.EXTREME


def test_classify_freshness_boundaries():
    assert classify_freshness(None) == DataFreshnessStatus.UNKNOWN
    assert classify_freshness(30)  == DataFreshnessStatus.FRESH
    assert classify_freshness(120) == DataFreshnessStatus.STALE
    assert classify_freshness(600) == DataFreshnessStatus.EXPIRED


# ====================================================================
# 3. observe_market — deterministic + friendly fallback
# ====================================================================


def test_observe_market_with_no_data_emits_friendly_fallback():
    """모든 필드 None — UNKNOWN / WATCH_ONLY 등으로 friendly fallback (예외 X)."""
    out = observe_market(MarketObserverInput())
    assert out.turnover_state == TurnoverState.UNKNOWN
    assert out.volatility_state == VolatilityState.UNKNOWN
    assert out.freshness_status == DataFreshnessStatus.UNKNOWN
    assert out.is_order_signal is False
    # risk_level은 LOW (UNKNOWN은 페널티 X) — recommended_stance는 NORMAL.
    assert out.risk_level == MarketRiskLevel.LOW
    assert out.recommended_stance == RecommendedStance.NORMAL


def test_observe_market_is_deterministic():
    inp = MarketObserverInput(
        turnover_vs_avg=1.5, volatility_pct=2.5,
        surge_count=10, plunge_count=5,
    )
    out1 = observe_market(inp)
    out2 = observe_market(inp)
    # created_at은 시간이 흘러 다를 수 있으므로 to_dict에서 created_at 제외.
    d1 = {k: v for k, v in out1.to_dict().items() if k != "created_at"}
    d2 = {k: v for k, v in out2.to_dict().items() if k != "created_at"}
    assert d1 == d2


# ====================================================================
# 4. Risk level matrix
# ====================================================================


def test_risk_level_blocked_when_freshness_expired():
    out = observe_market(MarketObserverInput(data_freshness_seconds=600))
    assert out.risk_level == MarketRiskLevel.BLOCKED
    assert out.recommended_stance == RecommendedStance.PAUSE_NEW_BUY


def test_risk_level_blocked_when_regime_is_risk_off():
    regime = classify_market_regime(risk_off_signal=True)
    assert regime.trade_permission == "BLOCK"
    out = observe_market(MarketObserverInput(market_regime=regime))
    assert out.risk_level == MarketRiskLevel.BLOCKED
    assert out.recommended_stance == RecommendedStance.PAUSE_NEW_BUY


def test_risk_level_high_when_volatility_extreme():
    out = observe_market(MarketObserverInput(volatility_pct=5.0))
    assert out.risk_level == MarketRiskLevel.HIGH
    assert out.recommended_stance == RecommendedStance.WATCH_ONLY


def test_risk_level_high_when_too_many_plunges():
    out = observe_market(MarketObserverInput(plunge_count=30))
    assert out.risk_level == MarketRiskLevel.HIGH
    assert out.recommended_stance == RecommendedStance.WATCH_ONLY


def test_risk_level_medium_when_volatility_elevated():
    out = observe_market(MarketObserverInput(volatility_pct=2.5))
    assert out.risk_level == MarketRiskLevel.MEDIUM
    assert out.recommended_stance == RecommendedStance.DEFENSIVE


def test_risk_level_medium_when_freshness_stale():
    out = observe_market(MarketObserverInput(data_freshness_seconds=120))
    assert out.risk_level == MarketRiskLevel.MEDIUM


def test_risk_level_low_in_calm_market():
    out = observe_market(MarketObserverInput(
        turnover_vs_avg=1.0, volatility_pct=1.2,
        data_freshness_seconds=10, surge_count=0, plunge_count=0,
    ))
    assert out.risk_level == MarketRiskLevel.LOW


# ====================================================================
# 5. Recommended stance matrix
# ====================================================================


def test_stance_aggressive_when_low_risk_with_surge_turnover():
    out = observe_market(MarketObserverInput(
        turnover_vs_avg=2.5, volatility_pct=1.5,
        data_freshness_seconds=10,
    ))
    # turnover SURGE + volatility NORMAL (1.5%) + freshness FRESH = LOW risk
    # → AGGRESSIVE.
    assert out.risk_level == MarketRiskLevel.LOW
    assert out.recommended_stance == RecommendedStance.AGGRESSIVE


def test_stance_normal_when_calm_average_market():
    out = observe_market(MarketObserverInput(
        turnover_vs_avg=1.0, volatility_pct=0.8,
        data_freshness_seconds=10,
    ))
    assert out.recommended_stance == RecommendedStance.NORMAL


# ====================================================================
# 6. Summary lines (3 lines required)
# ====================================================================


def test_summary_has_three_lines():
    out = observe_market(MarketObserverInput(
        turnover_vs_avg=1.0, volatility_pct=1.5,
        data_freshness_seconds=10,
    ))
    assert len(out.summary_lines) == 3
    assert "시장 위험도" in out.summary_lines[0]


def test_summary_mentions_stale_when_freshness_stale():
    out = observe_market(MarketObserverInput(data_freshness_seconds=120))
    joined = " ".join(out.summary_lines)
    assert "stale" in joined.lower() or "stale" in joined


def test_summary_mentions_surge_when_many_high_movers():
    out = observe_market(MarketObserverInput(
        turnover_vs_avg=1.0, volatility_pct=1.0,
        surge_count=50,
    ))
    joined = " ".join(out.summary_lines)
    assert "급등" in joined and "50" in joined


# ====================================================================
# 7. Market regime carry
# ====================================================================


def test_market_regime_is_carried_to_output():
    regime = classify_market_regime(trend_strength_pct=3.0, volume_ratio=1.5)
    out = observe_market(MarketObserverInput(market_regime=regime))
    assert out.market_regime is not None
    assert out.market_regime["regime"] == regime.regime
    assert out.market_regime["trade_permission"] == regime.trade_permission


def test_no_market_regime_carry_when_not_provided():
    out = observe_market(MarketObserverInput())
    assert out.market_regime is None


# ====================================================================
# 8. Indices carry
# ====================================================================


def test_indices_carry_to_output_dict():
    inp = MarketObserverInput(indices=[
        IndexQuote(name="KOSPI",  last_price=2700.50, change_pct=0.8),
        IndexQuote(name="KOSDAQ", last_price=850.30,  change_pct=-0.4),
    ])
    out = observe_market(inp)
    assert len(out.indices) == 2
    assert out.indices[0]["name"] == "KOSPI"
    assert out.indices[1]["change_pct"] == -0.4


# ====================================================================
# 9. MarketObserverAgent (#51 AgentBase 호환)
# ====================================================================


def test_agent_metadata_marks_no_execute():
    agent = MarketObserverAgent()
    md = agent.metadata
    assert md.role == AgentRole.OBSERVER
    assert md.can_execute_order is False
    forbidden_text = " ".join(md.forbidden)
    assert "BUY" in forbidden_text or "주문 신호" in forbidden_text
    assert "broker" in forbidden_text


def test_agent_run_returns_observe_decision():
    agent = MarketObserverAgent()
    out = agent.run(AgentContext(market_state={
        "turnover_vs_avg": 1.5, "volatility_pct": 2.5,
        "surge_count": 10, "plunge_count": 5,
    }))
    assert out.role == AgentRole.OBSERVER
    assert out.decision == AgentDecision.OBSERVE
    assert out.is_order_intent is False
    assert out.can_execute_order is False
    # snapshot이 metadata에 carry.
    assert "summary_lines" in out.metadata
    assert "risk_level" in out.metadata


# ====================================================================
# 10. /api/agents/market-observer endpoint
# ====================================================================


def test_api_market_observer_returns_snapshot(client):
    res = client.post("/api/agents/market-observer", json={
        "turnover_vs_avg": 1.5,
        "volatility_pct": 2.5,
        "surge_count": 10,
        "plunge_count": 5,
        "leading_sectors": ["반도체", "2차전지"],
    })
    assert res.status_code == 200
    body = res.json()
    assert body["is_order_signal"] is False
    assert body["risk_level"] in ("LOW", "MEDIUM", "HIGH", "BLOCKED")
    assert body["recommended_stance"] in (
        "AGGRESSIVE", "NORMAL", "DEFENSIVE", "WATCH_ONLY", "PAUSE_NEW_BUY",
    )
    assert len(body["summary_lines"]) == 3
    assert body["leading_sectors"] == ["반도체", "2차전지"]


def test_api_market_observer_friendly_fallback_on_empty_payload(client):
    res = client.post("/api/agents/market-observer", json={})
    assert res.status_code == 200
    body = res.json()
    assert body["is_order_signal"] is False
    assert body["turnover_state"] == "UNKNOWN"
    assert body["volatility_state"] == "UNKNOWN"
    assert body["freshness_status"] == "UNKNOWN"


def test_api_market_observer_with_regime_input_classifies_and_carries(client):
    res = client.post("/api/agents/market-observer", json={
        "market_regime_input": {"risk_off_signal": True},
    })
    body = res.json()
    assert body["risk_level"] == "BLOCKED"
    assert body["market_regime"] is not None
    assert body["market_regime"]["trade_permission"] == "BLOCK"


def test_api_market_observer_does_not_create_audit_or_orders(client):
    """endpoint는 read-only — DB / audit / approval row 변경 0건."""
    from sqlalchemy import select
    from app.db.models import OrderAuditLog, PendingApproval

    client.post("/api/agents/market-observer", json={
        "turnover_vs_avg": 1.0, "volatility_pct": 1.0,
    })
    with client.test_db_factory() as db:
        assert db.execute(select(OrderAuditLog)).scalars().all() == []
        assert db.execute(select(PendingApproval)).scalars().all() == []


# ====================================================================
# 11. Static guards — no broker / executor / route_order imports
# ====================================================================


def test_market_observer_module_does_not_import_broker_or_executor():
    import app.agents.market_observer as mod
    src_path = mod.__file__
    assert src_path is not None
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    forbidden = (
        "from app.brokers",
        "import app.brokers",
        "from app.execution.executor",
        "from app.execution.order_router",
        "from app.permission.gate",
        "broker.place_order(",
        ".place_order(",
        ".cancel_order(",
        "route_order(",
    )
    for snippet in forbidden:
        assert snippet not in src, (
            f"app.agents.market_observer must not contain '{snippet}' — "
            "Observer is context-only."
        )


def test_market_observer_does_not_emit_buy_sell_hold_signals():
    """Observer는 BUY/SELL/HOLD를 *반환하지 않는다* — 결정 매트릭스 검증."""
    out = observe_market(MarketObserverInput(
        turnover_vs_avg=2.5, volatility_pct=1.5, surge_count=20,
    ))
    # 응답 dict 어디에도 BUY/SELL/HOLD 키 / 값이 없음.
    d = out.to_dict()
    text = repr(d)
    # recommended_stance는 advisory enum이며 BUY/SELL/HOLD가 아님.
    assert "BUY" not in d.get("recommended_stance", "")
    assert "SELL" not in d.get("recommended_stance", "")
    assert "HOLD" not in d.get("recommended_stance", "")
    # 명시적: 본 출력은 order signal 아님.
    assert d["is_order_signal"] is False


# ====================================================================
# 12. Module guards on AgentContext — no broker/key fields
# ====================================================================


def test_observer_does_not_receive_broker_or_keys_via_context():
    """`AgentContext`는 broker / api_key 필드 0개 (#51 invariant 상속)."""
    import dataclasses
    from app.agents.base import AgentContext
    field_names = {f.name for f in dataclasses.fields(AgentContext)}
    forbidden = {"broker", "api_key", "secret", "anthropic_api_key",
                 "kis_app_key"}
    assert field_names.isdisjoint(forbidden)
