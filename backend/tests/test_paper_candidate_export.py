"""paper_candidate_config.json 생성기 (paper_candidate.py) + filter 분류기 테스트.

invariant:
- 빈 후보도 *반드시* 파일 생성 + reasons_no_candidate carry.
- candidates 객체 / 최상위 모두 `is_order_signal=False` 등 invariant 노출.
- BUY/SELL/HOLD 단어 0건 (verdict + reasons).
- top_k 상한 적용 — 운영자 인지 부하 최소화.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.backtest.real_data.filters import (
    BacktestVerdict,
    ClassificationResult,
    FilterThresholds,
    classify_backtest_result,
)
from app.backtest.real_data.metrics import (
    REQUIRED_METRIC_KEYS,
    compute_extended_metrics,
)
from app.backtest.real_data.paper_candidate import (
    CandidateInput,
    build_paper_candidate_config,
    read_paper_candidate_config,
    write_paper_candidate_config,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. compute_extended_metrics — 13 키 모두 포함
# ─────────────────────────────────────────────────────────────────────────────

class TestExtendedMetricsKeys:
    def test_all_required_keys_with_zero_trades(self):
        metrics = compute_extended_metrics(
            trades=[],
            initial_cash=10_000_000,
            trading_days=60,
            raw_return=0.0,
            fees_paid=0.0,
            taxes_paid=0.0,
            slippage_paid=0.0,
            max_drawdown=0.0,
        )
        for k in REQUIRED_METRIC_KEYS:
            assert k in metrics, f"missing metric key: {k}"
        # JSON 호환 — inf / NaN 없음.
        text = json.dumps(metrics)
        assert "Infinity" not in text
        assert "NaN"      not in text

    def test_metrics_with_mixed_trades(self):
        class _Trade:
            def __init__(self, pnl):
                self.pnl = pnl
        trades = [_Trade(50_000), _Trade(-20_000), _Trade(30_000), _Trade(-10_000)]
        metrics = compute_extended_metrics(
            trades=trades,
            initial_cash=10_000_000,
            trading_days=60,
            raw_return=0.05,
            fees_paid=2000.0,
            taxes_paid=3000.0,
            slippage_paid=1000.0,
            max_drawdown=0.03,
        )
        assert metrics["trade_count"] == 4
        assert metrics["win_rate"]    == 0.5
        # profit_factor = 80000 / 30000 ≈ 2.667
        assert metrics["profit_factor"] is not None
        assert metrics["profit_factor"] > 2.6
        assert metrics["loss_streak"]   == 1
        assert metrics["expectancy"]    > 0


# ─────────────────────────────────────────────────────────────────────────────
# 2. classify_backtest_result — 5 verdict 분기
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyVerdicts:
    def _base_metrics(self, **over):
        m = {k: 0.0 for k in REQUIRED_METRIC_KEYS}
        m["trade_count"] = 0
        m["loss_streak"] = 0
        m["profit_factor"] = 0.0
        m.update(over)
        return m

    def test_insufficient_data(self):
        r = classify_backtest_result(self._base_metrics(trade_count=5))
        assert r.verdict == BacktestVerdict.INSUFFICIENT_DATA

    def test_negative_expectancy(self):
        r = classify_backtest_result(self._base_metrics(
            trade_count=20, expectancy=-100.0,
        ))
        assert r.verdict == BacktestVerdict.NEGATIVE_EXPECTANCY

    def test_high_drawdown(self):
        r = classify_backtest_result(self._base_metrics(
            trade_count=20, expectancy=200.0, max_drawdown=0.25,
            profit_factor=1.5, fee_adjusted_return=0.05, slippage_adjusted_return=0.04,
        ))
        assert r.verdict == BacktestVerdict.HIGH_DRAWDOWN

    def test_low_quality_due_to_profit_factor(self):
        r = classify_backtest_result(self._base_metrics(
            trade_count=20, expectancy=100.0, max_drawdown=0.05,
            profit_factor=1.05,
            fee_adjusted_return=0.02, slippage_adjusted_return=0.01,
        ))
        assert r.verdict == BacktestVerdict.LOW_QUALITY

    def test_low_quality_due_to_negative_fee_adjusted(self):
        r = classify_backtest_result(self._base_metrics(
            trade_count=20, expectancy=100.0, max_drawdown=0.05,
            profit_factor=2.0,
            fee_adjusted_return=-0.001, slippage_adjusted_return=-0.002,
        ))
        assert r.verdict == BacktestVerdict.LOW_QUALITY

    def test_paper_candidate(self):
        r = classify_backtest_result(self._base_metrics(
            trade_count=30, expectancy=500.0, max_drawdown=0.05,
            profit_factor=1.8,
            fee_adjusted_return=0.08, slippage_adjusted_return=0.07,
        ))
        assert r.verdict == BacktestVerdict.PAPER_CANDIDATE

    def test_thresholds_overridable(self):
        th = FilterThresholds(min_trade_count=5)
        r = classify_backtest_result(
            self._base_metrics(trade_count=6, expectancy=100.0, max_drawdown=0.05,
                                profit_factor=1.5,
                                fee_adjusted_return=0.02, slippage_adjusted_return=0.01),
            thresholds=th,
        )
        assert r.verdict == BacktestVerdict.PAPER_CANDIDATE


# ─────────────────────────────────────────────────────────────────────────────
# 3. paper_candidate_config — 0 / 1 / 2 candidate 시나리오
# ─────────────────────────────────────────────────────────────────────────────

class TestPaperCandidateConfig:
    def _mk_input(self, *, strategy="sma_crossover", symbol="005930",
                  verdict=BacktestVerdict.PAPER_CANDIDATE, score=0.1):
        metrics = {k: 0.0 for k in REQUIRED_METRIC_KEYS}
        metrics["risk_adjusted_score"] = score
        return CandidateInput(
            strategy=strategy,
            symbol=symbol,
            params={},
            risk_metrics=metrics,
            validation_status=verdict,
            reasons=["test"],
            score=score,
        )

    def test_empty_input_produces_safe_empty_config(self):
        cfg = build_paper_candidate_config([])
        assert cfg.candidate_count == 0
        assert cfg.reasons_no_candidate
        d = cfg.to_dict()
        assert d["candidate_count"] == 0
        assert d["is_order_signal"] is False
        assert d["auto_apply_allowed"] is False
        assert d["is_live_authorization"] is False
        # 사유 collection 비어 있지 않음.
        assert d["reasons_no_candidate"]

    def test_no_candidate_when_all_classified_negative(self):
        items = [
            self._mk_input(verdict=BacktestVerdict.LOW_QUALITY),
            self._mk_input(verdict=BacktestVerdict.INSUFFICIENT_DATA),
            self._mk_input(verdict=BacktestVerdict.HIGH_DRAWDOWN),
        ]
        cfg = build_paper_candidate_config(items)
        assert cfg.candidate_count == 0
        # verdict 사유 집계가 reasons_no_candidate 에 포함.
        joined = " ".join(cfg.reasons_no_candidate)
        assert "LOW_QUALITY" in joined or "INSUFFICIENT_DATA" in joined

    def test_single_candidate_passes(self):
        items = [
            self._mk_input(verdict=BacktestVerdict.LOW_QUALITY,    score=0.5),
            self._mk_input(verdict=BacktestVerdict.PAPER_CANDIDATE, score=0.2,
                           strategy="rsi_reversion"),
        ]
        cfg = build_paper_candidate_config(items)
        assert cfg.candidate_count == 1
        assert cfg.candidates[0].strategy == "rsi_reversion"

    def test_top_k_caps_candidates(self):
        items = [
            self._mk_input(verdict=BacktestVerdict.PAPER_CANDIDATE, score=0.9, strategy="a"),
            self._mk_input(verdict=BacktestVerdict.PAPER_CANDIDATE, score=0.5, strategy="b"),
            self._mk_input(verdict=BacktestVerdict.PAPER_CANDIDATE, score=0.7, strategy="c"),
            self._mk_input(verdict=BacktestVerdict.PAPER_CANDIDATE, score=0.3, strategy="d"),
        ]
        cfg = build_paper_candidate_config(items, top_k=2)
        assert cfg.candidate_count == 2
        # score 내림차순.
        scores = [c.score for c in cfg.candidates]
        assert scores == sorted(scores, reverse=True)
        # top 1 = 0.9 ("a"), top 2 = 0.7 ("c").
        assert cfg.candidates[0].strategy == "a"
        assert cfg.candidates[1].strategy == "c"

    def test_each_candidate_has_safety_invariants(self):
        items = [self._mk_input(score=0.2)]
        cfg = build_paper_candidate_config(items)
        d = cfg.to_dict()
        for c in d["candidates"]:
            assert c["is_order_signal"] is False
            assert c["auto_apply_allowed"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. JSON write + round-trip
# ─────────────────────────────────────────────────────────────────────────────

class TestJsonRoundTrip:
    def test_write_and_read_back(self, tmp_path):
        items = [
            CandidateInput(
                strategy="sma_crossover", symbol="005930",
                params={"fast": 5, "slow": 20},
                risk_metrics={k: 0.0 for k in REQUIRED_METRIC_KEYS},
                validation_status=BacktestVerdict.PAPER_CANDIDATE,
                reasons=["test"],
                score=0.123,
            ),
        ]
        cfg = build_paper_candidate_config(items, top_k=1)
        out = tmp_path / "paper_candidate_config.json"
        written = write_paper_candidate_config(cfg, out)
        assert written == out and out.exists()

        roundtrip = read_paper_candidate_config(out)
        assert roundtrip["candidate_count"] == 1
        assert roundtrip["candidates"][0]["strategy"] == "sma_crossover"
        assert roundtrip["is_order_signal"] is False

    def test_empty_config_round_trip(self, tmp_path):
        cfg = build_paper_candidate_config([])
        out = tmp_path / "paper_candidate_config.json"
        write_paper_candidate_config(cfg, out)
        roundtrip = read_paper_candidate_config(out)
        assert roundtrip["candidate_count"] == 0
        assert roundtrip["reasons_no_candidate"]


# ─────────────────────────────────────────────────────────────────────────────
# 5. invariants — JSON 에 BUY/SELL/HOLD/Place Order 단어 0건
# ─────────────────────────────────────────────────────────────────────────────

class TestNoForbiddenLabels:
    def test_paper_candidate_json_has_no_order_labels(self, tmp_path):
        items = [
            CandidateInput(
                strategy="sma_crossover", symbol="005930",
                params={"fast": 5, "slow": 20},
                risk_metrics={k: 0.0 for k in REQUIRED_METRIC_KEYS},
                validation_status=BacktestVerdict.PAPER_CANDIDATE,
                reasons=["all_filters_passed"],
                score=0.5,
            ),
        ]
        cfg = build_paper_candidate_config(items, top_k=2)
        out = tmp_path / "paper_candidate_config.json"
        write_paper_candidate_config(cfg, out)
        text = out.read_text(encoding="utf-8")
        forbidden = ["Place Order", "지금 매수", "지금 매도", "실거래 시작",
                     "ENABLE_LIVE_TRADING"]
        for w in forbidden:
            assert w not in text, f"forbidden label found in JSON: {w}"
