"""#4-02: Strategy Combination Recommender 테스트.

invariant:
- `StrategyDecision` + `StrategyCombinationRecommendation` 둘 다
  is_order_signal=False / auto_apply_allowed=False / is_live_authorization=False
  (`__post_init__` ValueError 가드).
- `StrategyCombinationRecommendation.auto_start_paper_trader=False` 추가 invariant.
- BUY/SELL/HOLD/Place Order/실거래 시작 라벨 0건 (JSON 출력에도 없음).
  `StrategyAction.HOLD` 는 *보류* 의미이며 주문 방향이 아님 — 본 모듈은
  `"BUY"` / `"SELL"` 같은 enum 값 0개.
- broker / OrderExecutor / route_order / KIS 주문 import 0건 (정적 grep).
- 외부 HTTP / AI SDK import 0건.
- secret / API key / 계좌번호 / `.env` 접근 0건.
- 후보 0건도 권고 객체 생성 + reasons_no_candidate carry.
- 추천 / 제외 / 보류 분류 + 다양성 휴리스틱 검증.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.agents.base import (
    AgentContext,
    AgentDecision,
    AgentOutput,
    AgentRole,
)
from app.agents.strategy_combination_recommender import (
    COMBINATION_SCHEMA_VERSION,
    OverallRecommendation,
    StrategyAction,
    StrategyCombinationRecommendation,
    StrategyCombinationRecommenderAgent,
    StrategyDecision,
    build_combination_recommendation,
)
from app.agents.strategy_optimizer_agent import (
    build_strategy_agent_input,
)
from app.analytics.paper_candidate_aggregator import PipelineStage
from app.analytics.strategy_optimization_report import (
    OperatorReport,
    ReportInputs,
    ReportStatus,
    StrategyEntry,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helper — synthetic input
# ─────────────────────────────────────────────────────────────────────────────


def _entry(
    strategy="sma_crossover", symbol="005930", status=ReportStatus.READY_FOR_PAPER,
    score=0.05, params=None, risk_signals=None, exclusion_reasons=None,
):
    return StrategyEntry(
        strategy_id=strategy,
        display_name=f"{strategy} display",
        symbol=symbol,
        params=params or {},
        status=status,
        pipeline_stages=[
            PipelineStage(name="3-02", verdict="BACKTEST_PASS",
                          extra={"metrics": {"profit_factor": 1.5,
                                              "max_drawdown": 0.08,
                                              "expectancy": 500.0,
                                              "win_rate": 0.55,
                                              "trade_count": 45}}),
            PipelineStage(name="3-03", verdict="PAPER_CANDIDATE"),
            PipelineStage(name="3-04", verdict="HEALTHY"),
            PipelineStage(name="3-05", verdict="PASS"),
        ],
        risk_metrics={"profit_factor": 1.5, "max_drawdown": 0.08,
                      "expectancy": 500.0, "win_rate": 0.55,
                      "trade_count": 45},
        risk_signals=risk_signals or [],
        exclusion_reasons=exclusion_reasons or [],
        score=score,
    )


def _build_report(entries):
    """Helper — direct OperatorReport with given entries (status-aware)."""
    paper_candidates = [e for e in entries if e.status == ReportStatus.READY_FOR_PAPER]
    excluded         = [e for e in entries if e.status != ReportStatus.READY_FOR_PAPER]
    if paper_candidates:
        overall = ReportStatus.READY_FOR_PAPER
    elif entries:
        overall = ReportStatus.NO_CANDIDATE
    else:
        overall = ReportStatus.NO_CANDIDATE
    return OperatorReport(
        generated_at="2026-05-17T00:00:00+00:00",
        overall_status=overall,
        paper_ready_count=len(paper_candidates),
        excluded_count=len(excluded),
        entries=entries,
        paper_candidates=paper_candidates,
        excluded=excluded,
        reasons_no_candidate=[] if paper_candidates else (
            ["no_candidate_passed_all_required_stages"] if entries
            else ["no_pipeline_results_loaded"]
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. 후보 있음 — RECOMMEND 분류 + 조합 선정
# ─────────────────────────────────────────────────────────────────────────────


class TestRecommendationWithCandidates:
    def test_single_candidate_produces_single_recommendation(self):
        report = _build_report([_entry()])
        rec = build_combination_recommendation(operator_report=report)
        assert rec.overall_recommendation == OverallRecommendation.HAS_RECOMMENDATIONS
        assert rec.recommended_count == 1
        assert rec.held_count == 0
        assert rec.excluded_count == 0
        d = rec.recommended_combo[0]
        assert d.action == StrategyAction.RECOMMEND
        assert d.strategy == "sma_crossover"

    def test_two_diverse_candidates_both_selected(self):
        report = _build_report([
            _entry(strategy="sma_crossover", symbol="005930", score=0.10),
            _entry(strategy="rsi_reversion", symbol="000660", score=0.08),
        ])
        rec = build_combination_recommendation(operator_report=report)
        assert rec.recommended_count == 2
        # 다양성 확보 — 다른 strategy + 다른 symbol.
        strategies = {d.strategy for d in rec.recommended_combo}
        symbols    = {d.symbol   for d in rec.recommended_combo}
        assert len(strategies) == 2
        assert len(symbols) == 2

    def test_score_ordering_top_first(self):
        report = _build_report([
            _entry(strategy="a", symbol="A", score=0.05),
            _entry(strategy="b", symbol="B", score=0.20),
            _entry(strategy="c", symbol="C", score=0.10),
        ])
        rec = build_combination_recommendation(operator_report=report, max_combo_size=3)
        scores = [d.score for d in rec.recommended_combo]
        assert scores[0] == 0.20   # b first.

    def test_diversity_prefers_different_strategy_over_higher_score(self):
        # 같은 strategy + symbol 의 2 후보 점수 차이 0.20 vs 0.10,
        # 다른 strategy + symbol 의 점수 0.05 후보.
        report = _build_report([
            _entry(strategy="a", symbol="X", score=0.20),
            _entry(strategy="a", symbol="X", params={"v": 1}, score=0.10),
            _entry(strategy="b", symbol="Y", score=0.05),
        ])
        rec = build_combination_recommendation(
            operator_report=report, max_combo_size=2,
        )
        # 1st = score top (a/X 0.20)
        assert rec.recommended_combo[0].strategy == "a"
        # 2nd = 다른 strategy + 다른 symbol (b/Y 0.05) — score 가 더 낮은 0.10 (a/X v=1) 보다 우선.
        assert rec.recommended_combo[1].strategy == "b"
        assert rec.recommended_combo[1].symbol   == "Y"

    def test_max_combo_size_caps_selection(self):
        report = _build_report([
            _entry(strategy=f"s_{i}", symbol=f"SYM_{i}", score=1.0 - i * 0.1)
            for i in range(5)
        ])
        rec = build_combination_recommendation(operator_report=report, max_combo_size=2)
        assert rec.recommended_count == 2
        # 나머지 3 → HOLD 로 demote.
        assert rec.held_count >= 3
        # 모든 held 가 demoted reason carry.
        for d in rec.held:
            assert any("demoted_from_recommend" in r for r in d.reasons)


# ─────────────────────────────────────────────────────────────────────────────
# 2. 후보 없음 — NO_CANDIDATES_TODAY
# ─────────────────────────────────────────────────────────────────────────────


class TestNoCandidates:
    def test_empty_input_marks_no_candidates_today(self):
        rec = build_combination_recommendation(inputs=ReportInputs())
        assert rec.overall_recommendation == OverallRecommendation.NO_CANDIDATES_TODAY
        assert rec.recommended_count == 0
        assert rec.held_count == 0
        assert rec.excluded_count == 0
        assert rec.reasons_no_candidate, "후보 0건이면 사유 carry"
        # operator note 에 "강제로 paper trader 를 시작하지 마세요" 포함.
        joined = " ".join(rec.operator_notes)
        assert "paper trader" in joined or "후보 없음" in joined

    def test_all_strategies_excluded_marks_no_candidates_today(self):
        report = _build_report([
            _entry(status=ReportStatus.STRESS_FAILED,
                   exclusion_reasons=["3-05_단계_탈락_verdict=FAIL"]),
            _entry(strategy="rsi_reversion", status=ReportStatus.OVERFIT_RISK,
                   exclusion_reasons=["3-04_단계_탈락_verdict=OVERFIT_RISK"]),
        ])
        rec = build_combination_recommendation(operator_report=report)
        assert rec.overall_recommendation == OverallRecommendation.NO_CANDIDATES_TODAY
        assert rec.recommended_count == 0
        assert rec.excluded_count == 2

    def test_excluded_decisions_carry_reasons(self):
        report = _build_report([
            _entry(status=ReportStatus.STRESS_FAILED,
                   exclusion_reasons=["3-05_단계_탈락_verdict=FAIL"]),
        ])
        rec = build_combination_recommendation(operator_report=report)
        d = rec.excluded[0]
        assert d.action == StrategyAction.EXCLUDE
        joined = " ".join(d.reasons)
        assert "스트레스" in joined or "3-05" in joined


# ─────────────────────────────────────────────────────────────────────────────
# 3. HOLD 분류 — risk_flags 임계 기반
# ─────────────────────────────────────────────────────────────────────────────


class TestHoldClassification:
    def test_ready_with_many_risk_flags_marks_hold(self):
        # READY_FOR_PAPER 이지만 risk_signals 2개 이상 → HOLD (default threshold=2).
        report = _build_report([
            _entry(risk_signals=[
                "profit_factor_below_1 (0.7)",
                "high_max_drawdown (0.25)",
            ]),
        ])
        rec = build_combination_recommendation(operator_report=report)
        assert rec.recommended_count == 0
        assert rec.held_count == 1
        assert rec.overall_recommendation == OverallRecommendation.ALL_HOLD
        # operator note 에 "위험 신호로 모두 보류" 포함.
        joined = " ".join(rec.operator_notes)
        assert "보류" in joined or "위험 신호" in joined

    def test_one_risk_flag_still_recommended(self):
        report = _build_report([
            _entry(risk_signals=["low_trade_count (28)"]),
        ])
        rec = build_combination_recommendation(operator_report=report)
        assert rec.recommended_count == 1
        assert rec.held_count == 0

    def test_custom_threshold_changes_behavior(self):
        report = _build_report([
            _entry(risk_signals=["low_trade_count (28)"]),
        ])
        # threshold=1 → 1개 신호도 HOLD.
        rec = build_combination_recommendation(
            operator_report=report, hold_risk_flag_threshold=1,
        )
        assert rec.recommended_count == 0
        assert rec.held_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# 4. Diversity warning (operator notes)
# ─────────────────────────────────────────────────────────────────────────────


class TestDiversityWarnings:
    def test_all_same_symbol_carries_operator_note(self):
        # 다른 strategy 지만 같은 symbol.
        report = _build_report([
            _entry(strategy="a", symbol="005930", score=0.20),
            _entry(strategy="b", symbol="005930", score=0.10),
        ])
        rec = build_combination_recommendation(operator_report=report, max_combo_size=2)
        if rec.recommended_count == 2:
            # 다양성 경고 carry.
            joined = " ".join(rec.operator_notes)
            assert "종목" in joined or "분산" in joined


# ─────────────────────────────────────────────────────────────────────────────
# 5. Invariants — is_order_signal=False / auto_apply_allowed=False / ...
# ─────────────────────────────────────────────────────────────────────────────


class TestInvariants:
    def test_decision_is_order_signal_must_be_false(self):
        with pytest.raises(ValueError):
            StrategyDecision(
                strategy="x", symbol="X", params={},
                action=StrategyAction.RECOMMEND,
                paper_candidate_status="READY_FOR_PAPER", score=0.0,
                is_order_signal=True,
            )

    def test_decision_auto_apply_allowed_must_be_false(self):
        with pytest.raises(ValueError):
            StrategyDecision(
                strategy="x", symbol="X", params={},
                action=StrategyAction.RECOMMEND,
                paper_candidate_status="READY_FOR_PAPER", score=0.0,
                auto_apply_allowed=True,
            )

    def test_decision_is_live_authorization_must_be_false(self):
        with pytest.raises(ValueError):
            StrategyDecision(
                strategy="x", symbol="X", params={},
                action=StrategyAction.RECOMMEND,
                paper_candidate_status="READY_FOR_PAPER", score=0.0,
                is_live_authorization=True,
            )

    def test_top_level_invariants_immutable(self):
        for kwargs in (
            {"is_order_signal": True},
            {"auto_apply_allowed": True},
            {"is_live_authorization": True},
            {"auto_start_paper_trader": True},
        ):
            with pytest.raises(ValueError):
                StrategyCombinationRecommendation(
                    generated_at="x", schema_version="1.0",
                    overall_recommendation=OverallRecommendation.NO_CANDIDATES_TODAY,
                    recommended_combo=[],
                    **kwargs,
                )

    def test_disclaimer_cannot_be_empty(self):
        with pytest.raises(ValueError):
            StrategyCombinationRecommendation(
                generated_at="x", schema_version="1.0",
                overall_recommendation=OverallRecommendation.NO_CANDIDATES_TODAY,
                recommended_combo=[], advisory_disclaimer="",
            )

    def test_to_dict_carries_invariants_at_all_levels(self):
        report = _build_report([_entry()])
        rec = build_combination_recommendation(operator_report=report)
        d = rec.to_dict()
        assert d["is_order_signal"]           is False
        assert d["auto_apply_allowed"]        is False
        assert d["is_live_authorization"]     is False
        assert d["auto_start_paper_trader"]   is False
        for dec_d in d["recommended_combo"] + d["held"] + d["excluded"] + d["decisions"]:
            assert dec_d["is_order_signal"]       is False
            assert dec_d["auto_apply_allowed"]    is False
            assert dec_d["is_live_authorization"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 6. Action enum invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestActionEnum:
    def test_action_enum_has_no_buy_sell_hold_order_values(self):
        """StrategyAction 은 *주문 방향* 값을 0개 포함해야 한다 — HOLD 는
        *보류* (advisory) 이며 주문 신호로서의 HOLD 가 *아니다*.

        본 enum 에 "BUY" / "SELL" 값 자체가 없는지 lock.
        """
        values = {a.value for a in StrategyAction}
        forbidden = {"BUY", "SELL", "BUY_SIGNAL", "SELL_SIGNAL",
                     "PLACE_ORDER", "EXECUTE", "FILL"}
        assert not (values & forbidden), \
            f"forbidden order-direction values in StrategyAction: {values & forbidden}"
        # 3 액션만 — 새 액션 추가 시 명시 PR 필요.
        assert values == {"RECOMMEND", "HOLD", "EXCLUDE"}

    def test_overall_enum_values_are_advisory_only(self):
        values = {o.value for o in OverallRecommendation}
        forbidden = {"BUY", "SELL", "EXECUTE", "PLACE_ORDER",
                     "ENABLE_LIVE_TRADING"}
        assert not (values & forbidden)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Agent compliance (AgentBase)
# ─────────────────────────────────────────────────────────────────────────────


class TestAgent:
    def test_agent_metadata_shape(self):
        agent = StrategyCombinationRecommenderAgent()
        meta = agent.metadata
        assert meta.name == "strategy_combination_recommender"
        assert meta.role == AgentRole.STRATEGY_RESEARCHER
        assert meta.can_execute_order is False

    def test_agent_run_with_no_inputs_returns_no_candidates(self):
        agent = StrategyCombinationRecommenderAgent()
        out = agent.run(AgentContext(extra={}))
        assert isinstance(out, AgentOutput)
        assert out.decision == AgentDecision.RECOMMEND
        assert out.is_order_intent is False
        assert out.can_execute_order is False
        rec_dict = out.metadata["combination_recommendation"]
        assert rec_dict["overall_recommendation"] == "NO_CANDIDATES_TODAY"

    def test_agent_run_with_operator_report(self):
        report = _build_report([_entry()])
        agent = StrategyCombinationRecommenderAgent()
        out = agent.run(AgentContext(extra={"operator_report": report}))
        rec_dict = out.metadata["combination_recommendation"]
        assert rec_dict["recommended_count"] == 1
        assert rec_dict["overall_recommendation"] == "HAS_RECOMMENDATIONS"

    def test_agent_run_with_existing_recommendation_passthrough(self):
        # 호출자가 이미 빌드한 권고 객체 주입 → 그대로 carry.
        report = _build_report([_entry()])
        rec = build_combination_recommendation(operator_report=report)
        agent = StrategyCombinationRecommenderAgent()
        out = agent.run(AgentContext(extra={"combination_recommendation": rec}))
        assert out.metadata["combination_recommendation"]["recommended_count"] == 1

    def test_agent_run_with_strategy_agent_input(self):
        # 4-01 StrategyAgentInput 주입.
        report = _build_report([_entry()])
        agent_input = build_strategy_agent_input(operator_report=report)
        agent = StrategyCombinationRecommenderAgent()
        out = agent.run(AgentContext(extra={"strategy_agent_input": agent_input}))
        assert out.metadata["combination_recommendation"]["recommended_count"] == 1

    def test_agent_output_invariants(self):
        report = _build_report([_entry()])
        agent = StrategyCombinationRecommenderAgent()
        out = agent.run(AgentContext(extra={"operator_report": report}))
        d = out.to_dict()
        assert d["is_order_intent"]   is False
        assert d["can_execute_order"] is False
        meta = d["metadata"]
        assert meta["advisory_only"]               is True
        assert meta["is_order_signal"]             is False
        assert meta["auto_apply_allowed"]          is False
        assert meta["is_live_authorization"]       is False
        assert meta["auto_start_paper_trader"]     is False

    def test_agent_does_not_emit_buy_sell_hold_order_signals(self):
        report = _build_report([_entry()])
        agent = StrategyCombinationRecommenderAgent()
        out = agent.run(AgentContext(extra={"operator_report": report}))
        text = json.dumps(out.to_dict(), ensure_ascii=False)
        forbidden = [
            "\"action\": \"BUY\"",
            "\"action\": \"SELL\"",
            "\"action\": \"PLACE_ORDER\"",
            "Place Order",
            "지금 매수",
            "지금 매도",
            "실거래 시작",
            "ENABLE_LIVE_TRADING 토글",
            "AI 자동매매 켜기",
        ]
        for fp in forbidden:
            assert fp not in text, f"forbidden in agent output: {fp}"


# ─────────────────────────────────────────────────────────────────────────────
# 8. Static guards — forbidden imports / safety flag mutation / secrets
# ─────────────────────────────────────────────────────────────────────────────


_MOD_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "agents"
    / "strategy_combination_recommender.py"
)


class TestNoForbiddenImports:
    def test_module_has_no_broker_or_executor_imports(self):
        src = _MOD_PATH.read_text(encoding="utf-8")
        forbidden = [
            r"from\s+app\.brokers\.kis\b",
            r"from\s+app\.brokers\.mock_broker\b",
            r"from\s+app\.execution\.executor\b",
            r"from\s+app\.execution\.order_router\b",
            r"from\s+app\.ai\.assist\b",
            r"from\s+app\.ai\.client\b",
            r"from\s+app\.core\.config\s+import\s+get_settings",
            r"broker\.place_order\(",
            r"route_order\s*\(",
            r"OrderExecutor\s*\(",
            r"KisClient\b",
        ]
        for pat in forbidden:
            assert not re.search(pat, src, re.MULTILINE), \
                f"forbidden in strategy_combination_recommender.py: {pat}"

    def test_module_has_no_external_http_or_ai_sdk_imports(self):
        src = _MOD_PATH.read_text(encoding="utf-8")
        forbidden = [
            r"^import\s+anthropic\b",
            r"^import\s+openai\b",
            r"^import\s+requests\b",
            r"^import\s+httpx\b",
            r"^import\s+yfinance\b",
            r"^from\s+anthropic\b",
            r"^from\s+openai\b",
            r"^from\s+httpx\b",
            r"^from\s+requests\b",
        ]
        for pat in forbidden:
            assert not re.search(pat, src, re.MULTILINE), \
                f"forbidden http/AI in strategy_combination_recommender.py: {pat}"

    def test_no_safety_flag_mutation(self):
        src = _MOD_PATH.read_text(encoding="utf-8")
        bad = [
            r"ENABLE_LIVE_TRADING\s*=\s*['\"]?true",
            r"ENABLE_AI_EXECUTION\s*=\s*['\"]?true",
            r"ENABLE_FUTURES_LIVE_TRADING\s*=\s*['\"]?true",
            r"KIS_IS_PAPER\s*=\s*['\"]?false",
            r"settings\.enable_live_trading\s*=",
            r"settings\.enable_ai_execution\s*=",
        ]
        for pat in bad:
            assert not re.search(pat, src, re.IGNORECASE), \
                f"safety flag mutation in strategy_combination_recommender.py: {pat}"


class TestNoSecretPatterns:
    def test_no_secret_patterns_in_module(self):
        src = _MOD_PATH.read_text(encoding="utf-8")
        patterns = [
            r"sk-[A-Za-z0-9]{20,}",
            r"sk-ant-[A-Za-z0-9_\-]{20,}",
            r"ghp_[A-Za-z0-9]{30,}",
            r"Bearer\s+[A-Za-z0-9._\-]{20,}",
            r"PST[A-Za-z0-9]{30,}",
        ]
        for pat in patterns:
            assert not re.search(pat, src), \
                f"secret pattern in module: {pat}"

    def test_schema_has_no_secret_fields(self):
        decision_fields = StrategyDecision.__dataclass_fields__.keys()
        top_fields      = StrategyCombinationRecommendation.__dataclass_fields__.keys()
        secret_names = [
            "api_key", "secret", "app_key", "app_secret", "access_token",
            "account_no", "account_number", "anthropic_api_key",
            "openai_api_key", "kis_app_key", "kis_app_secret",
        ]
        for name in secret_names:
            assert name not in decision_fields, f"decision has secret field: {name}"
            assert name not in top_fields,      f"top has secret field: {name}"


# ─────────────────────────────────────────────────────────────────────────────
# 9. Required field lock — schema 진화 시 의도적 PR 필요
# ─────────────────────────────────────────────────────────────────────────────


class TestSchemaFieldLock:
    def test_decision_has_required_fields(self):
        required = {
            "strategy", "symbol", "params", "action",
            "paper_candidate_status", "score", "risk_flags", "reasons",
            "is_order_signal", "auto_apply_allowed", "is_live_authorization",
        }
        actual = set(StrategyDecision.__dataclass_fields__.keys())
        missing = required - actual
        assert not missing, f"missing decision fields: {missing}"

    def test_top_level_has_required_fields(self):
        required = {
            "generated_at", "schema_version", "overall_recommendation",
            "recommended_combo", "held", "excluded", "decisions",
            "reasons_no_candidate", "operator_notes", "advisory_disclaimer",
            "metadata", "is_order_signal", "auto_apply_allowed",
            "is_live_authorization", "auto_start_paper_trader",
        }
        actual = set(StrategyCombinationRecommendation.__dataclass_fields__.keys())
        missing = required - actual
        assert not missing, f"missing top fields: {missing}"

    def test_schema_version_carried(self):
        rec = build_combination_recommendation(inputs=ReportInputs())
        assert rec.schema_version == COMBINATION_SCHEMA_VERSION
