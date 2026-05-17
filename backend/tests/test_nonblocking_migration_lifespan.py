"""Lifespan + /health + /api/status during background migration.

fix/desktop-nonblocking-migration-health: 데스크톱 EXE 흐름에서 alembic
migration 이 background thread 로 실행되는 동안 `/health` 와 `/api/status`
가 즉시 200 응답하는지 검증.

전략: `app.main.apply_migrations` 를 patch 로 *느린* 함수로 swap → TestClient
lifespan 진입 → migration 이 RUNNING 상태일 때 /health 와 /api/status 가
응답하는지 확인.

⚠ TestClient(app) 의 lifespan 은 *프로세스 당 1회* 만 동작. 본 모듈은 *각
테스트마다* 새 FastAPI app 인스턴스를 만들어 격리한다 (importlib.reload).
"""

from __future__ import annotations

import importlib
import logging
import threading
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _ensure_migration_logger_enabled():
    """앞선 다른 test 모듈이 alembic 을 실행했다면 fileConfig 가 본 logger 를
    disabled=True 로 만든다 — caplog 가 record 를 받기 위해 강제 enable."""
    log = logging.getLogger("autotrade.migration")
    prev_disabled = log.disabled
    log.disabled = False
    yield
    log.disabled = prev_disabled


def _make_app_with(migration_nonblocking: bool, fake_apply):
    """`MIGRATION_NONBLOCKING` env + 가짜 apply 를 set 한 후 app.main 을
    reload 해 새 FastAPI 인스턴스 반환."""
    import os
    os.environ["MIGRATION_NONBLOCKING"] = "true" if migration_nonblocking else "false"
    # Settings lru_cache 무효화 — env 변경 반영.
    from app.core import config as _config
    _config.get_settings.cache_clear()  # type: ignore[attr-defined]

    import app.db.migration_runner as runner
    runner.reset_status_for_tests()

    import app.main as main_module
    importlib.reload(main_module)
    # main_module 은 import 시점에 `apply_migrations` 를 from import 함 — patch
    # 는 main 모듈의 reference 를 swap 해야 lifespan 이 fake 를 호출.
    main_module.apply_migrations = fake_apply  # type: ignore[assignment]
    return main_module


# ── 1. /health 는 migration 진행 중에도 200 응답 ──────────────────────────


def test_health_returns_200_while_migration_running():
    """non-blocking 모드 + 느린 apply → lifespan 이 즉시 yield → /health 200."""
    barrier = threading.Event()

    def slow_apply():
        barrier.wait(timeout=10)

    main_module = _make_app_with(migration_nonblocking=True, fake_apply=slow_apply)

    try:
        with TestClient(main_module.app) as client:
            # lifespan 이 즉시 yield 했으므로 즉시 응답이 와야 함.
            resp = client.get("/health")
            assert resp.status_code == 200
            body = resp.json()
            assert body["ok"] is True
            assert body["status"] == "ok"
            # migration 은 아직 진행 중.
            assert body["db_ready"] is False
            assert body["migration_status"] in ("pending", "running")
    finally:
        barrier.set()


# ── 2. /api/status 는 migration 중 db_ready=false ─────────────────────────


def test_api_status_reports_db_ready_false_during_migration():
    barrier = threading.Event()

    def slow_apply():
        barrier.wait(timeout=10)

    main_module = _make_app_with(migration_nonblocking=True, fake_apply=slow_apply)

    try:
        with TestClient(main_module.app) as client:
            resp = client.get("/api/status")
            assert resp.status_code == 200
            body = resp.json()
            assert body["db_ready"] is False
            assert body["migration_status"] in ("pending", "running")
            # safety flag 매트릭스는 그대로 노출.
            assert "safety_flags" in body
            assert body["safety_flags"]["kis_is_paper"] is True
            assert body["safety_flags"]["enable_live_trading"] is False
    finally:
        barrier.set()


# ── 3. migration 완료 후 db_ready=true ───────────────────────────────────


