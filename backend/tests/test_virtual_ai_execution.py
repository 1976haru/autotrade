"""VIRTUAL_AI_EXECUTION mode + VirtualAiAgent tests (152, MUST).

CLAUDE.md 절대 원칙: AI는 RiskManager / PermissionGate / Audit 가드 체인을
우회하지 않는다. 본 테스트는 그 invariant를 검증.
"""

import asyncio

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai.virtual_agent import AiProposal, VirtualAiAgent
from app.brokers.base import OrderSide
from app.brokers.mock_broker import MockBrokerAdapter
from app.core.modes import (
    MODE_CAPABILITIES,
    OperationMode,
    can_ai_execute,
    can_place_live_order,
)
from app.db.base import Base
from app.db.models import OrderAuditLog
from app.risk.risk_manager import RiskDecision, RiskManager, RiskPolicy


def _session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


# ---------- mode + capability matrix ----------

def test_virtual_ai_execution_mode_present_in_enum():
    assert OperationMode("VIRTUAL_AI_EXECUTION") == OperationMode.VIRTUAL_AI_EXECUTION


def test_virtual_ai_execution_capability_matrix():
    cap = MODE_CAPABILITIES[OperationMode.VIRTUAL_AI_EXECUTION]
    assert cap["live_order"]              is False  # 라이브 broker 미사용
    assert cap["ai_can_execute"]          is True   # 본 모드 핵심
    assert cap["requires_user_approval"]  is False  # 자동 라우팅 (가드 통과 시)


def test_can_place_live_order_false_for_virtual_ai_execution():
    """live_order=False라 어떤 flag 조합으로도 live broker 라우팅이 활성화되지 않는다."""
    assert can_place_live_order(
        OperationMode.VIRTUAL_AI_EXECUTION, enable_live_trading=True
    ) is False
    assert can_place_live_order(
        OperationMode.VIRTUAL_AI_EXECUTION, enable_live_trading=False
    ) is False


def test_can_ai_execute_true_for_virtual_regardless_of_flag():
    """가상 모드는 정의상 AI 실행이 가능 — env flag와 무관."""
    assert can_ai_execute(
        OperationMode.VIRTUAL_AI_EXECUTION, enable_ai_execution=False
    ) is True
    assert can_ai_execute(
        OperationMode.VIRTUAL_AI_EXECUTION, enable_ai_execution=True
    ) is True


def test_can_ai_execute_for_live_still_requires_flag():
    """LIVE_AI_EXECUTION은 flag 의존성 유지 — 152가 그 가드를 약화시키지 않는다."""
    assert can_ai_execute(
        OperationMode.LIVE_AI_EXECUTION, enable_ai_execution=False
    ) is False


# ---------- AiProposal → OrderRequest ----------

def test_proposal_carries_ai_decision_meta_to_order_request():
    p = AiProposal(symbol="005930", side=OrderSide.BUY, quantity=1,
                   confidence=80, reasons=["earnings_beat", "regime_match"])
    req = p.to_order_request()
    assert req.trade_reason       == "ai_recommendation"
    assert req.signal_strength    == 80
    assert req.signal_confidence  == 80
    assert req.ai_decision_meta["confidence"] == 80
    assert req.ai_decision_meta["reasons"]    == ["earnings_beat", "regime_match"]
    assert req.ai_decision_meta["rejected_by_guard"] is False


def test_proposal_stub_buys_on_close_up():
    agent = VirtualAiAgent()
    p = agent.propose_stub("005930", last_close=110, prev_close=100)
    assert p.side == OrderSide.BUY


def test_proposal_stub_sells_on_close_down():
    agent = VirtualAiAgent()
    p = agent.propose_stub("005930", last_close=90, prev_close=100)
    assert p.side == OrderSide.SELL


# ---------- propose_and_route — full guard chain ----------

def _route_and_get_audit(agent, proposal, *, mode, db, broker=None,
                         risk=None, client_order_id=None):
    broker = broker or MockBrokerAdapter()
    risk   = risk   or RiskManager(RiskPolicy())
    return asyncio.run(agent.propose_and_route(
        proposal, mode=mode, broker=broker, risk=risk, db=db,
        client_order_id=client_order_id,
    ))


def test_virtual_ai_proposal_routes_to_audit_with_ai_meta():
    Session = _session()
    with Session() as db:
        agent = VirtualAiAgent()
        proposal = agent.propose_stub("005930", last_close=110, prev_close=100,
                                       confidence=80)
        result = _route_and_get_audit(
            agent, proposal,
            mode=OperationMode.VIRTUAL_AI_EXECUTION, db=db,
        )
        db.commit()
    assert result.decision == RiskDecision.APPROVED
    assert result.audit.requested_by_ai is True
    assert result.audit.strategy == "ai_virtual"
    assert result.audit.trade_reason == "ai_recommendation"
    assert result.audit.ai_decision_meta is not None
    assert result.audit.ai_decision_meta["confidence"] == 80


def test_virtual_ai_proposal_blocked_by_emergency_stop():
    """060 invariant: AI 제안도 emergency_stop을 우회 못 한다."""
    Session = _session()
    with Session() as db:
        agent = VirtualAiAgent()
        proposal = agent.propose_stub("005930", last_close=110, prev_close=100)
        risk = RiskManager(RiskPolicy())
        risk.set_emergency_stop(True)
        result = _route_and_get_audit(
            agent, proposal,
            mode=OperationMode.VIRTUAL_AI_EXECUTION, db=db, risk=risk,
        )
        db.commit()
    assert result.decision == RiskDecision.REJECTED
    assert any("emergency stop" in r for r in result.reasons)


