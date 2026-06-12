"""KIS Realtime Paper Auto V2 — 권한 가드 + 다종목 스캔 테스트.

- auto_permission: mock 시세로는 KIS 모의주문 전송 차단 (KIS_REALTIME_PRICE_REQUIRED).
- scan: 실 KIS 호출 0건 (fake market_input_fn + route_order_fn 주입).
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
from app.kis_paper.auto_permission import (
    KisPaperOrderPermissionInput,
    KisPaperPermReason,
    evaluate_kis_paper_order_permission,
)
from app.kis_paper.driver_bridge import kis_paper_realtime_scan_tick
from app.market_data.kis_realtime import KIS_PRICE_OK, KisRealtimeQuote
from app.risk.risk_manager import RiskDecision

OPEN_TIME = datetime(2026, 5, 27, 5, 0, 0, tzinfo=timezone.utc)  # 14:00 KST Wed (OPEN)


# ───────────────────────── permission guard ─────────────────────────


def _perm_input(**kw):
    base = dict(
        enable_kis_paper_auto_trading=True, dry_run=False, kis_is_paper=True,
        enable_live_trading=False, broker_is_kis_paper=True, credentials_present=True,
        side="BUY", notional_krw=900_000, confidence=0.8, quality_score=80,
        has_exit_plan=True, max_order_notional=1_000_000, daily_order_count=0,
        max_orders_per_day=10, window_start="09:05", window_end="14:50",
        min_confidence=0.6, min_quality_score=60, price_source="kis",
        price_is_stale=False, now=OPEN_TIME,
    )
    base.update(kw)
    return KisPaperOrderPermissionInput(**base)


def test_mock_price_blocks_real_kis_order():
    r = evaluate_kis_paper_order_permission(_perm_input(price_source="mock"))
    assert r.allowed is False
    assert r.reason_code == KisPaperPermReason.KIS_REALTIME_PRICE_REQUIRED.value


def test_stale_price_blocks_real_kis_order():
    r = evaluate_kis_paper_order_permission(_perm_input(price_is_stale=True))
    assert r.allowed is False
    assert r.reason_code == KisPaperPermReason.KIS_PRICE_STALE.value


def test_kis_realtime_price_allowed():
    r = evaluate_kis_paper_order_permission(_perm_input(price_source="kis"))
    assert r.allowed is True
    assert r.reason_code == KisPaperPermReason.KIS_PAPER_ORDER_ALLOWED.value


def test_dryrun_not_blocked_by_price_source():
    # dry_run 이면 실제 전송이 없으므로 price_source 가드는 적용되지 않는다.
    r = evaluate_kis_paper_order_permission(_perm_input(dry_run=True, price_source="mock"))
    assert r.allowed is True


# ───────────────────────── multi-symbol scan ─────────────────────────


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return eng


def _paper_broker_no_holdings():
    """실 KisBrokerAdapter(=_broker_is_kis_paper True) + get_positions 스텁(보유 0).
    held 가드 격리 + 실 KIS 호출 0(P0 테스트 정책) — 잔고조회만 차단, 주문경로는 mock route."""
    b = KisBrokerAdapter(is_paper=True)
    async def _no_pos():
        return []
    b.get_positions = _no_pos
    return b


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
        # evaluate_readiness 자격 (fake 값 — secret 아님).
        kis_app_key="FAKE-KEY", kis_app_secret="FAKE-SECRET", kis_account_no="00000000",
        kis_product_code="01",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _strong_buy_input(symbol):
    return StrategyMarketInput(
        symbol=symbol, current_price=10800.0, prev_close=10000.0, open_price=10300.0,
        vwap=10400.0, opening_range_high=10500.0, opening_range_low=10200.0,
        recent_closes=(10000.0, 10200.0, 10400.0, 10600.0, 10800.0),
        current_volume=200.0, avg_volume=100.0,
        market_regime="TREND_UP", regime_decision="ALLOW",
    )


def _ok_quote(symbol):
    return KisRealtimeQuote(symbol=symbol, status=KIS_PRICE_OK, price=10800.0,
                            is_stale=False)


async def _buy_input_fn(symbol, *, client, now, market_is_open, **kw):
    return _strong_buy_input(symbol), _ok_quote(symbol)


async def _no_data_input_fn(symbol, *, client, now, market_is_open, **kw):
    from app.market_data.kis_realtime import KIS_MARKET_DATA_UNAVAILABLE
    return None, KisRealtimeQuote(symbol=symbol, status=KIS_MARKET_DATA_UNAVAILABLE,
                                  price=None)


def _approved_route():
    async def _fn(**kw):
        audit = SimpleNamespace(id=1, broker_order_id="PAPER-9001",
                                broker_status="FILLED", filled_quantity=92, executed=True)
        return SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[], audit=audit)
    return _fn


def test_scan_no_data_no_orders(engine):
    Session = sessionmaker(bind=engine)
    out = asyncio.run(kis_paper_realtime_scan_tick(
        session_factory=Session, broker=_paper_broker_no_holdings(), risk=object(),
        route_order_fn=_approved_route(), settings=_scan_settings(),
        market_input_fn=_no_data_input_fn, universe_symbols=["005930", "000660"],
        client=object(), now=OPEN_TIME,
    ))
    assert out["symbols_scanned"] == 2
    assert out["candidates_found"] == 0
    assert out["orders_attempted"] == 0
    assert out["orders_submitted"] == 0
    assert out["broker_order_sent"] is False
    assert out["price_source"] == "kis"


def test_scan_submits_within_per_tick_limit(engine):
    Session = sessionmaker(bind=engine)
    out = asyncio.run(kis_paper_realtime_scan_tick(
        session_factory=Session, broker=_paper_broker_no_holdings(), risk=object(),
        route_order_fn=_approved_route(), settings=_scan_settings(),
        market_input_fn=_buy_input_fn,
        universe_symbols=["005930", "000660", "035720"],
        client=object(), now=OPEN_TIME,
    ))
    assert out["symbols_scanned"] == 3
    assert out["candidates_found"] == 3            # 모두 BUY 후보
    assert out["orders_attempted"] == 1            # max_new_positions_per_tick=1
    assert out["orders_submitted"] == 1
    assert out["broker_order_sent"] is True
    assert out["broker_order_no"] == "PAPER-9001"
    assert out["price_source"] == "kis"
    assert out["is_live_authorization"] is False
    # 나머지 2종목은 per-tick 한도로 skip.
    codes = {s["reason_code"] for s in out["skipped"]}
    assert "MAX_NEW_POSITIONS_PER_TICK_REACHED" in codes


def test_scan_respects_daily_buy_limit(engine, monkeypatch):
    Session = sessionmaker(bind=engine)
    # T2(2026-06-12): 일일 한도는 이제 effective_daily_buy_limit()(런타임 오버라이드>env,
    #   R1 패턴 — budget/concurrent 와 동일). 스캔이 그 getter 를 읽는지 검증하려 패치.
    import app.core.runtime_config as _rc
    monkeypatch.setattr(_rc, "effective_daily_buy_limit", lambda: 1_000)
    # 일일 매수 한도를 1주 미만으로 낮추면 BUY 가 막힌다.
    out = asyncio.run(kis_paper_realtime_scan_tick(
        session_factory=Session, broker=_paper_broker_no_holdings(), risk=object(),
        route_order_fn=_approved_route(),
        settings=_scan_settings(),
        market_input_fn=_buy_input_fn, universe_symbols=["005930"],
        client=object(), now=OPEN_TIME,
    ))
    assert out["orders_submitted"] == 0
    codes = {s["reason_code"] for s in out["skipped"]}
    assert "DAILY_BUY_LIMIT_REACHED" in codes


def test_scan_smoke_mode_single_symbol_qty_one(engine):
    Session = sessionmaker(bind=engine)
    captured = {}

    async def _route(**kw):
        captured["qty"] = kw["order"].quantity
        audit = SimpleNamespace(id=1, broker_order_id="PAPER-SMOKE",
                                broker_status="FILLED", filled_quantity=1, executed=True)
        return SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[], audit=audit)

    out = asyncio.run(kis_paper_realtime_scan_tick(
        session_factory=Session, broker=_paper_broker_no_holdings(), risk=object(),
        route_order_fn=_route,
        settings=_scan_settings(kis_paper_smoke_mode=True, kis_paper_smoke_qty=1,
                                kis_paper_smoke_symbol="005930"),
        market_input_fn=_buy_input_fn, client=object(), now=OPEN_TIME,
    ))
    assert out["symbols_scanned"] == 1
    assert out["smoke_mode"] is True
    if out["orders_submitted"] == 1:
        assert captured.get("qty") == 1
