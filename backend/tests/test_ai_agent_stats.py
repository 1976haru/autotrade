"""AI agent stats tests (162, MUST)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai.agent_stats import compute_ai_agent_stats
from app.db.base import Base
from app.db.models import OrderAuditLog


def _session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


def _ai_audit(
    db, *,
    decision="APPROVED",
    strategy="ai_virtual",
    symbol="005930",
    requested_by_ai=True,
    confidence=80,
    executed=True,
    reasons=None,
    created_at=None,
):
    row = OrderAuditLog(
        mode="VIRTUAL_AI_EXECUTION", symbol=symbol, side="BUY", quantity=1,
        order_type="MARKET", latest_price=100,
        decision=decision, reasons=list(reasons or []),
        requested_by_ai=requested_by_ai,
        strategy=strategy,
        signal_strength=confidence,
        signal_confidence=confidence,
        executed=executed,
        broker_status="FILLED" if executed else None,
        filled_quantity=1 if executed else 0,
        avg_fill_price=100 if executed else None,
        created_at=created_at,
    )
    db.add(row)
    db.flush()
    return row


# ---------- core stats ----------

def test_empty_db_returns_zero_counts():
    Session = _session()
    with Session() as db:
        s = compute_ai_agent_stats(db)
    assert s["total_proposals"] == 0
    assert s["approval_rate"]   == 0.0
    assert s["avg_confidence"]  == 0.0
    assert s["per_strategy"]    == []


def test_counts_only_requested_by_ai_rows():
    """비-AI 주문은 카운트 X."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        _ai_audit(db, requested_by_ai=False, created_at=now)
        _ai_audit(db, requested_by_ai=True,  created_at=now)
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    assert s["total_proposals"] == 1


def test_approval_rate_excludes_needs_approval():
    """approval_rate 분모 = approved + rejected. NEEDS_APPROVAL은 별도 카운트."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        for _ in range(3):
            _ai_audit(db, decision="APPROVED", created_at=now)
        _ai_audit(db, decision="REJECTED", created_at=now)
        _ai_audit(db, decision="NEEDS_APPROVAL", created_at=now)
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    assert s["approved"]       == 3
    assert s["rejected"]       == 1
    assert s["needs_approval"] == 1
    assert s["approval_rate"]  == 0.75


def test_avg_confidence_only_executed_rows_with_confidence():
    """미체결(executed=False) 또는 confidence=None인 row는 평균 산출에서 제외."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        _ai_audit(db, executed=True,  confidence=80, created_at=now)
        _ai_audit(db, executed=True,  confidence=60, created_at=now)
        _ai_audit(db, executed=False, confidence=10, created_at=now)
        _ai_audit(db, executed=True,  confidence=None, created_at=now)
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    assert s["avg_confidence"] == 70.0


def test_lookback_days_zero_means_all_time():
    """lookback_days=0이면 cutoff 없이 전체."""
    Session = _session()
    now = datetime.now(timezone.utc)
    very_old = now - timedelta(days=400)
    with Session() as db:
        _ai_audit(db, created_at=very_old)
        _ai_audit(db, created_at=now)
        db.commit()
        s = compute_ai_agent_stats(db, lookback_days=0, now=now)
    assert s["total_proposals"] == 2


def test_lookback_excludes_rows_outside_window():
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        _ai_audit(db, created_at=now - timedelta(days=10))  # 밖
        _ai_audit(db, created_at=now)                       # 안
        db.commit()
        s = compute_ai_agent_stats(db, lookback_days=7, now=now)
    assert s["total_proposals"] == 1


# ---------- per_strategy ----------