def test_api_status_reports_db_ready_true_after_migration_complete():
    """fast apply → migration 곧 완료 → /api/status 가 db_ready=true."""
    def fast_apply():
        pass  # no-op = 즉시 종료

    main_module = _make_app_with(migration_nonblocking=True, fake_apply=fast_apply)

    with TestClient(main_module.app) as client:
        # migration thread 가 끝나길 기다림 — get_migration_status 가 COMPLETED
        # 가 될 때까지 polling.
        import app.db.migration_runner as runner
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if runner.db_is_ready():
                break
            time.sleep(0.02)
        assert runner.db_is_ready() is True

        resp = client.get("/api/status")
        body = resp.json()
        assert body["db_ready"] is True
        assert body["migration_status"] == "completed"
        assert body["migration_duration_seconds"] is not None


# ── 4. migration 실패 시 traceback 로그 + db_ready 영원히 false ───────────


def test_migration_failure_logs_traceback_and_status_reports_error(caplog):
    def boom():
        raise RuntimeError("simulated alembic OperationalError for test")

    main_module = _make_app_with(migration_nonblocking=True, fake_apply=boom)

    with caplog.at_level(logging.ERROR, logger="autotrade.migration"):
        with TestClient(main_module.app) as client:
            # thread 가 raise → FAILED 로 진입할 때까지 polling.
            import app.db.migration_runner as runner
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if runner.get_migration_status().state.value == "failed":
                    break
                time.sleep(0.02)

            assert runner.get_migration_status().state.value == "failed"
            resp = client.get("/api/status")
            body = resp.json()
            assert body["db_ready"] is False
            assert body["migration_status"] == "failed"
            assert body["migration_error_type"] == "RuntimeError"
            assert "simulated alembic OperationalError" in body["migration_error_summary"]

    # traceback 이 log 에 — exc_info 가 set 된 record 존재.
    has_traceback = any(
        r.exc_info is not None and r.levelno >= logging.ERROR
        for r in caplog.records
    )
    assert has_traceback, "FAILED path must log full traceback (exc_info=True)"


# ── 5. blocking 모드 (default) 는 기존 동기 동작 유지 ─────────────────────


def test_blocking_mode_runs_migration_before_yielding():
    """`MIGRATION_NONBLOCKING=false` (default) → lifespan 이 migration 완료 후
    yield. TestClient 진입 시점에는 이미 db_ready=true."""
    call_log = []
    def tracking_apply():
        call_log.append("apply-started")
        # 미세한 sleep 으로 비동기 effect 가 있는지 확인 가능.
        time.sleep(0.05)
        call_log.append("apply-finished")

    main_module = _make_app_with(migration_nonblocking=False, fake_apply=tracking_apply)

    with TestClient(main_module.app) as client:
        # lifespan 이 blocking 했으므로 apply 가 *완전히* 끝난 상태.
        assert call_log == ["apply-started", "apply-finished"]
        resp = client.get("/api/status")
        body = resp.json()
        assert body["db_ready"] is True
        assert body["migration_status"] == "completed"


def test_blocking_mode_raises_runtime_error_on_failure():
    """`MIGRATION_NONBLOCKING=false` 에서 apply 가 실패하면 lifespan startup 이
    `RuntimeError` 로 escalate — TestClient context manager 가 raise."""
    def boom():
        raise RuntimeError("blocking-mode failure test")

    main_module = _make_app_with(migration_nonblocking=False, fake_apply=boom)

    with pytest.raises(RuntimeError):
        with TestClient(main_module.app):
            pass


# ── 6. Secret 노출 0건 검증 ────────────────────────────────────────────


def test_api_status_response_does_not_leak_url_secret():
    """apply 가 URL credential 포함 예외를 raise 해도 /api/status 응답에는
    redact 적용 — JSON body 에 원문 0건."""
    fake_secret = "FAKE-PASS-DO-NOT-LEAK-987654321"
    def boom():
        raise RuntimeError(
            f"connection refused postgres://admin:{fake_secret}@localhost/db"
        )

    main_module = _make_app_with(migration_nonblocking=True, fake_apply=boom)

    with TestClient(main_module.app) as client:
        import app.db.migration_runner as runner
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if runner.get_migration_status().state.value == "failed":
                break
            time.sleep(0.02)
        resp = client.get("/api/status")
        raw_body = resp.text
        assert fake_secret not in raw_body, \
            f"raw secret leaked into /api/status response: {raw_body[:300]}"
        body = resp.json()
        assert fake_secret not in (body.get("migration_error_summary") or "")
