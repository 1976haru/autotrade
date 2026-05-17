"""3-03 — optimization verdict 분류기 + paper_candidate exporter 테스트.

invariant:
- 5단계 verdict (INSUFFICIENT_DATA / NEGATIVE_EXPECTANCY / HIGH_DRAWDOWN /
  LOW_QUALITY / PAPER_CANDIDATE).
- 우선순위: INSUFFICIENT_DATA > NEGATIVE_EXPECTANCY > HIGH_DRAWDOWN > LOW_QUALITY > PAPER_CANDIDATE.
- 후보 0건도 JSON 파일 생성 + reasons 명시.
- 모든 결과 객체: is_order_signal=False / auto_apply_allowed=False / is_live_authorization=False.
- BUY/SELL/HOLD/Place Order 단어 0건.
"""

from __future__ import annotations

import re
from pathlib import Path


from app.backtest.real_data.optimization_verdicts import (
    OptimizationThresholds,
    OptimizationVerdict,
    classify_optimization_run,
)
from app.backtest.real_data.paper_candidate import (
    CandidateInput,
    build_paper_candidate_config,
    read_paper_candidate_config,
    write_paper_candidate_config,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. classify_optimization_run — 5 verdict 분기
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyOptimizationRun:
    def _metrics(self, **over):
        m = {
            "trade_count": 0,
            "expectancy": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
        }
        m.update(over)
        return m

    def test_insufficient_data(self):
        r = classify_optimization_run(self._metrics(trade_count=5))
        assert r.verdict == OptimizationVerdict.INSUFFICIENT_DATA

    def test_negative_expectancy(self):
        r = classify_optimization_run(self._metrics(
            trade_count=20, expectancy=-100.0,
        ))
        assert r.verdict == OptimizationVerdict.NEGATIVE_EXPECTANCY

    def test_high_drawdown(self):
        r = classify_optimization_run(self._metrics(
            trade_count=20, expectancy=200.0,
            profit_factor=2.0, max_drawdown=0.25,
        ))
        assert r.verdict == OptimizationVerdict.HIGH_DRAWDOWN

    def test_low_quality_profit_factor(self):
        r = classify_optimization_run(self._metrics(
            trade_count=20, expectancy=100.0,
            profit_factor=1.05, max_drawdown=0.05,
        ))
        assert r.verdict == OptimizationVerdict.LOW_QUALITY

    def test_paper_candidate_passes_all(self):
        r = classify_optimization_run(self._metrics(
            trade_count=30, expectancy=500.0,
            profit_factor=1.8, max_drawdown=0.05,
        ))
        assert r.verdict == OptimizationVerdict.PAPER_CANDIDATE

    def test_priority_insufficient_over_negative_expectancy(self):
        # 데이터 부족이 더 우선.
        r = classify_optimization_run(self._metrics(
            trade_count=5, expectancy=-100.0, max_drawdown=0.5,
        ))
        assert r.verdict == OptimizationVerdict.INSUFFICIENT_DATA

    def test_priority_negative_expectancy_over_drawdown(self):
        # 음수 기대값이 더 우선.
        r = classify_optimization_run(self._metrics(
            trade_count=20, expectancy=-100.0, max_drawdown=0.5,
        ))
        assert r.verdict == OptimizationVerdict.NEGATIVE_EXPECTANCY

    def test_priority_drawdown_over_low_quality(self):
        # 드로다운 한도가 더 우선.
        r = classify_optimization_run(self._metrics(
            trade_count=20, expectancy=100.0,
            profit_factor=0.5, max_drawdown=0.25,
        ))
        assert r.verdict == OptimizationVerdict.HIGH_DRAWDOWN

    def test_thresholds_overridable(self):
        th = OptimizationThresholds(
            min_trade_count=5,
            min_profit_factor=1.0,
            max_drawdown_pct=0.30,
        )
        r = classify_optimization_run(
            self._metrics(trade_count=6, expectancy=100.0,
                          profit_factor=1.5, max_drawdown=0.25),
            thresholds=th,
        )
        assert r.verdict == OptimizationVerdict.PAPER_CANDIDATE

    def test_safety_invariants_in_classification_result(self):
        r = classify_optimization_run(self._metrics(
            trade_count=30, expectancy=500.0,
            profit_factor=1.8, max_drawdown=0.05,
        ))
        d = r.to_dict()
        assert d["is_order_signal"]       is False
        assert d["auto_apply_allowed"]    is False
        assert d["is_live_authorization"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 2. paper_candidate — 0 / 1 / N 후보 시나리오
# ─────────────────────────────────────────────────────────────────────────────

class TestPaperCandidateBuilder:
    def _mk(self, *, strategy="sma_crossover", symbol="005930",
            verdict=OptimizationVerdict.PAPER_CANDIDATE, score=0.5):
        return CandidateInput(
            strategy=strategy,
            symbol=symbol,
            params={"short": 5, "long": 20},
            risk_metrics={"trade_count": 30, "expectancy": score * 100.0,
                          "profit_factor": 1.5, "max_drawdown": 0.05},
            validation_status=verdict,
            reasons=["all_filters_passed"],
            score=score,
        )

    def test_empty_input_produces_safe_empty(self):
        cfg = build_paper_candidate_config([])
        d = cfg.to_dict()
        assert d["candidate_count"] == 0
        assert d["is_order_signal"]       is False
        assert d["auto_apply_allowed"]    is False
        assert d["is_live_authorization"] is False
        assert d["reasons_no_candidate"]

    def test_no_candidate_when_all_negative(self):
        items = [
            self._mk(verdict=OptimizationVerdict.LOW_QUALITY),
            self._mk(verdict=OptimizationVerdict.INSUFFICIENT_DATA),
            self._mk(verdict=OptimizationVerdict.NEGATIVE_EXPECTANCY),
            self._mk(verdict=OptimizationVerdict.HIGH_DRAWDOWN),
        ]
        cfg = build_paper_candidate_config(items)
        assert cfg.candidate_count == 0
        # 사유 집계.
        joined = " ".join(cfg.reasons_no_candidate)
        assert "LOW_QUALITY" in joined or "HIGH_DRAWDOWN" in joined

    def test_single_candidate(self):
        items = [
            self._mk(verdict=OptimizationVerdict.LOW_QUALITY, score=0.5),
            self._mk(verdict=OptimizationVerdict.PAPER_CANDIDATE, score=0.2,
                     strategy="rsi_reversion"),
        ]
        cfg = build_paper_candidate_config(items)
        assert cfg.candidate_count == 1
        assert cfg.candidates[0].strategy == "rsi_reversion"

    def test_top_k_caps_candidates(self):
        items = [
            self._mk(verdict=OptimizationVerdict.PAPER_CANDIDATE, score=0.9, strategy="a"),
            self._mk(verdict=OptimizationVerdict.PAPER_CANDIDATE, score=0.5, strategy="b"),
            self._mk(verdict=OptimizationVerdict.PAPER_CANDIDATE, score=0.7, strategy="c"),
            self._mk(verdict=OptimizationVerdict.PAPER_CANDIDATE, score=0.3, strategy="d"),
        ]
        cfg = build_paper_candidate_config(items, top_k=2)
        assert cfg.candidate_count == 2
        scores = [c.score for c in cfg.candidates]
        assert scores == sorted(scores, reverse=True)
        assert cfg.candidates[0].strategy == "a"
        assert cfg.candidates[1].strategy == "c"

    def test_each_candidate_has_safety_invariants(self):
        items = [self._mk(score=0.2)]
        cfg = build_paper_candidate_config(items)
        d = cfg.to_dict()
        for c in d["candidates"]:
            assert c["is_order_signal"]    is False
            assert c["auto_apply_allowed"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. JSON write / read round-trip
# ─────────────────────────────────────────────────────────────────────────────

class TestJsonRoundTrip:
    def test_write_and_read_back(self, tmp_path):
        items = [
            CandidateInput(
                strategy="sma_crossover", symbol="005930",
                params={"short": 5, "long": 20},
                risk_metrics={"trade_count": 30, "expectancy": 500.0,
                              "profit_factor": 1.8, "max_drawdown": 0.05},
                validation_status=OptimizationVerdict.PAPER_CANDIDATE,
                reasons=["all_filters_passed"],
                score=500.0,
            ),
        ]
        cfg = build_paper_candidate_config(items, top_k=1)
        out = tmp_path / "paper_candidate_config.json"
        write_paper_candidate_config(cfg, out)
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
# 4. invariants — JSON 에 BUY/SELL/HOLD/Place Order 단어 0건
# ─────────────────────────────────────────────────────────────────────────────

class TestNoForbiddenLabels:
    def test_paper_candidate_json_has_no_order_labels(self, tmp_path):
        items = [
            CandidateInput(
                strategy="sma_crossover", symbol="005930",
                params={"short": 5, "long": 20},
                risk_metrics={"trade_count": 30, "expectancy": 500.0,
                              "profit_factor": 1.8, "max_drawdown": 0.05},
                validation_status=OptimizationVerdict.PAPER_CANDIDATE,
                reasons=["all_filters_passed"],
                score=500.0,
            ),
        ]
        cfg = build_paper_candidate_config(items, top_k=2)
        out = tmp_path / "paper_candidate_config.json"
        write_paper_candidate_config(cfg, out)
        text = out.read_text(encoding="utf-8")
        forbidden = ["Place Order", "지금 매수", "지금 매도", "실거래 시작",
                     "ENABLE_LIVE_TRADING"]
        for w in forbidden:
            assert w not in text, f"forbidden label in JSON: {w}"


# ─────────────────────────────────────────────────────────────────────────────
# 5. 정적 가드 — broker / OrderExecutor / route_order / KIS 주문 import 0건
# ─────────────────────────────────────────────────────────────────────────────

class TestNoForbiddenImports:
    def test_optimization_modules_have_no_broker_imports(self):
        pkg_dir = Path(__file__).resolve().parents[1] / "app" / "backtest" / "real_data"
        targets = [
            pkg_dir / "grid_search.py",
            pkg_dir / "optimization_verdicts.py",
            pkg_dir / "paper_candidate.py",
        ]
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
        ]
        for path in targets:
            src = path.read_text(encoding="utf-8")
            for pat in forbidden:
                assert not re.search(pat, src, re.MULTILINE), \
                    f"forbidden pattern {pat!r} in {path.name}"
