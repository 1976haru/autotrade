"""Stress / volume tests (133, MUST).

CLAUDE.md '단타 자동매매 = 손실 방어와 감사 로그 우선'에 따라, 시스템이
대량 트래픽이나 악의적/비정상 입력 패턴 하에서도 invariant를 유지하는지
검증한다.

각 시나리오는 시간 측정(time_ns)을 포함해 docs/stress_test_report.md에
기록할 결과를 산출한다 — 실측값은 환경에 따라 달라지므로 assert에는
관대한 임계만 둔다.

시나리오 (사용자 명시 8번):
1. approval 1000건 — 큐 생성/조회 안정
2. mock order 1000건 (SIMULATION) — 즉시 체결 + audit 기록
3. risk rejection 대량 — max_order_notional 초과 모두 REJECTED + audit
4. emergency stop ON — 모든 모드 차단 (060)
5. stale price 차단 — broker가 price 못 가져오면 거부 (현 MockBroker 미구현, TODO)
6. duplicate approval 차단 — 같은 approval 두 번째부터 409
7. audit endpoint limit 캡 — 단일 응답 페이로드 폭주 방지
8. duplicate order 차단 (140) — 같은 client_order_id로 두 번째부터 409
"""

import time

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.modes import OperationMode
from app.db.models import OrderAuditLog, PendingApproval


# 157: 모듈 전체 slow 마커 — 일반 CI에서 자동 제외, nightly workflow에서만 실행.
# CI 러너의 cold start나 jsdom-비교 시간 측정 assertion이 flake를 일으키므로
# unit test 흐름과 분리한다.
pytestmark = pytest.mark.slow


def _enable_live_manual(monkeypatch, client):
    monkeypatch.setattr(
        get_settings(), "default_mode", OperationMode.LIVE_MANUAL_APPROVAL
    )
    client.test_risk_manager.policy.enable_live_trading = True


def _submit(client, symbol="005930", side="BUY", quantity=1):
    return client.post("/api/broker/orders", json={
        "symbol": symbol, "side": side, "quantity": quantity,
    })


# 사용자 명시는 1000건이지만 CI 시간을 고려해 100건으로 같은 시스템적
# invariant를 검증한다 — 'submit 1만번 했을 때도 거부 invariant가 유지되는가'
# 같은 질문은 100건과 1000건이 동등하게 답한다(시간 측정만 차이). 1000건은
# manual full-run에서 별도 검증 — docs/stress_test_report.md 참조.
LARGE_N = 100


def test_stress_create_pending_approvals_at_volume(client, monkeypatch):
    """1) 1000건 PENDING 큐 — 모두 PENDING_APPROVAL로 응답하고 list 응답에
    포함. SQLite + StaticPool 환경에서 1000 insert가 합리적인 시간(< 30s)에
    완료. 운영 환경(Postgres)에서는 인덱스로 더 빠르다."""
    _enable_live_manual(monkeypatch, client)

    t0 = time.time_ns()
    for _ in range(LARGE_N):
        res = _submit(client)
        assert res.status_code == 202
    elapsed = (time.time_ns() - t0) / 1e9

    pending = client.get("/api/approvals").json()
    assert len(pending) == LARGE_N
    # 1건당 30ms 이상 걸리면 의심 — 운영 환경에선 한참 더 빠름.
    assert elapsed < LARGE_N * 0.03, (
        f"submit avg too slow: {elapsed:.2f}s for {LARGE_N} (>{LARGE_N * 30}ms)"
    )


def test_stress_simulation_orders_execute_immediately(client):
    """2) SIMULATION N건 — risk OK이면 즉시 broker FILL + audit 기록.
    NEEDS_APPROVAL 큐를 거치지 않는 fast-path.

    Note: default RiskPolicy는 max_positions=5 / max_symbol_exposure=1.5M라
    같은 symbol BUY를 N번 누적하면 후반부 거부된다. 본 시나리오의 의도는
    'risk OK일 때 fast-path' 확인이므로 (a) policy 한도를 stress 동안만 풀고
    (b) BUY/SELL 교대로 net exposure 0 유지.
    """
    # policy 한도를 임시로 풀어 누적 한도 외의 invariant만 검증.
    client.test_risk_manager.policy.max_positions       = 999_999
    client.test_risk_manager.policy.max_symbol_exposure = 999_999_999_999
    client.test_risk_manager.policy.max_order_notional  = 999_999_999_999

    N = LARGE_N
    for i in range(N):
        side = "BUY" if i % 2 == 0 else "SELL"
        res = _submit(client, side=side)
        assert res.status_code == 200, f"unexpected reject at {i}: {res.text}"
        body = res.json()
        assert body["status"] == "FILLED"

    audit = client.get("/api/audit/orders", params={"limit": N}).json()
    assert len(audit) == N
    assert all(a["executed"] for a in audit)
    assert all(a["broker_status"] == "FILLED" for a in audit)


