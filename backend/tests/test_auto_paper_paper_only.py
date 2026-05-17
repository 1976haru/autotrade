"""Auto Paper Loop tick → PaperBroker/VirtualExecutor 결선 검증.

feat/step2-06-paper-broker-wiring:
- `tick()` 이 RUNNING 상태일 때만 `paper_tick_handler` 호출 → 신규 가상
  주문 후보 생성.
- PAUSED / STOPPED / EMERGENCY_STOP 상태에서는 `LoopNotRunningError` 즉시
  raise → handler 호출 0건 → 신규 후보 0건.
- 본 loop 모듈은 broker / OrderExecutor / route_order 를 *어떤 경로로도*
  import / 호출 0건 (기존 정적 grep 가드 유지).
- 실제 VirtualOrder ledger 호출까지 wiring 한 통합 테스트 — sqlite in-memory.
"""

from __future__ import annotations

import inspect
import logging
import re
import threading
from pathlib import Path
from typing import Any

import pytest

from app.auto_paper.loop import (
    AutoPaperLoop,
    AutoPaperState,
    LoopNotRunningError,
    PaperTickContext,
    get_auto_paper_loop,
)


@pytest.fixture(autouse=True)
def _force_market_open(monkeypatch):
    """feat/step2-market-waiting-mode 도입 후 호환 — 본 파일의 핸들러
    dispatch / VirtualOrder 검증은 *시장 시간 분기 이전* 의 가정 (start →
    RUNNING) 위에서 동작. market_clock 분기 자체는
    `test_auto_paper_market_hours.py` 에서 검증.
    """
    from app.scheduler.market_clock import MarketPhase
    monkeypatch.setattr(
        "app.auto_paper.loop.current_market_phase",
        lambda *args, **kwargs: MarketPhase.OPEN,
    )


# ─────────────────────────────────────────────────────────────────────
# 1. PaperTickContext invariants
# ─────────────────────────────────────────────────────────────────────


class TestPaperTickContext:
    def test_is_paper_only_invariant(self):
        """is_paper_only=False 면 ValueError — paper-only 운영 강제."""
        with pytest.raises(ValueError):
            PaperTickContext(
                cycle_count=1,
                state="RUNNING",
                tick_at="2026-01-01T00:00:00+00:00",
                tick_interval_sec=30.0,
                is_paper_only=False,
            )

    def test_to_dict_carries_all_fields(self):
        ctx = PaperTickContext(
            cycle_count=42,
            state="RUNNING",
            tick_at="2026-01-01T00:00:00+00:00",
            tick_interval_sec=30.0,
        )
        d = ctx.to_dict()
        assert d == {
            "cycle_count":       42,
            "state":             "RUNNING",
            "tick_at":           "2026-01-01T00:00:00+00:00",
            "tick_interval_sec": 30.0,
            "is_paper_only":     True,
        }


# ─────────────────────────────────────────────────────────────────────
# 2. handler 호출 — RUNNING 만 fire, 다른 상태에서 0건
# ─────────────────────────────────────────────────────────────────────


