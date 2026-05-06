import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.brokers.base import OrderRequest, OrderSide, OrderType
from app.brokers.mock_broker import MockBrokerAdapter
from app.core.modes import OperationMode
from app.db.base import Base
from app.db.models import OrderAuditLog, PendingApproval
from app.permission.gate import (
    ApprovalAlreadyDecidedError,
    ApprovalNotFoundError,
    ApprovalRiskCheckFailedError,
    PermissionGate,
)
from app.risk.risk_manager import RiskManager, RiskPolicy


def _risk_for_live_manual():
    """070: PermissionGate.approve re-evaluates risk against current broker
    state, so the LIVE_MANUAL_APPROVAL queue tests need the global flag on
    (otherwise re-eval would block at the queue gate added in 061)."""
    return RiskManager(RiskPolicy(enable_live_trading=True))


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _audit(db, symbol="005930"):
    a = OrderAuditLog(
        mode="LIVE_MANUAL_APPROVAL", symbol=symbol, side="BUY", quantity=1,
        order_type="MARKET", latest_price=75_000,
        decision="NEEDS_APPROVAL", reasons=["manual approval required"],
    )
    db.add(a)
    db.flush()
    return a


def _order(symbol="005930", qty=1):
    return OrderRequest(symbol=symbol, side=OrderSide.BUY, quantity=qty,
                        order_type=OrderType.MARKET)


def test_submit_creates_pending_row_linked_to_audit():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        approval = PermissionGate(db).submit(
            audit=audit, order=_order(), mode=OperationMode.LIVE_MANUAL_APPROVAL,
        )
        assert approval.status == "PENDING"
        assert approval.audit_id == audit.id
        assert approval.symbol == "005930"
        assert approval.mode == "LIVE_MANUAL_APPROVAL"
        assert approval.decided_at is None


def test_list_pending_excludes_decided():
    Session = _session()
    with Session() as db:
        gate = PermissionGate(db)
        a1 = gate.submit(audit=_audit(db, "005930"), order=_order("005930"),
                         mode=OperationMode.LIVE_MANUAL_APPROVAL)
        a2 = gate.submit(audit=_audit(db, "000660"), order=_order("000660"),
                         mode=OperationMode.LIVE_MANUAL_APPROVAL)
        gate.reject(a1.id, note="not now")
        pending = gate.list_pending()
        assert [p.id for p in pending] == [a2.id]


def test_get_unknown_id_raises():
    Session = _session()
    with Session() as db:
        with pytest.raises(ApprovalNotFoundError):
            PermissionGate(db).get(9999)


def test_approve_executes_order_and_updates_audit():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(qty=2),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        broker = MockBrokerAdapter()
        approved, result = asyncio.run(gate.approve(
            approval.id, broker, _risk_for_live_manual(),
            decided_by="user", note="ok",
        ))
        assert approved.status == "APPROVED"
        assert approved.decided_by == "user"
        assert approved.decided_at is not None
        assert result.status.value == "FILLED"
        assert result.filled_quantity == 2

        refreshed_audit = db.get(OrderAuditLog, audit.id)
        assert refreshed_audit.executed is True
        assert refreshed_audit.broker_status == "FILLED"
        assert refreshed_audit.filled_quantity == 2
        assert refreshed_audit.avg_fill_price == 75_000


def test_reject_does_not_execute_or_touch_audit_executed_flag():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        rejected = gate.reject(approval.id, decided_by="user", note="risky")
        assert rejected.status == "REJECTED"
        assert rejected.note == "risky"

        refreshed_audit = db.get(OrderAuditLog, audit.id)
        assert refreshed_audit.executed is False
        assert refreshed_audit.broker_order_id is None


def test_cannot_approve_already_decided():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)
        gate.reject(approval.id)
        with pytest.raises(ApprovalAlreadyDecidedError):
            asyncio.run(gate.approve(approval.id, MockBrokerAdapter(), _risk_for_live_manual()))


def test_cannot_reject_already_decided():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)
        asyncio.run(gate.approve(approval.id, MockBrokerAdapter(), _risk_for_live_manual()))
        with pytest.raises(ApprovalAlreadyDecidedError):
            gate.reject(approval.id)


def test_submit_persists_via_session():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        approval = PermissionGate(db).submit(
            audit=audit, order=_order(), mode=OperationMode.LIVE_MANUAL_APPROVAL,
        )
        approval_id = approval.id
    with Session() as db2:
        loaded = db2.execute(
            select(PendingApproval).where(PendingApproval.id == approval_id)
        ).scalar_one()
        assert loaded.status == "PENDING"


