"""KIS Paper Auto Trading 파이프라인 — 권한 게이트 + executor + KIS client mock.

이번 작업은 *한투 모의투자 API 전용* 자동매매다. 실거래가 아니다.

검증:
- 권한 게이트: 14개 조건 + reason code (disabled / mode required / live must be
  disabled / creds missing / emergency / market closed / window / no signal /
  low confidence / low quality / missing exit / notional / daily limit / allowed).
- executor: dry_run → KIS_PAPER_DRY_RUN_OK (route_order 호출 0건), submitted →
  KIS_PAPER_SUBMITTED + broker_order_no + AgentDecisionLog, rejected →
  BLOCKED_BY_RISK_MANAGER, needs_approval → BLOCKED_BY_PERMISSION_GATE, live
  broker → NotPaperBrokerError, route_order 예외 → KIS_PAPER_ERROR.
- KIS client mock: paper buy/sell 성공, rejected 응답, network error, live
  place_order NotImplementedError, secret 미노출.
- 정적: auto_executor / auto_permission broker.place_order 직접 호출 0건.
- API: /auto/status, /auto/run-once 기본 OFF.
- 안전 flag 변경 0건, secret 입력 없음 (credentials_present bool 만).
"""

from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.brokers.base import OrderRequest, OrderSide, OrderStatus, OrderType
from app.brokers.kis import KisBrokerAdapter
from app.brokers.kis_client import KisApiError
from app.brokers.mock_broker import MockBrokerAdapter
from app.core.config import Settings
from app.db.base import Base
from app.db.models import AgentDecisionLog, OrderAuditLog
from app.execution.paper_trader import NotPaperBrokerError
from app.kis_paper.auto_executor import (
    KisPaperAutoDecision,
    execute_kis_paper_auto_order,
)
from app.kis_paper.auto_permission import (
    KisPaperOrderPermissionInput,
    KisPaperPermReason,
    evaluate_kis_paper_order_permission,
)
from app.risk.risk_manager import RiskDecision


_KST = timezone(timedelta(hours=9))


def _utc(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=_KST).astimezone(timezone.utc)


OPEN_TIME = _utc(2026, 5, 22, 10, 0)     # 금요일 10:00 KST (장중, 창 안)
CLOSED_TIME = _utc(2026, 5, 22, 16, 0)
EARLY_TIME = _utc(2026, 5, 22, 9, 2)     # 09:02 — 창(09:05) 전


# ──────────────────────────────────────────────────────────────────────────────
# 권한 게이트
# ──────────────────────────────────────────────────────────────────────────────


def _perm_input(**kw) -> KisPaperOrderPermissionInput:
    base = dict(
        enable_kis_paper_auto_trading=True, dry_run=False, kis_is_paper=True,
        enable_live_trading=False, broker_is_kis_paper=True, credentials_present=True,
        emergency_stop=False, side="BUY", notional_krw=975_000, confidence=0.74,
        quality_score=82, has_exit_plan=True, max_order_notional=1_000_000,
        daily_order_count=0, max_orders_per_day=10, window_start="09:05",
        window_end="14:50", min_confidence=0.6, min_quality_score=60, now=OPEN_TIME,
    )
    base.update(kw)
    return KisPaperOrderPermissionInput(**base)


def _R(code):
    return code.value


