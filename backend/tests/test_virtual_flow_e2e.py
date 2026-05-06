"""Virtual Flow End-to-End tests (154, MUST).

체크리스트 13: 전략 신호 → SignalQuality → ApprovalQueue → 승인/거부/취소 →
체결 → audit + virtual order ledger + position 일관성 검증.

기존 test_e2e_approval_order_flow.py가 HTTP 라우트 흐름을 다루고 본 모듈은
**가상 환경 전용 흐름**과 144/148/149/150/152의 invariant를 합치는 추가 테스트.
"""

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai.virtual_agent import VirtualAiAgent
from app.brokers.base import OrderRequest, OrderSide, OrderType
from app.brokers.mock_broker import MockBrokerAdapter
from app.core.modes import OperationMode
from app.db.base import Base
from app.db.models import OrderAuditLog, PendingApproval, VirtualOrder
from app.execution.order_router import route_order
from app.permission.gate import (
    ApprovalRiskCheckFailedError,
    PermissionGate,
)
from app.risk.risk_manager import RiskDecision, RiskManager, RiskPolicy
from app.virtual.fill_engine import FillContext, simulate_fill
from app.virtual.order_ledger import (
    STATUS_ACCEPTED,
    STATUS_FILLED,
    STATUS_NEW,
    STATUS_PARTIALLY_FILLED,
    STATUS_REJECTED,
    create_order,
    transition,
)
from app.virtual.position_engine import compute_open_positions


def _session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


# ---------- 1. SIMULATION 모드 — strategy 신호 → audit (즉시 체결) ----------

def test_simulation_strategy_buy_flows_to_audit_executed():
    Session = _session()
    risk = RiskManager(RiskPolicy())
    broker = MockBrokerAdapter()
    with Session() as db:
        result = asyncio.run(route_order(
            order=OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1,
                                strategy="sma_crossover", trade_reason="strategy_signal"),
            requested_by_ai=False,
            mode=OperationMode.SIMULATION,
            broker=broker, risk=risk, db=db,
        ))
        db.commit()
    assert result.decision == RiskDecision.APPROVED
    assert result.audit.executed is True
    assert result.audit.strategy == "sma_crossover"
    assert result.audit.trade_reason == "strategy_signal"


# ---------- 2. LIVE_MANUAL_APPROVAL — strategy 신호 → 승인 → 체결 ----------

def test_live_manual_strategy_signal_through_approval_to_audit():
    Session = _session()
    risk = RiskManager(RiskPolicy(enable_live_trading=True))
    broker = MockBrokerAdapter()
    with Session() as db:
        # Submit
        result = asyncio.run(route_order(
            order=OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1,
                                strategy="sma_crossover", trade_reason="strategy_signal"),
            requested_by_ai=False,
            mode=OperationMode.LIVE_MANUAL_APPROVAL,
            broker=broker, risk=risk, db=db,
        ))
        db.commit()
    assert result.decision == RiskDecision.NEEDS_APPROVAL
    approval = result.approval
    # Approve
    with Session() as db:
        gate = PermissionGate(db)
        approval, fill = asyncio.run(gate.approve(
            approval.id, broker, risk, decided_by="ops1",
        ))
        db.commit()
    assert approval.status == "APPROVED"
    assert fill.status.value == "FILLED"


# ---------- 3. operator reject path ----------

def test_live_manual_approval_reject_path():
    Session = _session()
    risk = RiskManager(RiskPolicy(enable_live_trading=True))
    broker = MockBrokerAdapter()
    with Session() as db:
        result = asyncio.run(route_order(
            order=OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1),
            requested_by_ai=False, mode=OperationMode.LIVE_MANUAL_APPROVAL,
            broker=broker, risk=risk, db=db,
        ))
        db.commit()
        approval = result.approval
        gate = PermissionGate(db)
        rejected = gate.reject(approval.id, decided_by="ops1", note="bad signal")
        db.commit()
    assert rejected.status == "REJECTED"
    # audit row는 NEEDS_APPROVAL 상태 그대로 — broker 호출 안 됨.
    with Session() as db:
        audits = db.execute(select(OrderAuditLog)).scalars().all()
        assert len(audits) == 1
        assert audits[0].executed is False


# ---------- 4. operator cancel path ----------

def test_live_manual_approval_cancel_path():
    Session = _session()
    risk = RiskManager(RiskPolicy(enable_live_trading=True))
    broker = MockBrokerAdapter()
    with Session() as db:
        result = asyncio.run(route_order(
            order=OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1),
            requested_by_ai=False, mode=OperationMode.LIVE_MANUAL_APPROVAL,
            broker=broker, risk=risk, db=db,
        ))
        db.commit()
        approval = result.approval
        gate = PermissionGate(db)
        cancelled = gate.cancel(approval.id, decided_by="ops1", note="changed mind")
        db.commit()
    assert cancelled.status == "CANCELLED"


# ---------- 5. emergency stop blocks pending approve ----------

def test_emergency_stop_blocks_approve_after_submit():
    Session = _session()
    risk = RiskManager(RiskPolicy(enable_live_trading=True))
    broker = MockBrokerAdapter()
    with Session() as db:
        result = asyncio.run(route_order(
            order=OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1),
            requested_by_ai=False, mode=OperationMode.LIVE_MANUAL_APPROVAL,
            broker=broker, risk=risk, db=db,
        ))
        db.commit()
        approval = result.approval

        # Emergency stop ON.
        risk.set_emergency_stop(True)

        gate = PermissionGate(db)
        with pytest.raises(ApprovalRiskCheckFailedError) as exc:
            asyncio.run(gate.approve(approval.id, broker, risk))
        assert any("emergency" in r for r in exc.value.reasons)

        # Approval은 PENDING 유지.
        refreshed = db.get(PendingApproval, approval.id)
        assert refreshed.status == "PENDING"