# ---------- cancel ----------

def test_cancel_marks_approval_cancelled_with_metadata():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        cancelled = gate.cancel(approval.id, decided_by="user", note="signal stale")
        assert cancelled.status == "CANCELLED"
        assert cancelled.decided_by == "user"
        assert cancelled.note == "signal stale"
        assert cancelled.decided_at is not None


def test_cancel_does_not_execute_or_touch_audit_executed_flag():
    """Cancel must not run the order or mutate audit beyond what reject does."""
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        gate.cancel(approval.id)

        refreshed_audit = db.get(OrderAuditLog, audit.id)
        assert refreshed_audit.executed is False
        assert refreshed_audit.broker_order_id is None


def test_cancelled_approval_excluded_from_list_pending():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)
        gate.cancel(approval.id)
        assert gate.list_pending() == []


def test_cannot_cancel_already_decided():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)
        gate.reject(approval.id)
        with pytest.raises(ApprovalAlreadyDecidedError):
            gate.cancel(approval.id)


def test_cannot_approve_after_cancel():
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)
        gate.cancel(approval.id)
        with pytest.raises(ApprovalAlreadyDecidedError):
            asyncio.run(gate.approve(approval.id, MockBrokerAdapter(), _risk_for_live_manual()))


def test_cancel_unknown_id_raises_not_found():
    Session = _session()
    with Session() as db:
        with pytest.raises(ApprovalNotFoundError):
            PermissionGate(db).cancel(99999)


# ---------- list_decided ----------

def test_list_decided_excludes_pending():
    Session = _session()
    with Session() as db:
        gate = PermissionGate(db)
        a1 = gate.submit(audit=_audit(db), order=_order(),
                         mode=OperationMode.LIVE_MANUAL_APPROVAL)
        # a2 stays PENDING — only a1 should appear in list_decided
        gate.submit(audit=_audit(db), order=_order(),
                    mode=OperationMode.LIVE_MANUAL_APPROVAL)
        gate.reject(a1.id)

        decided = gate.list_decided()
        assert len(decided) == 1
        assert decided[0].id == a1.id
        assert decided[0].status == "REJECTED"


def test_list_decided_status_filter():
    Session = _session()
    with Session() as db:
        gate = PermissionGate(db)
        a1 = gate.submit(audit=_audit(db), order=_order(),
                         mode=OperationMode.LIVE_MANUAL_APPROVAL)
        a2 = gate.submit(audit=_audit(db), order=_order(),
                         mode=OperationMode.LIVE_MANUAL_APPROVAL)
        a3 = gate.submit(audit=_audit(db), order=_order(),
                         mode=OperationMode.LIVE_MANUAL_APPROVAL)
        gate.reject(a1.id)
        gate.cancel(a2.id)
        gate.cancel(a3.id)

        cancelled = gate.list_decided(status="CANCELLED")
        assert {a.id for a in cancelled} == {a2.id, a3.id}
        rejected = gate.list_decided(status="REJECTED")
        assert {a.id for a in rejected} == {a1.id}


def test_list_decided_orders_most_recent_first():
    Session = _session()
    with Session() as db:
        gate = PermissionGate(db)
        a1 = gate.submit(audit=_audit(db), order=_order(),
                         mode=OperationMode.LIVE_MANUAL_APPROVAL)
        a2 = gate.submit(audit=_audit(db), order=_order(),
                         mode=OperationMode.LIVE_MANUAL_APPROVAL)
        gate.reject(a1.id)
        gate.cancel(a2.id)  # decided after a1

        decided = gate.list_decided()
        # Most recent decided_at first
        assert decided[0].id == a2.id
        assert decided[1].id == a1.id


def test_list_decided_limit_offset():
    Session = _session()
    with Session() as db:
        gate = PermissionGate(db)
        ids = []
        for _ in range(5):
            a = gate.submit(audit=_audit(db), order=_order(),
                            mode=OperationMode.LIVE_MANUAL_APPROVAL)
            gate.cancel(a.id)
            ids.append(a.id)

        first_two  = gate.list_decided(limit=2)
        assert len(first_two) == 2
        next_two = gate.list_decided(limit=2, offset=2)
        assert len(next_two) == 2
        # No overlap between pages
        assert {r.id for r in first_two}.isdisjoint({r.id for r in next_two})


# ---------- 070: re-evaluation at approve time ----------

