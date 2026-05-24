"""5-03: 실전 Canary Gate 테스트.

핵심 invariant:
- 기본 차단. Paper Gate / Manual Approval / Operator / Whitelist / 1일1건 /
  최소·최대 주문금액 / daily notional / risk profile / window 중 하나라도 없으면 차단
- LIVE_AI_EXECUTION 은 canary gate 전 불가
- 모든 조건 충족(canary_ready=True)이라도 order_created/broker_order_sent/
  is_live_authorization=False, broker_order_no=None
- Paper/비-live 경로 무회귀(NOT_A_LIVE_ORDER)
"""

from __future__ import annotations

import pytest

from app.core.modes import OperationMode
from app.permission import live_canary_gate as c
from app.permission.live_canary_gate import (
    LiveCanaryGateInput as Inp,
    evaluate_live_canary_gate as ev,
)


def _full_ok(**over) -> Inp:
    base = dict(
        mode=OperationMode.LIVE_MANUAL_APPROVAL,
        live_order_requested=True,
        broker_order_type="KIS_LIVE",
        kis_is_paper=False,
        enable_live_trading=True,
        enable_ai_execution=False,
        # #42 manual approval 전제.
        live_capital_review_approved=True,
        manual_approval_present=True,
        operator_name="op-1",
        operator_reason="canary review",
        operator_approved_at="2026-05-24T01:00:00+00:00",
        symbol="005930",
        symbol_whitelist=("005930",),
        max_order_notional_configured=True,
        daily_live_limit_configured=True,
        # Paper Gate 전제.
        paper_gate_passed=True,
        can_review_live_canary=True,
        # canary 한도.
        daily_order_count_limit=1,
        today_live_order_count=0,
        min_order_notional_krw=10_000,
        max_order_notional_krw=20_000,
        daily_live_notional_limit_krw=20_000,
        canary_risk_profile="CONSERVATIVE",
        canary_window_active=True,
    )
    base.update(over)
    return Inp(**base)


# ── manual approval 전제 미충족 → 차단 ───────────────────────────────────────


def test_manual_gate_blocks_propagate():
    # capital review 없음 → manual gate reason 그대로 노출, canary 차단.
    res = ev(_full_ok(live_capital_review_approved=False))
    assert res.canary_ready is False
    assert res.manual_gate_passed is False
    assert res.reason_code == "LIVE_CAPITAL_REVIEW_REQUIRED"


def test_missing_operator_blocks():
    res = ev(_full_ok(operator_name=None))
    assert res.canary_ready is False
    assert res.reason_code == "OPERATOR_APPROVAL_REQUIRED"


def test_symbol_not_whitelisted_blocks():
    res = ev(_full_ok(symbol="000660", symbol_whitelist=("005930",)))
    assert res.canary_ready is False
    assert res.reason_code == "SYMBOL_NOT_WHITELISTED"


# ── Paper Gate 전제 ──────────────────────────────────────────────────────────


def test_paper_gate_not_passed_blocks():
    res = ev(_full_ok(paper_gate_passed=False))
    assert res.canary_ready is False
    assert res.reason_code == c.CANARY_PAPER_GATE_REQUIRED


def test_cannot_review_canary_blocks():
    res = ev(_full_ok(can_review_live_canary=False))
    assert res.reason_code == c.CANARY_REVIEW_NOT_AVAILABLE


# ── LIVE_AI_EXECUTION 불가 ───────────────────────────────────────────────────


def test_ai_execution_blocked():
    res = ev(_full_ok(enable_ai_execution=True))
    assert res.canary_ready is False
    assert res.reason_code == c.CANARY_AI_EXECUTION_BLOCKED
    assert res.enable_ai_execution_allowed is False


# ── 1일 1건 제한 ─────────────────────────────────────────────────────────────


def test_daily_order_limit_required():
    res = ev(_full_ok(daily_order_count_limit=0))
    assert res.reason_code == c.CANARY_DAILY_ORDER_LIMIT_REQUIRED


def test_daily_order_limit_must_be_one():
    res = ev(_full_ok(daily_order_count_limit=5))
    assert res.reason_code == c.CANARY_DAILY_ORDER_LIMIT_REQUIRED


def test_daily_order_limit_exceeded():
    res = ev(_full_ok(daily_order_count_limit=1, today_live_order_count=1))
    assert res.reason_code == c.CANARY_DAILY_ORDER_LIMIT_EXCEEDED


