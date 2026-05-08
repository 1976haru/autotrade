"""PaperTrader + paper status API 테스트 (#42).

체크리스트 #42: Paper 모드를 명확히 만들고, paper가 live와 섞이지 않게 한다.

본 테스트가 가드:
1. PaperBrokerKind 선택 — MOCK / KIS_PAPER.
2. KIS_IS_PAPER=False면 KIS_PAPER 차단.
3. is_live_broker / is_paper_broker / assert_paper_broker.
4. PaperTrader는 OrderExecutor 위임 — RiskManager 우회 진입점 0건.
5. /api/paper/status read-only.
6. 모든 경로에서 broker.place_order 직접 호출 0건.
"""

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.brokers.base import (
    OrderRequest, OrderResult, OrderSide, OrderStatus,
)
from app.brokers.kis import KisBrokerAdapter
from app.brokers.mock_broker import MockBrokerAdapter
from app.db.models import OrderAuditLog
from app.execution.executor import UnauthorizedOrderError
from app.execution.paper_trader import (
    NotPaperBrokerError,
    PaperBrokerKind,
    PaperTrader,
    assert_paper_broker,
    build_paper_status,
    is_live_broker,
    is_paper_broker,
    make_paper_broker,
)


def _run(coro):
    return asyncio.run(coro)


# ====================================================================
# Broker classification
# ====================================================================


class TestBrokerClassification:
    def test_mock_broker_is_paper(self):
        broker = MockBrokerAdapter()
        assert is_live_broker(broker) is False
        assert is_paper_broker(broker) is True

    def test_kis_with_is_paper_true_is_paper(self):
        # is_paper=True 명시 (settings 의존 회피)
        broker = KisBrokerAdapter(
            app_key="x", app_secret="y", account_no="0/0",
            is_paper=True, client=MagicMock(),
        )
        assert is_paper_broker(broker) is True

    def test_kis_with_is_paper_false_is_live(self):
        broker = KisBrokerAdapter(
            app_key="x", app_secret="y", account_no="0/0",
            is_paper=False, client=MagicMock(),
        )
        assert is_live_broker(broker) is True

    def test_unknown_broker_is_treated_live_conservatively(self):
        """모르는 BrokerAdapter는 보수적으로 live 취급."""
        class _Unknown:
            pass
        assert is_live_broker(_Unknown()) is True


class TestAssertPaperBroker:
    def test_paper_broker_passes(self):
        # No raise expected
        assert_paper_broker(MockBrokerAdapter())

    def test_kis_paper_passes(self):
        broker = KisBrokerAdapter(
            app_key="x", app_secret="y", account_no="0/0",
            is_paper=True, client=MagicMock(),
        )
        assert_paper_broker(broker)

    def test_kis_live_raises(self):
        broker = KisBrokerAdapter(
            app_key="x", app_secret="y", account_no="0/0",
            is_paper=False, client=MagicMock(),
        )
        with pytest.raises(NotPaperBrokerError, match="non-paper broker"):
            assert_paper_broker(broker)


# ====================================================================
# make_paper_broker — selection
# ====================================================================


class TestMakePaperBroker:
    def test_mock_returns_mock_broker(self):
        b = make_paper_broker(PaperBrokerKind.MOCK)
        assert isinstance(b, MockBrokerAdapter)
        assert is_paper_broker(b) is True

    def test_kis_paper_with_setting_true_returns_kis_paper(self, monkeypatch):
        from app.core.config import get_settings
        s = get_settings()
        monkeypatch.setattr(s, "kis_is_paper", True)
        # KisBrokerAdapter init은 Settings 자격증명을 읽지만 client는 lazy.
        b = make_paper_broker(PaperBrokerKind.KIS_PAPER)
        assert isinstance(b, KisBrokerAdapter)
        assert b.is_paper is True

    def test_kis_paper_blocked_when_setting_false(self, monkeypatch):
        from app.core.config import get_settings
        s = get_settings()
        monkeypatch.setattr(s, "kis_is_paper", False)
        with pytest.raises(RuntimeError, match="KIS_IS_PAPER=true"):
            make_paper_broker(PaperBrokerKind.KIS_PAPER)