@pytest.mark.parametrize("kw,expected", [
    ({}, KisPaperPermReason.KIS_PAPER_ORDER_ALLOWED),
    ({"enable_kis_paper_auto_trading": False}, KisPaperPermReason.KIS_PAPER_AUTO_DISABLED),
    ({"kis_is_paper": False}, KisPaperPermReason.KIS_PAPER_MODE_REQUIRED),
    ({"broker_is_kis_paper": False}, KisPaperPermReason.KIS_PAPER_MODE_REQUIRED),
    ({"enable_live_trading": True}, KisPaperPermReason.LIVE_TRADING_MUST_BE_DISABLED),
    ({"credentials_present": False}, KisPaperPermReason.KIS_PAPER_CREDENTIALS_MISSING),
    ({"emergency_stop": True}, KisPaperPermReason.EMERGENCY_STOP_ENABLED),
    ({"now": CLOSED_TIME}, KisPaperPermReason.MARKET_CLOSED),
    ({"now": EARLY_TIME}, KisPaperPermReason.KIS_PAPER_ORDER_WINDOW_CLOSED),
    ({"side": "HOLD"}, KisPaperPermReason.NO_STRATEGY_SIGNAL),
    ({"confidence": 0.3}, KisPaperPermReason.LOW_CONFIDENCE),
    ({"quality_score": 10}, KisPaperPermReason.LOW_QUALITY_SCORE),
    ({"side": "BUY", "has_exit_plan": False}, KisPaperPermReason.MISSING_EXIT_PLAN),
    ({"notional_krw": 2_000_000}, KisPaperPermReason.KIS_PAPER_NOTIONAL_LIMIT_EXCEEDED),
    ({"daily_order_count": 10}, KisPaperPermReason.KIS_PAPER_ORDER_LIMIT_EXCEEDED),
])
def test_permission_gate_reason_codes(kw, expected):
    res = evaluate_kis_paper_order_permission(_perm_input(**kw))
    assert res.reason_code == _R(expected)
    assert res.is_live_authorization is False
    if expected == KisPaperPermReason.KIS_PAPER_ORDER_ALLOWED:
        assert res.allowed is True
    else:
        assert res.allowed is False


def test_permission_sell_does_not_require_exit_plan():
    res = evaluate_kis_paper_order_permission(_perm_input(side="SELL", has_exit_plan=False))
    assert res.allowed is True


@pytest.mark.parametrize("count", [10, 11, 100])
def test_permission_sell_exempt_from_daily_order_cap(count):
    # A(2026-06-05 회귀): 일일 주문 횟수 한도는 *신규 진입(BUY)에만* 적용.
    #   청산(SELL: 손절/익절)은 한도 소진 상태에서도 항상 허용돼야 한다.
    sell = evaluate_kis_paper_order_permission(
        _perm_input(side="SELL", has_exit_plan=False, daily_order_count=count))
    assert sell.allowed is True
    assert sell.reason_code == _R(KisPaperPermReason.KIS_PAPER_ORDER_ALLOWED)
    # 대조군: 같은 한도에서 BUY 는 여전히 차단.
    buy = evaluate_kis_paper_order_permission(
        _perm_input(side="BUY", daily_order_count=count))
    assert buy.allowed is False
    assert buy.reason_code == _R(KisPaperPermReason.KIS_PAPER_ORDER_LIMIT_EXCEEDED)


def test_permission_sell_still_blocked_by_notional_cap():
    # 면제는 *횟수* 한도에 한정 — notional 1회 한도는 SELL 에도 그대로 적용.
    res = evaluate_kis_paper_order_permission(
        _perm_input(side="SELL", has_exit_plan=False,
                    daily_order_count=99, notional_krw=2_000_000))
    assert res.allowed is False
    assert res.reason_code == _R(KisPaperPermReason.KIS_PAPER_NOTIONAL_LIMIT_EXCEEDED)


def test_permission_input_has_no_secret_fields():
    # secret 값(app_key/app_secret/account_no)을 *받지 않는다* — bool 라벨만.
    fields = set(KisPaperOrderPermissionInput.__dataclass_fields__.keys())
    for forbidden in ("app_key", "app_secret", "account_no", "kis_app_key",
                      "kis_app_secret", "kis_account_no", "token", "secret"):
        assert forbidden not in fields


# ──────────────────────────────────────────────────────────────────────────────
# executor
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _settings(**kw):
    base = dict(
        enable_kis_paper_auto_trading=True, kis_paper_auto_order_dry_run=False,
        kis_is_paper=True, enable_live_trading=False,
        kis_paper_auto_max_order_notional=1_000_000, kis_paper_auto_max_orders_per_day=10,
        kis_paper_auto_order_window_start="09:05", kis_paper_auto_order_window_end="14:50",
        kis_paper_auto_min_confidence=0.6, kis_paper_auto_min_quality_score=60,
        market_data_provider="mock",
    )
    base.update(kw)
    return SimpleNamespace(**base)


_DEC = KisPaperAutoDecision(
    symbol="005930", side="BUY", quantity=13, price=75_000,
    selected_strategies=["MOMENTUM", "VWAP"], confidence=0.74, quality_score=82,
    entry_reason="vol+momentum", has_exit_plan=True,
    exit_plan={"stop_loss_pct": 1.5, "take_profit_pct": 3.0},
)