def test_per_strategy_separates_correctly():
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        for _ in range(3):
            _ai_audit(db, strategy="ai_a", decision="APPROVED", created_at=now)
        _ai_audit(db, strategy="ai_a", decision="REJECTED", created_at=now)
        _ai_audit(db, strategy="ai_b", decision="APPROVED", created_at=now)
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    by = {row["strategy"]: row for row in s["per_strategy"]}
    assert by["ai_a"]["total"]         == 4
    assert by["ai_a"]["approved"]      == 3
    assert by["ai_a"]["approval_rate"] == 0.75
    assert by["ai_b"]["total"]         == 1
    assert by["ai_b"]["approved"]      == 1


def test_per_strategy_sorted_by_total_desc():
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        for _ in range(2):
            _ai_audit(db, strategy="b", created_at=now)
        for _ in range(5):
            _ai_audit(db, strategy="a", created_at=now)
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    assert [r["strategy"] for r in s["per_strategy"]] == ["a", "b"]


def test_null_strategy_appears_as_unknown():
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        _ai_audit(db, strategy=None, created_at=now)
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    assert s["per_strategy"][0]["strategy"] == "(unknown)"


# ---------- top_rejection_reasons ----------

def test_top_rejection_reasons_categorized():
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        _ai_audit(db, decision="REJECTED", created_at=now,
                  reasons=["emergency stop is enabled"])
        _ai_audit(db, decision="REJECTED", created_at=now,
                  reasons=["AI signal confidence 50 < min_ai_confidence 70"])
        _ai_audit(db, decision="REJECTED", created_at=now,
                  reasons=["AI signal confidence 30 < min_ai_confidence 70"])
        _ai_audit(db, decision="REJECTED", created_at=now,
                  reasons=["AI rate limit exceeded: 5 proposals in 60s"])
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    assert s["top_rejection_reasons"]["low_confidence"] == 2
    assert s["top_rejection_reasons"]["emergency_stop"] == 1
    assert s["top_rejection_reasons"]["rate_limit"]     == 1


def test_top_rejection_reasons_does_not_count_approved_rows():
    """APPROVED row의 reasons는 이상한 카테고리로 누적 안 됨."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        _ai_audit(db, decision="APPROVED", created_at=now,
                  reasons=["AI signal confidence above threshold"])
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    assert s["top_rejection_reasons"] == {}


# ---------- HTTP integration ----------

def test_endpoint_surface_basic_metrics(client):
    now = datetime.now(timezone.utc)
    with client.test_db_factory() as db:
        for _ in range(3):
            _ai_audit(db, decision="APPROVED", strategy="ai_virtual",
                      created_at=now)
        _ai_audit(db, decision="REJECTED", strategy="ai_virtual",
                  reasons=["AI signal confidence 30 < min_ai_confidence 70"],
                  created_at=now)
        db.commit()
    res = client.get("/api/ai/agent-stats?lookback_days=7")
    assert res.status_code == 200
    body = res.json()
    assert body["total_proposals"] == 4
    assert body["approval_rate"]   == 0.75
    assert body["top_rejection_reasons"]["low_confidence"] == 1


def test_endpoint_validates_lookback_days_range(client):
    res = client.get("/api/ai/agent-stats?lookback_days=-1")
    assert res.status_code == 422
    res = client.get("/api/ai/agent-stats?lookback_days=999999")
    assert res.status_code == 422


def test_endpoint_empty_returns_zero_counts(client):
    res = client.get("/api/ai/agent-stats")
    assert res.status_code == 200
    body = res.json()
    assert body["total_proposals"] == 0
    assert body["per_strategy"]    == []


# ---------- 165: confidence histogram + realized PnL by strategy ----------

def test_confidence_histogram_buckets_correctly():
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        # 각 bucket에 의도적으로 다른 confidence 시드.
        _ai_audit(db, confidence=10,  created_at=now)   # 0-25
        _ai_audit(db, confidence=24,  created_at=now)   # 0-25
        _ai_audit(db, confidence=40,  created_at=now)   # 25-50
        _ai_audit(db, confidence=70,  created_at=now)   # 50-75
        _ai_audit(db, confidence=80,  created_at=now)   # 75-100
        _ai_audit(db, confidence=100, created_at=now)   # 75-100
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    h = s["confidence_histogram"]
    assert h["0-25"]   == 2
    assert h["25-50"]  == 1
    assert h["50-75"]  == 1
    assert h["75-100"] == 2


def test_confidence_histogram_missing_counter():
    """confidence=None인 row는 별도 missing 카운터로."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        _ai_audit(db, confidence=None, created_at=now)
        _ai_audit(db, confidence=50,   created_at=now)
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    assert s["confidence_histogram_missing"] == 1
    assert s["confidence_histogram"]["50-75"] == 1


