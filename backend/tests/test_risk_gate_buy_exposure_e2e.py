"""3-08: 중복 매수·과다 노출 방지 재검증 (Risk Gate E2E).

AGGRESSIVE 성향이어도 중복 매수 / 종목 비중 / 일일 매수금액 / 일일 주문 횟수 /
per-order notional / 최대 보유 종목 수 / 현금 부족 / 1주 가격 초과가 모두 차단되는지
검증. Risk Gate 는 risk_profile 인자를 받지 않으므로(프로파일 무관 하드 한도)
AGGRESSIVE 가 우회할 수 없다. 차단 시 reason_code 가 표준화되고 한국어로 표시된다.

실거래 0건: KIS_IS_PAPER=true / ENABLE_LIVE_TRADING=false. broker 직접 호출 0건.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from app.auto_paper.affordability_check import (
    AffordabilityVerdict,
    check_paper_affordability,
)
from app.auto_paper.blocked_reasons import (
    is_block_code,
    normalize_reason_code,
    title_for,
)
from app.auto_paper.capital_state import check_duplicate_position_buy
from app.kis_paper.auto_executor import (
    KisPaperAutoDecision,
    build_permission_input,
)
from app.kis_paper.auto_permission import evaluate_kis_paper_order_permission
from app.risk.loss_limits import check_daily_buy_limit
from app.risk.position_limits import check_symbol_weight_limit

_ALL_GATES = (check_duplicate_position_buy, check_daily_buy_limit,
              check_symbol_weight_limit, check_paper_affordability)


# ── A. 중복 매수 차단 ──

def test_duplicate_position_blocked():
    r = check_duplicate_position_buy(
        side="BUY", symbol="005930", current_position_quantity=10,
        allow_additional_buy=False)
    assert r.allowed is False
    assert r.reason_code == "DUPLICATE_POSITION_BUY_BLOCKED"


def test_duplicate_allowed_when_opt_in():
    r = check_duplicate_position_buy(
        side="BUY", symbol="005930", current_position_quantity=10,
        allow_additional_buy=True)
    assert r.allowed is True   # opt-in 이라도 cash/daily/weight 는 별도 적용.


def test_duplicate_not_applicable_for_sell():
    # SELL(청산)은 중복 매수 가드 대상 아님.
    r = check_duplicate_position_buy(
        side="SELL", symbol="005930", current_position_quantity=10,
        allow_additional_buy=False)
    assert r.allowed is True
    assert r.reason_code != "DUPLICATE_POSITION_BUY_BLOCKED"


# ── B. 종목 비중 한도 ──

def test_symbol_weight_limit_blocked():
    # equity 1,000,000 × 30% = 300,000 한도. 기보유 250,000 + 신규 1,000,000 초과.
    r = check_symbol_weight_limit(
        side="BUY", symbol="005930", price=10000, quantity=100,
        total_paper_equity=1_000_000, current_symbol_exposure_amount=250_000,
        max_symbol_weight_pct=0.3)
    assert r.allowed is False
    assert r.reason_code == "SYMBOL_WEIGHT_LIMIT_EXCEEDED"


# ── C. 일일 매수금액 한도 ──

def test_daily_buy_limit_blocked():
    r = check_daily_buy_limit(
        side="BUY", price=10000, quantity=100, today_buy_used_amount=2_900_000,
        max_daily_buy_amount=3_000_000)
    assert r.allowed is False
    assert r.reason_code == "DAILY_BUY_LIMIT_EXCEEDED"


# ── D. 일일 주문 횟수 한도 (KIS gate) ──

def _perm(**kw):
    base = dict(
        enable_kis_paper_auto_trading=True, kis_paper_auto_order_dry_run=False,
        kis_is_paper=True, enable_live_trading=False,
        kis_paper_auto_max_order_notional=100_000_000,
        kis_paper_auto_max_orders_per_day=10,
        kis_paper_auto_order_window_start="00:00", kis_paper_auto_order_window_end="23:59",
        kis_paper_auto_min_confidence=0.0, kis_paper_auto_min_quality_score=0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _dec(notional=750_000):
    return KisPaperAutoDecision(symbol="005930", side="BUY", quantity=10,
                                price=notional // 10, has_exit_plan=True,
                                confidence=0.8, quality_score=80)


def test_daily_order_count_limit_blocked():
    from datetime import datetime, timezone
    now = datetime(2026, 5, 22, 4, 0, tzinfo=timezone.utc)
    inp = build_permission_input(
        settings=_perm(), decision=_dec(), broker_is_kis_paper=True,
        credentials_present=True, emergency_stop=False,
        daily_order_count=10, now=now)      # 이미 한도 도달.
    res = evaluate_kis_paper_order_permission(inp)
    assert res.allowed is False
    assert normalize_reason_code(res.reason_code) == "DAILY_ORDER_LIMIT_EXCEEDED"


def test_per_order_notional_limit_blocked():
    from datetime import datetime, timezone
    now = datetime(2026, 5, 22, 4, 0, tzinfo=timezone.utc)
    inp = build_permission_input(
        settings=_perm(kis_paper_auto_max_order_notional=100_000),
        decision=_dec(notional=5_000_000), broker_is_kis_paper=True,
        credentials_present=True, emergency_stop=False, daily_order_count=0, now=now)
    res = evaluate_kis_paper_order_permission(inp)
    assert res.allowed is False
    assert normalize_reason_code(res.reason_code) == "NOTIONAL_LIMIT_EXCEEDED"


# ── E. 최대 보유 종목 수 ──

def test_max_concurrent_positions_blocked():
    r = check_paper_affordability(
        action="BUY", symbol="000660", price=10000,
        available_cash_krw=10_000_000, effective_per_symbol_cap_krw=5_000_000,
        current_held_symbols=["005930", "035720", "068270"],
        max_concurrent_positions=3)         # 신규 4번째 종목 → 차단.
    assert r.verdict == AffordabilityVerdict.MAX_POSITIONS_REACHED
    assert normalize_reason_code(r.verdict.value) == "MAX_POSITIONS_REACHED"


# ── F. 현금 부족 ──

def test_insufficient_cash_blocked():
    r = check_paper_affordability(
        action="BUY", symbol="005930", price=200_000,
        available_cash_krw=100_000, effective_per_symbol_cap_krw=500_000)
    assert r.verdict == AffordabilityVerdict.INSUFFICIENT_CASH
    assert normalize_reason_code(r.verdict.value) == "INSUFFICIENT_PAPER_CASH"


# ── G. 1주 가격이 종목당 투자금 초과 ──

def test_min_lot_not_affordable_blocked():
    r = check_paper_affordability(
        action="BUY", symbol="005930", price=600_000,
        available_cash_krw=10_000_000, effective_per_symbol_cap_krw=500_000)
    # price > cap → PRICE_OVER_CAP → MIN_LOT_NOT_AFFORDABLE.
    assert r.verdict == AffordabilityVerdict.PRICE_OVER_CAP
    assert normalize_reason_code(r.verdict.value) == "MIN_LOT_NOT_AFFORDABLE"


# ── H. AGGRESSIVE 도 Risk Gate 우회 불가 ──

def test_gates_have_no_risk_profile_escape_hatch():
    # 모든 Risk Gate 함수가 risk_profile 인자를 받지 않음 → AGGRESSIVE 가 완화 불가.
    for fn in _ALL_GATES:
        params = set(inspect.signature(fn).parameters)
        assert "risk_profile" not in params, f"{fn.__name__} must not accept risk_profile"
        assert "aggressive" not in params


def test_aggressive_still_blocked_duplicate():
    # AGGRESSIVE 컨텍스트를 가정해도 동일 함수 → 동일 차단.
    r = check_duplicate_position_buy(side="BUY", symbol="005930",
                                     current_position_quantity=10,
                                     allow_additional_buy=False)
    assert r.allowed is False


# ── I. KIS Gate 직전 차단 → route_order 호출 0건 ──

def test_gate_block_no_route_order_call():
    from datetime import datetime, timezone

    from app.brokers.mock_broker import MockBrokerAdapter
    from app.db.models import Base
    from app.kis_paper.auto_executor import execute_kis_paper_auto_order
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    called = {"n": 0}

    async def _route(**kw):
        called["n"] += 1
        return SimpleNamespace(decision=None, reasons=[], audit=SimpleNamespace(id=1))

    try:
        # auto-trading 비활성 → 게이트 차단 → route_order 호출 0건.
        r = asyncio.run(execute_kis_paper_auto_order(
            db, decision=_dec(), settings=_perm(enable_kis_paper_auto_trading=False),
            broker=MockBrokerAdapter(), risk=object(), broker_is_kis_paper=True,
            credentials_present=True, route_order_fn=_route,
            now=datetime(2026, 5, 22, 4, 0, tzinfo=timezone.utc), chain_id="ep-block"))
        db.commit()
        assert r.reason_code != "KIS_PAPER_SUBMITTED"
        assert r.broker_order_sent is False
        assert r.broker_order_no is None
        assert called["n"] == 0
    finally:
        db.close()


# ── J. 차단 사유 한국어 표시 (formatter) ──

@pytest.mark.parametrize("code", [
    "DUPLICATE_POSITION_BUY_BLOCKED", "SYMBOL_WEIGHT_LIMIT_EXCEEDED",
    "DAILY_BUY_LIMIT_EXCEEDED", "DAILY_ORDER_LIMIT_EXCEEDED",
    "NOTIONAL_LIMIT_EXCEEDED", "MAX_POSITIONS_REACHED",
    "INSUFFICIENT_PAPER_CASH", "MIN_LOT_NOT_AFFORDABLE",
])
def test_block_reason_korean_title(code):
    assert is_block_code(code) is True
    title = title_for(code)
    assert title and "매수" in title or "제외" in title or "차단" in title
    # secret/계좌번호 류 단어 없음.
    for bad in ("app_secret", "account_no", "api_key"):
        assert bad not in title.lower()
