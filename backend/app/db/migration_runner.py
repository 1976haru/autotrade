"""Background migration runner (fix/desktop-nonblocking-migration-health).

데스크톱 EXE 운영자가 첫 실행 시 alembic migration 으로 1~2분 멈춰 보이는
문제를 해결하기 위한 *상태 머신 + 백그라운드 실행기*.

설계:
  * `apply_migrations()` (in `app.db.session`) 자체는 변경하지 않는다 —
    scripts / CLI 가 *동기적으로* 그대로 호출 가능.
  * 본 모듈은 그 위에 *얇은* runner 를 얹어 `MigrationStatus` singleton 을
    유지한다. lifespan 은 `start_migration_in_background()` 으로 데몬 thread
    를 띄우고 즉시 yield → `/health` 와 `/api/status` 가 첫 응답부터 200.
  * `/api/status` 는 singleton 을 read-only 로 노출 → frontend launcher 가
    `db_ready` / `migration_status` 로 "초기 DB 준비 중" 배너를 그린다.

CLAUDE.md 절대 원칙:
  * broker / OrderExecutor / route_order import 0건.
  * Secret 출력 0건 — exception message 의 URL credential 은 `[REDACTED]`
    로 redact, 그 외 secret-shaped substring 은 별도 검출이 어려워 *truncate*
    (200 char) + 첫 줄만 노출. 전체 traceback 은 `_logger.error(exc_info=True)`
    로 log file 에 기록 — log file 자체는 운영자 PC 의 `%APPDATA%\\Autotrade\\
    logs\\backend-YYYYMMDD.log` 에 위치하며, 응답 본문 / git / artifact 로
    노출되지 않는다.
  * `apply()` callable 은 동기 함수 — alembic.command.upgrade 가 동기이므로
    asyncio task 가 아닌 *threading.Thread* 로 실행. event loop 를 blocking
    하지 않는다.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional

_logger = logging.getLogger("autotrade.migration")

# 60s 이상 RUNNING 이면 1회 warning emit. 본 값은 *경고 임계* 일 뿐 자동
# 중단 / kill 트리거가 아님 — alembic 이 1~2분 걸려도 그대로 진행.
SLOW_WARN_SECONDS = 60.0


class MigrationState(str, Enum):
    PENDING = "pending"        # 아직 시작 안 함
    RUNNING = "running"        # 진행 중 (thread 살아있음)
    COMPLETED = "completed"    # 성공 종료
    FAILED = "failed"          # 예외 종료
    SKIPPED = "skipped"        # 운영자가 명시적으로 skip (현재 미사용)


@dataclass
class MigrationStatus:
    """Snapshot of migration state. *Immutable from caller's perspective* —
    `get_migration_status()` returns a fresh copy each call.

    secret 보호: error_summary 는 *첫 줄 + 200 char truncate* + URL credential
    redact. 전체 traceback 은 log file 에만 존재.
    """

    state: MigrationState = MigrationState.PENDING
    started_at: Optional[str] = None       # ISO 8601 UTC
    completed_at: Optional[str] = None     # ISO 8601 UTC
    duration_seconds: Optional[float] = None
    error_type: Optional[str] = None       # e.g., "OperationalError"
    error_summary: Optional[str] = None    # 1줄, redacted, ≤200 char
    slow_warning_emitted: bool = False     # 60s 경고를 이미 emit 했는지

    def to_dict(self) -> dict:
        return {
            "state":             self.state.value,
            "started_at":        self.started_at,
            "completed_at":      self.completed_at,
            "duration_seconds":  self.duration_seconds,
            "error_type":        self.error_type,
            "error_summary":     self.error_summary,
        }


_lock = threading.RLock()
_status = MigrationStatus()
_thread: Optional[threading.Thread] = None


def get_migration_status() -> MigrationStatus:
    """현재 status 의 *복사본* 반환 — 외부 mutation 차단."""
    with _lock:
        return MigrationStatus(
            state=_status.state,
            started_at=_status.started_at,
            completed_at=_status.completed_at,
            duration_seconds=_status.duration_seconds,
            error_type=_status.error_type,
            error_summary=_status.error_summary,
            slow_warning_emitted=_status.slow_warning_emitted,
        )


def db_is_ready() -> bool:
    """state == COMPLETED 일 때만 True. PENDING / RUNNING / FAILED 모두 False."""
    with _lock:
        return _status.state == MigrationState.COMPLETED


def reset_status_for_tests() -> None:
    """Tests-only — 본 모듈 singleton 을 초기 상태로 reset.

    *프로덕션 코드는 본 함수를 호출하지 않는다* — `_for_tests` suffix 로 명시.
    """
    global _thread
    with _lock:
        _status.state = MigrationState.PENDING
        _status.started_at = None
        _status.completed_at = None
        _status.duration_seconds = None
        _status.error_type = None
        _status.error_summary = None
        _status.slow_warning_emitted = False
    _thread = None


def _redact_error_message(exc: BaseException) -> str:
    """예외 메시지에서 secret 패턴 제거 + 1줄 + 200 char truncate.

    SQLAlchemy 등이 connection URL 의 username:password 를 메시지에 포함할 수
    있어 `scheme://user:pass@host` 패턴을 `scheme://[REDACTED]@host` 로 redact.
    """
    msg = str(exc) or repr(exc)
    first_line = msg.splitlines()[0] if msg else ""
    # URL credential redact — `://user:pass@host` → `://[REDACTED]@host`.
    redacted = re.sub(r"://[^@\s]+@", "://[REDACTED]@", first_line)
    if len(redacted) > 200:
        redacted = redacted[:197] + "..."
    return redacted


def _emit_slow_warning_if_running(start_monotonic: float) -> None:
    """별도 thread 에서 60s 후 RUNNING 이면 1회 warning emit.

    *migration 을 kill 하지 않는다* — 단지 운영자가 log 에서 "오래 걸림" 을
    인지할 수 있도록.
    """
    time.sleep(SLOW_WARN_SECONDS)
    with _lock:
        if _status.state != MigrationState.RUNNING:
            return
        if _status.slow_warning_emitted:
            return
        _status.slow_warning_emitted = True
        elapsed = time.monotonic() - start_monotonic
    _logger.warning(
        "[migration] still running after %.0fs (elapsed=%.0fs) — "
        "첫 실행 시 alembic upgrade 가 더 걸릴 수 있습니다",
        SLOW_WARN_SECONDS, elapsed,
    )


def run_migration_blocking(apply: Callable[[], None]) -> None:
    """`apply()` 를 *동기적으로* 실행하면서 singleton 을 갱신.

    예외가 발생하면 status=FAILED 로 기록하고 *full traceback 을 log* 에 남긴
    뒤 *return* — caller 에게 raise 하지 않는다 (lifespan 이 죽지 않도록).
    Caller 가 명시적으로 raise 를 원하면 `get_migration_status().state` 를
    확인.
    """
    started_mono = time.monotonic()
    started_iso = datetime.now(timezone.utc).isoformat()
    with _lock:
        _status.state = MigrationState.RUNNING
        _status.started_at = started_iso
        _status.completed_at = None
        _status.duration_seconds = None
        _status.error_type = None
        _status.error_summary = None
        _status.slow_warning_emitted = False

    _logger.info("[migration] starting (alembic upgrade head)")

    # slow-warning watcher — daemon thread, migration 종료와 무관하게 sleep 후
    # 단 1회 check.
    warn_thread = threading.Thread(
        target=_emit_slow_warning_if_running,
        args=(started_mono,),
        daemon=True,
        name="autotrade-migration-slow-warn",
    )
    warn_thread.start()

    try:
        apply()
    except BaseException as exc:
        elapsed = time.monotonic() - started_mono
        completed_iso = datetime.now(timezone.utc).isoformat()
        summary = _redact_error_message(exc)
        with _lock:
            _status.state = MigrationState.FAILED
            _status.completed_at = completed_iso
            _status.duration_seconds = round(elapsed, 3)
            _status.error_type = type(exc).__name__
            _status.error_summary = summary
        # *full traceback 을 log file 에* — exc_info=True 가 traceback 모듈을
        # 호출해 backend-YYYYMMDD.log 에 multi-line stack 을 기록. 응답 본문
        # 에는 절대 안 들어감.
        _logger.error(
            "[migration] FAILED after %.2fs — %s: %s",
            elapsed, type(exc).__name__, summary,
            exc_info=True,
        )
        return

    elapsed = time.monotonic() - started_mono
    completed_iso = datetime.now(timezone.utc).isoformat()
    with _lock:
        _status.state = MigrationState.COMPLETED
        _status.completed_at = completed_iso
        _status.duration_seconds = round(elapsed, 3)
    _logger.info("[migration] complete in %.2fs", elapsed)


def start_migration_in_background(
    apply: Callable[[], None],
) -> threading.Thread:
    """`run_migration_blocking(apply)` 를 데몬 thread 에서 실행.

    idempotent — 이미 살아있는 thread 가 있으면 그대로 반환. 호출자는 thread
    를 직접 join 하지 않고 `get_migration_status()` 로 polling 한다.
    """
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return _thread
    new_thread = threading.Thread(
        target=run_migration_blocking,
        args=(apply,),
        daemon=True,
        name="autotrade-migration",
    )
    with _lock:
        _thread = new_thread
    new_thread.start()
    return new_thread


def get_migration_thread_for_tests() -> Optional[threading.Thread]:
    """Tests-only — 마지막으로 spawn 된 migration thread 의 reference.

    Test 가 명시적으로 `thread.join(timeout=5)` 해서 결정적으로 fence 할 수
    있도록.
    """
    with _lock:
        return _thread
