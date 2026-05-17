"""#4-01: Strategy Optimizer Agent + 입력 schema 테스트.

invariant:
- `StrategyAgentInput` + `StrategyAgentInputItem` 둘 다 is_order_signal=False /
  auto_apply_allowed=False / is_live_authorization=False (__post_init__ ValueError 가드).
- BUY/SELL/HOLD/Place Order/실거래 시작 라벨 0건 — JSON 직렬화에도 없음.
- broker / OrderExecutor / route_order / KIS 주문 import 0건 (정적 grep).
- 외부 HTTP / AI SDK import 0건 (anthropic/openai/httpx/requests/yfinance).
- secret / API key / 계좌번호 / `.env` 접근 0건.
- StrategyOptimizerAgent.run() 은 AgentOutput 반환 — broker / route_order 호출 0건.
- 후보 0건도 표준 입력 생성 + reasons_no_candidate carry.
- 제외 사유 (exclusion_reasons) 가 item 에 carry.
- 14 필수 필드 모두 schema 에 존재 (정적 키 lock).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.agents.base import (
    AgentContext,
    AgentDecision,
    AgentOutput,
    AgentRole,
)
from app.agents.strategy_optimizer_agent import (
    SCHEMA_VERSION,
    StrategyAgentInput,
    StrategyAgentInputItem,
    StrategyOptimizerAgent,
    build_strategy_agent_input,
)
from app.analytics.paper_candidate_aggregator import PipelineStage
from app.analytics.strategy_optimization_report import (
    OperatorReport,
    ReportInputs,
    ReportStatus,
    StrategyEntry,
    build_operator_report,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helper — synthetic 입력 (3-02 ~ 3-05 payload + paper_candidate_config)
# ─────────────────────────────────────────────────────────────────────────────


def _backtest_payload(strategy="sma_crossover", symbol="005930",
                      verdict="BACKTEST_PASS", params=None,
                      metrics_override=None):
    base = {
        "trade_count":               45,
        "profit_factor":             1.6,
        "max_drawdown":              0.08,
        "expectancy":                650.0,
        "win_rate":                  0.55,
        "risk_adjusted_score":       0.05,
        "total_return":              0.18,
        "fee_adjusted_return":       0.16,
        "slippage_adjusted_return":  0.14,
        "loss_streak":               3,
    }
    if metrics_override:
        base.update(metrics_override)
    return {
        "per_symbol": [{
            "symbol": symbol,
            "runs": [{"strategy": strategy, "params": params or {},
                       "verdict": verdict, "metrics": base}],
        }],
    }


def _optimization_payload(strategy="sma_crossover", symbol="005930",
                          verdict="PAPER_CANDIDATE", params=None):
    return {
        "all_runs": [{
            "strategy": strategy, "symbol": symbol,
            "params": params or {}, "verdict": verdict,
            "metrics": {
                "trade_count":         45,
                "profit_factor":       1.6,
                "max_drawdown":        0.08,
                "expectancy":          650.0,
                "win_rate":            0.55,
                "risk_adjusted_score": 0.05,
            },
            "reasons": ["all_filters_passed"],
        }],
    }


def _walk_forward_payload(strategy="sma_crossover", symbol="005930",
                          verdict="HEALTHY", params=None):
    return {
        "results": [{
            "strategy": strategy, "symbol": symbol,
            "params": params or {}, "verdict": verdict,
            "fold_count": 5, "train_expectancy_avg": 720.0,
            "val_expectancy_avg": 580.0,
        }],
    }


def _stress_test_payload(strategy="sma_crossover", symbol="005930",
                         all_pass=True, params=None):
    scenarios = ["CRASH", "SURGE", "SIDEWAYS", "SLIPPAGE_SPIKE", "DATA_GAP"]
    return {
        "results": [
            {
                "strategy": strategy, "symbol": symbol,
                "params": params or {}, "scenario_name": s,
                "stress_verdict": "PASS" if all_pass else (
                    "FAIL" if i == 0 else "PASS"
                ),
                "stress_score": 85.0,
            }
            for i, s in enumerate(scenarios)
        ],
    }


def _write_all_inputs(tmp_path, *, all_pass=True):
    bt   = tmp_path / "bt.json"
    opt  = tmp_path / "opt.json"
    wf   = tmp_path / "wf.json"
    st   = tmp_path / "st.json"
    bt.write_text(json.dumps(_backtest_payload()), encoding="utf-8")
    opt.write_text(json.dumps(_optimization_payload()), encoding="utf-8")
    wf.write_text(json.dumps(_walk_forward_payload()), encoding="utf-8")
    st.write_text(json.dumps(_stress_test_payload(all_pass=all_pass)),
                  encoding="utf-8")
    return {"bt": str(bt), "opt": str(opt), "wf": str(wf), "st": str(st)}


# ─────────────────────────────────────────────────────────────────────────────
# 1. 백테스트 결과 → Agent 입력 변환
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildFromBacktestResults:
    def test_all_stages_pass_produces_ready_for_paper_item(self, tmp_path):
        paths = _write_all_inputs(tmp_path, all_pass=True)
        agent_input = build_strategy_agent_input(inputs=ReportInputs(
            backtest_summary_path=paths["bt"],
            optimization_summary_path=paths["opt"],
            walk_forward_summary_path=paths["wf"],
            stress_test_summary_path=paths["st"],
        ))
        assert agent_input.item_count == 1
        item = agent_input.items[0]
        assert item.strategy == "sma_crossover"
        assert item.symbol == "005930"
        assert item.paper_candidate_status == ReportStatus.READY_FOR_PAPER.value
        # 14 필수 필드 모두 존재.
        d = item.to_dict()
        required_fields = [
            "strategy", "symbol", "params", "backtest_metrics",
            "optimization_metrics", "walk_forward_verdict",
            "stress_test_verdict", "paper_candidate_status",
            "risk_flags", "exclusion_reasons", "recommendation_context",
            "is_order_signal", "auto_apply_allowed", "is_live_authorization",
        ]
        for f in required_fields:
            assert f in d, f"missing field: {f}"

    def test_walk_forward_and_stress_verdicts_carried(self, tmp_path):
        paths = _write_all_inputs(tmp_path, all_pass=True)
        agent_input = build_strategy_agent_input(inputs=ReportInputs(
            backtest_summary_path=paths["bt"],
            optimization_summary_path=paths["opt"],
            walk_forward_summary_path=paths["wf"],
            stress_test_summary_path=paths["st"],
        ))
        item = agent_input.items[0]
        assert item.walk_forward_verdict == "HEALTHY"
        assert item.stress_test_verdict  == "PASS"

    def test_recommendation_context_has_headline_metrics(self, tmp_path):
        paths = _write_all_inputs(tmp_path, all_pass=True)
        agent_input = build_strategy_agent_input(inputs=ReportInputs(
            backtest_summary_path=paths["bt"],
            optimization_summary_path=paths["opt"],
            walk_forward_summary_path=paths["wf"],
            stress_test_summary_path=paths["st"],
        ))
        ctx = agent_input.items[0].recommendation_context
        headline = ctx["headline_metrics"]
        for key in ("profit_factor", "max_drawdown", "expectancy",
                    "win_rate", "trade_count", "loss_streak",
                    "fee_adjusted_return", "slippage_adjusted_return"):
            assert key in headline, f"missing headline metric: {key}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. 후보 없음 상태 → Agent 입력 변환
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildWhenNoCandidates:
    def test_no_inputs_still_produces_valid_schema(self):
        agent_input = build_strategy_agent_input(inputs=ReportInputs())
        assert agent_input.item_count == 0
        assert agent_input.overall_status == ReportStatus.NO_CANDIDATE.value
        assert agent_input.reasons_no_candidate, \
            "후보 0건이면 reasons_no_candidate 채워져야 함"

    def test_stress_fail_carries_exclusion_reasons(self, tmp_path):
        paths = _write_all_inputs(tmp_path, all_pass=False)
        agent_input = build_strategy_agent_input(inputs=ReportInputs(
            backtest_summary_path=paths["bt"],
            optimization_summary_path=paths["opt"],
            walk_forward_summary_path=paths["wf"],
            stress_test_summary_path=paths["st"],
        ))
        item = agent_input.items[0]
        assert item.paper_candidate_status == ReportStatus.STRESS_FAILED.value
        joined = " ".join(item.exclusion_reasons)
        assert "3-05" in joined or "탈락" in joined or "단계" in joined

    def test_missing_stages_marks_need_more_data(self, tmp_path):
        bt = tmp_path / "bt.json"
        bt.write_text(json.dumps(_backtest_payload()), encoding="utf-8")
        agent_input = build_strategy_agent_input(inputs=ReportInputs(
            backtest_summary_path=str(bt),
        ))
        assert agent_input.items
        item = agent_input.items[0]
        assert item.paper_candidate_status == ReportStatus.NEED_MORE_DATA.value
        # 누락된 단계 표시 carry.
        joined = " ".join(item.exclusion_reasons)
        assert "3-03" in joined or "3-04" in joined or "3-05" in joined


# ─────────────────────────────────────────────────────────────────────────────
# 3. Invariant 강제 — is_order_signal=False / auto_apply_allowed=False /
#    is_live_authorization=False
# ─────────────────────────────────────────────────────────────────────────────


class TestInvariants:
    def test_item_is_order_signal_must_be_false(self):
        with pytest.raises(ValueError):
            StrategyAgentInputItem(
                strategy="sma_crossover", symbol="005930",
                is_order_signal=True,
            )

    def test_item_auto_apply_allowed_must_be_false(self):
        with pytest.raises(ValueError):
            StrategyAgentInputItem(
                strategy="sma_crossover", symbol="005930",
                auto_apply_allowed=True,
            )

    def test_item_is_live_authorization_must_be_false(self):
        with pytest.raises(ValueError):
            StrategyAgentInputItem(
                strategy="sma_crossover", symbol="005930",
                is_live_authorization=True,
            )

    def test_top_level_invariants_immutable(self):
        with pytest.raises(ValueError):
            StrategyAgentInput(
                generated_at="x", schema_version="1.0",
                overall_status="NO_CANDIDATE",
                is_order_signal=True,
            )
        with pytest.raises(ValueError):
            StrategyAgentInput(
                generated_at="x", schema_version="1.0",
                overall_status="NO_CANDIDATE",
                auto_apply_allowed=True,
            )
        with pytest.raises(ValueError):
            StrategyAgentInput(
                generated_at="x", schema_version="1.0",
                overall_status="NO_CANDIDATE",
                is_live_authorization=True,
            )

    def test_to_dict_carries_invariants_at_both_levels(self, tmp_path):
        paths = _write_all_inputs(tmp_path, all_pass=True)
        agent_input = build_strategy_agent_input(inputs=ReportInputs(
            backtest_summary_path=paths["bt"],
            optimization_summary_path=paths["opt"],
            walk_forward_summary_path=paths["wf"],
            stress_test_summary_path=paths["st"],
        ))
        top = agent_input.to_dict()
        assert top["is_order_signal"]       is False
        assert top["auto_apply_allowed"]    is False
        assert top["is_live_authorization"] is False
        for item_dict in top["items"]:
            assert item_dict["is_order_signal"]       is False
            assert item_dict["auto_apply_allowed"]    is False
            assert item_dict["is_live_authorization"] is False

    def test_advisory_disclaimer_present(self):
        agent_input = build_strategy_agent_input(inputs=ReportInputs())
        # disclaimer 가 사람이 읽을 수 있는 advisory 안내 carry.
        assert "advisory" in agent_input.advisory_disclaimer.lower() \
            or "주문" in agent_input.advisory_disclaimer
        assert "is_order_signal=False" in agent_input.advisory_disclaimer

    def test_disclaimer_cannot_be_empty(self):
        with pytest.raises(ValueError):
            StrategyAgentInput(
                generated_at="x", schema_version="1.0",
                overall_status="NO_CANDIDATE",
                advisory_disclaimer="",
            )


# ─────────────────────────────────────────────────────────────────────────────
# 4. Agent (AgentBase compliance)
# ─────────────────────────────────────────────────────────────────────────────


class TestStrategyOptimizerAgent:
    def test_agent_metadata_shape(self):
        agent = StrategyOptimizerAgent()
        meta = agent.metadata
        assert meta.name == "strategy_optimizer_agent"
        assert meta.role == AgentRole.STRATEGY_RESEARCHER
        assert meta.can_execute_order is False

    def test_agent_run_with_no_inputs_returns_report(self):
        agent = StrategyOptimizerAgent()
        ctx = AgentContext(extra={})
        out = agent.run(ctx)
        assert isinstance(out, AgentOutput)
        assert out.role == AgentRole.STRATEGY_RESEARCHER
        assert out.decision == AgentDecision.REPORT
        assert out.is_order_intent is False
        assert out.can_execute_order is False

    def test_agent_run_with_direct_input(self, tmp_path):
        paths = _write_all_inputs(tmp_path, all_pass=True)
        agent_input = build_strategy_agent_input(inputs=ReportInputs(
            backtest_summary_path=paths["bt"],
            optimization_summary_path=paths["opt"],
            walk_forward_summary_path=paths["wf"],
            stress_test_summary_path=paths["st"],
        ))
        agent = StrategyOptimizerAgent()
        ctx = AgentContext(extra={"strategy_agent_input": agent_input})
        out = agent.run(ctx)
        assert out.metadata["strategy_agent_input"]["item_count"] == 1
        # summary 안에 "검토" 같은 advisory 키워드.
        assert "검토" in out.summary or "advisory" in out.summary.lower()

    def test_agent_run_with_operator_report_input(self, tmp_path):
        paths = _write_all_inputs(tmp_path, all_pass=True)
        report = build_operator_report(ReportInputs(
            backtest_summary_path=paths["bt"],
            optimization_summary_path=paths["opt"],
            walk_forward_summary_path=paths["wf"],
            stress_test_summary_path=paths["st"],
        ))
        agent = StrategyOptimizerAgent()
        ctx = AgentContext(extra={"operator_report": report})
        out = agent.run(ctx)
        assert out.metadata["strategy_agent_input"]["item_count"] == 1

    def test_agent_output_invariants(self, tmp_path):
        paths = _write_all_inputs(tmp_path, all_pass=True)
        agent_input = build_strategy_agent_input(inputs=ReportInputs(
            backtest_summary_path=paths["bt"],
            optimization_summary_path=paths["opt"],
            walk_forward_summary_path=paths["wf"],
            stress_test_summary_path=paths["st"],
        ))
        agent = StrategyOptimizerAgent()
        out = agent.run(AgentContext(extra={"strategy_agent_input": agent_input}))
        d = out.to_dict()
        assert d["is_order_intent"]   is False
        assert d["can_execute_order"] is False
        meta = d["metadata"]
        assert meta["advisory_only"]          is True
        assert meta["is_order_signal"]        is False
        assert meta["auto_apply_allowed"]     is False
        assert meta["is_live_authorization"]  is False

    def test_agent_does_not_emit_buy_sell_hold(self, tmp_path):
        paths = _write_all_inputs(tmp_path, all_pass=True)
        agent = StrategyOptimizerAgent()
        ctx = AgentContext(extra={
            "operator_report": build_operator_report(ReportInputs(
                backtest_summary_path=paths["bt"],
                optimization_summary_path=paths["opt"],
                walk_forward_summary_path=paths["wf"],
                stress_test_summary_path=paths["st"],
            )),
        })
        out = agent.run(ctx)
        text = json.dumps(out.to_dict(), ensure_ascii=False)
        forbidden_signals = [
            "\"decision\": \"BUY\"",
            "\"decision\": \"SELL\"",
            "\"decision\": \"HOLD\"",
            "Place Order",
            "지금 매수",
            "지금 매도",
            "실거래 시작",
            "ENABLE_LIVE_TRADING 토글",
        ]
        for fp in forbidden_signals:
            assert fp not in text, f"forbidden signal in agent output: {fp}"


# ─────────────────────────────────────────────────────────────────────────────
# 5. 금지 라벨 / Secret / 정적 import 가드
# ─────────────────────────────────────────────────────────────────────────────


_MOD_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "agents"
    / "strategy_optimizer_agent.py"
)


class TestNoForbiddenImports:
    def test_module_has_no_broker_or_executor_imports(self):
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
                f"forbidden import in strategy_optimizer_agent.py: {pat}"

    def test_module_has_no_external_http_or_ai_sdk_imports(self):
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
                f"forbidden http/AI import in strategy_optimizer_agent.py: {pat}"

    def test_no_safety_flag_mutation(self):
        src = _MOD_PATH.read_text(encoding="utf-8")
        bad = [
            r"ENABLE_LIVE_TRADING\s*=\s*['\"]?true",
            r"ENABLE_AI_EXECUTION\s*=\s*['\"]?true",
            r"ENABLE_FUTURES_LIVE_TRADING\s*=\s*['\"]?true",
            r"KIS_IS_PAPER\s*=\s*['\"]?false",
            r"settings\.enable_live_trading\s*=",
            r"settings\.enable_ai_execution\s*=",
            r"settings\.enable_futures_live_trading\s*=",
        ]
        for pat in bad:
            assert not re.search(pat, src, re.IGNORECASE), \
                f"safety flag mutation in strategy_optimizer_agent.py: {pat}"


class TestNoSecretPatterns:
    def test_no_secret_patterns_in_module(self):
        src = _MOD_PATH.read_text(encoding="utf-8")
        patterns = [
            r"sk-[A-Za-z0-9]{20,}",
            r"sk-ant-[A-Za-z0-9_\-]{20,}",
            r"ghp_[A-Za-z0-9]{30,}",
            r"Bearer\s+[A-Za-z0-9._\-]{20,}",
            r"PST[A-Za-z0-9]{30,}",   # KIS personal token shape.
        ]
        for pat in patterns:
            assert not re.search(pat, src), \
                f"secret pattern in module: {pat}"

    def test_schema_has_no_secret_fields(self):
        """schema 자체에 API key / Secret / 계좌번호 carry 필드 0건."""
        item_fields = StrategyAgentInputItem.__dataclass_fields__.keys()
        top_fields = StrategyAgentInput.__dataclass_fields__.keys()
        secret_field_names = [
            "api_key", "secret", "app_key", "app_secret", "access_token",
            "account_no", "account_number", "anthropic_api_key",
            "openai_api_key", "kis_app_key", "kis_app_secret",
        ]
        for name in secret_field_names:
            assert name not in item_fields, f"item has secret field: {name}"
            assert name not in top_fields,  f"top has secret field: {name}"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Required 14 fields lock — schema 진화 시 의도적 PR 필요
# ─────────────────────────────────────────────────────────────────────────────


class TestSchemaFieldLock:
    def test_item_schema_has_required_14_fields(self):
        required = {
            "strategy", "symbol", "params", "backtest_metrics",
            "optimization_metrics", "walk_forward_verdict",
            "stress_test_verdict", "paper_candidate_status",
            "risk_flags", "exclusion_reasons", "recommendation_context",
            "is_order_signal", "auto_apply_allowed", "is_live_authorization",
        }
        actual = set(StrategyAgentInputItem.__dataclass_fields__.keys())
        missing = required - actual
        assert not missing, f"missing required fields: {missing}"

    def test_top_level_schema_has_required_fields(self):
        required = {
            "generated_at", "schema_version", "overall_status",
            "items", "reasons_no_candidate", "advisory_disclaimer",
            "metadata", "is_order_signal", "auto_apply_allowed",
            "is_live_authorization",
        }
        actual = set(StrategyAgentInput.__dataclass_fields__.keys())
        missing = required - actual
        assert not missing, f"missing top-level fields: {missing}"

    def test_schema_version_carried(self):
        agent_input = build_strategy_agent_input(inputs=ReportInputs())
        assert agent_input.schema_version == SCHEMA_VERSION


# ─────────────────────────────────────────────────────────────────────────────
# 7. Integration — direct StrategyEntry → Agent input round-trip via builder
# ─────────────────────────────────────────────────────────────────────────────


class TestEndToEnd:
    def test_round_trip_from_operator_report(self):
        """OperatorReport (3-08) → StrategyAgentInput (4-01) 동일 strategy carry."""
        entry = StrategyEntry(
            strategy_id="sma_crossover",
            display_name="단기/장기 이동평균 교차",
            symbol="005930",
            params={"short": 5, "long": 20},
            status=ReportStatus.READY_FOR_PAPER,
            pipeline_stages=[
                PipelineStage(name="3-02", verdict="BACKTEST_PASS",
                              extra={"metrics": {"profit_factor": 1.5}}),
                PipelineStage(name="3-03", verdict="PAPER_CANDIDATE",
                              extra={"metrics": {"win_rate": 0.55}}),
                PipelineStage(name="3-04", verdict="HEALTHY"),
                PipelineStage(name="3-05", verdict="PASS"),
            ],
            risk_metrics={"profit_factor": 1.5, "win_rate": 0.55,
                          "max_drawdown": 0.08, "expectancy": 500.0,
                          "trade_count": 45},
            exclusion_reasons=[],
            risk_signals=[],
            score=0.05,
        )
        report = OperatorReport(
            generated_at="2026-05-17T00:00:00+00:00",
            overall_status=ReportStatus.READY_FOR_PAPER,
            paper_ready_count=1,
            excluded_count=0,
            entries=[entry],
            paper_candidates=[entry],
            excluded=[],
        )
        agent_input = build_strategy_agent_input(operator_report=report)
        assert agent_input.item_count == 1
        item = agent_input.items[0]
        assert item.strategy == "sma_crossover"
        assert item.symbol == "005930"
        assert item.params == {"short": 5, "long": 20}
        assert item.walk_forward_verdict == "HEALTHY"
        assert item.stress_test_verdict == "PASS"
        assert item.paper_candidate_status == ReportStatus.READY_FOR_PAPER.value
        assert item.recommendation_context["display_name"] == "단기/장기 이동평균 교차"
