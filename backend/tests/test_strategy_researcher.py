"""#55: Strategy Researcher Agent tests.

본 Agent는 advisory 분석 전용 — 자동 코드 / 자동 파라미터 / 자동 주문 0건.
이 테스트는 그 invariant를 *코드 단*에서 강제한다.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.agents.base import AgentContext, AgentDecision, AgentRole
from app.agents.strategy_researcher import (
    BacktestSummary,
    DataQualitySummary,
    FindingCode,
    MonteCarloSummary,
    PromotionGateSummary,
    ResearchSeverity,
    StrategyResearchReport,
    StrategyResearcherAgent,
    StrategyResearcherInput,
    SuggestionCategory,
    WalkForwardSummary,
    analyze_strategy,
    load_backtest_run,
    load_recent_backtest_runs,
)
from app.db.models import BacktestRun


_MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "agents" / "strategy_researcher.py"


# ====================================================================
# Helpers
# ====================================================================


def _baseline_backtest(**overrides) -> BacktestSummary:
    """건강한 baseline — 모든 임계 통과."""
    base = dict(
        run_id=1,
        strategy="sma_cross",
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc).replace(tzinfo=None),
        params={"window_short": 5, "window_long": 20},
        initial_cash=10_000_000,
        bars_processed=10_000,
        trade_count=200,
        win_count=110,
        loss_count=90,
        total_pnl=2_000_000,
        final_cash=12_000_000,
        win_rate=0.55,
        profit_factor=1.40,
        expectancy=10_000.0,
        max_drawdown=1_000_000,
        max_consecutive_losses=4,
        max_consecutive_wins=8,
        sharpe_ratio=1.5,
        avg_win=20_000.0,
        avg_loss=-15_000.0,
        hourly_pnl={9: 600_000, 10: 500_000, 13: 400_000, 14: 300_000, 15: 200_000},
        data_symbol="005930",
        data_interval="5m",
        data_start=datetime(2025, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None),
        data_end=datetime(2026, 5, 1, tzinfo=timezone.utc).replace(tzinfo=None),
    )
    base.update(overrides)
    return BacktestSummary(**base)


# ====================================================================
# Output guard invariants
# ====================================================================


class TestReportInvariants:
    def test_report_rejects_auto_apply_allowed_true(self):
        with pytest.raises(ValueError, match="auto_apply_allowed"):
            StrategyResearchReport(
                audit_level=ResearchSeverity.HEALTHY,
                findings=(),
                suggestions=(),
                required_next_tests=(),
                markdown_report="",
                summary_lines=(),
                strategy="x",
                run_id=1,
                auto_apply_allowed=True,         # ← invariant 위반
                is_order_signal=False,
                created_at=datetime.now(timezone.utc),
            )

    def test_report_rejects_is_order_signal_true(self):
        with pytest.raises(ValueError, match="is_order_signal"):
            StrategyResearchReport(
                audit_level=ResearchSeverity.HEALTHY,
                findings=(),
                suggestions=(),
                required_next_tests=(),
                markdown_report="",
                summary_lines=(),
                strategy="x",
                run_id=1,
                auto_apply_allowed=False,
                is_order_signal=True,            # ← invariant 위반
                created_at=datetime.now(timezone.utc),
            )


# ====================================================================
# Severity classification
# ====================================================================


class TestSeverityClassification:
    def test_healthy_baseline_no_findings(self):
        bt = _baseline_backtest()
        report = analyze_strategy(StrategyResearcherInput(backtest=bt))
        assert report.audit_level == ResearchSeverity.HEALTHY
        assert report.findings == ()
        assert report.suggestions == ()
        # markdown 본문은 항상 disclaimer 포함
        assert "자동 반영 안 됨" in report.markdown_report

    def test_negative_expectancy_critical(self):
        bt = _baseline_backtest(expectancy=-5000.0)
        report = analyze_strategy(StrategyResearcherInput(backtest=bt))
        assert report.audit_level == ResearchSeverity.CRITICAL
        codes = {f.code for f in report.findings}
        assert FindingCode.NEGATIVE_EXPECTANCY in codes

    def test_low_profit_factor_below_one_critical(self):
        bt = _baseline_backtest(profit_factor=0.85)
        report = analyze_strategy(StrategyResearcherInput(backtest=bt))
        assert report.audit_level == ResearchSeverity.CRITICAL
        codes = {f.code for f in report.findings}
        assert FindingCode.LOW_PROFIT_FACTOR in codes

    def test_low_pf_under_promotion_threshold_warning(self):
        bt = _baseline_backtest(profit_factor=1.10)  # < 1.20 promotion 임계
        report = analyze_strategy(StrategyResearcherInput(backtest=bt))
        assert report.audit_level == ResearchSeverity.WARNING
        codes = {f.code for f in report.findings}
        assert FindingCode.LOW_PROFIT_FACTOR in codes

    def test_high_max_drawdown_critical(self):
        # MDD 30% > 25% caution 임계 → CRITICAL
        bt = _baseline_backtest(max_drawdown=3_000_000)
        report = analyze_strategy(StrategyResearcherInput(backtest=bt))
        assert report.audit_level == ResearchSeverity.CRITICAL
        codes = {f.code for f in report.findings}
        assert FindingCode.HIGH_MAX_DRAWDOWN in codes
        # 위험 강화 제안 포함
        cats = {s.category for s in report.suggestions}
        assert SuggestionCategory.RISK_TIGHTEN in cats

    def test_low_trade_count_warning(self):
        bt = _baseline_backtest(trade_count=15, win_count=8, loss_count=7)
        report = analyze_strategy(StrategyResearcherInput(backtest=bt))
        assert report.audit_level == ResearchSeverity.WARNING
        codes = {f.code for f in report.findings}
        assert FindingCode.LOW_TRADE_COUNT in codes
        cats = {s.category for s in report.suggestions}
        assert SuggestionCategory.RE_RUN_TEST in cats

    def test_low_win_rate_warning(self):
        bt = _baseline_backtest(win_rate=0.30)
        report = analyze_strategy(StrategyResearcherInput(backtest=bt))
        codes = {f.code for f in report.findings}
        assert FindingCode.LOW_WIN_RATE in codes

    def test_high_consecutive_losses_warning(self):
        bt = _baseline_backtest(max_consecutive_losses=10)
        report = analyze_strategy(StrategyResearcherInput(backtest=bt))
        codes = {f.code for f in report.findings}
        assert FindingCode.HIGH_CONSECUTIVE_LOSSES in codes
        cats = {s.category for s in report.suggestions}
        assert SuggestionCategory.ADD_FILTER in cats

    def test_hourly_imbalance_caution(self):
        # 단일 hour가 양수 PnL의 80% 차지 — CAUTION
        bt = _baseline_backtest(hourly_pnl={9: 800_000, 10: 100_000, 13: 100_000})
        report = analyze_strategy(StrategyResearcherInput(backtest=bt))
        codes = {f.code for f in report.findings}
        assert FindingCode.HOURLY_PNL_IMBALANCE in codes
        cats = {s.category for s in report.suggestions}
        assert SuggestionCategory.TIMEFRAME_FILTER in cats


# ====================================================================
# Walk-forward
# ====================================================================


class TestWalkForward:
    def test_walk_forward_fail_critical(self):
        bt = _baseline_backtest()
        wf = WalkForwardSummary(
            recommendation="FAIL",
            fold_count=5,
            positive_fold_ratio=0.20,
            warnings=("low positive fold ratio",),
        )
        report = analyze_strategy(StrategyResearcherInput(backtest=bt, walk_forward=wf))
        assert report.audit_level == ResearchSeverity.CRITICAL
        codes = {f.code for f in report.findings}
        assert FindingCode.WALK_FORWARD_FAIL in codes
        assert FindingCode.LOW_POSITIVE_FOLD_RATIO in codes
        cats = {s.category for s in report.suggestions}
        assert SuggestionCategory.OVERFIT_GUARD in cats

    def test_walk_forward_caution_warning(self):
        bt = _baseline_backtest()
        wf = WalkForwardSummary(
            recommendation="CAUTION",
            fold_count=5,
            positive_fold_ratio=0.65,
        )
        report = analyze_strategy(StrategyResearcherInput(backtest=bt, walk_forward=wf))
        codes = {f.code for f in report.findings}
        assert FindingCode.WALK_FORWARD_CAUTION in codes

    def test_single_fold_dominance_warning(self):
        bt = _baseline_backtest()
        wf = WalkForwardSummary(
            recommendation="PASS",
            fold_count=5,
            positive_fold_ratio=0.80,
            single_best_fold_share=0.85,
        )
        report = analyze_strategy(StrategyResearcherInput(backtest=bt, walk_forward=wf))
        codes = {f.code for f in report.findings}
        assert FindingCode.SINGLE_FOLD_DOMINANCE in codes

    def test_overfit_risk_high_warning(self):
        bt = _baseline_backtest()
        wf = WalkForwardSummary(recommendation="PASS", overfit_risk_score=0.75)
        report = analyze_strategy(StrategyResearcherInput(backtest=bt, walk_forward=wf))
        codes = {f.code for f in report.findings}
        assert FindingCode.OVERFIT_RISK_HIGH in codes


# ====================================================================
# Monte Carlo
# ====================================================================


class TestMonteCarlo:
    def test_high_risk_of_ruin_critical(self):
        bt = _baseline_backtest()
        mc = MonteCarloSummary(
            method="bootstrap",
            iterations=1000,
            risk_of_ruin=0.15,
            promotion_risk_flag="FAIL",
        )
        report = analyze_strategy(StrategyResearcherInput(backtest=bt, monte_carlo=mc))
        assert report.audit_level == ResearchSeverity.CRITICAL
        codes = {f.code for f in report.findings}
        assert FindingCode.MONTE_CARLO_RUIN_HIGH in codes
        cats = {s.category for s in report.suggestions}
        assert SuggestionCategory.SHRINK_SIZE in cats

    def test_moderate_risk_of_ruin_warning(self):
        bt = _baseline_backtest()
        mc = MonteCarloSummary(risk_of_ruin=0.07)
        report = analyze_strategy(StrategyResearcherInput(backtest=bt, monte_carlo=mc))
        codes = {f.code for f in report.findings}
        assert FindingCode.MONTE_CARLO_RUIN_HIGH in codes

    def test_fat_tail_detection(self):
        bt = _baseline_backtest()
        mc = MonteCarloSummary(
            risk_of_ruin=0.02,
            p05_total_pnl=-2_000_000,  # 큰 좌측 꼬리
            p50_total_pnl=1_000_000,
        )
        report = analyze_strategy(StrategyResearcherInput(backtest=bt, monte_carlo=mc))
        codes = {f.code for f in report.findings}
        assert FindingCode.MONTE_CARLO_FAT_TAIL in codes


# ====================================================================
# Data quality
# ====================================================================


class TestDataQuality:
    def test_poor_data_quality_critical(self):
        bt = _baseline_backtest()
        quality = (
            DataQualitySummary(symbol="005930", interval="5m", score=45.0, grade="POOR"),
            DataQualitySummary(symbol="000660", interval="5m", score=92.0, grade="GOOD"),
        )
        report = analyze_strategy(StrategyResearcherInput(backtest=bt, data_quality=quality))
        assert report.audit_level == ResearchSeverity.CRITICAL
        codes = {f.code for f in report.findings}
        assert FindingCode.DATA_QUALITY_POOR in codes
        cats = {s.category for s in report.suggestions}
        assert SuggestionCategory.DATA_QUALITY in cats

    def test_warning_data_quality_warning_only(self):
        bt = _baseline_backtest()
        quality = (
            DataQualitySummary(symbol="005930", interval="5m", score=70.0, grade="WARNING"),
        )
        report = analyze_strategy(StrategyResearcherInput(backtest=bt, data_quality=quality))
        codes = {f.code for f in report.findings}
        assert FindingCode.DATA_QUALITY_WARNING in codes


# ====================================================================
# Promotion gate
# ====================================================================


class TestPromotionGate:
    def test_blocked_decision_critical(self):
        bt = _baseline_backtest()
        pg = PromotionGateSummary(
            current_stage="PAPER",
            target_stage="LIVE_MANUAL_APPROVAL",
            decision="BLOCKED",
            failed_criteria=("missing_human_approval",),
            required_actions=("운영자 승인 필요", "shadow 7일 추가"),
        )
        report = analyze_strategy(StrategyResearcherInput(backtest=bt, promotion_gate=pg))
        assert report.audit_level == ResearchSeverity.CRITICAL
        codes = {f.code for f in report.findings}
        assert FindingCode.PROMOTION_BLOCKED in codes
        cats = {s.category for s in report.suggestions}
        assert SuggestionCategory.PROMOTION_BLOCK in cats

    def test_failed_decision_warning(self):
        bt = _baseline_backtest()
        pg = PromotionGateSummary(decision="FAIL", failed_criteria=("low_pf",))
        report = analyze_strategy(StrategyResearcherInput(backtest=bt, promotion_gate=pg))
        codes = {f.code for f in report.findings}
        assert FindingCode.PROMOTION_FAILED in codes


# ====================================================================
# Markdown report
# ====================================================================


class TestMarkdownReport:
    def test_markdown_contains_required_sections(self):
        bt = _baseline_backtest()
        report = analyze_strategy(StrategyResearcherInput(backtest=bt))
        md = report.markdown_report
        assert "# Strategy Research Report" in md
        assert "## 1. 분석 대상" in md
        assert "## 2. 핵심 metric" in md
        assert "## 3. Findings" in md
        assert "## 4. 개선 제안" in md
        assert "## 5. Required Next Tests" in md
        assert "## 6. 한계" in md

    def test_markdown_contains_disclaimer(self):
        bt = _baseline_backtest()
        report = analyze_strategy(StrategyResearcherInput(backtest=bt))
        md = report.markdown_report
        assert "자동 반영 안 됨" in md
        assert "PR 검토 필요" in md
        assert "운영자 검토" in md
        assert "별도 PR" in md

    def test_markdown_no_buy_sell_hold_signals(self):
        bt = _baseline_backtest(profit_factor=0.7, expectancy=-1000.0)
        report = analyze_strategy(StrategyResearcherInput(backtest=bt))
        md = report.markdown_report
        # 본 Agent는 매수/매도 결정을 만들지 않는다 — 매수 실행 / 매도 실행 같은
        # *결정 명령*이 markdown에 없어야 한다.
        assert "매수 실행" not in md
        assert "매도 실행" not in md
        assert "BUY signal" not in md
        assert "SELL signal" not in md

    def test_markdown_required_validation_checkboxes(self):
        bt = _baseline_backtest(profit_factor=0.5)  # critical → 제안 발생
        report = analyze_strategy(StrategyResearcherInput(backtest=bt))
        md = report.markdown_report
        # required_validation은 [ ] 체크박스로 표현 — 운영자가 *수동* 실행한다는 의미
        assert "[ ]" in md

    def test_summary_lines_friendly(self):
        bt = _baseline_backtest()
        report = analyze_strategy(StrategyResearcherInput(backtest=bt))
        assert len(report.summary_lines) >= 3
        joined = "\n".join(report.summary_lines)
        assert "advisory" in joined or "자동 반영 안 됨" in joined


# ====================================================================
# Required next tests
# ====================================================================


class TestRequiredNextTests:
    def test_healthy_has_default_recheck(self):
        bt = _baseline_backtest()
        report = analyze_strategy(StrategyResearcherInput(backtest=bt))
        assert report.required_next_tests
        assert any("재검증" in t for t in report.required_next_tests)

    def test_critical_includes_operator_review(self):
        bt = _baseline_backtest(profit_factor=0.5)
        report = analyze_strategy(StrategyResearcherInput(backtest=bt))
        joined = " · ".join(report.required_next_tests)
        # 어떤 형태로든 운영자 검토 / 별도 PR 항목이 들어 있어야 함
        assert "운영자" in joined or "PR" in joined or "백테스트" in joined


# ====================================================================
# Agent class (#51 AgentBase 호환)
# ====================================================================


class TestAgentClass:
    def test_agent_metadata(self):
        agent = StrategyResearcherAgent()
        meta = agent.metadata
        assert meta.role == AgentRole.STRATEGY_RESEARCHER
        assert meta.can_execute_order is False
        assert "advisory" in meta.description.lower() or "자동 반영" in meta.description

    def test_agent_run_with_no_input_returns_no_op(self):
        agent = StrategyResearcherAgent()
        out = agent.run(AgentContext())
        assert out.decision == AgentDecision.NO_OP
        assert out.is_order_intent is False
        assert out.can_execute_order is False

    def test_agent_run_with_healthy_input_returns_report(self):
        agent = StrategyResearcherAgent()
        bt = _baseline_backtest()
        ctx = AgentContext(extra={"researcher_input": StrategyResearcherInput(backtest=bt)})
        out = agent.run(ctx)
        assert out.decision == AgentDecision.REPORT
        assert out.metadata["audit_level"] == ResearchSeverity.HEALTHY
        assert out.metadata["auto_apply_allowed"] is False
        assert out.metadata["is_order_signal"] is False

    def test_agent_run_with_critical_input_returns_recommend(self):
        agent = StrategyResearcherAgent()
        bt = _baseline_backtest(profit_factor=0.5, expectancy=-1000.0)
        ctx = AgentContext(extra={"researcher_input": StrategyResearcherInput(backtest=bt)})
        out = agent.run(ctx)
        assert out.decision == AgentDecision.RECOMMEND
        assert out.is_order_intent is False
        assert out.can_execute_order is False


# ====================================================================
# DB helpers (read-only)
# ====================================================================


class TestDBHelpers:
    def test_db_helpers_return_empty_for_fresh_session(self, client):
        db = client.test_db_factory()
        try:
            assert load_recent_backtest_runs(db) == []
            assert load_backtest_run(db, 999) is None
        finally:
            db.close()

    def test_db_helper_loads_seeded_run(self, client):
        db = client.test_db_factory()
        try:
            run = BacktestRun(
                strategy="sma_cross",
                params={"window_short": 5},
                initial_cash=10_000_000,
                quantity=10,
                bars_processed=1000,
                final_cash=11_000_000,
                total_pnl=1_000_000,
                win_count=50, loss_count=30,
                max_drawdown=500_000,
                trades_json=[],
            )
            db.add(run)
            db.commit()
            loaded = load_backtest_run(db, run.id)
            assert loaded is not None
            assert loaded.strategy == "sma_cross"
            recent = load_recent_backtest_runs(db, limit=10)
            assert len(recent) == 1
            recent_filtered = load_recent_backtest_runs(db, strategy="other", limit=10)
            assert recent_filtered == []
        finally:
            db.close()


# ====================================================================
# API endpoints (FastAPI client)
# ====================================================================


class TestAPI:
    def test_recent_endpoint_returns_empty_for_no_runs(self, client):
        res = client.get("/api/agents/strategy-researcher/recent")
        assert res.status_code == 200
        body = res.json()
        assert body["items"] == []

    def test_report_endpoint_404_for_unknown_run(self, client):
        res = client.get("/api/agents/strategy-researcher/report/9999")
        assert res.status_code == 404

    def test_mock_endpoint_works_without_run_id(self, client):
        res = client.post("/api/agents/strategy-researcher/mock", json={})
        assert res.status_code == 200
        body = res.json()
        assert body["auto_apply_allowed"] is False
        assert body["is_order_signal"] is False
        assert body["audit_level"] in ("HEALTHY", "CAUTION", "WARNING", "CRITICAL")

    def test_mock_endpoint_with_critical_inputs(self, client):
        res = client.post("/api/agents/strategy-researcher/mock", json={
            "walk_forward": {
                "recommendation": "FAIL",
                "fold_count": 5,
                "positive_fold_ratio": 0.20,
                "warnings": ["low positive fold ratio"],
            },
            "monte_carlo": {"risk_of_ruin": 0.20},
        })
        assert res.status_code == 200
        body = res.json()
        assert body["audit_level"] == "CRITICAL"
        assert body["auto_apply_allowed"] is False
        assert any("walk_forward" in f["code"] for f in body["findings"])

    def test_report_endpoint_returns_advisory_for_seeded_run(self, client):
        db = client.test_db_factory()
        try:
            run = BacktestRun(
                strategy="x",
                params={},
                initial_cash=10_000_000,
                quantity=10,
                bars_processed=100,
                final_cash=10_500_000,
                total_pnl=500_000,
                win_count=20, loss_count=15,
                max_drawdown=400_000,
                trades_json=[],
            )
            db.add(run)
            db.commit()
            run_id = run.id
        finally:
            db.close()

        res = client.get(f"/api/agents/strategy-researcher/report/{run_id}")
        assert res.status_code == 200
        body = res.json()
        assert body["auto_apply_allowed"] is False
        assert body["is_order_signal"] is False
        assert body["run_id"] == run_id

    def test_api_does_not_create_new_runs(self, client):
        before = client.get("/api/agents/strategy-researcher/recent").json()["items"]
        # mock 호출 — DB write 0건이어야 함
        client.post("/api/agents/strategy-researcher/mock", json={})
        client.post("/api/agents/strategy-researcher/mock", json={"walk_forward": {"recommendation": "FAIL"}})
        after = client.get("/api/agents/strategy-researcher/recent").json()["items"]
        assert len(before) == len(after)


# ====================================================================
# Static module guards (CLAUDE.md 절대 원칙 강제)
# ====================================================================


class TestStaticGuards:
    """본 클래스의 모든 테스트는 strategy_researcher.py 소스 파일을 *직접* 읽어
    금지된 import / 호출이 없는지 확인한다.
    """

    def _source(self) -> str:
        return _MODULE_PATH.read_text(encoding="utf-8")

    def test_module_does_not_import_broker(self):
        src = self._source()
        for forbidden in ("from app.brokers", "import app.brokers"):
            assert forbidden not in src, \
                f"strategy_researcher.py must not import {forbidden}"

    def test_module_does_not_import_executor_or_router(self):
        src = self._source()
        for forbidden in (
            "from app.execution.executor",
            "from app.execution.order_executor",
            "from app.execution.order_router",
            "import app.execution",
        ):
            assert forbidden not in src, \
                f"strategy_researcher.py must not import {forbidden}"

    def test_module_does_not_import_strategies(self):
        # 본 Agent는 strategy 코드를 *모른다*. BacktestRun row만 읽고 분석.
        src = self._source()
        for forbidden in ("from app.strategies", "import app.strategies"):
            assert forbidden not in src, \
                f"strategy_researcher.py must not import {forbidden} — it would imply mutation/auto-apply"

    def test_module_does_not_import_permission_or_approval(self):
        src = self._source()
        # 실제 import / 호출만 금지 — docstring에서 언급은 허용 (정책 설명 목적).
        for forbidden in (
            "from app.permission",
            "import app.permission",
            "from app.ai.assist",
            "import app.ai.assist",
            "submit_candidate(",
            "route_order(",
        ):
            assert forbidden not in src, \
                f"strategy_researcher.py must not register approvals: {forbidden}"

    def test_module_does_not_import_external_http_or_ai(self):
        src = self._source()
        for forbidden in ("import httpx", "import requests", "import urllib3",
                          "from anthropic", "import anthropic",
                          "from openai", "import openai"):
            assert forbidden not in src, \
                f"strategy_researcher.py must not import {forbidden}"

    def test_module_does_not_emit_db_writes(self):
        src = self._source()
        for forbidden in (
            "db.add(", "db.commit(", "db.delete(", "db.merge(",
            "session.add(", "session.commit(", "session.delete(",
            ".insert(", ".update(", ".delete(",
        ):
            # 단, 'select' / 'select_from'은 read-only OK
            assert forbidden not in src, \
                f"strategy_researcher.py must not perform DB writes: {forbidden}"

    def test_module_does_not_call_emergency_stop_or_set_params(self):
        src = self._source()
        # 실제 코드 호출만 금지 (docstring 정책 설명은 허용).
        for forbidden in (
            "self.set_emergency_stop(",
            "risk.set_emergency_stop(",
            "manager.set_emergency_stop(",
            "self.emergency_stop = True",
            "risk_manager.emergency_stop = True",
            "policy.max_order_notional =",
            "policy.max_daily_loss =",
            "strategy.params =",
            ".save_params(",
            ".apply_params(",
            ".update_params(",
        ):
            assert forbidden not in src, \
                f"strategy_researcher.py must not auto-apply: {forbidden}"

    def test_no_buy_sell_hold_in_enum_values(self):
        """ResearchSeverity / FindingCode / SuggestionCategory에 BUY/SELL/HOLD 값 0개."""
        for enum_cls in (ResearchSeverity, FindingCode, SuggestionCategory):
            for member in enum_cls:
                v = str(member.value).upper()
                assert "BUY" not in v, f"{enum_cls.__name__}.{member.name} should not contain BUY"
                assert "SELL" not in v, f"{enum_cls.__name__}.{member.name} should not contain SELL"
                # HOLD를 'shadow holdout' 등 substring으로 쓰지 않는 게 안전
                assert v != "HOLD", f"{enum_cls.__name__}.{member.name} must not be HOLD"

    def test_module_imports_only_from_safe_internal(self):
        """strategy_researcher.py가 사용하는 외부 import가 모두 read-only safe인지."""
        src = self._source()
        # broker 카테고리 키워드가 평문 코드에 등장하면 의심
        for keyword in ("broker.place_order", "OrderExecutor(", "route_order(",
                        "PermissionGate(", "anthropic.Anthropic(",
                        "openai.OpenAI("):
            assert keyword not in src, \
                f"strategy_researcher.py reaches into runtime resource: {keyword}"

    def test_report_dataclass_has_invariant_guards(self):
        """`StrategyResearchReport.__post_init__`에 두 가드가 살아 있는지 확인."""
        src = self._source()
        # 두 invariant 모두 ValueError를 발생시키는지 정적 검사
        assert re.search(r"auto_apply_allowed.*False", src), \
            "auto_apply_allowed=False invariant 가드가 보이지 않음"
        assert re.search(r"is_order_signal.*False", src), \
            "is_order_signal=False invariant 가드가 보이지 않음"

    def test_proposed_change_strings_dont_imply_auto_apply(self):
        """제안 텍스트가 '자동 적용', '바로 반영', '코드 수정' 같은 명령형을
        쓰지 않는지 — 모든 제안은 *advisory* 톤이어야 한다."""
        bt = _baseline_backtest(profit_factor=0.5, max_drawdown=3_000_000,
                                trade_count=10)
        wf = WalkForwardSummary(recommendation="FAIL")
        mc = MonteCarloSummary(risk_of_ruin=0.20)
        quality = (DataQualitySummary(symbol="x", interval="5m", score=40.0, grade="POOR"),)
        pg = PromotionGateSummary(decision="BLOCKED",
                                  required_actions=("test",))
        report = analyze_strategy(StrategyResearcherInput(
            backtest=bt, walk_forward=wf, monte_carlo=mc,
            data_quality=quality, promotion_gate=pg,
        ))
        forbidden_phrases = (
            "자동으로 적용", "자동 반영하라", "코드를 수정하라",
            "파라미터를 저장하라", "지금 변경",
        )
        for s in report.suggestions:
            for phrase in forbidden_phrases:
                assert phrase not in s.proposed_change, \
                    f"Suggestion '{s.title}' has forbidden auto-apply tone: {phrase}"
                assert phrase not in s.rationale, \
                    f"Suggestion '{s.title}' rationale has forbidden tone: {phrase}"
