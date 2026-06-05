"""PART1-4: SELL 보유 가드 + net 보유 계산 회귀 테스트.

2026-06-01 첫 실전 모의 버그:
  - held_symbols 가 "오늘 BUY 한 종목" 전부를 잡고 SELL 청산을 빼지 않아,
    청산된 005380 이 계속 보유로 잡혀 SELL 신호가 broker 로 전송 → KIS
    "잔고부족" 거부 3건.

본 테스트가 보장:
  1. _today_kis_paper_buy_state 가 net(BUY−SELL) 보유만 반환 (REJECTED 제외).
  2. SELL 신호인데 net 보유 0 이면 broker 로 가지 않고 SELL_NO_HELD_POSITION skip.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.agent_council import StrategyMarketInput
from app.brokers.kis import KisBrokerAdapter
from app.db.base import Base
from app.db.models import OrderAuditLog
from app.kis_paper.driver_bridge import (
    _kis_held_symbols,
    _today_kis_paper_buy_state,
    kis_paper_realtime_scan_tick,
)
from app.market_data.kis_realtime import KIS_PRICE_OK, KisRealtimeQuote
from app.risk.risk_manager import RiskDecision

OPEN_TIME = datetime(2026, 5, 27, 5, 0, 0, tzinfo=timezone.utc)  # 14:00 KST Wed


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return eng


def _add_order(db, *, symbol, side, qty, broker_status="RECEIVED", price=10000):
    db.add(OrderAuditLog(
        created_at=OPEN_TIME, mode="PAPER", requested_by_ai=False,
        symbol=symbol, side=side, quantity=qty, order_type="MARKET",
        decision="APPROVED", executed=True, broker_order_id="X",
        broker_status=broker_status, filled_quantity=0,
        limit_price=price, latest_price=price,
        trade_reason="kis_paper_auto", strategy="VWAP",
    ))


# ───────────────────────── net holding 계산 ─────────────────────────

class _FakePosBroker:
    def __init__(self, positions=None, raise_error=None):
        self._positions = positions or []
        self._raise = raise_error

    async def get_positions(self):
        if self._raise is not None:
            raise self._raise
        return self._positions


def test_kis_held_symbols_from_broker_positions():
    # 보유 = KIS 잔고(브로커 진실). 전일 캐리오버(DB 무관)도 잡는다. qty<=0 제외.
    broker = _FakePosBroker([
        SimpleNamespace(symbol="071050", quantity=1),   # orphan/캐리오버
        SimpleNamespace(symbol="005935", quantity=4),
        SimpleNamespace(symbol="000270", quantity=0),    # 0 → 제외
    ])
    held = asyncio.run(_kis_held_symbols(broker, fallback=set()))
    assert held == {"071050", "005935"}


def test_kis_held_symbols_trusts_empty_kis():
    # 조회 성공 + 빈 결과 → 실제 무보유로 신뢰 (fallback 무시).
    assert asyncio.run(_kis_held_symbols(_FakePosBroker([]), fallback={"005930"})) == set()


def test_kis_held_symbols_fallback_on_failure():
    # 조회 실패 → 보수적으로 fallback(오늘 DB net) 사용.
    broker = _FakePosBroker(raise_error=RuntimeError("no creds"))
    assert asyncio.run(_kis_held_symbols(broker, fallback={"005930"})) == {"005930"}


def test_kis_held_symbols_no_broker_uses_fallback():
    assert asyncio.run(_kis_held_symbols(None, fallback={"x"})) == {"x"}


def test_net_holding_excludes_fully_sold_symbol(engine):
    Session = sessionmaker(bind=engine)
    db = Session()
    # 005380: BUY 1 → SELL 1 (전량 청산) → net 0 → held 에 없어야.
    _add_order(db, symbol="005380", side="BUY", qty=1)
    _add_order(db, symbol="005380", side="SELL", qty=1)
    # 035420: BUY 4, SELL 없음 → net 4 → held.
    _add_order(db, symbol="035420", side="BUY", qty=4)
    db.commit()
    held, used = _today_kis_paper_buy_state(db, OPEN_TIME)
    assert "005380" not in held       # 청산됨 → 보유 아님
    assert "035420" in held           # 보유 중


def test_net_holding_ignores_rejected_sell(engine):
    Session = sessionmaker(bind=engine)
    db = Session()
    # BUY 5 정상, SELL 5 는 REJECTED(잔고부족 시뮬) → net 은 여전히 5 (보유).
    _add_order(db, symbol="000270", side="BUY", qty=5)
    _add_order(db, symbol="000270", side="SELL", qty=5, broker_status="REJECTED")
    db.commit()
    held, _ = _today_kis_paper_buy_state(db, OPEN_TIME)
    assert "000270" in held           # REJECTED SELL 은 보유를 줄이지 않음


def test_net_holding_partial_sell_still_held(engine):
    Session = sessionmaker(bind=engine)
    db = Session()
    _add_order(db, symbol="068270", side="BUY", qty=5)
    _add_order(db, symbol="068270", side="SELL", qty=2)   # 일부 청산 → net 3
    db.commit()
    held, _ = _today_kis_paper_buy_state(db, OPEN_TIME)
    assert "068270" in held


# ───────────────────────── SELL 가드 (보유 0 차단) ─────────────────────────

def _sell_input(symbol):
    # 보유 청산 신호를 유도하는 약세 input (council 이 SELL 쪽으로).
    return StrategyMarketInput(
        symbol=symbol, current_price=9200.0, prev_close=10000.0, open_price=9900.0,
        vwap=9800.0, opening_range_high=10100.0, opening_range_low=9500.0,
        recent_closes=(10000.0, 9800.0, 9600.0, 9400.0, 9200.0),
        current_volume=200.0, avg_volume=100.0,
        market_regime="TREND_DOWN", regime_decision="ALLOW",
    )


async def _sell_input_fn(symbol, *, client, now, market_is_open, **kw):
    return _sell_input(symbol), KisRealtimeQuote(
        symbol=symbol, status=KIS_PRICE_OK, price=9200.0, is_stale=False)


def _route_should_not_be_called():
    async def _fn(**kw):
        raise AssertionError("route_order must NOT be called for SELL with no holding")
    return _fn


def _scan_settings(**kw):
    base = dict(
        market_data_provider="kis", enable_kis_paper_auto_trading=True,
        kis_paper_auto_order_dry_run=False, kis_is_paper=True, enable_live_trading=False,
        kis_paper_auto_max_order_notional=1_000_000, kis_paper_auto_max_orders_per_day=10,
        kis_paper_auto_order_window_start="09:05", kis_paper_auto_order_window_end="14:50",
        kis_paper_auto_min_confidence=0.6, kis_paper_auto_min_quality_score=60,
        kis_paper_smoke_mode=False, kis_paper_smoke_symbol="005930", kis_paper_smoke_qty=1,
        kis_paper_max_concurrent_positions=5, kis_paper_per_symbol_notional_krw=1_000_000,
        kis_paper_daily_buy_limit_krw=3_000_000, kis_paper_max_new_positions_per_tick=1,
        kis_paper_scan_max_symbols=10,
        kis_app_key="FAKE-KEY", kis_app_secret="FAKE-SECRET", kis_account_no="00000000",
        kis_product_code="01",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_sell_with_no_holding_is_skipped_not_sent(engine):
    Session = sessionmaker(bind=engine)
    # KIS 잔고 비어있음(get_positions=[]) → held_symbols 빈 set → 어떤 SELL 도 보유 0.
    out = asyncio.run(kis_paper_realtime_scan_tick(
        session_factory=Session, broker=_FakePosBroker([]), risk=object(),
        route_order_fn=_route_should_not_be_called(), settings=_scan_settings(),
        market_input_fn=_sell_input_fn, universe_symbols=["005380"],
        client=object(), now=OPEN_TIME,
    ))
    # SELL 신호가 났더라도 보유 0 이면 broker 로 가지 않는다.
    assert out["orders_attempted"] == 0
    assert out["broker_order_sent"] is False
    codes = {s["reason_code"] for s in out["skipped"]}
    # SELL 후보였다면 SELL_NO_HELD_POSITION, council 이 HOLD 였다면 HOLD_NO_SIGNAL.
    assert codes <= {"SELL_NO_HELD_POSITION", "HOLD_NO_SIGNAL"}


def test_carryover_kis_holding_not_blocked_as_no_holding(engine):
    # ★캐리오버 회귀(2026-06-05): 오늘 DB 주문 0건이어도 KIS 잔고에 005935 보유
    #   → held 로 인식 → SELL 이 'SELL_NO_HELD_POSITION' 으로 막히지 않는다(청산 가능).
    Session = sessionmaker(bind=engine)

    async def _permissive_route(**kw):
        return SimpleNamespace(
            decision=RiskDecision.APPROVED, reasons=[],
            audit=SimpleNamespace(id=1, broker_order_id="ODNO-1",
                                  broker_status="FILLED", filled_quantity=4, executed=True))

    broker = _FakePosBroker([SimpleNamespace(symbol="005935", quantity=4)])
    out = asyncio.run(kis_paper_realtime_scan_tick(
        session_factory=Session, broker=broker, risk=object(),
        route_order_fn=_permissive_route, settings=_scan_settings(),
        market_input_fn=_sell_input_fn, universe_symbols=["005935"],
        client=object(), now=OPEN_TIME,
    ))
    codes = {s.get("reason_code") for s in out["skipped"]}
    assert "SELL_NO_HELD_POSITION" not in codes  # KIS 보유 → '보유없음'으로 안 막힘.
