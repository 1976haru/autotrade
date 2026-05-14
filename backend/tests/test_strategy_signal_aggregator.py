"""StrategySignalAggregator facade + integration tests (#85).

본 파일은 #84 의 `test_strategy_aggregator.py` 가 다루는 *순수 함수* 단위
테스트 위에, ``StrategySignalAggregator`` *facade class* 와 selection agent
연계 흐름을 검증한다.

테스트 범위:
- facade 가 module-level 함수와 동일 결과 반환 (parity)
- 같은 정책 인스턴스 재사용 (stateless wrapper)
- ``to_proposal()`` 이 ``to_execution_proposal`` 위임 정확성
- broker / OrderExecutor import 0건 (정적 grep 재확인)
- ``is_order_intent=False`` invariant 그대로
"""

from __future__ import annotations

import pathlib

import pytest

from app.strategies.aggregator import (
    AggregatedAction,
    AggregatorPolicy,
    ConflictLevel,
    StrategySignalAggregator,
    StrategyVote,
    aggregate_signals,
    to_execution_proposal,
)
from app.strategies.base import SignalAction


def _vote(
    sid: str, sym: str = "005930",
    *,
    action: SignalAction = SignalAction.BUY,
    conf: int = 70, qual: int = 80,
    indicators: dict | None = None,
) -> StrategyVote:
    return StrategyVote(
        strategy_id=sid, symbol=sym, action=action,
        confidence=conf, quality_score=qual,
        indicators=indicators or {},
    )


# ====================================================================
# Facade ↔ module-level 함수 parity
# ====================================================================


def test_facade_aggregate_matches_module_function():
    """동일 입력에 대해 facade.aggregate() == aggregate_signals()."""
    votes = (
        _vote("volume_breakout",  conf=80, qual=85),
        _vote("pullback_rebreak", conf=75, qual=80),
    )
    facade = StrategySignalAggregator()
    via_facade = facade.aggregate(votes, market_regime="TREND_UP")
    via_func   = aggregate_signals(votes, market_regime="TREND_UP")

    assert len(via_facade.signals) == len(via_func.signals)
    for a, b in zip(via_facade.signals, via_func.signals):
        assert a.symbol == b.symbol
        assert a.final_action == b.final_action
        assert a.confidence == b.confidence
        assert a.quality_score == b.quality_score
        assert a.supporting_strategies == b.supporting_strategies


def test_facade_carries_custom_policy():
    """policy 인스턴스가 facade 내부에서 그대로 사용됨."""
    policy = AggregatorPolicy(
        min_quality_score_single_strategy=90,    # 매우 까다로움
        min_confidence_to_qualify=50,
    )
    facade = StrategySignalAggregator(policy=policy)
    out = facade.aggregate([_vote("volume_breakout", qual=80)])
    s = out.signals[0]
    # quality=80 < 90 → WATCH 강등.
    assert s.final_action == AggregatedAction.WATCH
    assert s.candidate_qualified is False


def test_facade_is_stateless_between_batches():
    """같은 facade 인스턴스로 여러 batch 처리해도 상태 누적 0건."""
    f = StrategySignalAggregator()
    out_a = f.aggregate([_vote("volume_breakout", conf=80, qual=80)])
    out_b = f.aggregate([_vote("volume_breakout", conf=80, qual=80)])
    assert out_a.signals[0].confidence == out_b.signals[0].confidence
    assert out_a.signals[0].final_action == out_b.signals[0].final_action


# ====================================================================
# Aggregation rules (요청 사항 — 본 파일에서도 facade 경유로 재검증)
# ====================================================================


def test_same_direction_two_strategies_boost_confidence_via_facade():
    f = StrategySignalAggregator()
    single = f.aggregate([_vote("volume_breakout", conf=60, qual=80)])
    pair   = f.aggregate([
        _vote("volume_breakout",  conf=60, qual=80),
        _vote("pullback_rebreak", conf=60, qual=80),
    ])
    assert pair.signals[0].confidence > single.signals[0].confidence


def test_vwap_exit_beats_buy_via_facade():
    f = StrategySignalAggregator()
    out = f.aggregate([
        _vote("volume_breakout",  action=SignalAction.BUY,  conf=60, qual=70),
        _vote("vwap_strategy",    action=SignalAction.EXIT, conf=80, qual=85),
    ])
    s = out.signals[0]
    assert s.final_action == AggregatedAction.EXIT
    assert s.recommended_strategy == "vwap_strategy"


def test_risk_off_blocks_buy_via_facade():
    f = StrategySignalAggregator()
    out = f.aggregate(
        [_vote("volume_breakout", conf=90, qual=90)],
        market_regime="RISK_OFF",
    )
    for s in out.signals:
        assert s.final_action != AggregatedAction.BUY


def test_high_conflict_blocks_candidate_via_facade():
    f = StrategySignalAggregator()
    out = f.aggregate([
        _vote("volume_breakout", action=SignalAction.BUY,  conf=80, qual=85),
        _vote("noise_short",     action=SignalAction.SELL, conf=80, qual=85),
    ])
    s = next(s for s in out.signals if s.final_action == AggregatedAction.BUY)
    assert s.conflict_level == ConflictLevel.HIGH
    assert s.candidate_qualified is False


