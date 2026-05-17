"""3-02 — 실제 데이터 backtest runner (scripts/run_backtest_real_data.py) 통합 테스트.

invariant:
- 6 전략 × N symbol 매트릭스 — 데이터 있는 symbol 만 실행.
- 4단계 verdict (INSUFFICIENT_DATA / LOW_QUALITY / HIGH_DRAWDOWN / BACKTEST_PASS).
- 결과 파일 (JSON / CSV / Markdown) 생성.
- broker / OrderExecutor / route_order / KIS 주문 API 호출 0건.
- 안전 flag (KIS_IS_PAPER / ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION) 변경 0건.
- secret / .env / API key 노출 0건.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from app.backtest.real_data import (
    BacktestVerdict,
    FilterThresholds,
    REPRESENTATIVE_SYMBOLS,
    classify_backtest_metrics,
)
from app.strategies.concrete import STRATEGY_REGISTRY

# scripts/ 디렉토리 PYTHONPATH 에 추가 — `_import_cli()` 가 런타임에 사용.
# `app.*` 모듈은 본 backend 패키지에 포함되어 있어 sys.path 조정 *전*에 import 가능.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))


def _import_cli():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_backtest_real_data_module",
        _SCRIPTS_DIR / "run_backtest_real_data.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cli():
    return _import_cli()


# ─────────────────────────────────────────────────────────────────────────────
# 1. 전략 / 종목 카탈로그 확인 (3-02 완료 기준 일부)
# ─────────────────────────────────────────────────────────────────────────────

class TestStrategyAndSymbolCatalog:
    def test_six_strategies_registered(self):
        expected = {
            "sma_crossover", "rsi_reversion", "vwap_strategy",
            "orb_vwap", "volume_breakout", "pullback_rebreak",
        }
        assert expected.issubset(set(STRATEGY_REGISTRY.keys()))

    def test_ten_representative_symbols(self):
        assert len(REPRESENTATIVE_SYMBOLS) == 10
        for s in REPRESENTATIVE_SYMBOLS:
            assert len(s.symbol) == 6
            assert s.symbol.isdigit()


# ─────────────────────────────────────────────────────────────────────────────
# 2. CLI 실행 — dry-run 으로 전체 매트릭스 한 번에 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestCliEndToEnd:
    def test_pipeline_runs_with_default_args(self, cli):
        args = cli._parse_args([
            "--dry-run",
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
        ])
        payload = cli.run_pipeline(args)

        # 대표 10 종 모두 로드 시도.
        assert len(payload["per_symbol"]) == len(REPRESENTATIVE_SYMBOLS)
        # 005930 은 CSV_LOCAL.
        statuses = {r["symbol"]: r["data_status"] for r in payload["per_symbol"]}
        assert statuses["005930"] == "CSV_LOCAL"
        # 나머지 9 종은 fixture 없음.
        for sym, st in statuses.items():
            if sym == "005930":
                continue
            assert st in ("DISABLED", "NO_DATA")

    def test_six_strategies_run_for_005930(self, cli):
        args = cli._parse_args([
            "--dry-run",
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
        ])
        payload = cli.run_pipeline(args)
        for rec in payload["per_symbol"]:
            if rec["symbol"] != "005930":
                continue
            assert len(rec["runs"]) == 6
            names = {r["strategy"] for r in rec["runs"]}
            assert names == set(STRATEGY_REGISTRY.keys())

    def test_verdict_in_valid_set(self, cli):
        valid = {v.value for v in BacktestVerdict}
        args = cli._parse_args([
            "--dry-run",
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
        ])
        payload = cli.run_pipeline(args)
        for rec in payload["per_symbol"]:
            for run in rec["runs"]:
                assert run["verdict"] in valid


# ─────────────────────────────────────────────────────────────────────────────
# 3. verdict 분류기 — 4단계 분기
# ─────────────────────────────────────────────────────────────────────────────

class TestVerdictClassifier:
    def _base_metrics(self, **over):
        m = {
            "trade_count": 0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
        }
        m.update(over)
        return m

    def test_insufficient_data(self):
        r = classify_backtest_metrics(self._base_metrics(trade_count=5))
        assert r.verdict == BacktestVerdict.INSUFFICIENT_DATA

    def test_high_drawdown(self):
        r = classify_backtest_metrics(self._base_metrics(
            trade_count=20, profit_factor=2.0, max_drawdown=0.25,
        ))
        assert r.verdict == BacktestVerdict.HIGH_DRAWDOWN

    def test_low_quality(self):
        r = classify_backtest_metrics(self._base_metrics(
            trade_count=20, profit_factor=1.05, max_drawdown=0.05,
        ))
        assert r.verdict == BacktestVerdict.LOW_QUALITY

    def test_backtest_pass(self):
        r = classify_backtest_metrics(self._base_metrics(
            trade_count=30, profit_factor=1.8, max_drawdown=0.05,
        ))
        assert r.verdict == BacktestVerdict.BACKTEST_PASS

    def test_priority_drawdown_over_low_quality(self):
        """HIGH_DRAWDOWN 이 LOW_QUALITY 보다 우선."""
        r = classify_backtest_metrics(self._base_metrics(
            trade_count=20, profit_factor=0.5, max_drawdown=0.25,
        ))
        assert r.verdict == BacktestVerdict.HIGH_DRAWDOWN

    def test_thresholds_overridable(self):
        th = FilterThresholds(min_trade_count=5, min_profit_factor=1.0)
        r = classify_backtest_metrics(
            self._base_metrics(trade_count=6, profit_factor=1.5, max_drawdown=0.05),
            thresholds=th,
        )
        assert r.verdict == BacktestVerdict.BACKTEST_PASS

    def test_safety_invariants_in_classification_result(self):
        r = classify_backtest_metrics(self._base_metrics(
            trade_count=30, profit_factor=1.8, max_drawdown=0.05,
        ))
        d = r.to_dict()
        assert d["is_order_signal"] is False
        assert d["auto_apply_allowed"] is False
        assert d["is_live_authorization"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. 파일 출력 — JSON / CSV / Markdown 모두 생성
# ─────────────────────────────────────────────────────────────────────────────

class TestFileOutputs:
    def test_writes_all_three_artifacts(self, cli, tmp_path):
        args = cli._parse_args([
            "--output-dir", str(tmp_path),
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
        ])
        payload = cli.run_pipeline(args)
        written = cli._write_outputs(payload, tmp_path)
        assert written["summary_json"].exists()
        assert written["ranking_csv"].exists()
        assert written["report_md"].exists()

    def test_summary_json_invariants(self, cli, tmp_path):
        args = cli._parse_args([
            "--output-dir", str(tmp_path),
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
        ])
        payload = cli.run_pipeline(args)
        cli._write_outputs(payload, tmp_path)
        loaded = json.loads(
            (tmp_path / "real_data_backtest_summary.json").read_text(encoding="utf-8")
        )
        assert loaded["is_order_signal"] is False
        assert loaded["auto_apply_allowed"] is False
        assert loaded["is_live_authorization"] is False
        assert "per_symbol" in loaded
        assert "pass_runs"  in loaded


# ─────────────────────────────────────────────────────────────────────────────
# 5. 안전 invariants — broker / OrderExecutor / route_order / KIS 주문 0건
# ─────────────────────────────────────────────────────────────────────────────

class TestSafetyInvariants:
    def test_cli_script_no_forbidden_imports(self):
        src = (_SCRIPTS_DIR / "run_backtest_real_data.py").read_text(encoding="utf-8")
        forbidden = [
            r"from\s+app\.brokers\.kis",
            r"from\s+app\.brokers\.mock_broker",
            r"from\s+app\.execution\.executor",
            r"from\s+app\.execution\.order_router",
            r"broker\.place_order",
            r"route_order\s*\(",
            r"OrderExecutor\s*\(",
            r"KisClient\b",
        ]
        for pat in forbidden:
            assert not re.search(pat, src), f"forbidden pattern in CLI: {pat}"

    def test_real_data_package_modules_no_forbidden_imports(self):
        pkg_dir = Path(__file__).resolve().parents[1] / "app" / "backtest" / "real_data"
        for py_file in pkg_dir.glob("*.py"):
            src = py_file.read_text(encoding="utf-8")
            forbidden = [
                r"from\s+app\.brokers\.kis",
                r"from\s+app\.brokers\.mock_broker",
                r"from\s+app\.execution\.executor",
                r"from\s+app\.execution\.order_router",
                r"broker\.place_order",
                r"route_order\s*\(",
                r"^import\s+anthropic",
                r"^import\s+openai",
                r"^import\s+requests",
            ]
            for pat in forbidden:
                assert not re.search(pat, src, re.MULTILINE), \
                    f"forbidden pattern {pat!r} in {py_file.name}"

    def test_no_safety_flag_mutation_anywhere(self):
        """CLI + pipeline 모듈에서 안전 flag mutate 0건."""
        targets = [
            _SCRIPTS_DIR / "run_backtest_real_data.py",
        ]
        pkg_dir = Path(__file__).resolve().parents[1] / "app" / "backtest" / "real_data"
        targets.extend(pkg_dir.glob("*.py"))
        bad_patterns = [
            r"ENABLE_LIVE_TRADING\s*=\s*['\"]?true",
            r"ENABLE_AI_EXECUTION\s*=\s*['\"]?true",
            r"ENABLE_FUTURES_LIVE_TRADING\s*=\s*['\"]?true",
            r"KIS_IS_PAPER\s*=\s*['\"]?false",
            r"settings\.enable_live_trading\s*=",
            r"settings\.enable_ai_execution\s*=",
        ]
        for path in targets:
            src = path.read_text(encoding="utf-8")
            for pat in bad_patterns:
                assert not re.search(pat, src, re.IGNORECASE), \
                    f"safety flag mutation pattern {pat!r} in {path.name}"
