"""5-02: 실전 주문 = manual approval 전용 Gate 테스트.

핵심 invariant:
- ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION 만으로는 절대 허용되지 않음
- Live Capital Review / Manual Approval / Operator Approval / Symbol Whitelist /
  Max notional / Daily live limit 중 하나라도 없으면 차단
- Paper 승인/자금은 live 승인으로 인정 안 됨
- 모든 조건 충족(approved=True)이라도 is_live_authorization / broker_order_sent /
  order_created 는 항상 False, broker_order_no 는 None
- Paper / 비-live 경로는 차단하지 않음 (NOT_A_LIVE_ORDER)
"""

from __future__ import annotations

import pytest

from app.core.modes import OperationMode
from app.permission import live_manual_approval_gate as g
from app.permission.live_manual_approval_gate import (
    LiveManualApprovalInput as Inp,
    evaluate_live_manual_approval_gate as ev,
)


def _full_ok(**over) -> Inp:
    """모든 조건이 충족된 실전 주문 입력 (override 로 하나씩 빼서 차단 검증)."""
    base = dict(
        mode=OperationMode.LIVE_MANUAL_APPROVAL,
        live_order_requested=True,
        broker_order_type="KIS_LIVE",
        kis_is_paper=False,
        enable_live_trading=True,
        enable_ai_execution=False,
        live_capital_review_approved=True,
        manual_approval_present=True,
        operator_name="op-1",
        operator_reason="manual live review",
        operator_approved_at="2026-05-24T01:00:00+00:00",
        symbol="005930",
        symbol_whitelist=("005930",),
        max_order_notional_configured=True,
        daily_live_limit_configured=True,
        paper_approval_reused_as_live=False,
    )
    base.update(over)
    return Inp(**base)


# ── 단순 flag 만으로는 불가 ───────────────────────────────────────────────────


def test_enable_live_trading_only_is_blocked():
    inp = Inp(mode=OperationMode.LIVE_MANUAL_APPROVAL, live_order_requested=True,
              broker_order_type="KIS_LIVE", kis_is_paper=False,
              enable_live_trading=True)
    res = ev(inp)
    assert res.approved is False
    assert res.reason_code == g.LIVE_CAPITAL_REVIEW_REQUIRED
    assert res.is_live_authorization is False
    assert res.broker_order_sent is False
    assert res.order_created is False
    assert res.broker_order_no is None


def test_enable_ai_execution_only_is_blocked():
    inp = Inp(mode=OperationMode.LIVE_AI_EXECUTION, live_order_requested=True,
              broker_order_type="KIS_LIVE", kis_is_paper=False,
              enable_ai_execution=True)
    res = ev(inp)
    assert res.approved is False
    assert res.is_live_authorization is False


# ── 필수 조건별 차단 ─────────────────────────────────────────────────────────


def test_missing_live_capital_review_blocked():
    res = ev(_full_ok(live_capital_review_approved=False))
    assert res.approved is False
    assert res.reason_code == g.LIVE_CAPITAL_REVIEW_REQUIRED


def test_missing_manual_approval_blocked():
    res = ev(_full_ok(manual_approval_present=False))
    assert res.reason_code == g.LIVE_MANUAL_APPROVAL_REQUIRED


def test_missing_operator_approval_blocked():
    res = ev(_full_ok(operator_name=None))
    assert res.reason_code == g.OPERATOR_APPROVAL_REQUIRED
    res2 = ev(_full_ok(operator_approved_at=None))
    assert res2.reason_code == g.OPERATOR_APPROVAL_REQUIRED


def test_missing_symbol_whitelist_blocked():
    res = ev(_full_ok(symbol_whitelist=()))
    assert res.reason_code == g.SYMBOL_WHITELIST_REQUIRED


def test_symbol_not_in_whitelist_blocked():
    res = ev(_full_ok(symbol="000660", symbol_whitelist=("005930",)))
    assert res.reason_code == g.SYMBOL_NOT_WHITELISTED


def test_missing_max_order_notional_blocked():
    res = ev(_full_ok(max_order_notional_configured=False))
    assert res.reason_code == g.MAX_ORDER_NOTIONAL_REQUIRED


def test_missing_daily_live_limit_blocked():
    res = ev(_full_ok(daily_live_limit_configured=False))
    assert res.reason_code == g.DAILY_LIVE_LIMIT_REQUIRED


