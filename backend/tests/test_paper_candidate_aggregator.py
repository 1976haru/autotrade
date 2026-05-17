"""3-07 — Paper 후보 통합 export 모듈 테스트.

invariant:
- 4 단계 (3-02/3-03/3-04/3-05) 모두 통과한 후보만 export.
- 후보 0건도 파일 생성 + ``reasons_no_candidate`` carry.
- 최상위 + 각 candidate 모두 is_order_signal=False / auto_apply_allowed=False /
  is_live_authorization=False.
- BUY/SELL/HOLD/Place Order/실거래 시작 라벨 0건.
- broker / OrderExecutor / route_order / KIS 주문 import 0건 (정적 grep).
- reports/strategy_optimization/paper_candidate_config.json 경로 lock.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from app.analytics.paper_candidate_aggregator import (
    AggregatedCandidate,
    AggregationInputs,
    PipelineStage,
    aggregate_candidates,
    build_paper_candidate_config,
    extract_from_backtest_summary,
    extract_from_optimization_summary,
    extract_from_stress_test_summary,
    extract_from_walk_forward_summary,
    read_paper_candidate_config,
    write_paper_candidate_config,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helper — synthetic 4-단계 payload
# ─────────────────────────────────────────────────────────────────────────────


def _backtest_payload(strategy="sma_crossover", symbol="005930", verdict="BACKTEST_PASS",
                       params=None):
    return {
        "per_symbol": [
            {
                "symbol": symbol,
                "runs": [
                    {
                        "strategy": strategy,
                        "params":   params or {},
                        "verdict":  verdict,
                        "metrics": {
                            "trade_count":         30,
                            "profit_factor":       1.5,
                            "max_drawdown":        0.08,
                            "expectancy":          500.0,
                            "risk_adjusted_score": 0.05,
                        },
                    },
                ],
            },
        ],
    }


def _optimization_payload(strategy="sma_crossover", symbol="005930",
                            verdict="PAPER_CANDIDATE", params=None):
    return {
        "all_runs": [
            {
                "strategy": strategy,
                "symbol":   symbol,
                "params":   params or {},
                "verdict":  verdict,
                "metrics": {
                    "trade_count":         30,
                    "profit_factor":       1.5,
                    "max_drawdown":        0.08,
                    "expectancy":          500.0,
                    "risk_adjusted_score": 0.05,
                },
                "reasons":  ["all_filters_passed"],
            },
        ],
    }


def _walk_forward_payload(strategy="sma_crossover", symbol="005930",
                            verdict="HEALTHY", params=None):
    return {
        "results": [
            {
                "strategy": strategy,
                "symbol":   symbol,
                "params":   params or {},
                "verdict":  verdict,
                "fold_count":            5,
                "train_expectancy_avg":  600.0,
                "val_expectancy_avg":    450.0,
            },
        ],
    }


def _stress_test_payload(strategy="sma_crossover", symbol="005930",
                          all_pass=True, params=None):
    """10 시나리오 모두 PASS or 일부 FAIL."""
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
                "stress_verdict": "PASS" if all_pass else ("FAIL" if i == 0 else "PASS"),
                "stress_score":  80.0,
            }
            for i, s in enumerate(scenarios)
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. Adapter — 4 단계 입력 파싱
# ─────────────────────────────────────────────────────────────────────────────


class TestAdapters:
    def test_backtest_adapter_extracts_entries(self):
        out = extract_from_backtest_summary(_backtest_payload())
        assert len(out) == 1
        ps = next(iter(out.values()))
        assert ps.name == "3-02"
        assert ps.verdict == "BACKTEST_PASS"

    def test_optimization_adapter_extracts_entries(self):
        out = extract_from_optimization_summary(_optimization_payload())
        assert len(out) == 1
        ps = next(iter(out.values()))
        assert ps.name == "3-03"
        assert ps.verdict == "PAPER_CANDIDATE"

    def test_walk_forward_adapter_extracts_entries(self):
        out = extract_from_walk_forward_summary(_walk_forward_payload())
        assert len(out) == 1
        ps = next(iter(out.values()))
        assert ps.name == "3-04"
        assert ps.verdict == "HEALTHY"

    def test_stress_test_adapter_extracts_list_per_key(self):
        out = extract_from_stress_test_summary(_stress_test_payload(all_pass=True))
        assert len(out) == 1
        stages = next(iter(out.values()))
        assert len(stages) == 10
        assert all(s.name == "3-05" and s.verdict == "PASS" for s in stages)

    def test_adapters_empty_for_malformed(self):
        assert extract_from_backtest_summary({}) == {}
        assert extract_from_optimization_summary({"all_runs": "x"}) == {}
        assert extract_from_walk_forward_summary(None) == {}
        assert extract_from_stress_test_summary({"results": []}) == {}


# ─────────────────────────────────────────────────────────────────────────────
# 2. Aggregate — 모든 단계 통합
# ─────────────────────────────────────────────────────────────────────────────


class TestAggregate:
    def test_all_stages_pass_produces_aggregated(self, tmp_path):
        # 4 파일 생성.
        bt_path  = tmp_path / "bt.json"
        opt_path = tmp_path / "opt.json"
        wf_path  = tmp_path / "wf.json"
        st_path  = tmp_path / "st.json"
        bt_path.write_text(json.dumps(_backtest_payload()))
        opt_path.write_text(json.dumps(_optimization_payload()))
        wf_path.write_text(json.dumps(_walk_forward_payload()))
        st_path.write_text(json.dumps(_stress_test_payload(all_pass=True)))

        result = aggregate_candidates(AggregationInputs(
            backtest_summary_path=str(bt_path),
            optimization_summary_path=str(opt_path),
            walk_forward_summary_path=str(wf_path),
            stress_test_summary_path=str(st_path),
        ))
        assert len(result) == 1
        c = result[0]
        assert c.strategy == "sma_crossover"
        assert c.symbol   == "005930"
        # 4 stages 모두 carry.
        assert {s.name for s in c.pipeline_stages} == {"3-02", "3-03", "3-04", "3-05"}
        assert c.all_stages_passed()

    def test_stress_fail_marks_3_05_fail(self, tmp_path):
        st_path = tmp_path / "st.json"
        st_path.write_text(json.dumps(_stress_test_payload(all_pass=False)))
        result = aggregate_candidates(AggregationInputs(
            stress_test_summary_path=str(st_path),
        ))
        assert len(result) == 1
        c = result[0]
        # 1 시나리오 FAIL → summary verdict FAIL.
        stage = next(s for s in c.pipeline_stages if s.name == "3-05")
        assert stage.verdict == "FAIL"

    def test_missing_stages_recorded_as_no_pass(self, tmp_path):
        # 3-02, 3-03 만 있고 3-04, 3-05 누락.
        bt_path  = tmp_path / "bt.json"
        opt_path = tmp_path / "opt.json"
        bt_path.write_text(json.dumps(_backtest_payload()))
        opt_path.write_text(json.dumps(_optimization_payload()))
        result = aggregate_candidates(AggregationInputs(
            backtest_summary_path=str(bt_path),
            optimization_summary_path=str(opt_path),
        ))
        assert len(result) == 1
        c = result[0]
        assert {s.name for s in c.pipeline_stages} == {"3-02", "3-03"}
        # 3-04, 3-05 누락 → all_stages_passed False.
        assert not c.all_stages_passed()


# ─────────────────────────────────────────────────────────────────────────────
# 3. build_paper_candidate_config — 0 / 1 / N 시나리오
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildPaperCandidateConfig:
    def test_empty_input_produces_safe_empty(self):
        cfg = build_paper_candidate_config([])
        d = cfg.to_dict()
        assert d["candidate_count"] == 0
        assert d["candidates"] == []
        assert d["reasons_no_candidate"]
        assert "no_pipeline_results_loaded" in d["reasons_no_candidate"]

    def test_no_candidate_when_stages_missing(self):
        # 3-02만 통과한 후보 — 4단계 필수 미달.
        c = AggregatedCandidate(
            strategy="sma_crossover", symbol="005930", params={},
            pipeline_stages=[PipelineStage(name="3-02", verdict="BACKTEST_PASS")],
            risk_metrics={"expectancy": 500.0},
            score=0.05,
        )
        cfg = build_paper_candidate_config([c])
        d = cfg.to_dict()
        assert d["candidate_count"] == 0
        # reasons 에 누락된 단계 정보 carry.
        joined = " ".join(d["reasons_no_candidate"])
        assert "3-03" in joined
        assert "3-04" in joined
        assert "3-05" in joined

    def test_single_candidate_passing_all_stages(self):
        c = AggregatedCandidate(
            strategy="sma_crossover", symbol="005930", params={"short": 5, "long": 20},
            pipeline_stages=[
                PipelineStage(name="3-02", verdict="BACKTEST_PASS"),
                PipelineStage(name="3-03", verdict="PAPER_CANDIDATE"),
                PipelineStage(name="3-04", verdict="HEALTHY"),
                PipelineStage(name="3-05", verdict="PASS"),
            ],
            risk_metrics={"expectancy": 500.0, "risk_adjusted_score": 0.05},
            score=0.05,
        )
        cfg = build_paper_candidate_config([c])
        d = cfg.to_dict()
        assert d["candidate_count"] == 1
        assert d["candidates"][0]["strategy"] == "sma_crossover"
        # 후보 단위 invariant.
        assert d["candidates"][0]["is_order_signal"]    is False
        assert d["candidates"][0]["auto_apply_allowed"] is False

    def test_top_k_caps_candidates(self):
        candidates = []
        for i, score in enumerate([0.9, 0.5, 0.7, 0.3]):
            candidates.append(AggregatedCandidate(
                strategy=f"s_{i}", symbol="005930", params={},
                pipeline_stages=[
                    PipelineStage(name="3-02", verdict="BACKTEST_PASS"),
                    PipelineStage(name="3-03", verdict="PAPER_CANDIDATE"),
                    PipelineStage(name="3-04", verdict="HEALTHY"),
                    PipelineStage(name="3-05", verdict="PASS"),
                ],
                risk_metrics={"expectancy": score * 1000},
                score=score,
            ))
        cfg = build_paper_candidate_config(candidates, top_k=2)
        d = cfg.to_dict()
        assert d["candidate_count"] == 2
        # score 내림차순.
        scores = [c["score"] for c in d["candidates"]]
        assert scores == sorted(scores, reverse=True)
        assert d["candidates"][0]["strategy"] == "s_0"   # 0.9
        assert d["candidates"][1]["strategy"] == "s_2"   # 0.7

    def test_each_candidate_carries_pipeline_stages(self):
        c = AggregatedCandidate(
            strategy="sma_crossover", symbol="005930", params={},
            pipeline_stages=[
                PipelineStage(name="3-02", verdict="BACKTEST_PASS"),
                PipelineStage(name="3-03", verdict="PAPER_CANDIDATE"),
                PipelineStage(name="3-04", verdict="HEALTHY"),
                PipelineStage(name="3-05", verdict="PASS"),
            ],
            risk_metrics={"expectancy": 500.0},
            score=0.05,
        )
        cfg = build_paper_candidate_config([c])
        d = cfg.to_dict()
        candidate = d["candidates"][0]
        assert "pipeline_stages" in candidate
        assert len(candidate["pipeline_stages"]) == 4
        assert sorted(candidate["passed_stages"]) == ["3-02", "3-03", "3-04", "3-05"]

    def test_top_level_invariants(self):
        cfg = build_paper_candidate_config([])
        d = cfg.to_dict()
        assert d["is_order_signal"]       is False
        assert d["auto_apply_allowed"]    is False
        assert d["is_live_authorization"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. File write / read round-trip + 출력 경로
# ─────────────────────────────────────────────────────────────────────────────


class TestFileOutput:
    def test_round_trip(self, tmp_path):
        c = AggregatedCandidate(
            strategy="sma_crossover", symbol="005930", params={"short": 5},
            pipeline_stages=[
                PipelineStage(name="3-02", verdict="BACKTEST_PASS"),
                PipelineStage(name="3-03", verdict="PAPER_CANDIDATE"),
                PipelineStage(name="3-04", verdict="HEALTHY"),
                PipelineStage(name="3-05", verdict="PASS"),
            ],
            risk_metrics={"expectancy": 500.0},
            score=0.05,
        )
        cfg = build_paper_candidate_config([c])
        out = tmp_path / "paper_candidate_config.json"
        written = write_paper_candidate_config(cfg, out)
        assert written == out and out.exists()
        roundtrip = read_paper_candidate_config(out)
        assert roundtrip["candidate_count"] == 1
        assert roundtrip["is_order_signal"] is False

    def test_empty_config_writes_file(self, tmp_path):
        """후보 0건도 *반드시* 파일 생성."""
        cfg = build_paper_candidate_config([])
        out = tmp_path / "paper_candidate_config.json"
        write_paper_candidate_config(cfg, out)
        assert out.exists()
        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert loaded["candidate_count"] == 0
        assert loaded["candidates"] == []
        assert loaded["reasons_no_candidate"]

    def test_creates_parent_directory(self, tmp_path):
        cfg = build_paper_candidate_config([])
        out = tmp_path / "strategy_optimization" / "paper_candidate_config.json"
        write_paper_candidate_config(cfg, out)
        assert out.exists()


# ─────────────────────────────────────────────────────────────────────────────
# 5. CLI 통합
# ─────────────────────────────────────────────────────────────────────────────


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))


def _import_cli():
    spec = importlib.util.spec_from_file_location(
        "run_paper_candidate_aggregator_module",
        _SCRIPTS_DIR / "run_paper_candidate_aggregator.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cli():
    return _import_cli()


class TestCli:
    def test_runs_with_all_four_inputs(self, cli, tmp_path):
        bt = tmp_path / "bt.json"
        opt = tmp_path / "opt.json"
        wf = tmp_path / "wf.json"
        st = tmp_path / "st.json"
        bt.write_text(json.dumps(_backtest_payload()))
        opt.write_text(json.dumps(_optimization_payload()))
        wf.write_text(json.dumps(_walk_forward_payload()))
        st.write_text(json.dumps(_stress_test_payload(all_pass=True)))

        args = cli._parse_args([
            "--dry-run",
            "--from-backtest", str(bt),
            "--from-optimization", str(opt),
            "--from-walk-forward", str(wf),
            "--from-stress-test", str(st),
        ])
        result = cli.run_aggregation(args)
        assert result["aggregated_count"] == 1
        assert result["candidate_count"] == 1

    def test_runs_with_no_inputs_produces_zero_candidates(self, cli):
        args = cli._parse_args(["--dry-run"])
        result = cli.run_aggregation(args)
        assert result["aggregated_count"] == 0
        assert result["candidate_count"] == 0

    def test_writes_to_strategy_optimization_path(self, cli, tmp_path):
        bt = tmp_path / "bt.json"
        opt = tmp_path / "opt.json"
        wf = tmp_path / "wf.json"
        st = tmp_path / "st.json"
        bt.write_text(json.dumps(_backtest_payload()))
        opt.write_text(json.dumps(_optimization_payload()))
        wf.write_text(json.dumps(_walk_forward_payload()))
        st.write_text(json.dumps(_stress_test_payload(all_pass=True)))

        out_dir = tmp_path / "strategy_optimization"
        args = cli._parse_args([
            "--output-dir", str(out_dir),
            "--from-backtest", str(bt),
            "--from-optimization", str(opt),
            "--from-walk-forward", str(wf),
            "--from-stress-test", str(st),
        ])
        result = cli.run_aggregation(args)
        written = cli._write_outputs(result, out_dir)
        assert written["paper_candidate_config"].exists()
        # 경로 = strategy_optimization/paper_candidate_config.json.
        assert written["paper_candidate_config"].name == "paper_candidate_config.json"

    def test_zero_candidate_writes_file_with_reasons(self, cli, tmp_path):
        # 3-02 만 통과, 3-03 ~ 3-05 누락 → 후보 0건 + 사유.
        bt = tmp_path / "bt.json"
        bt.write_text(json.dumps(_backtest_payload()))
        out_dir = tmp_path / "strategy_optimization"
        args = cli._parse_args([
            "--output-dir", str(out_dir),
            "--from-backtest", str(bt),
        ])
        result = cli.run_aggregation(args)
        written = cli._write_outputs(result, out_dir)
        path = written["paper_candidate_config"]
        assert path.exists()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["candidate_count"] == 0
        assert loaded["candidates"] == []
        assert loaded["reasons_no_candidate"]

    def test_summary_invariants_in_file(self, cli, tmp_path):
        args = cli._parse_args([
            "--output-dir", str(tmp_path / "strategy_optimization"),
        ])
        result = cli.run_aggregation(args)
        out_dir = tmp_path / "strategy_optimization"
        cli._write_outputs(result, out_dir)
        loaded = json.loads(
            (out_dir / "paper_candidate_config.json").read_text(encoding="utf-8")
        )
        assert loaded["is_order_signal"]       is False
        assert loaded["auto_apply_allowed"]    is False
        assert loaded["is_live_authorization"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 6. 금지 라벨 / Secret / 정적 import 가드
# ─────────────────────────────────────────────────────────────────────────────


class TestNoForbiddenLabels:
    def test_paper_candidate_json_has_no_order_labels(self, tmp_path):
        c = AggregatedCandidate(
            strategy="sma_crossover", symbol="005930", params={},
            pipeline_stages=[
                PipelineStage(name="3-02", verdict="BACKTEST_PASS"),
                PipelineStage(name="3-03", verdict="PAPER_CANDIDATE"),
                PipelineStage(name="3-04", verdict="HEALTHY"),
                PipelineStage(name="3-05", verdict="PASS"),
            ],
            risk_metrics={"expectancy": 500.0},
            score=0.05,
        )
        cfg = build_paper_candidate_config([c])
        out = tmp_path / "paper_candidate_config.json"
        write_paper_candidate_config(cfg, out)
        text = out.read_text(encoding="utf-8")
        forbidden = ["Place Order", "지금 매수", "지금 매도", "실거래 시작",
                     "ENABLE_LIVE_TRADING"]
        for w in forbidden:
            assert w not in text, f"forbidden label in JSON: {w}"


class TestNoForbiddenImports:
    def test_module_has_no_broker_imports(self):
        import app.analytics.paper_candidate_aggregator as mod
        src = Path(mod.__file__).read_text(encoding="utf-8")
        forbidden = [
            r"from\s+app\.brokers\.kis",
            r"from\s+app\.brokers\.mock_broker",
            r"from\s+app\.execution\.executor",
            r"from\s+app\.execution\.order_router",
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
                f"forbidden in paper_candidate_aggregator.py: {pat}"

    def test_cli_has_no_broker_imports(self):
        src = (_SCRIPTS_DIR / "run_paper_candidate_aggregator.py").read_text(encoding="utf-8")
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
            _SCRIPTS_DIR / "run_paper_candidate_aggregator.py",
            Path(__file__).resolve().parents[1] / "app" / "analytics"
                / "paper_candidate_aggregator.py",
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
                / "paper_candidate_aggregator.py",
            _SCRIPTS_DIR / "run_paper_candidate_aggregator.py",
        ):
            src = path.read_text(encoding="utf-8")
            patterns = [
                r"sk-[A-Za-z0-9]{20,}",
                r"ghp_[A-Za-z0-9]{30,}",
                r"Bearer\s+[A-Za-z0-9._\-]{20,}",
            ]
            for pat in patterns:
                assert not re.search(pat, src), \
                    f"secret pattern in {path.name}: {pat}"


# ─────────────────────────────────────────────────────────────────────────────
# 7. reports/ 경로가 gitignore 인지 확인
# ─────────────────────────────────────────────────────────────────────────────


class TestReportsGitignore:
    def test_reports_dir_in_gitignore(self):
        repo_root = Path(__file__).resolve().parents[2]
        gitignore = repo_root / ".gitignore"
        content = gitignore.read_text(encoding="utf-8")
        # `reports/` 또는 `reports/*` 가 ignore 되어야 한다.
        assert ("reports/" in content) or ("reports/*" in content)