def _approved_route():
    async def _fn(**kw):
        audit = SimpleNamespace(id=1, broker_order_id="PAPER-0001",
                                broker_status="FILLED", filled_quantity=13, executed=True)
        return SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[], audit=audit)
    return _fn


def _rejected_route():
    async def _fn(**kw):
        audit = SimpleNamespace(id=2, broker_order_id=None, broker_status=None,
                                filled_quantity=0, executed=False)
        return SimpleNamespace(decision=RiskDecision.REJECTED,
                               reasons=["notional too high"], audit=audit)
    return _fn


def _needs_approval_route():
    async def _fn(**kw):
        audit = SimpleNamespace(id=3, broker_order_id=None, broker_status=None,
                                filled_quantity=0, executed=False)
        return SimpleNamespace(decision=RiskDecision.NEEDS_APPROVAL, reasons=[], audit=audit)
    return _fn


def _run(db, *, settings=None, decision=None, broker=None, route=None, **kw):
    return asyncio.run(execute_kis_paper_auto_order(
        db, decision=decision or _DEC, settings=settings or _settings(),
        broker=broker or MockBrokerAdapter(), risk=object(),
        broker_is_kis_paper=True, credentials_present=True,
        route_order_fn=route or _approved_route(), now=OPEN_TIME, **kw,
    ))


def test_executor_dry_run_no_route_order(db):
    called = {"n": 0}

    async def _route(**kw):
        called["n"] += 1
        return SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[],
                               audit=SimpleNamespace(id=1))

    r = _run(db, settings=_settings(kis_paper_auto_order_dry_run=True), route=_route)
    db.commit()
    assert r.reason_code == "KIS_PAPER_DRY_RUN_OK"
    assert r.submitted is False
    assert r.broker_order_sent is False
    assert called["n"] == 0                       # route_order 호출 0건.
    assert db.query(AgentDecisionLog).count() == 1   # 기록은 남음.


def test_executor_submitted(db):
    r = _run(db, route=_approved_route())
    db.commit()
    assert r.reason_code == "KIS_PAPER_SUBMITTED"
    assert r.submitted is True
    assert r.broker_order_no == "PAPER-0001"
    assert r.order_status == "FILLED"
    assert r.fill_status == "FILLED"
    assert r.broker_order_type == "KIS_PAPER"
    assert r.broker_order_sent is True
    assert r.is_live_authorization is False
    log = db.query(AgentDecisionLog).one()
    assert log.meta["broker_order_type"] == "KIS_PAPER"
    assert log.meta["broker_order_no"] == "PAPER-0001"
    assert log.meta["is_live_authorization"] is False


def test_executor_sell(db):
    sell = KisPaperAutoDecision(symbol="005930", side="SELL", quantity=13, price=75_000,
                                confidence=0.74, quality_score=82)
    r = _run(db, decision=sell, route=_approved_route())
    assert r.reason_code == "KIS_PAPER_SUBMITTED"
    assert r.side == "SELL"


def _seed_paper_auto_orders(db, n, *, now):
    for i in range(n):
        db.add(OrderAuditLog(
            created_at=now, mode="PAPER", requested_by_ai=False,
            symbol="005930", side="BUY", quantity=1, order_type="MARKET",
            decision="APPROVED", executed=True, broker_order_id=f"X{i}",
            broker_status="FILLED", filled_quantity=1,
            limit_price=75_000, latest_price=75_000,
            trade_reason="kis_paper_auto", strategy="VWAP",
        ))
    db.commit()


def test_executor_sell_routes_when_daily_cap_reached(db):
    # A(2026-06-05 회귀): 한도(10) 소진 상태에서
    #   - 신규 BUY 는 한도로 차단(route_order 호출 0건),
    #   - 청산 SELL 은 면제돼 route_order 로 전송된다.
    _seed_paper_auto_orders(db, 10, now=OPEN_TIME)

    buy_calls = {"n": 0}

    async def _buy_route(**kw):
        buy_calls["n"] += 1
        return SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[],
                               audit=SimpleNamespace(id=1))

    rb = _run(db, decision=_DEC, route=_buy_route)
    assert rb.reason_code == _R(KisPaperPermReason.KIS_PAPER_ORDER_LIMIT_EXCEEDED)
    assert rb.broker_order_sent is False
    assert buy_calls["n"] == 0                       # 한도 차단 → 전송 0건.

    sell = KisPaperAutoDecision(symbol="005930", side="SELL", quantity=13,
                                price=75_000, confidence=0.74, quality_score=82)
    rs = _run(db, decision=sell, route=_approved_route())
    assert rs.reason_code == "KIS_PAPER_SUBMITTED"   # 면제 → 정상 전송.
    assert rs.broker_order_sent is True
    assert rs.side == "SELL"


