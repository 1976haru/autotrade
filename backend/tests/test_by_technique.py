"""S3/S4/S6: 기법별 성적 — 귀속 규칙(다중 귀속)·스냅샷 없는 과거 거래 제외·플래그."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import AgentDecisionLog, OrderAuditLog
from app.performance.by_technique import _buy_voters_by_audit_id, compute_by_technique


@pytest.fixture
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _utc(d=5, hh=3):
    return datetime(2026, 6, d, hh, 0, tzinfo=timezone.utc)


def _buy(db, *, symbol, qty, price, at):
    row = OrderAuditLog(
        created_at=at, mode="PAPER", requested_by_ai=False, symbol=symbol, side="BUY",
        quantity=qty, order_type="MARKET", decision="APPROVED", executed=True,
        broker_status="FILLED", filled_quantity=qty, avg_fill_price=price,
        limit_price=price, latest_price=price, trade_reason="kis_paper_auto")
    db.add(row)
    db.flush()
    return row.id


def _sell(db, *, symbol, qty, price, at):
    db.add(OrderAuditLog(
        created_at=at, mode="PAPER", requested_by_ai=False, symbol=symbol, side="SELL",
        quantity=qty, order_type="MARKET", decision="APPROVED", executed=True,
        broker_status="FILLED", filled_quantity=qty, avg_fill_price=price,
        limit_price=price, latest_price=price, trade_reason="kis_paper_auto"))


def _snapshot(db, *, audit_id, votes, profile="balanced"):
    # S3: 진입 결정 스냅샷 — votes(기법별 방향) + risk_profile + audit_id(연결 키).
    db.add(AgentDecisionLog(
        created_at=_utc(), agent_name="kis_paper_auto_executor", symbol="X", mode="PAPER",
        decision="BUY", confidence=70, reasons=["entry"],
        meta={"audit_id": audit_id, "risk_profile": profile, "votes": votes}))


def test_multi_attribution_to_each_buy_voter(db):
    # 진입에 ORB·모멘텀 찬성, GAP 반대(SELL), VWAP 무표 → ORB·모멘텀에만 귀속.
    bid = _buy(db, symbol="005930", qty=1, price=1_000_000, at=_utc(5, 1))
    _sell(db, symbol="005930", qty=1, price=1_200_000, at=_utc(5, 2))  # 이익(net>0)
    _snapshot(db, audit_id=bid, votes=[
        {"strategy": "ORB", "signal": "BUY"},
        {"strategy": "MOMENTUM", "signal": "BUY"},
        {"strategy": "GAP", "signal": "SELL"},
    ])
    db.commit()
    out = compute_by_technique(db, start=date(2026, 6, 5), end=date(2026, 6, 5))
    by = {t["technique"]: t for t in out["techniques"]}
    assert by["ORB"]["trade_count"] == 1 and by["ORB"]["win_count"] == 1
    assert by["MOMENTUM"]["trade_count"] == 1
    assert by["GAP"]["trade_count"] == 0      # 찬성 아님(SELL)
    assert by["VWAP"]["trade_count"] == 0     # 무표
    assert by["ORB"]["net_contribution_krw"] > 0
    assert out["attributed_count"] == 1
    assert out["small_sample"] is True


def test_pre_snapshot_trades_excluded(db):
    # 스냅샷 없는 진입(과거 거래) → 귀속 불가 → 집계 제외(unattributed), 추정 소급 금지.
    _buy(db, symbol="000660", qty=1, price=1_000_000, at=_utc(5, 1))
    _sell(db, symbol="000660", qty=1, price=1_100_000, at=_utc(5, 2))
    db.commit()
    out = compute_by_technique(db, start=date(2026, 6, 5), end=date(2026, 6, 5))
    assert out["attributed_count"] == 0
    assert out["unattributed_count"] == 1
    assert out["no_data"] is True
    assert all(t["trade_count"] == 0 for t in out["techniques"])


def test_loss_attribution_and_net_contribution(db):
    bid = _buy(db, symbol="005930", qty=1, price=1_000_000, at=_utc(5, 1))
    _sell(db, symbol="005930", qty=1, price=900_000, at=_utc(5, 2))  # 손실
    _snapshot(db, audit_id=bid, votes=[{"strategy": "VWAP", "signal": "BUY"}])
    db.commit()
    out = compute_by_technique(db, start=date(2026, 6, 5), end=date(2026, 6, 5))
    by = {t["technique"]: t for t in out["techniques"]}
    assert by["VWAP"]["trade_count"] == 1 and by["VWAP"]["loss_count"] == 1
    assert by["VWAP"]["win_rate"] == 0.0
    assert by["VWAP"]["net_contribution_krw"] < 0


def test_buy_voters_map_reads_snapshot(db):
    bid = _buy(db, symbol="005930", qty=1, price=1000, at=_utc(5, 1))
    _snapshot(db, audit_id=bid, votes=[{"strategy": "ORB", "signal": "BUY"},
                                       {"strategy": "GAP", "signal": "HOLD"}])
    db.commit()
    voters = _buy_voters_by_audit_id(db)
    assert voters[bid] == {"ORB"}    # BUY 찬성만(GAP HOLD 제외)


def test_api_by_technique_endpoint_empty():
    from fastapi.testclient import TestClient
    from app.db.session import get_db
    from app.main import app
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
            r = c.get("/api/performance/by-technique?period=daily")
        assert r.status_code == 200
        b = r.json()
        assert b["no_data"] is True
        assert len(b["techniques"]) == 4
        assert "multi_attribution_note" in b
        assert b["active_profile"] in ("conservative", "balanced", "aggressive")
    finally:
        app.dependency_overrides.pop(get_db, None)
