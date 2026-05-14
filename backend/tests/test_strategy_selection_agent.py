"""Strategy Selection Agent tests (#85).

요청 항목 매핑:
- 같은 방향 2개 이상 → confidence 상승 (aggregator delegate, 본 파일에서 재확인)
- VWAP EXIT 이 BUY 보다 우선
- RISK_OFF 에서 BUY 차단
- conflicting signals → HIGH conflict
- 단일 고품질 신호 → 후보 가능 + selected_strategy 산출
- 낮은 품질 단일 신호 → WATCH
- 중복 symbol 신호 하나로 합침
- selected_strategy 산출
- blocked_strategy reason 표시
- is_order_intent=False / is_order_signal=False / can_execute_order=False
- broker / order executor import 0건
- AgentBase 호환 (run() returns AgentOutput)
"""

from __future__ import annotations

import pathlib

import pytest

from app.agents.base import (
    AgentContext,
    AgentDecision,
    AgentOutput,
    AgentRole,
)
from app.agents.strategy_selection_agent import (
    BlockedReason,
    BlockedStrategyEntry,
    StrategyCandidate,
    StrategySelectionAgent,
    StrategySelectionInput,
    StrategySelectionReport,
    select_strategies,
    to_execution_proposal_from_selection,
)
from app.strategies.aggregator import (
    AggregatedAction,
    ConflictLevel,
    StrategyVote,
)
from app.strategies.base import SignalAction


def _vote(
    sid: str, sym: str = "005930",
    *,
    action: SignalAction = SignalAction.BUY,
    conf: int = 70, qual: int = 80,
    indicators: dict | None = None,
    is_fresh: bool = True,
) -> StrategyVote:
    return StrategyVote(
        strategy_id=sid, symbol=sym, action=action,
        confidence=conf, quality_score=qual,
        indicators=indicators or {},
        is_fresh=is_fresh,
    )


# ====================================================================
# selected_strategy 산출
# ====================================================================


def test_two_strategies_yield_selected_strategy():
    """volume_breakout + pullback_rebreak BUY → selected_strategy 산출."""
    out = select_strategies(StrategySelectionInput(
        votes=(
            _vote("volume_breakout",  conf=80, qual=85),
            _vote("pullback_rebreak", conf=75, qual=80),
        ),
    ))
    assert out.final_action == AggregatedAction.BUY
    assert out.candidate_qualified is True
    assert out.selected_strategy in {"volume_breakout", "pullback_rebreak"}


def test_selected_strategy_is_none_when_unqualified():
    """단일 저품질 신호 — selected_strategy=None, candidates 는 carry."""
    out = select_strategies(StrategySelectionInput(
        votes=(_vote("volume_breakout", conf=70, qual=40),),
    ))
    assert out.selected_strategy is None
    assert out.final_action == AggregatedAction.WATCH
    # candidates 에는 vote 가 carry 되어 있어 운영자가 reason 확인 가능.
    assert out.candidates
    assert out.candidates[0].strategy_id == "volume_breakout"


def test_trend_up_regime_picks_volume_breakout_over_vwap():
    """TREND_UP 가중치 — volume_breakout(1.3) > vwap_strategy(1.0)."""
    out = select_strategies(StrategySelectionInput(
        votes=(
            _vote("volume_breakout", conf=70, qual=80),
            _vote("vwap_strategy",   conf=70, qual=80),
        ),
        market_regime="TREND_UP",
    ))
    assert out.selected_strategy == "volume_breakout"


def test_choppy_regime_picks_vwap_strategy():
    """CHOPPY 가중치 — vwap_strategy(1.3) > volume_breakout(0.8)."""
    out = select_strategies(StrategySelectionInput(
        votes=(
            _vote("volume_breakout", conf=70, qual=80),
            _vote("vwap_strategy",   conf=70, qual=80),
        ),
        market_regime="CHOPPY",
    ))
    assert out.selected_strategy == "vwap_strategy"


# ====================================================================
# blocked_strategy reason 표시
# ====================================================================


def test_blocked_carries_risk_off_reason():
    """RISK_OFF — BUY vote 들은 모두 RISK_OFF_REGIME 사유로 blocked."""
    out = select_strategies(StrategySelectionInput(
        votes=(
            _vote("volume_breakout",  conf=80, qual=85),
            _vote("pullback_rebreak", conf=75, qual=80),
        ),
        market_regime="RISK_OFF",
    ))
    assert out.final_action in (
        AggregatedAction.REJECT, AggregatedAction.NO_SIGNAL,
    )
    assert out.selected_strategy is None
    # 모든 BUY vote 는 blocked 목록에 RISK_OFF_REGIME 사유로 등장.
    risk_off_blocks = [
        b for b in out.blocked if b.reason == BlockedReason.RISK_OFF_REGIME
    ]
    assert len(risk_off_blocks) == 2
    assert {b.strategy_id for b in risk_off_blocks} == {
        "volume_breakout", "pullback_rebreak",
    }