class TestHandlerDispatch:
    def test_handler_called_on_each_tick_in_running(self):
        calls: list[PaperTickContext] = []
        def recorder(ctx: PaperTickContext) -> None:
            calls.append(ctx)

        loop = AutoPaperLoop(paper_tick_handler=recorder)
        loop.start()
        loop.tick()
        loop.tick()
        loop.tick()

        assert len(calls) == 3
        # cycle_count 가 매 tick 마다 증가.
        assert [c.cycle_count for c in calls] == [1, 2, 3]
        # 모든 호출이 RUNNING 상태 + is_paper_only=True.
        for c in calls:
            assert c.state == "RUNNING"
            assert c.is_paper_only is True

    def test_handler_not_called_in_paused_state(self):
        """tick() 가 PAUSED 에서 LoopNotRunningError → handler 호출 0건."""
        calls: list[Any] = []
        def recorder(ctx: PaperTickContext) -> None:
            calls.append(ctx)

        loop = AutoPaperLoop(paper_tick_handler=recorder)
        assert loop.status().state == "PAUSED"
        with pytest.raises(LoopNotRunningError):
            loop.tick()
        assert calls == []

    def test_handler_not_called_in_stopped_state(self):
        calls: list[Any] = []
        def recorder(ctx: PaperTickContext) -> None:
            calls.append(ctx)

        loop = AutoPaperLoop(paper_tick_handler=recorder)
        loop.start()
        loop.stop()
        assert loop.status().state == "STOPPED"
        with pytest.raises(LoopNotRunningError):
            loop.tick()
        assert calls == []

    def test_handler_not_called_in_emergency_stop_state(self):
        calls: list[Any] = []
        def recorder(ctx: PaperTickContext) -> None:
            calls.append(ctx)

        loop = AutoPaperLoop(paper_tick_handler=recorder)
        loop.start()
        loop.emergency_stop()
        assert loop.status().state == "EMERGENCY_STOP"
        with pytest.raises(LoopNotRunningError):
            loop.tick()
        assert calls == []

    def test_handler_not_called_in_emergency_stop_after_pause(self):
        """PAUSED → emergency_stop() → EMERGENCY_STOP → tick() 차단."""
        calls: list[Any] = []
        def recorder(ctx: PaperTickContext) -> None:
            calls.append(ctx)

        loop = AutoPaperLoop(paper_tick_handler=recorder)
        loop.emergency_stop()
        assert loop.status().state == "EMERGENCY_STOP"
        with pytest.raises(LoopNotRunningError):
            loop.tick()
        assert calls == []

    def test_no_handler_means_noop_tick(self):
        """handler 미주입 → tick() 은 cycle 만 증가, handler 호출 0건 (None)."""
        loop = AutoPaperLoop()    # paper_tick_handler=None (default)
        loop.start()
        s = loop.tick()
        assert s.cycle_count == 1
        # 추가 호출도 정상.
        s2 = loop.tick()
        assert s2.cycle_count == 2

    def test_handler_exception_does_not_break_loop(self, caplog):
        """handler 실패는 cycle 무효화하지 않음 — last_error 만 기록."""
        def boom(ctx: PaperTickContext) -> None:
            raise RuntimeError("simulated handler failure")

        loop = AutoPaperLoop(paper_tick_handler=boom)
        loop.start()
        log = logging.getLogger("autotrade.auto_paper")
        prev = log.disabled
        log.disabled = False
        try:
            with caplog.at_level(logging.WARNING, logger="autotrade.auto_paper"):
                s = loop.tick()
        finally:
            log.disabled = prev
        # cycle 은 정상 증가.
        assert s.cycle_count == 1
        # last_error 에 caching.
        s2 = loop.status()
        assert "simulated handler failure" in (s2.last_error or "")
        # log 에 warning emit.
        msgs = " ".join(r.getMessage() for r in caplog.records)
        assert "paper_tick_handler" in msgs

    def test_handler_invoked_outside_lock(self):
        """handler 가 stop() 을 호출해도 dead-lock 0건 — lock 해제 후 호출."""
        loop = AutoPaperLoop(paper_tick_handler=None)
        stops: list[Any] = []

        def stop_inside_handler(ctx: PaperTickContext) -> None:
            # handler 안에서 stop() 호출 — lock 해제 상태여야 성공.
            stops.append(loop.stop())

        loop._paper_tick_handler = stop_inside_handler  # 직접 주입 (테스트만)
        loop.start()
        loop.tick()    # handler 가 stop() 호출 → STOPPED.
        assert len(stops) == 1
        assert loop.status().state == "STOPPED"


# ─────────────────────────────────────────────────────────────────────
# 3. 정적 import 가드 — broker / OrderExecutor / route_order 0건 유지
# ─────────────────────────────────────────────────────────────────────


def _module_src(dotted: str) -> str:
    import importlib
    mod = importlib.import_module(dotted)
    return Path(inspect.getfile(mod)).read_text(encoding="utf-8")