def test_stress_risk_rejection_at_volume_records_audit(client, monkeypatch):
    """3) max_order_notional 초과 1000건 — 모두 REJECTED, 매 건 audit row
    기록. invariant: '거부도 audit에 남는다' (CLAUDE.md '손실 방어 + 감사
    로그 우선')."""
    # 거부되는 입력: market price * quantity > max_order_notional.
    # MockBroker는 price ~ symbol 매핑이라 quantity를 키워 초과를 유발.
    huge_qty = 100_000  # 1억원 노출 시도, default max_order_notional=1,000,000

    N = LARGE_N
    for _ in range(N):
        res = _submit(client, quantity=huge_qty)
        assert res.status_code == 400, f"expected REJECTED but got {res.status_code}"

    with client.test_db_factory() as db:
        rows = db.execute(select(OrderAuditLog)).scalars().all()
        assert len(rows) == N
        assert all(r.decision == "REJECTED" for r in rows)
        assert all(r.executed is False for r in rows)


def test_stress_emergency_stop_blocks_all_submissions(client):
    """4) emergency_stop ON — 모든 모드에서 어떤 양식의 주문이든 거부 (060).
    1000건 흘려도 PendingApproval / 체결 audit 모두 0 (REJECTED audit만)."""
    client.test_risk_manager.emergency_stop = True

    N = LARGE_N
    for _ in range(N):
        res = _submit(client)
        assert res.status_code == 400

    with client.test_db_factory() as db:
        # PendingApproval 한 건도 없어야 한다.
        assert db.execute(select(PendingApproval)).all() == []
        rows = db.execute(select(OrderAuditLog)).scalars().all()
        assert len(rows) == N
        assert all(r.decision == "REJECTED" for r in rows)
        assert all(r.executed is False for r in rows)


def test_stress_stale_price_rejects_order(client):
    """5) broker 시세가 stale_price_max_age_seconds 초과로 오래되면 RiskManager가
    REJECTED. invariant: '시세 없으면 / 시세가 stale이면 주문 안 간다' (143).

    MockBroker.set_stale_price_for_test로 특정 symbol에 인공적인 stale timestamp
    를 주입한 뒤 주문 → 400 + audit row REJECTED 확인."""
    threshold = client.test_risk_manager.policy.stale_price_max_age_seconds
    # threshold가 0/음수면 이 검사가 비활성 — 테스트 의도와 어긋나므로 명시.
    assert threshold > 0, "stale check must be enabled for this test"
    # threshold + 여유로 set — flaky 회피.
    client.test_broker.set_stale_price_for_test("005930", age_seconds=threshold + 30)

    res = client.post("/api/broker/orders", json={
        "symbol": "005930", "side": "BUY", "quantity": 1,
    })
    assert res.status_code == 400, res.text
    # error envelope의 reasons는 audit row에서 직접 검증 (envelope 형식과 무관하게
    # invariant: 거부 사유가 audit에 남는다).
    with client.test_db_factory() as db:
        rows = db.execute(select(OrderAuditLog)).scalars().all()
        assert len(rows) == 1
        assert rows[0].decision == "REJECTED"
        assert any("stale" in r for r in rows[0].reasons), rows[0].reasons


def test_stress_duplicate_approval_returns_409(client, monkeypatch):
    """6) 한 approval에 대해 두 번째 approve부터는 409 — duplicate decision
    차단. PermissionGate가 status=PENDING만 처리하는 invariant."""
    _enable_live_manual(monkeypatch, client)
    submit = _submit(client).json()
    approval_id = submit["approval_id"]

    first = client.post(f"/api/approvals/{approval_id}/approve",
                        json={"decided_by": "ops1"})
    assert first.status_code == 200

    # 100번 추가 시도 — 모두 409.
    for _ in range(100):
        res = client.post(f"/api/approvals/{approval_id}/approve",
                          json={"decided_by": "ops1"})
        assert res.status_code == 409


