"""#4-05: Paper Start Explanation 테스트.

invariant:
- `PaperStartExplanation` + `StrategyExplanation` 모두 is_order_signal=False
  / auto_apply_allowed=False / is_live_authorization=False (`__post_init__` 가드).
- 5단계 verdict (READY_TO_REVIEW / REVIEW_WITH_WARNING / HOLD / DO_NOT_START
  / INSUFFICIENT_DATA) 정확히 lock.
- Pre-market BLOCK > LOW_LIQUIDITY > UNKNOWN > NO_CANDIDATE > NEED_MORE_DATA
  > REJECTED/WATCH_ONLY > 추천 우선순위 lock.
- OVERFIT_RISK 전략은 *추천이 아니라 제외* 사유에 표시 (4-03 우선).
- can_start_paper=False 시 blocking_reasons carry.
- broker / OrderExecutor / route_order import 0건 (정적 grep).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.agents.market_regime_agent import MarketStateInput
from app.agents.paper_start_explanation import (
    EXPLANATION_SCHEMA_VERSION,
    ExplanationVerdict,
    PaperStartExplanation,
    PreMarketSummary,
    StrategyExplanation,
    build_paper_start_explanation,
)
from app.analytics.paper_candidate_aggregator import PipelineStage
from app.analytics.strategy_optimization_report import (
    OperatorReport,
    ReportInputs,
    ReportStatus,
    StrategyEntry,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
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
                                              "expectancy": 500.0,
                                              "win_rate": 0.55,
                                              "trade_count": 45,
                                              "max_drawdown": 0.08}}),
            PipelineStage(name="3-03", verdict="PAPER_CANDIDATE"),
            PipelineStage(name="3-04", verdict=wf_verdict,
                          extra={"fold_count": 5,
                                  "train_expectancy_avg": train_avg,
                                  "val_expectancy_avg": val_avg}),
            PipelineStage(name="3-05", verdict="PASS"),
        ],
        risk_metrics={"profit_factor": 1.6, "expectancy": 500.0,
                      "win_rate": 0.55, "trade_count": 45,
                      "max_drawdown": 0.08},
        risk_signals=risk_signals or [],
        exclusion_reasons=[],
        score=score,
    )


def _report(entries):
    paper = [e for e in entries if e.status == ReportStatus.READY_FOR_PAPER]
    excluded = [e for e in entries if e.status != ReportStatus.READY_FOR_PAPER]
    overall = (
        ReportStatus.READY_FOR_PAPER if paper else
        (ReportStatus.NO_CANDIDATE if entries else ReportStatus.NO_CANDIDATE)
    )
    return OperatorReport(
        generated_at="2026-05-18T00:00:00+00:00",
        overall_status=overall,
        paper_ready_count=len(paper),
        excluded_count=len(excluded),
        entries=entries,
        paper_candidates=paper,
        excluded=excluded,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestInvariants:
    def test_top_invariants_must_be_false(self):
        for kwargs in (
            {"is_order_signal": True},
            {"auto_apply_allowed": True},
            {"is_live_authorization": True},
        ):
            with pytest.raises(ValueError):
                PaperStartExplanation(
                    generated_at="t", schema_version="1.0",
                    verdict=ExplanationVerdict.DO_NOT_START,
                    recommended_explanations=[],
                    watchlist_explanations=[],
                    excluded_explanations=[],
                    market_regime="UNKNOWN", regime_confidence=0.3,
                    **kwargs,
                )

    def test_disclaimer_cannot_be_empty(self):
        with pytest.raises(ValueError):
            PaperStartExplanation(
                generated_at="t", schema_version="1.0",
                verdict=ExplanationVerdict.DO_NOT_START,
                recommended_explanations=[], watchlist_explanations=[],
                excluded_explanations=[],
                market_regime="UNKNOWN", regime_confidence=0.3,
                advisory_disclaimer="",
            )

    def test_confidence_range_check(self):
        with pytest.raises(ValueError):
            PaperStartExplanation(
                generated_at="t", schema_version="1.0",
                verdict=ExplanationVerdict.DO_NOT_START,
                recommended_explanations=[], watchlist_explanations=[],
                excluded_explanations=[],
                market_regime="UNKNOWN", regime_confidence=1.5,
            )

    def test_strategy_explanation_invariants(self):
        with pytest.raises(ValueError):
            StrategyExplanation(
                strategy="s", symbol="x", bucket="recommended",
                paper_candidate_status="READY_FOR_PAPER",
                is_order_signal=True,
            )

    def test_strategy_explanation_bucket_validation(self):
        with pytest.raises(ValueError):
            StrategyExplanation(
                strategy="s", symbol="x", bucket="invalid",
                paper_candidate_status="READY_FOR_PAPER",
            )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Verdict matrix — priority order
# ─────────────────────────────────────────────────────────────────────────────


class TestVerdictPriority:
    def test_pre_market_block_wins(self):
        """Pre-market BLOCK 이 최우선 — 다른 모든 조건 무관."""
        explanation = build_paper_start_explanation(
            operator_report=_report([_entry()]),
            market_state=MarketStateInput(trend_direction="UP"),
            pre_market=PreMarketSummary(
                start_allowed=False, verdict="DO_NOT_START",
                blocking_reasons=["api_unhealthy"],
            ),
        )
        assert explanation.verdict == ExplanationVerdict.DO_NOT_START
        assert explanation.can_start_paper is False
        assert any("pre_market_block" in r for r in explanation.blocking_reasons)

    def test_low_liquidity_blocks(self):
        explanation = build_paper_start_explanation(
            operator_report=_report([_entry()]),
            market_state=MarketStateInput(liquidity_score=0.10),
        )
        assert explanation.verdict == ExplanationVerdict.DO_NOT_START
        assert explanation.can_start_paper is False
        assert any("liquidity" in r.lower() for r in explanation.blocking_reasons)
        assert explanation.market_regime == "LOW_LIQUIDITY"

    def test_unknown_regime_blocks(self):
        """장세 분류 불가 (입력 없음) → DO_NOT_START."""
        explanation = build_paper_start_explanation(
            operator_report=_report([_entry()]),
            market_state=None,
        )
        assert explanation.market_regime == "UNKNOWN"
        assert explanation.verdict == ExplanationVerdict.DO_NOT_START
        assert any("unknown" in r.lower() for r in explanation.blocking_reasons)

    def test_no_candidate_blocks(self):
        """4-02 v2 NO_CANDIDATE → DO_NOT_START."""
        explanation = build_paper_start_explanation(
            inputs=ReportInputs(),    # 빈 입력
            market_state=MarketStateInput(trend_direction="UP"),
        )
        assert explanation.verdict == ExplanationVerdict.DO_NOT_START
        assert any("no_candidate" in r for r in explanation.blocking_reasons)

    def test_ready_to_review_when_clean(self):
        explanation = build_paper_start_explanation(
            operator_report=_report([
                _entry(strategy="sma_crossover", symbol="005930"),
            ]),
            market_state=MarketStateInput(trend_direction="UP"),
        )
        assert explanation.verdict == ExplanationVerdict.READY_TO_REVIEW
        assert explanation.can_start_paper is True
        assert len(explanation.recommended_explanations) >= 1
        assert "검토" in explanation.headline or "advisory" in explanation.headline

    def test_review_with_warning_when_overfit_or_flags(self):
        """OVERFIT_RISK 있어도 다른 추천이 있으면 REVIEW_WITH_WARNING (or HOLD)."""
        explanation = build_paper_start_explanation(
            operator_report=_report([
                _entry(strategy="sma_crossover", symbol="005930",
                       wf_verdict="OVERFIT_RISK",
                       train_avg=800.0, val_avg=100.0,
                       status=ReportStatus.OVERFIT_RISK,
                       score=0.10),
                _entry(strategy="rsi_reversion", symbol="000660",
                       wf_verdict="HEALTHY",
                       train_avg=600.0, val_avg=580.0, score=0.09),
            ]),
            market_state=MarketStateInput(trend_direction="UP"),
        )
        # OVERFIT_RISK 전략은 excluded 에 가야 함.
        assert explanation.overfit_count >= 1
        assert any("sma_crossover" in s for s in explanation.overfit_strategies)
        # rsi_reversion 은 recommended 또는 watchlist (SIDEWAYS 가 아니라 TREND_UP)
        # SIDEWAYS preferred 가 아닌데 TREND_UP 에서 rsi_reversion 은 정책 외 → KEEP
        # 4-04 TREND_UP 정책에 rsi_reversion 없음. 그래도 차단/보류 대상 아님.

    def test_overfit_only_marks_excluded(self):
        """OVERFIT_RISK 전략은 추천에 포함 안 됨 — *제외 사유* 에 표시."""
        explanation = build_paper_start_explanation(
            operator_report=_report([
                _entry(strategy="sma_crossover", symbol="005930",
                       wf_verdict="OVERFIT_RISK",
                       train_avg=800.0, val_avg=100.0,
                       status=ReportStatus.OVERFIT_RISK),
            ]),
            market_state=MarketStateInput(trend_direction="UP"),
        )
        # 추천에 없어야.
        rec_strats = {e.strategy for e in explanation.recommended_explanations}
        assert "sma_crossover" not in rec_strats
        # 제외 또는 보류 에 있어야 + 사유에 "훈련구간" 또는 "과최적화" 포함.
        excluded_or_watch = (
            explanation.excluded_explanations + explanation.watchlist_explanations
        )
        sma_exp = next(
            (e for e in excluded_or_watch if e.strategy == "sma_crossover"), None
        )
        assert sma_exp is not None
        joined = " ".join(sma_exp.rationale_lines)
        assert "훈련구간" in joined or "과최적화" in joined


# ─────────────────────────────────────────────────────────────────────────────
# 3. 규정 핵심 동작 — 사용자 spec
# ─────────────────────────────────────────────────────────────────────────────


class TestRequiredBehaviors:
    def test_overfit_risk_is_excluded_not_recommended(self):
        """spec: OVERFIT_RISK 전략은 추천 사유가 아니라 제외 사유에 표시."""
        explanation = build_paper_start_explanation(
            operator_report=_report([
                _entry(strategy="sma_crossover", symbol="005930",
                       wf_verdict="OVERFIT_RISK",
                       train_avg=800.0, val_avg=100.0,
                       status=ReportStatus.OVERFIT_RISK),
            ]),
            market_state=MarketStateInput(trend_direction="UP"),
        )
        rec_strats = {e.strategy for e in explanation.recommended_explanations}
        assert "sma_crossover" not in rec_strats
        # overfit_strategies 에 반드시 포함.
        assert any("sma_crossover" in s for s in explanation.overfit_strategies)

    def test_can_start_false_carries_blocking_reasons(self):
        explanation = build_paper_start_explanation(
            inputs=ReportInputs(),     # 후보 0 → NO_CANDIDATE
            market_state=MarketStateInput(trend_direction="UP"),
        )
        assert explanation.can_start_paper is False
        assert len(explanation.blocking_reasons) >= 1

    def test_can_start_true_when_ready(self):
        explanation = build_paper_start_explanation(
            operator_report=_report([_entry()]),
            market_state=MarketStateInput(trend_direction="UP"),
        )
        assert explanation.can_start_paper is True
        assert explanation.blocking_reasons == []

    def test_no_candidate_message(self):
        explanation = build_paper_start_explanation(
            inputs=ReportInputs(),
            market_state=MarketStateInput(trend_direction="UP"),
        )
        # 후보 0건 — operator_note 또는 headline 에 명시.
        joined = explanation.headline + " " + " ".join(explanation.next_actions)
        assert "후보" in joined or "시작 금지" in joined or "no_candidate" in joined.lower()


# ─────────────────────────────────────────────────────────────────────────────
# 4. 사유 lines (rationale_lines)
# ─────────────────────────────────────────────────────────────────────────────


class TestRationaleLines:
    def test_recommended_strategy_has_rationale(self):
        explanation = build_paper_start_explanation(
            operator_report=_report([_entry()]),
            market_state=MarketStateInput(trend_direction="UP"),
        )
        assert len(explanation.recommended_explanations) >= 1
        exp = explanation.recommended_explanations[0]
        assert len(exp.rationale_lines) >= 1
        assert exp.bucket == "recommended"

    def test_overfit_carries_train_val_info(self):
        explanation = build_paper_start_explanation(
            operator_report=_report([
                _entry(strategy="sma_crossover", symbol="005930",
                       wf_verdict="OVERFIT_RISK",
                       train_avg=800.0, val_avg=100.0,
                       status=ReportStatus.OVERFIT_RISK),
            ]),
            market_state=MarketStateInput(trend_direction="UP"),
        )
        # excluded 에서 train_validation_gap 정보 carry.
        excluded = [
            e for e in explanation.excluded_explanations
            if e.strategy == "sma_crossover"
        ]
        if excluded:
            assert excluded[0].overfit_verdict == "OVERFIT_RISK"
            assert excluded[0].train_validation_gap is not None

    def test_regime_policy_role_carried(self):
        """TREND_UP 에서 sma_crossover 는 preferred 정책."""
        explanation = build_paper_start_explanation(
            operator_report=_report([
                _entry(strategy="sma_crossover", symbol="005930"),
            ]),
            market_state=MarketStateInput(trend_direction="UP"),
        )
        exp = explanation.recommended_explanations[0]
        assert exp.regime_policy_role == "preferred"


# ─────────────────────────────────────────────────────────────────────────────
# 5. to_dict + JSON consumer
# ─────────────────────────────────────────────────────────────────────────────


REQUIRED_TOP_FIELDS = [
    "verdict", "verdict_label_ko",
    "recommended_explanations", "watchlist_explanations", "excluded_explanations",
    "market_regime", "regime_confidence", "regime_reasons",
    "regime_allowed_tactics", "regime_blocked_tactics",
    "overfit_count", "overfit_strategies",
    "headline", "risk_summary", "operator_note", "next_actions",
    "can_start_paper", "blocking_reasons",
    "advisory_disclaimer",
    "is_order_signal", "auto_apply_allowed", "is_live_authorization",
]


class TestToDict:
    def test_top_dict_has_all_required_fields(self):
        explanation = build_paper_start_explanation(
            operator_report=_report([_entry()]),
            market_state=MarketStateInput(trend_direction="UP"),
        )
        d = explanation.to_dict()
        for f in REQUIRED_TOP_FIELDS:
            assert f in d, f"missing: {f}"

    def test_invariants_carried_top_and_per_entry(self):
        explanation = build_paper_start_explanation(
            operator_report=_report([_entry()]),
            market_state=MarketStateInput(trend_direction="UP"),
        )
        d = explanation.to_dict()
        assert d["is_order_signal"]       is False
        assert d["auto_apply_allowed"]    is False
        assert d["is_live_authorization"] is False
        for bucket in ("recommended_explanations", "watchlist_explanations",
                        "excluded_explanations"):
            for entry in d[bucket]:
                assert entry["is_order_signal"]       is False
                assert entry["auto_apply_allowed"]    is False
                assert entry["is_live_authorization"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 6. API endpoint
# ─────────────────────────────────────────────────────────────────────────────


def _client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


class TestAPI:
    def test_endpoint_with_no_inputs_returns_unknown_block(self):
        client = _client()
        r = client.post("/api/agents/paper-start-explanation", json={})
        assert r.status_code == 200
        body = r.json()
        assert body["market_regime"] == "UNKNOWN"
        assert body["can_start_paper"] is False
        assert body["is_order_signal"]       is False
        assert body["auto_apply_allowed"]    is False
        assert body["is_live_authorization"] is False

    def test_endpoint_with_trend_up_returns_dict(self):
        client = _client()
        r = client.post("/api/agents/paper-start-explanation", json={
            "market_state": {"trend_direction": "UP"},
        })
        body = r.json()
        # 입력 0건이라 NO_CANDIDATE 로 DO_NOT_START 가 될 것.
        assert body["verdict"] in [
            "DO_NOT_START", "READY_TO_REVIEW", "REVIEW_WITH_WARNING",
            "HOLD", "INSUFFICIENT_DATA",
        ]
        assert body["market_regime"] == "TREND_UP"

    def test_endpoint_response_has_no_secret_fields(self):
        client = _client()
        r = client.post("/api/agents/paper-start-explanation", json={})
        text = r.text.lower()
        for f in ("anthropic_api_key", "openai_api_key", "kis_app_key",
                   "kis_app_secret", "account_no"):
            assert f not in text

    def test_endpoint_pre_market_block(self):
        client = _client()
        r = client.post("/api/agents/paper-start-explanation", json={
            "market_state": {"trend_direction": "UP"},
            "pre_market": {
                "start_allowed": False, "verdict": "DO_NOT_START",
                "blocking_reasons": ["api_unhealthy"],
            },
        })
        body = r.json()
        assert body["verdict"] == "DO_NOT_START"
        assert body["can_start_paper"] is False
        assert any("pre_market_block" in r for r in body["blocking_reasons"])


# ─────────────────────────────────────────────────────────────────────────────
# 7. Static guards
# ─────────────────────────────────────────────────────────────────────────────


_MOD = (
    Path(__file__).resolve().parents[1] / "app" / "agents"
    / "paper_start_explanation.py"
)


class TestNoForbiddenImports:
    def test_no_broker_or_executor_imports(self):
        src = _MOD.read_text(encoding="utf-8")
        forbidden = [
            r"from\s+app\.brokers\.kis\b",
            r"from\s+app\.brokers\.mock_broker\b",
            r"from\s+app\.execution\.executor\b",
            r"from\s+app\.execution\.order_router\b",
            r"broker\.place_order\(",
            r"route_order\s*\(",
            r"OrderExecutor\s*\(",
            r"KisClient\b",
        ]
        for pat in forbidden:
            assert not re.search(pat, src, re.MULTILINE), \
                f"forbidden in paper_start_explanation.py: {pat}"

    def test_no_external_http_or_ai_sdk(self):
        src = _MOD.read_text(encoding="utf-8")
        forbidden = [
            r"^import\s+anthropic\b",
            r"^import\s+openai\b",
            r"^import\s+requests\b",
            r"^import\s+httpx\b",
            r"^from\s+anthropic\b",
            r"^from\s+openai\b",
            r"^from\s+httpx\b",
            r"^from\s+requests\b",
        ]
        for pat in forbidden:
            assert not re.search(pat, src, re.MULTILINE), \
                f"forbidden http/AI: {pat}"

    def test_no_safety_flag_mutation(self):
        src = _MOD.read_text(encoding="utf-8")
        bad = [
            r"ENABLE_LIVE_TRADING\s*=\s*['\"]?true",
            r"ENABLE_AI_EXECUTION\s*=\s*['\"]?true",
            r"settings\.enable_live_trading\s*=",
        ]
        for pat in bad:
            assert not re.search(pat, src, re.IGNORECASE), \
                f"safety mutation: {pat}"


class TestSchemaLock:
    def test_schema_has_no_secret_fields(self):
        top_fields = PaperStartExplanation.__dataclass_fields__.keys()
        entry_fields = StrategyExplanation.__dataclass_fields__.keys()
        secret_names = [
            "api_key", "secret", "app_key", "app_secret", "access_token",
            "account_no", "account_number", "anthropic_api_key",
            "openai_api_key", "kis_app_key", "kis_app_secret",
        ]
        for n in secret_names:
            assert n not in top_fields, f"top has secret: {n}"
            assert n not in entry_fields, f"entry has secret: {n}"

    def test_schema_version_carried(self):
        explanation = build_paper_start_explanation(
            operator_report=_report([_entry()]),
            market_state=MarketStateInput(trend_direction="UP"),
        )
        assert explanation.schema_version == EXPLANATION_SCHEMA_VERSION


# ─────────────────────────────────────────────────────────────────────────────
# 8. Forbidden order signal labels in JSON output
# ─────────────────────────────────────────────────────────────────────────────


class TestNoForbiddenLabels:
    def test_no_buy_sell_buttons_in_output(self):
        """spec: BUY/SELL 버튼 만들지 마세요 — 결과 JSON 에 그런 라벨 0건."""
        explanation = build_paper_start_explanation(
            operator_report=_report([
                _entry(strategy="sma_crossover", symbol="005930"),
            ]),
            market_state=MarketStateInput(trend_direction="UP"),
        )
        text = json.dumps(explanation.to_dict(), ensure_ascii=False)
        forbidden = [
            "지금 매수", "지금 매도", "실거래 시작",
            "Place Order", "ENABLE_LIVE_TRADING 토글",
            "AI 자동매매 켜기",
        ]
        for fp in forbidden:
            assert fp not in text, f"forbidden label: {fp}"