class TestStaticImportGuards:
    """기존 2-01 가드를 *paper handler 도입 이후에도* 유지."""

    @pytest.mark.parametrize("mod_name", [
        "app.auto_paper.loop",
        "app.api.routes_auto_paper",
    ])
    def test_no_broker_or_executor_imports(self, mod_name):
        src = _module_src(mod_name)
        for forbidden in (
            "from app.brokers", "import app.brokers",
            "from app.execution.executor", "from app.execution.order_executor",
            "from app.execution.order_router",
        ):
            assert forbidden not in src, (
                f"{mod_name} contains banned import {forbidden!r}"
            )

    @pytest.mark.parametrize("mod_name", [
        "app.auto_paper.loop",
        "app.api.routes_auto_paper",
    ])
    def test_no_order_execution_call_sites(self, mod_name):
        src = _module_src(mod_name)
        # AST 검사 — docstring / comment 안 매칭 0건.
        import ast
        tree = ast.parse(src)
        forbidden_names = {
            "place_order", "route_order", "OrderExecutor",
            "cancel_order",
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            else:
                continue
            assert name not in forbidden_names, (
                f"{mod_name}: forbidden call {name}() at line {node.lineno}"
            )

    @pytest.mark.parametrize("mod_name", [
        "app.auto_paper.loop",
        "app.api.routes_auto_paper",
    ])
    def test_no_external_api_imports(self, mod_name):
        src = _module_src(mod_name)
        for forbidden in (
            "import anthropic", "from anthropic",
            "import openai", "from openai",
            "import httpx", "from httpx",
            "import requests", "from requests",
        ):
            assert forbidden not in src

    def test_loop_does_not_import_kis_or_mock_broker_concrete(self):
        """KisBrokerAdapter / MockBroker 클래스를 *직접 import* 하지 않는다 —
        handler 주입 패턴을 우회하는 routes 추가 회귀 차단."""
        src = _module_src("app.auto_paper.loop")
        for forbidden in (
            "KisBrokerAdapter", "MockBroker", "BrokerAdapter",
            "from app.brokers.kis", "from app.brokers.mock_broker",
        ):
            assert forbidden not in src

    def test_status_payload_carries_no_secret_shapes(self):
        """to_dict() / status 에 secret 패턴 0건."""
        loop = AutoPaperLoop()
        loop.start()
        loop.tick()
        text = repr(loop.status().to_dict())
        secret_patterns = [
            r"sk-[a-zA-Z0-9]{20,}",
            r"ghp_[A-Za-z0-9]{36,}",
            r"AKIA[0-9A-Z]{16}",
            r"xox[abprs]-[A-Za-z0-9-]{10,}",
        ]
        for pat in secret_patterns:
            assert re.search(pat, text) is None


# ─────────────────────────────────────────────────────────────────────
# 4. 통합 — 실제 VirtualOrder ledger 와 결선 (sqlite in-memory)
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def memory_session():
    """In-memory sqlite + alembic 통과한 새 session."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.models import Base

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


class TestVirtualOrderLedgerWiring:
    """RUNNING 상태일 때만 VirtualOrder row 생성 — STOPPED/EMERGENCY 0건."""

    def test_running_tick_creates_virtual_order_row(self, memory_session):
        from app.virtual import order_ledger
        from app.db.models import VirtualOrder

        def virtual_handler(ctx: PaperTickContext) -> None:
            # *Paper-only* handler — VirtualOrder ledger 만 사용, broker 호출 0건.
            order_ledger.create_order(
                memory_session,
                symbol="005930",
                side="BUY",
                quantity=1,
                order_type="MARKET",
                strategy="auto_paper_test",
                mode="SIMULATION",
            )
            memory_session.commit()

        loop = AutoPaperLoop(paper_tick_handler=virtual_handler)
        loop.start()
        loop.tick()
        loop.tick()

        # VirtualOrder 가 2 건 생성 — RUNNING tick 마다 1 건.
        rows = memory_session.query(VirtualOrder).all()
        assert len(rows) == 2
        for r in rows:
            assert r.symbol == "005930"
            assert r.side == "BUY"
            assert r.mode == "SIMULATION"

    def test_paused_emergency_stopped_no_virtual_order_row(self, memory_session):
        """state != RUNNING 에서 tick() 호출 시 VirtualOrder 0건."""
        from app.virtual import order_ledger
        from app.db.models import VirtualOrder

        def virtual_handler(ctx: PaperTickContext) -> None:
            order_ledger.create_order(
                memory_session,
                symbol="005930",
                side="BUY",
                quantity=1,
                order_type="MARKET",
                strategy="auto_paper_test",
                mode="SIMULATION",
            )
            memory_session.commit()

        # 1) PAUSED 에서 tick → LoopNotRunningError + 행 0건.
        loop = AutoPaperLoop(paper_tick_handler=virtual_handler)
        assert loop.status().state == "PAUSED"
        with pytest.raises(LoopNotRunningError):
            loop.tick()

        # 2) STOPPED 에서 tick → 동일.
        loop.start()
        loop.stop()
        with pytest.raises(LoopNotRunningError):
            loop.tick()

        # 3) EMERGENCY_STOP 에서 tick → 동일.
        loop.emergency_stop()
        with pytest.raises(LoopNotRunningError):
            loop.tick()

        # VirtualOrder 행은 *0 건*.
        rows = memory_session.query(VirtualOrder).all()
        assert rows == []


# ─────────────────────────────────────────────────────────────────────
# 5. paper-only handler 우회 시 broker.place_order 호출 시도 0건 검증
# ─────────────────────────────────────────────────────────────────────


class TestNoRealBrokerCallEvenIfHandlerTries:
    """handler 가 *시도* 하더라도 loop 자체는 broker 를 import / 노출하지 않는다."""

    def test_loop_attributes_no_broker_reference(self):
        loop = AutoPaperLoop()
        # 인스턴스 속성에 broker 관련 reference 0건.
        for attr in vars(loop):
            assert "broker" not in attr.lower()
            assert "executor" not in attr.lower()
            assert "router" not in attr.lower()

    def test_loop_module_globals_no_broker_reference(self):
        import app.auto_paper.loop as loop_mod
        for name in dir(loop_mod):
            if name.startswith("_"):
                continue
            # 본 모듈은 broker / OrderExecutor / route_order *식별자* 0건.
            # (PaperTickHandler / PaperTickContext / PreMarketSummary 등 신규는 OK.)
            lower = name.lower()
            assert "broker" not in lower, f"unexpected broker reference: {name}"
            assert "orderexecutor" not in lower
            assert "routeorder" not in lower
            assert "route_order" not in lower
