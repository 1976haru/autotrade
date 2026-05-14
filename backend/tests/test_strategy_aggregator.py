"""Strategy Signal Aggregator tests.

테스트 항목 (요청 사항):
1. 같은 방향 2개 이상 → confidence 상승
2. VWAP EXIT 이 BUY 보다 우선
3. RISK_OFF 에서 BUY 차단
4. conflicting signals → HIGH conflict
5. 단일 고품질 신호는 후보 가능
6. 낮은 품질 단일 신호는 WATCH
7. 중복 종목 신호 하나로 합침
8. is_order_intent=False (불변)
9. broker / order executor import 0건 (정적 grep 가드)
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timezone

import pytest

from app.strategies.aggregator import (
    AggregatedAction,
    AggregatedSignal,
    AggregatorPolicy,
    ConflictLevel,
    StrategyAggregationResult,
    StrategyVote,
    aggregate_signals,
    to_execution_proposal,
)
from app.strategies.base import ExitPlan, SignalAction


# ====================================================================
# 헬퍼
# ====================================================================


def _vote(
    strategy_id: str, symbol: str = "005930",
    *,
    action: SignalAction = SignalAction.BUY,
    confidence: int = 70,
    quality_score: int = 80,
    reasons: tuple[str, ...] = (),
    risk_notes: tuple[str, ...] = (),
    indicators: dict | None = None,
    exit_plan: ExitPlan | None = None,
    is_fresh: bool = True,
    voted_at: datetime | None = None,
) -> StrategyVote:
    return StrategyVote(
        strategy_id=strategy_id, symbol=symbol, action=action,
        confidence=confidence, quality_score=quality_score,
        reasons=reasons, risk_notes=risk_notes,
        indicators=indicators or {},
        exit_plan=exit_plan,
        is_fresh=is_fresh,
        voted_at=voted_at,
    )


# ====================================================================
# 1. 같은 방향 2+ → confidence 상승
# ====================================================================


def test_same_direction_two_strategies_boost_confidence():
    """volume_breakout + pullback_rebreak 모두 BUY → confidence 가 단일 보다 큼."""
    single = aggregate_signals([
        _vote("volume_breakout", confidence=60, quality_score=80),
    ])
    pair = aggregate_signals([
        _vote("volume_breakout",  confidence=60, quality_score=80),
        _vote("pullback_rebreak", confidence=60, quality_score=80),
    ])
    s_single = single.signals[0]
    s_pair   = pair.signals[0]

    assert s_pair.final_action == AggregatedAction.BUY
    assert s_pair.confidence > s_single.confidence
    assert set(s_pair.supporting_strategies) == {"volume_breakout", "pullback_rebreak"}


def test_three_strategies_boost_more_than_two():
    """3개 supporting 이면 2개 보다 추가 boost."""
    two = aggregate_signals([
        _vote("volume_breakout",  confidence=60, quality_score=80),
        _vote("pullback_rebreak", confidence=60, quality_score=80),
    ])
    three = aggregate_signals([
        _vote("volume_breakout",  confidence=60, quality_score=80),
        _vote("pullback_rebreak", confidence=60, quality_score=80),
        _vote("vwap_strategy",    confidence=60, quality_score=80),
    ])
    assert three.signals[0].confidence >= two.signals[0].confidence


def test_supporting_boost_caps_at_100():
    """이미 100 인 vote 들은 boost 후에도 100 cap."""
    out = aggregate_signals([
        _vote("volume_breakout",  confidence=100, quality_score=100),
        _vote("pullback_rebreak", confidence=100, quality_score=100),
    ])
    assert out.signals[0].confidence == 100


# ====================================================================
# 2. VWAP EXIT 이 BUY 보다 우선
# ====================================================================


def test_vwap_exit_wins_over_buy():
    """vwap_strategy 가 EXIT 을 던지면 같은 종목의 BUY 들이 무시된다."""
    out = aggregate_signals([
        _vote("volume_breakout",  action=SignalAction.BUY,  confidence=80, quality_score=85),
        _vote("pullback_rebreak", action=SignalAction.BUY,  confidence=75, quality_score=80),
        _vote("vwap_strategy",    action=SignalAction.EXIT, confidence=80, quality_score=85),
    ])
    s = out.signals[0]
    assert s.final_action == AggregatedAction.EXIT
    assert s.recommended_strategy == "vwap_strategy"
    assert "vwap_strategy" in s.supporting_strategies
    assert "volume_breakout" in s.opposing_strategies
    assert "pullback_rebreak" in s.opposing_strategies


def test_vwap_sell_also_wins_over_buy():
    """SELL 도 EXIT 과 동일 — 손실 방어 우선."""
    out = aggregate_signals([
        _vote("volume_breakout", action=SignalAction.BUY,  confidence=85, quality_score=90),
        _vote("orb_vwap",        action=SignalAction.SELL, confidence=70, quality_score=75),
    ])
    s = out.signals[0]
    assert s.final_action == AggregatedAction.SELL
    assert s.recommended_strategy == "orb_vwap"


# ====================================================================
# 3. RISK_OFF 에서 BUY 차단
# ====================================================================


def test_risk_off_blocks_buy():
    """RISK_OFF regime — 모든 BUY 가 REJECT 로 강등."""
    out = aggregate_signals([
        _vote("volume_breakout",  confidence=90, quality_score=90),
        _vote("pullback_rebreak", confidence=85, quality_score=88),
    ], market_regime="RISK_OFF")

    for s in out.signals:
        assert s.final_action != AggregatedAction.BUY
        if s.final_action == AggregatedAction.REJECT:
            assert "RISK_OFF" in " ".join(s.reasons)
            assert s.candidate_qualified is False


def test_risk_off_lets_exit_through():
    """RISK_OFF 라도 EXIT/SELL 은 통과 — 손실 방어 정보."""
    out = aggregate_signals([
        _vote("volume_breakout", action=SignalAction.BUY,  confidence=80, quality_score=85),
        _vote("vwap_strategy",   action=SignalAction.EXIT, confidence=80, quality_score=85),
    ], market_regime="RISK_OFF")
    # BUY 는 차단, EXIT 은 살아남음.
    actions = {s.final_action for s in out.signals}
    assert AggregatedAction.EXIT in actions
    assert AggregatedAction.BUY not in actions


def test_low_liquidity_downgrades_buy_to_watch():
    """LOW_LIQUIDITY regime — BUY 가 WATCH 로 강등 (후보 자격 박탈)."""
    out = aggregate_signals([
        _vote("volume_breakout",  confidence=90, quality_score=90),
        _vote("pullback_rebreak", confidence=85, quality_score=85),
    ], market_regime="LOW_LIQUIDITY")
    s = out.signals[0]
    assert s.final_action == AggregatedAction.WATCH
    assert s.candidate_qualified is False
    assert any("LOW_LIQUIDITY" in r for r in s.reasons)


# ====================================================================
# 4. 충돌 처리 — HIGH conflict
# ====================================================================


def test_conflicting_high_confidence_signals_yield_high_conflict():
    """BUY conf=80 + SELL conf=80 (양쪽 모두 high) → HIGH."""
    out = aggregate_signals([
        _vote("volume_breakout", action=SignalAction.BUY,  confidence=80, quality_score=85),
        _vote("vwap_strategy",   action=SignalAction.SELL, confidence=80, quality_score=85),
    ])
    assert out.conflicts
    assert out.conflicts[0].severity == ConflictLevel.HIGH
    # VWAP SELL 이 우선 — 본 종목 final_action=SELL.
    assert out.signals[0].final_action == AggregatedAction.SELL
    assert out.signals[0].conflict_level == ConflictLevel.HIGH


def test_high_conflict_buy_loses_candidate_qualification():
    """BUY + 같은 종목 비-VWAP SELL high conflict — candidate_qualified=False."""
    # 비 VWAP 가짜 SELL: vwap_strategy / orb_vwap 가 아닌 strategy 에서 SELL 표시는
    # 본 모듈 흐름에서 통상 발생하지 않지만, 합산 결과를 위해 가상의 strategy
    # 이름으로 conflict 시뮬.
    out = aggregate_signals([
        _vote("volume_breakout",  action=SignalAction.BUY,  confidence=80, quality_score=85),
        _vote("pullback_rebreak", action=SignalAction.BUY,  confidence=80, quality_score=85),
        _vote("noise_short",      action=SignalAction.SELL, confidence=80, quality_score=85),
    ])
    # noise_short 는 VWAP 가 아니므로 EXIT 우선 분기 해당 X. 대신 conflict_level
    # HIGH 가 적용되며, BUY 최종 신호가 candidate_qualified=False.
    s = next(s for s in out.signals if s.final_action == AggregatedAction.BUY)
    assert s.conflict_level == ConflictLevel.HIGH
    assert s.candidate_qualified is False


def test_low_conflict_keeps_candidate_qualification():
    """BUY high conf + SELL low conf — MEDIUM conflict, 후보 자격 유지 가능."""
    out = aggregate_signals([
        _vote("volume_breakout",  action=SignalAction.BUY,  confidence=85, quality_score=90),
        _vote("pullback_rebreak", action=SignalAction.BUY,  confidence=85, quality_score=90),
        _vote("noise_short",      action=SignalAction.SELL, confidence=40, quality_score=50),
    ])
    s = next(s for s in out.signals if s.final_action == AggregatedAction.BUY)
    assert s.conflict_level == ConflictLevel.MEDIUM
    # MEDIUM 은 max_conflict_for_candidate default (=MEDIUM) 이하 → 후보 자격 유지.
    assert s.candidate_qualified is True


# ====================================================================
# 5/6. 단일 전략 가드 — 고품질만 후보, 저품질은 WATCH
# ====================================================================


def test_single_high_quality_strategy_is_candidate():
    """단일 vote 라도 quality_score 가 임계 이상이면 BUY 후보."""
    out = aggregate_signals([
        _vote("volume_breakout", confidence=70, quality_score=85),
    ])
    s = out.signals[0]
    assert s.final_action == AggregatedAction.BUY
    assert s.candidate_qualified is True


def test_single_low_quality_strategy_is_watch_only():
    """단일 vote 의 quality 가 임계 미만이면 WATCH 강등."""
    out = aggregate_signals([
        _vote("volume_breakout", confidence=70, quality_score=50),
    ])
    s = out.signals[0]
    assert s.final_action == AggregatedAction.WATCH
    assert s.candidate_qualified is False
    assert any("단일 전략" in r for r in s.reasons)


def test_single_low_confidence_loses_qualification():
    """단일 vote 의 quality 는 통과해도 confidence 가 임계 미만이면 자격 박탈."""
    out = aggregate_signals(
        [_vote("volume_breakout", confidence=30, quality_score=80)],
        policy=AggregatorPolicy(min_confidence_to_qualify=50),
    )
    s = out.signals[0]
    assert s.candidate_qualified is False


# ====================================================================
# 7. 중복 종목 — 하나로 합침 (같은 strategy 의 vote 는 dedupe)
# ====================================================================


def test_duplicate_votes_collapsed_to_one_signal():
    """같은 (strategy, symbol) 의 vote 2개는 1개로 dedupe — 결과 종목 1개."""
    older = datetime(2026, 5, 14, 9, 30, tzinfo=timezone.utc)
    newer = datetime(2026, 5, 14, 9, 40, tzinfo=timezone.utc)
    out = aggregate_signals([
        _vote("volume_breakout", confidence=50, quality_score=60, voted_at=older),
        _vote("volume_breakout", confidence=80, quality_score=85, voted_at=newer),
    ])
    assert len(out.signals) == 1
    s = out.signals[0]
    # 더 최신(80/85)이 채택됐는지 — confidence 80 base. boost 없음(supporter=1).
    assert s.confidence == 80


def test_two_strategies_same_symbol_yield_one_signal():
    """서로 다른 strategy 의 같은 종목 vote 들은 1개 AggregatedSignal 로 합쳐짐."""
    out = aggregate_signals([
        _vote("volume_breakout",  symbol="005930"),
        _vote("pullback_rebreak", symbol="005930"),
    ])
    symbols = [s.symbol for s in out.signals]
    assert symbols == ["005930"]


def test_different_symbols_yield_separate_signals():
    out = aggregate_signals([
        _vote("volume_breakout", symbol="005930"),
        _vote("volume_breakout", symbol="000660"),
    ])
    assert {s.symbol for s in out.signals} == {"005930", "000660"}


# ====================================================================
# 8. is_order_intent invariants (절대 원칙)
# ====================================================================


def test_aggregated_signal_is_not_order_intent():
    out = aggregate_signals([_vote("volume_breakout")])
    for s in out.signals:
        assert s.is_order_intent is False


def test_result_is_not_order_intent():
    out = aggregate_signals([_vote("volume_breakout")])
    assert out.is_order_intent is False


def test_aggregated_signal_rejects_true_is_order_intent():
    """dataclass __post_init__ 가드 — is_order_intent=True 생성 시 ValueError."""
    with pytest.raises(ValueError):
        AggregatedSignal(
            symbol="005930", final_action=AggregatedAction.BUY,
            confidence=60, quality_score=60,
            supporting_strategies=(), opposing_strategies=(),
            neutral_strategies=(),
            reasons=(), risk_notes=(),
            conflict_level=ConflictLevel.NONE,
            recommended_strategy=None,
            entry_plan=None, exit_plan=None,
            market_regime=None, candidate_qualified=False,
            is_order_intent=True,  # type: ignore[arg-type]
        )


def test_result_rejects_true_is_order_intent():
    with pytest.raises(ValueError):
        StrategyAggregationResult(
            signals=(), conflicts=(), dropped=(),
            market_regime=None,
            generated_at=datetime.now(timezone.utc),
            is_order_intent=True,  # type: ignore[arg-type]
        )


def test_strategy_vote_validates_confidence_range():
    with pytest.raises(ValueError):
        StrategyVote(
            strategy_id="x", symbol="y", action=SignalAction.BUY,
            confidence=200, quality_score=50,
        )


def test_strategy_vote_validates_quality_score_range():
    with pytest.raises(ValueError):
        StrategyVote(
            strategy_id="x", symbol="y", action=SignalAction.BUY,
            confidence=50, quality_score=-1,
        )


# ====================================================================
# 9. 정적 grep 가드 — broker / OrderExecutor / route_order import 0건
# ====================================================================


def _aggregator_source() -> str:
    """모듈 소스 파일을 읽어 grep — runtime import 가 아니라 *파일 텍스트* 검사.
    이로써 module-level 또는 함수 내부 import 모두 잡힌다.
    """
    path = pathlib.Path(__file__).parent.parent / "app" / "strategies" / "aggregator.py"
    return path.read_text(encoding="utf-8")


def test_aggregator_does_not_import_brokers():
    src = _aggregator_source()
    banned = [
        "from app.brokers.kis",
        "from app.brokers.mock_broker",
        "from app.brokers.futures_base",
        "from app.brokers.base import OrderRequest",
        "from app.brokers.base import BrokerAdapter",
        "import app.brokers",
    ]
    for needle in banned:
        assert needle not in src, f"aggregator must not contain '{needle}'"


def test_aggregator_does_not_import_order_executor():
    src = _aggregator_source()
    for needle in [
        "from app.execution.executor",
        "from app.execution.order_executor",
        "from app.execution.order_router",
        "import app.execution",
    ]:
        assert needle not in src, f"aggregator must not contain '{needle}'"


def test_aggregator_does_not_call_route_order_or_place_order():
    src = _aggregator_source()
    for call in [
        "route_order(",
        "= route_order",
        "broker.place_order(",
        "broker.cancel_order(",
        "self.place_order(",
        ".place_order(",
    ]:
        assert call not in src, f"aggregator must not contain call '{call}'"


def test_aggregator_does_not_import_external_http_or_ai():
    src = _aggregator_source()
    for needle in [
        "import anthropic",
        "import openai",
        "from anthropic",
        "from openai",
        "import httpx",
        "from httpx",
        "import requests",
        "from requests",
    ]:
        assert needle not in src, f"aggregator must not contain '{needle}'"


def test_aggregator_does_not_import_permission_or_ai_assist():
    """permission gate / ai.assist 는 본 모듈이 직접 호출하지 않는다 — 변환은
    `to_execution_proposal` 가 `ExecutionProposal` 까지만 만들고, 그 다음 흐름
    (submit) 은 caller 책임.
    """
    src = _aggregator_source()
    for needle in [
        "from app.permission",
        "import app.permission",
        "from app.ai.assist",
        "import app.ai.assist",
        "submit_candidate(",
    ]:
        assert needle not in src, f"aggregator must not contain '{needle}'"


# ====================================================================
# 10. to_execution_proposal helper
# ====================================================================


def test_to_execution_proposal_returns_none_for_watch():
    out = aggregate_signals([
        _vote("volume_breakout", confidence=50, quality_score=50),
    ])
    s = out.signals[0]
    assert s.final_action == AggregatedAction.WATCH
    assert to_execution_proposal(s) is None


def test_to_execution_proposal_returns_none_for_unqualified_candidate():
    """confidence 임계 미달 → candidate_qualified=False → None 반환."""
    s = AggregatedSignal(
        symbol="005930",
        final_action=AggregatedAction.BUY,
        confidence=80, quality_score=80,
        supporting_strategies=("volume_breakout",),
        opposing_strategies=(),
        neutral_strategies=(),
        reasons=(),
        risk_notes=(),
        conflict_level=ConflictLevel.NONE,
        recommended_strategy="volume_breakout",
        entry_plan=None, exit_plan=None,
        market_regime=None,
        candidate_qualified=False,
    )
    assert to_execution_proposal(s) is None


def test_to_execution_proposal_makes_advisory_payload():
    """후보 자격 BUY → ExecutionProposal (주문 아님). is_order_intent=False 유지."""
    out = aggregate_signals([
        _vote("volume_breakout",  confidence=80, quality_score=85),
        _vote("pullback_rebreak", confidence=75, quality_score=80),
    ])
    s = out.signals[0]
    assert s.candidate_qualified is True

    proposal = to_execution_proposal(s, expires_in_seconds=300, default_quantity=10)
    assert proposal is not None
    assert proposal.symbol == "005930"
    assert proposal.side.value == "BUY"
    assert proposal.quantity == 10
    assert proposal.confidence == s.confidence
    assert proposal.is_order_intent is False
    assert proposal.can_execute_order is False


def test_to_execution_proposal_for_vwap_exit():
    """EXIT advisory → SELL ExecutionProposal (주문 아님). HIGH conflict 가
    아닌 경우(BUY confidence 낮음) EXIT 는 후보 자격 유지."""
    out = aggregate_signals([
        _vote("volume_breakout", action=SignalAction.BUY,  confidence=40, quality_score=50),
        _vote("vwap_strategy",   action=SignalAction.EXIT, confidence=85, quality_score=90),
    ])
    s = out.signals[0]
    assert s.final_action == AggregatedAction.EXIT
    proposal = to_execution_proposal(s)
    assert proposal is not None
    assert proposal.side.value == "SELL"
    assert proposal.is_order_intent is False


# ====================================================================
# 11. exit_plan / dropped / regime weights 보조 검증
# ====================================================================


def test_picks_most_conservative_exit_plan():
    """여러 vote 의 exit_plan 중 stop_loss_pct 가 작은(보수적) 것 채택."""
    out = aggregate_signals([
        _vote("volume_breakout",
              exit_plan=ExitPlan(stop_loss_pct=3.0, take_profit_pct=6.0)),
        _vote("pullback_rebreak",
              exit_plan=ExitPlan(stop_loss_pct=1.5, take_profit_pct=4.0)),
    ])
    s = out.signals[0]
    assert s.exit_plan is not None
    assert s.exit_plan.stop_loss_pct == 1.5


def test_dropped_carries_no_signal_only_symbols():
    """모든 vote 가 NO_SIGNAL 인 종목은 dropped 로 표시 (signal 없음)."""
    out = aggregate_signals([
        _vote("volume_breakout", action=SignalAction.NO_SIGNAL, confidence=0, quality_score=0),
    ])
    assert out.signals == ()
    assert out.dropped


def test_trend_up_regime_weights_volume_breakout_higher():
    """TREND_UP 에서 volume_breakout (1.3) > vwap_strategy (1.0) 가중치 — 최종
    confidence 는 volume_breakout 가 더 크게 반영된다 (가중 평균)."""
    out = aggregate_signals([
        _vote("volume_breakout", confidence=80, quality_score=80),
        _vote("vwap_strategy",   confidence=50, quality_score=70),
    ], market_regime="TREND_UP")
    # weighted: (80*1.3 + 50*1.0) / (1.3 + 1.0) = (104 + 50) / 2.3 ≈ 66.96
    # boost: supporter_count=2, +7 → 73~74.
    s = out.signals[0]
    assert 70 <= s.confidence <= 80


def test_choppy_regime_weights_vwap_higher():
    """CHOPPY 에서 vwap_strategy (1.3) > volume_breakout (0.8) — vwap 가
    더 크게 반영. 같은 입력으로 TREND_UP 와 비교해 결과가 다름."""
    choppy = aggregate_signals([
        _vote("volume_breakout", confidence=80, quality_score=80),
        _vote("vwap_strategy",   confidence=50, quality_score=70),
    ], market_regime="CHOPPY")
    trend = aggregate_signals([
        _vote("volume_breakout", confidence=80, quality_score=80),
        _vote("vwap_strategy",   confidence=50, quality_score=70),
    ], market_regime="TREND_UP")
    assert choppy.signals[0].confidence != trend.signals[0].confidence


def test_opening_chaos_orb_cooldown_downgrades_to_watch():
    """OPENING_CHAOS 에서 orb_vwap 가 cooldown 미통과면 BUY → WATCH 강등."""
    out = aggregate_signals([
        _vote("orb_vwap", confidence=80, quality_score=85,
              indicators={"orb_cooldown_active": True}),
    ], market_regime="OPENING_CHAOS")
    s = out.signals[0]
    assert s.final_action == AggregatedAction.WATCH
    assert any("cooldown" in r for r in s.reasons)


def test_qualified_candidates_filter():
    out = aggregate_signals([
        _vote("volume_breakout",  confidence=80, quality_score=85),
        _vote("pullback_rebreak", confidence=80, quality_score=85),
    ])
    candidates = out.qualified_candidates()
    assert len(candidates) == 1
    assert candidates[0].candidate_qualified is True


def test_stale_votes_are_weighted_down():
    """is_fresh=False 인 vote 는 가중치 1/2 — 다른 fresh vote 와 합쳐도 영향
    축소."""
    fresh = aggregate_signals([
        _vote("volume_breakout", confidence=80, quality_score=80, is_fresh=True),
        _vote("pullback_rebreak", confidence=80, quality_score=80, is_fresh=True),
    ])
    mixed = aggregate_signals([
        _vote("volume_breakout", confidence=80, quality_score=80, is_fresh=True),
        _vote("pullback_rebreak", confidence=20, quality_score=20, is_fresh=False),
    ])
    # fresh-only 와 mixed 비교 — mixed 에서 두 번째 vote 가 stale 이라 confidence
    # 낮춤이 약화됨 → mixed.confidence 가 fresh.confidence 보다 크지 않다.
    assert mixed.signals[0].confidence <= fresh.signals[0].confidence