# ====================================================================
# PaperTrader wrapper
# ====================================================================


class TestPaperTrader:
    def _audit(self, decision="APPROVED"):
        return OrderAuditLog(
            mode="PAPER", symbol="005930", side="BUY", quantity=1,
            order_type="MARKET", latest_price=75_000, decision=decision,
            reasons=[],
        )

    def _order(self):
        return OrderRequest(symbol="005930", side=OrderSide.BUY, quantity=1)

    def _broker_with_place(self):
        broker = MockBrokerAdapter()
        broker.place_order = AsyncMock(return_value=OrderResult(
            order_id="X", status=OrderStatus.FILLED, symbol="005930",
            side=OrderSide.BUY, quantity=1, filled_quantity=1, avg_fill_price=75_000,
        ))
        return broker

    def test_init_refuses_live_broker(self):
        live = KisBrokerAdapter(
            app_key="x", app_secret="y", account_no="0/0",
            is_paper=False, client=MagicMock(),
        )
        with pytest.raises(NotPaperBrokerError):
            PaperTrader(live, db=MagicMock())

    def test_init_accepts_mock_broker(self):
        trader = PaperTrader(MockBrokerAdapter(), db=MagicMock())
        assert trader.broker is not None

    def test_execute_calls_broker_for_approved(self):
        broker = self._broker_with_place()
        trader = PaperTrader(broker, db=MagicMock())
        result = _run(trader.execute(self._order(), self._audit("APPROVED")))
        broker.place_order.assert_awaited_once()
        assert result.status == OrderStatus.FILLED

    def test_execute_refuses_unauthorized_audit(self):
        """RiskManager 우회 시도 — audit decision REJECTED → executor가 차단."""
        broker = self._broker_with_place()
        trader = PaperTrader(broker, db=MagicMock())
        with pytest.raises(UnauthorizedOrderError):
            _run(trader.execute(self._order(), self._audit("REJECTED")))
        broker.place_order.assert_not_awaited()

    def test_execute_refuses_none_audit(self):
        """audit=None은 OrderExecutor가 ValueError로 차단."""
        broker = self._broker_with_place()
        trader = PaperTrader(broker, db=MagicMock())
        with pytest.raises(ValueError, match="audit row is required"):
            _run(trader.execute(self._order(), None))
        broker.place_order.assert_not_awaited()

    def test_execute_re_validates_runtime_paper(self):
        """init 후 broker.is_paper가 외부에서 False로 바뀌면 execute가 차단."""
        broker = MockBrokerAdapter()
        broker.place_order = AsyncMock(return_value=OrderResult(
            order_id="X", status=OrderStatus.FILLED, symbol="005930",
            side=OrderSide.BUY, quantity=1,
        ))
        trader = PaperTrader(broker, db=MagicMock())
        # broker를 live KIS로 swap (정상 운영에선 없는 시나리오, 방어 테스트)
        live = KisBrokerAdapter(
            app_key="x", app_secret="y", account_no="0/0",
            is_paper=False, client=MagicMock(),
        )
        trader.broker = live  # swap
        with pytest.raises(NotPaperBrokerError):
            _run(trader.execute(self._order(), self._audit("APPROVED")))


# ====================================================================
# Paper status snapshot
# ====================================================================


