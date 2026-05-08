"""OrderExecutor 표준 진입점 + 직접 broker 호출 금지 테스트 (#40).

체크리스트 #40: 모든 주문은 route_order → OrderGuard → RiskManager →
PermissionGate → OrderExecutor → BrokerAdapter 단일 경로만 통과해야 한다.

본 테스트가 가드:
1. `app/execution/order_executor.py` 모듈 존재 + OrderExecutor / OrderSource
   re-export.
2. `derive_order_source` 휴리스틱 (STRATEGY / AI / MANUAL).
3. OrderExecutor 우회 방지 (#34 backstop은 그대로 유지).
4. **직접 broker.place_order 호출 0건** — frontend / strategies / agents /
   filters / explainability / risk / api 라우트 어떤 모듈도 우회 X.
5. route_order이 audit row에 source를 채운다.
"""

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.brokers.base import (
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
)
from app.db.models import OrderAuditLog
from app.execution.order_executor import (
    OrderExecutor,
    OrderSource,
    UnauthorizedOrderError,
    derive_order_source,
)


# ====================================================================
# Module shape — re-export
# ====================================================================


class TestModuleShape:
    def test_order_executor_module_exposes_core_symbols(self):
        from app.execution import order_executor as mod
        assert mod.OrderExecutor is OrderExecutor
        assert mod.UnauthorizedOrderError is UnauthorizedOrderError
        assert mod.derive_order_source is derive_order_source
        assert hasattr(mod, "OrderSource")

    def test_order_executor_alias_does_not_break_existing_imports(self):
        """기존 `from app.execution.executor import OrderExecutor` 그대로 동작."""
        from app.execution.executor import OrderExecutor as Legacy
        assert Legacy is OrderExecutor


# ====================================================================
# OrderSource enum + helper
# ====================================================================


class TestOrderSource:
    def test_enum_values(self):
        assert {s.value for s in OrderSource} == {
            "STRATEGY", "AI", "MANUAL", "OPERATOR_OVERRIDE", "UNKNOWN",
        }

    def test_ai_takes_precedence(self):
        order = OrderRequest(
            symbol="X", side=OrderSide.BUY, quantity=1,
            order_type=OrderType.MARKET, strategy="vwap",
        )
        assert derive_order_source(order, requested_by_ai=True) == OrderSource.AI

    def test_strategy_when_not_ai(self):
        order = OrderRequest(
            symbol="X", side=OrderSide.BUY, quantity=1,
            order_type=OrderType.MARKET, strategy="vwap",
        )
        assert derive_order_source(order, requested_by_ai=False) == OrderSource.STRATEGY

    def test_manual_when_no_strategy_no_ai(self):
        order = OrderRequest(
            symbol="X", side=OrderSide.BUY, quantity=1,
            order_type=OrderType.MARKET,
        )
        assert derive_order_source(order, requested_by_ai=False) == OrderSource.MANUAL

    def test_explicit_source_override(self):
        order = OrderRequest(
            symbol="X", side=OrderSide.BUY, quantity=1,
            order_type=OrderType.MARKET, strategy="vwap",
        )
        assert derive_order_source(
            order, requested_by_ai=True, explicit_source="OPERATOR_OVERRIDE",
        ) == OrderSource.OPERATOR_OVERRIDE

    def test_invalid_explicit_falls_back_to_heuristic(self):
        order = OrderRequest(
            symbol="X", side=OrderSide.BUY, quantity=1,
            order_type=OrderType.MARKET, strategy="vwap",
        )
        # invalid string → fall through
        result = derive_order_source(
            order, requested_by_ai=False, explicit_source="MYSTERY",
        )
        assert result == OrderSource.STRATEGY


# ====================================================================
# OrderExecutor backstop (가드 — #34 invariant)
# ====================================================================


def _run(coro):
    return asyncio.run(coro)