def test_default_empty_input_is_blocked():
    # 아무 조건도 없는 실전 요청 → 차단.
    res = ev(Inp(live_order_requested=True))
    assert res.approved is False
    assert res.is_live_authorization is False


# ── Paper 재사용 금지 ────────────────────────────────────────────────────────


def test_paper_approval_reuse_blocked():
    res = ev(_full_ok(paper_approval_reused_as_live=True))
    assert res.reason_code == g.PAPER_APPROVAL_NOT_LIVE_APPROVAL
    assert res.approved is False


def test_paper_capital_in_payload_blocked():
    res = ev(_full_ok(order_payload={"total_paper_capital": 10_000_000}))
    assert res.reason_code == g.PAPER_APPROVAL_NOT_LIVE_APPROVAL
    assert res.detected_paper_fields == ["total_paper_capital"]


# ── 모든 조건 충족 — 검토 readiness 이되 주문은 생성 안 됨 ────────────────────


def test_all_conditions_met_review_ready_but_no_order():
    res = ev(_full_ok())
    assert res.approved is True
    assert res.reason_code == g.LIVE_MANUAL_GATE_REVIEW_READY
    # 가장 중요한 안전 invariant: gate 통과여도 주문 0건.
    assert res.is_live_authorization is False
    assert res.broker_order_sent is False
    assert res.order_created is False
    assert res.broker_order_no is None


# ── Paper / 비-live 경로 무회귀 ──────────────────────────────────────────────


@pytest.mark.parametrize("mode", [
    OperationMode.SIMULATION, OperationMode.PAPER, OperationMode.LIVE_SHADOW,
])
def test_paper_modes_not_a_live_order(mode):
    inp = Inp(mode=mode, broker_order_type="KIS_PAPER", kis_is_paper=True)
    res = ev(inp)
    assert res.reason_code == g.NOT_A_LIVE_ORDER
    assert res.approved is False
    assert res.is_live_authorization is False


def test_kis_paper_with_paper_capital_not_blocked_as_live():
    # PAPER 모드 + paper capital → live gate 가 간섭하지 않음.
    inp = Inp(mode=OperationMode.PAPER, kis_is_paper=True,
              order_payload={"total_paper_capital": 10_000_000})
    res = ev(inp)
    assert res.reason_code == g.NOT_A_LIVE_ORDER


# ── 불변 가드 (dataclass __post_init__) ──────────────────────────────────────


def test_result_invariants_enforced():
    from app.permission.live_manual_approval_gate import LiveManualApprovalResult as R
    with pytest.raises(ValueError):
        R(approved=True, reason_code="X", reason_message="x", mode="LIVE",
          requires_live_capital_review=True, requires_manual_approval=True,
          requires_operator_approval=True, requires_symbol_whitelist=True,
          requires_max_order_notional=True, requires_daily_live_limit=True,
          is_live_authorization=True)  # 위반 → ValueError
    with pytest.raises(ValueError):
        R(approved=True, reason_code="X", reason_message="x", mode="LIVE",
          requires_live_capital_review=True, requires_manual_approval=True,
          requires_operator_approval=True, requires_symbol_whitelist=True,
          requires_max_order_notional=True, requires_daily_live_limit=True,
          broker_order_no="K-1")  # 위반 → ValueError


def test_to_dict_safe_fields_only():
    d = ev(_full_ok()).to_dict()
    assert d["is_live_authorization"] is False
    assert d["broker_order_sent"] is False
    assert d["order_created"] is False
    assert d["broker_order_no"] is None
    assert d["contains_secret"] is False
    # operator 이름 등 secret-유사 원문이 dict 에 실리지 않음.
    assert "operator_name" not in d
    assert "kis_app_secret" not in d


def test_no_broker_imports_in_module():
    # 실제 import 문 / 호출 패턴만 검사 (docstring 의 설명 산문은 제외).
    import app.permission.live_manual_approval_gate as mod
    src = open(mod.__file__, encoding="utf-8").read()
    for forbidden in ("from app.brokers", "import app.brokers",
                      "from app.execution", "import app.execution",
                      ".place_order(", "route_order(",
                      "import httpx", "import requests", "import anthropic"):
        assert forbidden not in src, f"forbidden import/call: {forbidden}"
