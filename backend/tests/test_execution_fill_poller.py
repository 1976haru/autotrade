import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.brokers.base import BrokerAdapter, OrderResult, OrderSide, OrderStatus
from app.db.base import Base
from app.db.models import OrderAuditLog
from app.execution.fill_poller import FillPoller, poll_once


def run(coro):
    return asyncio.run(coro)


# 2026-06-01 05:00 UTC = 14:00 KST, 월요일 정규장 중 — 실행 시각(실제 wall-clock)에
# 관계없이 결정적인 "장중, 방금 생성된 주문" 상태를 만들기 위한 고정 시각. staleness
# 판정이 실제 현재 시각에 좌우되면(예: 테스트가 야간/주말에 도는 CI) 아래 기존
# 테스트들이 실행 시점에 따라 flaky 해진다 — created_at/now 를 모두 이 값으로
# 고정해 만든다.
_INTRADAY_NOW = datetime(2026, 6, 1, 5, 0, 0, tzinfo=timezone.utc)


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _audit(
    db,
    *,
    broker_order_id: str | None = "K-001",
    broker_status:   str | None = "RECEIVED",
    quantity:        int = 10,
    filled_quantity: int = 0,
    avg_fill_price:  int | None = None,
    executed:        bool = True,
    created_at=_INTRADAY_NOW,
) -> OrderAuditLog:
    a = OrderAuditLog(
        mode="PAPER", symbol="005930", side="BUY", quantity=quantity,
        order_type="MARKET", latest_price=75_000,
        decision="APPROVED", reasons=[],
        executed=executed,
        broker_order_id=broker_order_id,
        broker_status=broker_status,
        filled_quantity=filled_quantity,
        avg_fill_price=avg_fill_price,
        created_at=created_at,
    )
    db.add(a)
    db.commit()
    return a


class _ScriptedBroker(BrokerAdapter):
    """Minimal broker stub — only get_order_status is interesting for the poller."""

    def __init__(self, status_map: dict[str, OrderResult] | None = None,
                 raise_on: dict[str, Exception] | None = None):
        self.status_map = status_map or {}
        self.raise_on   = raise_on or {}
        self.calls: list[str] = []

    async def get_order_status(self, order_id: str) -> OrderResult:
        self.calls.append(order_id)
        if order_id in self.raise_on:
            raise self.raise_on[order_id]
        return self.status_map.get(order_id, OrderResult(
            order_id=order_id, status=OrderStatus.RECEIVED,
            symbol="005930", side=OrderSide.BUY, quantity=0,
        ))

    # All other abstract methods raise — they are not used by the poller.
    async def get_price(self, symbol: str) -> Any:        raise NotImplementedError
    async def get_balance(self) -> Any:                   raise NotImplementedError
    async def get_positions(self) -> list[Any]:           raise NotImplementedError
    async def place_order(self, order: Any) -> Any:       raise NotImplementedError
    async def cancel_order(self, order_id: str) -> Any:   raise NotImplementedError


# ---------- poll_once ----------

def test_poll_once_no_candidates_returns_zero():
    Session = _session_factory()
    with Session() as db:
        broker = _ScriptedBroker()
        assert run(poll_once(broker, db, now=_INTRADAY_NOW)) == 0
        assert broker.calls == []


def test_poll_once_skips_unexecuted_audits():
    Session = _session_factory()
    with Session() as db:
        _audit(db, executed=False)
        broker = _ScriptedBroker()
        assert run(poll_once(broker, db, now=_INTRADAY_NOW)) == 0
        assert broker.calls == []


def test_poll_once_skips_already_filled():
    Session = _session_factory()
    with Session() as db:
        _audit(db, broker_status="FILLED", filled_quantity=10)
        broker = _ScriptedBroker()
        assert run(poll_once(broker, db, now=_INTRADAY_NOW)) == 0
        assert broker.calls == []


def test_poll_once_updates_received_to_filled():
    Session = _session_factory()
    filled_result = OrderResult(
        order_id="K-001", status=OrderStatus.FILLED,
        symbol="005930", side=OrderSide.BUY, quantity=10,
        filled_quantity=10, avg_fill_price=75_500,
    )
    with Session() as db:
        a = _audit(db)
        broker = _ScriptedBroker(status_map={"K-001": filled_result})
        assert run(poll_once(broker, db, now=_INTRADAY_NOW)) == 1
        # Reload to confirm commit
    with Session() as db2:
        loaded = db2.execute(select(OrderAuditLog).where(OrderAuditLog.id == a.id)).scalar_one()
        assert loaded.broker_status == "FILLED"
        assert loaded.filled_quantity == 10
        assert loaded.avg_fill_price == 75_500


