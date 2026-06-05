"""AG3/AG4/AG7: 결정 깔때기 + 확신도 보정 — 기존 기록 집계, 읽기 전용."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import AgentDecisionLog, OrderAuditLog
from app.performance.agent_dashboard import compute_calibration, compute_funnel


@pytest.fixture
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _utc(hh=3):
    return datetime(2026, 6, 5, hh, 0, tzinfo=timezone.utc)


def _decision(db, *, final, votes=None, sent=False, reason_code=None, audit_id=None, confidence=None):
    db.add(AgentDecisionLog(
        created_at=_utc(), agent_name="kis_paper_auto_executor", symbol="005930", mode="PAPER",
        decision=final, confidence=None, reasons=["x"],
        meta={"final_action": final, "votes": votes or [], "broker_order_sent": sent,
              "reason_code": reason_code, "audit_id": audit_id, "confidence": confidence}))


def _buy(db, *, price, status="FILLED"):
    row = OrderAuditLog(created_at=_utc(), mode="PAPER", requested_by_ai=False, symbol="005930",
                        side="BUY", quantity=1, order_type="MARKET", decision="APPROVED", executed=True,
                        broker_status=status, filled_quantity=1, avg_fill_price=price,
                        limit_price=price, latest_price=price, trade_reason="kis_paper_auto")
    db.add(row)
    db.flush()
    return row.id


def _sell(db, *, price):
    db.add(OrderAuditLog(created_at=_utc(4), mode="PAPER", requested_by_ai=False, symbol="005930",
                         side="SELL", quantity=1, order_type="MARKET", decision="APPROVED", executed=True,
                         broker_status="FILLED", filled_quantity=1, avg_fill_price=price,
                         limit_price=price, latest_price=price, trade_reason="kis_paper_auto"))


# ── AG3 깔때기 ─────────────────────────────────────────────────────────────────

def test_funnel_monotonic_with_drop_reasons(db):
    aid = _buy(db, price=1000)
    # signal=3 (BUY vote 3건), council BUY=2, submitted=1, filled=1.
    _decision(db, final="HOLD", votes=[{"strategy": "ORB", "signal": "BUY"}], reason_code="LOW_CONFIDENCE")  # signal, council drop
    _decision(db, final="BUY", votes=[{"strategy": "ORB", "signal": "BUY"}], sent=False, reason_code="KIS_PAPER_ORDER_LIMIT_EXCEEDED")  # passed, order drop
    _decision(db, final="BUY", votes=[{"strategy": "ORB", "signal": "BUY"}], sent=True, audit_id=aid)  # submitted + filled
    db.commit()
    out = compute_funnel(db, start=date(2026, 6, 5), end=date(2026, 6, 5))
    counts = {s["key"]: s["count"] for s in out["stages"]}
    assert counts["signal"] == 3 and counts["council"] == 2
    assert counts["submitted"] == 1 and counts["filled"] == 1
    # 단조 감소.
    assert counts["signal"] >= counts["council"] >= counts["submitted"] >= counts["filled"]
    drops = {(d["from"], d["to"]): d for d in out["drops"]}
    assert drops[("signal", "council")]["reasons"][0]["reason_code"] == "LOW_CONFIDENCE"
    assert drops[("council", "submitted")]["reasons"][0]["reason_code"] == "KIS_PAPER_ORDER_LIMIT_EXCEEDED"


def test_funnel_unknown_reason_marked(db):
    _decision(db, final="HOLD", votes=[{"strategy": "ORB", "signal": "BUY"}], reason_code=None)
    db.commit()
    out = compute_funnel(db, start=date(2026, 6, 5), end=date(2026, 6, 5))
    drop = next(d for d in out["drops"] if d["from"] == "signal")
    assert drop["reasons"][0]["reason_code"] == "사유 미기록"


def test_funnel_no_data(db):
    out = compute_funnel(db, start=date(2026, 6, 5), end=date(2026, 6, 5))
    assert out["no_data"] is True


# ── AG4 보정 ───────────────────────────────────────────────────────────────────

def test_calibration_buckets_by_confidence(db):
    # 진입 confidence 0.75 → '0.7~0.8' 구간, 이익(win).
    aid = _buy(db, price=1_000_000)
    _sell(db, price=1_200_000)
    _decision(db, final="BUY", votes=[{"strategy": "ORB", "signal": "BUY"}], sent=True, audit_id=aid, confidence=0.75)
    db.commit()
    out = compute_calibration(db, start=date(2026, 6, 5), end=date(2026, 6, 5))
    by = {b["bucket"]: b for b in out["buckets"]}
    assert "0.7~0.8" in by
    assert by["0.7~0.8"]["trade_count"] == 1 and by["0.7~0.8"]["win_rate"] == 1.0
    assert by["0.7~0.8"]["small_sample"] is True   # <5
    # 0건 구간은 표시 제외.
    assert "0.6~0.7" not in by


def test_calibration_excludes_trades_without_snapshot(db):
    _buy(db, price=1_000_000)
    _sell(db, price=1_100_000)  # 스냅샷(decision) 없음
    db.commit()
    out = compute_calibration(db, start=date(2026, 6, 5), end=date(2026, 6, 5))
    assert out["no_data"] is True
    assert out["buckets"] == []


def test_api_funnel_and_calibration_empty():
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
            assert c.get("/api/agent/funnel?period=daily").json()["no_data"] is True
            assert c.get("/api/agent/calibration?period=daily").json()["no_data"] is True
    finally:
        app.dependency_overrides.pop(get_db, None)


# ── AG5 학습 방향 (규칙 발화/침묵) ────────────────────────────────────────────

def test_learning_rules_fire_with_evidence(db, monkeypatch):
    import app.performance.agent_dashboard as ad
    import app.performance.performance as pf
    import app.performance.by_technique as bt
    import app.performance.shadow as sh
    monkeypatch.setattr(pf, "compute_performance", lambda *a, **k: {"win_rate": 0.6, "closed_count": 20})
    monkeypatch.setattr(bt, "compute_by_technique", lambda *a, **k: {"techniques": [
        {"technique": "ORB", "trade_count": 12, "win_rate": 0.40},
        {"technique": "VWAP", "trade_count": 3, "win_rate": 0.0},   # 표본<10 → 침묵
    ]})
    monkeypatch.setattr(ad, "compute_calibration", lambda *a, **k: {"buckets": [
        {"bucket": "0.7~0.8", "trade_count": 6, "win_rate": 0.5}]})
    monkeypatch.setattr(sh, "compute_shadow", lambda *a, **k: {"completed_count": 8, "correct_rate": 0.75, "avoided_loss_krw": 5000})
    out = ad.compute_learning(db, start=date(2026, 6, 5), end=date(2026, 6, 5))
    codes = {o["code"] for o in out["observations"]}
    assert codes == {"TECHNIQUE_LOW_WINRATE", "OVERCONFIDENCE", "GOOD_REJECTION"}
    orb = next(o for o in out["observations"] if o["code"] == "TECHNIQUE_LOW_WINRATE")
    assert orb["evidence"]["win_rate"] == 0.40 and orb["evidence"]["overall_win_rate"] == 0.6
    assert "운영자 승인" in out["footer"]


def test_learning_silent_when_conditions_unmet(db, monkeypatch):
    import app.performance.agent_dashboard as ad
    import app.performance.performance as pf
    import app.performance.by_technique as bt
    import app.performance.shadow as sh
    monkeypatch.setattr(pf, "compute_performance", lambda *a, **k: {"win_rate": 0.7, "closed_count": 2})
    monkeypatch.setattr(bt, "compute_by_technique", lambda *a, **k: {"techniques": [
        {"technique": "ORB", "trade_count": 3, "win_rate": 0.5}]})   # 표본 부족
    monkeypatch.setattr(ad, "compute_calibration", lambda *a, **k: {"buckets": []})
    monkeypatch.setattr(sh, "compute_shadow", lambda *a, **k: {"completed_count": 1, "correct_rate": 0.0})
    out = ad.compute_learning(db, start=date(2026, 6, 5), end=date(2026, 6, 5))
    codes = {o["code"] for o in out["observations"]}
    assert codes == {"INSUFFICIENT_SAMPLE"}   # 억지 관찰 없음
