"""#48 / 6-03: Agent / Risk Gate 스트레스 테스트.

핵심 invariant:
- 12개 악조건 시나리오 실행 (슬리피지/부분체결/거절/미체결/stale/crash/gap/data lock/
  drift/daily loss/kill switch).
- 진짜 가드 검증: stale price → BUY 차단, crash/gap down → BUY 차단, daily loss →
  block, kill switch(emergency_stop) → 모든 주문 REJECTED.
- order_quality 정합성: partial≠filled, rejected≠submitted, unfilled≠filled.
- risk_gate_triggered / kill_switch_triggered / PASS·WARN·FAIL 산출.
- 실제 주문 0건, broker/OrderExecutor/route_order/KIS import·호출 0건, secret 0건,
  is_order_signal/is_live_authorization/broker_order_sent=False.
"""

from __future__ import annotations

import json

import pytest

from app.stress_test import agent_stress_test as st
from app.stress_test.agent_stress_test import (
    ALL_SCENARIOS,
    StressTestReport,
    StressVerdict,
    render_markdown_report,
    run_agent_stress_test,
    run_stress_scenario,
    summarize_stress_report,
)


@pytest.fixture(scope="module")
def report() -> StressTestReport:
    return run_agent_stress_test()


# ── 1~12. 각 시나리오 실행 + 기대 동작 ───────────────────────────────────────


def test_all_twelve_scenarios_present(report):
    names = {r.scenario for r in report.scenarios}
    assert names == set(ALL_SCENARIOS)
    assert len(ALL_SCENARIOS) == 12


def test_slippage_high(report):
    r = next(x for x in report.scenarios if x.scenario == "SLIPPAGE_HIGH")
    assert r.verdict in (StressVerdict.WARN.value, StressVerdict.PASS.value)
    assert r.order_quality["slippage_bps"] is not None
    assert r.order_quality["slippage_bps"] >= st.SLIPPAGE_WARN_BPS


def test_partial_fill_not_treated_as_filled(report):
    r = next(x for x in report.scenarios if x.scenario == "PARTIAL_FILL")
    oq = r.order_quality
    assert oq["partial_fill"] is True
    assert oq["order_status"] != "FILLED"
    assert oq["filled_quantity"] == 3 and oq["unfilled_quantity"] == 7
    assert r.verdict != StressVerdict.FAIL.value


def test_order_rejected_not_submitted(report):
    r = next(x for x in report.scenarios if x.scenario == "ORDER_REJECTED")
    oq = r.order_quality
    assert oq["order_status"] == "REJECTED"
    assert oq["broker_order_no"] is None      # broker_order_sent=false.
    assert oq["filled_quantity"] == 0
    assert r.verdict == StressVerdict.PASS.value


def test_unfilled_timeout_not_filled(report):
    r = next(x for x in report.scenarios if x.scenario == "UNFILLED_TIMEOUT")
    oq = r.order_quality
    assert oq["order_status"] == "UNFILLED"
    assert oq["filled_quantity"] == 0
    assert oq["fill_polling"] is not None


def test_price_stale_blocks_buy(report):
    r = next(x for x in report.scenarios if x.scenario == "PRICE_STALE")
    # stale price 에서 BUY 가 차단돼야 PASS (FAIL 이면 stale 에서 BUY 허용된 것).
    assert r.verdict == StressVerdict.PASS.value
    assert r.risk_gate_triggered is True
    assert "STALE_PRICE_BLOCKED" == r.reason_code


def test_market_crash_blocks_new_buy(report):
    r = next(x for x in report.scenarios if x.scenario == "MARKET_CRASH")
    assert r.verdict == StressVerdict.PASS.value
    assert r.risk_gate_triggered is True
    # council 도 BUY 를 내지 않았다.
    assert "council=BUY" not in r.observed


def test_gap_down_blocks_new_buy(report):
    r = next(x for x in report.scenarios if x.scenario == "GAP_DOWN_OPEN")
    assert r.verdict == StressVerdict.PASS.value
    assert r.risk_gate_triggered is True


def test_gap_up_spike_flags_risk(report):
    r = next(x for x in report.scenarios if x.scenario == "GAP_UP_SPIKE")
    assert r.verdict in (StressVerdict.WARN.value, StressVerdict.PASS.value)


def test_data_lock_holds(report):
    r = next(x for x in report.scenarios if x.scenario == "DATA_LOCK")
    assert r.verdict == StressVerdict.PASS.value
    assert "HOLD" in r.observed


def test_portfolio_drift_detected(report):
    r = next(x for x in report.scenarios if x.scenario == "PORTFOLIO_DRIFT")
    assert r.verdict != StressVerdict.FAIL.value
    assert r.risk_gate_triggered is True
    assert r.reason_code == "PORTFOLIO_DRIFT_DETECTED"


def test_daily_loss_limit_blocks(report):
    r = next(x for x in report.scenarios if x.scenario == "DAILY_LOSS_LIMIT")
    assert r.verdict == StressVerdict.PASS.value
    assert r.risk_gate_triggered is True
    assert r.kill_switch_should_trigger is True   # 운영자 kill switch 권고.


