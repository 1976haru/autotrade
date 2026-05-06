"""Daily realized PnL aggregation tests (145, MUST).

145 이전에는 RiskManager.daily_realized_pnl이 어디에서도 갱신되지 않아
max_daily_loss 검사가 무효 상태였다. 본 모듈은 (a) audit log 기반 일별 PnL
재구성 함수와 (b) route_order 통합 후 강제력 회복을 검증.
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import OrderAuditLog
from app.risk.daily_pnl import (
    KST,
    compute_today_realized_pnl,
    today_kst,
    today_utc,
)


def _make_session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


def _audit(side="BUY", qty=1, fill_price=100, symbol="005930",
           executed=True, created_at=None):
    return OrderAuditLog(
        mode="LIVE_MANUAL_APPROVAL",
        symbol=symbol, side=side, quantity=qty,
        order_type="MARKET", latest_price=fill_price,
        decision="APPROVED", reasons=[],
        executed=executed,
        broker_status="FILLED",
        filled_quantity=qty if executed else 0,
        avg_fill_price=fill_price if executed else None,
        created_at=created_at,
    )


# ---------- compute_today_realized_pnl unit ----------

def test_returns_zero_when_no_audit_rows():
    Session = _make_session()
    with Session() as db:
        assert compute_today_realized_pnl(db) == 0


def test_returns_zero_when_only_open_buys():
    """체결된 BUY만 있고 SELL 없음 → realized PnL = 0 (unrealized)."""
    Session = _make_session()
    with Session() as db:
        db.add_all([_audit(side="BUY"), _audit(side="BUY", qty=2)])
        db.commit()
        assert compute_today_realized_pnl(db) == 0


def test_today_sell_against_today_buy_counts():
    """같은 날 BUY 후 SELL → realized PnL이 카운트."""
    Session = _make_session()
    today = today_utc()
    now_utc = datetime.now(timezone.utc)
    with Session() as db:
        db.add_all([
            _audit(side="BUY",  qty=1, fill_price=100, created_at=now_utc),
            _audit(side="SELL", qty=1, fill_price=110, created_at=now_utc),
        ])
        db.commit()
        assert compute_today_realized_pnl(db, today=today) == 10


def test_yesterday_sell_does_not_count():
    """SELL이 어제면 today's PnL에 안 들어간다 — 일자 경계 invariant."""
    Session = _make_session()
    today = today_utc()
    yesterday = today - timedelta(days=1)
    yesterday_dt = datetime.combine(yesterday, datetime.min.time(), tzinfo=timezone.utc)
    with Session() as db:
        db.add_all([
            _audit(side="BUY",  qty=1, fill_price=100, created_at=yesterday_dt),
            _audit(side="SELL", qty=1, fill_price=80,  created_at=yesterday_dt),
        ])
        db.commit()
        assert compute_today_realized_pnl(db, today=today) == 0


def test_yesterday_buy_today_sell_counts_today():
    """overnight 보유 후 오늘 청산 — PnL은 today에 귀속."""
    Session = _make_session()
    today = today_utc()
    yesterday_dt = datetime.combine(today - timedelta(days=1),
                                     datetime.min.time(), tzinfo=timezone.utc)
    today_dt     = datetime.now(timezone.utc)
    with Session() as db:
        db.add_all([
            _audit(side="BUY",  qty=1, fill_price=100, created_at=yesterday_dt),
            _audit(side="SELL", qty=1, fill_price=120, created_at=today_dt),
        ])
        db.commit()
        assert compute_today_realized_pnl(db, today=today) == 20


def test_loss_is_negative():
    """loss → 음수 — RiskManager의 max_daily_loss 비교가 음수 기준."""
    Session = _make_session()
    today = today_utc()
    now = datetime.now(timezone.utc)
    with Session() as db:
        db.add_all([
            _audit(side="BUY",  qty=1, fill_price=100, created_at=now),
            _audit(side="SELL", qty=1, fill_price=85,  created_at=now),
        ])
        db.commit()
        assert compute_today_realized_pnl(db, today=today) == -15


def test_partial_sell_realizes_only_matched_portion():
    """BUY 5 @ 100, SELL 3 @ 110 → realized = 30, 잔량 2주는 미반영."""
    Session = _make_session()
    today = today_utc()
    now = datetime.now(timezone.utc)
    with Session() as db:
        db.add_all([
            _audit(side="BUY",  qty=5, fill_price=100, created_at=now),
            _audit(side="SELL", qty=3, fill_price=110, created_at=now),
        ])
        db.commit()
        assert compute_today_realized_pnl(db, today=today) == 30


def test_unexecuted_rows_skipped():
    """REJECTED / NEEDS_APPROVAL audit row는 executed=False라 매칭 X."""
    Session = _make_session()
    today = today_utc()
    now = datetime.now(timezone.utc)
    with Session() as db:
        db.add_all([
            _audit(side="BUY",  fill_price=100, executed=False, created_at=now),
            _audit(side="SELL", fill_price=85,  executed=False, created_at=now),
        ])
        db.commit()
        assert compute_today_realized_pnl(db, today=today) == 0