class TestPaperStatus:
    def test_default_settings_is_paper_mode(self, monkeypatch):
        from app.core.config import get_settings
        s = get_settings()
        # Force SIMULATION (default) + flags off
        monkeypatch.setattr(s, "default_mode", "SIMULATION")
        monkeypatch.setattr(s, "enable_live_trading", False)
        monkeypatch.setattr(s, "enable_ai_execution", False)
        monkeypatch.setattr(s, "enable_futures_live_trading", False)
        monkeypatch.setattr(s, "kis_is_paper", True)
        status = build_paper_status()
        assert status.is_paper_mode is True
        assert status.enable_live_trading is False
        assert status.enable_ai_execution is False

    def test_default_broker_kind_is_mock_in_simulation(self, monkeypatch):
        from app.core.config import get_settings
        s = get_settings()
        monkeypatch.setattr(s, "default_mode", "SIMULATION")
        monkeypatch.setattr(s, "kis_is_paper", True)
        monkeypatch.setattr(s, "paper_broker_kind", "")  # default 추론
        status = build_paper_status()
        assert status.paper_broker_kind == "MOCK"

    def test_default_broker_kind_is_kis_paper_in_paper_mode(self, monkeypatch):
        from app.core.config import get_settings
        s = get_settings()
        monkeypatch.setattr(s, "default_mode", "PAPER")
        monkeypatch.setattr(s, "kis_is_paper", True)
        monkeypatch.setattr(s, "paper_broker_kind", "")
        status = build_paper_status()
        assert status.paper_broker_kind == "KIS_PAPER"

    def test_explicit_paper_broker_kind_override(self, monkeypatch):
        from app.core.config import get_settings
        s = get_settings()
        monkeypatch.setattr(s, "default_mode", "PAPER")
        monkeypatch.setattr(s, "kis_is_paper", True)
        monkeypatch.setattr(s, "paper_broker_kind", "MOCK")  # explicit
        status = build_paper_status()
        assert status.paper_broker_kind == "MOCK"


# ====================================================================
# /api/paper/status endpoint
# ====================================================================


class TestPaperStatusEndpoint:
    def test_endpoint_returns_payload(self, client):
        res = client.get("/api/paper/status")
        assert res.status_code == 200
        body = res.json()
        # Required shape
        assert "mode" in body
        assert "is_paper_mode" in body
        assert "paper_broker_kind" in body
        assert "kis_is_paper" in body
        assert "enable_live_trading" in body
        assert "enable_ai_execution" in body
        assert "enable_futures_live_trading" in body
        assert "fill_polling_enabled" in body
        assert "notice" in body
        # Notice mentions paper fill quality
        assert "체결 품질" in body["notice"]

    def test_endpoint_does_not_modify_state(self, client, monkeypatch):
        from app.core.config import get_settings
        s = get_settings()
        before_mode = s.default_mode
        before_kis = s.kis_is_paper
        client.get("/api/paper/status")
        # endpoint는 read-only — settings 변경 0건
        assert s.default_mode == before_mode
        assert s.kis_is_paper == before_kis


# ====================================================================
# Safety — 직접 broker 호출 0건, RiskManager 우회 진입점 0건
# ====================================================================


class TestSafety:
    def test_paper_trader_module_does_not_call_place_order_directly(self):
        from app.execution import paper_trader as mod
        src = inspect.getsource(mod)
        # broker.place_order / .place_order( 호출 형태 0건. 모든 실 호출은
        # OrderExecutor 단일 진입점.
        forbidden = (
            "broker.place_order(", "BrokerAdapter.place_order(",
            ".place_order(",  # 어떤 binding이든
        )
        for f in forbidden:
            assert f not in src, f"forbidden symbol in paper_trader: {f}"

    def test_paper_trader_module_does_not_bypass_route_order(self):
        """PaperTrader는 자체 cancel/route_order 흐름을 만들지 않는다."""
        from app.execution import paper_trader as mod
        src = inspect.getsource(mod)
        forbidden_imports = (
            "from app.brokers.kis_client",
            "from app.execution.order_router",  # circular avoid + bypass 방지
        )
        for f in forbidden_imports:
            assert f not in src

    def test_papertrader_init_signature_does_not_take_api_key(self):
        sig = inspect.signature(PaperTrader.__init__)
        for p in sig.parameters:
            assert "api_key" not in p.lower()
            assert "secret" not in p.lower()

    def test_routes_paper_does_not_modify_state(self):
        """routes_paper.py는 read-only — POST/PUT/DELETE 핸들러 0건."""
        from app.api import routes_paper as mod
        src = inspect.getsource(mod)
        forbidden = ("@router.post(", "@router.put(", "@router.delete(",
                     "@router.patch(")
        for f in forbidden:
            assert f not in src, f"routes_paper must be read-only: {f}"
