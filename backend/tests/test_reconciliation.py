"""Tests for position reconciliation (212, MUST).

backlog #2 — broker 인식 포지션 vs audit log 산출 포지션 drift 감지 메커니즘.
"""

import asyncio
from datetime import datetime, timezone

from app.brokers.base import Position
from app.db.models import OrderAuditLog
from app.reconciliation.position_checker import (
    aggregate_audit_positions,
    compare_positions,
    reconcile,
    report_to_dict,
)


def _seed_audit(
    db,
    *,
    symbol: str,
    side: str,
    quantity: int,
    avg_fill_price: int | None = 75_000,
    executed: bool = True,
    decision: str = "APPROVED",
    archived: bool = False,
):
    """Insert an OrderAuditLog row mimicking a broker-side fill.

    executed=True + filled_quantity > 0이 reconciliation에 등장하는 조건.
    """
    db.add(OrderAuditLog(
        created_at=datetime.now(timezone.utc),
        mode="SIMULATION",
        requested_by_ai=False,
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type="MARKET",
        latest_price=avg_fill_price or 0,
        decision=decision,
        reasons=[],
        executed=executed,
        filled_quantity=quantity if executed else 0,
        avg_fill_price=avg_fill_price if executed else None,
        archived=archived,
    ))
    db.commit()


# ---------- aggregate_audit_positions ----------


def test_aggregate_empty_returns_empty(client):
    with client.test_db_factory() as db:
        rows = aggregate_audit_positions(db)
    assert rows == []


def test_aggregate_skips_non_executed(client):
    with client.test_db_factory() as db:
        _seed_audit(db, symbol="005930", side="BUY", quantity=10, executed=False,
                    decision="REJECTED")
        rows = aggregate_audit_positions(db)
    assert rows == []


def test_aggregate_single_buy(client):
    with client.test_db_factory() as db:
        _seed_audit(db, symbol="005930", side="BUY", quantity=10, avg_fill_price=75_000)
        rows = aggregate_audit_positions(db)
    assert len(rows) == 1
    assert rows[0].symbol == "005930"
    assert rows[0].net_quantity == 10
    assert rows[0].buy_quantity == 10
    assert rows[0].sell_quantity == 0
    assert rows[0].avg_buy_price == 75_000


def test_aggregate_buy_then_partial_sell(client):
    with client.test_db_factory() as db:
        _seed_audit(db, symbol="005930", side="BUY",  quantity=10, avg_fill_price=75_000)
        _seed_audit(db, symbol="005930", side="SELL", quantity=4,  avg_fill_price=80_000)
        rows = aggregate_audit_positions(db)
    assert len(rows) == 1
    assert rows[0].net_quantity == 6
    assert rows[0].buy_quantity == 10
    assert rows[0].sell_quantity == 4


def test_aggregate_full_close_excluded(client):
    """net_quantity == 0인 symbol은 결과에 포함되지 않는다."""
    with client.test_db_factory() as db:
        _seed_audit(db, symbol="005930", side="BUY",  quantity=10)
        _seed_audit(db, symbol="005930", side="SELL", quantity=10)
        rows = aggregate_audit_positions(db)
    assert rows == []


def test_aggregate_weighted_avg_buy_price(client):
    with client.test_db_factory() as db:
        _seed_audit(db, symbol="005930", side="BUY", quantity=10, avg_fill_price=70_000)
        _seed_audit(db, symbol="005930", side="BUY", quantity=10, avg_fill_price=80_000)
        rows = aggregate_audit_positions(db)
    assert rows[0].buy_quantity == 20
    assert rows[0].avg_buy_price == 75_000


def test_aggregate_includes_archived(client):
    """168 archived rows도 reconciliation에는 포함 — 보유 포지션은 archive
    여부와 무관."""
    with client.test_db_factory() as db:
        _seed_audit(db, symbol="005930", side="BUY", quantity=10, archived=True)
        rows = aggregate_audit_positions(db)
    assert len(rows) == 1
    assert rows[0].net_quantity == 10