def test_poll_once_advances_partially_filled():
    Session = _session_factory()
    partial = OrderResult(
        order_id="K-001", status=OrderStatus.PARTIALLY_FILLED,
        symbol="005930", side=OrderSide.BUY, quantity=10,
        filled_quantity=4, avg_fill_price=75_300,
    )
    with Session() as db:
        a = _audit(db, broker_status="PARTIALLY_FILLED", filled_quantity=2)
        broker = _ScriptedBroker(status_map={"K-001": partial})
        assert run(poll_once(broker, db, now=_INTRADAY_NOW)) == 1
    with Session() as db2:
        loaded = db2.execute(select(OrderAuditLog).where(OrderAuditLog.id == a.id)).scalar_one()
        assert loaded.broker_status == "PARTIALLY_FILLED"
        assert loaded.filled_quantity == 4


def test_poll_once_skips_when_nothing_changed():
    Session = _session_factory()
    same = OrderResult(
        order_id="K-001", status=OrderStatus.RECEIVED,
        symbol="005930", side=OrderSide.BUY, quantity=10,
        filled_quantity=0,
    )
    with Session() as db:
        _audit(db, broker_status="RECEIVED", filled_quantity=0)
        broker = _ScriptedBroker(status_map={"K-001": same})
        assert run(poll_once(broker, db, now=_INTRADAY_NOW)) == 0


def test_poll_once_returns_zero_when_broker_does_not_implement():
    Session = _session_factory()
    with Session() as db:
        _audit(db)
        broker = _ScriptedBroker(raise_on={"K-001": NotImplementedError("mock no support")})
        assert run(poll_once(broker, db, now=_INTRADAY_NOW)) == 0


def test_poll_once_continues_past_a_failing_row():
    Session = _session_factory()
    ok = OrderResult(
        order_id="K-002", status=OrderStatus.FILLED,
        symbol="005930", side=OrderSide.BUY, quantity=10,
        filled_quantity=10, avg_fill_price=75_000,
    )
    with Session() as db:
        _audit(db, broker_order_id="K-001")
        _audit(db, broker_order_id="K-002")
        broker = _ScriptedBroker(
            status_map={"K-002": ok},
            raise_on={"K-001": RuntimeError("upstream flaked")},
        )
        assert run(poll_once(broker, db, now=_INTRADAY_NOW)) == 1
    with Session() as db2:
        rows = db2.execute(select(OrderAuditLog).order_by(OrderAuditLog.id)).scalars().all()
        assert rows[0].broker_status == "RECEIVED"  # untouched after failure
        assert rows[1].broker_status == "FILLED"


# ---------- regression: KIS submitted-but-not-concluded must not flip to REJECTED ----------

class _FakeCcldClient:
    """Duck-typed KIS client — only inquire_daily_ccld is used by get_order_status."""

    def __init__(self, ccld: dict):
        self._ccld = ccld

    async def inquire_daily_ccld(self, cano, prdt):
        return self._ccld


def test_poll_once_does_not_reject_submitted_kis_order_absent_from_ccld():
    """Regression (2026-06-02): a KIS paper BUY accepted at submission (has ODNO)
    but not yet in today's daily-ccld must keep broker_status=RECEIVED through the
    poller — never downgraded to REJECTED. Mirrors the 005935 BUY case where the
    order succeeded ("모의투자 매수주문이 완료 되었습니다") yet was recorded REJECTED.
    """
    from app.brokers.kis import KisBrokerAdapter

    Session = _session_factory()
    with Session() as db:
        a = _audit(db, broker_order_id="0000003912", broker_status="RECEIVED")
        broker = KisBrokerAdapter(
            app_key="k", app_secret="s", account_no="1234567801",
            client=_FakeCcldClient({"output1": []}),  # nothing concluded yet
        )
        # RECEIVED stays RECEIVED → no clobber, no update.
        assert run(poll_once(broker, db, now=_INTRADAY_NOW)) == 0
    with Session() as db2:
        loaded = db2.execute(select(OrderAuditLog).where(OrderAuditLog.id == a.id)).scalar_one()
        assert loaded.broker_status == "RECEIVED"   # NOT REJECTED (the bug)
        assert loaded.filled_quantity == 0