def test_executor_rejected_by_risk(db):
    r = _run(db, route=_rejected_route())
    assert r.reason_code == "BLOCKED_BY_RISK_MANAGER"
    assert r.submitted is False


def test_executor_needs_approval(db):
    r = _run(db, route=_needs_approval_route())
    assert r.reason_code == "BLOCKED_BY_PERMISSION_GATE"
    assert r.submitted is False


def test_executor_route_order_exception(db):
    async def _boom(**kw):
        raise RuntimeError("kis network down")
    r = _run(db, route=_boom)
    assert r.reason_code == "KIS_PAPER_ERROR"
    assert r.submitted is False


def test_executor_blocks_live_broker(db):
    # dry_run=false + 게이트 통과 후 assert_paper_broker 가 live broker 차단.
    class _FakeLiveBroker:
        is_paper = False
    with pytest.raises(NotPaperBrokerError):
        _run(db, broker=_FakeLiveBroker(), route=_approved_route())


def test_executor_gate_block_no_route(db):
    called = {"n": 0}

    async def _route(**kw):
        called["n"] += 1
        return SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[],
                               audit=SimpleNamespace(id=1))

    r = _run(db, settings=_settings(enable_kis_paper_auto_trading=False), route=_route)
    assert r.reason_code == "KIS_PAPER_AUTO_DISABLED"
    assert called["n"] == 0


# ──────────────────────────────────────────────────────────────────────────────
# KIS client mock (KisBrokerAdapter paper)
# ──────────────────────────────────────────────────────────────────────────────


_SECRET = "FAKE-SECRET-DO-NOT-LEAK-0000"


class _FakeKisClient:
    def __init__(self, *, response=None, raise_error=None):
        self._response = response
        self._raise = raise_error
        self.calls = []

    async def place_order(self, cano, prdt, symbol, *, is_buy, quantity,
                          order_type, limit_price):
        self.calls.append((cano, prdt, symbol, is_buy, quantity))
        if self._raise is not None:
            raise self._raise
        return self._response


def _paper_adapter(client):
    return KisBrokerAdapter(app_key="FAKE-KEY", app_secret=_SECRET,
                            account_no="12345678-01", is_paper=True, client=client)


def test_kis_paper_buy_order_mock_success():
    client = _FakeKisClient(response={"rt_cd": "0", "output": {"ODNO": "0001234567"},
                                      "msg1": "정상처리"})
    adapter = _paper_adapter(client)
    order = OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=13,
                         order_type=OrderType.MARKET)
    result = asyncio.run(adapter.place_order(order))
    assert result.order_id == "0001234567"
    assert result.status == OrderStatus.RECEIVED
    assert client.calls and client.calls[0][3] is True   # is_buy


def test_kis_paper_sell_order_mock_success():
    client = _FakeKisClient(response={"rt_cd": "0", "output": {"ODNO": "0007654321"}})
    adapter = _paper_adapter(client)
    order = OrderRequest(symbol="005930", side=OrderSide.SELL, quantity=5,
                         order_type=OrderType.MARKET)
    result = asyncio.run(adapter.place_order(order))
    assert result.order_id == "0007654321"
    assert client.calls[0][3] is False   # is_buy=False (sell)


def test_kis_paper_rejected_response():
    client = _FakeKisClient(response={"rt_cd": "1", "output": {}, "msg1": "주문거부"})
    adapter = _paper_adapter(client)
    order = OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=13,
                         order_type=OrderType.MARKET)
    result = asyncio.run(adapter.place_order(order))
    assert result.status == OrderStatus.REJECTED


