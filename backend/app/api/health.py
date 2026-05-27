"""Health check endpoints — read-only 컴포넌트 점검.

- GET /api/health        — liveness (항상 200, 의존성 0건)
- GET /api/health/full   — 컴포넌트별 OK/WARN/FAIL 집계

본 모듈은 *기존* read-only 로직만 재사용한다 (evaluate_readiness + DB SELECT 1
+ 안전 flag 현재값). broker / OrderExecutor / route_order 호출 0건, 주문 0건,
secret 값 출력 0건(present 여부 bool 만), 안전 flag mutate 0건.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.kis_paper.readiness import evaluate_readiness

router = APIRouter(prefix="/health", tags=["health"])

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"

# 프로세스 시작 시각 (uptime 계산).
_PROCESS_START = time.time()
# tick 지연 판정 임계 (초) — 기본 30s 간격 × 3.
_TICK_STALE_SECONDS = 90.0


def _stability_block() -> dict[str, Any]:
    """Watchdog/안정성 메트릭 — 실패해도 health 를 깨뜨리지 않는다."""
    now = datetime.now(timezone.utc)
    block: dict[str, Any] = {
        "watchdog_enabled": str(os.environ.get("WATCHDOG_ENABLED", "")).lower()
        in ("1", "true", "yes"),
        "backend_uptime_sec": round(time.time() - _PROCESS_START, 1),
        "last_tick_at": None,
        "tick_stale": False,
        "engine_state": None,
        "errors_last_5min": 0,
        "recovery_success_rate": None,   # 라이브 미집계 (chaos_test 에서 산출)
        "last_restart_at": None,
        "secret_leak_detected": False,
        "log_file_path": str(os.environ.get("STABILITY_LOG_PATH",
                                            "logs/runtime.jsonl")),
    }
    try:
        from app.kis_paper.engine import get_engine
        block["engine_state"] = getattr(get_engine().state, "value",
                                        str(get_engine().state))
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.system.event_log import EventLevel, get_runtime_event_log
        log = get_runtime_event_log()
        recent = log.recent(limit=1)
        if recent:
            block["last_tick_at"] = recent[-1].timestamp
            try:
                last = datetime.fromisoformat(recent[-1].timestamp)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                block["tick_stale"] = (now - last).total_seconds() > _TICK_STALE_SECONDS
            except Exception:  # noqa: BLE001
                pass
        errs = log.recent(limit=500, min_level=EventLevel.ERROR,
                          since=(now - timedelta(minutes=5)).isoformat())
        block["errors_last_5min"] = len(errs)
    except Exception:  # noqa: BLE001
        pass
    return block


@router.get("")
def health() -> dict[str, Any]:
    """Liveness — backend 가 응답하는지만 확인 (의존성 0건)."""
    return {"status": "ok"}


@router.get("/full")
def health_full(db: Session = Depends(get_db)) -> dict[str, Any]:
    """컴포넌트별 OK/WARN/FAIL 집계. read-only — 주문/secret/flag 변경 0건."""
    settings = get_settings()
    checks: dict[str, dict[str, Any]] = {}

    # 1. API liveness — 응답 중이므로 OK.
    checks["api"] = {"status": OK, "detail": "backend responding"}

    # 2. DB — SELECT 1.
    try:
        db.execute(text("SELECT 1"))
        checks["db"] = {"status": OK, "detail": "db reachable"}
    except Exception as exc:  # noqa: BLE001
        checks["db"] = {"status": FAIL, "detail": type(exc).__name__}

    # 3. KIS 자격 — *present 여부만* (값 0건).
    rd = evaluate_readiness(settings)
    cred = bool(rd.kis_key_present and rd.kis_secret_present and rd.kis_account_present)
    checks["kis_credentials"] = {
        "status": OK if cred else WARN,
        "detail": "present" if cred else "missing (paper test blocked)",
        "credentials_present": cred,
    }

    # 4. 안전 flag — live/ai/futures off + kis_is_paper on 이어야 안전.
    safe = (
        not settings.enable_live_trading
        and not settings.enable_ai_execution
        and not settings.enable_futures_live_trading
        and bool(settings.kis_is_paper)
    )
    checks["safety_flags"] = {
        "status": OK if safe else FAIL,
        "detail": "live/ai/futures OFF, kis_is_paper ON" if safe
        else "UNSAFE: live/ai/futures enabled or kis_is_paper off",
        "enable_live_trading": bool(settings.enable_live_trading),
        "enable_ai_execution": bool(settings.enable_ai_execution),
        "enable_futures_live_trading": bool(settings.enable_futures_live_trading),
        "kis_is_paper": bool(settings.kis_is_paper),
    }

    # 5. KIS Paper 진입 가능 여부 (자격 + 안전 flag 조합).
    checks["kis_paper_ready"] = {
        "status": OK if rd.can_run_kis_paper else WARN,
        "detail": "can_run_kis_paper" if rd.can_run_kis_paper
        else "blocked (자격/flag 확인)",
    }

    # 6. 시장 데이터 provider (정보).
    checks["market_data"] = {
        "status": OK,
        "detail": str(getattr(settings, "market_data_provider", "unknown")),
    }

    statuses = [c["status"] for c in checks.values()]
    overall = FAIL if FAIL in statuses else (WARN if WARN in statuses else OK)

    return {
        "overall": overall,
        "checks": checks,
        "credentials_present": cred,
        # Watchdog/안정성 메트릭 (CHECKLIST-03).
        "stability": _stability_block(),
        # 불변: 본 엔드포인트는 주문/실거래 권한과 무관.
        "is_live_authorization": False,
        "contains_secret": False,
    }