def test_poll_once_advances_kis_order_once_it_concludes_filled():
    """The same submitted order still advances to FILLED once it appears concluded
    in daily-ccld — proving the RECEIVED-while-pending fix does not strand fills.
    """
    from app.brokers.kis import KisBrokerAdapter

    Session = _session_factory()
    ccld_filled = {"output1": [{
        "odno": "0000003912", "pdno": "005935", "sll_buy_dvsn_cd": "02",
        "ord_qty": "10", "tot_ccld_qty": "10", "avg_prvs": "75500", "cncl_yn": "N",
    }]}
    with Session() as db:
        a = _audit(db, broker_order_id="0000003912", broker_status="RECEIVED")
        broker = KisBrokerAdapter(
            app_key="k", app_secret="s", account_no="1234567801",
            client=_FakeCcldClient(ccld_filled),
        )
        assert run(poll_once(broker, db, now=_INTRADAY_NOW)) == 1
    with Session() as db2:
        loaded = db2.execute(select(OrderAuditLog).where(OrderAuditLog.id == a.id)).scalar_one()
        assert loaded.broker_status == "FILLED"
        assert loaded.filled_quantity == 10
        assert loaded.avg_fill_price == 75500


# ---------- give-up on stale orders (2026-07-06 073240 무한재시도 사고 회귀) ----------
# 073240 매수(324주 중 1주만 체결, PARTIALLY_FILLED)가 장마감 후 4시간+ 동안
# 5초 간격으로 재조회되며 EGW00201(레이트리밋)에 1,294회 계속 걸린 사고의 회귀
# 테스트. 포기 조건 3가지(자정 넘어감 / 세션마감+유예 지남 / 절대 상한 4시간)를
# 각각 검증하고, 포기 시 broker 를 아예 호출하지 않는지, 이미 체결된 수량은
# 그대로 보존되는지 확인한다.

def test_poll_once_gives_up_when_order_day_has_rolled_over():
    Session = _session_factory()
    prev_day = _INTRADAY_NOW - timedelta(days=1)
    with Session() as db:
        a = _audit(db, broker_status="PARTIALLY_FILLED", filled_quantity=1, created_at=prev_day)
        broker = _ScriptedBroker()
        assert run(poll_once(broker, db, now=_INTRADAY_NOW)) == 1
        assert broker.calls == []  # 포기 — broker 조회 자체를 안 함
    with Session() as db2:
        loaded = db2.execute(select(OrderAuditLog).where(OrderAuditLog.id == a.id)).scalar_one()
        assert loaded.broker_status == "CANCELED"
        assert loaded.filled_quantity == 1  # 이미 체결된 수량은 보존


def test_poll_once_gives_up_after_session_close_plus_grace():
    Session = _session_factory()
    # _INTRADAY_NOW = 2026-06-01 05:00 UTC = 14:00 KST. 세션마감(15:30)+유예(10분)
    # = 15:40 KST = 06:40 UTC. 06:41 UTC 는 그 1분 뒤 — 포기 대상.
    just_after_close_grace = datetime(2026, 6, 1, 6, 41, 0, tzinfo=timezone.utc)
    with Session() as db:
        a = _audit(
            db, broker_status="PARTIALLY_FILLED", filled_quantity=1,
            quantity=324, created_at=_INTRADAY_NOW,
        )
        broker = _ScriptedBroker()
        assert run(poll_once(broker, db, now=just_after_close_grace)) == 1
        assert broker.calls == []
    with Session() as db2:
        loaded = db2.execute(select(OrderAuditLog).where(OrderAuditLog.id == a.id)).scalar_one()
        assert loaded.broker_status == "CANCELED"
        assert loaded.filled_quantity == 1
        assert loaded.quantity == 324  # 나머지 323주는 미체결 포기(취소) — 요청 수량 자체는 기록 보존


def test_poll_once_still_open_just_before_close_plus_grace_keeps_polling():
    """close+grace 경계 바로 앞(15:39 KST)에서는 아직 포기하지 않고 정상 조회한다."""
    Session = _session_factory()
    just_before_close_grace = datetime(2026, 6, 1, 6, 39, 0, tzinfo=timezone.utc)  # 15:39 KST
    same = OrderResult(
        order_id="K-001", status=OrderStatus.PARTIALLY_FILLED,
        symbol="005930", side=OrderSide.BUY, quantity=10, filled_quantity=1,
    )
    with Session() as db:
        _audit(db, broker_status="PARTIALLY_FILLED", filled_quantity=1, created_at=_INTRADAY_NOW)
        broker = _ScriptedBroker(status_map={"K-001": same})
        run(poll_once(broker, db, now=just_before_close_grace))
        assert broker.calls == ["K-001"]  # 아직 포기 안 함 — 정상 조회


def test_poll_once_gives_up_after_max_poll_age_even_intraday():
    """장중이어도(마감 전) 생성 후 4시간이 지나면 무한 매달림 방지 상한으로 포기한다."""
    Session = _session_factory()
    opened_at = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)   # 09:00 KST(개장)
    four_hours_one_min_later = datetime(2026, 6, 1, 4, 1, 0, tzinfo=timezone.utc)  # 13:01 KST, 마감 전
    with Session() as db:
        a = _audit(db, broker_status="PARTIALLY_FILLED", filled_quantity=1, created_at=opened_at)
        broker = _ScriptedBroker()
        assert run(poll_once(broker, db, now=four_hours_one_min_later)) == 1
        assert broker.calls == []
    with Session() as db2:
        loaded = db2.execute(select(OrderAuditLog).where(OrderAuditLog.id == a.id)).scalar_one()
        assert loaded.broker_status == "CANCELED"