# ---------- 6. stale price at approve time → ApprovalRiskCheckFailedError ----------

def test_stale_price_blocks_approve_at_approve_time():
    Session = _session()
    risk = RiskManager(RiskPolicy(enable_live_trading=True))
    broker = MockBrokerAdapter()
    threshold = risk.policy.stale_price_max_age_seconds
    with Session() as db:
        result = asyncio.run(route_order(
            order=OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1),
            requested_by_ai=False, mode=OperationMode.LIVE_MANUAL_APPROVAL,
            broker=broker, risk=risk, db=db,
        ))
        db.commit()
        approval = result.approval

        # broker가 stale timestamp 반환하도록 강제.
        broker.set_stale_price_for_test("005930", age_seconds=threshold + 30)

        gate = PermissionGate(db)
        with pytest.raises(ApprovalRiskCheckFailedError) as exc:
            asyncio.run(gate.approve(approval.id, broker, risk))
        assert any("stale" in r.lower() for r in exc.value.reasons)


# ---------- 7. virtual order full lifecycle ----------

def test_virtual_order_full_lifecycle_partial_to_filled():
    Session = _session()
    with Session() as db:
        order = create_order(
            db, symbol="005930", side="BUY", quantity=10,
            strategy="sma_crossover", mode="SIMULATION",
        )
        assert order.status == STATUS_NEW

        transition(db, order, to_status=STATUS_ACCEPTED, reason="risk_passed")
        # 거래량 4만 → 부분 체결. 슬리피지 0으로 산식 검증 단순화.
        outcome1 = simulate_fill(db, order, FillContext(
            quote_price=1000,
            quote_timestamp=datetime.now(timezone.utc),
            bar_volume=4, slippage_bps=0,
        ))
        assert outcome1.final_status == STATUS_PARTIALLY_FILLED
        # 잔량 6 → 전량 체결.
        outcome2 = simulate_fill(db, order, FillContext(
            quote_price=1010,
            quote_timestamp=datetime.now(timezone.utc),
            bar_volume=10, slippage_bps=0,
        ))
        assert outcome2.final_status == STATUS_FILLED
        assert order.filled_quantity == 10
        # weighted avg = (1000*4 + 1010*6) / 10 = 1006
        assert order.avg_fill_price == 1006

        # Position engine — 잔여 포지션 없음 (BUY만 있고 SELL 없음 → open 10주).
        positions = compute_open_positions(db, last_prices={"005930": 1010})
        assert len(positions) == 1
        assert positions[0].quantity == 10
        assert positions[0].avg_price == 1006


# ---------- 8. AI virtual proposal lifecycle ----------

def test_ai_virtual_proposal_routes_through_audit_with_meta():
    Session = _session()
    risk = RiskManager(RiskPolicy())
    broker = MockBrokerAdapter()
    agent = VirtualAiAgent()
    with Session() as db:
        proposal = agent.propose_stub("005930", last_close=110, prev_close=100,
                                       confidence=85)
        result = asyncio.run(agent.propose_and_route(
            proposal, mode=OperationMode.VIRTUAL_AI_EXECUTION,
            broker=broker, risk=risk, db=db,
            client_order_id="ai-e2e-001",
        ))
        db.commit()
    assert result.decision == RiskDecision.APPROVED
    assert result.audit.requested_by_ai is True
    assert result.audit.strategy == "ai_virtual"
    assert result.audit.trade_reason == "ai_recommendation"
    assert result.audit.signal_strength == 85
    assert result.audit.client_order_id == "ai-e2e-001"
    assert result.audit.ai_decision_meta["confidence"] == 85


# ---------- 9. fill engine respects emergency_stop in lifecycle ----------

def test_fill_engine_emergency_stop_terminates_accepted_order():
    Session = _session()
    with Session() as db:
        order = create_order(
            db, symbol="005930", side="BUY", quantity=10,
            strategy="sma", mode="SIMULATION",
        )
        transition(db, order, to_status=STATUS_ACCEPTED)
        outcome = simulate_fill(db, order, FillContext(
            quote_price=1000,
            quote_timestamp=datetime.now(timezone.utc),
            bar_volume=100,
            emergency_stop_enabled=True,
        ))
    assert outcome.final_status == STATUS_REJECTED
    assert order.status == STATUS_REJECTED


# ---------- 10. duplicate client_order_id idempotency in virtual context ----------

def test_idempotent_virtual_proposal_via_client_order_id():
    """140 invariant: 가상 AI 흐름에서도 client_order_id가 같으면 두 번째는 차단."""
    from app.execution.order_router import DuplicateOrderError
    Session = _session()
    with Session() as db:
        agent = VirtualAiAgent()
        proposal = agent.propose_stub("005930", last_close=110, prev_close=100)
        cid = "virt-dup-x"
        first = asyncio.run(agent.propose_and_route(
            proposal, mode=OperationMode.VIRTUAL_AI_EXECUTION,
            broker=MockBrokerAdapter(), risk=RiskManager(RiskPolicy()),
            db=db, client_order_id=cid,
        ))
        db.commit()
        assert first.decision == RiskDecision.APPROVED

        with pytest.raises(DuplicateOrderError):
            asyncio.run(agent.propose_and_route(
                proposal, mode=OperationMode.VIRTUAL_AI_EXECUTION,
                broker=MockBrokerAdapter(), risk=RiskManager(RiskPolicy()),
                db=db, client_order_id=cid,
            ))
