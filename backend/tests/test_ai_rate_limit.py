"""AI proposal rate limit tests (161, MUST)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai.rate_limit import check_rate_limit, count_recent_ai_proposals
from app.db.base import Base
from app.db.models import OrderAuditLog


def _session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


def _ai_audit(db, *, strategy="ai_virtual", symbol="005930",
              created_at=None, requested_by_ai=True):
    """AI 제안 audit row 시드."""
    a = OrderAuditLog(
        mode="VIRTUAL_AI_EXECUTION", symbol=symbol, side="BUY", quantity=1,
        order_type="MARKET", latest_price=100,
        decision="APPROVED", reasons=[],
        requested_by_ai=requested_by_ai,
        strategy=strategy,
        created_at=created_at,
    )
    db.add(a)
    db.flush()
    return a


# ---------- count_recent_ai_proposals ----------

def test_count_returns_zero_when_no_audit_rows():
    Session = _session()
    with Session() as db:
        assert count_recent_ai_proposals(
            db, strategy="ai_virtual", symbol="005930",
            window_seconds=60,
        ) == 0


def test_count_zero_window_returns_zero():
    """window_seconds <= 0이면 무조건 0."""
    Session = _session()
    with Session() as db:
        _ai_audit(db, created_at=datetime.now(timezone.utc))
        db.commit()
        assert count_recent_ai_proposals(
            db, strategy="ai_virtual", symbol="005930",
            window_seconds=0,
        ) == 0


def test_count_within_window():
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        for _ in range(5):
            _ai_audit(db, created_at=now)
        db.commit()
        assert count_recent_ai_proposals(
            db, strategy="ai_virtual", symbol="005930",
            window_seconds=60, now=now + timedelta(seconds=10),
        ) == 5


def test_count_excludes_outside_window():
    """window 밖 row는 카운트 안 됨."""
    Session = _session()
    now = datetime.now(timezone.utc)
    old = now - timedelta(seconds=120)
    with Session() as db:
        _ai_audit(db, created_at=old)        # 밖
        _ai_audit(db, created_at=now)        # 안
        db.commit()
        assert count_recent_ai_proposals(
            db, strategy="ai_virtual", symbol="005930",
            window_seconds=60, now=now,
        ) == 1


def test_count_excludes_non_ai_rows():
    """requested_by_ai=False audit는 카운트 안 됨."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        _ai_audit(db, requested_by_ai=False, created_at=now)
        db.commit()
        assert count_recent_ai_proposals(
            db, strategy="ai_virtual", symbol="005930",
            window_seconds=60, now=now,
        ) == 0


def test_count_separates_by_strategy_and_symbol():
    """다른 strategy 또는 symbol은 분리 카운트."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        _ai_audit(db, strategy="ai_a", symbol="005930", created_at=now)
        _ai_audit(db, strategy="ai_b", symbol="005930", created_at=now)
        _ai_audit(db, strategy="ai_a", symbol="000660", created_at=now)
        db.commit()
        assert count_recent_ai_proposals(
            db, strategy="ai_a", symbol="005930",
            window_seconds=60, now=now,
        ) == 1


def test_count_with_null_strategy():
    """strategy=None도 NULL 매칭으로 분리 카운트."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        _ai_audit(db, strategy=None, symbol="005930", created_at=now)
        _ai_audit(db, strategy="ai_a", symbol="005930", created_at=now)
        db.commit()
        assert count_recent_ai_proposals(
            db, strategy=None, symbol="005930",
            window_seconds=60, now=now,
        ) == 1


# ---------- check_rate_limit ----------

def test_check_disabled_when_max_count_zero():
    """max_count=0이면 무조건 (True, 0) — 비활성."""
    Session = _session()
    with Session() as db:
        within, count = check_rate_limit(
            db, strategy="x", symbol="x", window_seconds=60, max_count=0,
        )
        assert within is True
        assert count == 0


def test_check_within_limit():
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        for _ in range(2):
            _ai_audit(db, created_at=now)
        db.commit()
        within, count = check_rate_limit(
            db, strategy="ai_virtual", symbol="005930",
            window_seconds=60, max_count=5, now=now,
        )
        assert within is True
        assert count == 2