def test_aggregate_multi_symbol_sorted(client):
    with client.test_db_factory() as db:
        _seed_audit(db, symbol="000660", side="BUY", quantity=5)
        _seed_audit(db, symbol="005930", side="BUY", quantity=10)
        rows = aggregate_audit_positions(db)
    assert [r.symbol for r in rows] == ["000660", "005930"]


# ---------- compare_positions ----------


def test_compare_in_sync_returns_empty():
    from app.reconciliation.position_checker import AuditPositionRow
    broker = [Position(symbol="005930", quantity=10, avg_price=75_000, market_price=76_000)]
    audit  = [AuditPositionRow(symbol="005930", net_quantity=10,
                                buy_quantity=10, sell_quantity=0, avg_buy_price=75_000)]
    assert compare_positions(broker, audit) == []


def test_compare_quantity_mismatch():
    from app.reconciliation.position_checker import AuditPositionRow
    broker = [Position(symbol="005930", quantity=15, avg_price=75_000, market_price=76_000)]
    audit  = [AuditPositionRow(symbol="005930", net_quantity=10,
                                buy_quantity=10, sell_quantity=0, avg_buy_price=75_000)]
    mismatches = compare_positions(broker, audit)
    assert len(mismatches) == 1
    assert mismatches[0].symbol == "005930"
    assert mismatches[0].kind == "quantity_mismatch"
    assert mismatches[0].quantity_diff == 5
    assert mismatches[0].broker_quantity == 15
    assert mismatches[0].audit_quantity == 10


def test_compare_broker_only():
    """broker에만 있는 symbol — audit이 인식 못 한 외부 주문 흔적."""
    broker = [Position(symbol="000660", quantity=5, avg_price=180_000, market_price=185_000)]
    audit  = []
    mismatches = compare_positions(broker, audit)
    assert len(mismatches) == 1
    assert mismatches[0].kind == "broker_only"
    assert mismatches[0].broker_quantity == 5
    assert mismatches[0].audit_quantity == 0


def test_compare_audit_only():
    """audit에는 있지만 broker가 인식 못 한 — broker drop 가능성."""
    from app.reconciliation.position_checker import AuditPositionRow
    broker = []
    audit  = [AuditPositionRow(symbol="005930", net_quantity=10,
                                buy_quantity=10, sell_quantity=0, avg_buy_price=75_000)]
    mismatches = compare_positions(broker, audit)
    assert len(mismatches) == 1
    assert mismatches[0].kind == "audit_only"
    assert mismatches[0].broker_quantity == 0
    assert mismatches[0].audit_quantity == 10


def test_compare_zero_broker_position_skipped():
    """quantity == 0인 broker Position은 broker view에서 제외 — 양쪽 모두
    0이면 mismatch 없음."""
    broker = [Position(symbol="005930", quantity=0, avg_price=0, market_price=0)]
    audit  = []
    assert compare_positions(broker, audit) == []


# ---------- reconcile (orchestrator) ----------


def test_reconcile_in_sync(client):
    broker = client.test_broker
    broker.positions["005930"] = Position(
        symbol="005930", quantity=10, avg_price=75_000, market_price=76_000)
    with client.test_db_factory() as db:
        _seed_audit(db, symbol="005930", side="BUY", quantity=10, avg_fill_price=75_000)
        report = asyncio.run(reconcile(db, broker))
    assert report.in_sync is True
    assert report.broker_symbol_count == 1
    assert report.audit_symbol_count == 1
    assert report.matched_count == 1
    assert report.mismatches == []


def test_reconcile_drift_detected(client):
    broker = client.test_broker
    broker.positions["005930"] = Position(
        symbol="005930", quantity=15, avg_price=75_000, market_price=76_000)
    with client.test_db_factory() as db:
        _seed_audit(db, symbol="005930", side="BUY", quantity=10)
        report = asyncio.run(reconcile(db, broker))
    assert report.in_sync is False
    assert len(report.mismatches) == 1
    assert report.mismatches[0].kind == "quantity_mismatch"
    assert report.matched_count == 0