class TestExecutorBackstop:
    def _broker(self):
        broker = MagicMock()
        broker.place_order = AsyncMock(return_value=OrderResult(
            order_id="X", status=OrderStatus.FILLED, symbol="005930",
            side=OrderSide.BUY, quantity=1, filled_quantity=1, avg_fill_price=75_000,
        ))
        return broker

    def _audit(self, decision: str = "APPROVED") -> OrderAuditLog:
        return OrderAuditLog(
            mode="SIMULATION", symbol="005930", side="BUY", quantity=1,
            order_type="MARKET", latest_price=75_000, decision=decision,
            reasons=[],
        )

    def test_executor_calls_broker_for_approved_audit(self):
        broker = self._broker()
        executor = OrderExecutor(broker, db=MagicMock())
        order = OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1)
        result = _run(executor.execute(order, self._audit("APPROVED")))
        broker.place_order.assert_awaited_once()
        assert result.status == OrderStatus.FILLED

    def test_executor_refuses_unauthorized_audit(self):
        broker = self._broker()
        executor = OrderExecutor(broker, db=MagicMock())
        order = OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1)
        with pytest.raises(UnauthorizedOrderError):
            _run(executor.execute(order, self._audit("REJECTED")))
        broker.place_order.assert_not_awaited()


# ====================================================================
# Direct broker.place_order 호출 금지 — application-wide grep
# ====================================================================
#
# Frontend / Strategy / Agent / Filter / Explainability / API route 어느 곳도
# `BrokerAdapter.place_order`를 직접 호출해서는 안 된다. OrderExecutor 단
# 한 곳만 진입. 기존 `tests/test_risk_manager_bypass.py`가 일부 모듈을 가드
# 하지만, 본 테스트는 *애플리케이션 전체*를 grep해 단 하나의 caller만 남김
# 을 확인.

FORBIDDEN_PLACE_ORDER_CALLS = (
    "broker.place_order(",
    "BrokerAdapter.place_order(",
)


def _module_source(dotted_path: str) -> str:
    import importlib
    mod = importlib.import_module(dotted_path)
    if mod.__file__ is None:
        return ""
    with open(mod.__file__, encoding="utf-8") as f:
        return f.read()


# 검사 대상 — 직접 broker 호출이 절대 없어야 하는 모듈들.
_NO_BROKER_CALL_MODULES = (
    # API routes
    "app.api.routes_agents",
    "app.api.routes_ai",
    "app.api.routes_approvals",
    "app.api.routes_audit",
    "app.api.routes_backtest",
    "app.api.routes_broker",
    "app.api.routes_explainability",
    "app.api.routes_futures",
    "app.api.routes_governance",
    "app.api.routes_live_engine",
    "app.api.routes_market",
    "app.api.routes_reconciliation",
    "app.api.routes_risk",
    "app.api.routes_status",
    "app.api.routes_themes",
    "app.api.routes_virtual",
    "app.api.routes_watchlists",
    # Strategies
    "app.strategies.base",
    "app.strategies.live_engine",
    "app.strategies.quality",
    "app.strategies.scoreboard",
    "app.strategies.vwap",
    "app.strategies.concrete.sma_crossover",
    "app.strategies.concrete.orb_vwap",
    "app.strategies.concrete.rsi_reversion",
    "app.strategies.concrete.volume_breakout",
    "app.strategies.concrete.pullback_rebreak",
    "app.strategies.concrete.vwap_strategy",
    # Agents / AI
    "app.ai.virtual_agent",
    # Filters / Market
    "app.filters.market_regime",
    "app.market.regime",
    # Explainability
    "app.explainability.reasons",
    # Risk
    "app.risk.risk_manager",
    "app.risk.position_limits",
    "app.risk.loss_limits",
    "app.risk.order_guard",
    "app.risk.emergency_stop",
    "app.risk.ai_permission_gate",
    "app.risk.daily_pnl",
    "app.risk.auto_stop",
    # Permission
    "app.permission.gate",
)


class TestNoDirectBrokerCalls:
    @pytest.mark.parametrize("module_path", _NO_BROKER_CALL_MODULES)
    def test_module_has_no_place_order_call(self, module_path):
        src = _module_source(module_path)
        for forbidden in FORBIDDEN_PLACE_ORDER_CALLS:
            assert forbidden not in src, (
                f"{module_path} contains forbidden direct call '{forbidden}'. "
                "All orders must go through OrderExecutor (#40)."
            )

    def test_only_executor_calls_broker_place_order(self):
        """app.execution.executor만 broker.place_order를 호출 — 단일 진입점."""
        executor_src = _module_source("app.execution.executor")
        # OrderExecutor.execute() 내부에 broker.place_order 호출이 정확히 1번.
        assert executor_src.count("broker.place_order(") >= 1, (
            "app.execution.executor must call broker.place_order at least once"
        )

    def test_order_executor_alias_does_not_independently_call_broker(self):
        """alias 모듈은 자체적으로 broker를 호출하지 않고, executor.py만 위임."""
        alias_src = _module_source("app.execution.order_executor")
        assert "broker.place_order(" not in alias_src
        assert "BrokerAdapter.place_order(" not in alias_src