def test_check_at_limit_blocks():
    """카운트가 임계와 같아도 다음 제안은 차단 (>= max_count → block)."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        for _ in range(5):
            _ai_audit(db, created_at=now)
        db.commit()
        within, count = check_rate_limit(
            db, strategy="ai_virtual", symbol="005930",
            window_seconds=60, max_count=5, now=now,
        )
        assert within is False
        assert count == 5


def test_check_above_limit_blocks():
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        for _ in range(10):
            _ai_audit(db, created_at=now)
        db.commit()
        within, count = check_rate_limit(
            db, strategy="ai_virtual", symbol="005930",
            window_seconds=60, max_count=5, now=now,
        )
        assert within is False
        assert count == 10


# ---------- route_order integration ----------

def test_route_order_blocks_ai_when_rate_limit_exceeded(client):
    """161 핵심 invariant: AI 제안 N건이 누적된 후 추가 AI 제안은 거부."""
    import asyncio
    from app.ai.virtual_agent import VirtualAiAgent
    from app.brokers.mock_broker import MockBrokerAdapter
    from app.core.modes import OperationMode
    from app.risk.risk_manager import RiskDecision

    # rate limit: 60s window, max 3 per (strategy, symbol).
    client.test_risk_manager.policy.ai_rate_limit_max_count = 3

    agent = VirtualAiAgent()
    broker = MockBrokerAdapter()

    async def fire_n(db, n):
        results = []
        for i in range(n):
            proposal = agent.propose_stub("005930", last_close=110, prev_close=100)
            r = await agent.propose_and_route(
                proposal, mode=OperationMode.VIRTUAL_AI_EXECUTION,
                broker=broker, risk=client.test_risk_manager, db=db,
                client_order_id=f"rate-{i:03d}",
            )
            results.append(r)
        return results

    with client.test_db_factory() as db:
        results = asyncio.run(fire_n(db, 5))
        db.commit()
    # 첫 3건은 통과, 4-5번째는 rate limit으로 REJECTED.
    assert results[0].decision == RiskDecision.APPROVED
    assert results[2].decision == RiskDecision.APPROVED
    assert results[3].decision == RiskDecision.REJECTED
    assert any("AI rate limit" in r for r in results[3].reasons)


def test_route_order_does_not_apply_rate_limit_to_non_ai(client):
    """비-AI 주문은 rate limit과 무관 — 회귀 가드."""
    client.test_risk_manager.policy.ai_rate_limit_max_count = 1
    # 일반 주문 5건 — 모두 통과해야 한다.
    for i in range(5):
        side = "BUY" if i % 2 == 0 else "SELL"
        client.test_risk_manager.policy.max_positions = 999_999
        client.test_risk_manager.policy.max_symbol_exposure = 999_999_999_999
        res = client.post("/api/broker/orders", json={
            "symbol": "005930", "side": side, "quantity": 1,
        })
        assert res.status_code == 200, res.text


def test_route_order_rate_limit_separates_by_strategy(client):
    """다른 strategy의 AI 제안은 별도 윈도우 — 한 strategy 한도가 다른 strategy
    에 영향 X."""
    import asyncio
    from app.ai.virtual_agent import VirtualAiAgent
    from app.brokers.mock_broker import MockBrokerAdapter
    from app.core.modes import OperationMode
    from app.risk.risk_manager import RiskDecision

    client.test_risk_manager.policy.ai_rate_limit_max_count = 2
    risk = client.test_risk_manager
    broker = MockBrokerAdapter()
    agent = VirtualAiAgent()

    async def fire(db, strategy, n):
        for i in range(n):
            proposal = agent.propose_stub("005930", 110, 100)
            await agent.propose_and_route(
                proposal, mode=OperationMode.VIRTUAL_AI_EXECUTION,
                broker=broker, risk=risk, db=db,
                client_order_id=f"{strategy}-{i:03d}",
                strategy=strategy,
            )

    with client.test_db_factory() as db:
        # strategy_a는 한도 임박까지 (2건).
        asyncio.run(fire(db, "strategy_a", 2))
        # strategy_b도 첫 호출 — strategy_a와 격리되어 통과해야 한다.
        proposal = agent.propose_stub("005930", 110, 100)
        result = asyncio.run(agent.propose_and_route(
            proposal, mode=OperationMode.VIRTUAL_AI_EXECUTION,
            broker=broker, risk=risk, db=db,
            client_order_id="strategy_b-001",
            strategy="strategy_b",
        ))
        db.commit()
    assert result.decision == RiskDecision.APPROVED
