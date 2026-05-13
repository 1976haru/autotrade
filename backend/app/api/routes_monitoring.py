"""체크리스트 #70 — Monitoring API.

read-only endpoint:
- GET /api/monitoring/health   : 빠른 liveness + overall status
- GET /api/monitoring/metrics  : 전체 메트릭 스냅샷
- GET /api/monitoring/alerts   : 알림 후보 (WARN/CRITICAL) — *송신은 안 함*

CLAUDE.md 절대 원칙:
- 본 라우터는 broker / OrderExecutor / route_order 어떤 것도 import하지 않는다.
- DB는 read-only — Session으로 SELECT만.
- 응답에 Secret / API Key / 계좌번호를 포함하지 않는다.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.monitoring.service import MonitoringService

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


def _get_service() -> MonitoringService:
    """프로세스 단일 인스턴스. lru_cache 같은 의존성 캐시는 의도적으로 생략 —
    임계치 변경 시 재시작 없이 환경변수만 바꿔도 다음 요청부터 반영되도록.
    """
    return MonitoringService()


def _notification_status() -> dict[str, Any] | None:
    """NotificationService.status()를 안전하게 가져온다. 없으면 None."""
    try:
        from app.notifications.service import build_service_from_settings
        svc = build_service_from_settings(get_settings())
        return svc.status()
    except Exception:  # noqa: BLE001 — monitoring must not break
        return None


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    """빠른 liveness check — overall + 개별 메트릭 status enum.

    상세 value는 /metrics. /health는 가벼운 응답으로 외부 health checker
    (uptime monitor, k8s probe)가 자주 호출해도 부담 없게 한다.
    """
    settings = get_settings()
    svc = _get_service()
    snap = svc.snapshot(
        db,
        notification_status=_notification_status(),
        market_provider=settings.market_data_provider,
        market_stale_max_age=settings.stale_price_max_age_seconds,
    )
    return {
        "overall":      snap.overall.value,
        "generated_at": snap.generated_at.isoformat(),
        "metrics_summary": [
            {"name": m.name, "status": m.status.value, "message": m.message}
            for m in snap.metrics
        ],
        # 위험 안내가 필요한 경우 frontend가 즉시 surface할 수 있도록 알림
        # 후보 *개수*만 carry (상세는 /alerts).
        "alert_count": len(snap.alerts),
    }


@router.get("/metrics")
def metrics(db: Session = Depends(get_db)) -> dict:
    """전체 모니터링 스냅샷.

    응답은 `MonitoringSnapshot.to_dict()` — 각 메트릭의 status / value /
    threshold / message를 포함.
    """
    settings = get_settings()
    svc = _get_service()
    snap = svc.snapshot(
        db,
        notification_status=_notification_status(),
        market_provider=settings.market_data_provider,
        market_stale_max_age=settings.stale_price_max_age_seconds,
    )
    return snap.to_dict()


@router.get("/alerts")
def alerts(db: Session = Depends(get_db)) -> dict:
    """알림 후보 (WARN / CRITICAL).

    *본 endpoint는 알림을 송신하지 않는다* — 후보 *목록*만 반환. 운영자가
    notification 탭에서 확인하거나, 백그라운드 scheduler가 송신 정책으로 결정.
    """
    settings = get_settings()
    svc = _get_service()
    snap = svc.snapshot(
        db,
        notification_status=_notification_status(),
        market_provider=settings.market_data_provider,
        market_stale_max_age=settings.stale_price_max_age_seconds,
    )
    return {
        "generated_at": snap.generated_at.isoformat(),
        "overall":      snap.overall.value,
        "alerts":       [a.to_dict() for a in snap.alerts],
    }