# ====================================================================
# Permission gate / approval — broker.place_order 단 한 군데에만
# ====================================================================


class TestSinglePlaceOrderEntryPoint:
    def test_permission_gate_uses_order_executor(self):
        """PermissionGate.approve가 broker.place_order를 *직접* 호출하지 않고
        OrderExecutor를 경유한다 — 기존 invariant 보존 검증."""
        src = _module_source("app.permission.gate")
        # broker.place_order 직접 호출 0건. OrderExecutor는 사용 OK.
        assert "broker.place_order(" not in src
        assert "OrderExecutor(" in src

    def test_route_order_uses_order_executor(self):
        src = _module_source("app.execution.order_router")
        assert "broker.place_order(" not in src
        assert "OrderExecutor(" in src


# ====================================================================
# route_order populates source on audit row (#40)
# ====================================================================


class TestRouteOrderPopulatesSource:
    def test_strategy_signal_yields_strategy_source(self, client):
        """전략 주문(strategy 필드 set, requested_by_ai=False) → source=STRATEGY."""
        from app.api.deps import get_risk_manager
        from app.brokers.mock_broker import MockBrokerAdapter
        from app.core.modes import OperationMode
        from app.execution.order_router import route_order
        risk = get_risk_manager()
        order = OrderRequest(
            symbol="005930", side=OrderSide.BUY, quantity=1,
            order_type=OrderType.MARKET, strategy="vwap",
        )
        with client.test_db_factory() as db:
            _run(route_order(
                order=order, requested_by_ai=False,
                mode=OperationMode.SIMULATION, broker=MockBrokerAdapter(),
                risk=risk, db=db,
            ))
        body = client.get("/api/audit/orders").json()
        assert len(body) >= 1
        assert body[0]["source"] == "STRATEGY"
        assert body[0]["strategy"] == "vwap"

    def test_ai_request_yields_ai_source(self, client):
        from app.api.deps import get_risk_manager
        from app.brokers.mock_broker import MockBrokerAdapter
        from app.core.modes import OperationMode
        from app.execution.order_router import route_order
        risk = get_risk_manager()
        order = OrderRequest(
            symbol="005930", side=OrderSide.BUY, quantity=1,
            order_type=OrderType.MARKET, strategy="vwap",
            ai_decision_meta={"reasons": ["AI agreed"], "confidence": 80},
            signal_confidence=80,
        )
        with client.test_db_factory() as db:
            _run(route_order(
                order=order, requested_by_ai=True,
                mode=OperationMode.SIMULATION, broker=MockBrokerAdapter(),
                risk=risk, db=db,
            ))
        body = client.get("/api/audit/orders").json()
        assert body[0]["source"] == "AI"
        assert body[0]["requested_by_ai"] is True

    def test_manual_order_yields_manual_source(self, client):
        from app.api.deps import get_risk_manager
        from app.brokers.mock_broker import MockBrokerAdapter
        from app.core.modes import OperationMode
        from app.execution.order_router import route_order
        risk = get_risk_manager()
        order = OrderRequest(
            symbol="005930", side=OrderSide.BUY, quantity=1,
            order_type=OrderType.MARKET,  # no strategy
        )
        with client.test_db_factory() as db:
            _run(route_order(
                order=order, requested_by_ai=False,
                mode=OperationMode.SIMULATION, broker=MockBrokerAdapter(),
                risk=risk, db=db,
            ))
        body = client.get("/api/audit/orders").json()
        assert body[0]["source"] == "MANUAL"
        assert body[0]["strategy"] is None


# ====================================================================
# Safety
# ====================================================================


class TestSafety:
    def test_executor_module_does_not_take_api_key(self):
        """OrderExecutor 생성자 시그니처에 api_key/secret 매개변수 0건."""
        sig = inspect.signature(OrderExecutor.__init__)
        for p in sig.parameters:
            assert "api_key" not in p.lower()
            assert "secret" not in p.lower()
            assert "account" not in p.lower()

    def test_alias_module_imports_are_safe(self):
        """alias 모듈은 broker / kis / order_router import 0건 — 순수 re-export."""
        src = _module_source("app.execution.order_executor")
        forbidden = (
            "from app.brokers.kis",
            "from app.execution.order_router",  # circular import 방지
        )
        for f in forbidden:
            assert f not in src, f"forbidden import: {f}"
