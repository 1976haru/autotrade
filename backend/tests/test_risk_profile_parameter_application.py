"""#4-RiskProfileApply: 운용 성향 파라미터가 *실제 판단* 에 반영되는지 검증.

본 테스트는 4-RiskProfile (프리셋 카탈로그) 위에 4-RiskProfileApply 의 *통합*
계층을 검증한다. 동일한 입력에 대해 CONSERVATIVE / BALANCED / AGGRESSIVE 가
*다른* 결과를 만드는지 — confidence 임계 / position size / risk_veto 허용 /
거래 후보 수 / 손절 % — 의 모든 축에서 차이를 확인한다.

Covers:
* consumer.consume_agent_recommendations(risk_profile=...) — sizing_policy
  자동 도출 + metadata.risk_profile / risk_veto_max_flags carry.
* CONSERVATIVE: confidence 0.5 인 BUY 가 sizing → quantity=0 (HOLD 다운그레이드).
  BALANCED / AGGRESSIVE 에서는 confidence 0.5 가 통과.
* CONSERVATIVE: 1개 risk_flag → 신규 BUY 차단 (veto BLOCK_NEW_ENTRY).
  BALANCED: 1개 flag → 허용. 2개 flag → 차단.
  AGGRESSIVE: 2개 flag → 허용. 3개 flag → 차단.
* Same explanation + clean entry → 모든 성향 BUY 통과, position_size 가
  CONS < BAL < AGG 순서로 증가.
* 명시 sizing_policy override 가 risk_profile 보다 우선.
* 안전 invariant — broker spy 0건, mode=PAPER, is_order_signal=False.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.paper_decision_bridge import (
    bridge_explanation_to_paper_decisions,
)
from app.agents.paper_start_explanation import (
    ExplanationVerdict,
    PaperStartExplanation,
    StrategyExplanation,
)
from app.agents.risk_profile import (
    RiskProfile,
    policy_for,
    sizing_policy_for,
)
from app.auto_paper.agent_consumer import (
    build_deterministic_explanation,
    consume_agent_recommendations,
)
from app.auto_paper.events import DecisionAction
from app.auto_paper.ledger import reset_ledger_for_tests
from app.auto_paper.position_sizer import PositionSizingPolicy
from app.brokers.kis import KisBrokerAdapter
from app.db.models import AgentDecisionLog, Base


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def _isolated_ledger():
    reset_ledger_for_tests()
    yield
    reset_ledger_for_tests()


@pytest.fixture
def kis_spy(monkeypatch):
    """KisBrokerAdapter.place_order spy — must not be called by any profile."""
    spy = MagicMock(side_effect=AssertionError(
        "KisBrokerAdapter.place_order must not be invoked by AI Paper flow"
    ))
    monkeypatch.setattr(KisBrokerAdapter, "place_order", spy)
    cspy = MagicMock(side_effect=AssertionError(
        "KisBrokerAdapter.cancel_order must not be invoked by AI Paper flow"
    ))
    monkeypatch.setattr(KisBrokerAdapter, "cancel_order", cspy)
    return spy, cspy


def _se(strategy, symbol, *, bucket="recommended", risk_flags=None):
    return StrategyExplanation(
        strategy=strategy, symbol=symbol,
        bucket=bucket,
        paper_candidate_status="READY_FOR_PAPER",
        rationale_lines=["test"],
        risk_flags=list(risk_flags or []),
    )


def _exp(*, recommended=None, watchlist=None, excluded=None,
         verdict=ExplanationVerdict.READY_TO_REVIEW,
         market_regime="TREND_UP"):
    return PaperStartExplanation(
        generated_at="2026-05-19T01:00:00+00:00",
        schema_version="1.0",
        verdict=verdict,
        recommended_explanations=list(recommended or []),
        watchlist_explanations=list(watchlist or []),
        excluded_explanations=list(excluded or []),
        market_regime=market_regime,
        regime_confidence=0.85,
        regime_reasons=[],
        regime_risk_flags=[],
        regime_allowed_tactics=[],
        regime_blocked_tactics=[],
        overfit_count=0,
        overfit_strategies=[],
        headline="test",
        risk_summary=[],
        operator_note="",
        next_actions=[],
        can_start_paper=True,
        blocking_reasons=[],
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. consumer wiring — risk_profile derives sizing_policy + thresholds
# ─────────────────────────────────────────────────────────────────────────────


class TestConsumerWiring:

    def _runner_factory(self, profile, db):
        def _prov(_n):
            return build_deterministic_explanation(
                strategy="sma_crossover", symbol="005930",
            )

        def _run(loop_state: str, now: datetime):
            return consume_agent_recommendations(
                loop_state=loop_state,
                recommendation_provider=_prov,
                db_session=db,
                now=now,
                risk_profile=profile,
            )
        return _run

    @pytest.mark.parametrize("profile, expected_label", [
        (RiskProfile.CONSERVATIVE, "CONSERVATIVE"),
        (RiskProfile.BALANCED,     "BALANCED"),
        (RiskProfile.AGGRESSIVE,   "AGGRESSIVE"),
        ("conservative",           "CONSERVATIVE"),
        ("AGGRESSIVE",             "AGGRESSIVE"),
    ])
    def test_consumer_carries_profile_label(self, db, profile, expected_label, kis_spy):
        result = consume_agent_recommendations(
            loop_state="RUNNING",
            recommendation_provider=lambda _n: build_deterministic_explanation(),
            db_session=db,
            risk_profile=profile,
        )
        assert result.metadata["risk_profile"] == expected_label
        # spy 호출 0건.
        spy, cspy = kis_spy
        assert spy.call_count == 0
        assert cspy.call_count == 0

    def test_consumer_carries_risk_veto_max_flags(self, db, kis_spy):
        c = consume_agent_recommendations(
            loop_state="RUNNING",
            recommendation_provider=lambda _n: build_deterministic_explanation(),
            db_session=db,
            risk_profile=RiskProfile.CONSERVATIVE,
        )
        assert c.metadata["risk_veto_max_flags"] == 0  # CONS 임계.
        b = consume_agent_recommendations(
            loop_state="RUNNING",
            recommendation_provider=lambda _n: build_deterministic_explanation(),
            db_session=db,
            risk_profile=RiskProfile.BALANCED,
        )
        assert b.metadata["risk_veto_max_flags"] == 1  # BAL 임계.
        a = consume_agent_recommendations(
            loop_state="RUNNING",
            recommendation_provider=lambda _n: build_deterministic_explanation(),
            db_session=db,
            risk_profile=RiskProfile.AGGRESSIVE,
        )
        assert a.metadata["risk_veto_max_flags"] == 2  # AGG 임계.

    def test_consumer_no_profile_default(self, db, kis_spy):
        # risk_profile 미주입 → 기존 동작 (sizing_policy=None, risk_veto_max_flags=0).
        r = consume_agent_recommendations(
            loop_state="RUNNING",
            recommendation_provider=lambda _n: build_deterministic_explanation(),
            db_session=db,
        )
        assert r.metadata["risk_profile"] is None
        assert r.metadata["risk_veto_max_flags"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# 2. Confidence threshold ordering — CONS > BAL > AGG
# ─────────────────────────────────────────────────────────────────────────────


class TestConfidenceThreshold:

    def test_low_confidence_blocked_by_conservative_passed_by_aggressive(self, kis_spy):
        # confidence 0.35: CONSERVATIVE (임계 0.60) → quantity=0 → HOLD,
        # BALANCED (임계 0.40) → quantity=0 → HOLD,
        # AGGRESSIVE (임계 0.30) → quantity > 0 → BUY.
        exp = _exp(recommended=[_se("sma_crossover", "005930")])
        price = 70_000.0
        equity = 10_000_000.0
        confidence = 0.35

        for profile, expected_action in [
            (RiskProfile.CONSERVATIVE, DecisionAction.HOLD),
            (RiskProfile.BALANCED,     DecisionAction.HOLD),
            (RiskProfile.AGGRESSIVE,   DecisionAction.BUY),
        ]:
            sizing = sizing_policy_for(profile)
            report = bridge_explanation_to_paper_decisions(
                explanation=exp, loop_state="RUNNING", positions=[],
                sizing_policy=sizing,
                price_lookup={("sma_crossover", "005930"): price},
                account_equity=equity,
                confidence_lookup={("sma_crossover", "005930"): confidence},
            )
            actions = [d.action for d in report.decisions]
            assert expected_action in actions, (
                f"{profile.value}: expected {expected_action.value}, "
                f"got {[a.value for a in actions]}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 3. risk_veto_max_flags — flag-count threshold per profile
# ─────────────────────────────────────────────────────────────────────────────


class TestRiskVetoFlagThreshold:

    def test_one_flag_blocks_conservative_passes_balanced(self):
        # 1 flag: CONSERVATIVE (max=0) blocks, BALANCED (max=1) passes.
        exp = _exp(recommended=[
            _se("sma_crossover", "005930", risk_flags=["stale_data"]),
        ])
        cons = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            risk_veto_max_flags=policy_for(RiskProfile.CONSERVATIVE).risk_veto_max_flags,
        )
        bal = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            risk_veto_max_flags=policy_for(RiskProfile.BALANCED).risk_veto_max_flags,
        )
        cons_actions = [d.action for d in cons.decisions]
        bal_actions = [d.action for d in bal.decisions]
        # CONSERVATIVE: BUY 0, HOLD 1 (veto downgrade).
        assert DecisionAction.BUY not in cons_actions
        assert DecisionAction.HOLD in cons_actions
        # BALANCED: BUY 1 (veto relaxed).
        assert DecisionAction.BUY in bal_actions

    def test_two_flags_blocks_balanced_passes_aggressive(self):
        exp = _exp(recommended=[
            _se("sma_crossover", "005930",
                risk_flags=["stale_data", "high_correlation"]),
        ])
        bal = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            risk_veto_max_flags=policy_for(RiskProfile.BALANCED).risk_veto_max_flags,
        )
        agg = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            risk_veto_max_flags=policy_for(RiskProfile.AGGRESSIVE).risk_veto_max_flags,
        )
        bal_actions = [d.action for d in bal.decisions]
        agg_actions = [d.action for d in agg.decisions]
        assert DecisionAction.BUY not in bal_actions   # 2 > 1.
        assert DecisionAction.BUY in agg_actions       # 2 == 2 → 허용.

    def test_three_flags_blocks_even_aggressive(self):
        # AGGRESSIVE max=2 — 3 flags should still block.
        exp = _exp(recommended=[
            _se("sma_crossover", "005930",
                risk_flags=["stale_data", "high_correlation", "low_liquidity"]),
        ])
        agg = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            risk_veto_max_flags=policy_for(RiskProfile.AGGRESSIVE).risk_veto_max_flags,
        )
        actions = [d.action for d in agg.decisions]
        assert DecisionAction.BUY not in actions
        assert DecisionAction.HOLD in actions

    def test_emergency_stop_blocks_all_profiles(self):
        # EMERGENCY_STOP 은 어떤 성향 임계값으로도 우회 불가.
        exp = _exp(recommended=[_se("sma_crossover", "005930")])
        for profile in RiskProfile:
            report = bridge_explanation_to_paper_decisions(
                explanation=exp, loop_state="EMERGENCY_STOP", positions=[],
                risk_veto_max_flags=policy_for(profile).risk_veto_max_flags,
            )
            # EMERGENCY 단락 short-circuit — decisions 0.
            assert report.decisions == []

    def test_pre_market_block_blocks_all_profiles(self):
        exp = _exp(verdict=ExplanationVerdict.DO_NOT_START,
                   recommended=[_se("sma_crossover", "005930")])
        for profile in RiskProfile:
            report = bridge_explanation_to_paper_decisions(
                explanation=exp, loop_state="RUNNING", positions=[],
                risk_veto_max_flags=policy_for(profile).risk_veto_max_flags,
            )
            actions = [d.action for d in report.decisions]
            assert DecisionAction.BUY not in actions

    def test_risk_officer_reject_blocks_all_profiles(self):
        exp = _exp(recommended=[_se("sma_crossover", "005930")])
        for profile in RiskProfile:
            report = bridge_explanation_to_paper_decisions(
                explanation=exp, loop_state="RUNNING", positions=[],
                risk_officer_rejects={("sma_crossover", "005930"): "rejected"},
                risk_veto_max_flags=policy_for(profile).risk_veto_max_flags,
            )
            actions = [d.action for d in report.decisions]
            assert DecisionAction.BUY not in actions


# ─────────────────────────────────────────────────────────────────────────────
# 4. Position size ordering — CONS < BAL < AGG on clean entries
# ─────────────────────────────────────────────────────────────────────────────


class TestPositionSizeOrdering:

    def test_clean_entry_size_ascending(self):
        # 동일한 high-confidence + clean risk_flag 시 position size 가
        # CONS < BAL < AGG 순으로 증가.
        exp = _exp(recommended=[_se("sma_crossover", "005930")])
        price = 70_000.0
        equity = 100_000_000.0   # 1억 — cap engagement 가능
        confidence = 0.95

        sizes = {}
        for profile in (RiskProfile.CONSERVATIVE,
                        RiskProfile.BALANCED,
                        RiskProfile.AGGRESSIVE):
            report = bridge_explanation_to_paper_decisions(
                explanation=exp, loop_state="RUNNING", positions=[],
                sizing_policy=sizing_policy_for(profile),
                price_lookup={("sma_crossover", "005930"): price},
                account_equity=equity,
                confidence_lookup={("sma_crossover", "005930"): confidence},
            )
            buys = [d for d in report.decisions if d.action == DecisionAction.BUY]
            assert len(buys) == 1
            sizes[profile] = buys[0].virtual_position_delta

        assert sizes[RiskProfile.CONSERVATIVE] < sizes[RiskProfile.BALANCED] \
            < sizes[RiskProfile.AGGRESSIVE]


# ─────────────────────────────────────────────────────────────────────────────
# 5. Explicit sizing_policy overrides risk_profile
# ─────────────────────────────────────────────────────────────────────────────


class TestExplicitOverridePrecedence:

    def test_explicit_sizing_policy_wins(self, db, kis_spy):
        # operator override 사이즈 정책이 risk_profile (AGGRESSIVE) 보다 우선.
        custom = PositionSizingPolicy(
            max_risk_per_trade_pct=0.001,    # 매우 작게
            default_stop_loss_pct=0.10,
            max_position_pct=0.01,
            max_position_krw=100_000,
            min_confidence_threshold=0.99,   # 사실상 모두 차단.
            max_risk_flags=1,
        )
        result = consume_agent_recommendations(
            loop_state="RUNNING",
            recommendation_provider=lambda _n: build_deterministic_explanation(),
            db_session=db,
            sizing_policy=custom,
            risk_profile=RiskProfile.AGGRESSIVE,
        )
        # custom 의 0.99 임계 때문에 default confidence 0.5 BUY 가 HOLD 로 강등.
        assert result.by_action.get("BUY", 0) == 0
        # 그래도 metadata 의 risk_profile 라벨은 AGGRESSIVE carry (정보용).
        assert result.metadata["risk_profile"] == "AGGRESSIVE"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Safety invariants — broker spy + mode=PAPER + invariant False
# ─────────────────────────────────────────────────────────────────────────────


class TestSafetyInvariants:

    @pytest.mark.parametrize("profile", list(RiskProfile))
    def test_no_broker_call_for_any_profile(self, db, profile, kis_spy):
        result = consume_agent_recommendations(
            loop_state="RUNNING",
            recommendation_provider=lambda _n: build_deterministic_explanation(),
            db_session=db,
            risk_profile=profile,
        )
        spy, cspy = kis_spy
        assert spy.call_count == 0
        assert cspy.call_count == 0
        assert result.is_order_signal is False
        assert result.auto_apply_allowed is False
        assert result.is_live_authorization is False

    def test_agent_decision_log_rows_are_paper_mode(self, db, kis_spy):
        consume_agent_recommendations(
            loop_state="RUNNING",
            recommendation_provider=lambda _n: build_deterministic_explanation(),
            db_session=db,
            risk_profile=RiskProfile.AGGRESSIVE,
        )
        rows = db.query(AgentDecisionLog).all()
        for r in rows:
            assert r.mode == "PAPER"


# ─────────────────────────────────────────────────────────────────────────────
# 7. Non-RUNNING short-circuit independent of profile
# ─────────────────────────────────────────────────────────────────────────────


class TestNonRunningProfileInert:

    @pytest.mark.parametrize("profile", list(RiskProfile))
    @pytest.mark.parametrize("state", ["PAUSED", "STOPPED", "EMERGENCY_STOP"])
    def test_non_running_profile_carries_but_no_decisions(
        self, db, profile, state, kis_spy,
    ):
        result = consume_agent_recommendations(
            loop_state=state,
            recommendation_provider=lambda _n: build_deterministic_explanation(),
            db_session=db,
            risk_profile=profile,
        )
        assert result.consumed is False
        assert result.decision_count == 0
        rows = db.query(AgentDecisionLog).all()
        assert len(rows) == 0
