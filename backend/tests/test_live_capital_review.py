"""#72 / 9-03: Live Capital Review 테스트 (주문 승인 아님).

핵심 invariant:
- operator approval + max_order_notional + daily_live_limit + symbol whitelist
  없이는 READY 아님 (INCOMPLETE).
- 모두 충족 → READY (검토 readiness — 주문 생성 아님).
- review 기록해도 order_created/broker_order_sent/is_live_authorization=False.
- Paper 자금이 live 로 사용되지 않음 (분리).
"""

from __future__ import annotations

import json

import pytest

from app.permission import live_capital_review as lcr
from app.permission.live_capital_review import (
    REVIEW_INCOMPLETE,
    REVIEW_MISSING,
    REVIEW_READY,
    build_live_capital_review,
)
from app.permission.live_manual_approval_gate import LiveManualApprovalInput


def _full_live_input(**over):
    base = dict(
        mode="LIVE_MANUAL_APPROVAL", live_order_requested=True,
        broker_order_type="KIS_LIVE", kis_is_paper=False, enable_live_trading=True,
        live_capital_review_approved=True, manual_approval_present=True,
        operator_name="operator-1", operator_reason="reviewed live capital",
        operator_approved_at="2026-05-24T00:00:00Z", symbol="005930",
        symbol_whitelist=("005930",), max_order_notional_configured=True,
        daily_live_limit_configured=True,
    )
    base.update(over)
    return LiveManualApprovalInput(**base)


# ── MISSING (비-실전) ────────────────────────────────────────────────────────


def test_paper_request_review_missing():
    r = build_live_capital_review(LiveManualApprovalInput(mode="PAPER", kis_is_paper=True))
    assert r.approval_status == REVIEW_MISSING
    assert r.review_present is False
    assert r.order_created is False


# ── INCOMPLETE (필수 조건 누락) ──────────────────────────────────────────────


def test_missing_operator_approval_incomplete():
    r = build_live_capital_review(_full_live_input(operator_name=None,
                                                   manual_approval_present=False))
    assert r.approval_status == REVIEW_INCOMPLETE
    assert r.requirements["operator_approval"] is False


def test_missing_whitelist_incomplete():
    r = build_live_capital_review(_full_live_input(symbol_whitelist=()))
    assert r.approval_status == REVIEW_INCOMPLETE
    assert r.requirements["symbol_whitelist"] is False
    assert r.symbol_whitelist_count == 0


def test_missing_max_notional_incomplete():
    r = build_live_capital_review(_full_live_input(max_order_notional_configured=False))
    assert r.approval_status == REVIEW_INCOMPLETE
    assert r.requirements["max_order_notional"] is False


def test_missing_daily_limit_incomplete():
    r = build_live_capital_review(_full_live_input(daily_live_limit_configured=False))
    assert r.approval_status == REVIEW_INCOMPLETE
    assert r.requirements["daily_live_limit"] is False


# ── READY (모두 충족) — 단, 주문 생성 0건 ────────────────────────────────────


def test_full_review_ready_but_no_order():
    r = build_live_capital_review(_full_live_input())
    assert r.approval_status == REVIEW_READY
    assert all(r.requirements.values())
    assert r.max_order_notional_configured is True
    assert r.daily_live_limit_configured is True
    assert r.symbol_whitelist_count == 1
    assert r.operator_approval_present is True
    # 검토 readiness 일 뿐 — 주문/권한 0.
    assert r.order_created is False
    assert r.broker_order_sent is False
    assert r.is_live_authorization is False


def test_paper_capital_separated():
    r = build_live_capital_review(_full_live_input())
    assert r.paper_capital_separated is True


# ── invariants ───────────────────────────────────────────────────────────────


def test_review_invariant_enforced():
    with pytest.raises(ValueError):
        lcr.LiveCapitalReview(
            review_present=True, approval_status=REVIEW_READY, reason_code="X",
            reason_message="", requirements={}, max_order_notional_configured=True,
            daily_live_limit_configured=True, symbol_whitelist_count=1,
            operator_approval_present=True, paper_capital_separated=True,
            is_live_authorization=True)


def test_no_secret_in_review():
    text = json.dumps(build_live_capital_review(_full_live_input()).to_dict(),
                      ensure_ascii=False)
    for forbidden in ("kis_app_secret", "access_token", "Bearer ", "sk-ant-",
                      "app_key", "app_secret"):
        assert forbidden not in text


def test_no_forbidden_imports():
    src = open(lcr.__file__, encoding="utf-8").read()
    for tok in ("from app.brokers", "from app.execution.order_router",
                "from app.execution.executor", "from app.execution.paper_trader",
                "import anthropic", "import openai", "import httpx", "import requests",
                ".place_order(", "route_order(", "db.add(", "db.commit("):
        assert tok not in src, f"forbidden token: {tok}"


# ── API ──────────────────────────────────────────────────────────────────────


def test_api_review_block(client):
    body = client.get("/api/status/live-safety").json()
    rv = body["live_capital_review"]
    # 기본(운영자 입력 없음) → MISSING.
    assert rv["approval_status"] == REVIEW_MISSING
    assert rv["is_live_authorization"] is False
    assert rv["order_created"] is False
    assert rv["broker_order_sent"] is False