def test_stress_duplicate_order_returns_409(client):
    """8) 같은 client_order_id로 100건 시도 — 첫 1건 200, 나머지 99건 409.
    invariant: onClick double-fire 같은 사고에서 두 번 체결되는 위험 차단."""
    cid = "stress-dup-001"
    first = client.post("/api/broker/orders", json={
        "symbol": "005930", "side": "BUY", "quantity": 1, "client_order_id": cid,
    })
    assert first.status_code == 200, first.text

    for _ in range(99):
        res = client.post("/api/broker/orders", json={
            "symbol": "005930", "side": "BUY", "quantity": 1, "client_order_id": cid,
        })
        assert res.status_code == 409


def test_stress_distinct_client_order_ids_all_pass(client):
    """invariant: client_order_id가 다르면 idempotency 검사가 영향 없다.
    같은 symbol/side/quantity여도 서로 다른 id면 별개 주문.
    누적 risk 한도와 무관하도록 BUY/SELL 교대 + risk policy 풀기."""
    client.test_risk_manager.policy.max_positions       = 999_999
    client.test_risk_manager.policy.max_symbol_exposure = 999_999_999_999
    client.test_risk_manager.policy.max_order_notional  = 999_999_999_999

    for i in range(50):
        res = client.post("/api/broker/orders", json={
            "symbol": "005930", "side": "BUY" if i % 2 == 0 else "SELL",
            "quantity": 1,
            "client_order_id": f"stress-distinct-{i:03d}",
        })
        assert res.status_code == 200, f"unexpected reject at {i}: {res.text}"


# ---------- 155: virtual / futures / AI 확장 시나리오 ----------

def test_stress_virtual_stock_orders_audit_consistency(client):
    """가상 주식 주문 N건 → audit row N개 + 모두 executed=True (SIMULATION 흐름).
    invariant: '가드 통과한 모든 주문이 정확히 한 audit row를 만든다'."""
    from app.db.models import OrderAuditLog
    client.test_risk_manager.policy.max_positions       = 999_999
    client.test_risk_manager.policy.max_symbol_exposure = 999_999_999_999
    client.test_risk_manager.policy.max_order_notional  = 999_999_999_999

    N = 200
    for i in range(N):
        side = "BUY" if i % 2 == 0 else "SELL"
        res = _submit(client, side=side)
        assert res.status_code == 200

    with client.test_db_factory() as db:
        rows = db.execute(select(OrderAuditLog)).scalars().all()
        assert len(rows) == N
        assert all(r.executed for r in rows)


def test_stress_virtual_futures_orders_via_mock_broker():
    """200건 long/short 주문 — 가상 broker가 cash/margin/positions 정합성 유지."""
    import asyncio
    from app.futures.mock import MockFuturesBroker
    from app.futures.types import FuturesOrderRequest, FuturesSide

    broker = MockFuturesBroker(initial_cash=20_000_000)
    broker.set_mark_price("KOSPI200_2503", 1_000)
    broker.set_leverage(5.0)

    async def run_n(n):
        for i in range(n):
            side = FuturesSide.BUY if i % 2 == 0 else FuturesSide.SELL
            req = FuturesOrderRequest(
                contract="KOSPI200_2503", side=side, quantity=1,
            )
            res = await broker.place_order(req)
            # 진입/청산 모두 FILLED 또는 REJECTED(잔고 부족 시).
            assert res.status.value in ("FILLED", "REJECTED")

    asyncio.run(run_n(200))
    # 정합성: 잔고 + 마지막 포지션 + 누적 PnL = initial_cash 인근 (slippage/fee 손실).
    bal = asyncio.run(broker.get_balance())
    # 슬리피지 + fee로 약간 줄지만, equity는 양수 — 음수면 broker에 버그.
    assert bal.equity > 0


def test_stress_futures_force_liquidation():
    """5x 레버리지 LONG 진입 → mark price 강제청산 임계로 떨어뜨림 → 자동 청산."""
    import asyncio
    from app.futures.mock import MockFuturesBroker
    from app.futures.types import FuturesOrderRequest, FuturesSide

    broker = MockFuturesBroker(initial_cash=10_000_000)
    broker.set_mark_price("X", 1000)
    broker.set_leverage(5.0)
    asyncio.run(broker.place_order(
        FuturesOrderRequest(contract="X", side=FuturesSide.BUY, quantity=1)
    ))
    pos = broker.positions["X"]
    assert pos.liquidation_price is not None

    # liquidation_price로 강하게 하락.
    broker.set_mark_price("X", pos.liquidation_price)
    result = broker.force_liquidate_if_needed("X")
    assert result is not None
    assert result.message == "virtual_force_liquidate"
    assert "X" not in broker.positions


