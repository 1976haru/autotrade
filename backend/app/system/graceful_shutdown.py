"""데스크톱 '안전 종료' 의 정상 종료 경로 — KIS-PAPER-P0-SAFETY-FIX-V1 (2026-10-24).

이전: 앱이 sidecar 를 `taskkill /T /F` 로 즉시 종료 → lifespan 정리(driver/guardian/poller
stop, EOD lease 해제)가 실행되지 않아 진행 중 EOD 단계가 broker 호출과 audit commit 사이에서
끊길 수 있었고, lease 가 TTL 만료까지 고착됐다.

이제 순서:
  1. 앱 → POST /api/system/shutdown (loopback + 이번 실행본 instance_id 헤더 필수)
  2. 보유·미체결·EOD 진행 여부 확인 — 운영자 확인(confirm) 없이는 위험 시 409
  3. 허용되면 응답 후 프로세스에 SIGINT → uvicorn 정상 종료 → lifespan 이 guardian/driver 를
     *진행 중 단계 완료까지* 기다려 멈추고, poller 정지, 이 프로세스의 EOD lease 해제
  4. 앱은 종료 완료를 확인하고, 시간 내 끝나지 않을 때만 최종 강제 종료

주문·취소를 만들지 않는다 (broker / route_order import 0).
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any, Callable

_log = logging.getLogger("autotrade.system.shutdown")

INSTANCE_HEADER = "X-Autotrade-Instance"
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _raise_sigint() -> None:
    signal.raise_signal(signal.SIGINT)


# 테스트가 교체한다 — 실제 프로세스에 신호를 보내지 않도록.
request_process_exit: Callable[[], None] = _raise_sigint


def shutdown_preflight(db: Any) -> dict[str, Any]:
    """종료 전 상태 — 보유/미체결(원장·마지막 대조) + EOD 단계 진행 여부. broker 호출 0."""
    from app.kis_paper.eod_liquidation import shutdown_check, step_in_progress
    chk = shutdown_check(db)
    in_progress = bool(step_in_progress())
    return {
        **chk,
        "eod_step_in_progress": in_progress,
        "safe_without_confirmation": bool(chk.get("safe_to_quit")) and not in_progress,
    }


def authorize(client_host: str | None, header_instance: str | None,
              expected_instance: str) -> str | None:
    """거부 사유 (None = 허용). 다른 PC·다른 실행본이 이 백엔드를 끄지 못하게 한다."""
    if (client_host or "") not in _LOOPBACK:
        return "SHUTDOWN_LOOPBACK_ONLY"
    if not header_instance or header_instance != expected_instance:
        return "SHUTDOWN_INSTANCE_MISMATCH"
    return None


def schedule_exit(delay_seconds: float = 0.3) -> None:
    """응답이 나간 뒤 종료 신호 — uvicorn 이 정상 종료(lifespan finally) 하도록."""
    loop = asyncio.get_event_loop()
    loop.call_later(max(0.0, delay_seconds), request_process_exit)


def release_process_eod_leases() -> int:
    """lifespan 종료 시 — 이 프로세스의 EOD lease 해제 (실패해도 TTL 만료로 회복)."""
    try:
        from app.db.session import SessionLocal
        from app.kis_paper.eod_liquidation import release_owner_leases
        with SessionLocal() as db:
            n = release_owner_leases(db)
        if n:
            _log.info("[shutdown] released %d EOD lease(s)", n)
        return n
    except Exception as exc:  # noqa: BLE001 — 종료 정리는 best-effort.
        _log.warning("[shutdown] EOD lease release failed: %s", type(exc).__name__)
        return 0
