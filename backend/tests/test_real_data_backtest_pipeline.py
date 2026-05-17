"""3단계 — 실제 데이터 backtest 파이프라인 (run_real_data_backtest.py) 통합 테스트.

invariant:
- 6 전략 × N symbol 매트릭스 — 데이터 있는 symbol 만 실행.
- 13 필수 지표 모두 포함 — REQUIRED_METRIC_KEYS.
- 후보 0건도 paper_candidate_config.json 생성 (사유 명시).
- broker.place_order / route_order / OrderExecutor 호출 0건.
- reports/ 산출물은 .gitignore 로 차단 (test_repository_hygiene 가 lock).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

# repo root scripts 경로 추가 (이미 backend on sys.path 일 수 있음).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))


from app.backtest.real_data import (
    REPRESENTATIVE_SYMBOLS,
    REQUIRED_METRIC_KEYS,
    BacktestVerdict,
)
from app.strategies.concrete import STRATEGY_REGISTRY


def _import_cli():
    """동적 import — sys.path 에 scripts/ 가 들어간 뒤에만 가능."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_real_data_backtest_module",
        _SCRIPTS_DIR / "run_real_data_backtest.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cli():
    return _import_cli()


class TestPipelineExecution:
    def test_six_strategies_all_registered(self):
        # 3-01 — registry 에 6 전략이 정확히 등록되어 있어야 한다.
        expected = {
            "sma_crossover", "rsi_reversion", "vwap_strategy",
            "orb_vwap", "volume_breakout", "pullback_rebreak",
        }
        assert expected.issubset(set(STRATEGY_REGISTRY.keys()))

    def test_pipeline_runs_with_default_args(self, cli):
        """1회 명령으로 전체 파이프라인 실행 — 데이터 있는 symbol 만 백테스트."""
        args = cli._parse_args([
            "--dry-run",
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
        ])
        result = cli.run_pipeline(args)

        # 대표 10 종 모두 로드 시도.
        assert len(result["load_results"]) == len(REPRESENTATIVE_SYMBOLS)

        # 005930 (fixture) 은 CSV_LOCAL.
        statuses = {r["symbol"]: r["status"] for r in result["load_results"]}
        assert statuses["005930"] == "CSV_LOCAL"

        # 나머지 9 종은 fixture 없음 → DISABLED (yfinance off).
        for s in statuses:
            if s == "005930":
                continue
            assert statuses[s] in ("DISABLED", "NO_DATA"), s

    def test_runs_six_strategies_for_005930(self, cli):
        args = cli._parse_args([
            "--dry-run",
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
        ])
        result = cli.run_pipeline(args)
        # per_symbol 에서 005930 의 runs 가 6 개여야 한다.
        for rec in result["per_symbol"]:
            if rec["symbol"] != "005930":
                continue
            assert len(rec["runs"]) == 6
            names = [r["strategy"] for r in rec["runs"]]
            assert set(names) == set(STRATEGY_REGISTRY.keys())


class TestRequiredMetricsPresent:
    def test_all_13_metric_keys_in_results(self, cli):
        args = cli._parse_args([
            "--dry-run",
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
        ])
        result = cli.run_pipeline(args)
        # 005930 의 모든 run 에 필수 13 키 존재.
        for rec in result["per_symbol"]:
            if rec["symbol"] != "005930":
                continue
            for run in rec["runs"]:
                if "metrics" not in run:
                    continue
                metrics = run["metrics"]
                for k in REQUIRED_METRIC_KEYS:
                    assert k in metrics, f"missing metric: {k} in {run['strategy']}"


class TestVerdictRules:
    def test_verdict_in_valid_set(self, cli):
        valid = {v.value for v in BacktestVerdict}
        args = cli._parse_args([
            "--dry-run",
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
        ])
        result = cli.run_pipeline(args)
        for rec in result["per_symbol"]:
            for run in rec["runs"]:
                if "verdict" not in run:
                    continue
                assert run["verdict"] in valid


class TestFileOutputs:
    def test_writes_paper_candidate_config_and_summary(self, cli, tmp_path):
        args = cli._parse_args([
            "--output-dir", str(tmp_path),
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
        ])
        result = cli.run_pipeline(args)
        written = cli._write_outputs(result, tmp_path)

        # 1) paper_candidate_config.json — 항상 생성.
        paper_path = written["paper_candidate_config"]
        assert paper_path.exists()
        payload = json.loads(paper_path.read_text(encoding="utf-8"))
        # 절대 invariant.
        assert payload["is_order_signal"] is False
        assert payload["auto_apply_allowed"] is False
        assert payload["is_live_authorization"] is False
        # candidate_count 필드 존재.
        assert "candidate_count" in payload
        # candidates 가 비었으면 reasons_no_candidate 존재.
        if payload["candidate_count"] == 0:
            assert payload["reasons_no_candidate"]

        # 2) 요약 파일.
        summary_path = written["summary"]
        assert summary_path.exists()
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert "load_results" in summary
        assert "per_symbol" in summary
        assert "candidate_count" in summary


class TestSafetyInvariants:
    def test_cli_script_no_forbidden_imports(self):
        """CLI 스크립트가 broker / OrderExecutor / route_order import 0건."""
        src = (_SCRIPTS_DIR / "run_real_data_backtest.py").read_text(encoding="utf-8")
        forbidden_patterns = [
            r"from\s+app\.brokers\.kis",
            r"from\s+app\.brokers\.mock_broker",
            r"from\s+app\.execution\.executor",
            r"from\s+app\.execution\.order_router",
            r"broker\.place_order",
            r"route_order\s*\(",
        ]
        for pat in forbidden_patterns:
            assert not re.search(pat, src), f"forbidden pattern in CLI: {pat}"

    def test_pipeline_modules_no_forbidden_imports(self):
        """real_data 패키지 전체 — broker / OrderExecutor / route_order import 0건."""
        pkg_dir = Path(__file__).resolve().parents[1] / "app" / "backtest" / "real_data"
        for py_file in pkg_dir.glob("*.py"):
            src = py_file.read_text(encoding="utf-8")
            forbidden_patterns = [
                r"from\s+app\.brokers\.kis",
                r"from\s+app\.brokers\.mock_broker",
                r"from\s+app\.execution\.executor",
                r"from\s+app\.execution\.order_router",
                r"broker\.place_order",
                r"route_order\s*\(",
                # AI / network — pipeline 모듈 직접 사용 금지 (정적 검사).
                r"^import\s+anthropic",
                r"^import\s+openai",
                r"^import\s+requests",
            ]
            for pat in forbidden_patterns:
                assert not re.search(pat, src, re.MULTILINE), \
                    f"forbidden pattern {pat!r} in {py_file.name}"

    def test_no_safety_flag_mutation_in_cli(self):
        """CLI / pipeline 이 안전 flag 를 절대 mutate 하지 않아야 한다."""
        src = (_SCRIPTS_DIR / "run_real_data_backtest.py").read_text(encoding="utf-8")
        bad_patterns = [
            r"ENABLE_LIVE_TRADING\s*=\s*['\"]?true",
            r"ENABLE_AI_EXECUTION\s*=\s*['\"]?true",
            r"ENABLE_FUTURES_LIVE_TRADING\s*=\s*['\"]?true",
            r"KIS_IS_PAPER\s*=\s*['\"]?false",
            r"settings\.enable_live_trading\s*=",
            r"settings\.enable_ai_execution\s*=",
        ]
        for pat in bad_patterns:
            assert not re.search(pat, src, re.IGNORECASE), \
                f"safety flag mutation pattern: {pat}"