def test_blocked_carries_opposing_vwap_priority_reason():
    """VWAP EXIT 우선 — BUY vote 들은 OPPOSING_VWAP_PRIORITY 로 blocked."""
    out = select_strategies(StrategySelectionInput(
        votes=(
            _vote("volume_breakout", action=SignalAction.BUY,  conf=40, qual=50),
            _vote("vwap_strategy",   action=SignalAction.EXIT, conf=85, qual=90),
        ),
    ))
    assert out.final_action == AggregatedAction.EXIT
    # vwap_strategy 는 supporting, volume_breakout 은 opposing 으로 blocked.
    blocked_ids = {b.strategy_id: b for b in out.blocked}
    assert "volume_breakout" in blocked_ids
    assert blocked_ids["volume_breakout"].reason == BlockedReason.OPPOSING_VWAP_PRIORITY


def test_blocked_carries_low_quality_reason():
    """단일 vote quality 낮음 → WATCH 강등 + 후보 자격 없음."""
    out = select_strategies(StrategySelectionInput(
        votes=(_vote("volume_breakout", conf=70, qual=40),),
    ))
    # 본 경우 vote 자체가 supporting (long) 이지만 quality 가 낮아 최종 WATCH.
    # 본 블록은 합산 결과 single low-quality 가드로 처리됨 — 운영자는 reasons
    # 에서 "단일 전략 quality" 사유를 봐야 한다.
    assert out.final_action == AggregatedAction.WATCH
    assert out.candidate_qualified is False
    assert any("단일 전략" in r for r in out.reasons)


def test_blocked_carries_orb_cooldown_reason():
    """OPENING_CHAOS + orb cooldown — ORB_COOLDOWN_ACTIVE 사유 carry."""
    out = select_strategies(StrategySelectionInput(
        votes=(
            _vote("orb_vwap", conf=80, qual=85,
                  indicators={"orb_cooldown_active": True}),
        ),
        market_regime="OPENING_CHAOS",
    ))
    assert out.final_action == AggregatedAction.WATCH
    orb_blocks = [
        b for b in out.blocked if b.reason == BlockedReason.ORB_COOLDOWN_ACTIVE
    ]
    assert len(orb_blocks) == 1
    assert orb_blocks[0].strategy_id == "orb_vwap"


def test_blocked_carries_watch_only_reason():
    """다른 전략이 BUY, 한 전략은 WATCH → WATCH 사유로 blocked."""
    out = select_strategies(StrategySelectionInput(
        votes=(
            _vote("volume_breakout",  conf=80, qual=85),
            _vote("pullback_rebreak", action=SignalAction.WATCH, conf=30, qual=50),
        ),
    ))
    watch_blocks = [
        b for b in out.blocked if b.reason == BlockedReason.WATCH_ONLY
    ]
    assert any(b.strategy_id == "pullback_rebreak" for b in watch_blocks)


# ====================================================================
# 합산 규칙 — agent 경유에서도 동일하게 적용
# ====================================================================


def test_same_direction_two_strategies_boost_confidence_via_agent():
    single = select_strategies(StrategySelectionInput(
        votes=(_vote("volume_breakout", conf=60, qual=80),),
    ))
    pair = select_strategies(StrategySelectionInput(
        votes=(
            _vote("volume_breakout",  conf=60, qual=80),
            _vote("pullback_rebreak", conf=60, qual=80),
        ),
    ))
    assert pair.confidence > single.confidence


def test_vwap_exit_overrides_buy_via_agent():
    out = select_strategies(StrategySelectionInput(
        votes=(
            _vote("volume_breakout", action=SignalAction.BUY,  conf=80, qual=85),
            _vote("pullback_rebreak", action=SignalAction.BUY, conf=75, qual=80),
            _vote("vwap_strategy",   action=SignalAction.EXIT, conf=80, qual=85),
        ),
    ))
    assert out.final_action == AggregatedAction.EXIT
    # supporting = vwap_strategy. opposing = BUY 들.
    assert out.aggregated_signal is not None
    assert "vwap_strategy" in out.aggregated_signal.supporting_strategies


def test_high_conflict_disqualifies_candidate_via_agent():
    out = select_strategies(StrategySelectionInput(
        votes=(
            _vote("volume_breakout",  conf=80, qual=85),
            _vote("pullback_rebreak", conf=80, qual=85),
            _vote("noise_short",      action=SignalAction.SELL, conf=80, qual=85),
        ),
    ))
    # noise_short 는 VWAP 가 아니므로 BUY 가 최종 신호 — 단 HIGH conflict 로 자격 박탈.
    assert out.conflict_level == ConflictLevel.HIGH
    assert out.candidate_qualified is False