def test_multiple_symbols_aggregate_independently():
    """다른 symbol의 BUY/SELL은 서로 페어매칭하지 않는다."""
    Session = _make_session()
    today = today_utc()
    now = datetime.now(timezone.utc)
    with Session() as db:
        db.add_all([
            # 종목 A: +10
            _audit(side="BUY",  qty=1, fill_price=100, symbol="A", created_at=now),
            _audit(side="SELL", qty=1, fill_price=110, symbol="A", created_at=now),
            # 종목 B: -5 (다른 BUY 가격이라 분리 매칭)
            _audit(side="BUY",  qty=1, fill_price=200, symbol="B", created_at=now),
            _audit(side="SELL", qty=1, fill_price=195, symbol="B", created_at=now),
        ])
        db.commit()
        assert compute_today_realized_pnl(db, today=today) == 5


def test_naked_sell_ignored():
    """잔량 BUY 없이 SELL → 매칭 X, 0 누적."""
    Session = _make_session()
    today = today_utc()
    now = datetime.now(timezone.utc)
    with Session() as db:
        db.add(_audit(side="SELL", qty=1, fill_price=100, created_at=now))
        db.commit()
        assert compute_today_realized_pnl(db, today=today) == 0


# ---------- route_order integration: max_daily_loss enforcement ----------

def test_route_order_populates_daily_pnl_and_enforces_max_daily_loss(client):
    """145 핵심 invariant: 어제 BUY → 오늘 큰 손실 SELL이 audit에 있을 때
    다음 주문이 max_daily_loss로 거부되어야 한다. 145 이전에는 daily_realized_pnl
    이 0에 머물러 어떤 audit 상태에서도 거부되지 않았음."""
    # max_daily_loss를 작게 설정해 명확히 트리거.
    client.test_risk_manager.policy.max_daily_loss = 100

    today = today_utc()
    yesterday_dt = datetime.combine(today - timedelta(days=1),
                                     datetime.min.time(), tzinfo=timezone.utc)
    today_dt     = datetime.now(timezone.utc)
    with client.test_db_factory() as db:
        # 어제 BUY 1주 @ 100
        db.add(_audit(side="BUY", qty=1, fill_price=1000, created_at=yesterday_dt))
        # 오늘 SELL 1주 @ 500 — 오늘 -500 손실
        db.add(_audit(side="SELL", qty=1, fill_price=500, created_at=today_dt))
        db.commit()

    # 새 주문 — 한도(-100) 이미 초과한 상태(-500). 리스크가 거부해야 한다.
    res = client.post("/api/broker/orders", json={
        "symbol": "005930", "side": "BUY", "quantity": 1,
    })
    assert res.status_code == 400, res.text
    # detail은 라우트 envelope — reasons는 audit row에서 직접 검증.
    # 라우트의 envelope이 reasons를 담지 않더라도 audit에 남아있어야 한다.
    with client.test_db_factory() as db:
        new_rows = db.execute(
            select(OrderAuditLog).where(OrderAuditLog.decision == "REJECTED")
        ).scalars().all()
        # 새로 추가된 1건 — REJECTED, daily loss reason 포함.
        assert len(new_rows) == 1
        assert any("daily loss" in r.lower() for r in new_rows[0].reasons), \
            new_rows[0].reasons


def test_route_order_does_not_reject_when_today_pnl_within_limit(client):
    """오늘 손실이 한도 내면 거부 X — 회귀 가드."""
    client.test_risk_manager.policy.max_daily_loss = 1_000_000  # 100만원 한도

    today_dt = datetime.now(timezone.utc)
    with client.test_db_factory() as db:
        # 오늘 BUY-SELL 사이클로 -10원만 손실 (한도의 1만분의 1).
        db.add_all([
            _audit(side="BUY",  qty=1, fill_price=100, created_at=today_dt),
            _audit(side="SELL", qty=1, fill_price=90,  created_at=today_dt),
        ])
        db.commit()

    res = client.post("/api/broker/orders", json={
        "symbol": "005930", "side": "BUY", "quantity": 1,
    })
    # SIMULATION 모드 + 한도 내 → APPROVED + executed.
    assert res.status_code == 200, res.text


def test_route_order_recomputes_pnl_per_call(client):
    """route_order는 매 호출마다 daily_realized_pnl을 재계산 — singleton 상태가
    오염돼도 다음 호출에서 정확한 값이 들어간다."""
    risk = client.test_risk_manager
    risk.policy.max_daily_loss = 10_000

    # 단순 호출 1: audit row 없음 → 0이 채워져야 함.
    risk.daily_realized_pnl = -999_999  # 의도적 오염
    res = client.post("/api/broker/orders", json={
        "symbol": "005930", "side": "BUY", "quantity": 1,
    })
    assert res.status_code == 200
    # route_order가 0으로 재계산 후 평가했다는 간접 증거 — 오염 값이 그대로면
    # 한도 초과로 거부됐을 것.
    assert risk.daily_realized_pnl == 0


# ---------- 166: KST date boundary ----------

