"""Backend lifespan startup logging markers (#fix/desktop-backend-startup-readiness).

데스크톱 launcher (`app_desktop_launcher.py`) 가 desktop-backend.log 에서 첫 실행
시 alembic migration 지연을 진단할 수 있도록, `app.main` 의 FastAPI lifespan
은 startup 단계별로 명시 marker 를 emit 해야 한다.

본 테스트는 broker / OrderExecutor / route_order 를 호출하지 *않는다* — startup
phase 의 root logger record 만 검증.

⚠ `alembic.command.upgrade` 는 `alembic.ini` 의 `[loggers]` 섹션을
`logging.config.fileConfig` 로 적용하면서 **`disable_existing_loggers=True`**
default 동작으로 본 테스트의 capture handler 가 무력화된다. 본 테스트는 marker
*emit 자체* 만 검증하면 충분하므로 `apply_migrations` 를 no-op 으로 패치한다 —
실제 alembic 실행은 다른 테스트에서 별도 검증.

⚠ TestClient(app) 의 lifespan 은 *프로세스 당 1회* 만 동작 (FastAPI app 인스턴스
재사용 + lifespan generator one-shot). 따라서 모든 marker 단언을 *단일*
TestClient context 내에서 수집해야 한다 → module-scope fixture 로 공유.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def startup_log_capture() -> dict:
    """단일 TestClient 진입/종료로 startup 단계별 log marker 수집.

    fixture scope=module 로 한 번만 실행 — 모든 test 는 본 captured snapshot
    을 재사용한다. `apply_migrations` 는 patch 로 no-op 화하여 alembic 의
    fileConfig 가 본 handler 를 disable 하지 않도록 한다.
    """
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.name.startswith("autotrade."):
                captured.append(record)

    handler = _Capture()
    handler.setLevel(logging.DEBUG)

    # 직접 named logger 에 attach — basicConfig/fileConfig 가 root.handlers
    # 를 갈아치워도 named logger 의 handler list 는 보존된다.
    target_logger = logging.getLogger("autotrade.startup")
    prev_level = target_logger.level
    prev_disabled = target_logger.disabled
    target_logger.addHandler(handler)
    target_logger.setLevel(logging.INFO)
    # 앞선 다른 test 모듈이 TestClient lifespan 으로 alembic 을 실행했다면
    # alembic.ini 의 fileConfig 가 *disable_existing_loggers=True* default 로
    # autotrade.startup 을 disabled=True 로 설정해 둠. 본 fixture 는 record
    # 를 받기 위해 명시적으로 enable.
    target_logger.disabled = False

    # apply_migrations 를 no-op 으로 — fileConfig 호출 자체를 우회.
    import app.main as main_module
    original_apply = main_module.apply_migrations
    main_module.apply_migrations = lambda: None  # type: ignore[assignment]

    try:
        with TestClient(app) as client:
            health_resp = client.get("/health")
            health_status = health_resp.status_code
            health_body = health_resp.json()
    finally:
        main_module.apply_migrations = original_apply  # type: ignore[assignment]
        target_logger.removeHandler(handler)
        target_logger.setLevel(prev_level)
        target_logger.disabled = prev_disabled

    messages = [r.getMessage() for r in captured]
    return {
        "records":       captured,
        "messages":      messages,
        "joined":        "\n".join(messages),
        "health_status": health_status,
        "health_body":   health_body,
    }


def test_lifespan_emits_alembic_starting_marker(startup_log_capture):
    """첫 marker: alembic migration starting + 1~2분 안내."""
    full = startup_log_capture["joined"]
    assert "alembic migration starting" in full
    # 사용자가 "왜 안 뜨지" 라고 느끼지 않도록 첫 실행 안내가 포함되어야 함.
    assert "1~2분" in full or "1-2분" in full


def test_lifespan_emits_alembic_complete_marker(startup_log_capture):
    assert "alembic migration complete" in startup_log_capture["joined"]


def test_lifespan_emits_backend_ready_marker(startup_log_capture):
    """uvicorn 이 request 수신 가능한 시점을 명시 — frontend 가 health probe
    성공으로 READY 전환할 수 있는 단계."""
    assert "backend ready" in startup_log_capture["joined"]


def test_lifespan_emits_shutdown_marker(startup_log_capture):
    assert "lifespan exit" in startup_log_capture["joined"]


def test_lifespan_markers_in_correct_order(startup_log_capture):
    """starting → complete → backend ready → lifespan exit (시간 순)."""
    msgs = startup_log_capture["messages"]

    def _idx(needle: str) -> int:
        for i, m in enumerate(msgs):
            if needle in m:
                return i
        return -1

    i_starting = _idx("alembic migration starting")
    i_complete = _idx("alembic migration complete")
    i_ready    = _idx("backend ready")
    i_exit     = _idx("lifespan exit")
    assert i_starting >= 0
    assert i_complete > i_starting
    assert i_ready    > i_complete
    assert i_exit     > i_ready


def test_lifespan_markers_emit_no_secrets(startup_log_capture):
    """본 marker 들은 KIS app key / secret / 계좌번호 / Anthropic key 원문을
    포함하지 않는다 — desktop-backend.log 는 운영자가 공유할 수 있어야 한다."""
    full = startup_log_capture["joined"]
    forbidden = [
        "KIS_APP_SECRET=",
        "KIS_APP_KEY=",
        "ANTHROPIC_API_KEY=",
        "OPENAI_API_KEY=",
        # JWT-shaped tokens — 본 marker 에는 절대 안 들어감.
        "Bearer eyJ",
    ]
    for f in forbidden:
        assert f not in full, f"forbidden secret-shaped string in startup log: {f!r}"


def test_health_endpoint_responds_200_after_lifespan(startup_log_capture):
    """`/health` 는 lifespan startup 완료 후 즉시 200 응답.

    데스크톱 환경에서 alembic 이 진행 *중* 일 때 /health 가 응답하지 않는 것은
    FastAPI lifespan 설계이며, frontend 가 90s timeout 동안 polling 으로 대응
    한다 (backendLauncher.test.js).
    """
    assert startup_log_capture["health_status"] == 200
    body = startup_log_capture["health_body"]
    assert body.get("ok") is True
    assert body.get("status") == "ok"