def test_kis_paper_network_error_propagates():
    client = _FakeKisClient(raise_error=KisApiError("KIS paper API timeout"))
    adapter = _paper_adapter(client)
    order = OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=13,
                         order_type=OrderType.MARKET)
    with pytest.raises(KisApiError):
        asyncio.run(adapter.place_order(order))


def test_kis_live_place_order_not_implemented():
    adapter = KisBrokerAdapter(app_key="FAKE", app_secret=_SECRET,
                               account_no="12345678-01", is_paper=False,
                               client=_FakeKisClient(response={"rt_cd": "0"}))
    order = OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1,
                         order_type=OrderType.MARKET)
    with pytest.raises(NotImplementedError):
        asyncio.run(adapter.place_order(order))


def test_kis_secret_not_leaked_in_repr_or_result():
    client = _FakeKisClient(response={"rt_cd": "0", "output": {"ODNO": "0001"}})
    adapter = _paper_adapter(client)
    assert _SECRET not in repr(adapter)
    order = OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1,
                         order_type=OrderType.MARKET)
    result = asyncio.run(adapter.place_order(order))
    assert _SECRET not in str(result.model_dump())


# ──────────────────────────────────────────────────────────────────────────────
# 정적 가드 + 안전 flag
# ──────────────────────────────────────────────────────────────────────────────


def test_static_executor_no_direct_broker_place_order():
    for mod in ("auto_executor.py", "auto_permission.py", "driver_bridge.py"):
        src = (Path(__file__).resolve().parents[1] / "app" / "kis_paper" / mod
               ).read_text(encoding="utf-8")
        # 실제 *호출* 패턴 0건 (docstring 의 클래스 언급은 허용).
        assert "broker.place_order(" not in src
        assert "self.broker.place_order(" not in src
        tree = ast.parse(src)
        # AST 로 broker.place_order(...) 호출 노드 0건 확인.
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr != "place_order", f"{mod}: place_order call"
        # auto_permission / 게이트는 broker / route_order import 0건.
        if mod == "auto_permission.py":
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    m = node.module or ""
                    assert "brokers" not in m and "order_router" not in m and "executor" not in m


def test_safety_flags_defaults_off(safe_default_flags):
    s = Settings()
    assert s.enable_kis_paper_auto_trading is False
    assert s.kis_paper_auto_order_dry_run is True
    assert s.enable_live_trading is False
    assert s.kis_is_paper is True
    assert s.kis_paper_fill_polling is False


# ──────────────────────────────────────────────────────────────────────────────
# API endpoints
# ──────────────────────────────────────────────────────────────────────────────


def test_api_auto_status(safe_default_flags, client):
    res = client.get("/api/kis-paper/auto/status")
    assert res.status_code == 200
    j = res.json()
    assert j["enable_kis_paper_auto_trading"] is False
    assert j["dry_run"] is True
    assert j["is_live_authorization"] is False
    assert j["broker_order_type"] == "KIS_PAPER"


def test_api_auto_run_once_disabled_by_default(safe_default_flags, client):
    res = client.post("/api/kis-paper/auto/run-once", json={
        "symbol": "005930", "side": "BUY", "quantity": 13, "price": 75_000,
        "confidence": 0.74, "quality_score": 82, "has_exit_plan": True,
    })
    assert res.status_code == 200
    j = res.json()
    assert j["reason_code"] == "KIS_PAPER_AUTO_DISABLED"
    assert j["broker_order_sent"] is False
    assert j["is_live_authorization"] is False


# ── 옵션 A: 위험 청산(손절/익절) 품질/확신 게이트 면제 (2026-06-12 손실방어 구멍 수정) ──

def test_risk_exit_sell_bypasses_quality_confidence_gate():
    # ★손절/익절 도달 SELL — 낮은 품질/확신이어도 청산 통과(위험 축소, 강제 청산).
    res = evaluate_kis_paper_order_permission(_perm_input(
        side="SELL", has_exit_plan=False, confidence=0.05, quality_score=10,
        is_risk_exit=True))
    assert res.allowed is True, res.reason_code


def test_general_sell_still_blocked_by_quality_gate():
    # ★역방향 안전: 위험청산 아닌 일반 SELL 은 품질/확신 게이트 *그대로* 적용(면제 누수 0).
    low_q = evaluate_kis_paper_order_permission(_perm_input(
        side="SELL", confidence=0.74, quality_score=10, is_risk_exit=False))
    assert low_q.allowed is False and low_q.reason_code == "LOW_QUALITY_SCORE"
    low_c = evaluate_kis_paper_order_permission(_perm_input(
        side="SELL", confidence=0.05, quality_score=82, is_risk_exit=False))
    assert low_c.allowed is False and low_c.reason_code == "LOW_CONFIDENCE"