def test_approve_rejects_when_emergency_stop_toggled_after_submit():
    """Operator pulls emergency_stop between submit and approve. Re-eval must
    block execution and leave the approval as PENDING for retry."""
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        risk = _risk_for_live_manual()
        risk.set_emergency_stop(True)

        with pytest.raises(ApprovalRiskCheckFailedError) as excinfo:
            asyncio.run(gate.approve(approval.id, MockBrokerAdapter(), risk))
        assert any("emergency stop" in r for r in excinfo.value.reasons)

        # Approval still PENDING — operator can retry once the stop clears
        refreshed = db.get(PendingApproval, approval.id)
        assert refreshed.status == "PENDING"
        # And the audit row was untouched: no execution attempted
        refreshed_audit = db.get(OrderAuditLog, audit.id)
        assert refreshed_audit.executed is False


def test_approve_rejects_when_notional_now_exceeds_limit():
    """Price moved enough between submit and approve to violate the notional
    cap. Re-eval surfaces the violation and blocks execution."""
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(qty=2),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        # MockBroker default price=75_000 → 2 qty * 75_000 = 150_000 < 1M cap
        # Tighten the policy so re-eval finds the violation.
        risk = RiskManager(RiskPolicy(enable_live_trading=True, max_order_notional=100_000))

        with pytest.raises(ApprovalRiskCheckFailedError) as excinfo:
            asyncio.run(gate.approve(approval.id, MockBrokerAdapter(), risk))
        assert any("max_order_notional" in r for r in excinfo.value.reasons)

        refreshed = db.get(PendingApproval, approval.id)
        assert refreshed.status == "PENDING"


def test_approve_rejects_when_live_trading_flag_toggled_off():
    """The global ENABLE_LIVE_TRADING flag was on at submit (enabling the
    queue) but flipped off before approve. 061's queue gate fires at re-eval."""
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        # Submit happened with flag on (semantically). Now re-eval with flag off.
        risk = RiskManager(RiskPolicy(enable_live_trading=False))

        with pytest.raises(ApprovalRiskCheckFailedError) as excinfo:
            asyncio.run(gate.approve(approval.id, MockBrokerAdapter(), risk))
        assert any("live trading" in r for r in excinfo.value.reasons)

        refreshed = db.get(PendingApproval, approval.id)
        assert refreshed.status == "PENDING"


def test_approve_proceeds_when_re_eval_only_returns_mode_marker():
    """Steady state: no violations, mode-required-approval marker is the only
    reason in the re-eval result. The gate must let execution through."""
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(qty=1),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        approved, result = asyncio.run(gate.approve(
            approval.id, MockBrokerAdapter(), _risk_for_live_manual(),
        ))
        assert approved.status == "APPROVED"
        assert result.status.value == "FILLED"


# ---------- 076: persist re-eval-failed approve attempts ----------

def test_re_eval_failure_appends_an_attempts_entry():
    """The first time approve fails on re-eval, attempts should grow to length
    1 with {at, decided_by, reasons} populated."""
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        risk = _risk_for_live_manual()
        risk.set_emergency_stop(True)

        with pytest.raises(ApprovalRiskCheckFailedError):
            asyncio.run(gate.approve(approval.id, MockBrokerAdapter(), risk,
                                     decided_by="ops1"))

        refreshed = db.get(PendingApproval, approval.id)
        assert len(refreshed.attempts) == 1
        entry = refreshed.attempts[0]
        assert entry["decided_by"] == "ops1"
        assert any("emergency stop" in r for r in entry["reasons"])
        assert "at" in entry  # ISO timestamp


def test_re_eval_failures_accumulate_across_repeated_attempts():
    """Repeated failed attempts append; each carries its own {at, decided_by,
    reasons}. Operator handover ("did anyone try this already?") relies on
    the count + most-recent entry."""
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        risk = _risk_for_live_manual()
        risk.set_emergency_stop(True)

        for who in ("ops1", "ops2", "ops3"):
            with pytest.raises(ApprovalRiskCheckFailedError):
                asyncio.run(gate.approve(approval.id, MockBrokerAdapter(), risk,
                                         decided_by=who))

        refreshed = db.get(PendingApproval, approval.id)
        assert len(refreshed.attempts) == 3
        assert [e["decided_by"] for e in refreshed.attempts] == ["ops1", "ops2", "ops3"]