def test_single_high_quality_is_candidate_via_facade():
    f = StrategySignalAggregator()
    out = f.aggregate([_vote("volume_breakout", conf=70, qual=85)])
    s = out.signals[0]
    assert s.final_action == AggregatedAction.BUY
    assert s.candidate_qualified is True


def test_single_low_quality_is_watch_via_facade():
    f = StrategySignalAggregator()
    out = f.aggregate([_vote("volume_breakout", conf=70, qual=50)])
    s = out.signals[0]
    assert s.final_action == AggregatedAction.WATCH
    assert s.candidate_qualified is False


def test_duplicate_symbol_collapsed_via_facade():
    f = StrategySignalAggregator()
    out = f.aggregate([
        _vote("volume_breakout",  sym="005930"),
        _vote("pullback_rebreak", sym="005930"),
    ])
    assert [s.symbol for s in out.signals] == ["005930"]


# ====================================================================
# to_proposal facade
# ====================================================================


def test_to_proposal_returns_none_for_unqualified_signal():
    f = StrategySignalAggregator()
    out = f.aggregate([_vote("volume_breakout", conf=70, qual=50)])
    s = out.signals[0]
    assert f.to_proposal(s) is None


def test_to_proposal_returns_advisory_payload():
    f = StrategySignalAggregator()
    out = f.aggregate([
        _vote("volume_breakout",  conf=80, qual=85),
        _vote("pullback_rebreak", conf=75, qual=80),
    ])
    s = out.signals[0]
    proposal = f.to_proposal(s, expires_in_seconds=300, default_quantity=10)
    assert proposal is not None
    # 주문이 아니라 advisory — invariant 그대로.
    assert proposal.is_order_intent is False
    assert proposal.can_execute_order is False


def test_to_proposal_module_level_helper_matches_facade():
    """to_execution_proposal() 와 facade.to_proposal() 결과 핵심 필드 동등."""
    f = StrategySignalAggregator()
    out = f.aggregate([
        _vote("volume_breakout",  conf=80, qual=85),
        _vote("pullback_rebreak", conf=75, qual=80),
    ])
    s = out.signals[0]
    p_facade = f.to_proposal(s, default_quantity=5)
    p_func   = to_execution_proposal(s, default_quantity=5)
    assert p_facade.symbol == p_func.symbol
    assert p_facade.side == p_func.side
    assert p_facade.confidence == p_func.confidence


# ====================================================================
# is_order_intent / is_order_signal invariant
# ====================================================================


def test_result_is_not_order_intent():
    out = StrategySignalAggregator().aggregate(
        [_vote("volume_breakout", conf=80, qual=85)]
    )
    assert out.is_order_intent is False
    for s in out.signals:
        assert s.is_order_intent is False


# ====================================================================
# 정적 grep 가드 (broker / OrderExecutor / route_order import 0건)
# ====================================================================


def _aggregator_source() -> str:
    p = pathlib.Path(__file__).parent.parent / "app" / "strategies" / "aggregator.py"
    return p.read_text(encoding="utf-8")


def test_aggregator_module_no_broker_import():
    src = _aggregator_source()
    for needle in [
        "from app.brokers.kis",
        "from app.brokers.mock_broker",
        "from app.brokers.futures_base",
        "from app.brokers.base import OrderRequest",
        "from app.brokers.base import BrokerAdapter",
    ]:
        assert needle not in src


def test_aggregator_module_no_executor_or_route_order():
    src = _aggregator_source()
    for needle in [
        "from app.execution.executor",
        "from app.execution.order_executor",
        "from app.execution.order_router",
        "route_order(",
        "= route_order",
        "broker.place_order(",
        "broker.cancel_order(",
    ]:
        assert needle not in src


def test_aggregator_module_no_permission_or_ai_assist():
    src = _aggregator_source()
    for needle in [
        "from app.permission",
        "import app.permission",
        "from app.ai.assist",
        "import app.ai.assist",
        "submit_candidate(",
    ]:
        assert needle not in src


def test_aggregator_module_no_external_http_or_ai():
    src = _aggregator_source()
    for needle in [
        "import anthropic", "from anthropic",
        "import openai",    "from openai",
        "import httpx",     "from httpx",
        "import requests",  "from requests",
    ]:
        assert needle not in src


# ====================================================================
# 새 facade class smoke
# ====================================================================


def test_facade_class_is_importable():
    from app.strategies.aggregator import StrategySignalAggregator as Imported
    assert Imported is StrategySignalAggregator
    assert callable(Imported)
    inst = Imported()
    assert hasattr(inst, "aggregate")
    assert hasattr(inst, "to_proposal")


def test_facade_default_policy_is_aggregator_policy_instance():
    f = StrategySignalAggregator()
    assert isinstance(f.policy, AggregatorPolicy)


def test_facade_rejects_invalid_vote_via_dataclass_guard():
    with pytest.raises(ValueError):
        # confidence 범위 위반.
        StrategyVote(
            strategy_id="x", symbol="y",
            action=SignalAction.BUY, confidence=200, quality_score=50,
        )