# ---------- /api/reconciliation/status ----------


def test_status_endpoint_empty_in_sync(client):
    """No broker positions, no audit positions → in_sync."""
    res = client.get("/api/reconciliation/status")
    assert res.status_code == 200
    body = res.json()
    assert body["in_sync"] is True
    assert body["broker_symbol_count"] == 0
    assert body["audit_symbol_count"] == 0
    assert body["matched_count"] == 0
    assert body["mismatches"] == []


def test_status_endpoint_drift(client):
    broker = client.test_broker
    broker.positions["005930"] = Position(
        symbol="005930", quantity=15, avg_price=75_000, market_price=76_000)
    with client.test_db_factory() as db:
        _seed_audit(db, symbol="005930", side="BUY", quantity=10)

    body = client.get("/api/reconciliation/status").json()
    assert body["in_sync"] is False
    assert len(body["mismatches"]) == 1
    m = body["mismatches"][0]
    assert m["symbol"] == "005930"
    assert m["broker_quantity"] == 15
    assert m["audit_quantity"] == 10
    assert m["quantity_diff"] == 5
    assert m["kind"] == "quantity_mismatch"


def test_status_endpoint_audit_only(client):
    """broker는 비어 있고 audit에만 BUY가 있으면 audit_only."""
    with client.test_db_factory() as db:
        _seed_audit(db, symbol="005930", side="BUY", quantity=10)

    body = client.get("/api/reconciliation/status").json()
    assert body["in_sync"] is False
    assert body["audit_symbol_count"] == 1
    assert body["broker_symbol_count"] == 0
    assert body["mismatches"][0]["kind"] == "audit_only"


def test_status_endpoint_broker_only(client):
    """broker에는 있지만 audit log에는 흔적 없음 → broker_only."""
    broker = client.test_broker
    broker.positions["000660"] = Position(
        symbol="000660", quantity=5, avg_price=180_000, market_price=185_000)

    body = client.get("/api/reconciliation/status").json()
    assert body["in_sync"] is False
    assert body["broker_symbol_count"] == 1
    assert body["audit_symbol_count"] == 0
    assert body["mismatches"][0]["kind"] == "broker_only"


def test_status_endpoint_multiple_symbols_partial_drift(client):
    """일부 일치 + 일부 drift — matched_count, in_sync 검증."""
    broker = client.test_broker
    broker.positions["005930"] = Position(
        symbol="005930", quantity=10, avg_price=75_000, market_price=76_000)
    broker.positions["000660"] = Position(
        symbol="000660", quantity=8, avg_price=180_000, market_price=185_000)
    with client.test_db_factory() as db:
        _seed_audit(db, symbol="005930", side="BUY", quantity=10)  # match
        _seed_audit(db, symbol="000660", side="BUY", quantity=5)   # mismatch
        _seed_audit(db, symbol="035420", side="BUY", quantity=3)   # audit_only

    body = client.get("/api/reconciliation/status").json()
    assert body["in_sync"] is False
    assert body["broker_symbol_count"] == 2
    assert body["audit_symbol_count"] == 3
    assert body["matched_count"] == 1     # 005930만 일치
    kinds = sorted(m["kind"] for m in body["mismatches"])
    assert kinds == ["audit_only", "quantity_mismatch"]


# ---------- report_to_dict ----------


def test_report_to_dict_serializable():
    """asdict가 dataclass mismatches를 plain dict 리스트로 변환."""
    from app.reconciliation.position_checker import (
        PositionMismatch,
        ReconciliationReport,
    )
    report = ReconciliationReport(
        in_sync=False,
        broker_symbol_count=1,
        audit_symbol_count=0,
        matched_count=0,
        mismatches=[PositionMismatch(
            symbol="005930", broker_quantity=5, audit_quantity=0,
            quantity_diff=5, kind="broker_only",
        )],
    )
    out = report_to_dict(report)
    assert out["in_sync"] is False
    assert out["mismatches"][0]["symbol"] == "005930"
    assert out["mismatches"][0]["kind"] == "broker_only"