def test_successful_approve_does_not_append_attempts():
    """Only re-eval-blocked attempts persist; the successful path doesn't
    record on attempts (the audit row already records fulfillment)."""
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(qty=1),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        asyncio.run(gate.approve(approval.id, MockBrokerAdapter(),
                                 _risk_for_live_manual()))
        refreshed = db.get(PendingApproval, approval.id)
        assert refreshed.attempts == []


# ---------- 146: approve-time safety gates (143 stale price, 145 daily PnL) ----------

def test_approve_rejects_when_quote_is_stale_at_approve_time():
    """146: 143 stale price 가드가 approve 시점에도 적용되어야 한다 — submit과
    approve 시점의 invariant 일치. broker가 stale timestamp 반환하면 re-eval에서
    REJECTED로 차단."""
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(qty=1),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        # broker가 매 호출마다 stale timestamp 반환하도록 강제.
        broker = MockBrokerAdapter()
        risk   = _risk_for_live_manual()
        threshold = risk.policy.stale_price_max_age_seconds
        assert threshold > 0
        broker.set_stale_price_for_test("005930", age_seconds=threshold + 30)

        with pytest.raises(ApprovalRiskCheckFailedError) as exc:
            asyncio.run(gate.approve(approval.id, broker, risk))
        assert any("stale" in r.lower() for r in exc.value.reasons), exc.value.reasons

        # 접근권은 여전히 PENDING — 운영자가 시세 회복 후 재시도 가능.
        refreshed = db.get(PendingApproval, approval.id)
        assert refreshed.status == "PENDING"
        assert len(refreshed.attempts) == 1
        assert any("stale" in r.lower() for r in refreshed.attempts[0]["reasons"])


def test_approve_rejects_when_daily_loss_breached_after_submit():
    """146: 145 daily realized PnL 가드가 approve 시점에도 적용. submit 후 다른
    거래로 max_daily_loss를 초과한 손실이 누적되면 approve가 차단."""
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(qty=1),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        # submit 후 손실 거래 시드 — 오늘 청산된 -10000원.
        from datetime import datetime, timezone
        today_dt = datetime.now(timezone.utc)
        db.add_all([
            OrderAuditLog(
                mode="LIVE_MANUAL_APPROVAL", symbol="000660", side="BUY", quantity=1,
                order_type="MARKET", latest_price=10_000,
                decision="APPROVED", reasons=[], executed=True,
                broker_status="FILLED", filled_quantity=1, avg_fill_price=10_000,
                created_at=today_dt,
            ),
            OrderAuditLog(
                mode="LIVE_MANUAL_APPROVAL", symbol="000660", side="SELL", quantity=1,
                order_type="MARKET", latest_price=0,
                decision="APPROVED", reasons=[], executed=True,
                broker_status="FILLED", filled_quantity=1, avg_fill_price=0,
                created_at=today_dt,
            ),
        ])
        db.commit()

        # max_daily_loss를 5000원으로 — 이미 -10000 손실이라 한도 초과.
        risk = RiskManager(RiskPolicy(enable_live_trading=True, max_daily_loss=5_000))

        with pytest.raises(ApprovalRiskCheckFailedError) as exc:
            asyncio.run(gate.approve(approval.id, MockBrokerAdapter(), risk))
        assert any("daily loss" in r.lower() for r in exc.value.reasons)

        # 다시 PENDING으로 — 운영자가 새 날 또는 한도 조정 후 재시도.
        refreshed = db.get(PendingApproval, approval.id)
        assert refreshed.status == "PENDING"


def test_approve_recomputes_daily_pnl_per_call():
    """매 approve 호출이 daily_realized_pnl을 audit log에서 재계산 — singleton
    risk manager의 누적 상태에 의존하지 않는다."""
    Session = _session()
    with Session() as db:
        audit = _audit(db)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(qty=1),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        risk = _risk_for_live_manual()
        # 운영 중 다른 곳에서 카운터가 오염된 상황을 흉내냄 — approve가 재계산.
        risk.daily_realized_pnl = -999_999

        # audit 기반 실제 PnL은 0 (체결된 거래 없음). approve 통과해야 정상.
        asyncio.run(gate.approve(approval.id, MockBrokerAdapter(), risk))
        # 재계산되어 0이어야 한다.
        assert risk.daily_realized_pnl == 0


# ---------- 160: approve-time AI invariant consistency (158/159) ----------

