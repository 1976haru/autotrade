"""AI Paper Auto Loop service + API 테스트.

CLAUDE.md invariant 강제:
- broker / OrderExecutor / route_order import 0건 (정적 grep)
- AutoPaperStatus.is_order_signal=False / auto_apply_allowed=False / forced_paper=True 불변
- start / tick / stop / emergency-stop 어떤 경로도 broker.place_order 호출 0건
- ENABLE_LIVE_TRADING=True 여도 forced_paper 유지
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auto_paper.loop import (
    AutoPaperLoop,
    AutoPaperState,
    AutoPaperStatus,
    LoopAlreadyRunningError,
    LoopNotRunningError,
    get_auto_paper_loop,
)
from app.main import app


# ----------------------------------------------------------------------
# 1. AutoPaperStatus invariants
# ----------------------------------------------------------------------


class TestAutoPaperStatusInvariants:
    def test_is_order_signal_false_invariant(self):
        with pytest.raises(ValueError):
            AutoPaperStatus(
                state="IDLE", cycle_count=0,
                last_tick_at=None, started_at=None, stopped_at=None,
                emergency_at=None, last_error=None, tick_interval_sec=30.0,
                forced_paper=True,
                is_order_signal=True,   # forbidden
            )

    def test_auto_apply_allowed_false_invariant(self):
        with pytest.raises(ValueError):
            AutoPaperStatus(
                state="IDLE", cycle_count=0,
                last_tick_at=None, started_at=None, stopped_at=None,
                emergency_at=None, last_error=None, tick_interval_sec=30.0,
                forced_paper=True,
                auto_apply_allowed=True,   # forbidden
            )

    def test_forced_paper_must_be_true(self):
        with pytest.raises(ValueError):
            AutoPaperStatus(
                state="IDLE", cycle_count=0,
                last_tick_at=None, started_at=None, stopped_at=None,
                emergency_at=None, last_error=None, tick_interval_sec=30.0,
                forced_paper=False,   # forbidden — 실거래 진행 0건 invariant
            )


# ----------------------------------------------------------------------
# 2. AutoPaperLoop service
# ----------------------------------------------------------------------


def _fresh_loop() -> AutoPaperLoop:
    """매 테스트마다 새 인스턴스 — global cache 회피."""
    get_auto_paper_loop.cache_clear()
    return get_auto_paper_loop()


class TestAutoPaperLoopService:
    def test_initial_state_is_idle(self):
        loop = _fresh_loop()
        s = loop.status()
        assert s.state == "IDLE"
        assert s.cycle_count == 0
        assert s.forced_paper is True

    def test_start_transitions_to_running(self):
        loop = _fresh_loop()
        s = loop.start()
        assert s.state == "RUNNING"
        assert s.started_at is not None

    def test_double_start_raises(self):
        loop = _fresh_loop()
        loop.start()
        with pytest.raises(LoopAlreadyRunningError):
            loop.start()

    def test_stop_transitions_to_stopped(self):
        loop = _fresh_loop()
        loop.start()
        s = loop.stop()
        assert s.state == "STOPPED"
        assert s.stopped_at is not None

    def test_stop_when_not_running_raises(self):
        loop = _fresh_loop()
        with pytest.raises(LoopNotRunningError):
            loop.stop()

    def test_tick_only_running(self):
        loop = _fresh_loop()
        with pytest.raises(LoopNotRunningError):
            loop.tick()
        loop.start()
        s = loop.tick()
        assert s.state == "RUNNING"
        assert s.cycle_count == 1
        s2 = loop.tick()
        assert s2.cycle_count == 2

    def test_emergency_stop_from_any_state(self):
        loop = _fresh_loop()
        s = loop.emergency_stop()
        assert s.state == "EMERGENCY"
        # 멱등 — 재호출 OK.
        s2 = loop.emergency_stop()
        assert s2.state == "EMERGENCY"

    def test_emergency_stop_blocks_tick(self):
        loop = _fresh_loop()
        loop.start()
        loop.emergency_stop()
        with pytest.raises(LoopNotRunningError):
            loop.tick()

    def test_restart_after_stop(self):
        loop = _fresh_loop()
        loop.start()
        loop.stop()
        s = loop.start()
        assert s.state == "RUNNING"

    def test_reset_returns_to_idle(self):
        loop = _fresh_loop()
        loop.emergency_stop()
        s = loop.reset()
        assert s.state == "IDLE"

    def test_status_snapshot_has_no_secrets(self):
        loop = _fresh_loop()
        loop.start()
        loop.tick()
        d = loop.status().to_dict()
        # secret-related keys 0건.
        for forbidden in (
            "api_key", "secret", "password", "token",
            "kis_app_key", "kis_app_secret", "kis_account_no",
            "anthropic", "openai", "telegram",
        ):
            for key in d.keys():
                assert forbidden not in str(key).lower()

    def test_forced_paper_in_status(self):
        loop = _fresh_loop()
        s = loop.status()
        assert s.forced_paper is True
        # to_dict 도 carry.
        assert s.to_dict()["forced_paper"] is True


# ----------------------------------------------------------------------
# 3. HTTP API
# ----------------------------------------------------------------------


@pytest.fixture
def client():
    get_auto_paper_loop.cache_clear()
    return TestClient(app)


class TestAutoPaperApi:
    def test_desktop_health(self, client):
        r = client.get("/api/desktop/health")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert "safety_flags" in body
        assert "auto_paper" in body
        # secret 키 0건.
        body_str = str(body).lower()
        for forbidden in ("api_key", "kis_app_secret", "anthropic_api_key",
                          "telegram_bot_token", "계좌번호"):
            assert forbidden not in body_str

    def test_get_status(self, client):
        r = client.get("/api/auto-paper/status")
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "IDLE"
        assert body["forced_paper"] is True
        assert body["is_order_signal"] is False

    def test_start_then_stop_flow(self, client):
        r = client.post("/api/auto-paper/start")
        assert r.status_code == 200
        assert r.json()["state"] == "RUNNING"

        r = client.post("/api/auto-paper/stop")
        assert r.status_code == 200
        assert r.json()["state"] == "STOPPED"

    def test_double_start_returns_409(self, client):
        client.post("/api/auto-paper/start")
        r = client.post("/api/auto-paper/start")
        assert r.status_code == 409

    def test_stop_when_not_running_returns_409(self, client):
        r = client.post("/api/auto-paper/stop")
        assert r.status_code == 409

    def test_emergency_stop_idempotent(self, client):
        r = client.post("/api/auto-paper/emergency-stop")
        assert r.status_code == 200
        assert r.json()["state"] == "EMERGENCY"
        # 재호출 OK.
        r2 = client.post("/api/auto-paper/emergency-stop")
        assert r2.status_code == 200
        assert r2.json()["state"] == "EMERGENCY"

    def test_emergency_stop_after_running(self, client):
        client.post("/api/auto-paper/start")
        r = client.post("/api/auto-paper/emergency-stop")
        assert r.status_code == 200
        assert r.json()["state"] == "EMERGENCY"

    def test_reset_returns_idle(self, client):
        client.post("/api/auto-paper/emergency-stop")
        r = client.post("/api/auto-paper/reset")
        assert r.status_code == 200
        assert r.json()["state"] == "IDLE"


# ----------------------------------------------------------------------
# 4. 정적 import 가드
# ----------------------------------------------------------------------


class TestStaticImportGuards:
    def _read(self, dotted: str) -> str:
        import importlib
        mod = importlib.import_module(dotted)
        path = Path(inspect.getfile(mod))
        return path.read_text(encoding="utf-8")

    @pytest.mark.parametrize("mod_name", [
        "app.auto_paper.loop",
        "app.api.routes_auto_paper",
    ])
    def test_no_broker_imports(self, mod_name):
        src = self._read(mod_name)
        for forbidden in (
            "from app.brokers",
            "import app.brokers",
            "from app.execution.executor",
            "from app.execution.order_router",
        ):
            assert forbidden not in src, (
                f"{mod_name} contains forbidden import {forbidden!r}"
            )

    @pytest.mark.parametrize("mod_name", [
        "app.auto_paper.loop",
        "app.api.routes_auto_paper",
    ])
    def test_no_order_execution_calls(self, mod_name):
        src = self._read(mod_name)
        for forbidden in (
            "broker.place_order(",
            "route_order(",
            "submit_candidate(",
            ".place_order(",
            ".cancel_order(",
        ):
            assert forbidden not in src, (
                f"{mod_name} contains forbidden call {forbidden!r}"
            )

    @pytest.mark.parametrize("mod_name", [
        "app.auto_paper.loop",
        "app.api.routes_auto_paper",
    ])
    def test_no_external_api_imports(self, mod_name):
        src = self._read(mod_name)
        for forbidden in (
            "import anthropic", "from anthropic",
            "import openai", "from openai",
            "import httpx", "from httpx",
            "import requests", "from requests",
        ):
            assert forbidden not in src, (
                f"{mod_name} contains forbidden external import {forbidden!r}"
            )

    def test_loop_module_does_not_reference_orderrequest(self):
        src = self._read("app.auto_paper.loop")
        for forbidden in ("OrderRequest(", ": OrderRequest", "-> OrderRequest"):
            assert forbidden not in src, (
                f"app.auto_paper.loop references {forbidden!r}"
            )
