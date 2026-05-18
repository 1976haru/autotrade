"""#4-07: AI Paper 매수/매도 판단 연결 테스트.

invariant:
- `BridgeReport.is_order_signal=False` / `auto_apply_allowed=False` /
  `is_live_authorization=False` (`__post_init__` ValueError 가드).
- broker / OrderExecutor / route_order import 0건 (정적 grep).
- READY_FOR_PAPER + pos=0 → BUY, pos>0 → HOLD (중복 매수 차단).
- 보유 + watchlist + exit_condition → EXIT.
- OVERFIT_RISK / STRESS_FAILED / LOW_LIQUIDITY / UNKNOWN regime — 4-05 에서
  이미 차단 → bridge 가 BUY 생성 *불가능*.
- RUNNING 외 state → trade action 차단, HOLD/NO_OP audit 만 기록.
- EMERGENCY_STOP → 모든 action 차단 (ledger 손대지 않음).
- ledger 기록 — RUNNING + verdict 허용 시 trade event.
- secret / API key / 계좌번호 carry 0건.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.agents.market_regime_agent import MarketStateInput
from app.agents.paper_decision_bridge import (
    BRIDGE_SCHEMA_VERSION,
    BridgeReport,
    PositionSnapshot,
    bridge_explanation_to_paper_decisions,
)
from app.agents.paper_start_explanation import (
    ExplanationVerdict,
    PaperStartExplanation,
    PreMarketSummary,
    StrategyExplanation,
    build_paper_start_explanation,
)
from app.analytics.paper_candidate_aggregator import PipelineStage
from app.analytics.strategy_optimization_report import (
    OperatorReport,
    ReportStatus,
    StrategyEntry,
)
from app.auto_paper.events import DecisionAction, PaperFillStatus
from app.auto_paper.ledger import (
    get_ledger,
    reset_ledger_for_tests,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_ledger():
    reset_ledger_for_tests()
    yield
    reset_ledger_for_tests()


def _entry(
    *, strategy="sma_crossover", symbol="005930",
    status=ReportStatus.READY_FOR_PAPER, wf_verdict="HEALTHY",
    train_avg=720.0, val_avg=580.0, score=0.05,
):
    return StrategyEntry(
        strategy_id=strategy,
        display_name=f"{strategy} display",
        symbol=symbol,
        params={},
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
        risk_signals=[],
        exclusion_reasons=[],
        score=score,
    )


def _operator_report(entries):
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


def _build_explanation(*, entries=None, regime_trend="UP", pre_market=None):
    if entries is None:
        entries = [_entry()]
    return build_paper_start_explanation(
        operator_report=_operator_report(entries),
        market_state=MarketStateInput(trend_direction=regime_trend),
        pre_market=pre_market,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. BridgeReport invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestBridgeReportInvariants:
    def test_invariants_must_be_false(self):
        for kwargs in (
            {"is_order_signal": True},
            {"auto_apply_allowed": True},
            {"is_live_authorization": True},
        ):
            with pytest.raises(ValueError):
                BridgeReport(
                    generated_at="t", schema_version="1.0",
                    loop_state="RUNNING", explanation_verdict="DO_NOT_START",
                    **kwargs,
                )

    def test_disclaimer_cannot_be_empty(self):
        with pytest.raises(ValueError):
            BridgeReport(
                generated_at="t", schema_version="1.0",
                loop_state="RUNNING", explanation_verdict="DO_NOT_START",
                advisory_disclaimer="",
            )

    def test_to_dict_carries_invariants(self):
        exp = _build_explanation()
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
        )
        d = report.to_dict()
        assert d["is_order_signal"]       is False
        assert d["auto_apply_allowed"]    is False
        assert d["is_live_authorization"] is False
        for dec_d in d["decisions"]:
            assert dec_d["is_order_signal"]       is False
            assert dec_d["auto_apply_allowed"]    is False
            assert dec_d["is_live_authorization"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 2. READY_FOR_PAPER → BUY 변환 (pos=0)
# ─────────────────────────────────────────────────────────────────────────────


class TestReadyForPaperBuy:
    def test_recommended_zero_position_creates_buy(self):
        """spec: READY_FOR_PAPER → BUY PaperDecision."""
        exp = _build_explanation()
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
        )
        # 추천 entry 1개 → BUY decision 1개.
        buys = [d for d in report.decisions if d.action == DecisionAction.BUY]
        assert len(buys) >= 1
        assert buys[0].strategy == "sma_crossover"
        assert buys[0].paper_fill_status == PaperFillStatus.PAPER_FILLED
        # ledger 에 BUY event 1건 기록.
        assert report.events_recorded >= 1

    def test_recommended_with_existing_position_demotes_to_hold(self):
        """spec: 보유 중 + recommended → HOLD (중복 매수 회피)."""
        exp = _build_explanation()
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING",
            positions=[
                PositionSnapshot(strategy="sma_crossover", symbol="005930",
                                  quantity=10),
            ],
        )
        # 보유 중 → BUY 차단 → HOLD.
        buys = [d for d in report.decisions if d.action == DecisionAction.BUY]
        holds = [d for d in report.decisions if d.action == DecisionAction.HOLD]
        assert len(buys) == 0
        assert len(holds) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# 3. 위험 한도 위반 / 차단 — BUY 차단
# ─────────────────────────────────────────────────────────────────────────────


class TestRiskBlocksBuy:
    def test_overfit_risk_blocks_buy(self):
        """spec: OVERFIT_RISK → BUY 차단 (excluded → NO_OP audit)."""
        exp = _build_explanation(entries=[
            _entry(wf_verdict="OVERFIT_RISK", train_avg=800.0, val_avg=100.0,
                   status=ReportStatus.OVERFIT_RISK),
        ])
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
        )
        # OVERFIT 전략 BUY 0건.
        buys = [d for d in report.decisions if d.action == DecisionAction.BUY]
        assert len(buys) == 0

    def test_stress_failed_blocks_buy(self):
        exp = _build_explanation(entries=[
            _entry(status=ReportStatus.STRESS_FAILED),
        ])
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
        )
        buys = [d for d in report.decisions if d.action == DecisionAction.BUY]
        assert len(buys) == 0

    def test_low_liquidity_blocks_buy(self):
        """spec: LOW_LIQUIDITY 장세 → 모든 BUY 차단 (verdict=DO_NOT_START)."""
        exp = build_paper_start_explanation(
            operator_report=_operator_report([_entry()]),
            market_state=MarketStateInput(liquidity_score=0.10),
        )
        assert exp.market_regime == "LOW_LIQUIDITY"
        assert exp.verdict == ExplanationVerdict.DO_NOT_START
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
        )
        buys = [d for d in report.decisions if d.action == DecisionAction.BUY]
        assert len(buys) == 0
        # block_reasons 에 DO_NOT_START 명시.
        joined = " ".join(report.block_reasons)
        assert "DO_NOT_START" in joined

    def test_unknown_regime_blocks_buy(self):
        """spec: UNKNOWN 장세 → BUY 차단 (verdict=DO_NOT_START)."""
        exp = build_paper_start_explanation(
            operator_report=_operator_report([_entry()]),
            market_state=None,    # → UNKNOWN regime
        )
        assert exp.market_regime == "UNKNOWN"
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
        )
        buys = [d for d in report.decisions if d.action == DecisionAction.BUY]
        assert len(buys) == 0

    def test_pre_market_block_blocks_all_trades(self):
        """spec: pre-market block → 모든 trade action 차단."""
        exp = _build_explanation(pre_market=PreMarketSummary(
            start_allowed=False, verdict="DO_NOT_START",
            blocking_reasons=["api_unhealthy"],
        ))
        assert exp.verdict == ExplanationVerdict.DO_NOT_START
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
        )
        buys = [d for d in report.decisions if d.action == DecisionAction.BUY]
        sells = [d for d in report.decisions if d.action == DecisionAction.SELL]
        exits = [d for d in report.decisions if d.action == DecisionAction.EXIT]
        assert len(buys) == 0
        assert len(sells) == 0
        assert len(exits) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. EXIT 변환 — 보유 + watchlist + exit_condition
# ─────────────────────────────────────────────────────────────────────────────


class TestExitCondition:
    def _direct_explanation(self, *, watchlist_strategy):
        """4-05 의 redistribution 흐름을 우회 — watchlist explanation 직접 구성."""
        from datetime import datetime, timezone
        return PaperStartExplanation(
            generated_at=datetime.now(timezone.utc).isoformat(),
            schema_version="1.0",
            verdict=ExplanationVerdict.REVIEW_WITH_WARNING,
            recommended_explanations=[],
            watchlist_explanations=[
                StrategyExplanation(
                    strategy=watchlist_strategy, symbol="005930",
                    bucket="watchlist",
                    paper_candidate_status="READY_FOR_PAPER",
                    rationale_lines=["위험 신호 다수 — 추가 관찰"],
                    risk_flags=["high_risk_a"],
                ),
            ],
            excluded_explanations=[],
            market_regime="TREND_UP",
            regime_confidence=0.75,
            regime_allowed_tactics=["sma_crossover"],
        )

    def test_holding_with_exit_hint_creates_exit(self):
        """spec: 보유 포지션 + exit 조건 + watchlist → EXIT 생성."""
        exp = self._direct_explanation(watchlist_strategy="sma_crossover")
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING",
            positions=[
                PositionSnapshot(
                    strategy="sma_crossover", symbol="005930",
                    quantity=10, exit_condition=True,
                ),
            ],
        )
        exits = [d for d in report.decisions if d.action == DecisionAction.EXIT]
        assert len(exits) == 1
        assert exits[0].virtual_position_delta == -10   # 전량 청산
        # ledger 에 EXIT 1건 기록.
        assert report.events_recorded >= 1

    def test_no_position_exit_hint_marks_no_op(self):
        """보유 없음 + exit_condition=True → EXIT 생성 안 됨 (HOLD)."""
        exp = self._direct_explanation(watchlist_strategy="sma_crossover")
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING",
            positions=[
                PositionSnapshot(
                    strategy="sma_crossover", symbol="005930",
                    quantity=0, exit_condition=True,
                ),
            ],
        )
        exits = [d for d in report.decisions if d.action == DecisionAction.EXIT]
        assert len(exits) == 0
        # HOLD 로 분류.
        holds = [d for d in report.decisions if d.action == DecisionAction.HOLD]
        assert len(holds) >= 1

    def test_no_position_exit_hint_with_recommended_does_no_exit(self):
        """보유 없음 + recommended (not watchlist) → EXIT 0건."""
        exp = _build_explanation()
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING",
            positions=[
                PositionSnapshot(
                    strategy="sma_crossover", symbol="005930",
                    quantity=0, exit_condition=True,
                ),
            ],
        )
        exits = [d for d in report.decisions if d.action == DecisionAction.EXIT]
        assert len(exits) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 5. Loop state gating — RUNNING / EMERGENCY_STOP / 그 외
# ─────────────────────────────────────────────────────────────────────────────


class TestLoopStateGating:
    @pytest.mark.parametrize("state", [
        "PAUSED", "WAITING_MARKET", "STOPPED", "MARKET_CLOSED",
    ])
    def test_non_running_state_blocks_trade_actions(self, state):
        """spec: RUNNING 외 → BUY/SELL/EXIT 차단, HOLD/NO_OP audit 만."""
        exp = _build_explanation()
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state=state, positions=[],
        )
        buys = [d for d in report.decisions if d.action == DecisionAction.BUY]
        sells = [d for d in report.decisions if d.action == DecisionAction.SELL]
        exits = [d for d in report.decisions if d.action == DecisionAction.EXIT]
        assert len(buys) == 0
        assert len(sells) == 0
        assert len(exits) == 0
        # block_reasons 에 명시.
        joined = " ".join(report.block_reasons)
        assert "RUNNING" in joined or state in joined

    def test_emergency_stop_blocks_everything(self):
        """spec: EMERGENCY_STOP → 모든 action 차단, ledger 손대지 않음."""
        exp = _build_explanation()
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="EMERGENCY_STOP", positions=[],
        )
        # decisions 0건.
        assert len(report.decisions) == 0
        # ledger 손대지 않음 — events_recorded=0.
        assert report.events_recorded == 0
        assert len(get_ledger()) == 0
        # block_reasons 에 EMERGENCY_STOP 명시.
        joined = " ".join(report.block_reasons)
        assert "EMERGENCY_STOP" in joined


# ─────────────────────────────────────────────────────────────────────────────
# 6. Ledger 연결 검증
# ─────────────────────────────────────────────────────────────────────────────


class TestLedgerIntegration:
    def test_ledger_records_buy_event(self):
        exp = _build_explanation()
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
        )
        assert report.events_recorded >= 1
        # ledger 에 BUY event 1건.
        events = get_ledger().filter_by(decision_action=DecisionAction.BUY)
        assert len(events) >= 1

    def test_record_false_skips_ledger(self):
        exp = _build_explanation()
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            record=False,
        )
        # decisions 변환은 됨, 기록은 0.
        assert len(report.decisions) >= 1
        assert report.events_recorded == 0
        assert len(get_ledger()) == 0

    def test_hold_recorded_in_any_state(self):
        """HOLD 는 모든 state 에서 ledger 기록 가능 (judgment log)."""
        exp = _build_explanation()
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="PAUSED", positions=[],
        )
        # PAUSED → BUY 차단 → HOLD 변환 → ledger 기록.
        holds = [d for d in report.decisions if d.action == DecisionAction.HOLD]
        assert len(holds) >= 1
        assert report.events_recorded >= 1


# ─────────────────────────────────────────────────────────────────────────────
# 7. API endpoint
# ─────────────────────────────────────────────────────────────────────────────


def _client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


@pytest.fixture
def _force_market_open(monkeypatch):
    """test 격리: loop status() 가 RUNNING 으로 보이도록 monkey-patch."""
    from app.auto_paper import loop as loop_mod
    from app.scheduler.market_clock import MarketPhase
    monkeypatch.setattr(
        loop_mod, "current_market_phase", lambda *a, **kw: MarketPhase.OPEN,
    )
    from app.auto_paper.loop import get_auto_paper_loop, AutoPaperState
    loop = get_auto_paper_loop()
    loop._state = AutoPaperState.RUNNING   # type: ignore[attr-defined]
    return loop


class TestAPI:
    def test_endpoint_empty_input_returns_block(self):
        client = _client()
        r = client.post("/api/agents/paper-decision-bridge", json={})
        assert r.status_code == 200
        body = r.json()
        # 입력 없음 → UNKNOWN regime → DO_NOT_START → trade 차단.
        assert body["is_order_signal"]       is False
        assert body["auto_apply_allowed"]    is False
        assert body["is_live_authorization"] is False

    def test_endpoint_running_with_trend_up(self, _force_market_open):
        client = _client()
        r = client.post("/api/agents/paper-decision-bridge", json={
            "market_state": {"trend_direction": "UP"},
        })
        body = r.json()
        # input items 0건 → DO_NOT_START.
        # 본 endpoint 는 explanation 자체를 빌드하므로 input items 가 없으면
        # NO_CANDIDATE 또는 빈 decisions.
        assert body["loop_state"] == "RUNNING"
        assert "decisions" in body
        assert body["decision_count"] == len(body["decisions"])

    def test_endpoint_no_secret_in_response(self):
        client = _client()
        r = client.post("/api/agents/paper-decision-bridge", json={})
        text = r.text.lower()
        for f in ("anthropic_api_key", "openai_api_key", "kis_app_key",
                   "kis_app_secret", "account_no"):
            assert f not in text


# ─────────────────────────────────────────────────────────────────────────────
# 8. Static guards — broker / executor imports
# ─────────────────────────────────────────────────────────────────────────────


_MOD = (
    Path(__file__).resolve().parents[1] / "app" / "agents"
    / "paper_decision_bridge.py"
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
            assert not re.search(pat, src, re.MULTILINE), (
                f"FORBIDDEN in paper_decision_bridge.py: {pat}"
            )

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
            assert not re.search(pat, src, re.MULTILINE), (
                f"FORBIDDEN: {pat}"
            )

    def test_no_safety_flag_mutation(self):
        src = _MOD.read_text(encoding="utf-8")
        bad = [
            r"ENABLE_LIVE_TRADING\s*=\s*['\"]?true",
            r"ENABLE_AI_EXECUTION\s*=\s*['\"]?true",
            r"settings\.enable_live_trading\s*=(?!=)",
            r"settings\.enable_ai_execution\s*=(?!=)",
        ]
        for pat in bad:
            assert not re.search(pat, src, re.IGNORECASE), (
                f"safety flag mutation: {pat}"
            )


class TestSchemaLock:
    def test_bridge_report_has_no_secret_fields(self):
        names = BridgeReport.__dataclass_fields__.keys()
        secret = [
            "api_key", "secret", "app_key", "app_secret", "access_token",
            "account_no", "account_number", "anthropic_api_key",
            "openai_api_key", "kis_app_key", "kis_app_secret",
        ]
        for n in secret:
            assert n not in names, f"bridge has secret field: {n}"

    def test_position_snapshot_has_no_secret_fields(self):
        names = PositionSnapshot.__dataclass_fields__.keys()
        secret = ["api_key", "secret", "app_key", "app_secret", "account_no"]
        for n in secret:
            assert n not in names, f"position has secret field: {n}"

    def test_schema_version_carried(self):
        report = bridge_explanation_to_paper_decisions(
            explanation=_build_explanation(),
            loop_state="RUNNING", positions=[],
        )
        assert report.schema_version == BRIDGE_SCHEMA_VERSION


# ─────────────────────────────────────────────────────────────────────────────
# #4-08: Position sizing integration
# ─────────────────────────────────────────────────────────────────────────────


from app.auto_paper.position_sizer import (   # noqa: E402
    PositionSizingPolicy,
    SizingVerdict,
)


class TestBridgePositionSizing:
    """sizing_policy 가 주어지면 BUY/SELL/EXIT 의 가상 수량이 동적으로 결정.

    *broker 호출 0건* — sizing 결과는 metadata 와 PaperDecision.virtual_position_delta
    에만 반영.
    """

    # Permissive policy so cap doesn't dominate.
    _POL = PositionSizingPolicy(
        max_risk_per_trade_pct=0.01,
        default_stop_loss_pct=0.03,
        max_position_pct=1.0,
        max_position_krw=10_000_000_000,
        min_confidence_threshold=0.40,
    )

    def test_buy_uses_computed_size_when_policy_given(self):
        exp = _build_explanation()  # 1 recommended entry, regime=TREND_UP
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            sizing_policy=self._POL,
            price_lookup={("sma_crossover", "005930"): 70_000.0},
            account_equity=10_000_000.0,
            confidence_lookup={("sma_crossover", "005930"): 0.90},
        )
        buys = [d for d in report.decisions if d.action == DecisionAction.BUY]
        assert len(buys) == 1
        # virtual_position_delta = +quantity (BUY).
        assert buys[0].virtual_position_delta > 1   # > legacy fixed size of 1
        # metadata carries sizing.
        meta = buys[0].metadata
        assert meta.get("sizing_verdict") in (
            SizingVerdict.SIZED.value, SizingVerdict.REDUCED.value,
        )
        assert int(meta["sizing_quantity"]) == buys[0].virtual_position_delta

    def test_low_confidence_downgrades_to_hold(self):
        """confidence < threshold → sizing quantity=0 → direction downgrades to HOLD."""
        exp = _build_explanation()
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            sizing_policy=self._POL,
            price_lookup={("sma_crossover", "005930"): 70_000.0},
            account_equity=10_000_000.0,
            confidence_lookup={("sma_crossover", "005930"): 0.20},
        )
        buys = [d for d in report.decisions if d.action == DecisionAction.BUY]
        holds = [d for d in report.decisions if d.action == DecisionAction.HOLD]
        assert len(buys) == 0
        assert len(holds) >= 1
        joined = " ".join(report.block_reasons)
        assert "sizing quantity=0" in joined or "BLOCKED_LOW_CONFIDENCE" in joined

    def test_missing_price_blocks_buy(self):
        """price_lookup 미제공 → price=0 → INSUFFICIENT_DATA → HOLD."""
        exp = _build_explanation()
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            sizing_policy=self._POL,
            price_lookup={},   # missing
            account_equity=10_000_000.0,
            confidence_lookup={("sma_crossover", "005930"): 0.90},
        )
        buys = [d for d in report.decisions if d.action == DecisionAction.BUY]
        assert len(buys) == 0

    def test_zero_equity_blocks_buy(self):
        exp = _build_explanation()
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            sizing_policy=self._POL,
            price_lookup={("sma_crossover", "005930"): 70_000.0},
            account_equity=0.0,
            confidence_lookup={("sma_crossover", "005930"): 0.90},
        )
        buys = [d for d in report.decisions if d.action == DecisionAction.BUY]
        assert len(buys) == 0

    def test_sizing_results_in_metadata(self):
        exp = _build_explanation()
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            sizing_policy=self._POL,
            price_lookup={("sma_crossover", "005930"): 70_000.0},
            account_equity=10_000_000.0,
            confidence_lookup={("sma_crossover", "005930"): 0.90},
        )
        assert report.metadata["sizing_applied"] is True
        sr = report.metadata["sizing_results"]
        assert isinstance(sr, list)
        assert len(sr) == 1
        assert sr[0]["is_order_signal"] is False
        assert sr[0]["auto_apply_allowed"] is False
        assert sr[0]["is_live_authorization"] is False
        assert sr[0]["quantity"] >= 1

    def test_backwards_compat_no_policy_uses_fixed_size(self):
        """sizing_policy=None → legacy virtual_trade_size 그대로 사용."""
        exp = _build_explanation()
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            virtual_trade_size=3,
            # sizing_policy omitted.
        )
        buys = [d for d in report.decisions if d.action == DecisionAction.BUY]
        assert len(buys) == 1
        assert buys[0].virtual_position_delta == 3
        assert report.metadata["sizing_applied"] is False
        assert report.metadata["sizing_results"] == []

    def test_emergency_stop_with_policy_still_blocks_all(self):
        """EMERGENCY_STOP — sizing_policy 가 있어도 모든 변환 차단 (4-07 invariant)."""
        exp = _build_explanation()
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="EMERGENCY_STOP", positions=[],
            sizing_policy=self._POL,
            price_lookup={("sma_crossover", "005930"): 70_000.0},
            account_equity=10_000_000.0,
            confidence_lookup={("sma_crossover", "005930"): 0.90},
        )
        assert report.decisions == []
        assert report.events_recorded == 0

    def test_exit_uses_sizing_when_holding(self):
        """watchlist + exit_condition + 보유 → EXIT, sizing 적용 시 quantity carry."""
        # Build a watchlist-bucket explanation directly so the bridge sees an
        # exit-eligible entry. (4-05 redistribution may move WATCHLIST_ONLY
        # entries to `excluded`, so we construct PaperStartExplanation manually.)
        from app.agents.paper_start_explanation import (
            PaperStartExplanation as _PSE,
            StrategyExplanation as _SE,
            ExplanationVerdict as _EV,
        )
        exp = _PSE(
            generated_at="2026-05-18T00:00:00+00:00",
            schema_version="1.0",
            verdict=_EV.READY_TO_REVIEW,
            recommended_explanations=[],
            watchlist_explanations=[
                _SE(strategy="sma_crossover", symbol="005930",
                    bucket="watchlist", paper_candidate_status="WATCHLIST_ONLY",
                    rationale_lines=["watchlist + exit hint"]),
            ],
            excluded_explanations=[],
            market_regime="TREND_UP",
            regime_confidence=0.85,
            regime_reasons=[],
            regime_risk_flags=[],
            regime_allowed_tactics=[],
            regime_blocked_tactics=[],
            overfit_count=0,
            overfit_strategies=[],
            headline="test",
            risk_summary="test",
            operator_note="test",
            next_actions=[],
            can_start_paper=True,
            blocking_reasons=[],
        )
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING",
            positions=[PositionSnapshot(strategy="sma_crossover",
                                         symbol="005930",
                                         quantity=10, exit_condition=True)],
            sizing_policy=self._POL,
            price_lookup={("sma_crossover", "005930"): 70_000.0},
            account_equity=10_000_000.0,
            confidence_lookup={("sma_crossover", "005930"): 0.90},
        )
        exits = [d for d in report.decisions if d.action == DecisionAction.EXIT]
        assert len(exits) == 1
        # EXIT virtual_position_delta is negative (sell-side).
        assert exits[0].virtual_position_delta < 0