def test_today_kst_returns_kst_date():
    """today_kst()는 UTC와 9시간 차이로 다른 date 가능."""
    # KST 기준 현재 date.
    expected = datetime.now(KST).date()
    assert today_kst() == expected


def test_pnl_uses_kst_by_default():
    """compute_today_realized_pnl 기본값 = KST. 한국 장 종료 후(15:00 UTC)
    동안 청산된 거래는 그 날 KST date로 카운트.

    Note: SQLAlchemy DateTime 컬럼은 timezone strip이라 UTC clock으로 저장
    필요 — 테스트는 의도된 KST 시각을 UTC로 변환 후 입력."""
    Session = _make_session()
    # KST 14:00 = UTC 05:00. SQLite는 naive UTC clock 저장.
    utc_05 = datetime(2026, 5, 6, 5, 0, tzinfo=timezone.utc)
    with Session() as db:
        db.add_all([
            _audit(side="BUY",  qty=1, fill_price=100, created_at=utc_05),
            _audit(side="SELL", qty=1, fill_price=110, created_at=utc_05),
        ])
        db.commit()
        # KST 기준 SELL은 5/6 14:00 KST → date 5/6. PnL +10 카운트.
        pnl = compute_today_realized_pnl(db, today=date(2026, 5, 6))  # tz default KST
    assert pnl == 10


def test_pnl_kst_boundary_around_midnight_kst():
    """KST 자정(00:00 KST = 15:00 UTC) 직전/직후 청산이 KST date에 정확히 귀속."""
    Session = _make_session()
    # KST 5/5 23:30 = UTC 5/5 14:30 (전날). KST 5/6 00:30 = UTC 5/5 15:30 (오늘 KST).
    utc_5_14_30 = datetime(2026, 5, 5, 14, 30, tzinfo=timezone.utc)
    utc_5_15_30 = datetime(2026, 5, 5, 15, 30, tzinfo=timezone.utc)
    with Session() as db:
        # 5/5 KST BUY → 5/6 KST SELL (overnight 청산).
        db.add_all([
            _audit(side="BUY",  qty=1, fill_price=100, created_at=utc_5_14_30),  # KST 5/5 23:30
            _audit(side="SELL", qty=1, fill_price=120, created_at=utc_5_15_30),  # KST 5/6 00:30
        ])
        db.commit()
        pnl_5_5 = compute_today_realized_pnl(db, today=date(2026, 5, 5), tz=KST)
        pnl_5_6 = compute_today_realized_pnl(db, today=date(2026, 5, 6), tz=KST)
    # SELL이 KST 5/6에 일어남 → 그 날 PnL +20.
    assert pnl_5_5 == 0
    assert pnl_5_6 == 20


def test_pnl_kst_vs_utc_diverge_at_kst_midnight():
    """동일 SELL이 KST 기준과 UTC 기준에서 다른 date로 잡힌다 — 회귀 가드."""
    Session = _make_session()
    # KST 5/6 00:30 = UTC 5/5 15:30. KST date=5/6, UTC date=5/5.
    utc_5_15_30 = datetime(2026, 5, 5, 15, 30, tzinfo=timezone.utc)
    utc_buy_earlier = datetime(2026, 5, 5, 5, 0, tzinfo=timezone.utc)  # KST 5/5 14:00
    with Session() as db:
        db.add_all([
            _audit(side="BUY",  qty=1, fill_price=100, created_at=utc_buy_earlier),
            _audit(side="SELL", qty=1, fill_price=110, created_at=utc_5_15_30),
        ])
        db.commit()
        # KST 기준: SELL은 5/6에.
        pnl_kst_5_6 = compute_today_realized_pnl(db, today=date(2026, 5, 6), tz=KST)
        # UTC 기준: SELL은 5/5에 (UTC 15:30).
        pnl_utc_5_5 = compute_today_realized_pnl(db, today=date(2026, 5, 5), tz=timezone.utc)
    assert pnl_kst_5_6 == 10  # KST 기준 오늘 PnL
    assert pnl_utc_5_5 == 10  # UTC 기준 어제(=KST 5/6) PnL


def test_pnl_explicit_utc_tz_backwards_compat():
    """tz=timezone.utc 명시하면 145 이전 UTC 기반 동작 — 같은 입력에서 동일."""
    Session = _make_session()
    # 정상 흐름: 같은 UTC date 안의 BUY+SELL.
    utc_now = datetime(2026, 5, 6, 5, 0, tzinfo=timezone.utc)
    with Session() as db:
        db.add_all([
            _audit(side="BUY",  qty=1, fill_price=100, created_at=utc_now),
            _audit(side="SELL", qty=1, fill_price=110, created_at=utc_now),
        ])
        db.commit()
        # 양 tz 모두 5/6 date — 같은 결과.
        pnl_utc = compute_today_realized_pnl(db, today=date(2026, 5, 6), tz=timezone.utc)
        pnl_kst = compute_today_realized_pnl(db, today=date(2026, 5, 6), tz=KST)
    assert pnl_utc == 10
    assert pnl_kst == 10