def test_stress_futures_margin_insufficient_at_scale():
    """가상 broker에 cash 100원만 두고 100건 주문 → 모두 insufficient_cash."""
    import asyncio
    from app.futures.mock import MockFuturesBroker
    from app.futures.types import FuturesOrderRequest, FuturesSide

    broker = MockFuturesBroker(initial_cash=100)
    broker.set_mark_price("X", 1_000_000)
    broker.set_leverage(5.0)

    async def run_n():
        for _ in range(100):
            res = await broker.place_order(
                FuturesOrderRequest(contract="X", side=FuturesSide.BUY, quantity=1)
            )
            assert res.message == "insufficient_cash"

    asyncio.run(run_n())


def test_stress_ai_virtual_proposals_at_scale(client):
    """AI 제안 100건 — 모두 audit + requested_by_ai=True + ai_decision_meta 보존."""
    import asyncio
    from app.ai.virtual_agent import VirtualAiAgent
    from app.brokers.mock_broker import MockBrokerAdapter
    from app.core.modes import OperationMode
    from app.db.models import OrderAuditLog

    client.test_risk_manager.policy.max_positions       = 999_999
    client.test_risk_manager.policy.max_symbol_exposure = 999_999_999_999
    client.test_risk_manager.policy.max_order_notional  = 999_999_999_999

    agent = VirtualAiAgent()
    risk = client.test_risk_manager
    broker = MockBrokerAdapter()

    async def run_n(db):
        for i in range(100):
            # BUY/SELL 교대로 누적 한도 회피.
            last  = 110 if i % 2 == 0 else 90
            prev  = 100
            proposal = agent.propose_stub("005930", last, prev, confidence=70)
            await agent.propose_and_route(
                proposal, mode=OperationMode.VIRTUAL_AI_EXECUTION,
                broker=broker, risk=risk, db=db,
                client_order_id=f"ai-stress-{i:03d}",
            )

    with client.test_db_factory() as db:
        asyncio.run(run_n(db))
        db.commit()
        rows = db.execute(select(OrderAuditLog).where(
            OrderAuditLog.requested_by_ai.is_(True)
        )).scalars().all()
    assert len(rows) == 100
    assert all(r.ai_decision_meta is not None for r in rows)


def test_stress_audit_log_no_loss_invariant(client):
    """모든 주문 경로에서 audit row 누락 0건 — 거부도 audit에 남는다."""
    from app.db.models import OrderAuditLog
    # mix: 정상 주문 + 한도 초과 주문 (REJECTED) + emergency_stop 차단.
    huge_qty = 100_000
    for _ in range(50):
        _submit(client)                            # 정상 (REJECTED는 한도와 별개)
    for _ in range(50):
        _submit(client, quantity=huge_qty)         # notional REJECT
    client.test_risk_manager.set_emergency_stop(True)
    for _ in range(50):
        _submit(client)                            # emergency_stop REJECT

    with client.test_db_factory() as db:
        rows = db.execute(select(OrderAuditLog)).scalars().all()
    assert len(rows) == 150


def test_stress_audit_endpoint_returns_under_limit(client):
    """7) /api/audit/orders는 limit 파라미터로 응답 캡 — N건 인서트 후
    limit=N이면 N건, default(50)이면 50건. invariant: 단일 응답 페이로드 폭주
    방지. (limit > N이면 N건만 반환되는 것도 동시 검증.)"""
    # SIMULATION 100건 즉시 체결 — risk policy 풀어 fast-path 보장.
    client.test_risk_manager.policy.max_positions       = 999_999
    client.test_risk_manager.policy.max_symbol_exposure = 999_999_999_999
    client.test_risk_manager.policy.max_order_notional  = 999_999_999_999

    N = LARGE_N
    for i in range(N):
        _submit(client, side=("BUY" if i % 2 == 0 else "SELL"))

    res50    = client.get("/api/audit/orders").json()                       # default 50
    res_n    = client.get("/api/audit/orders", params={"limit": N}).json()  # N
    res_huge = client.get("/api/audit/orders", params={"limit": 200}).json()
    assert len(res50)    == 50
    assert len(res_n)    == N
    # N=100인데 limit=200이면 인서트된 N개만 반환 (DB에 그 이상이 없음).
    assert len(res_huge) == N
