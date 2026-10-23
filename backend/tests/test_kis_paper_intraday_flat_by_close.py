"""KIS-PAPER-INTRADAY-FLAT-BY-CLOSE-V1 — 14:30 신규매수 차단 + 15:15 당일 청산 검증.

실제 KIS 호출 0건: FakeKisPaperBroker 는 KisBrokerAdapter(is_paper=True) 를 상속해
*네트워크 메서드만* 메모리로 대체한다 — route_order → RiskManager → OrderExecutor →
broker.place_order 의 실제 sanctioned 체인을 그대로 통과시키고, place_order 호출
(= KIS 주문 API write) 횟수를 센다.

시나리오 라벨(A~W)은 작업 지시서 STEP 7 항목과 1:1.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.brokers.base import (
    Balance,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Quote,
)
from app.brokers.kis import KisBrokerAdapter, parse_ccld_row
from app.brokers.mock_broker import MockBrokerAdapter
from app.db.base import Base
from app.db.models import EodLiquidationState, OrderAuditLog
from app.execution.order_router import route_order
from app.kis_paper.auto_executor import (
    KisPaperAutoDecision,
    execute_kis_paper_auto_order,
)
from app.kis_paper.auto_permission import (
    KisPaperOrderPermissionInput,
    KisPaperPermReason,
    evaluate_kis_paper_order_permission,
)
from app.kis_paper.eod_liquidation import (
    EodState,
    acknowledge_incident,
    acquire_lease,
    eod_status,
    run_eod_step,
)
from app.kis_paper.intraday_buy_gate import program_buy_block_reason
from app.risk.risk_manager import RiskDecision, RiskManager, RiskPolicy
from app.scheduler.intraday_session import (
    IntradayPhase,
    IntradayPolicy,
    load_krx_calendar,
)

KST = timezone(timedelta(hours=9))
TRADING_DAY = date(2026, 10, 22)        # 목요일, 거래일


def kst(h: int, m: int, s: int = 0, d: date = TRADING_DAY) -> datetime:
    return datetime.combine(d, time(h, m, s), tzinfo=KST).astimezone(timezone.utc)


# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False,
                        expire_on_commit=False)


@pytest.fixture
def db(session_factory):
    s = session_factory()
    yield s
    s.close()


def make_settings(**kw):
    base = dict(
        enable_kis_paper_auto_trading=True, kis_paper_auto_order_dry_run=False,
        kis_is_paper=True, enable_live_trading=False, enable_ai_execution=False,
        market_data_provider="kis", kis_paper_smoke_mode=False,
        kis_paper_auto_max_order_notional=1_000_000, kis_paper_auto_max_orders_per_day=10,
        kis_paper_auto_order_window_start="09:05", kis_paper_auto_order_window_end="14:30",
        kis_paper_auto_min_confidence=0.6, kis_paper_auto_min_quality_score=60,
        kis_paper_buy_cutoff_minutes_before_close=60,
        kis_paper_eod_flatten_minutes_before_close=15, kis_paper_eod_staged_symbols_per_step=2,
        kis_paper_eod_closing_auction_orders_enabled=False,
        kis_paper_eod_max_attempts_per_symbol=3,
        kis_paper_auto_max_exit_order_notional=0,
        krx_calendar_override_path="",
        ai_paper_tick_interval_seconds=30,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class FakeKisPaperBroker(KisBrokerAdapter):
    """KIS 모의 어댑터 — 네트워크만 메모리로 대체 (실제 KIS 호출 0건).

    fill_mode: "fill"(시장가 즉시 체결) / "none"(접수만, 미체결) / "partial"(절반 체결).
    """

    def __init__(self, holdings: dict[str, int], *, price: int = 10_000,
                 fill_mode: str = "fill"):
        super().__init__(app_key="test-key", app_secret="test-secret",
                         account_no="1234567801", is_paper=True)
        self.holdings = dict(holdings)
        self.price = price
        self.fill_mode = fill_mode
        self.rows: list[dict] = []          # ccld rows (parsed shape)
        self.place_calls: list[OrderRequest] = []
        self.fail_positions = False
        self.fail_orders = False
        self.cancel_calls: list[tuple[str, str | None]] = []
        self.cancel_reject = False

    def _open_sell(self, sym: str) -> int:
        return sum(r["remaining"] for r in self.rows
                   if r["symbol"] == sym and r["side"] == "SELL")

    async def get_price(self, symbol: str) -> Quote:
        return Quote(symbol=symbol, price=self.price,
                     timestamp=datetime.now(timezone.utc).isoformat(), source="kis")

    async def get_balance(self) -> Balance:
        return Balance(cash=50_000_000, equity=60_000_000, buying_power=50_000_000)

    async def get_positions(self) -> list[Position]:
        if self.fail_positions:
            raise RuntimeError("EGW00201")
        return [Position(symbol=s, quantity=q, avg_price=self.price, market_price=self.price,
                         sellable_quantity=max(0, q - self._open_sell(s)))
                for s, q in self.holdings.items() if q > 0]

    async def place_order(self, order: OrderRequest) -> OrderResult:
        self.place_calls.append(order)
        odno = f"{len(self.place_calls):010d}"
        if self.fill_mode == "fill":
            filled = order.quantity
        elif self.fill_mode == "partial":
            filled = order.quantity // 2
        else:
            filled = 0
        sign = -1 if order.side == OrderSide.SELL else 1
        self.holdings[order.symbol] = self.holdings.get(order.symbol, 0) + sign * filled
        self.rows.append({"odno": odno, "symbol": order.symbol, "side": order.side.value,
                          "ord_qty": order.quantity, "filled": filled,
                          "remaining": order.quantity - filled, "canceled": False,
                          "ord_tmd": "", "ord_gno_brno": "06010"})
        return OrderResult(order_id=odno, status=OrderStatus.RECEIVED, symbol=order.symbol,
                           side=order.side, quantity=order.quantity)

    async def list_today_orders(self, *, kst_date: str) -> list[dict]:
        if self.fail_orders:
            raise RuntimeError("EGW00201")
        return [dict(r) for r in self.rows]

    async def cancel_order(self, order_id: str, *, org_no=None, symbol="", side=OrderSide.BUY):
        self.cancel_calls.append((order_id, org_no))
        if self.cancel_reject or not org_no:
            return OrderResult(order_id="", status=OrderStatus.REJECTED, symbol=symbol,
                               side=side, quantity=0, message="[40000000] reject")
        for r in self.rows:
            if r["odno"] == order_id:
                r["remaining"] = 0
        return OrderResult(order_id=f"C{order_id}", status=OrderStatus.RECEIVED,
                           symbol=symbol, side=side, quantity=0, message="[APBK0013] ok")

    def fill_all(self) -> None:
        """미체결 잔량 체결 처리 (시장 체결 시뮬)."""
        for r in self.rows:
            if r["remaining"] > 0:
                sign = -1 if r["side"] == "SELL" else 1
                self.holdings[r["symbol"]] = self.holdings.get(r["symbol"], 0) + sign * r["remaining"]
                r["filled"] += r["remaining"]
                r["remaining"] = 0


def seed_bot_position(db, symbol: str, qty: int, *, odno: str | None = None) -> None:
    """과거(전일) 프로그램 BUY 체결 — 소유권 원장."""
    db.add(OrderAuditLog(
        created_at=datetime(2026, 10, 21, 1, 0, tzinfo=timezone.utc), mode="PAPER",
        requested_by_ai=False, symbol=symbol, side="BUY", quantity=qty,
        order_type="MARKET", latest_price=10_000, decision="APPROVED", reasons=[],
        trade_reason="kis_paper_auto", executed=True,
        broker_order_id=odno or f"OLD-{symbol}", broker_status="FILLED",
        filled_quantity=qty,
    ))
    db.commit()


def step(session_factory, broker, now, *, settings=None, risk=None, owner="proc-A",
         loop_state="RUNNING", events=None, broker_is_kis_paper=None):
    return asyncio.run(run_eod_step(
        now=now, settings=settings or make_settings(), session_factory=session_factory,
        broker=broker, risk=risk or RiskManager(RiskPolicy()), route_order_fn=route_order,
        loop_state=loop_state, owner_id=owner,
        event_sink=(lambda **kw: events.append(kw)) if events is not None else (lambda **kw: None),
        broker_is_kis_paper=broker_is_kis_paper, credentials_present=True,
    ))


def perm(**kw) -> KisPaperOrderPermissionInput:
    base = dict(
        enable_kis_paper_auto_trading=True, dry_run=False, kis_is_paper=True,
        enable_live_trading=False, broker_is_kis_paper=True, credentials_present=True,
        side="BUY", notional_krw=500_000, confidence=0.9, quality_score=90,
        has_exit_plan=True, window_start="09:05", window_end="14:30", now=kst(10, 0),
    )
    base.update(kw)
    return KisPaperOrderPermissionInput(**base)


def _buy_decision(**kw) -> KisPaperAutoDecision:
    base = dict(symbol="005930", side="BUY", quantity=10, price=10_000,
                selected_strategies=["ORB"], confidence=0.9, quality_score=90,
                has_exit_plan=True, price_source="kis")
    base.update(kw)
    return KisPaperAutoDecision(**base)


def run_exec(db, broker, decision, now, settings=None, risk=None):
    return asyncio.run(execute_kis_paper_auto_order(
        db, decision=decision, settings=settings or make_settings(), broker=broker,
        risk=risk or RiskManager(RiskPolicy()), broker_is_kis_paper=True,
        credentials_present=True, route_order_fn=route_order, now=now, record=False,
        chain_id=f"t-{decision.side}-{now.timestamp()}"))


# ── A/B: 14:30 신규 BUY 경계 ──────────────────────────────────────────────


def test_A_buy_allowed_at_14_29_59():
    assert evaluate_kis_paper_order_permission(perm(now=kst(14, 29, 59))).allowed is True


@pytest.mark.parametrize("t", [(14, 30, 0), (14, 30, 1), (14, 59, 0), (15, 16, 0)])
def test_B_buy_blocked_at_and_after_cutoff(t):
    res = evaluate_kis_paper_order_permission(perm(now=kst(*t)))
    assert res.allowed is False
    assert res.reason_code == KisPaperPermReason.BUY_CUTOFF_ACTIVE.value


def test_B_cutoff_cannot_be_extended_by_window_setting():
    # 운영자 .env 가 window_end=15:20 이어도 close−60분(14:30) 이 상한.
    res = evaluate_kis_paper_order_permission(perm(window_end="15:20", now=kst(14, 45)))
    assert res.reason_code == KisPaperPermReason.BUY_CUTOFF_ACTIVE.value


@pytest.mark.parametrize("t,expect_orders", [((14, 29, 59), 1), ((14, 30, 0), 0),
                                             ((14, 30, 1), 0), ((14, 59, 0), 0)])
def test_AB_route_order_end_to_end_buy_boundary(db, t, expect_orders):
    broker = FakeKisPaperBroker({})
    r = run_exec(db, broker, _buy_decision(), kst(*t))
    assert len(broker.place_calls) == expect_orders
    if expect_orders == 0:
        assert r.broker_order_sent is False


def test_B_route_order_rejects_program_buy_even_if_executor_gate_bypassed(db):
    # 다른 경로(지연 호출/재시작)가 executor 게이트 없이 route_order 로 BUY 를 보내도 차단.
    broker = FakeKisPaperBroker({})
    order = OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1,
                         order_type=OrderType.MARKET, trade_reason="kis_paper_auto")
    from app.core.modes import OperationMode
    res = asyncio.run(route_order(order=order, requested_by_ai=False, mode=OperationMode.PAPER,
                                  broker=broker, risk=RiskManager(RiskPolicy()), db=db,
                                  now=kst(14, 30)))
    assert res.decision == RiskDecision.REJECTED
    assert res.reasons[0].startswith("BUY_CUTOFF_ACTIVE")
    assert broker.place_calls == []
    assert res.audit.executed is False


def test_B_manual_buy_not_subject_to_program_cutoff(db):
    # 사람의 수동매수(manual_buy)는 프로그램 게이트 대상 아님 (문서화된 설계).
    code, _ = program_buy_block_reason(db, settings=make_settings(), now=kst(10, 0))
    assert code is None


def test_B_approval_queue_program_buy_blocked_after_cutoff(db, monkeypatch):
    # 승인 큐(PermissionGate.approve)는 route_order 를 다시 안 거치므로 자체 재검증.
    from app.db.models import PendingApproval
    from app.permission import gate as gate_mod
    from app.permission.gate import ApprovalRiskCheckFailedError, PermissionGate
    audit = OrderAuditLog(mode="PAPER", requested_by_ai=False, symbol="005930", side="BUY",
                          quantity=1, order_type="MARKET", latest_price=10_000,
                          decision="NEEDS_APPROVAL", reasons=[], trade_reason="kis_paper_auto")
    db.add(audit)
    db.commit()
    ap = PendingApproval(audit_id=audit.id, mode="PAPER", symbol="005930", side="BUY",
                         quantity=1, order_type="MARKET", status="PENDING")
    db.add(ap)
    db.commit()
    import app.kis_paper.intraday_buy_gate as g
    real = g.program_buy_block_reason
    monkeypatch.setattr(g, "program_buy_block_reason",
                        lambda d, **kw: real(d, settings=make_settings(), now=kst(14, 31)))
    broker = FakeKisPaperBroker({})
    with pytest.raises(ApprovalRiskCheckFailedError):
        asyncio.run(PermissionGate(db).approve(ap.id, broker, RiskManager(RiskPolicy())))
    assert broker.place_calls == []
    assert gate_mod is not None


# ── C: 14:30 이후 일반 SELL 허용 ───────────────────────────────────────────


def test_C_normal_sell_allowed_after_cutoff(db):
    res = evaluate_kis_paper_order_permission(
        perm(side="SELL", is_risk_exit=True, confidence=0.1, quality_score=10,
             now=kst(14, 45)))
    assert res.allowed is True
    assert res.metadata["order_purpose"] == "NORMAL_EXIT"


def test_C_normal_sell_end_to_end_after_cutoff(db):
    seed_bot_position(db, "005930", 5)
    broker = FakeKisPaperBroker({"005930": 5})
    d = KisPaperAutoDecision(symbol="005930", side="SELL", quantity=5, price=10_000,
                             confidence=0.2, quality_score=10, held_position=True,
                             position_quantity=5, sell_reason_code="STOP_LOSS",
                             price_source="kis")
    r = run_exec(db, broker, d, kst(14, 50))
    assert r.broker_order_sent is True
    assert len(broker.place_calls) == 1 and broker.place_calls[0].side == OrderSide.SELL


def test_C_sell_quantity_over_held_rejected():
    res = evaluate_kis_paper_order_permission(
        perm(side="SELL", is_risk_exit=True, quantity=6, held_quantity=5, now=kst(14, 45)))
    assert res.reason_code == KisPaperPermReason.SELL_QUANTITY_INVALID.value


# ── D/E: 15:15 강제청산 — Council HOLD·일일상한·품질 무관 ─────────────────────


def test_D_eod_forced_exit_without_any_council_signal(session_factory, db):
    seed_bot_position(db, "005930", 7)
    broker = FakeKisPaperBroker({"005930": 7})
    out = step(session_factory, broker, kst(15, 15))
    assert out["state"] in (EodState.FLATTENING.value, EodState.WAITING_FOR_FILLS.value)
    assert out["orders_submitted_this_step"] == 1
    sell = broker.place_calls[0]
    assert sell.side == OrderSide.SELL and sell.quantity == 7
    assert sell.client_order_id == "eod-d20261022-s005930-n1"
    assert sell.order_type == OrderType.MARKET


def test_E_daily_cap_and_low_quality_do_not_block_eod(session_factory, db):
    seed_bot_position(db, "005930", 3)
    for i in range(12):   # 오늘 BUY 12건 — 일일 신규 상한(10) 초과 상태.
        db.add(OrderAuditLog(mode="PAPER", requested_by_ai=False, symbol=f"9{i:05d}",
                             side="BUY", quantity=1, order_type="MARKET",
                             latest_price=1000, decision="APPROVED", reasons=[],
                             trade_reason="kis_paper_auto", executed=True,
                             created_at=kst(10, 0)))
    db.commit()
    broker = FakeKisPaperBroker({"005930": 3})
    step(session_factory, broker, kst(15, 16),
         settings=make_settings(kis_paper_auto_min_confidence=0.99,
                                kis_paper_auto_min_quality_score=99))
    assert [o.quantity for o in broker.place_calls] == [3]


def test_E_entry_notional_cap_does_not_block_exit(session_factory, db):
    # 평가액 2,000,000 > 진입 한도 1,000,000 — 청산은 보유 수량이 상한 (RiskManager 포함).
    seed_bot_position(db, "005930", 200)
    broker = FakeKisPaperBroker({"005930": 200}, price=10_000)
    step(session_factory, broker, kst(15, 16))
    assert [o.quantity for o in broker.place_calls] == [200]


def test_E_daily_loss_limit_does_not_block_reduce_only_sell():
    from app.core.modes import OperationMode
    rm = RiskManager(RiskPolicy())
    rm.daily_realized_pnl = -10_000_000
    sell = OrderRequest(symbol="005930", side=OrderSide.SELL, quantity=5,
                        order_type=OrderType.MARKET)
    pos = [Position(symbol="005930", quantity=5, avg_price=10_000, market_price=10_000)]
    bal = Balance(cash=0, equity=1, buying_power=0)
    ok = rm.evaluate_order(order=sell, mode=OperationMode.PAPER, balance=bal,
                           positions=pos, latest_price=10_000)
    assert ok.decision == RiskDecision.APPROVED
    # 초과 SELL(보유 5 < 6)은 종전대로 손실 한도에 막힌다.
    over = sell.model_copy(update={"quantity": 6})
    bad = rm.evaluate_order(order=over, mode=OperationMode.PAPER, balance=bal,
                            positions=pos, latest_price=10_000)
    assert "daily loss limit reached" in bad.reasons
    buy = sell.model_copy(update={"side": OrderSide.BUY})
    assert rm.evaluate_order(order=buy, mode=OperationMode.PAPER, balance=bal,
                             positions=pos, latest_price=10_000).decision == RiskDecision.REJECTED


# ── F/S/W: KIS Paper 안전 가드 유지 ────────────────────────────────────────


@pytest.mark.parametrize("override,expect_state", [
    (dict(enable_live_trading=True), EodState.BLOCKED.value),
    (dict(market_data_provider="mock"), EodState.IDLE.value),
    (dict(enable_kis_paper_auto_trading=False), EodState.IDLE.value),
])
def test_F_safety_flags_mean_zero_orders(session_factory, db, override, expect_state):
    seed_bot_position(db, "005930", 3)
    broker = FakeKisPaperBroker({"005930": 3})
    out = step(session_factory, broker, kst(15, 16), settings=make_settings(**override))
    assert out["state"] == expect_state
    assert broker.place_calls == []


def test_F_kis_is_paper_false_blocks_at_gate(session_factory, db):
    seed_bot_position(db, "005930", 3)
    broker = FakeKisPaperBroker({"005930": 3})
    out = step(session_factory, broker, kst(15, 16), settings=make_settings(kis_is_paper=False))
    assert broker.place_calls == []
    sym = out["summary"]["symbols"]["005930"]
    assert sym["last_result"]["reason_code"] == KisPaperPermReason.KIS_PAPER_MODE_REQUIRED.value


def test_S_emergency_stop_blocks_and_alerts(session_factory, db):
    seed_bot_position(db, "005930", 3)
    broker = FakeKisPaperBroker({"005930": 3})
    rm = RiskManager(RiskPolicy())
    rm.emergency_stop = True
    events: list = []
    out = step(session_factory, broker, kst(15, 16), risk=rm, events=events)
    assert out["state"] == EodState.BLOCKED.value
    assert out["reason_code"] == "EOD_EMERGENCY_STOP_MANUAL_ACTION_REQUIRED"
    assert broker.place_calls == []
    assert any(e["level"] == "CRITICAL" for e in events)
    # 장 마감 후 보유가 남으면 FLATTEN_INCOMPLETE — 성공 위장 0.
    out2 = step(session_factory, broker, kst(15, 31), risk=rm)
    assert out2["state"] == EodState.FLATTEN_INCOMPLETE.value


def test_S_loop_stopped_still_flattens_by_default(session_factory, db):
    seed_bot_position(db, "005930", 2)
    broker = FakeKisPaperBroker({"005930": 2})
    step(session_factory, broker, kst(15, 16), loop_state="STOPPED")
    assert len(broker.place_calls) == 1


def test_S_no_silent_eod_disable_path(session_factory, db):
    # V2: 예전 opt-out 설정(flatten_enabled/when_loop_stopped=false)이 env 에 남아 있어도
    #   무시된다 — 당일 단타 모드에선 KIS 안전 게이트를 통과하면 EOD 는 항상 관리.
    seed_bot_position(db, "005930", 2)
    broker = FakeKisPaperBroker({"005930": 2})
    out = step(session_factory, broker, kst(15, 16), loop_state="STOPPED",
               settings=make_settings(kis_paper_eod_flatten_enabled=False,
                                      kis_paper_eod_flatten_when_loop_stopped=False,
                                      kis_paper_eod_flatten_minutes_before_close=0))
    assert out["state"] != EodState.BLOCKED.value
    assert len(broker.place_calls) == 1
    from app.core.config import Settings
    assert "kis_paper_eod_flatten_enabled" not in Settings.model_fields
    assert "kis_paper_eod_flatten_when_loop_stopped" not in Settings.model_fields


def test_W_eod_results_never_live_authorization(session_factory, db):
    seed_bot_position(db, "005930", 1)
    broker = FakeKisPaperBroker({"005930": 1})
    out = step(session_factory, broker, kst(15, 16))
    assert out["is_live_authorization"] is False
    assert broker.is_paper is True
    row = db.query(EodLiquidationState).one()
    assert row.is_live_authorization is False


# ── G/H/T: 부분체결 · 기존 미체결 · 이미 청산 · 접수≠체결 ────────────────────


def test_G_partial_fill_tracks_remainder_without_duplicate(session_factory, db):
    seed_bot_position(db, "005930", 10)
    broker = FakeKisPaperBroker({"005930": 10}, fill_mode="partial")
    step(session_factory, broker, kst(15, 15))
    assert [o.quantity for o in broker.place_calls] == [10]       # 5 체결, 5 미체결
    step(session_factory, broker, kst(15, 15, 30))
    assert len(broker.place_calls) == 1                            # 미체결 잔량 있으면 재매도 0
    broker.fill_all()
    out = step(session_factory, broker, kst(15, 16))
    assert len(broker.place_calls) == 1
    assert out["summary"]["totals"]["managed_qty"] == 0


def test_G_existing_open_sell_blocks_new_sell(session_factory, db):
    seed_bot_position(db, "005930", 4)
    broker = FakeKisPaperBroker({"005930": 4})
    broker.rows.append({"odno": "X1", "symbol": "005930", "side": "SELL", "ord_qty": 4,
                        "filled": 0, "remaining": 4, "canceled": False, "ord_tmd": ""})
    step(session_factory, broker, kst(15, 16))
    assert broker.place_calls == []


def test_H_already_flat_symbol_zero_orders(session_factory, db):
    seed_bot_position(db, "005930", 3)
    broker = FakeKisPaperBroker({})        # 이미 전량 매도됨
    out = step(session_factory, broker, kst(15, 16))
    assert broker.place_calls == []
    assert out["state"] == EodState.WAITING_FOR_FILLS.value
    final = step(session_factory, broker, kst(15, 30, 5))
    assert final["state"] == EodState.FLAT_CONFIRMED.value


def test_T_received_is_not_flat_until_broker_confirms(session_factory, db):
    seed_bot_position(db, "005930", 3)
    broker = FakeKisPaperBroker({"005930": 3}, fill_mode="none")
    step(session_factory, broker, kst(15, 16))
    assert len(broker.place_calls) == 1
    out = step(session_factory, broker, kst(15, 31))       # 접수만, 체결 0
    assert out["state"] == EodState.FLATTEN_INCOMPLETE.value
    assert out["reason_code"] in ("EOD_POSITIONS_REMAIN", "EOD_OPEN_ORDERS_REMAIN")


def test_T_unconfirmed_submission_blocks_resend(session_factory, db):
    # 접수됐지만 KIS 일별체결에 아직 안 보이는 주문 → 재매도 0 (반영 지연 이중매도 방지).
    seed_bot_position(db, "005930", 3)
    broker = FakeKisPaperBroker({"005930": 3}, fill_mode="none")
    step(session_factory, broker, kst(15, 16))
    broker.rows.clear()                     # KIS 조회 지연 시뮬
    step(session_factory, broker, kst(15, 16, 30))
    assert len(broker.place_calls) == 1


def test_fill_then_flat_confirmed(session_factory, db):
    seed_bot_position(db, "005930", 3)
    seed_bot_position(db, "000660", 2)
    broker = FakeKisPaperBroker({"005930": 3, "000660": 2})
    step(session_factory, broker, kst(15, 15))
    assert sorted(o.symbol for o in broker.place_calls) == ["000660", "005930"]
    out = step(session_factory, broker, kst(15, 30, 1))
    assert out["state"] == EodState.FLAT_CONFIRMED.value
    # 판정 확정 후엔 API/주문 0 (재호출해도 동일).
    broker.fail_positions = True
    again = step(session_factory, broker, kst(15, 45))
    assert again["state"] == EodState.FLAT_CONFIRMED.value
    assert len(broker.place_calls) == 2


# ── I/J/K: 중복 실행 · 재시작 복구 · 15:22 복구 ─────────────────────────────


def test_I_two_processes_lease_single_sell(session_factory, db):
    seed_bot_position(db, "005930", 5)
    broker = FakeKisPaperBroker({"005930": 5}, fill_mode="none")
    a = step(session_factory, broker, kst(15, 16), owner="proc-A")
    b = step(session_factory, broker, kst(15, 16, 1), owner="proc-B")
    assert a["orders_submitted_this_step"] == 1
    assert b["reason_code"] == "EOD_LEASE_HELD_BY_OTHER"
    assert len(broker.place_calls) == 1


def test_I_parallel_steps_same_process_single_sell(session_factory, db):
    seed_bot_position(db, "005930", 5)
    broker = FakeKisPaperBroker({"005930": 5}, fill_mode="none")

    async def both():
        kw = dict(settings=make_settings(), session_factory=session_factory, broker=broker,
                  risk=RiskManager(RiskPolicy()), route_order_fn=route_order,
                  loop_state="RUNNING", owner_id="proc-A", event_sink=lambda **k: None,
                  credentials_present=True)
        return await asyncio.gather(run_eod_step(now=kst(15, 16), **kw),
                                    run_eod_step(now=kst(15, 16), **kw))
    asyncio.run(both())
    assert len(broker.place_calls) == 1


def test_I_lease_expires_for_crashed_owner(session_factory, db):
    assert acquire_lease(db, "2026-10-22", "dead", kst(15, 14), ttl_seconds=45)
    assert not acquire_lease(db, "2026-10-22", "new", kst(15, 14, 30), ttl_seconds=45)
    assert acquire_lease(db, "2026-10-22", "new", kst(15, 15), ttl_seconds=45)


def test_J_restart_at_1515_restores_state_and_order_numbers(session_factory, db):
    seed_bot_position(db, "005930", 6)
    broker = FakeKisPaperBroker({"005930": 6}, fill_mode="none")
    step(session_factory, broker, kst(15, 15), owner="old-pid")
    assert len(broker.place_calls) == 1
    # 프로세스 재시작 — 새 owner, lease 만료 후 재개. 기존 미체결 주문을 복원해 재매도 0.
    out = step(session_factory, broker, kst(15, 16, 30), owner="new-pid")
    assert len(broker.place_calls) == 1
    sym = out["summary"]["symbols"]["005930"]
    assert sym["eod_attempts"] == 1
    assert sym["eod_orders"][0]["broker_order_no"] == "0000000001"
    assert sym["eod_orders"][0]["client_order_id"] == "eod-d20261022-s005930-n1"


def test_J_rejected_attempt_counted_and_capped(session_factory, db):
    seed_bot_position(db, "005930", 2)
    broker = FakeKisPaperBroker({"005930": 2})
    rm = RiskManager(RiskPolicy(symbol_whitelist=["000660"]))   # 005930 거부
    for i in range(5):
        step(session_factory, broker, kst(15, 15, i * 10), risk=rm)
    eod = db.query(OrderAuditLog).filter(OrderAuditLog.client_order_id.like("eod-%")).all()
    assert len(eod) == 3                         # 시도 상한
    assert sorted(r.client_order_id for r in eod) == [
        "eod-d20261022-s005930-n1", "eod-d20261022-s005930-n2", "eod-d20261022-s005930-n3"]
    assert broker.place_calls == []


def test_K_recovery_at_1522_no_unverified_auction_orders(session_factory, db):
    seed_bot_position(db, "005930", 3)
    broker = FakeKisPaperBroker({"005930": 3})
    out = step(session_factory, broker, kst(15, 22))
    assert broker.place_calls == []
    assert out["state"] == EodState.WAITING_FOR_FILLS.value
    assert out["reason_code"] == "EOD_CLOSING_AUCTION_ORDERS_DISABLED"


def test_K_auction_orders_only_when_explicitly_enabled(session_factory, db):
    seed_bot_position(db, "005930", 3)
    broker = FakeKisPaperBroker({"005930": 3})
    step(session_factory, broker, kst(15, 22),
         settings=make_settings(kis_paper_eod_closing_auction_orders_enabled=True))
    assert len(broker.place_calls) == 1


# ── L: 15:30 이후 미체결 → FLATTEN_INCOMPLETE ──────────────────────────────


def test_L_open_orders_after_close_incomplete_and_next_day_buy_block(session_factory, db):
    seed_bot_position(db, "005930", 3)
    broker = FakeKisPaperBroker({"005930": 3}, fill_mode="none")
    events: list = []
    step(session_factory, broker, kst(15, 16))
    out = step(session_factory, broker, kst(15, 30, 10), events=events)
    assert out["state"] == EodState.FLATTEN_INCOMPLETE.value
    assert any(e["level"] == "CRITICAL" for e in events)
    nxt = kst(10, 0, d=date(2026, 10, 23))
    code, _ = program_buy_block_reason(db, settings=make_settings(), now=nxt)
    assert code == "BUY_BLOCKED_BY_EOD_INCIDENT"
    acknowledge_incident(db, trading_date="2026-10-22", decided_by="operator", now=nxt)
    code2, _ = program_buy_block_reason(db, settings=make_settings(), now=nxt)
    assert code2 is None
    assert db.query(EodLiquidationState).one().state == "FLATTEN_INCOMPLETE"  # 기록 보존


def test_L_reconcile_failure_after_close_is_not_success(session_factory, db):
    seed_bot_position(db, "005930", 3)
    broker = FakeKisPaperBroker({"005930": 3})
    broker.fail_orders = True
    out = step(session_factory, broker, kst(15, 31))
    assert out["state"] == EodState.FLATTEN_INCOMPLETE.value
    assert out["reason_code"] == "EOD_FINAL_RECONCILE_FAILED"


def test_L_api_failure_during_flatten_sends_nothing(session_factory, db):
    seed_bot_position(db, "005930", 3)
    broker = FakeKisPaperBroker({"005930": 3})
    broker.fail_positions = True
    out = step(session_factory, broker, kst(15, 16))
    assert broker.place_calls == []
    assert out["reason_code"] == "EOD_POSITIONS_QUERY_FAILED"


# ── M: 휴장일 · 단축/특수장 · KST 경계 ─────────────────────────────────────


def test_M_holiday_no_buy_no_eod(session_factory, db):
    hol = date(2026, 10, 9)       # 한글날 (금)
    pol = IntradayPolicy(calendar=load_krx_calendar())
    assert pol.phase(kst(10, 0, d=hol)) == IntradayPhase.NON_TRADING_DAY
    assert evaluate_kis_paper_order_permission(
        perm(now=kst(10, 0, d=hol))).reason_code == "BUY_BLOCKED_NON_TRADING_DAY"
    broker = FakeKisPaperBroker({"005930": 3})
    out = step(session_factory, broker, kst(15, 16, d=hol))
    assert out["state"] == EodState.IDLE.value and broker.place_calls == []


def test_M_special_session_shifts_cutoff_and_flatten():
    suneung = date(2025, 11, 13)  # 2026학년도 수능 (확정 실적) 10:00~16:30
    pol = IntradayPolicy(calendar=load_krx_calendar())
    sch = pol.schedule(kst(12, 0, d=suneung))
    assert (sch.entry_start, sch.buy_cutoff, sch.precheck_start, sch.flatten_start,
            sch.session_close) == (
        time(10, 5), time(15, 30), time(16, 0), time(16, 15), time(16, 30))
    assert pol.buy_entry_decision(kst(15, 29, d=suneung))[0] is True
    assert pol.phase(kst(16, 15, d=suneung)) == IntradayPhase.EOD_FLATTEN


def test_M_calendar_unknown_year_blocks_buy_but_allows_eod(session_factory, db):
    far = date(2031, 3, 4)        # 화요일, 달력 미등록
    assert evaluate_kis_paper_order_permission(
        perm(now=kst(10, 0, d=far))).reason_code == "BUY_BLOCKED_CALENDAR_UNKNOWN"
    seed_bot_position(db, "005930", 1)
    broker = FakeKisPaperBroker({"005930": 1})
    out = step(session_factory, broker, kst(15, 16, d=far))
    assert out["calendar_fallback"] is True
    assert len(broker.place_calls) == 1


def test_M_kst_timezone_boundary_utc_input():
    # 05:29:59Z = 14:29:59 KST, 05:30:00Z = 14:30:00 KST.
    pol = IntradayPolicy(calendar=load_krx_calendar())
    assert pol.buy_entry_decision(datetime(2026, 10, 22, 5, 29, 59, tzinfo=timezone.utc))[0]
    assert not pol.buy_entry_decision(datetime(2026, 10, 22, 5, 30, 0, tzinfo=timezone.utc))[0]
    # naive 는 UTC 로 해석 (프론트 시각 미신뢰).
    assert not pol.buy_entry_decision(datetime(2026, 10, 22, 5, 30, 0))[0]


def test_M_calendar_override_file(tmp_path):
    p = tmp_path / "cal.json"
    p.write_text('{"holidays": ["2026-10-22"], "covered_years": [2031]}', encoding="utf-8")
    cal = load_krx_calendar(str(p))
    pol = IntradayPolicy(calendar=cal)
    assert pol.phase(kst(10, 0)) == IntradayPhase.NON_TRADING_DAY
    assert pol.phase(kst(10, 0, d=date(2031, 3, 4))) == IntradayPhase.ENTRY_OPEN


# ── N/O/P: stale/mock 시세 · dry-run · mock broker ────────────────────────


@pytest.mark.parametrize("kw,code", [
    (dict(price_source="mock"), "KIS_REALTIME_PRICE_REQUIRED"),
    (dict(price_source="yfinance"), "KIS_REALTIME_PRICE_REQUIRED"),
    (dict(price_is_stale=True), "KIS_PRICE_STALE"),
])
def test_N_mock_or_stale_price_zero_buys(db, kw, code):
    broker = FakeKisPaperBroker({})
    r = run_exec(db, broker, _buy_decision(**kw), kst(10, 0))
    assert r.reason_code == code and broker.place_calls == []


def test_O_dry_run_zero_kis_writes(session_factory, db):
    seed_bot_position(db, "005930", 3)
    broker = FakeKisPaperBroker({"005930": 3})
    s = make_settings(kis_paper_auto_order_dry_run=True)
    out = step(session_factory, broker, kst(15, 16), settings=s)
    step(session_factory, broker, kst(15, 17), settings=s)
    assert broker.place_calls == []
    assert out["reason_code"] == "EOD_DRY_RUN_NO_ORDERS"
    assert out["summary"]["symbols"]["005930"]["status"] == "DRY_RUN_PLANNED"
    r = run_exec(db, broker, _buy_decision(), kst(10, 0), settings=s)
    assert r.reason_code == "KIS_PAPER_DRY_RUN_OK" and broker.place_calls == []


def test_P_mock_broker_never_reported_as_kis_fill(session_factory, db):
    seed_bot_position(db, "005930", 3)
    out = step(session_factory, MockBrokerAdapter(), kst(15, 16))
    assert out["state"] == EodState.BLOCKED.value
    assert out["reason_code"] == "EOD_BROKER_NOT_KIS_PAPER"


# ── Q/R: 보유 대조 · 수동 보유 보호 ─────────────────────────────────────────


def test_Q_R_mixed_holding_sells_only_program_quantity(session_factory, db):
    seed_bot_position(db, "005930", 4)               # 프로그램 4주
    broker = FakeKisPaperBroker({"005930": 10, "035420": 7})  # KIS 10주(수동 6) + 수동 종목
    out = step(session_factory, broker, kst(15, 16))
    assert [(o.symbol, o.quantity) for o in broker.place_calls] == [("005930", 4)]
    um = {u["symbol"]: u["quantity"] for u in out["summary"]["unmanaged_holdings"]}
    assert um == {"005930": 6, "035420": 7}
    final = step(session_factory, broker, kst(15, 30, 1))
    assert final["state"] == EodState.FLAT_CONFIRMED.value   # 수동 보유는 판정 대상 아님
    assert broker.holdings == {"005930": 6, "035420": 7}


def test_Q_ledger_exceeds_broker_sells_only_broker_quantity(session_factory, db):
    seed_bot_position(db, "005930", 9)               # 원장 9 > KIS 4 (운영자가 일부 수동 매도)
    broker = FakeKisPaperBroker({"005930": 4})
    out = step(session_factory, broker, kst(15, 16))
    assert [o.quantity for o in broker.place_calls] == [4]
    assert out["summary"]["symbols"]["005930"]["ledger_exceeds_broker"] is True


def test_R_ownership_ledger_failure_blocks(session_factory, db, monkeypatch):
    import app.kis_paper.eod_liquidation as eod
    seed_bot_position(db, "005930", 3)
    monkeypatch.setattr(eod, "_program_audit_rows",
                        lambda _db: (_ for _ in ()).throw(RuntimeError("db")))
    broker = FakeKisPaperBroker({"005930": 3})
    out = step(session_factory, broker, kst(15, 16))
    assert out["state"] == EodState.BLOCKED.value
    assert out["reason_code"] == "EOD_OWNERSHIP_UNKNOWN"
    assert broker.place_calls == []


# ── 스캔 tick / driver 통합 ─────────────────────────────────────────────────


def test_scan_tick_hands_exits_to_coordinator_after_flatten_start():
    from app.kis_paper.driver_bridge import kis_paper_realtime_scan_tick
    out = asyncio.run(kis_paper_realtime_scan_tick(
        now=kst(15, 15, 1), settings=make_settings(), session_factory=lambda: None,
        broker=object(), risk=object(), route_order_fn=lambda **k: None))
    assert out["reason_code"] == "EOD_COORDINATOR_OWNS_EXITS"
    assert out["orders_submitted"] == 0


def test_driver_runs_eod_step_even_when_loop_stopped():
    from app.auto_paper.background_driver import BackgroundTickDriver
    calls = []
    loop = SimpleNamespace(status=lambda now=None: SimpleNamespace(state="STOPPED", cycle_count=0))
    drv = BackgroundTickDriver(
        settings_provider=lambda: make_settings(enable_ai_paper_background_tick=True),
        loop_provider=lambda: loop, event_sink=lambda **kw: None,
        eod_step_fn=lambda **kw: calls.append(kw) or {"state": "FLATTENING"})
    res = asyncio.run(drv.maybe_eod_step(kst(15, 16)))
    assert res == {"state": "FLATTENING"} and calls[0]["loop_state"] == "STOPPED"
    assert drv.status()["last_eod_state"] == "FLATTENING"


def test_driver_skips_eod_when_kis_auto_disabled():
    from app.auto_paper.background_driver import BackgroundTickDriver
    calls = []
    drv = BackgroundTickDriver(
        settings_provider=lambda: make_settings(enable_kis_paper_auto_trading=False),
        loop_provider=lambda: None, eod_step_fn=lambda **kw: calls.append(kw))
    assert asyncio.run(drv.maybe_eod_step(kst(15, 16))) is None and calls == []


def test_driver_gate_treats_holiday_as_closed():
    from app.auto_paper.background_driver import BackgroundTickDriver
    from app.core.modes import OperationMode
    loop = SimpleNamespace(status=lambda now=None: SimpleNamespace(state="RUNNING", cycle_count=0))
    drv = BackgroundTickDriver(
        settings_provider=lambda: make_settings(enable_ai_paper_background_tick=True,
                                                default_mode=OperationMode.PAPER),
        loop_provider=lambda: loop)
    ok, code, _ = drv.evaluate_gate(kst(10, 0, d=date(2026, 10, 9)))
    assert ok is False and code == "MARKET_CLOSED"


# ── U: API / reason_code 정합 ───────────────────────────────────────────────


def test_U_status_shape_and_reason_codes(session_factory, db):
    seed_bot_position(db, "005930", 3)
    broker = FakeKisPaperBroker({"005930": 3})
    step(session_factory, broker, kst(15, 16))
    st = eod_status(db, settings=make_settings(), now=kst(15, 17))
    assert st["phase"] == "EOD_FLATTEN"
    assert st["buy_allowed"] is False and st["buy_block_reason_code"] == "BUY_CUTOFF_ACTIVE"
    assert st["schedule"]["buy_cutoff"] == "14:30"
    assert st["schedule"]["flatten_start"] == "15:15"
    assert st["schedule"]["session_close"] == "15:30"
    assert st["schedule"]["session_type"] == "REGULAR_ONLY"
    assert st["eod_state"] in ("FLATTENING", "WAITING_FOR_FILLS")
    assert st["summary"]["last_broker_order_no"] == "0000000001"
    assert st["paper_auto_mode"]["mode"] == "KIS_REALTIME_PAPER_AUTO"
    assert st["broker_label"] == "KIS_PAPER"
    assert st["eod_settings"]["cancel_order_supported"] is True
    assert st["eod_settings"]["cancel_order_verified_on_kis_paper"] is False
    assert st["auto_liquidation_guaranteed"] is False
    assert st["is_live_authorization"] is False


def test_U_api_routes(client):
    r = client.get("/api/kis-paper/eod/status")
    assert r.status_code == 200
    body = r.json()
    for k in ("phase", "schedule", "buy_allowed", "eod_state", "paper_auto_mode",
              "service_kind", "background_driver_running"):
        assert k in body
    assert body["is_live_authorization"] is False
    assert client.post("/api/kis-paper/eod/acknowledge",
                       json={"trading_date": "2000-01-03", "decided_by": "op"}).status_code == 404
    assert client.post("/api/kis-paper/eod/acknowledge",
                       json={"trading_date": "2000-01-03", "decided_by": " "}).status_code == 400


# ── KIS 일별체결 파싱 / 페이지네이션 (read-only) ─────────────────────────────


def test_parse_ccld_row_remaining():
    r = parse_ccld_row({"odno": "1", "pdno": "005930", "sll_buy_dvsn_cd": "01",
                        "ord_qty": "10", "tot_ccld_qty": "4", "rmn_qty": "6", "cncl_yn": "N"})
    assert (r["side"], r["remaining"], r["filled"]) == ("SELL", 6, 4)
    c = parse_ccld_row({"odno": "2", "pdno": "005930", "sll_buy_dvsn_cd": "02",
                        "ord_qty": "10", "tot_ccld_qty": "0", "cncl_yn": "Y"})
    assert c["remaining"] == 0 and c["side"] == "BUY"
    f = parse_ccld_row({"odno": "3", "pdno": "005930", "sll_buy_dvsn_cd": "01",
                        "ord_qty": "10", "tot_ccld_qty": "7", "cncl_cfrm_qty": "3"})
    assert f["remaining"] == 0


def test_ccld_pagination_follows_tr_cont(monkeypatch):
    from app.brokers import kis_client as kc

    pages = [
        ({"tr_cont": "M"}, {"rt_cd": "0", "output1": [{"odno": "1"}],
                            "ctx_area_fk100": "FK", "ctx_area_nk100": "NK"}),
        ({"tr_cont": "D"}, {"rt_cd": "0", "output1": [{"odno": "2"}]}),
    ]
    sent = []

    class _Resp:
        def __init__(self, h, b):
            self.status_code, self.headers, self._b, self.text = 200, h, b, ""

        def json(self):
            return self._b

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            sent.append((params, headers))
            return _Resp(*pages[len(sent) - 1])

    c = kc.KisClient("k", "s", True)
    monkeypatch.setattr(c, "_ensure_token", lambda: asyncio.sleep(0, result="tok"))
    monkeypatch.setattr(c, "_throttle", lambda: asyncio.sleep(0))
    monkeypatch.setattr(c, "_client", lambda: _Client())
    rows = asyncio.run(c.inquire_daily_ccld_all("12345678", "01", kst_date="20261022"))
    assert [r["odno"] for r in rows] == ["1", "2"]
    assert sent[1][0]["CTX_AREA_FK100"] == "FK" and sent[1][1]["tr_id"] == "VTTC0081R"
    assert sent[1][1]["tr_cont"] == "N"


def test_W_new_modules_never_call_live_tr_or_place_order_directly():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    for rel in ("kis_paper/eod_liquidation.py", "kis_paper/intraday_buy_gate.py",
                "scheduler/intraday_session.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert ".place_order(" not in src
        assert "TTTC0801U" not in src and "TTTC0802U" not in src
        assert ".cancel_order(" not in src     # 취소는 route_cancel → OrderExecutor.cancel 만


# ═══════════════════════ V2: KIS-PAPER-EOD-SAFETY-HARDENING ═══════════════════════


def seed_today_program_buy(db, symbol: str, qty: int, odno: str, *, filled: int = 0):
    """오늘(14:00 KST) 접수된 프로그램 BUY — 아직 미체결/부분체결."""
    db.add(OrderAuditLog(
        created_at=kst(14, 0).replace(tzinfo=None), mode="PAPER", requested_by_ai=False,
        symbol=symbol, side="BUY", quantity=qty, order_type="MARKET", latest_price=10_000,
        decision="APPROVED", reasons=[], trade_reason="kis_paper_auto", executed=True,
        broker_order_id=odno, broker_status="RECEIVED", filled_quantity=filled,
    ))
    db.commit()


def open_buy_row(odno: str, symbol: str, qty: int, filled: int = 0, org: str = "06010"):
    return {"odno": odno, "symbol": symbol, "side": "BUY", "ord_qty": qty, "filled": filled,
            "remaining": qty - filled, "canceled": False, "ord_tmd": "", "ord_gno_brno": org}


# ── 14:30 미체결 BUY 취소 ─────────────────────────────────────────────────────


def test_V2_open_program_buy_cancelled_after_cutoff_and_confirmed(session_factory, db):
    seed_today_program_buy(db, "005930", 10, "0000000777")
    broker = FakeKisPaperBroker({})
    broker.rows.append(open_buy_row("0000000777", "005930", 10))
    out = step(session_factory, broker, kst(14, 30, 5))
    assert out["state"] == EodState.BUY_CUTOFF_ACTIVE.value
    assert broker.cancel_calls == [("0000000777", "06010")]
    assert out["cancels_sent_this_step"] == 1
    cx = db.query(OrderAuditLog).filter(OrderAuditLog.order_type == "CANCEL").one()
    assert cx.client_order_id == "cxl-d20261022-o0000000777-n1"
    assert cx.trade_reason == "kis_paper_cancel" and cx.executed is True
    # 다음 단계: KIS 잔량 0 확인 → CONFIRMED, 재요청 0.
    out2 = step(session_factory, broker, kst(14, 31))
    assert len(broker.cancel_calls) == 1
    assert out2["summary"]["buy_cancels"]["0000000777"]["status"] == "CONFIRMED_NO_REMAINING"


def test_V2_partial_fill_before_cancel_is_liquidated(session_factory, db):
    # 4주 체결 후 잔량 6 취소 → 체결분 4주는 KIS 잔고/원장에 반영되어 EOD 청산 대상.
    seed_today_program_buy(db, "005930", 10, "0000000778")
    broker = FakeKisPaperBroker({"005930": 4})
    broker.rows.append(open_buy_row("0000000778", "005930", 10, filled=4))
    step(session_factory, broker, kst(14, 30, 5))
    assert broker.cancel_calls == [("0000000778", "06010")]
    out = step(session_factory, broker, kst(15, 15, 5))
    assert [(o.symbol, o.quantity) for o in broker.place_calls] == [("005930", 4)]
    assert out["orders_submitted_this_step"] == 1
    final = step(session_factory, broker, kst(15, 30, 5))
    assert final["state"] == EodState.FLAT_CONFIRMED.value


def test_V2_cancel_failure_is_critical_and_blocks_flat(session_factory, db):
    seed_today_program_buy(db, "005930", 10, "0000000779")
    broker = FakeKisPaperBroker({})
    broker.cancel_reject = True
    broker.rows.append(open_buy_row("0000000779", "005930", 10))
    events: list = []
    for i in range(5):
        step(session_factory, broker, kst(14, 31, i * 10), events=events)
    assert len(broker.cancel_calls) == 3                      # 시도 상한
    assert any(e["code"] == "KIS_PAPER_EOD_BUY_CANCEL_FAILED" and e["level"] == "CRITICAL"
               for e in events)
    final = step(session_factory, broker, kst(15, 30, 5))
    assert final["state"] == EodState.FLATTEN_INCOMPLETE.value
    assert final["reason_code"] == "EOD_OPEN_ORDERS_REMAIN"


def test_V2_cancel_waits_for_confirmation_before_retry(session_factory, db):
    seed_today_program_buy(db, "005930", 10, "0000000780")
    broker = FakeKisPaperBroker({})
    broker.rows.append(open_buy_row("0000000780", "005930", 10))

    async def accept_but_keep_open(order_id, *, org_no=None, symbol="", side=OrderSide.BUY):
        broker.cancel_calls.append((order_id, org_no))
        return OrderResult(order_id="C1", status=OrderStatus.RECEIVED, symbol=symbol,
                           side=side, quantity=0)
    broker.cancel_order = accept_but_keep_open
    step(session_factory, broker, kst(14, 31, 0))
    step(session_factory, broker, kst(14, 31, 30))            # 90초 확인 대기 중 재요청 0
    assert len(broker.cancel_calls) == 1


def test_V2_unconfirmed_program_buy_blocks_flat_confirmed(session_factory, db):
    # 접수됐지만 KIS 일별체결에 아직 안 보이는 BUY → 취소 불가(조직번호 미상)·대기 → 판정 금지.
    seed_today_program_buy(db, "005930", 10, "0000000781")
    broker = FakeKisPaperBroker({})
    out = step(session_factory, broker, kst(14, 31))
    assert broker.cancel_calls == []
    assert out["summary"]["buy_cancels"]["0000000781"]["status"] == "AWAITING_KIS_VISIBILITY"
    final = step(session_factory, broker, kst(15, 30, 5))
    assert final["state"] == EodState.FLATTEN_INCOMPLETE.value


def test_V2_emergency_no_cancel_and_manual_liquidation_warning(session_factory, db):
    seed_bot_position(db, "005930", 3)
    seed_today_program_buy(db, "000660", 5, "0000000782")
    broker = FakeKisPaperBroker({"005930": 3})
    broker.rows.append(open_buy_row("0000000782", "000660", 5))
    rm = RiskManager(RiskPolicy())
    rm.emergency_stop = True
    out = step(session_factory, broker, kst(14, 35), risk=rm)
    assert out["state"] == EodState.BLOCKED.value
    assert broker.cancel_calls == [] and broker.place_calls == []
    assert out["summary"]["manual_liquidation_required"] is True
    st = eod_status(db, settings=make_settings(), now=kst(14, 36), risk=rm)
    assert st["emergency_stop"] is True and st["manual_liquidation_required"] is True
    assert {"symbol": "005930", "quantity": 3} in st["program_ledger_holdings"]


# ── route_cancel / OrderExecutor.cancel 가드 ────────────────────────────────


def _rc(db, broker, **kw):
    from app.core.modes import OperationMode
    from app.execution.order_router import route_cancel
    base = dict(original_order_id="0000000001", org_no="06010", symbol="005930",
                side=OrderSide.BUY, reason="t", mode=OperationMode.PAPER, broker=broker,
                risk=RiskManager(RiskPolicy()), db=db, client_order_id="cxl-t-1")
    base.update(kw)
    return asyncio.run(route_cancel(**base))


def test_V2_route_cancel_guards(db):
    from app.core.modes import OperationMode
    from app.execution.order_router import DuplicateOrderError
    broker = FakeKisPaperBroker({})
    rm = RiskManager(RiskPolicy())
    rm.emergency_stop = True
    r1 = _rc(db, broker, risk=rm, client_order_id="c1")
    r2 = _rc(db, broker, mode=OperationMode.LIVE_MANUAL_APPROVAL, client_order_id="c2")
    r3 = _rc(db, broker, live_trading_enabled=True, client_order_id="c3")
    live = KisBrokerAdapter(app_key="k", app_secret="s", account_no="1234567801", is_paper=False)
    r4 = _rc(db, live, client_order_id="c4")
    for r in (r1, r2, r3, r4):
        assert r.decision == RiskDecision.REJECTED and r.audit.executed is False
    assert broker.cancel_calls == []
    ok = _rc(db, broker, client_order_id="c5")
    assert ok.decision == RiskDecision.APPROVED and broker.cancel_calls == [("0000000001", "06010")]
    with pytest.raises(DuplicateOrderError):
        _rc(db, broker, client_order_id="c5")
    assert len(broker.cancel_calls) == 1


def test_V2_order_executor_cancel_refuses_unapproved_audit(db):
    from app.execution.executor import OrderExecutor, UnauthorizedOrderError
    broker = FakeKisPaperBroker({})
    for decision, otype in (("REJECTED", "CANCEL"), ("APPROVED", "MARKET")):
        audit = OrderAuditLog(mode="PAPER", requested_by_ai=False, symbol="005930", side="BUY",
                              quantity=0, order_type=otype, latest_price=0, decision=decision,
                              reasons=[])
        with pytest.raises(UnauthorizedOrderError):
            asyncio.run(OrderExecutor(broker, db).cancel(
                "1", audit, org_no="06010", symbol="005930", side=OrderSide.BUY))
    assert broker.cancel_calls == []


def test_V2_kis_cancel_live_refused_before_network():
    from app.brokers import kis_client as kc
    c = kc.KisClient("k", "s", False)
    with pytest.raises(kc.KisApiError):
        asyncio.run(c.cancel_order_paper("12345678", "01", org_no="06010", orig_odno="1"))
    live = KisBrokerAdapter(app_key="k", app_secret="s", account_no="1234567801", is_paper=False)
    with pytest.raises(NotImplementedError):
        asyncio.run(live.cancel_order("1", org_no="06010"))


def test_V2_kis_cancel_paper_payload(monkeypatch):
    from app.brokers import kis_client as kc
    sent = {}

    class _Resp:
        status_code, text = 200, ""

        def json(self):
            return {"rt_cd": "0", "msg_cd": "APBK0013", "msg1": "ok", "output": {"ODNO": "0000099"}}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            sent.update(url=url, body=json, headers=headers)
            return _Resp()

    c = kc.KisClient("k", "s", True)
    monkeypatch.setattr(c, "_ensure_token", lambda: asyncio.sleep(0, result="tok"))
    monkeypatch.setattr(c, "_throttle", lambda: asyncio.sleep(0))
    monkeypatch.setattr(c, "_client", lambda: _Client())
    ad = KisBrokerAdapter(app_key="k", app_secret="s", account_no="1234567801",
                          is_paper=True, client=c)
    res = asyncio.run(ad.cancel_order("0000000777", org_no="06010", symbol="005930"))
    assert res.status == OrderStatus.RECEIVED and res.order_id == "0000099"
    assert sent["url"].endswith("/trading/order-rvsecncl")
    assert sent["headers"]["tr_id"] == "VTTC0013U"
    b = sent["body"]
    assert (b["KRX_FWDG_ORD_ORGNO"], b["ORGN_ODNO"], b["RVSE_CNCL_DVSN_CD"],
            b["QTY_ALL_ORD_YN"], b["EXCG_ID_DVSN_CD"]) == ("06010", "0000000777", "02", "Y", "KRX")
    # 조직번호 미상이면 네트워크 호출 없이 REJECTED.
    sent.clear()
    r2 = asyncio.run(ad.cancel_order("0000000777", org_no=None, symbol="005930"))
    assert r2.status == OrderStatus.REJECTED and sent == {}


# ── 15:00 사전점검 + 단계적 청산 / 15:15 전량 ─────────────────────────────────


def test_V2_precheck_at_1500_staged_losers_first(session_factory, db):
    for sym in ("000001", "000002", "000003"):
        seed_bot_position(db, sym, 1)
    broker = FakeKisPaperBroker({"000001": 1, "000002": 1, "000003": 1})
    orig = broker.get_positions
    pnl = {"000001": 10_500, "000002": 9_000, "000003": 9_800}   # avg 10,000

    async def priced():
        ps = await orig()
        return [p.model_copy(update={"market_price": pnl[p.symbol]}) for p in ps]
    broker.get_positions = priced
    events: list = []
    out = step(session_factory, broker, kst(15, 0, 5), events=events)
    assert out["phase"] == "EOD_PRECHECK"
    assert out["reason_code"] == "EOD_STAGED_FLATTEN_IN_PROGRESS"
    assert [o.symbol for o in broker.place_calls] == ["000002", "000003"]   # 손실 큰 순, 2종목
    assert out["summary"]["precheck"]["ok"] is True
    assert any(e["code"] == "KIS_PAPER_EOD_PRECHECK_OK" for e in events)
    step(session_factory, broker, kst(15, 0, 35))
    assert [o.symbol for o in broker.place_calls] == ["000002", "000003", "000001"]


def test_V2_precheck_failure_is_critical_early(session_factory, db):
    seed_bot_position(db, "005930", 1)
    broker = FakeKisPaperBroker({"005930": 1})
    broker.fail_orders = True
    events: list = []
    step(session_factory, broker, kst(15, 0, 5), events=events)
    assert any(e["code"] == "KIS_PAPER_EOD_PRECHECK_FAILED" and e["level"] == "CRITICAL"
               for e in events)
    assert broker.place_calls == []


def test_V2_scan_hands_off_from_1500():
    from app.kis_paper.driver_bridge import kis_paper_realtime_scan_tick
    out = asyncio.run(kis_paper_realtime_scan_tick(
        now=kst(15, 0, 1), settings=make_settings(), session_factory=lambda: None,
        broker=object(), risk=object(), route_order_fn=lambda **k: None))
    assert out["reason_code"] == "EOD_COORDINATOR_OWNS_EXITS"


@pytest.mark.parametrize("t,phase", [
    ((14, 29, 59), "ENTRY_OPEN"), ((14, 30, 0), "BUY_CUTOFF"), ((14, 59, 59), "BUY_CUTOFF"),
    ((15, 0, 0), "EOD_PRECHECK"), ((15, 14, 59), "EOD_PRECHECK"), ((15, 15, 0), "EOD_FLATTEN"),
    ((15, 19, 59), "EOD_FLATTEN"), ((15, 20, 0), "CLOSING_AUCTION"),
    ((15, 29, 59), "CLOSING_AUCTION"), ((15, 30, 0), "POST_CLOSE"),
    ((9, 4, 59), "OPENING_BUFFER"), ((8, 59, 59), "PRE_OPEN"),
])
def test_V2_all_kst_boundaries(t, phase):
    assert IntradayPolicy(calendar=load_krx_calendar()).phase(kst(*t)).value == phase


@pytest.mark.parametrize("minutes,expect", [(0, time(15, 15)), (1, time(15, 18)),
                                            (15, time(15, 15)), (100, time(15, 0))])
def test_V2_flatten_offset_clamped_cannot_disable(minutes, expect):
    pol = IntradayPolicy.from_settings(
        make_settings(kis_paper_eod_flatten_minutes_before_close=minutes))
    assert pol.schedule(kst(12, 0)).flatten_start == expect


# ── 수동매매 BUY ─────────────────────────────────────────────────────────────


def _manual_buy(db, broker, now):
    from app.core.modes import OperationMode
    order = OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1,
                         order_type=OrderType.MARKET, trade_reason="manual_buy")
    return asyncio.run(route_order(order=order, requested_by_ai=False, mode=OperationMode.PAPER,
                                   broker=broker, risk=RiskManager(RiskPolicy()), db=db, now=now))


def test_V2_manual_buy_blocked_after_cutoff_on_kis(db):
    broker = FakeKisPaperBroker({})
    r = _manual_buy(db, broker, kst(14, 30))
    assert r.decision == RiskDecision.REJECTED and r.reasons[0].startswith("BUY_CUTOFF_ACTIVE")
    assert broker.place_calls == []
    ok = _manual_buy(db, broker, kst(14, 29, 59))
    assert ok.decision == RiskDecision.APPROVED and len(broker.place_calls) == 1


def test_V2_manual_buy_mock_broker_not_session_bound(db):
    r = _manual_buy(db, MockBrokerAdapter(), kst(14, 45))
    assert not any(str(x).startswith("BUY_CUTOFF") for x in r.reasons)


# ── 휴장일 / 특수거래일 ──────────────────────────────────────────────────────


def test_V2_suneung_2026_is_thursday_and_unconfirmed_blocks_buy():
    d = date(2026, 11, 19)
    assert d.weekday() == 3                                    # 목요일
    pol = IntradayPolicy(calendar=load_krx_calendar())
    sch = pol.schedule(kst(11, 0, d=d))
    assert sch.special_session_unconfirmed is True
    assert (sch.flatten_start, sch.session_close) == (time(15, 15), time(15, 30))  # 보수적
    assert pol.buy_entry_decision(kst(11, 0, d=d)) == (
        False, "BUY_BLOCKED_SPECIAL_SESSION_UNCONFIRMED")
    assert evaluate_kis_paper_order_permission(perm(now=kst(11, 0, d=d))).allowed is False


def test_V2_confirmed_special_session_via_override(tmp_path):
    p = tmp_path / "cal.json"
    p.write_text('{"confirmed_special_sessions": ["2026-11-19"]}', encoding="utf-8")
    pol = IntradayPolicy(calendar=load_krx_calendar(str(p)))
    d = date(2026, 11, 19)
    sch = pol.schedule(kst(11, 0, d=d))
    assert (sch.entry_start, sch.buy_cutoff, sch.flatten_start, sch.session_close) == (
        time(10, 5), time(15, 30), time(16, 15), time(16, 30))
    assert pol.buy_entry_decision(kst(11, 0, d=d))[0] is True


@pytest.mark.parametrize("d", ["2026-07-17", "2027-05-03", "2027-07-19", "2027-02-09",
                               "2027-12-27", "2027-10-11"])
def test_V2_new_public_holidays(d):
    y, m, dd = (int(x) for x in d.split("-"))
    pol = IntradayPolicy(calendar=load_krx_calendar())
    assert pol.phase(kst(10, 0, d=date(y, m, dd))) == IntradayPhase.NON_TRADING_DAY


def test_V2_client_order_ids_do_not_trip_secret_scanner():
    from app.agents.agent_memory import _SECRET_PATTERNS
    for _name, pat in _SECRET_PATTERNS:
        for i in ("eod-d20261022-s005930-n1", "cxl-d20261022-o0000012345-n3"):
            assert not pat.search(i), (_name, i)


def test_V2_restart_after_cancel_does_not_resend(session_factory, db):
    seed_today_program_buy(db, "005930", 10, "0000000790")
    broker = FakeKisPaperBroker({})
    broker.rows.append(open_buy_row("0000000790", "005930", 10))

    async def accept_only(order_id, *, org_no=None, symbol="", side=OrderSide.BUY):
        broker.cancel_calls.append((order_id, org_no))
        return OrderResult(order_id="C9", status=OrderStatus.RECEIVED, symbol=symbol,
                           side=side, quantity=0)
    broker.cancel_order = accept_only
    step(session_factory, broker, kst(14, 31), owner="old-pid")
    # 재시작(새 owner, lease 만료 후) — DB 의 cancel audit 로 '확인 대기' 복원 → 재요청 0.
    step(session_factory, broker, kst(14, 32), owner="new-pid")
    assert len(broker.cancel_calls) == 1
