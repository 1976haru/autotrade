"""Tests for the futures module skeleton — verify the structure exists,
all broker calls raise NotImplementedError, and the risk manager rejects
every order while ENABLE_FUTURES_LIVE_TRADING is off."""

import asyncio
from datetime import datetime, timezone

import pytest

from app.futures.base import FuturesBrokerAdapter
from app.futures.mock import MockFuturesBroker
from app.futures.risk import (
    FuturesRiskDecision,
    FuturesRiskManager,
    FuturesRiskPolicy,
)
from app.futures.types import (
    FuturesContract,
    FuturesOrderRequest,
    FuturesOrderType,
    FuturesPosition,
    FuturesPositionSide,
    FuturesSide,
)


def run(coro):
    return asyncio.run(coro)


# ---------- types ----------

def test_futures_contract_basic_construction():
    c = FuturesContract(
        code="101W3000", underlying="KOSPI200",
        expiry=datetime(2025, 3, 13, tzinfo=timezone.utc), multiplier=250_000,
    )
    assert c.multiplier == 250_000
    assert c.underlying == "KOSPI200"


def test_futures_order_request_quantity_must_be_positive():
    with pytest.raises(ValueError):
        FuturesOrderRequest(contract="101W3000", side=FuturesSide.BUY, quantity=0)
    with pytest.raises(ValueError):
        FuturesOrderRequest(contract="101W3000", side=FuturesSide.BUY, quantity=-1)


def test_futures_order_request_default_market_order():
    req = FuturesOrderRequest(contract="101W3000", side=FuturesSide.BUY, quantity=1)
    assert req.order_type == FuturesOrderType.MARKET
    assert req.limit_price is None


def test_position_side_distinct_from_order_side():
    pos = FuturesPosition(
        contract="101W3000", side=FuturesPositionSide.LONG, quantity=1,
        entry_price=350, market_price=352, margin_used=100_000,
    )
    assert pos.side == FuturesPositionSide.LONG


# ---------- mock broker — virtual implementation (151) ----------
# 151 이전 stub 단계: 모든 메서드가 NotImplementedError. 151에서 가상 환경
# 구현으로 변경 — 실거래 broker endpoint는 여전히 호출하지 않는다.

def test_mock_broker_implements_protocol():
    assert issubclass(MockFuturesBroker, FuturesBrokerAdapter)


def test_get_quote_returns_virtual_quote():
    q = run(MockFuturesBroker().get_quote("KOSPI200_2503"))
    assert q.contract == "KOSPI200_2503"
    assert q.source   == "mock"
    assert q.price    > 0


def test_get_balance_returns_initial_cash():
    bal = run(MockFuturesBroker(initial_cash=20_000_000).get_balance())
    assert bal.cash == 20_000_000


def test_get_positions_empty_initially():
    assert run(MockFuturesBroker().get_positions()) == []


# ---------- futures risk manager ----------

def _order():
    return FuturesOrderRequest(contract="101W3000", side=FuturesSide.BUY, quantity=1)


def test_risk_default_policy_rejects_all_orders():
    risk = FuturesRiskManager()
    result = risk.evaluate_order(
        order=_order(), positions=[], margin_used=0, margin_available=10_000_000,
    )
    assert result.decision == FuturesRiskDecision.REJECTED
    assert any("ENABLE_FUTURES_LIVE_TRADING" in r for r in result.reasons)


def test_risk_explicit_policy_with_flag_off_rejects():
    risk = FuturesRiskManager(FuturesRiskPolicy(enable_futures_live_trading=False))
    result = risk.evaluate_order(
        order=_order(), positions=[], margin_used=0, margin_available=10_000_000,
    )
    assert result.decision == FuturesRiskDecision.REJECTED


def test_risk_with_flag_on_still_rejects_in_pr_151():
    """151은 live 평가 로직을 활성화하지 않는다 — flag만 켜도 REJECTED.
    실거래 broker 연결은 별도 PR (CLAUDE.md 절대 원칙 6)."""
    risk = FuturesRiskManager(FuturesRiskPolicy(enable_futures_live_trading=True))
    result = risk.evaluate_order(
        order=_order(), positions=[], margin_used=0, margin_available=10_000_000,
    )
    assert result.decision == FuturesRiskDecision.REJECTED


def test_default_policy_values_are_conservative():
    p = FuturesRiskPolicy()
    assert p.max_contracts == 1
    assert p.max_margin_used == 1_000_000
    assert p.max_daily_loss == 200_000
    assert p.enable_futures_live_trading is False
