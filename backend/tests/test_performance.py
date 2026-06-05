"""P1/P4: 성과 집계 — 라운드트립 짝짓기 / 비용 차감 / 지표 경계 / KST 기간."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import OrderAuditLog
from app.performance.performance import (
    compute_performance,
    compute_round_trips,
    resolve_period,
)


@pytest.fixture
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _utc(y, m, d, hh=3, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def _order(db, *, symbol, side, qty, price, at):
    db.add(OrderAuditLog(
        created_at=at, mode="PAPER", requested_by_ai=False, symbol=symbol, side=side,
        quantity=qty, order_type="MARKET", decision="APPROVED", executed=True,
        broker_status="FILLED", filled_quantity=qty, avg_fill_price=price,
        limit_price=price, latest_price=price, trade_reason="kis_paper_auto",
    ))


# ── 라운드트립 짝짓기 ──────────────────────────────────────────────────────────

def test_full_roundtrip_pairing(db):
    _order(db, symbol="005930", side="BUY", qty=1, price=70_000, at=_utc(2026, 6, 5))
    _order(db, symbol="005930", side="SELL", qty=1, price=80_000, at=_utc(2026, 6, 5))
    db.commit()
    trips = compute_round_trips(db)
    assert len(trips) == 1
    assert trips[0].gross_pnl == 10_000
    assert trips[0].cost > 0
    assert trips[0].net_pnl == 10_000 - trips[0].cost


def test_partial_close_fifo(db):
    # BUY 10 @100 → SELL 4 @120 (부분) → SELL 6 @90 → 2 라운드트립.
    _order(db, symbol="A", side="BUY", qty=10, price=100, at=_utc(2026, 6, 5, 1))
    _order(db, symbol="A", side="SELL", qty=4, price=120, at=_utc(2026, 6, 5, 2))
    _order(db, symbol="A", side="SELL", qty=6, price=90, at=_utc(2026, 6, 5, 3))
    db.commit()
    trips = compute_round_trips(db)
    assert len(trips) == 2
    assert trips[0].quantity == 4 and trips[0].gross_pnl == (120 - 100) * 4
    assert trips[1].quantity == 6 and trips[1].gross_pnl == (90 - 100) * 6


def test_fifo_weighted_buy_across_lots(db):
    # BUY 3 @100, BUY 3 @200, SELL 4 @300 → 가중매수 = (3×100 + 1×200)=500, qty4.
    _order(db, symbol="B", side="BUY", qty=3, price=100, at=_utc(2026, 6, 5, 1))
    _order(db, symbol="B", side="BUY", qty=3, price=200, at=_utc(2026, 6, 5, 2))
    _order(db, symbol="B", side="SELL", qty=4, price=300, at=_utc(2026, 6, 5, 3))
    db.commit()
    trips = compute_round_trips(db)
    assert len(trips) == 1
    assert trips[0].buy_cost == 3 * 100 + 1 * 200  # 500
    assert trips[0].gross_pnl == 300 * 4 - 500      # 700


def test_naked_sell_skipped(db):
    # 대응 매수 없는 SELL 은 라운드트립 아님(보유 비대응).
    _order(db, symbol="C", side="SELL", qty=5, price=100, at=_utc(2026, 6, 5))
    db.commit()
    assert compute_round_trips(db) == []


# ── 지표 / 비용 / 경계 ─────────────────────────────────────────────────────────

def test_metrics_win_loss_and_payoff(db):
    # 2승(+1000,+2000) 1패(-500) on big notionals so cost doesn't flip sign.
    for i, (b, s) in enumerate([(1_000_000, 1_100_000), (1_000_000, 1_300_000), (1_000_000, 950_000)]):
        _order(db, symbol=f"S{i}", side="BUY", qty=1, price=b, at=_utc(2026, 6, 5, 1))
        _order(db, symbol=f"S{i}", side="SELL", qty=1, price=s, at=_utc(2026, 6, 5, 2))
    db.commit()
    p = compute_performance(db, start=date(2026, 6, 5), end=date(2026, 6, 5))
    assert p["closed_count"] == 3
    assert p["win_count"] == 2 and p["loss_count"] == 1
    assert p["win_rate"] == round(2 / 3, 4)
    assert p["payoff_ratio"] is not None and p["payoff_ratio"] > 0
    assert p["no_data"] is False
    assert p["small_sample"] is True   # 3 < 10


def test_no_data_flag_when_zero_closed(db):
    p = compute_performance(db, start=date(2026, 6, 5), end=date(2026, 6, 5))
    assert p["no_data"] is True
    assert p["win_rate"] is None and p["payoff_ratio"] is None
    assert p["net_pnl_krw"] == 0


def test_payoff_dash_when_no_losses(db):
    # 전승 → 손실 표본 0 → payoff None ("—").
    _order(db, symbol="W", side="BUY", qty=1, price=1_000_000, at=_utc(2026, 6, 5, 1))
    _order(db, symbol="W", side="SELL", qty=1, price=1_200_000, at=_utc(2026, 6, 5, 2))
    db.commit()
    p = compute_performance(db, start=date(2026, 6, 5), end=date(2026, 6, 5))
    assert p["win_count"] == 1 and p["loss_count"] == 0
    assert p["payoff_ratio"] is None


def test_net_is_gross_minus_cost(db):
    _order(db, symbol="X", side="BUY", qty=1, price=1_000_000, at=_utc(2026, 6, 5, 1))
    _order(db, symbol="X", side="SELL", qty=1, price=1_000_000, at=_utc(2026, 6, 5, 2))
    db.commit()
    p = compute_performance(db, start=date(2026, 6, 5), end=date(2026, 6, 5))
    # 손익 0 인 거래도 비용 때문에 순손익은 음수.
    assert p["gross_pnl_krw"] == 0
    assert p["cost_krw"] > 0
    assert p["net_pnl_krw"] == -p["cost_krw"]


def test_period_return_uses_base_equity(db):
    _order(db, symbol="R", side="BUY", qty=1, price=1_000_000, at=_utc(2026, 6, 5, 1))
    _order(db, symbol="R", side="SELL", qty=1, price=1_100_000, at=_utc(2026, 6, 5, 2))
    db.commit()
    p = compute_performance(db, start=date(2026, 6, 5), end=date(2026, 6, 5), base_equity_krw=100_000_000)
    # net ≈ +96,700 (10만 − 비용) / 1억 ≈ +0.1%
    assert p["period_return_pct"] is not None
    assert 0.0 < p["period_return_pct"] < 0.2


def test_kst_period_boundary_excludes_other_days(db):
    # 06-04 KST 청산은 06-05 일간 집계에서 제외(KST 경계).
    _order(db, symbol="D", side="BUY", qty=1, price=1_000_000, at=_utc(2026, 6, 4, 1))
    _order(db, symbol="D", side="SELL", qty=1, price=1_100_000, at=_utc(2026, 6, 4, 2))  # KST 06-04
    db.commit()
    today = compute_performance(db, start=date(2026, 6, 5), end=date(2026, 6, 5))
    assert today["no_data"] is True
    yest = compute_performance(db, start=date(2026, 6, 4), end=date(2026, 6, 4))
    assert yest["closed_count"] == 1


def test_resolve_period_kst():
    today = date(2026, 6, 5)  # 금요일
    assert resolve_period("daily", today=today) == (today, today)
    assert resolve_period("weekly", today=today) == (date(2026, 6, 1), today)  # 월요일~
    assert resolve_period("monthly", today=today) == (date(2026, 6, 1), today)
    assert resolve_period("custom", today=today,
                          from_=date(2026, 6, 2), to=date(2026, 6, 4)) == (date(2026, 6, 2), date(2026, 6, 4))


def test_api_performance_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    from app.db.session import get_db
    from app.main import app
    import app.performance.market_index as mi

    # KIS 실호출 0 — 시장 컨텍스트를 unavailable 로 스텁(엔드포인트 망 호출 방지).
    async def _fake_ctx(*, start, end, **kw):
        return {"market": {"available": False, "reason": "MARKET_NOT_FETCHED"},
                "current_equity_krw": None}
    monkeypatch.setattr(mi, "get_market_comparison_context", _fake_ctx)

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, expire_on_commit=False)

    def _ov():
        s = TS()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _ov
    try:
        with TestClient(app) as c:
            r = c.get("/api/performance?period=daily")
        assert r.status_code == 200
        b = r.json()
        assert b["no_data"] is True          # 빈 DB → 가짜 0% 아님
        assert b["is_live_authorization"] is False
        assert "period_start_kst" in b
        assert b["market"]["available"] is False   # 시장 실패해도 봇 성과는 정상
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_api_performance_bot_vs_index_comparison(monkeypatch):
    from fastapi.testclient import TestClient
    from app.db.session import get_db
    from app.main import app
    import app.performance.market_index as mi

    async def _ctx(*, start, end, **kw):
        return {"market": {"available": True, "kospi_return_pct": 1.2,
                           "kosdaq_return_pct": 0.9, "fetched_at_kst": "09:05"},
                "current_equity_krw": 100_000_000}
    monkeypatch.setattr(mi, "get_market_comparison_context", _ctx)

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, expire_on_commit=False)
    with TS() as s:
        now = datetime.now(timezone.utc)
        _order(s, symbol="K", side="BUY", qty=1, price=1_000_000, at=now)
        _order(s, symbol="K", side="SELL", qty=1, price=1_100_000, at=now)
        s.commit()

    def _ov():
        s = TS()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _ov
    try:
        with TestClient(app) as c:
            r = c.get("/api/performance?period=daily")
        assert r.status_code == 200
        b = r.json()
        assert b["market"]["available"] is True
        assert b["period_return_pct"] is not None       # 평가자산 기준 봇 수익률
        assert b["comparison"] is not None
        assert b["comparison"]["kospi_return_pct"] == 1.2
        assert b["comparison"]["vs_kospi_pp"] is not None
    finally:
        app.dependency_overrides.pop(get_db, None)
