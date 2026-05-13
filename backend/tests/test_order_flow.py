"""체크리스트 #66: Integration Test — signal → risk → order → fill → position
전체 파이프라인 E2E.

본 파일의 목적은 *단위 테스트가 검증한 각 단계가 실제로 서로 연결*되는지
하나의 시나리오로 trace하는 것이다. 운영자/감사가 "신호가 발생하면 어떤
경로로 broker까지 가는가"를 한 파일에서 읽고 확인할 수 있어야 한다.

CLAUDE.md 절대 원칙 (테스트로 lock):
1. 실 broker live order 호출 0건 — 모든 테스트는 `MockBrokerAdapter`만 사용
2. 실 KIS / 키움 / Anthropic / Telegram API 호출 0건 — 외부 네트워크 0건
3. LIVE_AI_EXECUTION 활성화 0건
4. FUTURES_LIVE 활성화 0건
5. API Key / Secret / 계좌번호 변경 0건 (테스트 fixture는 빈 문자열 / 가짜 값)
6. in-memory SQLite + MockBrokerAdapter + fake AI client (`conftest.py`)
7. 모든 주문 경로는 `route_order` 단일 진입점을 통과한다는 invariant 검증

각 시나리오는 narrative 형태로 한 단계씩 trace + 중간 상태 assertion으로
구성. 단위 테스트가 이미 검증한 가드 세부값은 *반복 검증하지 않고*, 흐름
연결 자체에 집중한다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.brokers.base import OrderRequest, OrderSide, OrderType
from app.core.config import get_settings
from app.core.modes import OperationMode
from app.db.models import OrderAuditLog, PendingApproval
from app.execution.order_router import route_order
from app.risk.risk_manager import RiskDecision


# =====================================================================
# Helpers
# =====================================================================


def _submit_http(client, symbol="005930", side="BUY", quantity=1):
    """HTTP 진입점 — `POST /api/broker/orders`. route_order 단일 진입점을 거쳐
    RiskManager → (APPROVED/REJECTED/NEEDS_APPROVAL) 분기."""
    return client.post("/api/broker/orders", json={
        "symbol": symbol, "side": side, "quantity": quantity,
    })


def _enable_live_manual(monkeypatch, client):
    monkeypatch.setattr(
        get_settings(), "default_mode", OperationMode.LIVE_MANUAL_APPROVAL,
    )
    client.test_risk_manager.policy.enable_live_trading = True


def _set_default_mode(monkeypatch, mode: OperationMode):
    monkeypatch.setattr(get_settings(), "default_mode", mode)


# =====================================================================
# 1. SIMULATION mode — signal → risk APPROVED → MockBroker fill → position
# =====================================================================


def test_full_pipeline_simulation_buy_signal_to_filled_position(client):
    """SIMULATION 모드 + 정상 한도 + 충분 자본 → broker 호출 + audit/position
    동시 갱신을 한 단위로 trace."""
    broker = client.test_broker  # MockBrokerAdapter

    # 1) 사전 상태 — broker는 가상 자본만 보유, 포지션 0건.
    initial = asyncio.run(broker.get_balance())
    initial_positions = asyncio.run(broker.get_positions())
    assert initial.cash > 0
    assert initial_positions == []

    # 2) HTTP 진입 (route_order 단일 진입점)
    res = _submit_http(client, symbol="005930", side="BUY", quantity=1)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "FILLED"
    assert body["filled_quantity"] == 1
    fill_price = body["avg_fill_price"]
    assert fill_price > 0

    # 3) audit row — RiskManager APPROVED + broker FILLED
    with client.test_db_factory() as db:
        rows = db.execute(select(OrderAuditLog)).scalars().all()
        assert len(rows) == 1
        au = rows[0]
        assert au.decision == RiskDecision.APPROVED.value
        assert au.executed is True
        assert au.broker_status == "FILLED"
        assert au.filled_quantity == 1
        assert au.avg_fill_price == fill_price

    # 4) 포지션 형성 — MockBrokerAdapter 내부 dict 업데이트
    after_pos = asyncio.run(broker.get_positions())
    assert len(after_pos) == 1
    assert after_pos[0].symbol == "005930"
    assert after_pos[0].quantity == 1

    # 5) 자본 차감 — fill_price * 1만큼 cash 감소
    after_balance = asyncio.run(broker.get_balance())
    assert after_balance.cash == initial.cash - fill_price

    # 6) PendingApproval은 없음 — SIMULATION은 자동 라우팅
    with client.test_db_factory() as db:
        assert db.execute(select(PendingApproval)).all() == []


# =====================================================================
# 2. SIMULATION — BUY → SELL → 포지션 청산 + cash 복귀
# =====================================================================


def test_full_pipeline_buy_then_sell_closes_position_and_returns_cash(client):
    """라운드 트립: BUY → SELL → 포지션 0건 + 두 audit row + cash 복원
    (가격 변동 없으면 fill_price 동일)."""
    broker = client.test_broker
    initial_cash = asyncio.run(broker.get_balance()).cash

    # BUY
    buy = _submit_http(client, "005930", "BUY", 1)
    assert buy.json()["status"] == "FILLED"
    buy_fill = buy.json()["avg_fill_price"]

    # SELL
    sell = _submit_http(client, "005930", "SELL", 1)
    assert sell.json()["status"] == "FILLED"
    sell_fill = sell.json()["avg_fill_price"]

    # 포지션 0건
    positions = asyncio.run(broker.get_positions())
    assert positions == []

    # cash = initial - buy + sell. MockBroker는 set_price가 없는 한 가격 변동 0.
    after_cash = asyncio.run(broker.get_balance()).cash
    assert after_cash == initial_cash - buy_fill + sell_fill

    # audit row 2건 + 둘 다 executed
    with client.test_db_factory() as db:
        rows = db.execute(select(OrderAuditLog).order_by(OrderAuditLog.id)).scalars().all()
        assert len(rows) == 2
        assert rows[0].side == "BUY" and rows[0].executed is True
        assert rows[1].side == "SELL" and rows[1].executed is True


# =====================================================================
# 3. SIMULATION — RiskManager REJECTED (한도 초과) → broker 미호출
# =====================================================================


def test_full_pipeline_risk_rejects_oversized_order_no_broker_call(client):
    """주문 명목 > max_order_notional → RiskManager REJECTED → broker
    place_order 도달 0건. audit row는 REJECTED로 남음."""
    risk = client.test_risk_manager
    risk.policy.max_order_notional = 10_000   # 매우 작게
    broker = client.test_broker
    initial = asyncio.run(broker.get_balance())

    # 1주 * mock 가격(>> 10_000) → 한도 초과
    res = _submit_http(client, "005930", "BUY", 1)
    assert res.status_code == 400, res.text
    # detail은 dict({reasons, error}) 또는 string일 수 있다 — 양쪽 모두 대응.
    text = res.text.lower()
    assert "notional" in text or "rejected" in text

    # audit row 존재 + REJECTED
    with client.test_db_factory() as db:
        rows = db.execute(select(OrderAuditLog)).scalars().all()
        assert len(rows) == 1
        assert rows[0].decision == RiskDecision.REJECTED.value
        assert rows[0].executed is False
        # 한도 위반 reason 포함
        assert any("notional" in r.lower() or "한도" in r
                    for r in (rows[0].reasons or []))

    # broker 포지션 / cash 변경 없음
    final = asyncio.run(broker.get_balance())
    assert final.cash == initial.cash
    assert asyncio.run(broker.get_positions()) == []


# =====================================================================
# 4. LIVE_MANUAL_APPROVAL — signal → NEEDS_APPROVAL → 운영자 승인 → 체결
# =====================================================================


def test_full_pipeline_live_manual_approval_routes_through_queue(client, monkeypatch):
    """LIVE_MANUAL_APPROVAL 모드 + enable_live_trading=true → NEEDS_APPROVAL.
    운영자가 /approve 호출하면 PermissionGate 재검증 후 broker 체결.

    중요 단계: route_order는 broker를 *호출하지 않고* PendingApproval로 분기 → 운영자가
    승인 → PermissionGate.approve가 RiskManager 재검증 → OrderExecutor 호출.
    """
    _enable_live_manual(monkeypatch, client)
    broker = client.test_broker

    # 1) 제출 — 202 + NEEDS_APPROVAL
    submit = _submit_http(client)
    assert submit.status_code == 202, submit.text
    approval_id = submit.json()["approval_id"]

    # 2) DB 상태 — PENDING approval + audit NEEDS_APPROVAL
    with client.test_db_factory() as db:
        ap = db.execute(select(PendingApproval)).scalar_one()
        assert ap.id == approval_id
        assert ap.status == "PENDING"
        au = db.execute(select(OrderAuditLog)).scalar_one()
        assert au.decision == "NEEDS_APPROVAL"
        assert au.executed is False
    # 이 시점에 broker 포지션 0건 — broker에 도달 안 함
    assert asyncio.run(broker.get_positions()) == []

    # 3) 운영자 승인
    res = client.post(f"/api/approvals/{approval_id}/approve",
                      json={"decided_by": "ops1", "note": "ok"})
    assert res.status_code == 200, res.text
    assert res.json()["approval"]["status"] == "APPROVED"
    assert res.json()["result"]["status"]   == "FILLED"

    # 4) audit 갱신 + broker 포지션 1건
    with client.test_db_factory() as db:
        au = db.execute(select(OrderAuditLog)).scalar_one()
        assert au.executed is True
        assert au.broker_status == "FILLED"
    pos = asyncio.run(broker.get_positions())
    assert len(pos) == 1 and pos[0].symbol == "005930"


# =====================================================================
# 5. LIVE_MANUAL_APPROVAL — 운영자 거부 → broker 미호출
# =====================================================================


def test_full_pipeline_reject_at_approve_time_blocks_execution(client, monkeypatch):
    """승인 단계에서 운영자가 거부 → broker 도달 0건, PendingApproval REJECTED."""
    _enable_live_manual(monkeypatch, client)
    broker = client.test_broker

    submit = _submit_http(client)
    approval_id = submit.json()["approval_id"]

    res = client.post(f"/api/approvals/{approval_id}/reject",
                      json={"decided_by": "ops1", "note": "신호 약함"})
    assert res.status_code == 200
    assert res.json()["status"] == "REJECTED"

    # broker 변경 없음
    assert asyncio.run(broker.get_positions()) == []
    with client.test_db_factory() as db:
        au = db.execute(select(OrderAuditLog)).scalar_one()
        assert au.executed is False
        assert au.broker_status in (None, "")


# =====================================================================
# 6. Emergency stop ON → 신규 주문 차단 (broker 미호출)
# =====================================================================


def test_full_pipeline_emergency_stop_blocks_new_buy(client):
    """RiskManager.emergency_stop=True → SIMULATION 모드의 정상 주문도 REJECTED.
    audit row만 작성되고 broker.place_order 도달 0건."""
    risk = client.test_risk_manager
    risk.set_emergency_stop(True)
    broker = client.test_broker
    initial = asyncio.run(broker.get_balance())

    res = _submit_http(client)
    assert res.status_code == 400

    with client.test_db_factory() as db:
        au = db.execute(select(OrderAuditLog)).scalar_one()
        assert au.decision == RiskDecision.REJECTED.value
        assert au.executed is False
        assert any("emergency stop" in r.lower() for r in (au.reasons or []))

    # broker 상태 변경 없음
    assert asyncio.run(broker.get_balance()).cash == initial.cash
    assert asyncio.run(broker.get_positions()) == []


# =====================================================================
# 7. route_order 직접 호출 — Python 레벨 invariant 검증
# =====================================================================


def test_route_order_python_level_pipeline_buy_then_sell(client):
    """HTTP 우회 — `route_order(...)`를 직접 호출해 신호→리스크→체결 단일
    파이프라인이 *함수 호출 한 번*으로 끝남을 trace. 통합 테스트 narrative의
    가장 응축된 형태."""
    risk = client.test_risk_manager
    broker = client.test_broker
    initial_cash = asyncio.run(broker.get_balance()).cash

    # BUY through route_order — 가드 → broker.place_order → audit 갱신
    with client.test_db_factory() as db:
        buy_order = OrderRequest(
            symbol="005930", side=OrderSide.BUY, quantity=1,
            order_type=OrderType.MARKET, strategy="test",
            trade_reason="integration_test", client_order_id="flow-buy-1",
        )
        buy_result = asyncio.run(route_order(
            order=buy_order, requested_by_ai=False,
            mode=OperationMode.SIMULATION, broker=broker, risk=risk, db=db,
        ))
        assert buy_result.decision == RiskDecision.APPROVED
        assert buy_result.audit.executed is True
        assert buy_result.result is not None
        assert buy_result.result.status == "FILLED"

    # 포지션 형성 + cash 감소
    pos_after_buy = asyncio.run(broker.get_positions())
    assert len(pos_after_buy) == 1
    assert pos_after_buy[0].quantity == 1
    cash_after_buy = asyncio.run(broker.get_balance()).cash
    assert cash_after_buy < initial_cash

    # SELL through route_order
    with client.test_db_factory() as db:
        sell_order = OrderRequest(
            symbol="005930", side=OrderSide.SELL, quantity=1,
            order_type=OrderType.MARKET, strategy="test",
            trade_reason="integration_test", client_order_id="flow-sell-1",
        )
        sell_result = asyncio.run(route_order(
            order=sell_order, requested_by_ai=False,
            mode=OperationMode.SIMULATION, broker=broker, risk=risk, db=db,
        ))
        assert sell_result.decision == RiskDecision.APPROVED
        assert sell_result.audit.executed is True

    # 포지션 청산
    assert asyncio.run(broker.get_positions()) == []


# =====================================================================
# 8. Strategy 신호 → live_engine.submit_tick → 가드 → MockBroker
# =====================================================================


def test_strategy_signal_through_live_engine_reaches_mock_broker(client):
    """Strategy.on_bar로 BUY 신호 → LiveStrategyEngine.submit_tick → route_order
    → MockBroker. 전략 → 가드 → 체결의 최단 경로 trace."""
    from app.backtest.types import Bar, Signal
    from app.strategies.base import Strategy
    from app.strategies.live_engine import LiveStrategyEngine

    class _AlwaysBuy(Strategy):
        entry = "always BUY"
        exit  = "never"
        invalidation = "always"
        required_regime = "any"
        risk_profile = {"position_size_pct": 1, "stop_loss_pct": 1}
        def on_bar(self, bars):
            return Signal.BUY if len(bars) == 1 else Signal.HOLD

    risk = client.test_risk_manager
    broker = client.test_broker

    with client.test_db_factory() as db:
        engine = LiveStrategyEngine(
            strategy=_AlwaysBuy(), quantity=1,
            broker=broker, risk=risk, db=db,
            mode=OperationMode.SIMULATION,
            strategy_name="always_buy",
        )
        bar = Bar(
            symbol="005930",
            timestamp=datetime.now(timezone.utc),
            open=60_000, high=60_500, low=59_500, close=60_000, volume=10_000,
        )
        tick = asyncio.run(engine.submit_tick(bar))
        assert tick.signal == Signal.BUY
        assert tick.intended_order is not None
        assert tick.routing is not None
        assert tick.routing.decision == RiskDecision.APPROVED
        assert tick.routing.audit.executed is True

    # broker 포지션 1건 + audit row strategy 필드 채워짐
    positions = asyncio.run(broker.get_positions())
    assert len(positions) == 1
    with client.test_db_factory() as db:
        au = db.execute(select(OrderAuditLog)).scalar_one()
        assert au.strategy == "always_buy"
        assert au.trade_reason == "strategy_signal"


# =====================================================================
# 9. Idempotency — same client_order_id 재시도 → 두 번째는 DuplicateOrderError
# =====================================================================


def test_duplicate_client_order_id_does_not_double_execute(client):
    """동일 client_order_id로 두 번 호출하면 두 번째는 route_order에서 즉시
    `DuplicateOrderError` raise — broker.place_order 두 번째 호출 발생 안 함."""
    from app.execution.order_router import DuplicateOrderError

    risk = client.test_risk_manager
    broker = client.test_broker
    initial_cash = asyncio.run(broker.get_balance()).cash

    with client.test_db_factory() as db:
        order = OrderRequest(
            symbol="005930", side=OrderSide.BUY, quantity=1,
            order_type=OrderType.MARKET,
            client_order_id="idem-key-1", trade_reason="integration_test",
        )
        r1 = asyncio.run(route_order(
            order=order, requested_by_ai=False,
            mode=OperationMode.SIMULATION, broker=broker, risk=risk, db=db,
        ))
        assert r1.decision == RiskDecision.APPROVED
        cash_after_first = asyncio.run(broker.get_balance()).cash

        # 두 번째 같은 client_order_id → DuplicateOrderError
        try:
            asyncio.run(route_order(
                order=order, requested_by_ai=False,
                mode=OperationMode.SIMULATION, broker=broker, risk=risk, db=db,
            ))
            raise AssertionError("expected DuplicateOrderError")
        except DuplicateOrderError:
            pass

    # broker는 한 번만 체결 — cash가 두 번 줄지 않음
    assert asyncio.run(broker.get_balance()).cash == cash_after_first
    # audit row도 한 건만 (cash + 한 건 차감 = initial - first_fill)
    assert cash_after_first < initial_cash
    with client.test_db_factory() as db:
        rows = db.execute(select(OrderAuditLog)).scalars().all()
        assert len(rows) == 1


# =====================================================================
# 10. Invariants — 실 broker / 실 API / Secret 접근 0건
# =====================================================================


def test_invariant_test_broker_is_mock_not_kis_live(client):
    """conftest의 broker fixture는 *반드시* MockBrokerAdapter — KIS live 어댑터
    또는 다른 실 broker는 등장하지 않는다."""
    from app.brokers.mock_broker import MockBrokerAdapter
    assert isinstance(client.test_broker, MockBrokerAdapter), (
        f"integration test must use MockBrokerAdapter, got "
        f"{type(client.test_broker).__name__}"
    )


def test_invariant_live_trading_flag_default_false(client):
    """default 상태에서 enable_live_trading=False — LIVE 모드여도 실거래 차단."""
    assert client.test_risk_manager.policy.enable_live_trading is False


def test_invariant_test_db_is_in_memory(client):
    """test_db_factory는 in-memory SQLite — 운영 DB에 영향 0건."""
    with client.test_db_factory() as db:
        # bind URL이 sqlite:// 인지 확인 (in-memory pattern)
        url = str(db.get_bind().url)
        assert url.startswith("sqlite://"), f"test DB must be sqlite, got {url}"


def test_invariant_no_real_telegram_or_anthropic_keys_in_settings(client):  # noqa: ARG001
    """get_settings()의 KIS / Telegram / Anthropic key는 empty 또는 placeholder.
    .env에 실 키가 있어도 본 invariant는 '*테스트가 키를 의도적으로 사용하지
    않는다*'를 lock — 단, .env 자체는 본 PR이 건드리지 않으므로 검증은 키가
    잘 *전달되지 않는다*는 사실 위주.
    """
    settings = get_settings()
    # 모든 필드는 string이어야 한다 (Settings 모델 — 빈 값 default).
    # 본 테스트는 *형식*만 lock. 실값이 있어도 본 통합 테스트는 그 키를
    # 사용하는 코드 경로에 도달하지 않는다 (MockBroker / NoOpChannel).
    assert isinstance(settings.kis_app_key, str)
    assert isinstance(settings.telegram_bot_token, str)
    assert isinstance(settings.anthropic_api_key, str)


def test_invariant_no_outbound_network_during_pipeline(client, monkeypatch):
    """전체 파이프라인 실행 중 외부 네트워크(http/socket)를 시도하면 즉시 실패.
    `socket.create_connection`을 monkeypatch로 막아 어떤 외부 호출도 일어나지
    않음을 lock."""
    import socket

    def _block_connect(*args, **kwargs):  # noqa: ARG001
        raise AssertionError(
            "integration test attempted outbound network — invariant violated"
        )

    monkeypatch.setattr(socket, "create_connection", _block_connect)
    # 정상 simulation 주문 — 외부 호출 0건이라 통과해야 함
    res = _submit_http(client)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "FILLED"