def _ai_audit(db, *, requested_by_ai=True, signal_confidence=80,
               ai_decision_meta=None, symbol="005930"):
    """AI 발신 audit row + 그에 대응하는 PendingApproval. 160 invariant 테스트용."""
    if ai_decision_meta is None:
        ai_decision_meta = {"confidence": signal_confidence,
                            "reasons": ["test_reason"]}
    a = OrderAuditLog(
        mode="LIVE_MANUAL_APPROVAL", symbol=symbol, side="BUY", quantity=1,
        order_type="MARKET", latest_price=75_000,
        decision="NEEDS_APPROVAL", reasons=["manual approval required"],
        requested_by_ai=requested_by_ai,
        signal_strength=signal_confidence,
        signal_confidence=signal_confidence,
        ai_decision_meta=ai_decision_meta,
        strategy="ai_virtual",
    )
    db.add(a)
    db.flush()
    return a


def test_approve_blocks_ai_proposal_below_confidence_threshold():
    """submit 시 confidence 통과했지만 운영자가 임계를 올린 후 approve 시 거부."""
    Session = _session()
    with Session() as db:
        audit = _ai_audit(db, signal_confidence=50)
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(qty=1),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        # 임계 70으로 상향 — 50은 미달.
        risk = RiskManager(RiskPolicy(
            enable_live_trading=True, min_ai_confidence=70,
        ))
        with pytest.raises(ApprovalRiskCheckFailedError) as exc:
            asyncio.run(gate.approve(approval.id, MockBrokerAdapter(), risk))
        assert any("AI signal confidence" in r for r in exc.value.reasons), \
            exc.value.reasons


def test_approve_blocks_ai_proposal_with_empty_reasoning():
    """audit row에 빈 reasons 저장된 AI 주문이 approve 시 enforce_ai_reasoning=
    True 검사로 거부된다 — 159 invariant 일관성."""
    Session = _session()
    with Session() as db:
        audit = _ai_audit(db, ai_decision_meta={"confidence": 80, "reasons": []})
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(qty=1),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        risk = RiskManager(RiskPolicy(
            enable_live_trading=True, enforce_ai_reasoning=True,
        ))
        with pytest.raises(ApprovalRiskCheckFailedError) as exc:
            asyncio.run(gate.approve(approval.id, MockBrokerAdapter(), risk))
        assert any("missing reasoning" in r for r in exc.value.reasons)


def test_approve_succeeds_for_ai_proposal_with_reasoning_and_high_confidence():
    """정상 AI 주문 (high confidence + reasons) → approve 통과."""
    Session = _session()
    with Session() as db:
        audit = _ai_audit(db, signal_confidence=80,
                          ai_decision_meta={"confidence": 80,
                                            "reasons": ["earnings_beat"]})
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(qty=1),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        risk = RiskManager(RiskPolicy(
            enable_live_trading=True, min_ai_confidence=70,
        ))
        approval, _ = asyncio.run(
            gate.approve(approval.id, MockBrokerAdapter(), risk)
        )
        assert approval.status == "APPROVED"


def test_approve_does_not_apply_ai_invariant_to_non_ai_orders():
    """audit.requested_by_ai=False인 일반 운영자 주문은 AI 가드 무관 — 회귀 가드."""
    Session = _session()
    with Session() as db:
        # 일반 audit (AI 아님).
        audit = _audit(db)  # requested_by_ai 기본 False
        gate = PermissionGate(db)
        approval = gate.submit(audit=audit, order=_order(qty=1),
                               mode=OperationMode.LIVE_MANUAL_APPROVAL)

        # min_ai_confidence + enforce_ai_reasoning 켜져도 비-AI 주문엔 영향 X.
        risk = RiskManager(RiskPolicy(
            enable_live_trading=True,
            min_ai_confidence=99,
            enforce_ai_reasoning=True,
        ))
        approval, _ = asyncio.run(
            gate.approve(approval.id, MockBrokerAdapter(), risk)
        )
        assert approval.status == "APPROVED"


# ---------- 167: TTL expiry ----------


def _make_approval_with_age(db, *, age_seconds: int):
    """주어진 age로 PENDING approval 생성. created_at을 과거로 강제 설정."""
    audit = _audit(db)
    gate = PermissionGate(db)
    approval = gate.submit(
        audit=audit, order=_order(qty=1),
        mode=OperationMode.LIVE_MANUAL_APPROVAL,
    )
    # created_at backdating.
    approval.created_at = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    db.commit()
    db.refresh(approval)
    return approval