def test_confidence_histogram_boundary_values():
    """경계값 — 25는 25-50, 50은 50-75, 75는 75-100 (lower-bound inclusive)."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        _ai_audit(db, confidence=25, created_at=now)
        _ai_audit(db, confidence=50, created_at=now)
        _ai_audit(db, confidence=75, created_at=now)
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    h = s["confidence_histogram"]
    assert h["0-25"]   == 0
    assert h["25-50"]  == 1
    assert h["50-75"]  == 1
    assert h["75-100"] == 1


def test_per_strategy_includes_realized_pnl_from_fifo():
    """163의 compute_historical_accuracy 결과가 per_strategy에 반영."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        # ai_winner: BUY 100, SELL 110 — +10 realized.
        _ai_audit(db, strategy="ai_winner", decision="APPROVED",
                  confidence=80, created_at=now)
        # 위 audit row는 helper가 BUY로 만들지만, 체결 정보가 필요. helper가
        # filled_quantity/avg_fill_price 채우는지 확인.
        # helper에서 default executed=True, filled_quantity=1, avg_fill_price=100.
        # SELL 매칭하려면 두 번째 row 추가 (helper의 side는 default BUY라 직접 추가).
        sell_row = OrderAuditLog(
            mode="VIRTUAL_AI_EXECUTION", symbol="005930", side="SELL",
            quantity=1, order_type="MARKET", latest_price=110,
            decision="APPROVED", reasons=[], requested_by_ai=True,
            strategy="ai_winner", signal_strength=80, signal_confidence=80,
            executed=True, broker_status="FILLED",
            filled_quantity=1, avg_fill_price=110,
            created_at=now,
        )
        db.add(sell_row)
        db.commit()

        s = compute_ai_agent_stats(db, now=now)
    by = {row["strategy"]: row for row in s["per_strategy"]}
    winner = by["ai_winner"]
    assert winner["wins"]         == 1
    assert winner["losses"]       == 0
    assert winner["realized_pnl"] == 10


def test_per_strategy_unknown_has_zero_pnl():
    """(unknown) strategy는 NULL row 매핑이라 PnL 카운트 0."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        _ai_audit(db, strategy=None, decision="APPROVED", created_at=now)
        db.commit()
        s = compute_ai_agent_stats(db, now=now)
    unknown = s["per_strategy"][0]
    assert unknown["strategy"]     == "(unknown)"
    assert unknown["wins"]         == 0
    assert unknown["losses"]       == 0
    assert unknown["realized_pnl"] == 0


def test_endpoint_surfaces_extended_fields(client):
    """HTTP 응답에 165 신규 필드가 surface."""
    now = datetime.now(timezone.utc)
    with client.test_db_factory() as db:
        _ai_audit(db, confidence=80, created_at=now)
        _ai_audit(db, confidence=20, created_at=now)
        db.commit()
    body = client.get("/api/ai/agent-stats?lookback_days=7").json()
    assert "confidence_histogram" in body
    assert body["confidence_histogram"]["0-25"]   == 1
    assert body["confidence_histogram"]["75-100"] == 1
    assert "confidence_histogram_missing" in body
    # per_strategy entries should have wins/losses/realized_pnl.
    if body["per_strategy"]:
        assert "wins"         in body["per_strategy"][0]
        assert "losses"       in body["per_strategy"][0]
        assert "realized_pnl" in body["per_strategy"][0]