# ---------- exponential backoff on repeated failures ----------

def test_poll_once_backoff_skips_retry_before_delay_elapses():
    Session = _session_factory()
    with Session() as db:
        _audit(db)
        broker = _ScriptedBroker(raise_on={"K-001": RuntimeError("EGW00201 rate limited")})
        backoff: dict[int, tuple[int, float]] = {}
        # 1st tick: fails, schedules backoff.
        run(poll_once(broker, db, now=_INTRADAY_NOW, backoff=backoff))
        assert broker.calls == ["K-001"]
        # 2nd tick a moment later (well inside the base 10s backoff window):
        # must NOT call the broker again.
        soon_after = _INTRADAY_NOW + timedelta(seconds=2)
        run(poll_once(broker, db, now=soon_after, backoff=backoff))
        assert broker.calls == ["K-001"]  # 재호출 안 함


def test_poll_once_backoff_retries_after_delay_elapses():
    Session = _session_factory()
    with Session() as db:
        _audit(db)
        broker = _ScriptedBroker(raise_on={"K-001": RuntimeError("EGW00201 rate limited")})
        backoff: dict[int, tuple[int, float]] = {}
        run(poll_once(broker, db, now=_INTRADAY_NOW, backoff=backoff))
        assert broker.calls == ["K-001"]
        after_base_delay = _INTRADAY_NOW + timedelta(seconds=11)  # base backoff(10s) 경과
        run(poll_once(broker, db, now=after_base_delay, backoff=backoff))
        assert broker.calls == ["K-001", "K-001"]  # 이번엔 재조회


def test_poll_once_backoff_doubles_on_consecutive_failures():
    Session = _session_factory()
    with Session() as db:
        _audit(db)
        broker = _ScriptedBroker(raise_on={"K-001": RuntimeError("EGW00201 rate limited")})
        backoff: dict[int, tuple[int, float]] = {}
        run(poll_once(broker, db, now=_INTRADAY_NOW, backoff=backoff))
        fail_count_1, retry_at_1 = backoff[1]
        assert fail_count_1 == 1
        assert retry_at_1 - _INTRADAY_NOW.timestamp() == 10  # base

        after_first_delay = _INTRADAY_NOW + timedelta(seconds=11)
        run(poll_once(broker, db, now=after_first_delay, backoff=backoff))
        fail_count_2, retry_at_2 = backoff[1]
        assert fail_count_2 == 2
        assert retry_at_2 - after_first_delay.timestamp() == 20  # 2x base — 지수 증가


def test_poll_once_backoff_resets_on_success():
    Session = _session_factory()
    filled = OrderResult(
        order_id="K-001", status=OrderStatus.FILLED,
        symbol="005930", side=OrderSide.BUY, quantity=10,
        filled_quantity=10, avg_fill_price=75_000,
    )
    with Session() as db:
        _audit(db)
        broker = _ScriptedBroker(status_map={"K-001": filled})
        backoff: dict[int, tuple[int, float]] = {1: (3, _INTRADAY_NOW.timestamp() - 1)}
        run(poll_once(broker, db, now=_INTRADAY_NOW, backoff=backoff))
        assert 1 not in backoff  # 성공하면 백오프 기록 해제


# ---------- FillPoller lifecycle ----------

def test_fill_poller_rejects_non_positive_interval():
    with pytest.raises(ValueError):
        FillPoller(broker_factory=lambda: None, session_factory=lambda: None, interval=0)


def test_fill_poller_start_stop_invokes_poll_at_least_once():
    Session = _session_factory()
    with Session() as db:
        _audit(db)
    filled = OrderResult(
        order_id="K-001", status=OrderStatus.FILLED,
        symbol="005930", side=OrderSide.BUY, quantity=10,
        filled_quantity=10, avg_fill_price=75_000,
    )
    broker = _ScriptedBroker(status_map={"K-001": filled})

    async def driver():
        poller = FillPoller(
            broker_factory=lambda: broker,
            session_factory=Session,
            interval=1,
            now_factory=lambda: _INTRADAY_NOW,
        )
        poller.start()
        # Yield long enough for the first tick; the loop sleeps after each tick.
        await asyncio.sleep(0.05)
        await poller.stop()

    run(driver())
    assert broker.calls == ["K-001"]