def test_kill_switch_blocks_all(report):
    r = next(x for x in report.scenarios if x.scenario == "REPEATED_REJECTION")
    assert r.verdict == StressVerdict.PASS.value
    assert r.kill_switch_triggered is True        # emergency_stop → REJECTED.


# ── 13~15. 집계 산출 ─────────────────────────────────────────────────────────


def test_risk_gate_and_kill_switch_counts(report):
    assert report.risk_gate_triggered_count >= 5
    assert report.kill_switch_triggered_count >= 1
    assert report.kill_switch_should_trigger_count >= 2


def test_pass_warn_fail_counts(report):
    total = sum(report.counts.values())
    assert total == 12
    # 기본 fixture 는 어떤 가드도 무너지지 않아 FAIL 0.
    assert report.counts.get("FAIL", 0) == 0
    assert report.overall_verdict in (StressVerdict.PASS.value, StressVerdict.WARN.value)


def test_no_fail_in_default_run(report):
    assert report.overall_verdict != StressVerdict.FAIL.value
    assert report.reason_code in ("STRESS_ALL_PASS", "STRESS_WARN_ONLY")


# ── 단일 시나리오 / strict ───────────────────────────────────────────────────


def test_single_scenario_run():
    r = run_stress_scenario("PRICE_STALE")
    assert r.scenario == "PRICE_STALE"
    assert r.verdict == StressVerdict.PASS.value


def test_unknown_scenario_raises():
    with pytest.raises(KeyError):
        run_stress_scenario("NOT_A_SCENARIO")


def test_strict_promotes_warn_to_fail():
    rep = run_agent_stress_test(scenarios=("SLIPPAGE_HIGH",), strict=True)
    # SLIPPAGE_HIGH 는 WARN → strict 면 overall FAIL.
    assert rep.counts.get("WARN", 0) == 1
    assert rep.overall_verdict == StressVerdict.FAIL.value


def test_deterministic_with_seed():
    a = run_agent_stress_test(seed=42).to_dict()
    b = run_agent_stress_test(seed=42).to_dict()
    a.pop("generated_at")
    b.pop("generated_at")
    for s in a["scenarios"]:
        s.pop("observed", None)   # observed 동일하지만 안전하게.
    for s in b["scenarios"]:
        s.pop("observed", None)
    assert a == b


# ── 안전 invariant ───────────────────────────────────────────────────────────


def test_report_invariants(report):
    assert report.is_order_signal is False
    assert report.is_live_authorization is False
    assert report.broker_order_sent is False
    assert report.auto_apply_allowed is False
    assert report.contains_secret is False
    for r in report.scenarios:
        assert r.broker_order_sent is False
        assert r.is_order_signal is False
        assert r.is_live_authorization is False


def test_report_invariant_enforced():
    with pytest.raises(ValueError):
        StressTestReport(
            generated_at="x", scenarios=(), counts={}, overall_verdict="PASS",
            risk_gate_triggered_count=0, kill_switch_triggered_count=0,
            kill_switch_should_trigger_count=0, reason_code="STRESS_ALL_PASS",
            is_live_authorization=True)


def test_scenario_result_invariant_enforced():
    from app.stress_test.agent_stress_test import ScenarioResult
    with pytest.raises(ValueError):
        ScenarioResult(scenario="X", verdict="PASS", reason_code="R",
                       expected="e", observed="o", broker_order_sent=True)


# ── import / 호출 가드 ───────────────────────────────────────────────────────


def test_no_forbidden_imports_or_calls():
    src = open(st.__file__, encoding="utf-8").read()
    forbidden = (
        "from app.brokers.kis", "from app.brokers.mock_broker",
        "from app.execution.order_router", "from app.execution.executor",
        "from app.execution.order_executor", "from app.execution.paper_trader",
        "import anthropic", "import openai", "import httpx", "import requests",
        ".place_order(", ".cancel_order(", "route_order(",
    )
    for tok in forbidden:
        assert tok not in src, f"forbidden token in module: {tok}"


def test_no_secret_or_forbidden_language(report):
    text = json.dumps(summarize_stress_report(report), ensure_ascii=False)
    text += render_markdown_report(report)
    for forbidden in ("수익 보장", "실전 전환 승인", "원금 보장", "guaranteed",
                      "kis_app_secret", "access_token", "Bearer ", "sk-ant-",
                      "is_live_authorization\": true"):
        assert forbidden not in text


def test_markdown_required_sections(report):
    md = render_markdown_report(report)
    for section in ("요약", "시나리오별 결과", "슬리피지", "부분체결", "주문 거절",
                    "stale price", "crash / gap", "portfolio drift",
                    "daily loss / kill switch", "실패 시 조치",
                    "실전 전환을 자동 승인하지 않습니다", "미래 수익을 보장하지 않습니다"):
        assert section in md