def test_duplicate_symbol_collapsed_to_single_report():
    """같은 종목 vote 여러 개 → 1개 리포트 (focus_symbol 단일 산출)."""
    out = select_strategies(StrategySelectionInput(
        votes=(
            _vote("volume_breakout",  sym="005930"),
            _vote("pullback_rebreak", sym="005930"),
        ),
    ))
    assert out.symbol == "005930"
    # candidates 는 같은 symbol vote 만 carry.
    assert all(c.symbol == "005930" for c in out.candidates)


def test_focus_symbol_selects_specific_symbol():
    """focus_symbol 명시 시 그 종목의 신호만 리포트."""
    out = select_strategies(StrategySelectionInput(
        votes=(
            _vote("volume_breakout",  sym="005930", conf=80, qual=85),
            _vote("pullback_rebreak", sym="000660", conf=80, qual=85),
        ),
        focus_symbol="000660",
    ))
    assert out.symbol == "000660"


# ====================================================================
# invariants — is_order_intent / is_order_signal / can_execute_order
# ====================================================================


def test_report_is_not_order_intent():
    out = select_strategies(StrategySelectionInput(
        votes=(_vote("volume_breakout", conf=80, qual=85),),
    ))
    assert out.is_order_intent is False
    assert out.is_order_signal is False
    assert out.can_execute_order is False


def test_report_rejects_true_is_order_intent():
    with pytest.raises(ValueError):
        StrategySelectionReport(
            symbol="005930", market_regime=None,
            selected_strategy=None,
            final_action=AggregatedAction.NO_SIGNAL,
            confidence=0, quality_score=0,
            conflict_level=ConflictLevel.NONE,
            candidate_qualified=False,
            candidates=(), blocked=(),
            reasons=(), risk_notes=(),
            aggregated_signal=None,
            is_order_intent=True,  # invariant 위반
        )


def test_report_rejects_true_is_order_signal():
    with pytest.raises(ValueError):
        StrategySelectionReport(
            symbol=None, market_regime=None, selected_strategy=None,
            final_action=AggregatedAction.NO_SIGNAL,
            confidence=0, quality_score=0,
            conflict_level=ConflictLevel.NONE,
            candidate_qualified=False,
            candidates=(), blocked=(),
            reasons=(), risk_notes=(),
            aggregated_signal=None,
            is_order_signal=True,
        )


def test_report_rejects_true_can_execute_order():
    with pytest.raises(ValueError):
        StrategySelectionReport(
            symbol=None, market_regime=None, selected_strategy=None,
            final_action=AggregatedAction.NO_SIGNAL,
            confidence=0, quality_score=0,
            conflict_level=ConflictLevel.NONE,
            candidate_qualified=False,
            candidates=(), blocked=(),
            reasons=(), risk_notes=(),
            aggregated_signal=None,
            can_execute_order=True,
        )


# ====================================================================
# AgentBase 호환
# ====================================================================


def test_agent_metadata_exposes_role_and_forbidden():
    agent = StrategySelectionAgent()
    md = agent.metadata
    assert md.name == "StrategySelectionAgent"
    assert md.role == AgentRole.STRATEGY_RESEARCHER
    assert md.can_execute_order is False
    forbidden = " ".join(md.forbidden)
    assert "broker" in forbidden.lower()
    assert "executor" in forbidden.lower() or "route_order" in forbidden.lower()


def test_agent_run_returns_agent_output_with_no_input():
    """입력 없이 run() 호출 — NO_OP / NO_SIGNAL 리포트, AgentOutput 형식."""
    agent = StrategySelectionAgent()
    out = agent.run(AgentContext())
    assert isinstance(out, AgentOutput)
    assert out.is_order_intent is False
    assert out.can_execute_order is False
    assert out.role == AgentRole.STRATEGY_RESEARCHER


def test_agent_run_carries_recommendation_decision():
    """후보 자격 BUY → AgentDecision.RECOMMEND."""
    agent = StrategySelectionAgent()
    ctx = AgentContext(extra={
        "strategy_selection_input": StrategySelectionInput(
            votes=(
                _vote("volume_breakout",  conf=80, qual=85),
                _vote("pullback_rebreak", conf=75, qual=80),
            ),
        ),
    })
    out = agent.run(ctx)
    assert out.decision == AgentDecision.RECOMMEND
    assert "metadata" not in out.metadata  # to_dict 결과를 carry
    assert out.metadata["selected_strategy"] in {"volume_breakout", "pullback_rebreak"}


