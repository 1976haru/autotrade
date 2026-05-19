"""#3-15: final Paper combo candidate selector tests.

Covers:
* 7 selection conditions enforced — every failure path produces an
  exclusion reason and matching risk_flag.
* Status matrix: OK (>=2) / MIN_CANDIDATES (==1) / NO_CANDIDATE (0).
* Top-N (default max_candidates=3) ordering by composite_score desc.
* OVERFIT_RISK / STRESS_FAILED / LOW_LIQUIDITY / UNKNOWN auto-excluded.
* Invariants on PaperCandidate: is_order_signal / auto_apply /
  is_live_authorization permanent False, requires_operator_approval
  permanent True, even for top-ranked picks.
* FinalCandidateReport rejects status/candidate-count mismatch.
* Report file generation (JSON/MD/CSV) in tmp_path.
* Static guards.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from app.analytics.final_paper_candidates import (
    FINAL_CANDIDATE_SCHEMA_VERSION,
    CandidateInput,
    FinalCandidateCriteria,
    FinalCandidateReport,
    PaperCandidate,
    SelectionStatus,
    render_markdown,
    render_ranking_csv,
    select_paper_candidates,
    write_reports,
)


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "analytics" / "final_paper_candidates.py"
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _passing(name="sma_crossover", symbol="005930", **over):
    """모든 7 조건을 통과하는 default input."""
    base = dict(
        name=name,
        included_tactics=("MOMENTUM",),
        included_strategies=("sma_crossover",),
        symbol=symbol,
        primary_regime="TREND_UP",
        trade_count=20,
        expectancy=200.0,
        profit_factor=1.5,
        max_drawdown=0.12,
        win_rate=0.55,
        loss_streak=2,
        total_return=4000.0,
        paper_candidate_status="READY_FOR_PAPER",
        walk_forward_verdict="HEALTHY",
        stress_verdict="PASS",
        combo_verdict="PASS",
        regime_combo_verdict="PASS",
        combo_risk_verdict="PASS",
        confirmation_score=3,
        correlation_score=0.3,
        concentration_score=0.4,
    )
    base.update(over)
    return CandidateInput(**base)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Status matrix
# ─────────────────────────────────────────────────────────────────────────────


class TestStatusMatrix:

    def test_no_candidate_with_empty_inputs(self):
        report = select_paper_candidates(inputs=[])
        assert report.status == SelectionStatus.NO_CANDIDATE
        assert report.candidates == []
        assert "입력 데이터 0건" in report.reasons_no_candidate

    def test_no_candidate_when_all_fail(self):
        bad = [_passing(expectancy=-50.0, name="bad1"),
               _passing(profit_factor=0.5, name="bad2"),
               _passing(paper_candidate_status="OVERFIT_RISK", name="bad3")]
        report = select_paper_candidates(inputs=bad)
        assert report.status == SelectionStatus.NO_CANDIDATE
        assert report.candidates == []
        assert len(report.excluded) == 3
        # 가장 흔한 risk_flag 등장.
        joined = " ".join(report.reasons_no_candidate)
        assert "건" in joined

    def test_min_candidates_when_exactly_one_passes(self):
        inputs = [
            _passing(name="good1"),
            _passing(name="bad1", expectancy=-100.0),
            _passing(name="bad2", profit_factor=0.5),
        ]
        report = select_paper_candidates(inputs=inputs)
        assert report.status == SelectionStatus.MIN_CANDIDATES
        assert len(report.candidates) == 1
        assert report.candidates[0].name == "good1"

    def test_ok_when_two_or_more_pass(self):
        report = select_paper_candidates(inputs=[
            _passing(name="good1"), _passing(name="good2"),
        ])
        assert report.status == SelectionStatus.OK
        assert len(report.candidates) == 2


# ─────────────────────────────────────────────────────────────────────────────
# 2. 7 selection conditions
# ─────────────────────────────────────────────────────────────────────────────


class TestSelectionConditions:

    def test_overfit_risk_excluded(self):
        report = select_paper_candidates(inputs=[
            _passing(paper_candidate_status="OVERFIT_RISK"),
        ])
        assert report.status == SelectionStatus.NO_CANDIDATE
        ex = report.excluded[0]
        assert "overfit_risk" in ex.risk_flags

    def test_stress_failed_excluded(self):
        report = select_paper_candidates(inputs=[
            _passing(paper_candidate_status="STRESS_FAILED"),
        ])
        ex = report.excluded[0]
        assert "stress_failed" in ex.risk_flags

    def test_rejected_paper_status_excluded(self):
        report = select_paper_candidates(inputs=[
            _passing(paper_candidate_status="REJECTED"),
        ])
        ex = report.excluded[0]
        assert "paper_rejected" in ex.risk_flags

    def test_low_liquidity_regime_excluded(self):
        report = select_paper_candidates(inputs=[
            _passing(primary_regime="LOW_LIQUIDITY"),
        ])
        ex = report.excluded[0]
        assert "regime_low_liquidity" in ex.risk_flags

    def test_unknown_regime_excluded(self):
        report = select_paper_candidates(inputs=[
            _passing(primary_regime="UNKNOWN"),
        ])
        ex = report.excluded[0]
        assert "regime_unknown" in ex.risk_flags

    def test_non_positive_expectancy_excluded(self):
        report = select_paper_candidates(inputs=[
            _passing(expectancy=0.0),
        ])
        ex = report.excluded[0]
        assert "non_positive_expectancy" in ex.risk_flags

    def test_low_profit_factor_excluded(self):
        report = select_paper_candidates(inputs=[
            _passing(profit_factor=1.0),  # < 1.2
        ])
        ex = report.excluded[0]
        assert "low_profit_factor" in ex.risk_flags

    def test_high_drawdown_excluded(self):
        report = select_paper_candidates(inputs=[
            _passing(max_drawdown=0.30),  # > 0.20
        ])
        ex = report.excluded[0]
        assert "high_drawdown" in ex.risk_flags

    def test_insufficient_trades_excluded(self):
        report = select_paper_candidates(inputs=[
            _passing(trade_count=5),
        ])
        ex = report.excluded[0]
        assert "insufficient_trades" in ex.risk_flags

    def test_walk_forward_decay_excluded(self):
        report = select_paper_candidates(inputs=[
            _passing(walk_forward_verdict="DECAY_WARNING"),
        ])
        ex = report.excluded[0]
        assert "walk_forward_excluded" in ex.risk_flags

    def test_walk_forward_disable_candidate_excluded(self):
        report = select_paper_candidates(inputs=[
            _passing(walk_forward_verdict="DISABLE_CANDIDATE"),
        ])
        ex = report.excluded[0]
        assert "walk_forward_excluded" in ex.risk_flags

    def test_stress_fail_excluded(self):
        report = select_paper_candidates(inputs=[
            _passing(stress_verdict="FAIL"),
        ])
        ex = report.excluded[0]
        assert "stress_excluded" in ex.risk_flags

    def test_combo_fail_excluded(self):
        report = select_paper_candidates(inputs=[
            _passing(combo_verdict="FAIL"),
        ])
        ex = report.excluded[0]
        assert "combo_backtest_excluded" in ex.risk_flags

    def test_regime_combo_blocked_excluded(self):
        report = select_paper_candidates(inputs=[
            _passing(regime_combo_verdict="BLOCKED_REGIME"),
        ])
        ex = report.excluded[0]
        assert "regime_combo_excluded" in ex.risk_flags

    def test_combo_risk_block_excluded(self):
        report = select_paper_candidates(inputs=[
            _passing(combo_risk_verdict="BLOCK"),
        ])
        ex = report.excluded[0]
        assert "combo_risk_excluded" in ex.risk_flags

    def test_combo_risk_high_risk_excluded(self):
        report = select_paper_candidates(inputs=[
            _passing(combo_risk_verdict="HIGH_RISK"),
        ])
        ex = report.excluded[0]
        assert "combo_risk_excluded" in ex.risk_flags

    def test_walk_forward_watch_passes(self):
        # WATCH 는 통과 허용.
        report = select_paper_candidates(inputs=[
            _passing(walk_forward_verdict="WATCH"),
        ])
        assert len(report.candidates) == 1

    def test_stress_warn_passes(self):
        report = select_paper_candidates(inputs=[
            _passing(stress_verdict="WARN"),
        ])
        assert len(report.candidates) == 1

    def test_combo_warn_passes(self):
        report = select_paper_candidates(inputs=[
            _passing(combo_verdict="WARN"),
        ])
        assert len(report.candidates) == 1

    def test_regime_combo_watch_passes(self):
        report = select_paper_candidates(inputs=[
            _passing(regime_combo_verdict="WATCH"),
        ])
        assert len(report.candidates) == 1

    def test_combo_risk_watch_passes(self):
        report = select_paper_candidates(inputs=[
            _passing(combo_risk_verdict="WATCH"),
        ])
        assert len(report.candidates) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 3. Top-N ordering
# ─────────────────────────────────────────────────────────────────────────────


class TestTopN:

    def test_max_three_candidates_by_default(self):
        inputs = [
            _passing(name=f"c{i}", expectancy=100.0 + i * 100,
                     profit_factor=1.3 + i * 0.1)
            for i in range(5)
        ]
        report = select_paper_candidates(inputs=inputs)
        assert len(report.candidates) == 3
        # rank 순 정렬 — composite_score desc.
        ranks = [c.rank for c in report.candidates]
        assert ranks == [1, 2, 3]
        # composite_score 가 큰 순서.
        scores = [c.composite_score for c in report.candidates]
        assert scores == sorted(scores, reverse=True)
        # 통과 2 개가 빠짐.
        # excluded 는 통과하지 못한 후보가 아닌 *top_n 초과* 후보는 포함 X.

    def test_max_candidates_override(self):
        inputs = [
            _passing(name=f"c{i}", expectancy=100.0 + i * 100,
                     profit_factor=1.3 + i * 0.1)
            for i in range(5)
        ]
        report = select_paper_candidates(
            inputs=inputs,
            criteria=FinalCandidateCriteria(max_candidates=1),
        )
        assert len(report.candidates) == 1
        assert report.status == SelectionStatus.MIN_CANDIDATES

    def test_highest_expectancy_ranked_first(self):
        inputs = [
            _passing(name="lower", expectancy=100.0, profit_factor=1.3),
            _passing(name="higher", expectancy=500.0, profit_factor=2.0),
        ]
        report = select_paper_candidates(inputs=inputs)
        # higher 가 #1.
        assert report.candidates[0].name == "higher"
        assert report.candidates[0].rank == 1


# ─────────────────────────────────────────────────────────────────────────────
# 4. Invariants — PaperCandidate / Report
# ─────────────────────────────────────────────────────────────────────────────


class TestInvariants:

    def test_top_candidate_invariants_false(self):
        report = select_paper_candidates(inputs=[_passing()])
        c = report.candidates[0]
        assert c.is_order_signal is False
        assert c.auto_apply_allowed is False
        assert c.is_live_authorization is False
        assert c.requires_operator_approval is True

    def test_top_candidate_to_dict_carries_invariants(self):
        report = select_paper_candidates(inputs=[_passing()])
        d = report.candidates[0].to_dict()
        assert d["is_order_signal"] is False
        assert d["auto_apply_allowed"] is False
        assert d["is_live_authorization"] is False
        assert d["requires_operator_approval"] is True

    @pytest.mark.parametrize("override", [
        {"is_order_signal": True},
        {"auto_apply_allowed": True},
        {"is_live_authorization": True},
    ])
    def test_paper_candidate_invariant_violation_raises(self, override):
        base = dict(
            rank=1, name="x",
            included_tactics=("MOMENTUM",),
            included_strategies=("sma_crossover",),
            symbol="005930", params={}, primary_regime="TREND_UP",
            trade_count=20,
            expectancy=200.0, profit_factor=1.5, max_drawdown=0.10,
            win_rate=0.5, loss_streak=2, total_return=4000.0,
            correlation_score=0.3, concentration_score=0.4,
            confirmation_score=2,
            composite_score=0.5,
            paper_candidate_status="READY_FOR_PAPER",
            walk_forward_verdict="HEALTHY",
            stress_verdict="PASS",
            combo_verdict="PASS",
            regime_combo_verdict="PASS",
            combo_risk_verdict="PASS",
        )
        base.update(override)
        with pytest.raises(ValueError):
            PaperCandidate(**base)

    def test_paper_candidate_requires_operator_approval_invariant(self):
        # requires_operator_approval=False 시도 → ValueError.
        with pytest.raises(ValueError):
            PaperCandidate(
                rank=1, name="x",
                included_tactics=("MOMENTUM",),
                included_strategies=("sma_crossover",),
                symbol="005930", params={}, primary_regime="TREND_UP",
                trade_count=20,
                expectancy=200.0, profit_factor=1.5, max_drawdown=0.10,
                win_rate=0.5, loss_streak=2, total_return=4000.0,
                correlation_score=0.3, concentration_score=0.4,
                confirmation_score=2,
                composite_score=0.5,
                paper_candidate_status="READY_FOR_PAPER",
                walk_forward_verdict="HEALTHY",
                stress_verdict="PASS",
                combo_verdict="PASS",
                regime_combo_verdict="PASS",
                combo_risk_verdict="PASS",
                requires_operator_approval=False,
            )

    @pytest.mark.parametrize("override", [
        {"is_order_signal": True},
        {"auto_apply_allowed": True},
        {"is_live_authorization": True},
    ])
    def test_report_invariant_violation_raises(self, override):
        base = dict(
            generated_at="t", schema_version="1.0",
            status=SelectionStatus.NO_CANDIDATE,
            period_label="ad-hoc",
            candidates=[], excluded=[],
            criteria=FinalCandidateCriteria(),
        )
        base.update(override)
        with pytest.raises(ValueError):
            FinalCandidateReport(**base)

    def test_no_candidate_status_with_candidates_raises(self):
        with pytest.raises(ValueError):
            FinalCandidateReport(
                generated_at="t", schema_version="1.0",
                status=SelectionStatus.NO_CANDIDATE,
                period_label="x",
                candidates=[_build_minimal_candidate("x")],
                excluded=[], criteria=FinalCandidateCriteria(),
            )

    def test_min_candidates_status_with_two_candidates_raises(self):
        with pytest.raises(ValueError):
            FinalCandidateReport(
                generated_at="t", schema_version="1.0",
                status=SelectionStatus.MIN_CANDIDATES,
                period_label="x",
                candidates=[_build_minimal_candidate("a"),
                            _build_minimal_candidate("b", rank=2)],
                excluded=[], criteria=FinalCandidateCriteria(),
            )

    def test_ok_status_with_one_candidate_raises(self):
        with pytest.raises(ValueError):
            FinalCandidateReport(
                generated_at="t", schema_version="1.0",
                status=SelectionStatus.OK,
                period_label="x",
                candidates=[_build_minimal_candidate("a")],
                excluded=[], criteria=FinalCandidateCriteria(),
            )


def _build_minimal_candidate(name="x", rank=1) -> PaperCandidate:
    return PaperCandidate(
        rank=rank, name=name,
        included_tactics=("MOMENTUM",),
        included_strategies=("sma_crossover",),
        symbol="005930", params={}, primary_regime="TREND_UP",
        trade_count=20,
        expectancy=200.0, profit_factor=1.5, max_drawdown=0.10,
        win_rate=0.5, loss_streak=2, total_return=4000.0,
        correlation_score=0.3, concentration_score=0.4,
        confirmation_score=2, composite_score=0.5,
        paper_candidate_status="READY_FOR_PAPER",
        walk_forward_verdict="HEALTHY", stress_verdict="PASS",
        combo_verdict="PASS", regime_combo_verdict="PASS",
        combo_risk_verdict="PASS",
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. Criteria validation
# ─────────────────────────────────────────────────────────────────────────────


class TestCriteriaValidation:

    @pytest.mark.parametrize("kwargs", [
        {"min_trades": 0},
        {"min_profit_factor": 0.0},
        {"max_drawdown_abs": 0.0},
        {"max_drawdown_abs": 1.5},
        {"max_candidates": 0},
        {"max_candidates": 11},
    ])
    def test_invalid_criteria_raises(self, kwargs):
        with pytest.raises(ValueError):
            FinalCandidateCriteria(**kwargs)

    def test_default_criteria_valid(self):
        c = FinalCandidateCriteria()
        assert c.min_trades == 10
        assert c.min_profit_factor == 1.2
        assert c.max_drawdown_abs == 0.20
        assert c.max_candidates == 3


# ─────────────────────────────────────────────────────────────────────────────
# 6. Input validation
# ─────────────────────────────────────────────────────────────────────────────


class TestInputValidation:

    def test_empty_name_raises(self):
        with pytest.raises(ValueError):
            CandidateInput(name="", symbol="005930")

    def test_empty_symbol_raises(self):
        with pytest.raises(ValueError):
            CandidateInput(name="x", symbol="")

    def test_out_of_range_correlation_raises(self):
        with pytest.raises(ValueError):
            CandidateInput(name="x", symbol="005930", correlation_score=1.5)

    def test_default_input_constructs(self):
        i = CandidateInput(name="x", symbol="005930")
        assert i.trade_count == 0
        assert i.paper_candidate_status == "INSUFFICIENT_DATA"


# ─────────────────────────────────────────────────────────────────────────────
# 7. Report file generation
# ─────────────────────────────────────────────────────────────────────────────


class TestReportFiles:

    def test_three_files_generated(self, tmp_path):
        report = select_paper_candidates(inputs=[_passing()])
        paths = write_reports(report, tmp_path)
        for k in ("summary_json", "report_md", "ranking_csv"):
            assert paths[k].exists()

    def test_json_carries_invariants(self, tmp_path):
        report = select_paper_candidates(inputs=[_passing()])
        paths = write_reports(report, tmp_path)
        d = json.loads(paths["summary_json"].read_text(encoding="utf-8"))
        assert d["schema_version"] == FINAL_CANDIDATE_SCHEMA_VERSION
        assert d["status"] == "MIN_CANDIDATES"
        assert d["candidate_count"] == 1
        assert d["is_order_signal"] is False
        # candidate level invariants.
        c = d["candidates"][0]
        assert c["is_order_signal"] is False
        assert c["requires_operator_approval"] is True

    def test_markdown_includes_safety_text(self, tmp_path):
        report = select_paper_candidates(inputs=[_passing()])
        paths = write_reports(report, tmp_path)
        md = paths["report_md"].read_text(encoding="utf-8")
        assert "최종 Paper 조합 후보 리포트" in md
        assert "requires_operator_approval=True" in md
        assert "is_order_signal=False" in md
        assert "advisory" in md.lower()

    def test_csv_last_column_always_true(self, tmp_path):
        report = select_paper_candidates(inputs=[
            _passing(name="c1"), _passing(name="c2"),
        ])
        paths = write_reports(report, tmp_path)
        csv = paths["ranking_csv"].read_text(encoding="utf-8")
        for line in csv.strip().splitlines()[1:]:
            assert line.split(",")[-1] == "true"   # requires_operator_approval

    def test_no_candidate_report_writes_three_files(self, tmp_path):
        report = select_paper_candidates(inputs=[])
        paths = write_reports(report, tmp_path)
        assert paths["summary_json"].exists()
        d = json.loads(paths["summary_json"].read_text(encoding="utf-8"))
        assert d["status"] == "NO_CANDIDATE"
        assert d["candidates"] == []

    def test_reports_dir_gitignored(self):
        gitignore = (Path(__file__).resolve().parents[2] / ".gitignore")
        content = gitignore.read_text(encoding="utf-8")
        assert "reports/" in content or "reports/*" in content


# ─────────────────────────────────────────────────────────────────────────────
# 8. Render helpers
# ─────────────────────────────────────────────────────────────────────────────


class TestRenderHelpers:

    def test_render_markdown_returns_korean(self):
        report = select_paper_candidates(inputs=[_passing()])
        md = render_markdown(report)
        assert "최종 Paper 조합 후보" in md

    def test_render_csv_has_header_and_rows(self):
        report = select_paper_candidates(inputs=[
            _passing(name="a"), _passing(name="b"),
        ])
        csv = render_ranking_csv(report)
        rows = csv.strip().splitlines()
        assert len(rows) == 3  # header + 2 candidates

    def test_render_csv_no_rows_when_no_candidate(self):
        report = select_paper_candidates(inputs=[])
        csv = render_ranking_csv(report)
        rows = csv.strip().splitlines()
        assert len(rows) == 1   # header only


# ─────────────────────────────────────────────────────────────────────────────
# 9. Static guards
# ─────────────────────────────────────────────────────────────────────────────


_FORBIDDEN_IMPORT_SUBSTRINGS = (
    "app.brokers.kis",
    "app.brokers.mock_broker",
    "app.execution.order_router",
    "app.execution.executor",
    "app.execution.order_executor",
    "app.permission.gate",
    "app.ai.assist",
    "app.ai.client",
    "anthropic",
    "openai",
    "httpx",
    "requests",
)


_FORBIDDEN_CALL_SUBSTRINGS = (
    "broker.place_order",
    "broker.cancel_order",
    "route_order(",
    "OrderExecutor",
    "OrderRequest",
)


class TestStaticGuards:

    def _source(self) -> str:
        return _MODULE_PATH.read_text(encoding="utf-8")

    def test_no_forbidden_imports(self):
        tree = ast.parse(self._source())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for bad in _FORBIDDEN_IMPORT_SUBSTRINGS:
                        assert bad not in (alias.name or "")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for bad in _FORBIDDEN_IMPORT_SUBSTRINGS:
                    assert bad not in module

    def test_no_forbidden_calls(self):
        tree = ast.parse(self._source())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee = ast.unparse(node.func)
                for bad in _FORBIDDEN_CALL_SUBSTRINGS:
                    assert bad not in callee

    def test_no_db_write(self):
        src = self._source()
        for bad in ("session.commit", "session.add", "session.delete",
                    "db.commit(", "db.add(", "db.delete("):
            assert bad not in src

    def test_no_settings_mutation(self):
        src = self._source()
        assert not re.search(r"settings\.enable_[a-z_]+\s*=", src)

    def test_no_secret_fields(self):
        forbidden = {
            "api_key", "secret", "app_key", "app_secret", "access_token",
            "account_no", "account_number",
        }
        for name in PaperCandidate.__dataclass_fields__:
            assert name.lower() not in forbidden, name
        for name in CandidateInput.__dataclass_fields__:
            assert name.lower() not in forbidden, name
        for name in FinalCandidateReport.__dataclass_fields__:
            assert name.lower() not in forbidden, name


# ─────────────────────────────────────────────────────────────────────────────
# 10. ExcludedCandidate
# ─────────────────────────────────────────────────────────────────────────────


class TestExcludedCandidate:

    def test_to_dict_carries_measurements(self):
        report = select_paper_candidates(inputs=[
            _passing(expectancy=-100.0, name="bad"),
        ])
        ex = report.excluded[0]
        d = ex.to_dict()
        assert d["name"] == "bad"
        assert "expectancy" in d["measurements"]
        assert d["measurements"]["expectancy"] == -100.0
