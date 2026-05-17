"""#4-03: Overfit Warning Agent 테스트.

invariant:
- `OverfitWarning` / `OverfitWarningReport` 둘 다 is_order_signal=False /
  auto_apply_allowed=False / is_live_authorization=False / auto_disable=False
  (`__post_init__` ValueError 가드).
- OVERFIT_RISK 전략은 recommended_combo 에 포함되지 않음 (apply_overfit_filter).
- OVERFIT_RISK 전략은 excluded (default) 또는 watchlist=held (demote_to_watchlist=True) 로 분류.
- HEALTHY 전략은 추천 가능.
- 모든 후보가 OVERFIT_RISK 면 ALL_HOLD 또는 NO_CANDIDATES_TODAY.
- 사유에 "훈련구간에서만 좋고 검증구간에서 성과 저하" 문구 포함.
- 운영자 노트 "실제 Paper 운용 전 재검증 필요" 포함.
- BUY/SELL/HOLD 주문 방향 라벨 0건 — JSON 출력에도 없음.
- broker / OrderExecutor / route_order / 외부 HTTP / AI SDK import 0건.
- secret / API key / 계좌번호 / `.env` 접근 0건.
- 전략 자동 비활성 코드 0건 (`auto_disable=False`).
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
from app.agents.overfit_warning_agent import (
    DEFAULT_SUSPECT_GAP_THRESHOLD,
    OVERFIT_SCHEMA_VERSION,
    OverfitAction,
    OverfitVerdict,
    OverfitWarning,
    OverfitWarningAgent,
    OverfitWarningReport,
    apply_overfit_filter,
    build_overfit_warning_report,
)
from app.agents.strategy_combination_recommender import (
    OverallRecommendation,
    build_combination_recommendation,
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
# Helper — synthetic OperatorReport with walk_forward stage carrying extras
# ─────────────────────────────────────────────────────────────────────────────


def _entry(
    *,
    strategy="sma_crossover",
    symbol="005930",
    params=None,
    status=ReportStatus.READY_FOR_PAPER,
    wf_verdict="HEALTHY",
    train_avg=720.0,
    val_avg=580.0,
    score=0.05,
    risk_signals=None,
    exclusion_reasons=None,
):
    wf_extra = {"fold_count": 5,
                "train_expectancy_avg": train_avg,
                "val_expectancy_avg":   val_avg}
    return StrategyEntry(
        strategy_id=strategy,
        display_name=f"{strategy} display",
        symbol=symbol,
        params=params or {},
        status=status,
        pipeline_stages=[
            PipelineStage(name="3-02", verdict="BACKTEST_PASS",
                          extra={"metrics": {"profit_factor": 1.6,
                                              "max_drawdown": 0.08,
                                              "expectancy": 500.0,
                                              "win_rate": 0.55,
                                              "trade_count": 45}}),
            PipelineStage(name="3-03", verdict="PAPER_CANDIDATE"),
            PipelineStage(name="3-04", verdict=wf_verdict, extra=wf_extra),
            PipelineStage(name="3-05", verdict="PASS"),
        ],
        risk_metrics={"profit_factor": 1.6, "max_drawdown": 0.08,
                      "expectancy": 500.0, "win_rate": 0.55,
                      "trade_count": 45},
        risk_signals=risk_signals or [],
        exclusion_reasons=exclusion_reasons or [],
        score=score,
    )


def _build_report(entries):
    paper = [e for e in entries if e.status == ReportStatus.READY_FOR_PAPER]
    excluded = [e for e in entries if e.status != ReportStatus.READY_FOR_PAPER]
    overall = ReportStatus.READY_FOR_PAPER if paper else (
        ReportStatus.NO_CANDIDATE if entries else ReportStatus.NO_CANDIDATE
    )
    return OperatorReport(
        generated_at="2026-05-17T00:00:00+00:00",
        overall_status=overall,
        paper_ready_count=len(paper),
        excluded_count=len(excluded),
        entries=entries,
        paper_candidates=paper,
        excluded=excluded,
        reasons_no_candidate=[] if paper else (
            ["no_candidate_passed_all_required_stages"] if entries else
            ["no_pipeline_results_loaded"]
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. OVERFIT_RISK → recommended_combo 에 포함되지 않음 (필터 통한 데몬)
# ─────────────────────────────────────────────────────────────────────────────


class TestOverfitExcludedFromRecommendation:
    def test_overfit_risk_status_already_excluded_by_4_02(self):
        """3-08 status=OVERFIT_RISK 면 이미 4-02 가 EXCLUDE 처리.

        본 4-03 의 출발선 invariant — 회귀 검증.
        """
        report = _build_report([
            _entry(status=ReportStatus.OVERFIT_RISK, wf_verdict="OVERFIT_RISK",
                   train_avg=800.0, val_avg=100.0),
        ])
        combo = build_combination_recommendation(operator_report=report)
        assert combo.recommended_count == 0
        assert combo.excluded_count == 1

    def test_overfit_filter_demotes_walk_forward_overfit_risk(self):
        """status 가 READY_FOR_PAPER 라도 walk_forward verdict 가 OVERFIT_RISK 면
        본 필터가 추가로 demote.
        """
        report = _build_report([
            _entry(wf_verdict="OVERFIT_RISK", train_avg=800.0, val_avg=100.0),
            _entry(strategy="rsi_reversion", symbol="000660", wf_verdict="HEALTHY",
                   train_avg=600.0, val_avg=550.0),
        ])
        combo = build_combination_recommendation(operator_report=report)
        warnings = build_overfit_warning_report(operator_report=report)
        filtered = apply_overfit_filter(combo, warnings)
        recommended_strats = {(d.strategy, d.symbol) for d in filtered.recommended_combo}
        assert ("sma_crossover", "005930") not in recommended_strats
        assert ("rsi_reversion", "000660") in recommended_strats

    def test_overfit_demote_to_watchlist_moves_to_held(self):
        report = _build_report([
            _entry(wf_verdict="OVERFIT_RISK", train_avg=800.0, val_avg=100.0),
        ])
        combo = build_combination_recommendation(operator_report=report)
        warnings = build_overfit_warning_report(
            operator_report=report, demote_to_watchlist=True,
        )
        filtered = apply_overfit_filter(combo, warnings, demote_to_watchlist=True)
        assert filtered.recommended_count == 0
        # demote_to_watchlist=True → held 로 이동.
        assert filtered.held_count >= 1

    def test_overfit_default_demotes_to_excluded(self):
        report = _build_report([
            _entry(wf_verdict="OVERFIT_RISK", train_avg=800.0, val_avg=100.0),
        ])
        combo = build_combination_recommendation(operator_report=report)
        warnings = build_overfit_warning_report(operator_report=report)
        filtered = apply_overfit_filter(combo, warnings)
        assert filtered.recommended_count == 0
        # default → excluded 로 이동.
        assert filtered.excluded_count >= 1


# ─────────────────────────────────────────────────────────────────────────────
# 2. HEALTHY → 추천 가능
# ─────────────────────────────────────────────────────────────────────────────


class TestHealthyAllowed:
    def test_healthy_with_small_gap_is_kept(self):
        report = _build_report([
            _entry(wf_verdict="HEALTHY", train_avg=600.0, val_avg=580.0),
        ])
        warnings = build_overfit_warning_report(operator_report=report)
        w = warnings.warnings[0]
        assert w.overfit_verdict == OverfitVerdict.HEALTHY
        assert w.overfit_flag is False
        assert w.recommendation_action == OverfitAction.KEEP
        assert warnings.healthy_count == 1

    def test_healthy_passes_through_filter(self):
        report = _build_report([
            _entry(wf_verdict="HEALTHY", train_avg=600.0, val_avg=580.0),
        ])
        combo = build_combination_recommendation(operator_report=report)
        warnings = build_overfit_warning_report(operator_report=report)
        filtered = apply_overfit_filter(combo, warnings)
        assert filtered.recommended_count == 1
        assert ("sma_crossover", "005930") in {
            (d.strategy, d.symbol) for d in filtered.recommended_combo
        }


# ─────────────────────────────────────────────────────────────────────────────
# 3. 모든 후보가 OVERFIT_RISK → NO_CANDIDATES_TODAY 또는 ALL_HOLD
# ─────────────────────────────────────────────────────────────────────────────


class TestAllOverfit:
    def test_all_overfit_marks_all_hold_in_warning_report(self):
        report = _build_report([
            _entry(wf_verdict="OVERFIT_RISK", train_avg=800.0, val_avg=100.0),
            _entry(strategy="b", symbol="B", wf_verdict="OVERFIT_RISK",
                   train_avg=900.0, val_avg=50.0),
        ])
        warnings = build_overfit_warning_report(operator_report=report)
        # healthy=0 + overfit/suspect/insufficient > 0 → ALL_HOLD.
        assert warnings.overall_status == OverallRecommendation.ALL_HOLD

    def test_all_overfit_filter_produces_no_recommendations(self):
        report = _build_report([
            _entry(wf_verdict="OVERFIT_RISK", train_avg=800.0, val_avg=100.0),
            _entry(strategy="b", symbol="B", wf_verdict="OVERFIT_RISK",
                   train_avg=900.0, val_avg=50.0),
        ])
        combo = build_combination_recommendation(operator_report=report)
        warnings = build_overfit_warning_report(operator_report=report)
        filtered = apply_overfit_filter(combo, warnings)
        assert filtered.recommended_count == 0
        # Filter 후 overall — held/excluded 만 남았으면 NO_CANDIDATES_TODAY 또는 ALL_HOLD.
        assert filtered.overall_recommendation in (
            OverallRecommendation.NO_CANDIDATES_TODAY,
            OverallRecommendation.ALL_HOLD,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 4. train_validation_gap 계산 + SUSPECT 분류
# ─────────────────────────────────────────────────────────────────────────────


class TestTrainValidationGap:
    def test_overfit_risk_carries_gap_in_reason(self):
        """train=800, val=100 → gap = (800-100)/800 = 0.875"""
        report = _build_report([
            _entry(wf_verdict="OVERFIT_RISK", train_avg=800.0, val_avg=100.0),
        ])
        warnings = build_overfit_warning_report(operator_report=report)
        w = warnings.warnings[0]
        assert w.train_validation_gap is not None
        assert abs(w.train_validation_gap - 0.875) < 0.01
        # 사유에 한국어 문구 carry.
        assert "훈련구간" in (w.overfit_reason or "")
        assert "검증구간" in (w.overfit_reason or "")
        # 사유에 gap 숫자 포함.
        assert "gap=" in (w.overfit_reason or "") or "0.87" in (w.overfit_reason or "")

    def test_healthy_with_high_gap_marks_suspect(self):
        """walk_forward=HEALTHY but train=1000, val=400 → gap=0.6 >= 0.5 → SUSPECT."""
        report = _build_report([
            _entry(wf_verdict="HEALTHY", train_avg=1000.0, val_avg=400.0),
        ])
        warnings = build_overfit_warning_report(operator_report=report)
        w = warnings.warnings[0]
        assert w.overfit_verdict == OverfitVerdict.SUSPECT
        assert w.recommendation_action == OverfitAction.HOLD
        assert w.overfit_flag is False   # OVERFIT_RISK 아님 — SUSPECT.

    def test_suspect_threshold_configurable(self):
        report = _build_report([
            _entry(wf_verdict="HEALTHY", train_avg=600.0, val_avg=400.0),
        ])
        # gap = (600-400)/600 = 0.33
        # threshold=0.2 면 SUSPECT.
        warnings = build_overfit_warning_report(
            operator_report=report, suspect_gap_threshold=0.2,
        )
        assert warnings.warnings[0].overfit_verdict == OverfitVerdict.SUSPECT
        # threshold=0.5 (default) 이면 HEALTHY.
        warnings_default = build_overfit_warning_report(operator_report=report)
        assert warnings_default.warnings[0].overfit_verdict == OverfitVerdict.HEALTHY

    def test_insufficient_data_when_walk_forward_missing(self):
        # walk_forward verdict 없는 케이스 — agent_input 만 (operator_report 없음).
        agent_input = build_strategy_agent_input(inputs=ReportInputs())
        warnings = build_overfit_warning_report(agent_input=agent_input)
        # 후보 0건 → overall=NO_CANDIDATES_TODAY.
        assert warnings.overall_status == OverallRecommendation.NO_CANDIDATES_TODAY


# ─────────────────────────────────────────────────────────────────────────────
# 5. 필수 6 출력 필드 + operator_note 검증
# ─────────────────────────────────────────────────────────────────────────────


class TestRequired6Fields:
    def test_warning_has_all_six_required_fields(self):
        report = _build_report([
            _entry(wf_verdict="OVERFIT_RISK", train_avg=800.0, val_avg=100.0),
        ])
        warnings = build_overfit_warning_report(operator_report=report)
        d = warnings.warnings[0].to_dict()
        # User spec required fields.
        required = [
            "overfit_flag",
            "overfit_reason",
            "train_validation_gap",
            "walk_forward_verdict",
            "recommendation_action",
            "operator_note",
        ]
        for f in required:
            assert f in d, f"missing required field: {f}"

    def test_operator_note_says_recheck_before_paper(self):
        report = _build_report([
            _entry(wf_verdict="OVERFIT_RISK", train_avg=800.0, val_avg=100.0),
        ])
        warnings = build_overfit_warning_report(operator_report=report)
        w = warnings.warnings[0]
        # 운영자 노트 "실제 Paper 운용 전 재검증 필요" 메시지.
        assert "Paper 운용" in (w.operator_note or "")
        assert "재검증" in (w.operator_note or "")
        # 전체 운영자 노트에도 carry.
        joined = " ".join(warnings.operator_notes)
        assert "OVERFIT_RISK" in joined or "재검증" in joined


# ─────────────────────────────────────────────────────────────────────────────
# 6. Invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestInvariants:
    def test_warning_invariants_immutable(self):
        for kwargs in (
            {"is_order_signal": True},
            {"auto_apply_allowed": True},
            {"is_live_authorization": True},
            {"auto_disable": True},
        ):
            with pytest.raises(ValueError):
                OverfitWarning(
                    strategy="x", symbol="X", params={},
                    overfit_flag=False, overfit_reason=None,
                    train_validation_gap=None, walk_forward_verdict=None,
                    recommendation_action=OverfitAction.KEEP,
                    operator_note=None,
                    overfit_verdict=OverfitVerdict.HEALTHY,
                    **kwargs,
                )

    def test_report_invariants_immutable(self):
        for kwargs in (
            {"is_order_signal": True},
            {"auto_apply_allowed": True},
            {"is_live_authorization": True},
            {"auto_disable": True},
        ):
            with pytest.raises(ValueError):
                OverfitWarningReport(
                    generated_at="x", schema_version="1.0",
                    overall_status=OverallRecommendation.NO_CANDIDATES_TODAY,
                    warnings=[], overfit_count=0, suspect_count=0,
                    insufficient_data_count=0, healthy_count=0,
                    **kwargs,
                )

    def test_to_dict_carries_invariants_at_all_levels(self):
        report = _build_report([
            _entry(wf_verdict="OVERFIT_RISK", train_avg=800.0, val_avg=100.0),
        ])
        warnings = build_overfit_warning_report(operator_report=report)
        d = warnings.to_dict()
        assert d["is_order_signal"]       is False
        assert d["auto_apply_allowed"]    is False
        assert d["is_live_authorization"] is False
        assert d["auto_disable"]          is False
        for w in d["warnings"]:
            assert w["is_order_signal"]       is False
            assert w["auto_apply_allowed"]    is False
            assert w["is_live_authorization"] is False
            assert w["auto_disable"]          is False


# ─────────────────────────────────────────────────────────────────────────────
# 7. Action / Verdict enums — no order-direction values
# ─────────────────────────────────────────────────────────────────────────────


class TestEnums:
    def test_overfit_action_has_no_order_direction(self):
        values = {a.value for a in OverfitAction}
        forbidden = {"BUY", "SELL", "BUY_SIGNAL", "SELL_SIGNAL",
                     "PLACE_ORDER", "EXECUTE", "FILL"}
        assert not (values & forbidden)
        # 4 액션만.
        assert values == {"KEEP", "HOLD", "WATCHLIST", "EXCLUDE"}

    def test_overfit_verdict_values(self):
        values = {v.value for v in OverfitVerdict}
        assert values == {"HEALTHY", "SUSPECT", "OVERFIT_RISK", "INSUFFICIENT_DATA"}


# ─────────────────────────────────────────────────────────────────────────────
# 8. Agent (AgentBase compliance)
# ─────────────────────────────────────────────────────────────────────────────


class TestAgent:
    def test_agent_metadata_shape(self):
        agent = OverfitWarningAgent()
        meta = agent.metadata
        assert meta.name == "overfit_warning_agent"
        assert meta.role == AgentRole.RISK_AUDITOR
        assert meta.can_execute_order is False

    def test_agent_run_with_overfit_returns_warn(self):
        report = _build_report([
            _entry(wf_verdict="OVERFIT_RISK", train_avg=800.0, val_avg=100.0),
        ])
        agent = OverfitWarningAgent()
        out = agent.run(AgentContext(extra={"operator_report": report}))
        assert isinstance(out, AgentOutput)
        # overfit > 0 → WARN.
        assert out.decision == AgentDecision.WARN
        assert out.is_order_intent is False
        assert out.can_execute_order is False
        d = out.to_dict()
        rpt = d["metadata"]["overfit_warning_report"]
        assert rpt["overfit_count"] == 1

    def test_agent_run_with_healthy_returns_report_not_warn(self):
        report = _build_report([
            _entry(wf_verdict="HEALTHY", train_avg=600.0, val_avg=580.0),
        ])
        agent = OverfitWarningAgent()
        out = agent.run(AgentContext(extra={"operator_report": report}))
        # overfit_count=0 → REPORT.
        assert out.decision == AgentDecision.REPORT

    def test_agent_run_with_no_inputs_returns_no_candidates(self):
        agent = OverfitWarningAgent()
        out = agent.run(AgentContext(extra={}))
        rpt = out.metadata["overfit_warning_report"]
        assert rpt["overall_status"] == "NO_CANDIDATES_TODAY"

    def test_agent_output_invariants(self):
        report = _build_report([
            _entry(wf_verdict="OVERFIT_RISK", train_avg=800.0, val_avg=100.0),
        ])
        agent = OverfitWarningAgent()
        out = agent.run(AgentContext(extra={"operator_report": report}))
        d = out.to_dict()
        assert d["is_order_intent"]   is False
        assert d["can_execute_order"] is False
        meta = d["metadata"]
        assert meta["advisory_only"]          is True
        assert meta["is_order_signal"]        is False
        assert meta["auto_apply_allowed"]     is False
        assert meta["is_live_authorization"]  is False
        assert meta["auto_disable"]           is False

    def test_agent_does_not_emit_buy_sell_hold_order_signals(self):
        report = _build_report([
            _entry(wf_verdict="OVERFIT_RISK", train_avg=800.0, val_avg=100.0),
        ])
        agent = OverfitWarningAgent()
        out = agent.run(AgentContext(extra={"operator_report": report}))
        text = json.dumps(out.to_dict(), ensure_ascii=False)
        # `auto_disable=False` invariant 와 정적 grep 가드가 *실제 mutation*
        # 을 차단. 본 검사는 *주문 방향 / 실거래 활성화* 표현만 검증
        # (disclaimer 가 "수행하지 않습니다" 형태로 일부 단어를 언급할 수 있음).
        forbidden = [
            "\"action\": \"BUY\"",
            "\"action\": \"SELL\"",
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
# 9. Static guards — forbidden imports / safety flag mutation / secrets
# ─────────────────────────────────────────────────────────────────────────────


_MOD_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "agents"
    / "overfit_warning_agent.py"
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
                f"forbidden in overfit_warning_agent.py: {pat}"

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
                f"forbidden http/AI in overfit_warning_agent.py: {pat}"

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
                f"safety flag mutation in overfit_warning_agent.py: {pat}"

    def test_no_strategy_auto_disable(self):
        """전략 자동 비활성 코드 영구 금지 — `auto_disable=False` invariant 외에
        실제 mutation 패턴도 0건.
        """
        src = _MOD_PATH.read_text(encoding="utf-8")
        bad = [
            r"strategy\.enabled\s*=\s*False",
            r"\.disable_strategy\(",
            r"\.set_strategy_enabled\(False\)",
            r"STRATEGY_REGISTRY\[[^]]+\]\s*=\s*None",
        ]
        for pat in bad:
            assert not re.search(pat, src), \
                f"strategy auto-disable in overfit_warning_agent.py: {pat}"


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
        warning_fields = OverfitWarning.__dataclass_fields__.keys()
        report_fields  = OverfitWarningReport.__dataclass_fields__.keys()
        secret_names = [
            "api_key", "secret", "app_key", "app_secret", "access_token",
            "account_no", "account_number", "anthropic_api_key",
            "openai_api_key", "kis_app_key", "kis_app_secret",
        ]
        for name in secret_names:
            assert name not in warning_fields, f"warning has secret field: {name}"
            assert name not in report_fields,  f"report has secret field: {name}"


# ─────────────────────────────────────────────────────────────────────────────
# 10. Schema field lock
# ─────────────────────────────────────────────────────────────────────────────


class TestSchemaFieldLock:
    def test_warning_has_required_fields(self):
        required = {
            "strategy", "symbol", "params",
            "overfit_flag", "overfit_reason", "train_validation_gap",
            "walk_forward_verdict", "recommendation_action",
            "operator_note", "overfit_verdict",
            "is_order_signal", "auto_apply_allowed", "is_live_authorization",
            "auto_disable",
        }
        actual = set(OverfitWarning.__dataclass_fields__.keys())
        missing = required - actual
        assert not missing, f"missing warning fields: {missing}"

    def test_report_has_required_fields(self):
        required = {
            "generated_at", "schema_version", "overall_status",
            "warnings", "overfit_count", "suspect_count",
            "insufficient_data_count", "healthy_count",
            "operator_notes", "advisory_disclaimer", "metadata",
            "is_order_signal", "auto_apply_allowed",
            "is_live_authorization", "auto_disable",
        }
        actual = set(OverfitWarningReport.__dataclass_fields__.keys())
        missing = required - actual
        assert not missing, f"missing report fields: {missing}"

    def test_schema_version_carried(self):
        report = build_overfit_warning_report(inputs=ReportInputs())
        assert report.schema_version == OVERFIT_SCHEMA_VERSION

    def test_default_threshold_value(self):
        assert DEFAULT_SUSPECT_GAP_THRESHOLD == 0.5