def test_agent_run_carries_warn_for_vwap_exit():
    """VWAP EXIT 우선 → WARN (손실 방어 advisory)."""
    agent = StrategySelectionAgent()
    ctx = AgentContext(extra={
        "strategy_selection_input": StrategySelectionInput(
            votes=(
                _vote("volume_breakout", action=SignalAction.BUY,  conf=40, qual=50),
                _vote("vwap_strategy",   action=SignalAction.EXIT, conf=80, qual=85),
            ),
        ),
    })
    out = agent.run(ctx)
    assert out.decision == AgentDecision.WARN


def test_agent_run_carries_reject_for_risk_off():
    """RISK_OFF + BUY → REJECT."""
    agent = StrategySelectionAgent()
    ctx = AgentContext(extra={
        "strategy_selection_input": StrategySelectionInput(
            votes=(_vote("volume_breakout", conf=80, qual=85),),
            market_regime="RISK_OFF",
        ),
    })
    out = agent.run(ctx)
    assert out.decision == AgentDecision.REJECT
    assert "regime:RISK_OFF" in out.risk_flags


# ====================================================================
# ExecutionRecommender 연계 helper
# ====================================================================


def test_to_execution_proposal_from_selection_returns_none_when_unqualified():
    out = select_strategies(StrategySelectionInput(
        votes=(_vote("volume_breakout", conf=70, qual=40),),
    ))
    assert to_execution_proposal_from_selection(out) is None


def test_to_execution_proposal_from_selection_advisory_payload():
    """후보 자격 BUY → ExecutionProposal (주문 아님)."""
    out = select_strategies(StrategySelectionInput(
        votes=(
            _vote("volume_breakout",  conf=80, qual=85),
            _vote("pullback_rebreak", conf=75, qual=80),
        ),
    ))
    proposal = to_execution_proposal_from_selection(
        out, expires_in_seconds=300, default_quantity=10,
    )
    assert proposal is not None
    assert proposal.symbol == "005930"
    assert proposal.is_order_intent is False
    assert proposal.can_execute_order is False


# ====================================================================
# 정적 grep 가드
# ====================================================================


def _agent_source() -> str:
    p = pathlib.Path(__file__).parent.parent / "app" / "agents" / "strategy_selection_agent.py"
    return p.read_text(encoding="utf-8")


def test_agent_does_not_import_brokers():
    src = _agent_source()
    for needle in [
        "from app.brokers.kis",
        "from app.brokers.mock_broker",
        "from app.brokers.futures_base",
        "from app.brokers.base import OrderRequest",
        "from app.brokers.base import BrokerAdapter",
        "import app.brokers",
    ]:
        assert needle not in src, f"agent must not contain '{needle}'"


def test_agent_does_not_import_executor_or_route_order():
    src = _agent_source()
    for needle in [
        "from app.execution.executor",
        "from app.execution.order_executor",
        "from app.execution.order_router",
        "import app.execution",
        "route_order(",
        "= route_order",
        "broker.place_order(",
        "broker.cancel_order(",
        ".place_order(",
    ]:
        assert needle not in src, f"agent must not contain '{needle}'"


def test_agent_does_not_import_permission_or_ai_assist():
    src = _agent_source()
    for needle in [
        "from app.permission",
        "import app.permission",
        "from app.ai.assist",
        "import app.ai.assist",
        "submit_candidate(",
    ]:
        assert needle not in src, f"agent must not contain '{needle}'"


def test_agent_does_not_import_external_http_or_ai_sdk():
    src = _agent_source()
    for needle in [
        "import anthropic", "from anthropic",
        "import openai",    "from openai",
        "import httpx",     "from httpx",
        "import requests",  "from requests",
    ]:
        assert needle not in src, f"agent must not contain '{needle}'"


def test_agent_does_not_mutate_settings_or_live_flags():
    src = _agent_source()
    for needle in [
        "settings.enable_live_trading",
        "settings.enable_ai_execution",
        "settings.enable_futures_live_trading",
        "ENABLE_LIVE_TRADING =",
        "ENABLE_AI_EXECUTION =",
    ]:
        assert needle not in src, f"agent must not mutate '{needle}'"


# ====================================================================
# BlockedStrategyEntry / StrategyCandidate dataclass
# ====================================================================


def test_blocked_strategy_entry_is_frozen():
    entry = BlockedStrategyEntry(
        strategy_id="volume_breakout", symbol="005930",
        reason=BlockedReason.RISK_OFF_REGIME,
        detail="RISK_OFF — BUY 차단",
    )
    with pytest.raises(Exception):
        entry.strategy_id = "x"  # type: ignore[misc]


def test_strategy_candidate_is_frozen():
    cand = StrategyCandidate(
        strategy_id="volume_breakout", symbol="005930",
        action=AggregatedAction.BUY,
        confidence=80, quality_score=85, score=104.0,
        is_supporting=True,
    )
    with pytest.raises(Exception):
        cand.score = 999.0  # type: ignore[misc]
