"""#4-04: Market Regime Agent + 장세별 전략 선택 필터 테스트.

invariant:
- `MarketRegimeReport` invariants: is_order_signal=False / auto_apply_allowed=False /
  is_live_authorization=False / auto_start_paper_trader=False.
- 7 장세 모두 enum 에 존재: TREND_UP / TREND_DOWN / SIDEWAYS / HIGH_VOLATILITY /
  LOW_LIQUIDITY / CHOPPY / UNKNOWN.
- BUY/SELL/HOLD 주문 방향 값 0개 (enum).
- 장세별 정책: blocked / watchlist / preferred 매핑 일관.
- `apply_regime_filter` 가 OVERFIT_RISK 를 *원복하지 않음* (4-03 우선).
- broker / OrderExecutor / route_order / 외부 HTTP / AI SDK import 0건 (정적 grep).
- secret / API key / 계좌번호 필드 0건.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.agents.base import AgentContext, AgentDecision, AgentOutput, AgentRole
from app.agents.market_regime_agent import (
    REGIME_SCHEMA_VERSION,
    REGIME_STRATEGY_POLICY,
    MarketRegime,
    MarketRegimeAgent,
    MarketRegimeReport,
    MarketStateInput,
    apply_regime_filter,
    classify_market_regime,
)
from app.agents.strategy_combination_recommender import (
    OverallRecommendation,
    build_combination_recommendation,
)
from app.agents.overfit_warning_agent import (
    apply_overfit_filter,
    build_overfit_warning_report,
)
from app.analytics.paper_candidate_aggregator import PipelineStage
from app.analytics.strategy_optimization_report import (
    OperatorReport,
    ReportStatus,
    StrategyEntry,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helper — synthetic StrategyEntry + OperatorReport + combination
# ─────────────────────────────────────────────────────────────────────────────


def _entry(
    *,
    strategy="sma_crossover",
    symbol="005930",
    params=None,
    status=ReportStatus.READY_FOR_PAPER,
    wf_verdict="HEALTHY",
    train_avg=720.0,
    val_avg=580.0,
    score=0.05,
    risk_signals=None,
):
    return StrategyEntry(
        strategy_id=strategy,
        display_name=f"{strategy} display",
        symbol=symbol,
        params=params or {},
        status=status,
        pipeline_stages=[
            PipelineStage(name="3-02", verdict="BACKTEST_PASS",
                          extra={"metrics": {"profit_factor": 1.6,
                                              "max_drawdown": 0.08,
                                              "expectancy": 500.0,
                                              "win_rate": 0.55,
                                              "trade_count": 45}}),
            PipelineStage(name="3-03", verdict="PAPER_CANDIDATE"),
            PipelineStage(name="3-04", verdict=wf_verdict,
                          extra={"fold_count": 5,
                                  "train_expectancy_avg": train_avg,
                                  "val_expectancy_avg": val_avg}),
            PipelineStage(name="3-05", verdict="PASS"),
        ],
        risk_metrics={"profit_factor": 1.6, "max_drawdown": 0.08,
                      "expectancy": 500.0, "win_rate": 0.55,
                      "trade_count": 45},
        risk_signals=risk_signals or [],
        exclusion_reasons=[],
        score=score,
    )


def _build_report(entries):
    paper = [e for e in entries if e.status == ReportStatus.READY_FOR_PAPER]
    excluded = [e for e in entries if e.status != ReportStatus.READY_FOR_PAPER]
    overall = ReportStatus.READY_FOR_PAPER if paper else ReportStatus.NO_CANDIDATE
    return OperatorReport(
        generated_at="2026-05-17T00:00:00+00:00",
        overall_status=overall,
        paper_ready_count=len(paper),
        excluded_count=len(excluded),
        entries=entries,
        paper_candidates=paper,
        excluded=excluded,
    )


def _all_six_strategies():
    """6 등록 전략으로 추천 빌드 — 각 전략별 분류 검증용."""
    return _build_report([
        _entry(strategy="sma_crossover",   symbol="005930", score=0.10),
        _entry(strategy="rsi_reversion",   symbol="000660", score=0.09),
        _entry(strategy="vwap_strategy",   symbol="035420", score=0.08),
        _entry(strategy="orb_vwap",        symbol="035720", score=0.07),
        _entry(strategy="volume_breakout", symbol="207940", score=0.06),
        _entry(strategy="pullback_rebreak", symbol="068270", score=0.05),
    ])


# ─────────────────────────────────────────────────────────────────────────────
# 1. Enum + policy lock
# ─────────────────────────────────────────────────────────────────────────────


class TestEnumAndPolicy:
    def test_seven_regimes_present(self):
        values = {r.value for r in MarketRegime}
        required = {"TREND_UP", "TREND_DOWN", "SIDEWAYS", "HIGH_VOLATILITY",
                    "LOW_LIQUIDITY", "CHOPPY", "UNKNOWN"}
        missing = required - values
        assert not missing, f"missing regimes: {missing}"
        # 정확히 7개 — 진화 시 명시 PR 필요.
        assert len(values) == 7

    def test_regime_enum_has_no_order_direction(self):
        values = {r.value for r in MarketRegime}
        forbidden = {"BUY", "SELL", "PLACE_ORDER", "EXECUTE",
                     "ENABLE_LIVE_TRADING"}
        assert not (values & forbidden)

    def test_policy_covers_all_regimes(self):
        for regime in MarketRegime:
            assert regime in REGIME_STRATEGY_POLICY
            policy = REGIME_STRATEGY_POLICY[regime]
            assert {"preferred", "watchlist", "blocked"} <= set(policy.keys())

    def test_policy_strategies_are_registered_only(self):
        """정책에 등록된 전략 ID 는 6개 등록 전략 중 하나여야 함."""
        registered = {"sma_crossover", "rsi_reversion", "vwap_strategy",
                      "orb_vwap", "volume_breakout", "pullback_rebreak"}
        for regime, policy in REGIME_STRATEGY_POLICY.items():
            for category in ("preferred", "watchlist", "blocked"):
                for s in policy[category]:
                    assert s in registered, \
                        f"unregistered strategy {s} in {regime}/{category}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Classifier — input → regime
# ─────────────────────────────────────────────────────────────────────────────


class TestClassifier:
    def test_low_liquidity_classification(self):
        report = classify_market_regime(MarketStateInput(liquidity_score=0.15))
        assert report.regime == MarketRegime.LOW_LIQUIDITY
        assert any("liquidity" in r for r in report.reasons)

    def test_high_volatility_classification(self):
        report = classify_market_regime(MarketStateInput(volatility_pct=0.06))
        assert report.regime == MarketRegime.HIGH_VOLATILITY
        assert any("volatility" in r for r in report.reasons)

    def test_choppy_classification(self):
        report = classify_market_regime(MarketStateInput(choppiness_index=0.75))
        assert report.regime == MarketRegime.CHOPPY

    def test_trend_up_classification(self):
        report = classify_market_regime(MarketStateInput(
            trend_direction="UP", volatility_pct=0.02, liquidity_score=0.7,
        ))
        assert report.regime == MarketRegime.TREND_UP

    def test_trend_down_classification(self):
        report = classify_market_regime(MarketStateInput(
            trend_direction="DOWN", volatility_pct=0.02, liquidity_score=0.7,
        ))
        assert report.regime == MarketRegime.TREND_DOWN

    def test_sideways_classification(self):
        report = classify_market_regime(MarketStateInput(
            trend_direction="SIDEWAYS", volatility_pct=0.02, liquidity_score=0.7,
        ))
        assert report.regime == MarketRegime.SIDEWAYS

    def test_unknown_classification_when_no_data(self):
        report = classify_market_regime()
        assert report.regime == MarketRegime.UNKNOWN
        assert "watch_only" in " ".join(report.risk_flags).lower() or \
               "unknown" in " ".join(report.risk_flags).lower()

    def test_classification_priority_low_liquidity_over_volatility(self):
        """LOW_LIQUIDITY 가 HIGH_VOLATILITY 보다 우선 — 가장 보수적."""
        report = classify_market_regime(MarketStateInput(
            liquidity_score=0.15, volatility_pct=0.06,
        ))
        assert report.regime == MarketRegime.LOW_LIQUIDITY

    def test_report_carries_allowed_blocked_watchlist(self):
        report = classify_market_regime(MarketStateInput(trend_direction="UP"))
        # TREND_UP 의 정책과 일치.
        policy = REGIME_STRATEGY_POLICY[MarketRegime.TREND_UP]
        assert set(report.allowed_strategies)    == policy["preferred"]
        assert set(report.blocked_strategies)    == policy["blocked"]
        assert set(report.watchlist_strategies)  == policy["watchlist"]


# ─────────────────────────────────────────────────────────────────────────────
# 3. apply_regime_filter — 4-02 위에 장세 필터
# ─────────────────────────────────────────────────────────────────────────────


class TestApplyRegimeFilter:
    def test_trend_up_keeps_momentum_breakout(self):
        report = _build_report([
            _entry(strategy="sma_crossover",   symbol="005930", score=0.10),
            _entry(strategy="volume_breakout", symbol="000660", score=0.09),
        ])
        combo = build_combination_recommendation(operator_report=report)
        regime = classify_market_regime(MarketStateInput(trend_direction="UP"))
        filtered = apply_regime_filter(combo, regime)
        rec_strats = {d.strategy for d in filtered.recommended_combo}
        # TREND_UP 의 preferred — 차단 0건.
        assert "sma_crossover" in rec_strats
        assert "volume_breakout" in rec_strats
        # regime_context carry.
        assert filtered.regime_context is not None
        assert filtered.regime_context["market_regime"] == "TREND_UP"

    def test_sideways_blocks_breakout_strategies(self):
        report = _build_report([
            _entry(strategy="rsi_reversion",   symbol="005930", score=0.10),
            _entry(strategy="volume_breakout", symbol="000660", score=0.09),
            _entry(strategy="orb_vwap",        symbol="035420", score=0.08),
        ])
        combo = build_combination_recommendation(
            operator_report=report, max_combo_size=5,
        )
        regime = classify_market_regime(MarketStateInput(trend_direction="SIDEWAYS"))
        filtered = apply_regime_filter(combo, regime)
        rec_strats = {d.strategy for d in filtered.recommended_combo}
        excluded_strats = {d.strategy for d in filtered.excluded}
        # SIDEWAYS — rsi_reversion preferred, breakout blocked.
        assert "rsi_reversion" in rec_strats
        assert "volume_breakout" in excluded_strats
        assert "orb_vwap" in excluded_strats

    def test_high_volatility_carries_warning(self):
        report = _build_report([
            _entry(strategy="orb_vwap", symbol="005930", score=0.10),
            _entry(strategy="rsi_reversion", symbol="000660", score=0.09),
        ])
        combo = build_combination_recommendation(operator_report=report)
        regime = classify_market_regime(MarketStateInput(volatility_pct=0.06))
        filtered = apply_regime_filter(combo, regime)
        # 변동성 경고 carry.
        joined = " ".join(filtered.operator_notes)
        assert "변동성" in joined or "HIGH_VOLATILITY" in joined
        # orb_vwap 차단 — stop-loss 민감.
        excluded_strats = {d.strategy for d in filtered.excluded}
        assert "orb_vwap" in excluded_strats

    def test_low_liquidity_blocks_most_strategies(self):
        report = _all_six_strategies()
        combo = build_combination_recommendation(
            operator_report=report, max_combo_size=6,
        )
        regime = classify_market_regime(MarketStateInput(liquidity_score=0.15))
        filtered = apply_regime_filter(combo, regime)
        # 대부분 추천 차단 또는 watchlist.
        rec_strats = {d.strategy for d in filtered.recommended_combo}
        # LOW_LIQUIDITY 의 preferred 0개 → recommended 0건 또는 매우 적음.
        assert len(rec_strats) == 0
        # 슬리피지 경고 carry.
        joined = " ".join(filtered.operator_notes)
        assert "슬리피지" in joined or "LOW_LIQUIDITY" in joined or "거래대금" in joined

    def test_unknown_demotes_all_to_held(self):
        report = _build_report([
            _entry(strategy="sma_crossover", symbol="005930", score=0.10),
            _entry(strategy="rsi_reversion", symbol="000660", score=0.09),
        ])
        combo = build_combination_recommendation(operator_report=report)
        regime = classify_market_regime()   # 입력 없음 → UNKNOWN.
        filtered = apply_regime_filter(combo, regime)
        # UNKNOWN 이면 모두 watchlist (held).
        assert filtered.recommended_count == 0
        assert filtered.held_count >= 2
        assert filtered.overall_recommendation == OverallRecommendation.ALL_HOLD
        joined = " ".join(filtered.operator_notes)
        assert "UNKNOWN" in joined or "Paper 자동 시작 금지" in joined

    def test_choppy_blocks_trend_following(self):
        report = _build_report([
            _entry(strategy="sma_crossover",   symbol="005930", score=0.10),
            _entry(strategy="rsi_reversion",   symbol="000660", score=0.09),
            _entry(strategy="pullback_rebreak", symbol="035420", score=0.08),
        ])
        combo = build_combination_recommendation(
            operator_report=report, max_combo_size=5,
        )
        regime = classify_market_regime(MarketStateInput(choppiness_index=0.75))
        filtered = apply_regime_filter(combo, regime)
        excluded_strats = {d.strategy for d in filtered.excluded}
        # CHOPPY — sma_crossover / pullback_rebreak blocked.
        assert "sma_crossover" in excluded_strats
        assert "pullback_rebreak" in excluded_strats

    def test_trend_down_marks_rsi_as_watchlist(self):
        report = _build_report([
            _entry(strategy="rsi_reversion", symbol="005930", score=0.10),
        ])
        combo = build_combination_recommendation(operator_report=report)
        regime = classify_market_regime(MarketStateInput(trend_direction="DOWN"))
        filtered = apply_regime_filter(combo, regime)
        # rsi_reversion 은 TREND_DOWN watchlist → HOLD.
        held_strats = {d.strategy for d in filtered.held}
        assert "rsi_reversion" in held_strats
        # recommended 0건.
        assert filtered.recommended_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. 과최적화 우선순위 — overfit 차단이 regime 보다 우선
# ─────────────────────────────────────────────────────────────────────────────


class TestOverfitPriorityOverRegime:
    def test_overfit_overrides_trend_up_recommendation(self):
        """TREND_UP 의 preferred 전략이라도 OVERFIT_RISK 면 추천 제외 유지."""
        report = _build_report([
            _entry(strategy="sma_crossover", symbol="005930",
                   wf_verdict="OVERFIT_RISK", train_avg=800.0, val_avg=100.0,
                   score=0.10),
        ])
        combo = build_combination_recommendation(operator_report=report)
        # 1단계: 4-03 overfit 필터.
        warnings = build_overfit_warning_report(operator_report=report)
        after_overfit = apply_overfit_filter(combo, warnings)
        # 2단계: 4-04 regime 필터 (TREND_UP — sma_crossover 가 preferred).
        regime = classify_market_regime(MarketStateInput(trend_direction="UP"))
        after_regime = apply_regime_filter(after_overfit, regime)
        # 결과: sma_crossover 가 OVERFIT_RISK 라 *추천 X*.
        rec_strats = {d.strategy for d in after_regime.recommended_combo}
        assert "sma_crossover" not in rec_strats
        # excluded 에 남아있어야 — overfit 차단이 *원복되지 않음*.
        excluded_strats = {d.strategy for d in after_regime.excluded}
        assert "sma_crossover" in excluded_strats


# ─────────────────────────────────────────────────────────────────────────────
# 5. Invariants — Report + filter result
# ─────────────────────────────────────────────────────────────────────────────


class TestInvariants:
    def test_regime_report_invariants(self):
        for kwargs in (
            {"is_order_signal": True},
            {"auto_apply_allowed": True},
            {"is_live_authorization": True},
            {"auto_start_paper_trader": True},
        ):
            with pytest.raises(ValueError):
                MarketRegimeReport(
                    generated_at="x", schema_version="1.0",
                    regime=MarketRegime.UNKNOWN, confidence=0.5,
                    **kwargs,
                )

    def test_confidence_range_check(self):
        with pytest.raises(ValueError):
            MarketRegimeReport(
                generated_at="x", schema_version="1.0",
                regime=MarketRegime.UNKNOWN, confidence=1.5,
            )
        with pytest.raises(ValueError):
            MarketRegimeReport(
                generated_at="x", schema_version="1.0",
                regime=MarketRegime.UNKNOWN, confidence=-0.1,
            )

    def test_disclaimer_cannot_be_empty(self):
        with pytest.raises(ValueError):
            MarketRegimeReport(
                generated_at="x", schema_version="1.0",
                regime=MarketRegime.UNKNOWN, confidence=0.5,
                advisory_disclaimer="",
            )

    def test_to_dict_carries_invariants(self):
        report = classify_market_regime()
        d = report.to_dict()
        assert d["is_order_signal"]          is False
        assert d["auto_apply_allowed"]       is False
        assert d["is_live_authorization"]    is False
        assert d["auto_start_paper_trader"]  is False

    def test_filtered_recommendation_carries_invariants(self):
        report = _build_report([_entry()])
        combo = build_combination_recommendation(operator_report=report)
        regime = classify_market_regime(MarketStateInput(trend_direction="UP"))
        filtered = apply_regime_filter(combo, regime)
        d = filtered.to_dict()
        assert d["is_order_signal"]          is False
        assert d["auto_apply_allowed"]       is False
        assert d["is_live_authorization"]    is False
        assert d["auto_start_paper_trader"]  is False
        assert d["regime_context"] is not None
        assert d["regime_context"]["market_regime"] == "TREND_UP"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Agent (AgentBase 호환)
# ─────────────────────────────────────────────────────────────────────────────


class TestAgent:
    def test_agent_metadata_shape(self):
        agent = MarketRegimeAgent()
        meta = agent.metadata
        assert meta.name == "market_regime_agent"
        assert meta.role == AgentRole.OBSERVER
        assert meta.can_execute_order is False

    def test_agent_run_with_no_state_returns_unknown(self):
        agent = MarketRegimeAgent()
        out = agent.run(AgentContext(extra={}))
        assert isinstance(out, AgentOutput)
        assert out.decision == AgentDecision.OBSERVE
        report = out.metadata["regime_report"]
        assert report["regime"] == "UNKNOWN"

    def test_agent_run_with_state_classifies(self):
        agent = MarketRegimeAgent()
        out = agent.run(AgentContext(extra={
            "market_state": MarketStateInput(trend_direction="UP"),
        }))
        report = out.metadata["regime_report"]
        assert report["regime"] == "TREND_UP"

    def test_agent_run_with_recommendation_applies_filter(self):
        report = _build_report([
            _entry(strategy="volume_breakout", symbol="005930", score=0.10),
        ])
        combo = build_combination_recommendation(operator_report=report)
        agent = MarketRegimeAgent()
        out = agent.run(AgentContext(extra={
            "market_state": MarketStateInput(trend_direction="SIDEWAYS"),
            "recommendation": combo,
        }))
        filtered = out.metadata["filtered_recommendation"]
        assert filtered is not None
        # SIDEWAYS — volume_breakout blocked.
        excluded_strats = {d["strategy"] for d in filtered["excluded"]}
        assert "volume_breakout" in excluded_strats

    def test_agent_output_invariants(self):
        agent = MarketRegimeAgent()
        out = agent.run(AgentContext(extra={
            "market_state": MarketStateInput(trend_direction="UP"),
        }))
        d = out.to_dict()
        assert d["is_order_intent"]   is False
        assert d["can_execute_order"] is False
        meta = d["metadata"]
        assert meta["advisory_only"]               is True
        assert meta["is_order_signal"]             is False
        assert meta["auto_apply_allowed"]          is False
        assert meta["is_live_authorization"]       is False
        assert meta["auto_start_paper_trader"]     is False

    def test_agent_does_not_emit_buy_sell_hold_signals(self):
        agent = MarketRegimeAgent()
        out = agent.run(AgentContext(extra={
            "market_state": MarketStateInput(trend_direction="UP"),
        }))
        text = json.dumps(out.to_dict(), ensure_ascii=False)
        forbidden = [
            "\"regime\": \"BUY\"",
            "\"regime\": \"SELL\"",
            "Place Order",
            "지금 매수",
            "지금 매도",
            "실거래 시작",
            "ENABLE_LIVE_TRADING 토글",
            "AI 자동매매 켜기",
        ]
        for fp in forbidden:
            assert fp not in text, f"forbidden in agent output: {fp}"


# ─────────────────────────────────────────────────────────────────────────────
# 7. Static guards
# ─────────────────────────────────────────────────────────────────────────────


_MOD_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "agents"
    / "market_regime_agent.py"
)


class TestNoForbiddenImports:
    def test_no_broker_or_executor_imports(self):
        src = _MOD_PATH.read_text(encoding="utf-8")
        forbidden = [
            r"from\s+app\.brokers\.kis\b",
            r"from\s+app\.brokers\.mock_broker\b",
            r"from\s+app\.execution\.executor\b",
            r"from\s+app\.execution\.order_router\b",
            r"from\s+app\.ai\.assist\b",
            r"from\s+app\.ai\.client\b",
            r"from\s+app\.core\.config\s+import\s+get_settings",
            r"broker\.place_order\(",
            r"route_order\s*\(",
            r"OrderExecutor\s*\(",
            r"KisClient\b",
        ]
        for pat in forbidden:
            assert not re.search(pat, src, re.MULTILINE), \
                f"forbidden in market_regime_agent.py: {pat}"

    def test_no_external_http_or_ai_sdk_imports(self):
        src = _MOD_PATH.read_text(encoding="utf-8")
        forbidden = [
            r"^import\s+anthropic\b",
            r"^import\s+openai\b",
            r"^import\s+requests\b",
            r"^import\s+httpx\b",
            r"^import\s+yfinance\b",
            r"^from\s+anthropic\b",
            r"^from\s+openai\b",
            r"^from\s+httpx\b",
            r"^from\s+requests\b",
        ]
        for pat in forbidden:
            assert not re.search(pat, src, re.MULTILINE), \
                f"forbidden http/AI in market_regime_agent.py: {pat}"

    def test_no_safety_flag_mutation(self):
        src = _MOD_PATH.read_text(encoding="utf-8")
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


class TestNoSecretPatterns:
    def test_no_secret_patterns(self):
        src = _MOD_PATH.read_text(encoding="utf-8")
        patterns = [
            r"sk-[A-Za-z0-9]{20,}",
            r"sk-ant-[A-Za-z0-9_\-]{20,}",
            r"ghp_[A-Za-z0-9]{30,}",
            r"Bearer\s+[A-Za-z0-9._\-]{20,}",
            r"PST[A-Za-z0-9]{30,}",
        ]
        for pat in patterns:
            assert not re.search(pat, src), \
                f"secret pattern in module: {pat}"

    def test_schema_has_no_secret_fields(self):
        state_fields  = MarketStateInput.__dataclass_fields__.keys()
        report_fields = MarketRegimeReport.__dataclass_fields__.keys()
        secret_names = [
            "api_key", "secret", "app_key", "app_secret", "access_token",
            "account_no", "account_number", "anthropic_api_key",
            "openai_api_key", "kis_app_key", "kis_app_secret",
        ]
        for name in secret_names:
            assert name not in state_fields,  f"state has secret field: {name}"
            assert name not in report_fields, f"report has secret field: {name}"


# ─────────────────────────────────────────────────────────────────────────────
# 8. Schema field lock
# ─────────────────────────────────────────────────────────────────────────────


class TestSchemaFieldLock:
    def test_report_has_required_fields(self):
        required = {
            "generated_at", "schema_version", "regime", "confidence",
            "reasons", "risk_flags", "allowed_strategies",
            "blocked_strategies", "watchlist_strategies",
            "operator_note", "advisory_disclaimer", "metadata",
            "is_order_signal", "auto_apply_allowed",
            "is_live_authorization", "auto_start_paper_trader",
        }
        actual = set(MarketRegimeReport.__dataclass_fields__.keys())
        missing = required - actual
        assert not missing, f"missing report fields: {missing}"

    def test_schema_version_carried(self):
        report = classify_market_regime()
        assert report.schema_version == REGIME_SCHEMA_VERSION
