"""Health check endpoints — read-only 컴포넌트 점검.

- GET /api/health        — liveness (항상 200, 의존성 0건)
- GET /api/health/full   — 컴포넌트별 OK/WARN/FAIL 집계

본 모듈은 *기존* read-only 로직만 재사용한다 (evaluate_readiness + DB SELECT 1
+ 안전 flag 현재값). broker / OrderExecutor / route_order 호출 0건, 주문 0건,
secret 값 출력 0건(present 여부 bool 만), 안전 flag mutate 0건.
"""

from __future__ import annotations

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
        # 불변: 본 엔드포인트는 주문/실거래 권한과 무관.
        "is_live_authorization": False,
        "contains_secret": False,
    }
