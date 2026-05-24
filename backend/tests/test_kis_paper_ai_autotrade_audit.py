"""BUILD-02B-0: KIS 모의 AI 자동매매 코드 감사 테스트.

핵심 invariant:
- 18 섹션 감사 (env/universe/market/votes/council/risk/exit/quality/bridge/
  executor/limits/sell/order_quality/portfolio/outcome/ui/live/fake).
- BUY 흐름 연결, HOLD/risk/exit invalid → 주문 decision 미생성, SELL 보유 필요.
- broker_order_type=KIS_PAPER (LIVE 0), is_live_authorization=False.
- 실제 executor 를 fake route_order_fn + paper broker 로 offline 실행(KIS API 0).
- secret 0건, broker/route_order import·호출 0건 (audit 모듈).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.system import kis_paper_ai_autotrade_audit as ka
from app.system.kis_paper_ai_autotrade_audit import (
    ASection,
    AVerdict,
    KisPaperAuditInputs,
    KisPaperAuditReport,
    render_markdown_report,
    run_kis_paper_ai_autotrade_audit,
    summarize,
)


@pytest.fixture(scope="module")
def report() -> KisPaperAuditReport:
    return run_kis_paper_ai_autotrade_audit()


def _sec(report, name):
    return next((s for s in report.sections if s.section == name), None)


# ── 섹션 점검 ────────────────────────────────────────────────────────────────


def test_all_sections_present(report):
    names = {s.section for s in report.sections}
    for sec in ASection:
        assert sec.value in names, f"missing section {sec.value}"


@pytest.mark.parametrize("section", [
    "ENV_READINESS", "UNIVERSE", "MARKET_DATA_CONTRACT", "STRATEGY_VOTES",
    "AGENT_COUNCIL", "RISK_OFFICER", "EXIT_PLAN", "QUALITY_GATE",
    "PAPER_DECISION_BRIDGE", "PAPER_ORDER_EXECUTOR", "BUY_SELL_LIMITS",
    "SELL_TRIGGER", "ORDER_RESULT_QUALITY", "PORTFOLIO_APPLICATION",
    "OUTCOME_REVIEW_FEEDBACK", "LIVE_SAFETY", "FAKE_FLOWS",
])
def test_required_sections_pass(report, section):
    s = _sec(report, section)
    assert s.verdict in (AVerdict.PASS.value, AVerdict.WARN.value), \
        f"{section} verdict={s.verdict}"


def test_overall_pass_and_ready(report):
    assert report.counts.get("FAIL", 0) == 0
    assert report.paper_autotrade_ready is True
    assert report.overall_verdict in (AVerdict.PASS.value, AVerdict.WARN.value)


# ── 핵심 감사 invariant ──────────────────────────────────────────────────────


def test_executor_contract_kis_paper_only(report):
    s = _sec(report, "PAPER_ORDER_EXECUTOR")
    assert s.verdict == AVerdict.PASS.value
    # LIVE broker_order_type 생성 차단 항목 PASS.
    live_blocked = next((i for i in s.items if i.name == "live_type_blocked"), None)
    assert live_blocked and live_blocked.verdict == AVerdict.PASS.value


def test_buy_sell_limits_all_block(report):
    s = _sec(report, "BUY_SELL_LIMITS")
    assert s.verdict == AVerdict.PASS.value
    # 각 위반이 차단 + happy path allowed.
    names = {i.name for i in s.items}
    for n in ("low_confidence", "missing_exit_plan", "notional_over", "daily_over",
              "allowed_path"):
        assert n in names


def test_sell_trigger_naked_blocked(report):
    s = _sec(report, "SELL_TRIGGER")
    naked = next((i for i in s.items if i.name == "naked_sell_blocked"), None)
    assert naked and naked.verdict == AVerdict.PASS.value


def test_fake_flows_no_order_on_hold(report):
    s = _sec(report, "FAKE_FLOWS")
    assert s.verdict == AVerdict.PASS.value


def test_live_safety_pass(report):
    s = _sec(report, "LIVE_SAFETY")
    assert s.verdict == AVerdict.PASS.value


# ── rehearsal readiness ──────────────────────────────────────────────────────


def test_credentials_unknown_not_rehearsal_ready(report):
    assert report.ready_for_market_open_rehearsal is False


def test_credentials_present_rehearsal_ready():
    r = run_kis_paper_ai_autotrade_audit(KisPaperAuditInputs(kis_credentials_present=True))
    assert r.ready_for_market_open_rehearsal is True
    assert r.paper_autotrade_ready is True


def test_live_flag_on_fails_env():
    r = run_kis_paper_ai_autotrade_audit(KisPaperAuditInputs(enable_live_trading=True))
    assert _sec(r, "ENV_READINESS").verdict == AVerdict.FAIL.value
    assert r.paper_autotrade_ready is False


# ── invariants / secret / import 가드 ────────────────────────────────────────


def test_report_invariants(report):
    assert report.is_live_authorization is False
    assert report.broker_order_sent is False
    assert report.order_created is False
    assert report.contains_secret is False


def test_report_invariant_enforced():
    with pytest.raises(ValueError):
        KisPaperAuditReport(
            generated_at="x", sections=(), counts={}, overall_verdict="PASS",
            paper_autotrade_ready=True, ready_for_market_open_rehearsal=False,
            fail_items=(), warn_items=(), reason_codes=(), is_live_authorization=True)


def test_no_secret_in_report(report):
    text = json.dumps(summarize(report), ensure_ascii=False) + render_markdown_report(report)
    for forbidden in ("kis_app_secret", "access_token", "Bearer ", "sk-ant-",
                      "is_live_authorization\": true"):
        assert forbidden not in text


def test_no_forbidden_imports():
    src = open(ka.__file__, encoding="utf-8").read()
    for tok in ("from app.brokers.kis", "from app.brokers.mock_broker",
                "from app.execution.order_router", "from app.execution.executor",
                "from app.execution.order_executor",
                "import anthropic", "import openai", "import httpx", "import requests",
                ".place_order(", "route_order(", "db.add(", "db.commit("):
        assert tok not in src, f"forbidden token: {tok}"


def test_markdown_required_sections(report):
    md = render_markdown_report(report)
    for sec in ("전체 요약", "paper_autotrade_ready", "ready_for_market_open_rehearsal",
                "섹션별 감사", "BUILD-02B", "실전 승인 아님", "수익 보장 아님"):
        assert sec in md


# ── 실제 executor offline 실행 (fake route_order_fn + paper broker, KIS API 0) ──


def test_executor_offline_with_fake_route_order():
    """execute_kis_paper_auto_order 를 fake route_order_fn + Mock paper broker 로 실행."""
    from types import SimpleNamespace

    from app.brokers.mock_broker import MockBrokerAdapter
    from app.kis_paper.auto_executor import (
        KisPaperAutoDecision, execute_kis_paper_auto_order,
    )

    calls = {"n": 0}

    def fake_route_order(order, **kw):
        calls["n"] += 1
        # 실제 broker/route_order 미호출 — APPROVED 합성.
        return SimpleNamespace(decision="APPROVED", audit_id=1,
                               broker_order_no="PAPER-SIM-1", reasons=[])

    decision = KisPaperAutoDecision(
        symbol="005930", side="BUY", quantity=10, price=72_000,
        selected_strategies=["MOMENTUM"], confidence=0.8, quality_score=80,
        has_exit_plan=True, exit_plan={"stop_loss_pct": 2.0, "take_profit_pct": 3.0})

    # dry_run=True → route_order 미호출, KIS_PAPER_DRY_RUN_OK.
    settings = SimpleNamespace(
        enable_kis_paper_auto_trading=True, kis_paper_auto_order_dry_run=True,
        kis_is_paper=True, enable_live_trading=False,
        kis_paper_auto_max_orders_per_day=10, kis_paper_auto_max_order_notional=1_000_000,
        kis_paper_auto_order_window_start="00:00", kis_paper_auto_order_window_end="23:59",
        kis_paper_auto_min_confidence=0.6, kis_paper_auto_min_quality_score=60)

    class _NoDB:
        def query(self, *a, **k):
            class _Q:
                def filter(self, *a, **k): return self
                def count(self): return 0
                def all(self): return []
            return _Q()

    res = asyncio.run(execute_kis_paper_auto_order(
        _NoDB(), decision=decision, settings=settings, broker=MockBrokerAdapter(),
        risk=SimpleNamespace(), broker_is_kis_paper=True, credentials_present=True,
        route_order_fn=fake_route_order, record=False))
    # dry_run → 합성 route_order 호출 0, KIS API 0.
    assert res.broker_order_type == "KIS_PAPER"
    assert res.is_live_authorization is False
    assert calls["n"] == 0   # dry_run 이라 route_order_fn 미호출.
    assert res.reason_code in ("KIS_PAPER_DRY_RUN_OK", "MARKET_CLOSED",
                               "KIS_PAPER_ORDER_WINDOW_CLOSED", "BLOCKED_BY_RISK_MANAGER")


# ── API ──────────────────────────────────────────────────────────────────────


def test_api_audit(client):
    r = client.get("/api/system/kis-paper-autotrade-audit")
    assert r.status_code == 200
    body = r.json()
    assert "sections" in body and len(body["sections"]) >= 17
    assert body["is_live_authorization"] is False
    assert body["broker_order_sent"] is False
    assert "paper_autotrade_ready" in body


def test_api_no_broker_order(client):
    client.get("/api/system/kis-paper-autotrade-audit")
    assert len(client.test_broker.orders) == 0