# ── 최소/최대 주문금액 + daily notional ──────────────────────────────────────


def test_min_notional_required():
    res = ev(_full_ok(min_order_notional_krw=0))
    assert res.reason_code == c.CANARY_MIN_ORDER_NOTIONAL_REQUIRED


def test_max_notional_required():
    res = ev(_full_ok(max_order_notional_krw=0))
    assert res.reason_code == c.CANARY_MAX_ORDER_NOTIONAL_REQUIRED


def test_max_notional_below_min_blocks():
    res = ev(_full_ok(min_order_notional_krw=20_000, max_order_notional_krw=10_000))
    assert res.reason_code == c.CANARY_MAX_ORDER_NOTIONAL_REQUIRED


def test_max_notional_too_high_blocks():
    res = ev(_full_ok(max_order_notional_krw=c.CANARY_MAX_ALLOWED_ORDER_NOTIONAL_KRW + 1))
    assert res.reason_code == c.CANARY_MAX_ORDER_NOTIONAL_TOO_HIGH


def test_daily_notional_limit_required():
    res = ev(_full_ok(daily_live_notional_limit_krw=0))
    assert res.reason_code == c.CANARY_DAILY_NOTIONAL_LIMIT_REQUIRED


# ── risk profile + window ────────────────────────────────────────────────────


def test_risk_profile_required():
    res = ev(_full_ok(canary_risk_profile="AGGRESSIVE"))
    assert res.reason_code == c.CANARY_RISK_PROFILE_REQUIRED


def test_window_required():
    res = ev(_full_ok(canary_window_active=False))
    assert res.reason_code == c.CANARY_WINDOW_REQUIRED


# ── 기본 차단 (빈 입력) ──────────────────────────────────────────────────────


def test_default_live_request_blocked():
    res = ev(Inp(live_order_requested=True))
    assert res.canary_ready is False
    assert res.is_live_authorization is False


# ── 모든 조건 충족 — readiness 이되 주문 0건 ─────────────────────────────────


def test_all_conditions_met_review_ready_no_order():
    res = ev(_full_ok())
    assert res.canary_ready is True
    assert res.reason_code == c.CANARY_REVIEW_READY
    assert res.paper_gate_passed is True
    assert res.manual_gate_passed is True
    # 가장 중요한 안전 invariant.
    assert res.is_live_authorization is False
    assert res.broker_order_sent is False
    assert res.order_created is False
    assert res.broker_order_no is None
    assert res.enable_ai_execution_allowed is False


# ── Paper/비-live 무회귀 ─────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", [
    OperationMode.SIMULATION, OperationMode.PAPER, OperationMode.LIVE_SHADOW,
])
def test_paper_modes_not_a_live_order(mode):
    res = ev(Inp(mode=mode, broker_order_type="KIS_PAPER", kis_is_paper=True))
    assert res.reason_code == c.NOT_A_LIVE_ORDER
    assert res.canary_ready is False
    assert res.is_live_authorization is False


# ── 불변 가드 ────────────────────────────────────────────────────────────────


def test_result_invariants_enforced():
    from app.permission.live_canary_gate import LiveCanaryGateResult as R
    with pytest.raises(ValueError):
        R(canary_ready=True, reason_code="X", reason_message="x", mode="LIVE",
          paper_gate_passed=True, manual_gate_passed=True,
          is_live_authorization=True)
    with pytest.raises(ValueError):
        R(canary_ready=True, reason_code="X", reason_message="x", mode="LIVE",
          paper_gate_passed=True, manual_gate_passed=True, broker_order_no="K-1")


def test_to_dict_safe_only():
    d = ev(_full_ok()).to_dict()
    for k in ("is_live_authorization", "broker_order_sent", "order_created",
              "enable_ai_execution_allowed", "contains_secret"):
        assert d[k] is False
    assert d["broker_order_no"] is None
    assert "operator_name" not in d
    assert "kis_app_secret" not in d


def test_no_broker_imports_in_module():
    import app.permission.live_canary_gate as mod
    src = open(mod.__file__, encoding="utf-8").read()
    for forbidden in ("from app.brokers", "import app.brokers",
                      "from app.execution", "import app.execution",
                      ".place_order(", "route_order(",
                      "import httpx", "import requests", "import anthropic"):
        assert forbidden not in src, f"forbidden: {forbidden}"
