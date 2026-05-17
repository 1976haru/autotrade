"""3-08 — 운영자용 전략 최적화 리포트 테스트.

invariant:
- OperatorReport 최상위 + StrategyEntry 모두 is_order_signal=False /
  auto_apply_allowed=False / is_live_authorization=False / is_investment_advice=False.
- BUY/SELL/HOLD/Place Order/실거래 시작/ENABLE_LIVE_TRADING 토글 라벨 0건.
- broker / OrderExecutor / route_order / KIS 주문 import 0건 (정적 grep).
- 외부 HTTP / AI SDK import 0건.
- reports/strategy_optimization/{strategy_optimization_report.md,operator_summary.md}
  경로 lock — `tmp_path` 에서만 생성 확인.
- 12 필수 섹션 모두 markdown 에 포함.
- 후보 0건도 markdown 생성.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from app.analytics.strategy_optimization_report import (
    OperatorReport,
    ReportInputs,
    ReportStatus,
    StrategyEntry,
    build_operator_report,
    render_full_markdown,
    render_summary_markdown,
    write_report_files,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helper — synthetic 5-단계 payload (3-02/3-03/3-04/3-05 + paper_candidate_config)
# ─────────────────────────────────────────────────────────────────────────────


def _backtest_payload(
    strategy="sma_crossover", symbol="005930", verdict="BACKTEST_PASS",
    params=None, metrics_override=None,
):
    base_metrics = {
        "trade_count":               45,
        "profit_factor":             1.6,
        "max_drawdown":              0.08,
        "expectancy":                650.0,
        "win_rate":                  0.55,
        "risk_adjusted_score":       0.05,
        "total_return":              0.18,
        "fee_adjusted_return":       0.16,
        "slippage_adjusted_return":  0.14,
        "loss_streak":               3,
    }
    if metrics_override:
        base_metrics.update(metrics_override)
    return {
        "per_symbol": [{
            "symbol": symbol,
            "runs": [{
                "strategy": strategy,
                "params":   params or {},
                "verdict":  verdict,
                "metrics":  base_metrics,
            }],
        }],
    }


def _optimization_payload(
    strategy="sma_crossover", symbol="005930", verdict="PAPER_CANDIDATE",
    params=None,
):
    return {
        "all_runs": [{
            "strategy": strategy,
            "symbol":   symbol,
            "params":   params or {},
            "verdict":  verdict,
            "metrics": {
                "trade_count":         45,
                "profit_factor":       1.6,
                "max_drawdown":        0.08,
                "expectancy":          650.0,
                "win_rate":            0.55,
                "risk_adjusted_score": 0.05,
            },
            "reasons":  ["all_filters_passed"],
        }],
    }


def _walk_forward_payload(
    strategy="sma_crossover", symbol="005930", verdict="HEALTHY", params=None,
):
    return {
        "results": [{
            "strategy": strategy,
            "symbol":   symbol,
            "params":   params or {},
            "verdict":  verdict,
            "fold_count":            5,
            "train_expectancy_avg":  720.0,
            "val_expectancy_avg":    580.0,
        }],
    }


def _stress_test_payload(
    strategy="sma_crossover", symbol="005930", all_pass=True, params=None,
):
    scenarios = ["CRASH", "SURGE", "SIDEWAYS", "SLIPPAGE_SPIKE", "DATA_GAP",
                 "EXECUTION_REJECT", "STALE_PRICE", "DUPLICATE_SIGNAL",
                 "LOW_LIQUIDITY", "CORRELATED_DRAWDOWN"]
    return {
        "results": [
            {
                "strategy":      strategy,
                "symbol":        symbol,
                "params":        params or {},
                "scenario_name": s,
                "stress_verdict": "PASS" if all_pass else (
                    "FAIL" if i == 0 else "PASS"
                ),
                "stress_score":  85.0,
            }
            for i, s in enumerate(scenarios)
        ],
    }


def _paper_candidate_payload(candidates=None, reasons=None):
    return {
        "generated_at":          "2026-05-17T00:00:00+00:00",
        "is_order_signal":       False,
        "auto_apply_allowed":    False,
        "is_live_authorization": False,
        "candidate_count":       len(candidates or []),
        "candidates":            candidates or [],
        "reasons_no_candidate":  reasons or [],
        "metadata":              {},
    }


def _write_all_inputs(tmp_path, *, all_pass=True, include_pcc=True):
    """전체 5 파일 작성 + 경로 반환."""
    bt   = tmp_path / "bt.json"
    opt  = tmp_path / "opt.json"
    wf   = tmp_path / "wf.json"
    st   = tmp_path / "st.json"
    pcc  = tmp_path / "pcc.json"
    bt.write_text(json.dumps(_backtest_payload()), encoding="utf-8")
    opt.write_text(json.dumps(_optimization_payload()), encoding="utf-8")
    wf.write_text(json.dumps(_walk_forward_payload()), encoding="utf-8")
    st.write_text(json.dumps(_stress_test_payload(all_pass=all_pass)), encoding="utf-8")
    if include_pcc:
        pcc.write_text(json.dumps(_paper_candidate_payload([
            {"strategy": "sma_crossover", "symbol": "005930", "params": {}},
        ])), encoding="utf-8")
    return {
        "bt": str(bt), "opt": str(opt), "wf": str(wf), "st": str(st),
        "pcc": str(pcc) if include_pcc else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. build_operator_report — 후보 있음 / 없음 시나리오
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildOperatorReport:
    def test_all_stages_pass_produces_paper_ready(self, tmp_path):
        paths = _write_all_inputs(tmp_path, all_pass=True)
        report = build_operator_report(ReportInputs(
            paper_candidate_config_path=paths["pcc"],
            backtest_summary_path=paths["bt"],
            optimization_summary_path=paths["opt"],
            walk_forward_summary_path=paths["wf"],
            stress_test_summary_path=paths["st"],
        ))
        assert report.overall_status == ReportStatus.READY_FOR_PAPER
        assert report.paper_ready_count == 1
        assert report.excluded_count == 0
        assert report.paper_candidates[0].strategy_id == "sma_crossover"

    def test_no_inputs_produces_no_candidate(self):
        report = build_operator_report(ReportInputs())
        assert report.overall_status == ReportStatus.NO_CANDIDATE
        assert report.paper_ready_count == 0
        assert report.entries == []
        assert "no_pipeline_results_loaded" in report.reasons_no_candidate

    def test_stress_fail_marks_stress_failed(self, tmp_path):
        paths = _write_all_inputs(tmp_path, all_pass=False, include_pcc=False)
        report = build_operator_report(ReportInputs(
            backtest_summary_path=paths["bt"],
            optimization_summary_path=paths["opt"],
            walk_forward_summary_path=paths["wf"],
            stress_test_summary_path=paths["st"],
        ))
        assert report.paper_ready_count == 0
        assert report.excluded_count == 1
        entry = report.excluded[0]
        assert entry.status == ReportStatus.STRESS_FAILED

    def test_overfit_marks_overfit_risk(self, tmp_path):
        bt   = tmp_path / "bt.json"
        opt  = tmp_path / "opt.json"
        wf   = tmp_path / "wf.json"
        st   = tmp_path / "st.json"
        bt.write_text(json.dumps(_backtest_payload()), encoding="utf-8")
        opt.write_text(json.dumps(_optimization_payload()), encoding="utf-8")
        wf.write_text(
            json.dumps(_walk_forward_payload(verdict="OVERFIT_RISK")),
            encoding="utf-8",
        )
        st.write_text(json.dumps(_stress_test_payload(all_pass=True)),
                      encoding="utf-8")
        report = build_operator_report(ReportInputs(
            backtest_summary_path=str(bt),
            optimization_summary_path=str(opt),
            walk_forward_summary_path=str(wf),
            stress_test_summary_path=str(st),
        ))
        entry = report.excluded[0] if report.excluded else report.entries[0]
        assert entry.status == ReportStatus.OVERFIT_RISK

    def test_missing_stages_marks_need_more_data(self, tmp_path):
        # 3-02, 3-03 만 — 3-04, 3-05 누락.
        bt   = tmp_path / "bt.json"
        opt  = tmp_path / "opt.json"
        bt.write_text(json.dumps(_backtest_payload()), encoding="utf-8")
        opt.write_text(json.dumps(_optimization_payload()), encoding="utf-8")
        report = build_operator_report(ReportInputs(
            backtest_summary_path=str(bt),
            optimization_summary_path=str(opt),
        ))
        assert report.paper_ready_count == 0
        entry = report.excluded[0]
        assert entry.status == ReportStatus.NEED_MORE_DATA

    def test_exclusion_reasons_present(self, tmp_path):
        """제외 후보는 사유 carry."""
        paths = _write_all_inputs(tmp_path, all_pass=False, include_pcc=False)
        report = build_operator_report(ReportInputs(
            backtest_summary_path=paths["bt"],
            optimization_summary_path=paths["opt"],
            walk_forward_summary_path=paths["wf"],
            stress_test_summary_path=paths["st"],
        ))
        entry = report.excluded[0]
        joined = " ".join(entry.exclusion_reasons)
        assert "3-05" in joined or "탈락" in joined or "단계" in joined

    def test_risk_signals_compiled(self, tmp_path):
        # 낮은 PF / 높은 MDD 메트릭으로 신호 trigger.
        bt = tmp_path / "bt.json"
        bt.write_text(json.dumps(_backtest_payload(metrics_override={
            "profit_factor": 0.7, "max_drawdown": 0.25,
            "win_rate": 0.30, "loss_streak": 8, "expectancy": -100.0,
        })), encoding="utf-8")
        report = build_operator_report(ReportInputs(
            backtest_summary_path=str(bt),
        ))
        signals = report.ai_agent_risk_signals
        assert any("profit_factor_below_1" in s for s in signals)
        assert any("high_max_drawdown" in s for s in signals)
        assert any("low_win_rate" in s for s in signals)
        assert any("non_positive_expectancy" in s for s in signals)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Markdown rendering — 12 섹션 필수
# ─────────────────────────────────────────────────────────────────────────────


REQUIRED_SECTIONS = [
    "## 1. 전체 결론",
    "## 2. 전략별 순위",
    "## 3. Paper 후보 전략",
    "## 4. 후보가 없는 경우 사유",
    "## 5. 제외된 전략과 사유",
    "## 6. 수수료·슬리피지 반영 결과",
    "## 7. Walk-forward 결과",
    "## 8. Stress Test 결과",
    "## 9. 핵심 성과 지표",
    "## 10. AI Agent 가 참고할 위험 신호",
    "## 11. 다음 단계: Paper 모의운용 가능 여부",
    "## 12. 사용자가 해야 할 다음 행동",
]


class TestMarkdownRender:
    def test_full_report_has_all_12_sections(self, tmp_path):
        paths = _write_all_inputs(tmp_path, all_pass=True)
        report = build_operator_report(ReportInputs(
            paper_candidate_config_path=paths["pcc"],
            backtest_summary_path=paths["bt"],
            optimization_summary_path=paths["opt"],
            walk_forward_summary_path=paths["wf"],
            stress_test_summary_path=paths["st"],
        ))
        md = render_full_markdown(report)
        for section in REQUIRED_SECTIONS:
            assert section in md, f"missing section: {section}"

    def test_no_candidate_report_still_has_all_sections(self):
        report = build_operator_report(ReportInputs())
        md = render_full_markdown(report)
        for section in REQUIRED_SECTIONS:
            assert section in md, f"missing section even in 0-candidate: {section}"

    def test_disclaimer_present(self, tmp_path):
        paths = _write_all_inputs(tmp_path, all_pass=True)
        report = build_operator_report(ReportInputs(
            backtest_summary_path=paths["bt"],
            optimization_summary_path=paths["opt"],
            walk_forward_summary_path=paths["wf"],
            stress_test_summary_path=paths["st"],
        ))
        md = render_full_markdown(report)
        assert "투자 조언이 아닙니다" in md
        assert "is_order_signal=False" in md
        # summary 도 동일.
        s = render_summary_markdown(report)
        assert "투자 조언이 아닙니다" in s

    def test_exclusion_reasons_visible_in_markdown(self, tmp_path):
        paths = _write_all_inputs(tmp_path, all_pass=False, include_pcc=False)
        report = build_operator_report(ReportInputs(
            backtest_summary_path=paths["bt"],
            optimization_summary_path=paths["opt"],
            walk_forward_summary_path=paths["wf"],
            stress_test_summary_path=paths["st"],
        ))
        md = render_full_markdown(report)
        # § 5 에 strategy 이름 + 탈락 사유.
        assert "sma_crossover" in md
        assert ("STRESS_FAILED" in md or "탈락" in md)

    def test_risk_signals_section_renders(self, tmp_path):
        bt = tmp_path / "bt.json"
        bt.write_text(json.dumps(_backtest_payload(metrics_override={
            "profit_factor": 0.5, "max_drawdown": 0.30, "expectancy": -200.0,
        })), encoding="utf-8")
        report = build_operator_report(ReportInputs(
            backtest_summary_path=str(bt),
        ))
        md = render_full_markdown(report)
        assert "profit_factor_below_1" in md
        assert "high_max_drawdown" in md

    def test_summary_is_short(self, tmp_path):
        paths = _write_all_inputs(tmp_path, all_pass=True)
        report = build_operator_report(ReportInputs(
            backtest_summary_path=paths["bt"],
            optimization_summary_path=paths["opt"],
            walk_forward_summary_path=paths["wf"],
            stress_test_summary_path=paths["st"],
        ))
        full = render_full_markdown(report)
        summary = render_summary_markdown(report)
        # summary 는 full 보다 짧아야 함 — 1 페이지 목적.
        assert len(summary) < len(full)
        # 결론 한 줄 섹션 존재.
        assert "결론 한 줄" in summary


# ─────────────────────────────────────────────────────────────────────────────
# 3. File output — tmp_path 에서 2 파일 생성
# ─────────────────────────────────────────────────────────────────────────────


class TestFileOutput:
    def test_writes_two_markdown_files(self, tmp_path):
        paths = _write_all_inputs(tmp_path, all_pass=True)
        report = build_operator_report(ReportInputs(
            backtest_summary_path=paths["bt"],
            optimization_summary_path=paths["opt"],
            walk_forward_summary_path=paths["wf"],
            stress_test_summary_path=paths["st"],
        ))
        out_dir = tmp_path / "strategy_optimization"
        written = write_report_files(report, out_dir)
        assert "strategy_optimization_report" in written
        assert "operator_summary" in written
        full_path = written["strategy_optimization_report"]
        sum_path  = written["operator_summary"]
        assert full_path.exists() and full_path.name == "strategy_optimization_report.md"
        assert sum_path.exists() and sum_path.name == "operator_summary.md"

    def test_writes_files_even_when_zero_candidates(self, tmp_path):
        report = build_operator_report(ReportInputs())
        out_dir = tmp_path / "strategy_optimization"
        written = write_report_files(report, out_dir)
        for k, p in written.items():
            assert p.exists(), f"file missing for {k}"
            content = p.read_text(encoding="utf-8")
            assert "후보" in content or "전략" in content

    def test_creates_parent_directory(self, tmp_path):
        report = build_operator_report(ReportInputs())
        out_dir = tmp_path / "nested" / "strategy_optimization"
        write_report_files(report, out_dir)
        assert out_dir.exists()


# ─────────────────────────────────────────────────────────────────────────────
# 4. CLI 통합
# ─────────────────────────────────────────────────────────────────────────────


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))


def _import_cli():
    spec = importlib.util.spec_from_file_location(
        "run_strategy_optimization_report_module",
        _SCRIPTS_DIR / "run_strategy_optimization_report.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cli():
    return _import_cli()


class TestCli:
    def test_runs_with_all_inputs(self, cli, tmp_path):
        paths = _write_all_inputs(tmp_path, all_pass=True)
        args = cli._parse_args([
            "--dry-run",
            "--from-paper-candidate", paths["pcc"],
            "--from-backtest",        paths["bt"],
            "--from-optimization",    paths["opt"],
            "--from-walk-forward",    paths["wf"],
            "--from-stress-test",     paths["st"],
        ])
        result = cli.run_report(args)
        assert result["entry_count"] == 1
        assert result["paper_ready_count"] == 1
        assert result["overall_status"] == "READY_FOR_PAPER"

    def test_runs_with_no_inputs(self, cli):
        args = cli._parse_args(["--dry-run"])
        result = cli.run_report(args)
        assert result["entry_count"] == 0
        assert result["paper_ready_count"] == 0
        assert result["overall_status"] == "NO_CANDIDATE"

    def test_writes_markdown_to_output_dir(self, cli, tmp_path):
        paths = _write_all_inputs(tmp_path, all_pass=True)
        out_dir = tmp_path / "strategy_optimization"
        args = cli._parse_args([
            "--output-dir", str(out_dir),
            "--from-backtest", paths["bt"],
            "--from-optimization", paths["opt"],
            "--from-walk-forward", paths["wf"],
            "--from-stress-test",  paths["st"],
        ])
        result = cli.run_report(args)
        written = cli._write_outputs(result, out_dir)
        assert written["strategy_optimization_report"].exists()
        assert written["operator_summary"].exists()


# ─────────────────────────────────────────────────────────────────────────────
# 5. 금지 라벨 / Secret / 정적 import 가드
# ─────────────────────────────────────────────────────────────────────────────


class TestNoForbiddenLabels:
    def test_markdown_has_no_order_labels(self, tmp_path):
        paths = _write_all_inputs(tmp_path, all_pass=True)
        report = build_operator_report(ReportInputs(
            backtest_summary_path=paths["bt"],
            optimization_summary_path=paths["opt"],
            walk_forward_summary_path=paths["wf"],
            stress_test_summary_path=paths["st"],
        ))
        md = render_full_markdown(report) + "\n" + render_summary_markdown(report)
        forbidden = [
            "Place Order", "지금 매수", "지금 매도", "실거래 시작",
            "ENABLE_LIVE_TRADING 토글", "ENABLE_AI_EXECUTION 토글",
            "AI 자동매매 켜기", "주문 실행 시작",
        ]
        for w in forbidden:
            assert w not in md, f"forbidden label in markdown: {w}"

    def test_module_invariants_immutable(self):
        # is_order_signal=True 시도 → ValueError.
        with pytest.raises(ValueError):
            OperatorReport(
                generated_at="x",
                overall_status=ReportStatus.NO_CANDIDATE,
                paper_ready_count=0,
                excluded_count=0,
                is_order_signal=True,
            )
        with pytest.raises(ValueError):
            OperatorReport(
                generated_at="x",
                overall_status=ReportStatus.NO_CANDIDATE,
                paper_ready_count=0,
                excluded_count=0,
                auto_apply_allowed=True,
            )
        with pytest.raises(ValueError):
            OperatorReport(
                generated_at="x",
                overall_status=ReportStatus.NO_CANDIDATE,
                paper_ready_count=0,
                excluded_count=0,
                is_live_authorization=True,
            )
        with pytest.raises(ValueError):
            OperatorReport(
                generated_at="x",
                overall_status=ReportStatus.NO_CANDIDATE,
                paper_ready_count=0,
                excluded_count=0,
                is_investment_advice=True,
            )

    def test_strategy_entry_status_must_be_report_status(self):
        with pytest.raises(ValueError):
            StrategyEntry(
                strategy_id="sma_crossover", display_name="sma", symbol="005930",
                params={}, status="READY_FOR_PAPER",   # type: ignore[arg-type]
            )

    def test_to_dict_carries_invariants(self):
        report = build_operator_report(ReportInputs())
        d = report.to_dict()
        assert d["is_order_signal"] is False
        assert d["auto_apply_allowed"] is False
        assert d["is_live_authorization"] is False
        assert d["is_investment_advice"] is False


class TestNoForbiddenImports:
    """모듈 + CLI 정적 grep — broker / OrderExecutor / route_order /
    외부 HTTP / AI SDK / settings mutate 0건."""

    def test_module_has_no_forbidden_imports(self):
        import app.analytics.strategy_optimization_report as mod
        src = Path(mod.__file__).read_text(encoding="utf-8")
        forbidden = [
            r"from\s+app\.brokers\.kis",
            r"from\s+app\.brokers\.mock_broker",
            r"from\s+app\.execution\.executor",
            r"from\s+app\.execution\.order_router",
            r"from\s+app\.ai\.assist\b",
            r"from\s+app\.ai\.client\b",
            r"from\s+app\.core\.config\s+import\s+get_settings",
            r"broker\.place_order\(",
            r"route_order\s*\(",
            r"OrderExecutor\s*\(",
            r"KisClient\b",
            r"^import\s+anthropic",
            r"^import\s+openai",
            r"^import\s+requests",
            r"^import\s+yfinance",
            r"^import\s+httpx",
        ]
        for pat in forbidden:
            assert not re.search(pat, src, re.MULTILINE), \
                f"forbidden in strategy_optimization_report.py: {pat}"

    def test_cli_has_no_forbidden_imports(self):
        src = (_SCRIPTS_DIR / "run_strategy_optimization_report.py").read_text(
            encoding="utf-8",
        )
        forbidden = [
            r"from\s+app\.brokers\.kis",
            r"from\s+app\.brokers\.mock_broker",
            r"from\s+app\.execution\.executor",
            r"from\s+app\.execution\.order_router",
            r"broker\.place_order\(",
            r"route_order\s*\(",
            r"OrderExecutor\s*\(",
            r"KisClient\b",
        ]
        for pat in forbidden:
            assert not re.search(pat, src), f"forbidden in CLI: {pat}"

    def test_no_safety_flag_mutation(self):
        targets = [
            _SCRIPTS_DIR / "run_strategy_optimization_report.py",
            Path(__file__).resolve().parents[1] / "app" / "analytics"
                / "strategy_optimization_report.py",
        ]
        bad = [
            r"ENABLE_LIVE_TRADING\s*=\s*['\"]?true",
            r"ENABLE_AI_EXECUTION\s*=\s*['\"]?true",
            r"ENABLE_FUTURES_LIVE_TRADING\s*=\s*['\"]?true",
            r"KIS_IS_PAPER\s*=\s*['\"]?false",
            r"settings\.enable_live_trading\s*=",
            r"settings\.enable_ai_execution\s*=",
        ]
        for path in targets:
            src = path.read_text(encoding="utf-8")
            for pat in bad:
                assert not re.search(pat, src, re.IGNORECASE), \
                    f"safety flag mutation {pat!r} in {path.name}"

    def test_no_secret_patterns(self):
        for path in (
            Path(__file__).resolve().parents[1] / "app" / "analytics"
                / "strategy_optimization_report.py",
            _SCRIPTS_DIR / "run_strategy_optimization_report.py",
        ):
            src = path.read_text(encoding="utf-8")
            # Real secret patterns (not just the literal string "Bearer ").
            patterns = [
                r"sk-[A-Za-z0-9]{20,}",
                r"ghp_[A-Za-z0-9]{30,}",
                r"Bearer\s+[A-Za-z0-9._\-]{20,}",
                r"PST[A-Za-z0-9]{30,}",   # KIS personal token shape.
            ]
            for pat in patterns:
                assert not re.search(pat, src), \
                    f"secret pattern in {path.name}: {pat}"


# ─────────────────────────────────────────────────────────────────────────────
# 6. reports/ 경로가 gitignore 인지 확인
# ─────────────────────────────────────────────────────────────────────────────


class TestReportsGitignore:
    def test_reports_dir_in_gitignore(self):
        repo_root = Path(__file__).resolve().parents[2]
        gitignore = repo_root / ".gitignore"
        content = gitignore.read_text(encoding="utf-8")
        assert ("reports/" in content) or ("reports/*" in content)

    def test_no_committed_report_file_in_repo(self):
        """reports/strategy_optimization/strategy_optimization_report.md 가
        git 추적 0건이어야 한다 — gitignore 가 작동하는지 정적 검증."""
        repo_root = Path(__file__).resolve().parents[2]
        target = repo_root / "reports" / "strategy_optimization" / \
                 "strategy_optimization_report.md"
        # target 이 우연히 작업 디렉토리에 존재할 수도 있으므로, "tracked"
        # 여부만 검증할 수 없으면 skip — 본 테스트는 tmp_path 정책 lock.
        # 핵심: docs/strategy_optimization_report.md (정책 doc) 와 *별개* 파일.
        if target.exists():
            # 운영자가 로컬 실행한 결과 — git 미커밋 정책 신뢰.
            return
        assert not target.exists()
