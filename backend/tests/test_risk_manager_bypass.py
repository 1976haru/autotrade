"""RiskManager 표준 진입점 + 우회 방지 테스트 (#34).

체크리스트 #34: 모든 주문성 요청은 RiskManager.check_order(order, context)를
통과해야 한다. 본 테스트가 다음을 검증:

1. check_order 표준 메서드 — APPROVED / REJECTED / BLOCKED / NEEDS_APPROVAL
   분기.
2. 우회 방지 — RiskManager 거치지 않은 audit row로 OrderExecutor.execute가
   호출되면 UnauthorizedOrderError로 즉시 차단.
3. import 가드 — Strategy / Agent / Filter / Explainability / Market /
   Quality 모듈은 BrokerAdapter.place_order를 직접 호출하지 않는다.
4. CLAUDE.md 절대 원칙 2 — Strategy/Agent는 신호만 만들고, 실 주문은
   route_order → RiskManager → PermissionGate → OrderExecutor → Broker
   단일 진입점을 통과한다.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.brokers.base import (
    Balance,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    Position,
)
from app.core.modes import OperationMode
from app.db.models import OrderAuditLog
from app.execution.executor import OrderExecutor, UnauthorizedOrderError
from app.risk.risk_manager import (
    RiskCheckResult,
    RiskContext,
    RiskDecision,
    RiskManager,
    RiskPolicy,
)


def _run(coro):
    """Sync wrapper for async tests — 본 프로젝트는 pytest-asyncio를 쓰지 않음."""
    return asyncio.run(coro)


# ---------- helpers ----------


def _balance(cash: int = 10_000_000) -> Balance:
    return Balance(cash=cash, equity=cash, buying_power=cash)


def _buy(qty: int = 1, symbol: str = "005930") -> OrderRequest:
    return OrderRequest(symbol=symbol, side=OrderSide.BUY, quantity=qty)


def _sell(qty: int = 1, symbol: str = "005930") -> OrderRequest:
    return OrderRequest(symbol=symbol, side=OrderSide.SELL, quantity=qty)


def _ctx(
    *,
    mode: OperationMode = OperationMode.SIMULATION,
    cash: int = 10_000_000,
    positions: list[Position] | None = None,
    latest_price: int = 75_000,
    requested_by_ai: bool = False,
    latest_price_timestamp: datetime | None = None,
    market_regime: str | None = None,
    market_regime_decision: str | None = None,
    emergency_stop_override: bool | None = None,
) -> RiskContext:
    return RiskContext(
        mode=mode,
        balance=_balance(cash),
        positions=positions or [],
        latest_price=latest_price,
        latest_price_timestamp=latest_price_timestamp,
        requested_by_ai=requested_by_ai,
        market_regime=market_regime,
        market_regime_decision=market_regime_decision,
        emergency_stop_override=emergency_stop_override,
    )


# ====================================================================
# check_order — happy path
# ====================================================================


class TestCheckOrderHappy:
    def test_simulation_small_order_is_approved(self):
        risk = RiskManager(RiskPolicy())
        result = risk.check_order(_buy(1), _ctx())
        assert result.decision == RiskDecision.APPROVED
        assert result.allowed is True
        assert result.status == "APPROVED"
        assert result.blocked_by is None
        assert result.required_action is None
        assert result.evaluated_at is not None

    def test_to_dict_serializable(self):
        risk = RiskManager(RiskPolicy())
        result = risk.check_order(_buy(1), _ctx())
        d = result.to_dict()
        assert d["decision"] == "APPROVED"
        assert d["status"] == "APPROVED"
        assert d["allowed"] is True
        assert "evaluated_at" in d
        assert "warnings" in d


# ====================================================================
# check_order — REJECTED / BLOCKED 분기
# ====================================================================


class TestCheckOrderBlocked:
    def test_rejects_over_notional(self):
        risk = RiskManager(RiskPolicy(max_order_notional=100_000))
        result = risk.check_order(_buy(10), _ctx())
        assert result.decision == RiskDecision.REJECTED
        assert any("notional" in r for r in result.reasons)
        # policy_violation으로 분류 (specific blocked_by 키워드 매칭 안 됨)
        assert result.blocked_by == "policy_violation"

    def test_emergency_stop_yields_blocked(self):
        risk = RiskManager(RiskPolicy())
        risk.set_emergency_stop(True)
        result = risk.check_order(_buy(1), _ctx())
        assert result.decision == RiskDecision.BLOCKED
        assert result.blocked_by == "emergency_stop"
        assert result.required_action == "OPERATOR_RESET"

    def test_emergency_stop_override_yields_blocked(self):
        risk = RiskManager(RiskPolicy())
        result = risk.check_order(_buy(1), _ctx(emergency_stop_override=True))
        assert result.decision == RiskDecision.BLOCKED
        assert result.blocked_by == "emergency_stop_override"

    def test_stale_price_yields_blocked(self):
        risk = RiskManager(RiskPolicy(stale_price_max_age_seconds=30))
        old = datetime.now(timezone.utc) - timedelta(minutes=5)
        result = risk.check_order(_buy(1), _ctx(latest_price_timestamp=old))
        assert result.decision == RiskDecision.BLOCKED
        assert result.blocked_by == "stale_price"
        assert result.required_action == "WAIT_FOR_FRESH_DATA"

    def test_ai_disabled_yields_blocked(self):
        risk = RiskManager(RiskPolicy(disable_ai_orders=True))
        result = risk.check_order(_buy(1), _ctx(requested_by_ai=True))
        assert result.decision == RiskDecision.BLOCKED
        assert result.blocked_by == "ai_kill_switch"

    def test_ai_disabled_does_not_affect_non_ai_orders(self):
        risk = RiskManager(RiskPolicy(disable_ai_orders=True))
        result = risk.check_order(_buy(1), _ctx(requested_by_ai=False))
        assert result.decision == RiskDecision.APPROVED

    def test_live_trading_disabled_yields_blocked(self):
        # LIVE_AI_EXECUTION + enable_live_trading=False (default)
        risk = RiskManager(RiskPolicy(enable_live_trading=False, enable_ai_execution=True))
        result = risk.check_order(
            _buy(1),
            _ctx(mode=OperationMode.LIVE_AI_EXECUTION, requested_by_ai=True),
        )
        # evaluate_order이 LIVE 모드 + flag off에서 reasons에 추가 → check_order이 BLOCKED으로 변환
        assert result.decision == RiskDecision.BLOCKED
        assert result.blocked_by in ("live_trading_disabled", "ai_execution_disabled")


# ====================================================================
# check_order — NEEDS_APPROVAL
# ====================================================================


class TestCheckOrderNeedsApproval:
    def test_live_manual_approval_yields_needs_approval(self):
        risk = RiskManager(RiskPolicy(enable_live_trading=True))
        result = risk.check_order(
            _buy(1), _ctx(mode=OperationMode.LIVE_MANUAL_APPROVAL),
        )
        assert result.decision == RiskDecision.NEEDS_APPROVAL
        assert result.required_action == "MANUAL_APPROVAL"


# ====================================================================
# check_order — Market regime (#32 filter) 통합
# ====================================================================


class TestCheckOrderRegime:
    def test_buy_blocked_when_regime_block_new_buy(self):
        risk = RiskManager(RiskPolicy())
        result = risk.check_order(
            _buy(1),
            _ctx(market_regime="RISK_OFF", market_regime_decision="BLOCK_NEW_BUY"),
        )
        assert result.decision == RiskDecision.BLOCKED
        assert result.blocked_by == "market_regime"
        assert any("BLOCK_NEW_BUY" in r for r in result.reasons)

    def test_sell_passes_when_regime_block_new_buy(self):
        """SELL은 리스크 축소 — regime 차단해도 통과."""
        risk = RiskManager(RiskPolicy())
        # 보유 포지션이 있어야 SELL이 정상 평가됨
        positions = [Position(symbol="005930", quantity=10, avg_price=70_000, market_price=75_000)]
        result = risk.check_order(
            _sell(1),
            _ctx(positions=positions, market_regime="RISK_OFF",
                  market_regime_decision="BLOCK_NEW_BUY"),
        )
        assert result.decision == RiskDecision.APPROVED

    def test_buy_blocked_when_regime_watch_only(self):
        risk = RiskManager(RiskPolicy())
        result = risk.check_order(
            _buy(1),
            _ctx(market_regime="TREND_DOWN", market_regime_decision="WATCH_ONLY"),
        )
        assert result.decision == RiskDecision.BLOCKED
        assert result.blocked_by == "market_regime"

    def test_buy_warned_when_regime_reduce_size(self):
        """REDUCE_SIZE는 차단이 아닌 warning — APPROVED 유지."""
        risk = RiskManager(RiskPolicy())
        result = risk.check_order(
            _buy(1),
            _ctx(market_regime="HIGH_VOLATILITY", market_regime_decision="REDUCE_SIZE"),
        )
        assert result.decision == RiskDecision.APPROVED
        assert any("REDUCE_SIZE" in w for w in result.warnings)


# ====================================================================
# evaluate_order backwards compat
# ====================================================================


class TestBackwardsCompat:
    def test_evaluate_order_unchanged(self):
        """기존 evaluate_order 호출은 그대로 동작 — 기존 테스트 호환."""
        risk = RiskManager(RiskPolicy())
        result = risk.evaluate_order(
            order=_buy(1), mode=OperationMode.SIMULATION,
            balance=_balance(), positions=[], latest_price=75_000,
        )
        assert result.decision == RiskDecision.APPROVED
        assert isinstance(result, RiskCheckResult)

    def test_evaluate_order_emergency_stop_still_returns_rejected(self):
        """evaluate_order는 emergency_stop에서 REJECTED 반환 (BLOCKED 변환 X) —
        기존 테스트 호환."""
        risk = RiskManager(RiskPolicy())
        risk.set_emergency_stop(True)
        result = risk.evaluate_order(
            order=_buy(1), mode=OperationMode.SIMULATION,
            balance=_balance(), positions=[], latest_price=75_000,
        )
        assert result.decision == RiskDecision.REJECTED


# ====================================================================
# OrderExecutor 우회 방지 — UnauthorizedOrderError
# ====================================================================


class TestExecutorBypass:
    def _make_audit(self, decision: str = "APPROVED") -> OrderAuditLog:
        return OrderAuditLog(
            mode="SIMULATION", symbol="005930", side="BUY", quantity=1,
            order_type="MARKET", latest_price=75_000, decision=decision,
            reasons=[],
        )

    def _mock_broker(self) -> MagicMock:
        broker = MagicMock()
        broker.place_order = AsyncMock(return_value=OrderResult(
            order_id="X", status=OrderStatus.FILLED, symbol="005930",
            side=OrderSide.BUY, quantity=1, filled_quantity=1, avg_fill_price=75_000,
        ))
        return broker

    def test_execute_with_approved_audit_calls_broker(self):
        broker = self._mock_broker()
        audit = self._make_audit("APPROVED")
        executor = OrderExecutor(broker, db=MagicMock())
        result = _run(executor.execute(_buy(1), audit))
        broker.place_order.assert_awaited_once()
        assert audit.executed is True
        assert result.status == OrderStatus.FILLED

    def test_execute_refuses_rejected_audit(self):
        broker = self._mock_broker()
        audit = self._make_audit("REJECTED")
        executor = OrderExecutor(broker, db=MagicMock())
        with pytest.raises(UnauthorizedOrderError):
            _run(executor.execute(_buy(1), audit))
        broker.place_order.assert_not_awaited()
        # SQLAlchemy default가 insert 시점에 채워지므로 detached row의 attr은
        # None일 수 있음 — 핵심은 broker가 호출되지 않았다는 것.
        assert not audit.executed

    def test_execute_refuses_blocked_audit(self):
        broker = self._mock_broker()
        audit = self._make_audit("BLOCKED")
        executor = OrderExecutor(broker, db=MagicMock())
        with pytest.raises(UnauthorizedOrderError):
            _run(executor.execute(_buy(1), audit))
        broker.place_order.assert_not_awaited()

    def test_execute_accepts_needs_approval_audit(self):
        """NEEDS_APPROVAL audit row는 PermissionGate.approve의 정상 경로 —
        operator 결정은 PendingApproval.status에 별도 기록되고 audit.decision
        는 RiskManager의 원래 판정을 보존하는 기존 contract.
        bypass 가드는 REJECTED/BLOCKED만 막는다."""
        broker = self._mock_broker()
        audit = self._make_audit("NEEDS_APPROVAL")
        executor = OrderExecutor(broker, db=MagicMock())
        result = _run(executor.execute(_buy(1), audit))
        broker.place_order.assert_awaited_once()
        assert result.status == OrderStatus.FILLED

    def test_execute_refuses_unknown_decision(self):
        broker = self._mock_broker()
        audit = self._make_audit("MYSTERY_DECISION")
        executor = OrderExecutor(broker, db=MagicMock())
        with pytest.raises(UnauthorizedOrderError):
            _run(executor.execute(_buy(1), audit))
        broker.place_order.assert_not_awaited()

    def test_execute_requires_audit(self):
        broker = self._mock_broker()
        executor = OrderExecutor(broker, db=MagicMock())
        with pytest.raises(ValueError, match="audit row is required"):
            _run(executor.execute(_buy(1), None))
        broker.place_order.assert_not_awaited()


# ====================================================================
# Import 가드 — broker.place_order 직접 호출 금지
# ====================================================================


_FORBIDDEN_BROKER_CALLS = (
    "broker.place_order(", "BrokerAdapter.place_order(",
    ".place_order(",  # any binding's .place_order(
)


def _module_source(module_path: str) -> str:
    """모듈 source를 읽어 반환 (.place_order( 검색용)."""
    import importlib

    mod = importlib.import_module(module_path)
    if mod.__file__ is None:
        return ""
    with open(mod.__file__, encoding="utf-8") as f:
        return f.read()


class TestNoDirectBrokerCalls:
    """전략 / 필터 / 설명/감사 / 신호 품질 / regime 모듈은 broker.place_order
    를 직접 호출하지 않는다 (CLAUDE.md 절대 원칙 2). 본 테스트는 strict —
    `.place_order(` substring을 직접 grep해 최후의 backstop을 만든다.
    """

    @pytest.mark.parametrize("module_path", [
        "app.strategies.base",
        "app.strategies.concrete.sma_crossover",
        "app.strategies.concrete.orb_vwap",
        "app.strategies.concrete.rsi_reversion",
        "app.strategies.concrete.volume_breakout",
        "app.strategies.concrete.pullback_rebreak",
        "app.strategies.concrete.vwap_strategy",
        "app.strategies.live_engine",
        "app.strategies.quality",
        "app.strategies.scoreboard",
        "app.strategies.vwap",
        "app.market.regime",
        "app.filters.market_regime",
        "app.explainability.reasons",
        "app.api.routes_explainability",
    ])
    def test_module_does_not_call_place_order(self, module_path: str):
        src = _module_source(module_path)
        for forbidden in _FORBIDDEN_BROKER_CALLS:
            assert forbidden not in src, (
                f"forbidden broker call '{forbidden}' found in {module_path} — "
                "Strategy/Filter/Explainability layer must route through "
                "RiskManager.check_order, not call broker directly"
            )

    @pytest.mark.parametrize("module_path", [
        "app.strategies.base",
        "app.strategies.concrete.sma_crossover",
        "app.strategies.concrete.volume_breakout",
        "app.strategies.concrete.pullback_rebreak",
        "app.strategies.concrete.vwap_strategy",
        "app.strategies.quality",
        "app.strategies.vwap",
        "app.filters.market_regime",
        "app.explainability.reasons",
        "app.market.regime",
    ])
    def test_module_does_not_import_executor_or_router(self, module_path: str):
        """전략/필터/설명 레이어는 OrderExecutor / route_order / RiskManager
        본체를 import하지 않는다 — 신호 생성 책임만 진다."""
        src = _module_source(module_path)
        forbidden_imports = (
            "from app.execution.executor",
            "from app.execution.order_router",
            "from app.risk.risk_manager",
            "from app.risk.auto_stop",
        )
        for f in forbidden_imports:
            assert f not in src, f"forbidden import '{f}' in {module_path}"

    def test_routes_audit_does_not_call_broker_directly(self):
        """routes_audit은 read-only — broker 호출 0건."""
        src = _module_source("app.api.routes_audit")
        for forbidden in _FORBIDDEN_BROKER_CALLS:
            assert forbidden not in src

    def test_routes_explainability_does_not_call_broker_or_route(self):
        src = _module_source("app.api.routes_explainability")
        for forbidden in _FORBIDDEN_BROKER_CALLS + ("route_order(",):
            assert forbidden not in src


# ====================================================================
# route_order이 RiskManager를 거치는지 (정적 검증)
# ====================================================================


class TestRouteOrderUsesRiskManager:
    def test_route_order_imports_risk_manager(self):
        src = _module_source("app.execution.order_router")
        # evaluate_order 또는 check_order 둘 중 하나는 호출되어야 한다.
        assert "from app.risk.risk_manager" in src
        assert ("risk.evaluate_order(" in src) or ("risk.check_order(" in src)

    def test_route_order_uses_order_executor(self):
        src = _module_source("app.execution.order_router")
        assert "from app.execution.executor" in src
        assert "OrderExecutor(" in src

    def test_route_order_does_not_call_broker_place_order_directly(self):
        """route_order는 broker.place_order를 직접 호출하지 않고 OrderExecutor
        를 통해서만 호출 — 단일 진입점 invariant."""
        src = _module_source("app.execution.order_router")
        # broker.place_order 직접 호출 없음 (OrderExecutor.execute 경유 only).
        # 단 broker.get_price/get_balance/get_positions은 read이므로 허용.
        assert "broker.place_order(" not in src