def test_expire_stale_approvals_marks_old_as_expired():
    Session = _session()
    with Session() as db:
        old      = _make_approval_with_age(db, age_seconds=3600)  # 1시간
        recent   = _make_approval_with_age(db, age_seconds=60)    # 1분

        gate = PermissionGate(db)
        expired = gate.expire_stale_approvals(ttl_seconds=600)  # 10분 TTL

    assert len(expired) == 1
    assert expired[0].id == old.id
    # DB 상태 확인.
    with Session() as db2:
        old_refresh = db2.get(PendingApproval, old.id)
        recent_refresh = db2.get(PendingApproval, recent.id)
        assert old_refresh.status     == "EXPIRED"
        assert old_refresh.decided_at is not None
        assert "TTL" in (old_refresh.note or "")
        assert recent_refresh.status  == "PENDING"


def test_expire_stale_approvals_zero_ttl_is_noop():
    Session = _session()
    with Session() as db:
        _make_approval_with_age(db, age_seconds=99999)
        gate = PermissionGate(db)
        result = gate.expire_stale_approvals(ttl_seconds=0)
        assert result == []
        # PENDING 그대로.
        rows = db.execute(select(PendingApproval)).scalars().all()
        assert all(r.status == "PENDING" for r in rows)


def test_list_pending_lazy_expires_when_ttl_passed():
    """list_pending(ttl_seconds=N) 호출 시 자동 만료 — pending 응답에서 빠짐."""
    Session = _session()
    with Session() as db:
        _make_approval_with_age(db, age_seconds=3600)  # 만료 대상
        recent = _make_approval_with_age(db, age_seconds=60)

        gate = PermissionGate(db)
        pending = gate.list_pending(ttl_seconds=600)

    assert len(pending) == 1
    assert pending[0].id == recent.id


def test_list_pending_default_no_expiration():
    """ttl_seconds 미명시 (=0)는 lazy expire 안 함 — backwards compat."""
    Session = _session()
    with Session() as db:
        old = _make_approval_with_age(db, age_seconds=99999)
        gate = PermissionGate(db)
        pending = gate.list_pending()  # ttl_seconds 미명시

    assert len(pending) == 1
    assert pending[0].id == old.id
    assert pending[0].status == "PENDING"


def test_expired_approval_cannot_be_approved():
    """이미 EXPIRED된 approval은 approve 시도가 ApprovalAlreadyDecidedError."""
    Session = _session()
    with Session() as db:
        approval = _make_approval_with_age(db, age_seconds=3600)
        gate = PermissionGate(db)
        gate.expire_stale_approvals(ttl_seconds=600)

        with pytest.raises(ApprovalAlreadyDecidedError):
            asyncio.run(gate.approve(
                approval.id, MockBrokerAdapter(), _risk_for_live_manual()
            ))


def test_expired_approval_cannot_be_rejected_or_cancelled():
    """terminal EXPIRED → reject/cancel도 모두 차단."""
    Session = _session()
    with Session() as db:
        approval = _make_approval_with_age(db, age_seconds=3600)
        gate = PermissionGate(db)
        gate.expire_stale_approvals(ttl_seconds=600)

        with pytest.raises(ApprovalAlreadyDecidedError):
            gate.reject(approval.id)
        with pytest.raises(ApprovalAlreadyDecidedError):
            gate.cancel(approval.id)


def test_expire_uses_explicit_now_for_determinism():
    """now 인자로 결정적 만료 테스트 — wall clock 의존 회피."""
    Session = _session()
    with Session() as db:
        # 3600초 전 created.
        approval = _make_approval_with_age(db, age_seconds=3600)
        gate = PermissionGate(db)

        # now를 명시적으로 created_at + 1200초로 설정 → 1200초 age.
        # ttl=1800이면 안 만료 (age < ttl).
        result = gate.expire_stale_approvals(ttl_seconds=1800,
                                              now=approval.created_at + timedelta(seconds=1200))
        assert result == []  # 1200 < 1800

        # ttl=600이면 만료 (age=1200 > ttl=600).
        result2 = gate.expire_stale_approvals(ttl_seconds=600,
                                                now=approval.created_at + timedelta(seconds=1200))
        assert len(result2) == 1


def test_expired_excluded_from_list_pending_after_explicit_sweep():
    """expire_stale_approvals 명시 호출 후 list_pending에서 제외."""
    Session = _session()
    with Session() as db:
        old = _make_approval_with_age(db, age_seconds=3600)
        gate = PermissionGate(db)
        gate.expire_stale_approvals(ttl_seconds=600)

        # ttl 안 넘기고 list_pending — old는 이미 EXPIRED라 제외.
        pending = gate.list_pending()
        assert old.id not in [p.id for p in pending]
