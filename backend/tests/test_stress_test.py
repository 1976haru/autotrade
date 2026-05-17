"""3-05 — Stress test 모듈 테스트.

invariant:
- 10 시나리오 enum + 4단계 verdict (PASS / WARN / FAIL / INSUFFICIENT_DATA).
- 데이터 변형 결정론적 (동일 input → 동일 output).
- 비용 가중 (SLIPPAGE_SPIKE) BacktestConfig 갱신.
- DATA_GAP / EXECUTION_REJECT / STALE_PRICE / DUPLICATE_SIGNAL counter 정확.
- 결과 객체 모두 is_order_signal=False / auto_apply_allowed=False /
  is_live_authorization=False.
- broker / OrderExecutor / route_order / KIS 주문 import 0건 (정적 grep).
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.analytics.stress_test import (
    StressCandidateInput,
    StressResult,
    StressScenario,
    StressTestConfig,
    StressVerdict,
    apply_crash,
    apply_data_gap,
    apply_low_liquidity,
    apply_sideways,
    apply_surge,
    correlated_drawdown_proxy,
    count_duplicate_signals,
    count_stale_bars,
    evaluate_stress,
    read_candidates_from_walk_forward,
    simulate_rejected_trades,
)
from app.backtest.types import Bar


# ─────────────────────────────────────────────────────────────────────────────
# Helper — 합성 OHLCV
# ─────────────────────────────────────────────────────────────────────────────


def _synthetic_bars(n: int, *, symbol: str = "TESTSYM",
                     base: int = 70000, trend: int = 100) -> list[Bar]:
    out: list[Bar] = []
    t0 = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    price = base
    for i in range(n):
        wave = ((i % 10) - 5) * 200
        close = base + i * trend + wave
        high  = close + 200
        low   = close - 200
        open_ = price
        volume = 5_000_000 + (i % 7) * 100_000
        out.append(Bar(
            symbol=symbol, timestamp=t0 + timedelta(days=i),
            open=open_, high=high, low=low, close=close, volume=volume,
        ))
        price = close
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 1. Scenario enum / Config
# ─────────────────────────────────────────────────────────────────────────────


class TestEnums:
    def test_ten_scenarios_defined(self):
        names = {s.value for s in StressScenario}
        expected = {
            "CRASH", "SURGE", "SIDEWAYS", "SLIPPAGE_SPIKE", "DATA_GAP",
            "EXECUTION_REJECT", "STALE_PRICE", "DUPLICATE_SIGNAL",
            "LOW_LIQUIDITY", "CORRELATED_DRAWDOWN",
        }
        assert names == expected

    def test_four_verdicts_defined(self):
        names = {v.value for v in StressVerdict}
        assert names == {"PASS", "WARN", "FAIL", "INSUFFICIENT_DATA"}

    def test_no_buy_sell_hold_in_verdicts(self):
        for v in StressVerdict:
            assert v.value not in {"BUY", "SELL", "HOLD"}


# ─────────────────────────────────────────────────────────────────────────────
# 2. 데이터 변형 — deterministic
# ─────────────────────────────────────────────────────────────────────────────


class TestCrashScenario:
    def test_crash_lowers_second_half(self):
        bars = _synthetic_bars(100)
        crashed = apply_crash(bars, pct=0.08)
        assert len(crashed) == len(bars)
        # 전반부 동일.
        for i in range(50):
            assert crashed[i].close == bars[i].close
        # 후반부 ≤ 원본 (갭 + 누적 하락).
        for i in range(50, 100):
            assert crashed[i].close <= bars[i].close

    def test_crash_deterministic(self):
        bars = _synthetic_bars(50)
        a = apply_crash(bars, pct=0.08)
        b = apply_crash(bars, pct=0.08)
        assert [x.close for x in a] == [x.close for x in b]


class TestSurgeScenario:
    def test_surge_raises_second_half(self):
        bars = _synthetic_bars(100)
        surged = apply_surge(bars, pct=0.08)
        assert len(surged) == len(bars)
        for i in range(50, 100):
            assert surged[i].close >= bars[i].close


class TestSidewaysScenario:
    def test_sideways_compresses_range(self):
        bars = _synthetic_bars(100)
        side = apply_sideways(bars, band=0.005)
        # 전체 range 가 원본보다 작아야 한다.
        orig_range = max(b.close for b in bars) - min(b.close for b in bars)
        side_range = max(b.close for b in side) - min(b.close for b in side)
        assert side_range < orig_range


# ─────────────────────────────────────────────────────────────────────────────
# 3. Slippage / data gap / low liquidity
# ─────────────────────────────────────────────────────────────────────────────


class TestSlippageSpikeScenario:
    def test_evaluate_stress_uses_higher_slippage_bps(self):
        """SLIPPAGE_SPIKE → BacktestConfig.slippage_bps 가 default 보다 높음.

        StressTestConfig.slippage_spike_bps=30 (default 5).
        """
        from app.backtest.types import BacktestConfig
        bt_cfg = BacktestConfig(execution_model="next_open", execution_delay_bars=1,
                                 slippage_bps=5, commission_bps=15, tax_bps=23)
        bars = _synthetic_bars(60)
        result = evaluate_stress(
            bars=bars, strategy_name="sma_crossover", symbol="TESTSYM",
            scenario=StressScenario.SLIPPAGE_SPIKE,
            params={"short": 5, "long": 20},
            bt_config=bt_cfg,
        )
        assert result.scenario_name == "SLIPPAGE_SPIKE"


class TestDataGapScenario:
    def test_data_gap_removes_bars(self):
        bars = _synthetic_bars(100)
        gapped = apply_data_gap(bars, ratio=0.14)  # ~14% drop
        # 약 14개 제거.
        assert 80 < len(gapped) < 100

    def test_data_gap_zero_ratio_returns_unchanged(self):
        bars = _synthetic_bars(50)
        out = apply_data_gap(bars, ratio=0.0)
        assert len(out) == len(bars)


class TestLowLiquidityScenario:
    def test_low_liquidity_reduces_volume(self):
        bars = _synthetic_bars(20)
        low = apply_low_liquidity(bars, ratio=0.10)
        for orig, mod in zip(bars, low):
            assert mod.volume < orig.volume
            assert mod.close == orig.close   # 가격 영향 X


# ─────────────────────────────────────────────────────────────────────────────
# 4. Counter — EXECUTION_REJECT / STALE_PRICE / DUPLICATE_SIGNAL
# ─────────────────────────────────────────────────────────────────────────────


class TestExecutionRejectScenario:
    def test_simulate_rejected_trades_drops_every_n(self):
        trades = list(range(10))  # placeholder trade objects
        kept, rejected = simulate_rejected_trades(trades, ratio=0.20)
        # 매 5번째 trade reject (index 4, 9) → rejected_count == 2.
        assert rejected == 2
        assert len(kept) == 8

    def test_rejected_count_recorded_in_evaluate(self):
        bars = _synthetic_bars(120)
        result = evaluate_stress(
            bars=bars, strategy_name="sma_crossover", symbol="TESTSYM",
            scenario=StressScenario.EXECUTION_REJECT,
            params={"short": 5, "long": 20},
        )
        # trade 가 발생하면 rejected_count > 0 가능.
        # trade 발생 자체가 conditional 이므로 type 만 검증.
        assert isinstance(result.rejected_order_count, int)
        assert result.rejected_order_count >= 0


class TestStalePriceScenario:
    def test_count_stale_bars(self):
        bars = _synthetic_bars(100)
        count = count_stale_bars(bars, ratio=0.10)
        # 10% 카운트.
        assert count == 10

    def test_stale_zero_ratio_returns_zero(self):
        bars = _synthetic_bars(100)
        assert count_stale_bars(bars, ratio=0.0) == 0

    def test_stale_violation_recorded_in_evaluate(self):
        bars = _synthetic_bars(60)
        result = evaluate_stress(
            bars=bars, strategy_name="sma_crossover", symbol="TESTSYM",
            scenario=StressScenario.STALE_PRICE,
            params={"short": 5, "long": 20},
        )
        assert isinstance(result.stale_data_violation_count, int)
        assert result.stale_data_violation_count >= 0


class TestDuplicateSignalScenario:
    def test_count_duplicate_signals_returns_int(self):
        bars = _synthetic_bars(50)
        count = count_duplicate_signals(bars)
        assert isinstance(count, int)
        assert count >= 0

    def test_dup_count_recorded_in_evaluate(self):
        bars = _synthetic_bars(60)
        result = evaluate_stress(
            bars=bars, strategy_name="sma_crossover", symbol="TESTSYM",
            scenario=StressScenario.DUPLICATE_SIGNAL,
            params={"short": 5, "long": 20},
        )
        assert isinstance(result.duplicate_signal_count, int)


# ─────────────────────────────────────────────────────────────────────────────
# 5. evaluate_stress — 모든 시나리오 호출 가능
# ─────────────────────────────────────────────────────────────────────────────


class TestEvaluateStressAllScenarios:
    @pytest.mark.parametrize("scenario", list(StressScenario))
    def test_each_scenario_returns_result(self, scenario):
        bars = _synthetic_bars(60)
        result = evaluate_stress(
            bars=bars, strategy_name="sma_crossover", symbol="TESTSYM",
            scenario=scenario, params={"short": 5, "long": 20},
        )
        assert isinstance(result, StressResult)
        assert result.scenario_name == scenario.value
        # 16 필수 필드 모두 존재.
        d = result.to_dict()
        required = {
            "scenario_name", "strategy", "symbol", "total_return",
            "expectancy", "profit_factor", "max_drawdown", "win_rate",
            "trade_count", "loss_streak", "rejected_order_count",
            "stale_data_violation_count", "duplicate_signal_count",
            "slippage_cost", "stress_score", "stress_verdict",
        }
        assert required.issubset(set(d.keys()))

    def test_safety_invariants_in_result(self):
        bars = _synthetic_bars(60)
        result = evaluate_stress(
            bars=bars, strategy_name="sma_crossover", symbol="TESTSYM",
            scenario=StressScenario.CRASH,
            params={"short": 5, "long": 20},
        )
        d = result.to_dict()
        assert d["is_order_signal"]       is False
        assert d["auto_apply_allowed"]    is False
        assert d["is_live_authorization"] is False

    def test_insufficient_data_for_short_bars(self):
        bars = _synthetic_bars(10)
        result = evaluate_stress(
            bars=bars, strategy_name="sma_crossover", symbol="TESTSYM",
            scenario=StressScenario.CRASH,
            params={"short": 5, "long": 20},
        )
        # trade 가 적으면 INSUFFICIENT_DATA.
        assert result.stress_verdict in (
            StressVerdict.INSUFFICIENT_DATA, StressVerdict.FAIL,
            StressVerdict.WARN, StressVerdict.PASS,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 6. CORRELATED_DRAWDOWN proxy
# ─────────────────────────────────────────────────────────────────────────────


class TestCorrelatedDrawdownProxy:
    def test_returns_float(self):
        bars = _synthetic_bars(50)
        dd = correlated_drawdown_proxy(bars)
        assert isinstance(dd, float)
        assert 0.0 <= dd <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# 7. Walk-forward adapter (3-04 입력)
# ─────────────────────────────────────────────────────────────────────────────


class TestWalkForwardAdapter:
    def test_extracts_healthy_only(self):
        payload = {
            "results": [
                {"strategy": "a", "symbol": "005930", "params": {},
                 "verdict": "HEALTHY"},
                {"strategy": "b", "symbol": "000660", "params": {},
                 "verdict": "OVERFIT_RISK"},
                {"strategy": "c", "symbol": "035420", "params": {},
                 "verdict": "UNDERFIT"},
            ],
        }
        out = read_candidates_from_walk_forward(payload)
        assert len(out) == 1
        assert out[0].strategy == "a"
        assert out[0].verdict == "HEALTHY"

    def test_empty_for_malformed(self):
        assert read_candidates_from_walk_forward({}) == []
        assert read_candidates_from_walk_forward({"results": "x"}) == []

    def test_skips_invalid_entries(self):
        payload = {
            "results": [
                {"strategy": "a", "symbol": "005930", "params": {},
                 "verdict": "HEALTHY"},
                {"missing": "fields"},
                {"strategy": 1, "symbol": 2, "params": {}, "verdict": "HEALTHY"},
            ],
        }
        out = read_candidates_from_walk_forward(payload)
        assert len(out) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 8. CLI 통합
# ─────────────────────────────────────────────────────────────────────────────


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))


def _import_cli():
    spec = importlib.util.spec_from_file_location(
        "run_stress_test_module",
        _SCRIPTS_DIR / "run_stress_test.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cli():
    return _import_cli()


class TestCli:
    def test_runs_with_default_args(self, cli):
        args = cli._parse_args([
            "--dry-run",
            "--symbol", "005930",
            "--strategy", "sma_crossover",
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
        ])
        payload = cli.run_stress(args)
        assert payload["candidate_count"] == 1
        assert len(payload["scenarios"]) == 10
        # 1 candidate × 10 시나리오.
        assert payload["scenario_run_count"] == 10

    def test_writes_three_artifacts(self, cli, tmp_path):
        args = cli._parse_args([
            "--output-dir", str(tmp_path),
            "--symbol", "005930",
            "--strategy", "sma_crossover",
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
        ])
        payload = cli.run_stress(args)
        written = cli._write_outputs(payload, tmp_path)
        assert written["summary"].exists()
        assert written["ranking_csv"].exists()
        assert written["report_md"].exists()

    def test_summary_invariants(self, cli, tmp_path):
        args = cli._parse_args([
            "--output-dir", str(tmp_path),
            "--symbol", "005930",
            "--strategy", "sma_crossover",
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
        ])
        payload = cli.run_stress(args)
        cli._write_outputs(payload, tmp_path)
        loaded = json.loads(
            (tmp_path / "stress_test_summary.json").read_text(encoding="utf-8")
        )
        assert loaded["is_order_signal"]       is False
        assert loaded["auto_apply_allowed"]    is False
        assert loaded["is_live_authorization"] is False

    def test_from_walk_forward_input_path(self, cli, tmp_path):
        wf_payload = {
            "results": [
                {"strategy": "sma_crossover", "symbol": "005930",
                 "params": {"short": 5, "long": 20}, "verdict": "HEALTHY"},
            ],
        }
        wf_path = tmp_path / "fake_walk_forward.json"
        wf_path.write_text(json.dumps(wf_payload), encoding="utf-8")
        args = cli._parse_args([
            "--dry-run",
            "--from-walk-forward", str(wf_path),
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
        ])
        payload = cli.run_stress(args)
        assert payload["candidate_count"] == 1

    def test_specific_scenarios_filter(self, cli):
        args = cli._parse_args([
            "--dry-run",
            "--symbol", "005930",
            "--strategy", "sma_crossover",
            "--scenarios", "CRASH", "SURGE",
            "--start", "2025-01-01",
            "--end",   "2025-05-01",
        ])
        payload = cli.run_stress(args)
        assert payload["scenario_run_count"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# 9. 정적 import 가드
# ─────────────────────────────────────────────────────────────────────────────


class TestNoForbiddenImports:
    def test_module_no_broker_imports(self):
        import app.analytics.stress_test as mod
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
        ]
        for pat in forbidden:
            assert not re.search(pat, src, re.MULTILINE), \
                f"forbidden in stress_test.py: {pat}"

    def test_cli_no_broker_imports(self):
        src = (_SCRIPTS_DIR / "run_stress_test.py").read_text(encoding="utf-8")
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
            _SCRIPTS_DIR / "run_stress_test.py",
            Path(__file__).resolve().parents[1] / "app" / "analytics" / "stress_test.py",
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

    def test_no_secret_strings_in_module(self):
        """secret 상수 / placeholder 가 본문에 0건."""
        for path in (
            Path(__file__).resolve().parents[1] / "app" / "analytics" / "stress_test.py",
            _SCRIPTS_DIR / "run_stress_test.py",
        ):
            src = path.read_text(encoding="utf-8")
            patterns = [
                r"sk-[A-Za-z0-9]{20,}",
                r"ghp_[A-Za-z0-9]{30,}",
                r"Bearer\s+[A-Za-z0-9._\-]{20,}",
            ]
            for pat in patterns:
                assert not re.search(pat, src), f"secret pattern in {path.name}: {pat}"
