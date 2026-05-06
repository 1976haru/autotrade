"""Position close auto-route tests (172, MUST)."""

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.brokers.mock_broker import MockBrokerAdapter
from app.core.modes import OperationMode
from app.db.base import Base
from app.risk.risk_manager import RiskDecision, RiskManager, RiskPolicy
from app.virtual.auto_close import auto_close_position
from app.virtual.position_engine import CloseEvaluation, PositionSummary


def _session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


def _pos(quantity=1, avg_price=1000, last_price=900):
    return PositionSummary(
        symbol="005930", strategy="sma_crossover",
        quantity=quantity, avg_price=avg_price, last_price=last_price,
        unrealized_pnl=(last_price - avg_price) * quantity,
        unrealized_pct=(last_price - avg_price) / avg_price,
        hold_seconds=300, realized_pnl=0,
    )


def _close_eval(reason="stop_loss"):
    return CloseEvaluation(should_close=True, reason=reason)


# ---------- happy path ----------

def test_auto_close_routes_through_audit():
    Session = _session()
    with Session() as db:
        result = asyncio.run(auto_close_position(
            _pos(), _close_eval("stop_loss"),
            mode=OperationMode.SIMULATION,
            broker=MockBrokerAdapter(),
            risk=RiskManager(RiskPolicy()),
            db=db,
        ))
        db.commit()
    # SELL이 RiskManager 통과 + audit row 작성. 단, MockBroker는 SELL할
    # 포지션 없어 broker가 'insufficient position' 처리 — audit는 그래도 남음.
    # 본 테스트는 audit row 작성 + reason carry만 검증.
    assert result.audit is not None
    assert result.audit.symbol     == "005930"
    assert result.audit.side       == "SELL"
    assert result.audit.trade_reason == "stop_loss"
    assert result.audit.strategy   == "sma_crossover"


def test_auto_close_take_profit_carries_reason():
    Session = _session()
    with Session() as db:
        result = asyncio.run(auto_close_position(
            _pos(), _close_eval("take_profit"),
            mode=OperationMode.SIMULATION,
            broker=MockBrokerAdapter(),
            risk=RiskManager(RiskPolicy()),
            db=db,
        ))
    assert result.audit.trade_reason == "take_profit"


def test_auto_close_time_exit_carries_reason():
    Session = _session()
    with Session() as db:
        result = asyncio.run(auto_close_position(
            _pos(), _close_eval("time_exit"),
            mode=OperationMode.SIMULATION,
            broker=MockBrokerAdapter(),
            risk=RiskManager(RiskPolicy()),
            db=db,
        ))
    assert result.audit.trade_reason == "time_exit"


def test_auto_close_unknown_reason_falls_back_to_auto_close():
    """should_close=True + reason='unknown' (방어적) → trade_reason='auto_close'."""
    Session = _session()
    with Session() as db:
        # CloseEvaluation은 보통 should_close=False일 때 reason='unknown'이지만
        # 강제로 True+unknown 만들어 fallback 경로 검증.
        ev = CloseEvaluation(should_close=True, reason="unknown")
        result = asyncio.run(auto_close_position(
            _pos(), ev,
            mode=OperationMode.SIMULATION,
            broker=MockBrokerAdapter(),
            risk=RiskManager(RiskPolicy()),
            db=db,
        ))
    assert result.audit.trade_reason == "auto_close"


# ---------- guard chain invariants ----------

def test_auto_close_blocked_by_emergency_stop():
    """060: emergency_stop ON이면 auto-close도 차단."""
    Session = _session()
    with Session() as db:
        risk = RiskManager(RiskPolicy())
        risk.set_emergency_stop(True)
        result = asyncio.run(auto_close_position(
            _pos(), _close_eval("stop_loss"),
            mode=OperationMode.SIMULATION,
            broker=MockBrokerAdapter(),
            risk=risk, db=db,
        ))
    assert result.decision == RiskDecision.REJECTED
    assert any("emergency" in r for r in result.reasons)


def test_auto_close_in_live_manual_mode_goes_to_approval():
    """LIVE_MANUAL_APPROVAL 모드 — 자동 청산도 승인 큐 통과 (가드 우회 X)."""
    Session = _session()
    with Session() as db:
        result = asyncio.run(auto_close_position(
            _pos(), _close_eval("stop_loss"),
            mode=OperationMode.LIVE_MANUAL_APPROVAL,
            broker=MockBrokerAdapter(),
            risk=RiskManager(RiskPolicy(enable_live_trading=True)),
            db=db,
        ))
    assert result.decision == RiskDecision.NEEDS_APPROVAL
    assert result.approval is not None


def test_auto_close_requested_by_ai_false():
    """청산은 시스템 결정 — AI 가드(158/159) 무관 — requested_by_ai=False."""
    Session = _session()
    with Session() as db:
        result = asyncio.run(auto_close_position(
            _pos(), _close_eval("stop_loss"),
            mode=OperationMode.SIMULATION,
            broker=MockBrokerAdapter(),
            risk=RiskManager(RiskPolicy(
                # AI 가드 활성화돼 있어도 영향 X.
                min_ai_confidence=99, enforce_ai_reasoning=True,
            )),
            db=db,
        ))
    assert result.audit.requested_by_ai is False
    # 158/159 reason은 reasons에 없어야 한다.
    assert not any("AI signal confidence" in r for r in result.reasons)
    assert not any("missing reasoning" in r for r in result.reasons)


def test_auto_close_persists_strategy_in_audit():
    """원래 포지션의 strategy가 audit row에 carry — Strategy Scoreboard 합산."""
    Session = _session()
    with Session() as db:
        pos = PositionSummary(
            symbol="005930", strategy="orb_vwap",
            quantity=1, avg_price=1000, last_price=900,
            unrealized_pnl=-100, unrealized_pct=-0.1,
            hold_seconds=300, realized_pnl=0,
        )
        result = asyncio.run(auto_close_position(
            pos, _close_eval("stop_loss"),
            mode=OperationMode.SIMULATION,
            broker=MockBrokerAdapter(),
            risk=RiskManager(RiskPolicy()),
            db=db,
        ))
    assert result.audit.strategy == "orb_vwap"


# ---------- input validation ----------

def test_should_close_false_raises():
    """evaluation.should_close=False면 ValueError — 호출자 실수 방지."""
    Session = _session()
    with Session() as db:
        ev = CloseEvaluation(should_close=False, reason="unknown")
        with pytest.raises(ValueError, match="should_close"):
            asyncio.run(auto_close_position(
                _pos(), ev,
                mode=OperationMode.SIMULATION,
                broker=MockBrokerAdapter(),
                risk=RiskManager(RiskPolicy()),
                db=db,
            ))


def test_client_order_id_propagates_to_audit():
    Session = _session()
    with Session() as db:
        result = asyncio.run(auto_close_position(
            _pos(), _close_eval("stop_loss"),
            mode=OperationMode.SIMULATION,
            broker=MockBrokerAdapter(),
            risk=RiskManager(RiskPolicy()),
            db=db,
            client_order_id="auto-close-001",
        ))
    assert result.audit.client_order_id == "auto-close-001"
