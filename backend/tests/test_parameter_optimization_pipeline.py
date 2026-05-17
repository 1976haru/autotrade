"""3-03 — parameter optimization CLI 통합 테스트.

invariant:
- 6 전략 × 10 종목 × N 파라미터 그리드 실행.
- 5단계 verdict 분류 결과 carry.
- paper_candidate_config.json 생성 (후보 0건도 파일 생성).
- broker / OrderExecutor / route_order / KIS 주문 0건.
- 안전 flag mutate 0건.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from app.backtest.real_data import (
    OptimizationVerdict,
    PARAMETER_GRIDS,
    REPRESENTATIVE_SYMBOLS,
    total_combinations,
)
from app.strategies.concrete import STRATEGY_REGISTRY

# scripts/ 디렉토리 PYTHONPATH 에 추가 — `_import_cli()` 가 런타임에 사용.
# `app.*` 모듈은 본 backend 패키지에 포함되어 있어 sys.path 조정 *전*에 import 가능.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))


def _import_cli():
    spec = importlib.util.spec_from_file_location(
        "run_parameter_optimization_module",
        _SCRIPTS_DIR / "run_parameter_optimization.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cli():
    return _import_cli()


class TestPipelineExecution:
    def test_six_strategies_have_grids(self):
        expected = set(STRATEGY_REGISTRY.keys())
        assert expected == set(PARAMETER_GRIDS.keys())

    def test_pipeline_runs_with_default_args(self, cli):
        args = cli._parse_args([
            "--dry-run",
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
        ])
        payload = cli.run_optimization(args)

        # 대표 10 종 모두 로드 시도.
        assert len(payload["load_results"]) == len(REPRESENTATIVE_SYMBOLS)
        # 005930 은 CSV 보유 → grid × 6 strategy 만큼 run 발생.
        # 다른 9 종은 데이터 없음 → all_runs 에 추가 안 됨.
        statuses = {r["symbol"]: r["status"] for r in payload["load_results"]}
        assert statuses["005930"] == "CSV_LOCAL"

        # 005930 한 종목 × 총 grid combinations = 29 runs.
        assert len(payload["all_runs"]) == total_combinations()

    def test_runs_cover_all_six_strategies(self, cli):
        args = cli._parse_args([
            "--dry-run",
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
        ])
        payload = cli.run_optimization(args)
        strategies_in_runs = {r["strategy"] for r in payload["all_runs"]}
        assert strategies_in_runs == set(STRATEGY_REGISTRY.keys())

    def test_verdict_values_in_optimization_enum(self, cli):
        valid = {v.value for v in OptimizationVerdict}
        args = cli._parse_args([
            "--dry-run",
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
        ])
        payload = cli.run_optimization(args)
        for run in payload["all_runs"]:
            assert run["verdict"] in valid


class TestFileOutputs:
    def test_writes_all_four_artifacts(self, cli, tmp_path):
        args = cli._parse_args([
            "--output-dir", str(tmp_path),
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
        ])
        payload = cli.run_optimization(args)
        written = cli._write_outputs(payload, tmp_path)

        assert written["paper_candidate_config"].exists()
        assert written["summary"].exists()
        assert written["ranking_csv"].exists()
        assert written["report_md"].exists()

    def test_paper_candidate_config_invariants(self, cli, tmp_path):
        args = cli._parse_args([
            "--output-dir", str(tmp_path),
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
        ])
        payload = cli.run_optimization(args)
        cli._write_outputs(payload, tmp_path)
        loaded = json.loads(
            (tmp_path / "paper_candidate_config.json").read_text(encoding="utf-8")
        )
        assert loaded["is_order_signal"]       is False
        assert loaded["auto_apply_allowed"]    is False
        assert loaded["is_live_authorization"] is False
        # 후보 0 건이면 reasons_no_candidate 채워짐.
        if loaded["candidate_count"] == 0:
            assert loaded["reasons_no_candidate"]

    def test_summary_invariants(self, cli, tmp_path):
        args = cli._parse_args([
            "--output-dir", str(tmp_path),
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
        ])
        payload = cli.run_optimization(args)
        cli._write_outputs(payload, tmp_path)
        loaded = json.loads(
            (tmp_path / "parameter_optimization_summary.json").read_text(encoding="utf-8")
        )
        assert loaded["is_order_signal"]       is False
        assert loaded["auto_apply_allowed"]    is False
        assert loaded["is_live_authorization"] is False


class TestSafetyInvariants:
    def test_cli_no_forbidden_imports(self):
        src = (_SCRIPTS_DIR / "run_parameter_optimization.py").read_text(encoding="utf-8")
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
        src = (_SCRIPTS_DIR / "run_parameter_optimization.py").read_text(encoding="utf-8")
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
                f"safety flag mutation: {pat}"
