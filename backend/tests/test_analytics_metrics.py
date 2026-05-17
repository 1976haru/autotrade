"""3-06 — 표준 성과 지표 모듈 테스트.

invariant:
- 14 필수 키 (+ avg_trade_pnl alias = 15) 모두 포함.
- 빈 거래 / 손실 없는 경우 / NaN / inf 안전 처리.
- JSON 직렬화 가능 (Infinity / NaN 0건).
- broker / OrderExecutor / route_order 호출 0건 (순수 함수).
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest

from app.analytics.metrics import (
    DEFAULT_MIN_TRADE_COUNT_FOR_FULL,
    PERFORMANCE_METRIC_KEYS,
    annualize_return,
    assert_required_keys_present,
    compute_loss_streak,
    compute_max_drawdown,
    compute_max_drawdown_from_equity,
    compute_performance_metrics,
    compute_profit_factor,
    compute_sharpe_like_score,
    extract_pnl,
    is_insufficient_data,
    safe_empty_metrics,
    safe_float,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helper — 다양한 trade 형식 지원 (dataclass / dict / objects with .pnl)
# ─────────────────────────────────────────────────────────────────────────────


class _Trade:
    def __init__(self, pnl: float):
        self.pnl = pnl


# ─────────────────────────────────────────────────────────────────────────────
# 1. 안전 helper (safe_float / extract_pnl)
# ─────────────────────────────────────────────────────────────────────────────


class TestSafeFloat:
    def test_returns_default_for_none(self):
        assert safe_float(None) == 0.0
        assert safe_float(None, default=5.0) == 5.0

    def test_returns_default_for_nan(self):
        assert safe_float(float("nan")) == 0.0

    def test_returns_default_for_inf(self):
        assert safe_float(float("inf")) == 0.0
        assert safe_float(float("-inf")) == 0.0

    def test_passes_normal_floats(self):
        assert safe_float(1.5) == 1.5
        assert safe_float(0) == 0.0
        assert safe_float("3.14") == 3.14


class TestExtractPnl:
    def test_extracts_from_object_with_pnl_attr(self):
        assert extract_pnl(_Trade(100.0)) == 100.0

    def test_extracts_from_dict(self):
        assert extract_pnl({"pnl": -50.0}) == -50.0

    def test_returns_zero_for_none(self):
        assert extract_pnl(None) == 0.0

    def test_returns_zero_for_missing_pnl(self):
        assert extract_pnl({"other": 1}) == 0.0
        assert extract_pnl(object()) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 2. 단일 지표 계산
# ─────────────────────────────────────────────────────────────────────────────


class TestWinRate:
    def test_basic_win_rate(self):
        m = compute_performance_metrics(
            trades=[_Trade(100), _Trade(-50), _Trade(100), _Trade(100)],
            initial_cash=10_000_000, trading_days=60,
        )
        # 3 wins / 4 trades = 0.75.
        assert m["win_rate"] == 0.75

    def test_zero_trades_returns_zero(self):
        m = compute_performance_metrics(
            trades=[], initial_cash=10_000_000, trading_days=0,
        )
        assert m["win_rate"] == 0.0


class TestProfitFactor:
    def test_positive_pf(self):
        pnls = [100.0, -50.0, 200.0, -50.0]   # win=300, loss=100 → PF=3.0
        assert compute_profit_factor(pnls) == 3.0

    def test_no_losses_with_wins_returns_none(self):
        """손실 없는 경우 PF=무한 회피 → None (JSON 안전)."""
        assert compute_profit_factor([100.0, 50.0]) is None

    def test_no_trades_returns_zero(self):
        assert compute_profit_factor([]) == 0.0

    def test_all_losses_returns_zero(self):
        # win 0, loss 100 → PF = 0/100 = 0.
        assert compute_profit_factor([-100.0]) == 0.0

    def test_pf_in_metrics_dict_is_json_safe(self):
        """profit_factor=None 으로 직렬화 가능 (Infinity 회피)."""
        m = compute_performance_metrics(
            trades=[_Trade(100), _Trade(50)],
            initial_cash=10_000_000, trading_days=10,
        )
        text = json.dumps(m)
        assert "Infinity" not in text
        assert "NaN" not in text
        # PF 가 None 으로 직렬화.
        assert m["profit_factor"] is None or isinstance(m["profit_factor"], (int, float))


class TestMaxDrawdown:
    def test_max_drawdown_basic(self):
        # cum=[10,30,15,25,5] → peaks=[10,30,30,30,30] → dd 최대=(30-5)/30=0.833
        pnls = [10.0, 20.0, -15.0, 10.0, -20.0]
        dd = compute_max_drawdown(pnls)
        assert 0.0 < dd <= 1.0
        # 정확히 25/30 = 0.833...
        assert abs(dd - 25 / 30) < 0.001

    def test_no_drawdown_returns_zero(self):
        # 누적 증가만 → drawdown 0.
        assert compute_max_drawdown([10.0, 10.0, 10.0]) == 0.0

    def test_max_drawdown_clamps_to_unit(self):
        assert 0.0 <= compute_max_drawdown([100.0, -200.0]) <= 1.0

    def test_max_drawdown_from_equity_curve(self):
        # peak=1100, low=800 → dd = 300/1100 ≈ 0.273.
        eq = [1000.0, 1100.0, 900.0, 800.0, 950.0]
        dd = compute_max_drawdown_from_equity(eq)
        assert abs(dd - 300 / 1100) < 0.001


class TestExpectancy:
    def test_expectancy_is_average_pnl(self):
        m = compute_performance_metrics(
            trades=[_Trade(100), _Trade(-50), _Trade(200)],
            initial_cash=10_000_000, trading_days=10,
        )
        # (100 - 50 + 200) / 3 = 83.333.
        assert abs(m["expectancy"] - 250 / 3) < 0.01

    def test_expectancy_zero_for_empty_trades(self):
        m = safe_empty_metrics()
        assert m["expectancy"] == 0.0

    def test_avg_trade_pnl_equals_expectancy(self):
        """avg_trade_pnl alias must equal expectancy."""
        m = compute_performance_metrics(
            trades=[_Trade(100), _Trade(-50)],
            initial_cash=10_000_000, trading_days=10,
        )
        assert m["avg_trade_pnl"] == m["expectancy"]


class TestLossStreak:
    def test_consecutive_losses(self):
        pnls = [10, -1, -1, -1, 5, -1, -1]
        assert compute_loss_streak(pnls) == 3

    def test_no_losses(self):
        assert compute_loss_streak([10, 20, 30]) == 0

    def test_all_losses(self):
        assert compute_loss_streak([-1, -1, -1, -1]) == 4

    def test_zero_pnl_breaks_streak(self):
        # 0 은 음수 아니므로 streak 끊김.
        assert compute_loss_streak([-1, 0, -1, -1]) == 2

    def test_empty_returns_zero(self):
        assert compute_loss_streak([]) == 0


class TestSharpeLikeScore:
    def test_zero_when_too_few_samples(self):
        assert compute_sharpe_like_score([]) == 0.0
        assert compute_sharpe_like_score([100.0]) == 0.0

    def test_zero_when_std_zero(self):
        # 모든 PnL 동일 → std=0.
        assert compute_sharpe_like_score([100.0, 100.0, 100.0]) == 0.0

    def test_positive_mean_returns_positive_score(self):
        score = compute_sharpe_like_score([100.0, 50.0, 150.0, 200.0])
        assert score > 0

    def test_negative_mean_returns_negative_score(self):
        score = compute_sharpe_like_score([-100.0, -50.0, -150.0])
        assert score < 0

    def test_score_is_finite(self):
        score = compute_sharpe_like_score([10.0, -5.0, 8.0])
        assert math.isfinite(score)


# ─────────────────────────────────────────────────────────────────────────────
# 3. 수수료 / 슬리피지 반영
# ─────────────────────────────────────────────────────────────────────────────


class TestFeeSlippageAdjustment:
    def test_raw_vs_fee_adjusted_separate(self):
        m = compute_performance_metrics(
            trades=[_Trade(1000)],
            initial_cash=10_000_000, trading_days=10,
            raw_total_return=0.10,
            fees_paid=2000, taxes_paid=3000, slippage_paid=1000,
        )
        # total_return 은 raw 그대로.
        assert m["total_return"] == 0.10
        # fee_adjusted = 0.10 - (2000+3000)/10_000_000 = 0.10 - 0.0005 = 0.0995.
        assert abs(m["fee_adjusted_return"] - 0.0995) < 1e-6
        # slippage_adjusted = 0.0995 - 1000/10_000_000 = 0.09940.
        assert abs(m["slippage_adjusted_return"] - 0.0994) < 1e-6

    def test_zero_fees_means_raw_equals_fee_adjusted(self):
        m = compute_performance_metrics(
            trades=[_Trade(100)],
            initial_cash=10_000_000, trading_days=10,
            raw_total_return=0.05,
            fees_paid=0, taxes_paid=0, slippage_paid=0,
        )
        assert m["fee_adjusted_return"] == m["total_return"]
        assert m["slippage_adjusted_return"] == m["total_return"]


# ─────────────────────────────────────────────────────────────────────────────
# 4. annualize / risk_adjusted_score
# ─────────────────────────────────────────────────────────────────────────────


class TestAnnualizeReturn:
    def test_one_year_returns_same(self):
        # 252 trading days = 1 year.
        a = annualize_return(0.10, 252)
        assert abs(a - 0.10) < 0.001

    def test_zero_days_returns_zero(self):
        assert annualize_return(0.10, 0) == 0.0

    def test_invalid_return_returns_zero(self):
        assert annualize_return(-1.5, 252) == 0.0


class TestRiskAdjustedScore:
    def test_zero_drawdown_returns_zero(self):
        # 모든 trade win → max_dd=0 → risk_adjusted_score=0.
        m = compute_performance_metrics(
            trades=[_Trade(100), _Trade(100), _Trade(100)],
            initial_cash=10_000_000, trading_days=10,
        )
        assert m["risk_adjusted_score"] == 0.0

    def test_positive_score_with_drawdown(self):
        m = compute_performance_metrics(
            trades=[_Trade(100), _Trade(-50), _Trade(150)],
            initial_cash=10_000_000, trading_days=10,
        )
        # expectancy > 0 + max_dd > 0 → risk_adjusted_score 계산됨.
        if m["max_drawdown"] > 0:
            assert m["risk_adjusted_score"] != 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 5. 필수 키 / INSUFFICIENT_DATA / JSON 직렬화
# ─────────────────────────────────────────────────────────────────────────────


class TestRequiredKeys:
    def test_all_keys_present_with_trades(self):
        m = compute_performance_metrics(
            trades=[_Trade(100), _Trade(-50)],
            initial_cash=10_000_000, trading_days=60,
        )
        missing = assert_required_keys_present(m)
        assert missing == []

    def test_all_keys_present_with_empty_trades(self):
        m = compute_performance_metrics(
            trades=[], initial_cash=10_000_000, trading_days=0,
        )
        missing = assert_required_keys_present(m)
        assert missing == []

    def test_required_keys_count_is_15(self):
        # 14 spec + 1 alias (avg_trade_pnl).
        assert len(PERFORMANCE_METRIC_KEYS) == 15

    def test_required_keys_include_user_spec(self):
        user_required = {
            "total_return", "annualized_return", "win_rate", "trade_count",
            "profit_factor", "expectancy", "max_drawdown", "avg_trade_pnl",
            "avg_win", "avg_loss", "loss_streak",
            "fee_adjusted_return", "slippage_adjusted_return",
        }
        assert user_required.issubset(set(PERFORMANCE_METRIC_KEYS))
        # 14번째: sharpe_like_score 또는 risk_adjusted_score — 둘 다 포함.
        assert "risk_adjusted_score" in PERFORMANCE_METRIC_KEYS
        assert "sharpe_like_score"   in PERFORMANCE_METRIC_KEYS


class TestInsufficientData:
    def test_below_default_threshold(self):
        m = compute_performance_metrics(
            trades=[_Trade(100)] * 5,
            initial_cash=10_000_000, trading_days=10,
        )
        assert is_insufficient_data(m) is True

    def test_above_default_threshold(self):
        m = compute_performance_metrics(
            trades=[_Trade(100)] * 20,
            initial_cash=10_000_000, trading_days=10,
        )
        assert is_insufficient_data(m) is False

    def test_custom_threshold(self):
        m = compute_performance_metrics(
            trades=[_Trade(100)] * 3,
            initial_cash=10_000_000, trading_days=10,
        )
        assert is_insufficient_data(m, min_trade_count=2) is False
        assert is_insufficient_data(m, min_trade_count=5) is True


class TestJsonSerializable:
    def test_metrics_dict_json_serializable_empty(self):
        m = compute_performance_metrics(
            trades=[], initial_cash=10_000_000, trading_days=0,
        )
        text = json.dumps(m)
        assert "Infinity" not in text and "NaN" not in text
        # round-trip.
        loaded = json.loads(text)
        for k in PERFORMANCE_METRIC_KEYS:
            assert k in loaded

    def test_metrics_dict_json_serializable_with_trades(self):
        m = compute_performance_metrics(
            trades=[_Trade(100), _Trade(-50), _Trade(200)],
            initial_cash=10_000_000, trading_days=60,
            raw_total_return=0.05,
            fees_paid=2000, taxes_paid=3000, slippage_paid=1000,
        )
        text = json.dumps(m)
        loaded = json.loads(text)
        assert loaded["trade_count"] == 3
        # PF 가 None 또는 finite float.
        pf = loaded["profit_factor"]
        assert pf is None or (isinstance(pf, (int, float)) and math.isfinite(pf))

    def test_nan_inputs_get_clamped(self):
        """NaN / inf inputs 가 안전한 0.0 으로 클램프."""
        m = compute_performance_metrics(
            trades=[], initial_cash=10_000_000, trading_days=0,
            raw_total_return=float("nan"),
            fees_paid=float("inf"), slippage_paid=float("-inf"),
        )
        text = json.dumps(m)
        assert "Infinity" not in text and "NaN" not in text


# ─────────────────────────────────────────────────────────────────────────────
# 6. equity_curve 우선순위
# ─────────────────────────────────────────────────────────────────────────────


class TestMaxDrawdownPriority:
    def test_explicit_max_drawdown_arg_wins(self):
        m = compute_performance_metrics(
            trades=[_Trade(100), _Trade(-50)],
            initial_cash=10_000_000, trading_days=10,
            max_drawdown=0.05,   # 명시.
            equity_curve=[1000, 1100, 800],
        )
        assert m["max_drawdown"] == 0.05

    def test_equity_curve_used_when_no_explicit(self):
        m = compute_performance_metrics(
            trades=[_Trade(100), _Trade(-50)],
            initial_cash=10_000_000, trading_days=10,
            equity_curve=[1000.0, 1100.0, 800.0],
        )
        # 300/1100 ≈ 0.273
        assert abs(m["max_drawdown"] - 300/1100) < 0.001

    def test_pnl_based_when_no_explicit_or_curve(self):
        m = compute_performance_metrics(
            trades=[_Trade(100), _Trade(-150)],
            initial_cash=10_000_000, trading_days=10,
        )
        # cum=[100, -50] → peak=100, dd=(100-(-50))/100=1.5 → clamped to 1.0.
        assert m["max_drawdown"] == 1.0


# ─────────────────────────────────────────────────────────────────────────────
# 7. 기존 백테스트/최적화/스트레스 모듈과의 호환
# ─────────────────────────────────────────────────────────────────────────────


class TestCompatibilityWithExistingModules:
    """3-03 verdict 분류기 / 3-05 stress_test 가 사용하는 metric 키를 모두 제공."""

    # 3-03 optimization_verdicts.classify_optimization_run 가 읽는 키.
    OPTIMIZATION_REQUIRED_KEYS = {
        "trade_count", "expectancy", "profit_factor", "max_drawdown",
    }
    # 3-05 stress_test._compute_metrics 가 생성하는 키.
    STRESS_TEST_KEYS = {
        "trade_count", "total_return", "expectancy", "profit_factor",
        "max_drawdown", "win_rate", "loss_streak",
    }
    # 3-02 verdicts.classify_backtest_metrics 가 읽는 키.
    BACKTEST_VERDICT_KEYS = {
        "trade_count", "profit_factor", "max_drawdown",
    }

    def test_optimization_verdict_keys_present(self):
        for k in self.OPTIMIZATION_REQUIRED_KEYS:
            assert k in PERFORMANCE_METRIC_KEYS, f"3-03 키 누락: {k}"

    def test_stress_test_keys_present(self):
        for k in self.STRESS_TEST_KEYS:
            assert k in PERFORMANCE_METRIC_KEYS, f"3-05 키 누락: {k}"

    def test_backtest_verdict_keys_present(self):
        for k in self.BACKTEST_VERDICT_KEYS:
            assert k in PERFORMANCE_METRIC_KEYS, f"3-02 키 누락: {k}"

    def test_compute_metrics_provides_all_consumer_keys(self):
        """compute_performance_metrics 출력이 모든 consumer 키를 carry."""
        m = compute_performance_metrics(
            trades=[_Trade(100), _Trade(-50)],
            initial_cash=10_000_000, trading_days=60,
        )
        for k in (self.OPTIMIZATION_REQUIRED_KEYS
                  | self.STRESS_TEST_KEYS
                  | self.BACKTEST_VERDICT_KEYS):
            assert k in m, f"키 누락 in compute_performance_metrics: {k}"

    def test_optimization_classifier_accepts_metrics(self):
        """3-03 classifier 가 본 모듈 결과를 그대로 받을 수 있는지."""
        from app.backtest.real_data.optimization_verdicts import (
            OptimizationThresholds, classify_optimization_run,
        )
        m = compute_performance_metrics(
            trades=[_Trade(100)] * 30,
            initial_cash=10_000_000, trading_days=60,
        )
        # PASS 조건이 아닐 수 있지만 호환 자체는 성립해야 한다.
        result = classify_optimization_run(m, thresholds=OptimizationThresholds())
        assert result.verdict is not None


# ─────────────────────────────────────────────────────────────────────────────
# 8. 정적 import 가드 — broker / OrderExecutor / route_order / KIS 0건
# ─────────────────────────────────────────────────────────────────────────────


class TestNoForbiddenImports:
    def test_module_has_no_broker_imports(self):
        import app.analytics.metrics as mod
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
                f"forbidden pattern in metrics.py: {pat}"

    def test_no_safety_flag_mutation(self):
        import app.analytics.metrics as mod
        src = Path(mod.__file__).read_text(encoding="utf-8")
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

    def test_no_secret_strings(self):
        import app.analytics.metrics as mod
        src = Path(mod.__file__).read_text(encoding="utf-8")
        patterns = [
            r"sk-[A-Za-z0-9]{20,}",
            r"ghp_[A-Za-z0-9]{30,}",
            r"Bearer\s+[A-Za-z0-9._\-]{20,}",
        ]
        for pat in patterns:
            assert not re.search(pat, src), f"secret pattern in metrics.py: {pat}"
