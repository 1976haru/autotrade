"""#4-02 v2: Paper Strategy Combination Recommender 테스트.

본 테스트는 *기존 `test_strategy_combination_recommender.py` 와 별도 파일* —
v1 API (`build_combination_recommendation` + `StrategyCombinationRecommendation`)
는 4-03/4-04 의 dependency 로 미터치, 본 PR 은 v2 API 만 검증.

invariant:
- `PaperStrategyCombination` + `PaperStrategyEntry` 둘 다 is_order_signal=False
  / auto_apply_allowed=False / is_live_authorization=False (`__post_init__`).
- 5단계 PaperCombinationStatus enum lock (BUY/SELL/HOLD 주문 방향 0개).
- 7 출력 필드 정확히 carry (사용자 spec).
- 분류 매트릭스 lock (READY+small_flags → recommend, OVERFIT → reject, etc.).
- 동일 종목/전략 쏠림 경고 carry.
- broker / OrderExecutor / route_order import 0건 (모듈은 기존 import 가드 상속).
"""

from __future__ import annotations

import pytest

from app.agents.strategy_combination_recommender import (
    PaperCombinationStatus,
    PaperStrategyCombination,
    PaperStrategyEntry,
    build_paper_combination_recommendation,
)
from app.agents.strategy_optimizer_agent import build_strategy_agent_input
from app.analytics.paper_candidate_aggregator import PipelineStage
from app.analytics.strategy_optimization_report import (
    OperatorReport,
    ReportInputs,
    ReportStatus,
    StrategyEntry,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────


def _entry(
    *,
    strategy="sma_crossover",
    symbol="005930",
    params=None,
    status=ReportStatus.READY_FOR_PAPER,
    score=0.05,
    risk_signals=None,
    exclusion_reasons=None,
):
    return StrategyEntry(
        strategy_id=strategy,
        display_name=f"{strategy} display",
        symbol=symbol,
        params=params or {},
        status=status,
        pipeline_stages=[
            PipelineStage(name="3-02", verdict="BACKTEST_PASS"),
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


def _report(entries):
    paper = [e for e in entries if e.status == ReportStatus.READY_FOR_PAPER]
    excluded = [e for e in entries if e.status != ReportStatus.READY_FOR_PAPER]
    overall = ReportStatus.READY_FOR_PAPER if paper else (
        ReportStatus.NO_CANDIDATE if entries else ReportStatus.NO_CANDIDATE
    )
    return OperatorReport(
        generated_at="2026-05-18T00:00:00+00:00",
        overall_status=overall,
        paper_ready_count=len(paper),
        excluded_count=len(excluded),
        entries=entries,
        paper_candidates=paper,
        excluded=excluded,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Enum lock
# ─────────────────────────────────────────────────────────────────────────────


class TestPaperCombinationStatusEnum:
    def test_five_status_values(self):
        assert {s.value for s in PaperCombinationStatus} == {
            "RECOMMEND_PAPER", "WATCH_ONLY", "NO_CANDIDATE",
            "REJECTED_BY_RISK", "NEED_MORE_DATA",
        }

    def test_no_order_direction_values(self):
        values = {s.value for s in PaperCombinationStatus}
        forbidden = {"BUY", "SELL", "PLACE_ORDER", "EXECUTE", "FILL"}
        assert not (values & forbidden)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Invariants — 양 레벨 dataclass guard
# ─────────────────────────────────────────────────────────────────────────────


class TestInvariants:
    def test_entry_invariants_must_be_false(self):
        for kwargs in (
            {"is_order_signal": True},
            {"auto_apply_allowed": True},
            {"is_live_authorization": True},
        ):
            with pytest.raises(ValueError):
                PaperStrategyEntry(
                    strategy="s", symbol="x", params={},
                    paper_candidate_status="READY_FOR_PAPER", score=0.0,
                    rationale="r", **kwargs,
                )

    def test_combination_invariants_must_be_false(self):
        for kwargs in (
            {"is_order_signal": True},
            {"auto_apply_allowed": True},
            {"is_live_authorization": True},
        ):
            with pytest.raises(ValueError):
                PaperStrategyCombination(
                    generated_at="t", status=PaperCombinationStatus.NO_CANDIDATE,
                    **kwargs,
                )

    def test_disclaimer_cannot_be_empty(self):
        with pytest.raises(ValueError):
            PaperStrategyCombination(
                generated_at="t", status=PaperCombinationStatus.NO_CANDIDATE,
                advisory_disclaimer="",
            )

    def test_to_dict_carries_invariants_top_and_per_entry(self):
        rec = build_paper_combination_recommendation(
            operator_report=_report([_entry()]),
        )
        d = rec.to_dict()
        assert d["is_order_signal"]       is False
        assert d["auto_apply_allowed"]    is False
        assert d["is_live_authorization"] is False
        for bucket in ("recommended_strategies", "excluded_strategies", "watchlist_strategies"):
            for e in d[bucket]:
                assert e["is_order_signal"]       is False
                assert e["auto_apply_allowed"]    is False
                assert e["is_live_authorization"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. 분류 매트릭스 lock — 5 path 모두
# ─────────────────────────────────────────────────────────────────────────────


class TestClassificationMatrix:
    def test_ready_low_flags_recommends(self):
        rec = build_paper_combination_recommendation(
            operator_report=_report([
                _entry(strategy="sma_crossover", symbol="005930"),
            ]),
        )
        assert rec.status == PaperCombinationStatus.RECOMMEND_PAPER
        assert len(rec.recommended_strategies) == 1
        assert rec.no_candidate_reason is None

    def test_ready_many_flags_watchlist(self):
        rec = build_paper_combination_recommendation(
            operator_report=_report([
                _entry(risk_signals=["flag_a (x)", "flag_b (y)", "flag_c (z)"]),
            ]),
        )
        assert rec.status == PaperCombinationStatus.WATCH_ONLY
        assert len(rec.recommended_strategies) == 0
        assert len(rec.watchlist_strategies) == 1

    def test_overfit_rejected(self):
        rec = build_paper_combination_recommendation(
            operator_report=_report([
                _entry(status=ReportStatus.OVERFIT_RISK,
                       exclusion_reasons=["3-04_단계_탈락_verdict=OVERFIT_RISK"]),
            ]),
        )
        assert rec.status == PaperCombinationStatus.REJECTED_BY_RISK
        assert len(rec.excluded_strategies) == 1
        assert "과최적화" in rec.excluded_strategies[0].rationale

    def test_stress_failed_rejected(self):
        rec = build_paper_combination_recommendation(
            operator_report=_report([
                _entry(status=ReportStatus.STRESS_FAILED),
            ]),
        )
        assert rec.status == PaperCombinationStatus.REJECTED_BY_RISK
        assert "스트레스" in rec.excluded_strategies[0].rationale

    def test_need_more_data_only_marks_need_more_data(self):
        rec = build_paper_combination_recommendation(
            operator_report=_report([
                _entry(status=ReportStatus.NEED_MORE_DATA),
                _entry(strategy="rsi_reversion", symbol="000660",
                       status=ReportStatus.NEED_MORE_DATA),
            ]),
        )
        assert rec.status == PaperCombinationStatus.NEED_MORE_DATA
        assert rec.no_candidate_reason and "NEED_MORE_DATA" in rec.no_candidate_reason
        assert len(rec.watchlist_strategies) == 2

    def test_rejected_by_risk_status_rejected(self):
        rec = build_paper_combination_recommendation(
            operator_report=_report([
                _entry(status=ReportStatus.REJECTED_BY_RISK),
            ]),
        )
        assert rec.status == PaperCombinationStatus.REJECTED_BY_RISK
        assert "위험 한도" in rec.excluded_strategies[0].rationale

    def test_empty_input_marks_no_candidate(self):
        rec = build_paper_combination_recommendation(inputs=ReportInputs())
        assert rec.status == PaperCombinationStatus.NO_CANDIDATE
        assert rec.no_candidate_reason is not None
        assert rec.recommended_strategies == []


# ─────────────────────────────────────────────────────────────────────────────
# 4. 최대 2개 추천 — max_recommended 정책
# ─────────────────────────────────────────────────────────────────────────────


class TestMaxRecommended:
    def test_default_max_2(self):
        rec = build_paper_combination_recommendation(
            operator_report=_report([
                _entry(strategy=f"s_{i}", symbol=f"SYM_{i}", score=1.0 - i * 0.1)
                for i in range(5)
            ]),
        )
        assert len(rec.recommended_strategies) == 2
        # 나머지 3 → watchlist 로 demote.
        assert len(rec.watchlist_strategies) >= 3
        # demote reason carry.
        demoted = [e for e in rec.watchlist_strategies
                    if "조합 상한" in e.rationale]
        assert len(demoted) >= 3

    def test_score_desc_order(self):
        rec = build_paper_combination_recommendation(
            operator_report=_report([
                _entry(strategy="a", symbol="A", score=0.10),
                _entry(strategy="b", symbol="B", score=0.30),
                _entry(strategy="c", symbol="C", score=0.20),
            ]),
        )
        scores = [e.score for e in rec.recommended_strategies]
        assert scores == sorted(scores, reverse=True)
        assert rec.recommended_strategies[0].strategy == "b"   # 0.30 top


# ─────────────────────────────────────────────────────────────────────────────
# 5. 동일 종목/전략 쏠림 경고
# ─────────────────────────────────────────────────────────────────────────────


class TestConcentrationWarnings:
    def test_all_same_strategy_warning(self):
        rec = build_paper_combination_recommendation(
            operator_report=_report([
                _entry(strategy="sma_crossover", symbol="005930", score=0.20),
                _entry(strategy="sma_crossover", symbol="000660",
                       params={"v": 1}, score=0.10),
            ]),
        )
        joined = " ".join(rec.risk_summary)
        assert "전략" in joined or "다양성" in joined

    def test_all_same_symbol_warning(self):
        rec = build_paper_combination_recommendation(
            operator_report=_report([
                _entry(strategy="sma_crossover", symbol="005930", score=0.20),
                _entry(strategy="rsi_reversion", symbol="005930", score=0.10),
            ]),
        )
        joined = " ".join(rec.risk_summary)
        assert "종목" in joined or "분산" in joined


# ─────────────────────────────────────────────────────────────────────────────
# 6. 7 출력 필드 lock
# ─────────────────────────────────────────────────────────────────────────────


class TestSevenOutputFields:
    def test_all_seven_fields_present(self):
        rec = build_paper_combination_recommendation(
            operator_report=_report([_entry()]),
        )
        d = rec.to_dict()
        required = [
            "recommended_strategies",
            "excluded_strategies",
            "watchlist_strategies",
            "no_candidate_reason",
            "risk_summary",
            "agent_rationale",
            "operator_next_action",
        ]
        for f in required:
            assert f in d, f"missing required field: {f}"

    def test_agent_rationale_non_empty_when_recommended(self):
        rec = build_paper_combination_recommendation(
            operator_report=_report([_entry()]),
        )
        assert rec.agent_rationale
        assert "추천" in rec.agent_rationale or "advisory" in rec.agent_rationale

    def test_operator_next_action_non_empty(self):
        rec = build_paper_combination_recommendation(inputs=ReportInputs())
        assert len(rec.operator_next_action) >= 1
        # 마지막에 항상 advisory 안내 추가.
        assert any("advisory" in a for a in rec.operator_next_action)


# ─────────────────────────────────────────────────────────────────────────────
# 7. agent_input 직접 주입 (4-01 흐름)
# ─────────────────────────────────────────────────────────────────────────────


class TestAgentInputInjection:
    def test_strategy_agent_input_direct(self):
        report = _report([_entry()])
        agent_input = build_strategy_agent_input(operator_report=report)
        rec = build_paper_combination_recommendation(agent_input=agent_input)
        assert rec.status == PaperCombinationStatus.RECOMMEND_PAPER
        assert rec.metadata["source_item_count"] == 1