def test_buy_never_exempt_even_if_flag_set():
    # BUY 는 위험청산 아님 — 플래그가 어쩌다 켜져도 품질 게이트 적용(BUY 면제 없음).
    res = evaluate_kis_paper_order_permission(_perm_input(
        side="BUY", confidence=0.05, quality_score=10, is_risk_exit=True))
    # ★게이트가 side=SELL 재확인 → BUY 는 플래그 무관 품질/확신 적용 → 차단.
    assert res.allowed is False and res.reason_code in ("LOW_CONFIDENCE", "LOW_QUALITY_SCORE")


def test_risk_exit_still_blocked_by_emergency_stop():
    # ★긴급정지는 모든 것 위 — 위험청산 면제가 긴급정지를 뚫으면 안 됨.
    res = evaluate_kis_paper_order_permission(_perm_input(
        side="SELL", confidence=0.05, quality_score=10, is_risk_exit=True,
        emergency_stop=True))
    assert res.allowed is False and res.reason_code == "EMERGENCY_STOP_ENABLED"


def test_build_permission_input_is_risk_exit_for_held_and_risk_exits():
    # 2026-06-25 결함 B 수정: 보유 청산 SELL 은 리스크 축소 → 진입용 confidence/quality
    #   게이트 면제. 종전엔 sell_reason∈{STOP_LOSS,TAKE_PROFIT}만 면제해, avg=0 등으로
    #   강제손절이 죽고 vote(VWAP) 청산만 나올 때 게이트에 막혀 손실 포지션 미청산
    #   (STOP_LOSS 0건). 이제 held_position 이거나 청산성 reason 이면 면제한다.
    from app.kis_paper.auto_executor import build_permission_input, KisPaperAutoDecision
    from types import SimpleNamespace
    st = SimpleNamespace(enable_kis_paper_auto_trading=True, kis_paper_auto_order_dry_run=False,
                         kis_is_paper=True, enable_live_trading=False,
                         kis_paper_auto_max_order_notional=1_000_000,
                         kis_paper_auto_max_orders_per_day=100,
                         kis_paper_auto_order_window_start="09:05", kis_paper_auto_order_window_end="15:20",
                         kis_paper_auto_min_confidence=0.6, kis_paper_auto_min_quality_score=60)
    def _mk(side, code, held=False):
        return KisPaperAutoDecision(symbol="035420", side=side, quantity=1, price=100,
                                    confidence=0.1, quality_score=5, sell_reason_code=code,
                                    held_position=held)
    kw = dict(settings=st, broker_is_kis_paper=True, credentials_present=True,
              emergency_stop=False, daily_order_count=0, now=OPEN_TIME)
    # 청산성 reason_code (held 무관) → 면제.
    assert build_permission_input(decision=_mk("SELL", "STOP_LOSS"), **kw).is_risk_exit is True
    assert build_permission_input(decision=_mk("SELL", "TAKE_PROFIT"), **kw).is_risk_exit is True
    assert build_permission_input(decision=_mk("SELL", "TRAILING_STOP"), **kw).is_risk_exit is True
    assert build_permission_input(decision=_mk("SELL", "MARKET_CLOSE_EXIT"), **kw).is_risk_exit is True   # 장마감도 청산
    # 보유 청산 SELL 은 reason 무관 면제(핵심 변경) — vote(VWAP 등) 청산도 안 막힘.
    assert build_permission_input(decision=_mk("SELL", "MOMENTUM_REVERSAL", held=True), **kw).is_risk_exit is True
    # 미보유 + 청산성 reason 아님 → 면제 아님(naked 성 일반 SELL).
    assert build_permission_input(decision=_mk("SELL", "MOMENTUM_REVERSAL", held=False), **kw).is_risk_exit is False
    # BUY 는 held 여도 절대 면제 아님(진입 게이트 유지).
    assert build_permission_input(decision=_mk("BUY", "STOP_LOSS", held=True), **kw).is_risk_exit is False
