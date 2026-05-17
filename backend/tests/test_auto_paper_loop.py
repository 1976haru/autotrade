"""AI Paper Auto Loop service + API 테스트.

feat/step2-01-auto-paper-states: 체크리스트 표준 상태 (PAUSED / RUNNING /
STOPPED / EMERGENCY_STOP) 정렬. 레거시 IDLE / EMERGENCY 는 *member alias* 로
보존.

CLAUDE.md invariant 강제:
- broker / OrderExecutor / route_order import 0건 (정적 grep)
- AutoPaperStatus.is_order_signal=False / auto_apply_allowed=False / forced_paper=True 불변
- start / tick / stop / emergency-stop 어떤 경로도 broker.place_order 호출 0건
- EMERGENCY_STOP 상태에서 start() 차단 (자동 재시작 우회 금지)
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
    LoopBlockedError,
    LoopNotRunningError,
    get_auto_paper_loop,
)
from app.main import app


@pytest.fixture(autouse=True)
def _force_market_open(monkeypatch):
    """feat/step2-market-waiting-mode 도입 후 호환 — 본 파일의 모든 테스트는
    *시장 시간 분기 이전* 의 동작 (start() → RUNNING) 을 가정한다. 따라서
    market_clock 을 OPEN 으로 강제. 시장 시간 분기 자체의 검증은
    `test_auto_paper_market_hours.py` 가 담당.
    """
    from app.scheduler.market_clock import MarketPhase
    monkeypatch.setattr(
        "app.auto_paper.loop.current_market_phase",
        lambda *args, **kwargs: MarketPhase.OPEN,
    )


class TestAutoPaperStatusInvariants:
    def test_is_order_signal_false_invariant(self):
        with pytest.raises(ValueError):
            AutoPaperStatus(
                state="PAUSED", cycle_count=0,
                last_tick_at=None, started_at=None, stopped_at=None,
                emergency_at=None, last_error=None, tick_interval_sec=30.0,
                forced_paper=True,
                is_order_signal=True,
            )

    def test_auto_apply_allowed_false_invariant(self):
        with pytest.raises(ValueError):
            AutoPaperStatus(
                state="PAUSED", cycle_count=0,
                last_tick_at=None, started_at=None, stopped_at=None,
                emergency_at=None, last_error=None, tick_interval_sec=30.0,
                forced_paper=True,
                auto_apply_allowed=True,
            )

    def test_forced_paper_must_be_true(self):
        with pytest.raises(ValueError):
            AutoPaperStatus(
                state="PAUSED", cycle_count=0,
                last_tick_at=None, started_at=None, stopped_at=None,
                emergency_at=None, last_error=None, tick_interval_sec=30.0,
                forced_paper=False,
            )


def _fresh_loop() -> AutoPaperLoop:
    get_auto_paper_loop.cache_clear()
    return get_auto_paper_loop()


# ─────────────────────────────────────────────────────────────────────
# 1. 체크리스트 표준 4 상태 + 레거시 alias
# ─────────────────────────────────────────────────────────────────────


class TestAutoPaperStateEnum:
    def test_canonical_state_values(self):
        """체크리스트 표준 4 상태."""
        assert AutoPaperState.PAUSED.value == "PAUSED"
        assert AutoPaperState.RUNNING.value == "RUNNING"
        assert AutoPaperState.STOPPED.value == "STOPPED"
        assert AutoPaperState.EMERGENCY_STOP.value == "EMERGENCY_STOP"

    def test_six_canonical_states_lock(self):
        """#2-01 표준 모델 — 정확히 6 개 canonical state 가 존재한다.

        새 state 추가 / 삭제는 본 테스트 + state machine 다이어그램 +
        frontend Korean 라벨 + `AutoPaperLoopCard.test.jsx` 6-state 라벨 lock
        *동시* 갱신 PR 외에서는 금지.
        """
        canonical_values = {
            AutoPaperState.PAUSED.value,
            AutoPaperState.WAITING_MARKET.value,
            AutoPaperState.RUNNING.value,
            AutoPaperState.STOPPED.value,
            AutoPaperState.EMERGENCY_STOP.value,
            AutoPaperState.MARKET_CLOSED.value,
        }
        assert canonical_values == {
            "PAUSED", "WAITING_MARKET", "RUNNING", "STOPPED",
            "EMERGENCY_STOP", "MARKET_CLOSED",
        }
        # member 수 = 6 canonical + 2 alias (IDLE, EMERGENCY) = 8 member.
        # 단 set(AutoPaperState) 는 alias 를 제외한 6 개만 반환 (StrEnum 동작).
        unique_members = set(AutoPaperState)
        assert len(unique_members) == 6, (
            f"expected 6 canonical states, got {len(unique_members)}: "
            f"{sorted(s.value for s in unique_members)}"
        )

    def test_two_legacy_aliases_lock(self):
        """#2-01 표준 모델 — 정확히 2 deprecated alias 가 존재한다.

        IDLE → PAUSED, EMERGENCY → EMERGENCY_STOP. 새 alias 추가는 별도
        옵트인 PR + 본 lock 테스트 갱신 필요.

        Python StrEnum 의 `__members__` 는 canonical + alias 모두 포함하며,
        alias 는 canonical member 와 *동일 instance* — `__members__["IDLE"]
        is __members__["PAUSED"]`.
        """
        # IDLE → PAUSED.
        assert AutoPaperState.IDLE is AutoPaperState.PAUSED
        # EMERGENCY → EMERGENCY_STOP.
        assert AutoPaperState.EMERGENCY is AutoPaperState.EMERGENCY_STOP

        canonical_names = {"PAUSED", "WAITING_MARKET", "RUNNING", "STOPPED",
                           "EMERGENCY_STOP", "MARKET_CLOSED"}
        legacy_alias_names = {"IDLE", "EMERGENCY"}
        # __members__ 는 canonical + alias 모두 등록 (8개).
        all_member_names = set(AutoPaperState.__members__.keys())
        # canonical + alias *외* 의 member 가 등록되면 본 어설션이 실패.
        assert all_member_names == canonical_names | legacy_alias_names, (
            f"unexpected enum members. "
            f"extras: {sorted(all_member_names - (canonical_names | legacy_alias_names))}, "
            f"missing: {sorted((canonical_names | legacy_alias_names) - all_member_names)}"
        )
        # alias 가 canonical 과 *동일 instance* — value 동일 + identity 동일.
        assert AutoPaperState.__members__["IDLE"] is AutoPaperState.__members__["PAUSED"]
        assert AutoPaperState.__members__["EMERGENCY"] is AutoPaperState.__members__["EMERGENCY_STOP"]

    def test_legacy_idle_is_alias_for_paused(self):
        """기존 코드가 AutoPaperState.IDLE 을 import 해도 동작 — alias."""
        assert AutoPaperState.IDLE is AutoPaperState.PAUSED
        assert AutoPaperState.IDLE.value == "PAUSED"

    def test_legacy_emergency_is_alias_for_emergency_stop(self):
        """EMERGENCY → EMERGENCY_STOP alias."""
        assert AutoPaperState.EMERGENCY is AutoPaperState.EMERGENCY_STOP
        assert AutoPaperState.EMERGENCY.value == "EMERGENCY_STOP"

    def test_status_payload_emits_canonical_strings_only(self):
        """API 응답 / to_dict 는 canonical 만 emit — 레거시 'IDLE' / 'EMERGENCY' 0건."""
        loop = _fresh_loop()
        d = loop.status().to_dict()
        # 초기 상태 = PAUSED.
        assert d["state"] == "PAUSED"
        loop.start()
        loop.emergency_stop()
        d2 = loop.status().to_dict()
        assert d2["state"] == "EMERGENCY_STOP"
        # 레거시 문자열 emit 0건.
        all_values = " ".join(str(v) for v in d2.values())
        assert "IDLE" not in all_values
        assert (
            "EMERGENCY_STOP" in all_values
            and " EMERGENCY " not in all_values   # bare "EMERGENCY" 단독 emit 안 함
        )


# ─────────────────────────────────────────────────────────────────────
# 2. 기본 service 동작 (기존)
# ─────────────────────────────────────────────────────────────────────


class TestAutoPaperLoopService:
    def test_initial_state_is_paused(self):
        loop = _fresh_loop()
        s = loop.status()
        assert s.state == "PAUSED"
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
        assert s.cycle_count == 1
        s2 = loop.tick()
        assert s2.cycle_count == 2

    def test_emergency_stop_from_any_state(self):
        loop = _fresh_loop()
        s = loop.emergency_stop()
        assert s.state == "EMERGENCY_STOP"
        s2 = loop.emergency_stop()
        assert s2.state == "EMERGENCY_STOP"

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

    def test_reset_returns_to_paused(self):
        loop = _fresh_loop()
        loop.emergency_stop()
        s = loop.reset()
        assert s.state == "PAUSED"

    def test_status_snapshot_has_no_secrets(self):
        loop = _fresh_loop()
        loop.start()
        loop.tick()
        d = loop.status().to_dict()
        for forbidden in (
            "api_key", "secret", "password", "token",
            "kis_app_key", "kis_app_secret", "kis_account_no",
            "anthropic", "openai", "telegram",
        ):
            for key in d.keys():
                assert forbidden not in str(key).lower()


# ─────────────────────────────────────────────────────────────────────
# 3. 필수 전이 매트릭스 (체크리스트 요구)
# ─────────────────────────────────────────────────────────────────────


class TestRequiredTransitions:
    """체크리스트 6 필수 전이 + EMERGENCY_STOP block."""

    def test_paused_to_running(self):
        """PAUSED → RUNNING (초기 시작)."""
        loop = _fresh_loop()
        assert loop.status().state == "PAUSED"
        loop.start()
        assert loop.status().state == "RUNNING"

    def test_running_to_stopped(self):
        """RUNNING → STOPPED."""
        loop = _fresh_loop()
        loop.start()
        loop.stop()
        assert loop.status().state == "STOPPED"

    def test_running_to_emergency_stop(self):
        """RUNNING → EMERGENCY_STOP."""
        loop = _fresh_loop()
        loop.start()
        loop.emergency_stop()
        assert loop.status().state == "EMERGENCY_STOP"

    def test_stopped_to_running(self):
        """STOPPED → RUNNING (재시작)."""
        loop = _fresh_loop()
        loop.start()
        loop.stop()
        loop.start()
        assert loop.status().state == "RUNNING"

    def test_paused_to_emergency_stop(self):
        """PAUSED → EMERGENCY_STOP (시작 전 긴급정지)."""
        loop = _fresh_loop()
        assert loop.status().state == "PAUSED"
        loop.emergency_stop()
        assert loop.status().state == "EMERGENCY_STOP"

    def test_emergency_stop_blocks_start(self):
        """EMERGENCY_STOP 상태에서 start() 차단 — LoopBlockedError."""
        loop = _fresh_loop()
        loop.emergency_stop()
        assert loop.status().state == "EMERGENCY_STOP"
        with pytest.raises(LoopBlockedError):
            loop.start()
        # 차단 후에도 상태는 그대로.
        assert loop.status().state == "EMERGENCY_STOP"

    def test_emergency_stop_reset_then_start(self):
        """EMERGENCY_STOP → reset() → PAUSED → start() → RUNNING.
        운영자 명시 reset() 만 우회 가능."""
        loop = _fresh_loop()
        loop.emergency_stop()
        loop.reset()
        assert loop.status().state == "PAUSED"
        loop.start()
        assert loop.status().state == "RUNNING"

    def test_running_emergency_then_blocked_start(self):
        """전체 흐름: RUNNING → EMERGENCY_STOP → start() 차단."""
        loop = _fresh_loop()
        loop.start()
        loop.emergency_stop()
        with pytest.raises(LoopBlockedError):
            loop.start()


# ─────────────────────────────────────────────────────────────────────
# 4. API 통합
# ─────────────────────────────────────────────────────────────────────


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
        body_str = str(body).lower()
        for forbidden in ("api_key", "kis_app_secret", "anthropic_api_key",
                          "telegram_bot_token", "계좌번호"):
            assert forbidden not in body_str

    def test_get_status(self, client):
        r = client.get("/api/auto-paper/status")
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "PAUSED"
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
        assert r.json()["state"] == "EMERGENCY_STOP"
        r2 = client.post("/api/auto-paper/emergency-stop")
        assert r2.status_code == 200

    def test_reset_returns_paused(self, client):
        client.post("/api/auto-paper/emergency-stop")
        r = client.post("/api/auto-paper/reset")
        assert r.json()["state"] == "PAUSED"

    def test_emergency_stop_blocks_start_via_api(self, client):
        """EMERGENCY_STOP 상태에서 POST /start 가 409 — 자동 재시작 우회 차단."""
        client.post("/api/auto-paper/emergency-stop")
        r = client.post("/api/auto-paper/start")
        # FastAPI 가 LoopBlockedError 를 LoopAlreadyRunningError 와 같은 409 로
        # 변환할 수 있도록 routes_auto_paper.py 가 catch — 본 테스트가 회귀 lock.
        assert r.status_code == 409
        # 상태는 EMERGENCY_STOP 그대로.
        r2 = client.get("/api/auto-paper/status")
        assert r2.json()["state"] == "EMERGENCY_STOP"


# ─────────────────────────────────────────────────────────────────────
# 5. 정적 import guard (broker / 외부 API 호출 0건)
# ─────────────────────────────────────────────────────────────────────


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
            "from app.brokers", "import app.brokers",
            "from app.execution.executor", "from app.execution.order_router",
        ):
            assert forbidden not in src

    @pytest.mark.parametrize("mod_name", [
        "app.auto_paper.loop",
        "app.api.routes_auto_paper",
    ])
    def test_no_order_execution_calls(self, mod_name):
        src = self._read(mod_name)
        for forbidden in (
            "broker.place_order(", "route_order(", "submit_candidate(",
            ".place_order(", ".cancel_order(",
        ):
            assert forbidden not in src

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
            assert forbidden not in src