def test_virtual_ai_proposal_blocked_by_max_order_notional():
    """RiskPolicy.max_order_notional이 AI 제안에도 적용."""
    Session = _session()
    with Session() as db:
        agent = VirtualAiAgent(default_quantity=1000)  # 1000 * 75000 = 75M >> 한도
        proposal = agent.propose_stub("005930", last_close=110, prev_close=100)
        result = _route_and_get_audit(
            agent, proposal,
            mode=OperationMode.VIRTUAL_AI_EXECUTION, db=db,
        )
        db.commit()
    assert result.decision == RiskDecision.REJECTED


def test_virtual_ai_proposal_in_live_manual_mode_goes_to_approval_queue():
    """LIVE_MANUAL_APPROVAL 모드에서 AI 제안 → NEEDS_APPROVAL. 가드 우회 0."""
    Session = _session()
    with Session() as db:
        agent = VirtualAiAgent()
        proposal = agent.propose_stub("005930", last_close=110, prev_close=100)
        risk = RiskManager(RiskPolicy(enable_live_trading=True))
        result = _route_and_get_audit(
            agent, proposal,
            mode=OperationMode.LIVE_MANUAL_APPROVAL, db=db, risk=risk,
        )
        db.commit()
    assert result.decision == RiskDecision.NEEDS_APPROVAL
    assert result.approval is not None


def test_virtual_ai_proposal_persists_audit_row_even_on_reject():
    """거부도 audit에 남는다 — CLAUDE.md '감사 로그 우선'."""
    Session = _session()
    with Session() as db:
        agent = VirtualAiAgent(default_quantity=1000)
        proposal = agent.propose_stub("005930", last_close=110, prev_close=100)
        _route_and_get_audit(agent, proposal,
                              mode=OperationMode.VIRTUAL_AI_EXECUTION, db=db)
        rows = db.execute(select(OrderAuditLog)).scalars().all()
        # 한 row 들어있고, AI metadata도 보존.
        assert len(rows) == 1
        r = rows[0]
        assert r.requested_by_ai is True
        assert r.decision == "REJECTED"
        assert r.ai_decision_meta is not None


def test_virtual_ai_idempotent_with_client_order_id():
    """140 invariant도 AI 경로에 적용 — 같은 client_order_id 두 번째 호출은 차단."""
    from app.execution.order_router import DuplicateOrderError
    Session = _session()
    with Session() as db:
        agent = VirtualAiAgent()
        proposal = agent.propose_stub("005930", last_close=110, prev_close=100)
        cid = "ai-dup-001"
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


# ---------- 164: auto-generated client_order_id when not provided ----------

def test_propose_and_route_auto_generates_client_order_id_when_none():
    """client_order_id 미전달 시 자동 UUID 생성 — audit row에 채워진다."""
    Session = _session()
    with Session() as db:
        agent = VirtualAiAgent()
        proposal = agent.propose_stub("005930", last_close=110, prev_close=100)
        result = asyncio.run(agent.propose_and_route(
            proposal, mode=OperationMode.VIRTUAL_AI_EXECUTION,
            broker=MockBrokerAdapter(), risk=RiskManager(RiskPolicy()),
            db=db,
            # client_order_id 미명시
        ))
        db.commit()
    assert result.decision == RiskDecision.APPROVED
    cid = result.audit.client_order_id
    assert cid is not None and cid.startswith("ai-")
    # UUID v4 형식 — "ai-" 접두 후 36자 (8-4-4-4-12).
    assert len(cid) == 3 + 36


def test_auto_generated_ids_are_unique_per_call():
    """매 호출마다 UUID 다름 — 같은 proposal로 두 번 호출해도 dup 안 됨."""
    Session = _session()
    with Session() as db:
        agent = VirtualAiAgent()
        proposal = agent.propose_stub("005930", last_close=110, prev_close=100)

        risk = RiskManager(RiskPolicy())
        risk.policy.max_positions       = 999_999
        risk.policy.max_symbol_exposure = 999_999_999_999

        broker = MockBrokerAdapter()
        # 첫 호출 — auto cid 1.
        r1 = asyncio.run(agent.propose_and_route(
            proposal, mode=OperationMode.VIRTUAL_AI_EXECUTION,
            broker=broker, risk=risk, db=db,
        ))
        db.commit()
        # 같은 proposal — 두 번째 호출. auto cid 다른 UUID이라 dup X.
        r2 = asyncio.run(agent.propose_and_route(
            proposal, mode=OperationMode.VIRTUAL_AI_EXECUTION,
            broker=broker, risk=risk, db=db,
        ))
        db.commit()
    assert r1.audit.client_order_id != r2.audit.client_order_id
    assert r1.decision == RiskDecision.APPROVED
    assert r2.decision == RiskDecision.APPROVED


def test_explicit_client_order_id_preserved_over_auto_gen():
    """호출자가 명시한 cid는 auto-gen으로 덮이지 않는다 — 회귀 가드."""
    Session = _session()
    with Session() as db:
        agent = VirtualAiAgent()
        proposal = agent.propose_stub("005930", last_close=110, prev_close=100)
        result = asyncio.run(agent.propose_and_route(
            proposal, mode=OperationMode.VIRTUAL_AI_EXECUTION,
            broker=MockBrokerAdapter(), risk=RiskManager(RiskPolicy()),
            db=db, client_order_id="explicit-cid-001",
        ))
        db.commit()
    assert result.audit.client_order_id == "explicit-cid-001"
