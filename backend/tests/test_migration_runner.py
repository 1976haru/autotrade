"""Background migration runner unit tests.

fix/desktop-nonblocking-migration-health: `apply_migrations` 를 background
thread 로 실행하면서 `/health` 와 `/api/status` 가 첫 응답부터 200 응답하도록
하는 runner. 본 테스트는 runner *자체* 의 상태 머신 / Secret redact / traceback
logging 만 검증 — TestClient / lifespan 통합 테스트는 별도 파일에서.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from app.db.migration_runner import (
    MigrationState,
    MigrationStatus,
    _redact_error_message,
    db_is_ready,
    get_migration_status,
    reset_status_for_tests,
    run_migration_blocking,
    start_migration_in_background,
)


@pytest.fixture(autouse=True)
def _reset_runner():
    reset_status_for_tests()
    yield
    reset_status_for_tests()


@pytest.fixture(autouse=True)
def _ensure_migration_logger_enabled():
    """앞선 다른 test 모듈이 alembic 을 실행했다면 `alembic.ini` 의 fileConfig
    가 *disable_existing_loggers=True* default 로 `autotrade.migration` 로거
    를 disabled=True 로 만든다. 본 fixture 는 caplog 가 record 를 받을 수
    있도록 명시적으로 enable.
    """
    log = logging.getLogger("autotrade.migration")
    prev_disabled = log.disabled
    log.disabled = False
    yield
    log.disabled = prev_disabled


# ── 1. PENDING → RUNNING → COMPLETED happy path ──────────────────────────


def test_initial_state_is_pending():
    s = get_migration_status()
    assert s.state == MigrationState.PENDING
    assert s.started_at is None
    assert s.completed_at is None
    assert s.duration_seconds is None
    assert s.error_type is None
    assert db_is_ready() is False


def test_run_migration_blocking_completes_successfully():
    calls = []
    def fake_apply():
        calls.append("called")

    run_migration_blocking(fake_apply)

    assert calls == ["called"]
    s = get_migration_status()
    assert s.state == MigrationState.COMPLETED
    assert s.started_at is not None
    assert s.completed_at is not None
    assert s.duration_seconds is not None and s.duration_seconds >= 0
    assert s.error_type is None
    assert s.error_summary is None
    assert db_is_ready() is True


def test_to_dict_excludes_internal_fields():
    """`MigrationStatus.to_dict()` 는 응답 본문용 — slow_warning_emitted 같은
    내부 flag 를 노출하지 않는다."""
    st = MigrationStatus(
        state=MigrationState.COMPLETED,
        started_at="2026-05-17T00:00:00+00:00",
        completed_at="2026-05-17T00:00:01+00:00",
        duration_seconds=1.0,
    )
    d = st.to_dict()
    assert "slow_warning_emitted" not in d
    assert d["state"] == "completed"


# ── 2. RUNNING 중 상태 노출 ────────────────────────────────────────────────


def test_background_thread_exposes_running_state():
    """background thread 가 apply 를 실행 중일 때 get_migration_status() 가
    RUNNING 을 반환 — `/api/status` 가 db_ready=false 를 보고할 수 있는
    근거."""
    barrier = threading.Event()

    def slow_apply():
        barrier.wait(timeout=5)

    th = start_migration_in_background(slow_apply)

    # apply 가 시작되어 RUNNING 으로 진입할 때까지 잠깐 양보 — flakiness
    # 방지를 위해 polling.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if get_migration_status().state == MigrationState.RUNNING:
            break
        time.sleep(0.01)

    s = get_migration_status()
    assert s.state == MigrationState.RUNNING
    assert s.started_at is not None
    assert s.completed_at is None
    assert db_is_ready() is False

    # apply 종료 → thread join → COMPLETED.
    barrier.set()
    th.join(timeout=5)
    s = get_migration_status()
    assert s.state == MigrationState.COMPLETED
    assert db_is_ready() is True


# ── 3. FAILED + traceback log ────────────────────────────────────────────


def test_run_migration_blocking_records_failure(caplog):
    """예외 발생 시 status=FAILED + error_type / error_summary 채워짐 +
    full traceback 이 log 에 (exc_info=True)."""
    def boom():
        raise RuntimeError("simulated alembic OperationalError detail")

    with caplog.at_level(logging.ERROR, logger="autotrade.migration"):
        run_migration_blocking(boom)

    s = get_migration_status()
    assert s.state == MigrationState.FAILED
    assert s.error_type == "RuntimeError"
    assert "simulated alembic OperationalError detail" in s.error_summary
    assert s.completed_at is not None
    assert s.duration_seconds is not None
    assert db_is_ready() is False

    # log 에 traceback 포함 — caplog 의 record 에 exc_info 가 set.
    has_traceback_record = any(
        r.exc_info is not None and r.levelno >= logging.ERROR
        for r in caplog.records
    )
    assert has_traceback_record, "failure path must log with exc_info=True"


def test_run_migration_blocking_does_not_raise():
    """실패해도 caller 에게 raise 하지 않는다 — lifespan 이 죽지 않도록."""
    def boom():
        raise RuntimeError("x")

    # raise 가 발생하면 본 line 에서 예외가 propagate 됨. propagate 0건 검증.
    run_migration_blocking(boom)
    assert get_migration_status().state == MigrationState.FAILED


# ── 4. Secret redact ────────────────────────────────────────────────────


def test_redact_url_credentials_in_error_message():
    """SQLAlchemy 가 connection URL 의 user:password 를 메시지에 포함시켜도
    redact 된다."""
    class FakeExc(Exception):
        pass

    exc = FakeExc(
        "could not connect to postgresql://operator:s3cr3t_p@ss@db.internal:5432/autotrade"
    )
    redacted = _redact_error_message(exc)
    assert "s3cr3t_p@ss" not in redacted
    assert "operator:" not in redacted
    assert "[REDACTED]" in redacted


def test_error_summary_does_not_leak_secret_when_apply_raises_with_url(caplog):
    """run_migration_blocking 통합 검증 — apply 가 URL 포함 예외를 raise 해도
    /api/status 가 노출하는 error_summary 에는 redact 적용."""
    fake_secret = "FAKE-SUPER-SECRET-DO-NOT-LOG"

    def boom_with_url():
        raise RuntimeError(
            f"OperationalError (connection refused) "
            f"postgres://admin:{fake_secret}@127.0.0.1:5432/db"
        )

    with caplog.at_level(logging.ERROR, logger="autotrade.migration"):
        run_migration_blocking(boom_with_url)

    s = get_migration_status()
    assert s.state == MigrationState.FAILED
    assert fake_secret not in s.error_summary
    assert "[REDACTED]" in s.error_summary


def test_error_summary_truncated_to_200_chars():
    """매우 긴 예외 메시지는 200 char 이내로 잘려 응답이 거대해지지 않는다."""
    long_msg = "X" * 500
    class _E(Exception):
        pass
    redacted = _redact_error_message(_E(long_msg))
    assert len(redacted) <= 200


def test_error_summary_takes_first_line_only():
    """multi-line traceback 의 *첫 줄만* error_summary 로 — 응답이 줄바꿈
    문자로 깨지지 않도록."""
    class _E(Exception):
        pass
    redacted = _redact_error_message(_E("first line\nsecond line\nthird"))
    assert redacted == "first line"


# ── 5. Idempotency / start_migration_in_background ────────────────────────


def test_start_migration_in_background_is_idempotent():
    """이미 살아있는 thread 가 있으면 동일 reference 반환 — 중복 spawn 0건."""
    barrier = threading.Event()
    def slow_apply():
        barrier.wait(timeout=5)

    th1 = start_migration_in_background(slow_apply)
    th2 = start_migration_in_background(slow_apply)
    assert th1 is th2
    barrier.set()
    th1.join(timeout=5)


# ── 6. db_is_ready ──────────────────────────────────────────────────────


def test_db_is_ready_is_true_only_when_completed():
    """db_is_ready 는 state==COMPLETED 일 때만 True — RUNNING / FAILED /
    PENDING / SKIPPED 는 False."""
    reset_status_for_tests()
    assert db_is_ready() is False  # PENDING

    run_migration_blocking(lambda: None)
    assert db_is_ready() is True  # COMPLETED

    reset_status_for_tests()
    def boom():
        raise ValueError("nope")
    run_migration_blocking(boom)
    assert db_is_ready() is False  # FAILED


# ── 7. get_migration_status returns immutable copy ───────────────────────


def test_get_migration_status_returns_copy():
    """반환된 status 를 mutate 해도 내부 singleton 은 그대로 — race condition
    /외부 mutation 차단."""
    run_migration_blocking(lambda: None)
    s1 = get_migration_status()
    s1.state = MigrationState.PENDING  # 외부 mutation
    s2 = get_migration_status()
    assert s2.state == MigrationState.COMPLETED  # singleton 보존
