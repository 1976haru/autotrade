"""체크리스트 #64: Notifications API (status / test / mock-event).

CLAUDE.md 절대 원칙:
- 본 라우트는 broker / OrderExecutor / route_order 어떤 함수도 호출하지 않는다.
- Telegram Bot Token / chat_id / KIS / Anthropic Secret을 응답에 *절대*
  포함하지 않는다 (`NotificationService.status()`가 token/chat_id 미포함).
- 알림 발송 실패가 HTTP 200을 다른 status로 바꾸지 않는다 — SendResult를 그대로
  carry해서 운영자가 사후 확인.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.notifications import (
    NotificationKind,
    NotificationSeverity,
)
from app.notifications.service import build_service_from_settings
from app.notifications.templates import (
    build_approval_pending_event,
    build_broker_error_event,
    build_daily_loss_warning_event,
    build_data_stale_event,
    build_emergency_stop_event,
    build_margin_risk_event,
    build_repeated_rejection_event,
    build_risk_auditor_event,
)
from app.notifications.types import NotificationEvent


router = APIRouter(prefix="/notifications", tags=["notifications"])


# ====================================================================
# Schemas
# ====================================================================


class NotificationStatusOut(BaseModel):
    """알림 상태 스냅샷 — *Secret 미포함*.

    `channel_configured`는 boolean으로만 노출. token / chat_id 값은 어떤
    응답 필드에도 포함되지 *않는다*.
    """
    enabled:               bool
    channel:               str
    channel_configured:    bool
    telegram_configured:   bool
    min_severity:          int
    min_severity_name:     str
    dedupe_window_seconds: int
    always_send_critical:  bool
    notice:                str = (
        "Telegram Bot Token / chat_id는 backend/.env에만 저장됩니다. "
        "본 응답에는 어떤 Secret도 포함되지 않습니다."
    )


class TestNotificationOut(BaseModel):
    ok:             bool
    channel:        str
    skipped_reason: str | None = None
    error:          str | None = None


class MockEventIn(BaseModel):
    kind:        str
    # 각 kind별로 추가 필드. 모두 optional — 미지정 시 안전 기본값.
    enabled:           bool | None = None
    level:             str | None  = None
    reason_code:       str | None  = None
    decided_by:        str | None  = None
    note:              str | None  = None
    symbol:            str | None  = None
    age_seconds:       int | None  = None
    threshold_seconds: int | None  = None
    approval_id:       int | None  = None
    side:              str | None  = None
    quantity:          int | None  = None
    strategy:          str | None  = None
    requested_by_ai:   bool | None = None
    expires_at:        str | None  = None
    current_loss:      int | None  = None
    limit:             int | None  = None
    pct:               int | None  = None
    broker:            str | None  = None
    operation:         str | None  = None
    message:           str | None  = None
    count:             int | None  = None
    window_seconds:    int | None  = None
    threshold:         int | None  = None
    used_pct:          float | None = None
    liquidation_distance_pct: float | None = None
    audit_level:       str | None  = None
    risk_score:        int | None  = None
    summary:           str | None  = None
    # dry_run=True (default): channel 호출까지 가지만 token 없으면 noop으로 skip.
    # False여도 토큰 없으면 NoOpChannel이라 무해.
    dry_run:           bool = True


# ====================================================================
# Routes
# ====================================================================


@router.get("/status", response_model=NotificationStatusOut)
def get_status() -> NotificationStatusOut:
    """알림 상태 + 채널 구성 여부.

    Secret 미포함 — boolean / int / 채널 이름만 노출. token / chat_id 값은
    어떤 응답 필드에도 들어가지 *않는다*.
    """
    service = build_service_from_settings(get_settings())
    s = service.status()
    settings = get_settings()
    # telegram_configured는 channel_configured와 동의어지만, 별도 boolean으로
    # 노출해 UI가 "현재 채널이 Telegram인지" 명시적으로 분기할 수 있게 한다.
    telegram_cfg = (s["channel"] == "telegram") and bool(s["channel_configured"])
    # 누락된 telegram 변수 확인 (boolean만, 값 자체는 노출 X)
    if not telegram_cfg:
        telegram_cfg = bool(
            (settings.telegram_bot_token or "").strip()
            and (settings.telegram_chat_id or "").strip()
        )
    return NotificationStatusOut(
        enabled=             bool(s["enabled"]),
        channel=             str(s["channel"]),
        channel_configured=  bool(s["channel_configured"]),
        telegram_configured= bool(telegram_cfg),
        min_severity=        int(s["min_severity"]),
        min_severity_name=   str(s["min_severity_name"]),
        dedupe_window_seconds=int(s["dedupe_window_seconds"]),
        always_send_critical=bool(s["always_send_critical"]),
    )


@router.post("/test", response_model=TestNotificationOut)
def post_test() -> TestNotificationOut:
    """테스트 메시지 발송.

    `NOTIFICATIONS_ENABLED=false` 또는 Telegram 미구성이면 즉시 skipped 반환
    (실제 broker / 외부 호출 0건). 응답에 token 미포함.
    """
    service = build_service_from_settings(get_settings())
    event = NotificationEvent(
        kind=NotificationKind.TEST,
        severity=NotificationSeverity.INFO,
        title="[테스트] 알림 채널 점검",
        message=(
            "본 메시지는 /api/notifications/test에서 발송된 테스트 알림입니다. "
            "Secret/계좌번호/Token은 포함되지 않습니다."
        ),
        dedupe_key=None,   # 테스트는 dedupe 안 함 — 운영자가 의도적으로 반복 호출 가능
    )
    result = service.notify(event)
    return TestNotificationOut(**result.to_dict())


@router.post("/mock-event", response_model=TestNotificationOut)
def post_mock_event(body: MockEventIn) -> TestNotificationOut:
    """mock NotificationEvent 생성 후 발송. broker 호출 0건. token 노출 0건.

    각 kind는 templates의 builder를 그대로 호출 — 운영자가 안전한 시나리오
    검증용. dry_run=True 기본이라 실제 Telegram API 호출이 일어나지 않는다.
    """
    try:
        kind_str = (body.kind or "").lower()
        event: NotificationEvent | None = None
        if kind_str == NotificationKind.EMERGENCY_STOP.value:
            event = build_emergency_stop_event(
                enabled=bool(body.enabled),
                level=body.level,
                reason_code=body.reason_code,
                decided_by=body.decided_by,
                note=body.note,
            )
        elif kind_str == NotificationKind.DATA_STALE.value:
            event = build_data_stale_event(
                symbol=body.symbol or "TEST",
                age_seconds=int(body.age_seconds or 120),
                threshold_seconds=body.threshold_seconds,
            )
        elif kind_str == NotificationKind.APPROVAL_PENDING.value:
            event = build_approval_pending_event(
                approval_id=int(body.approval_id or 0),
                symbol=body.symbol or "TEST",
                side=(body.side or "BUY"),
                quantity=int(body.quantity or 1),
                strategy=body.strategy,
                requested_by_ai=bool(body.requested_by_ai),
                expires_at=body.expires_at,
            )
        elif kind_str == NotificationKind.DAILY_LOSS_WARNING.value:
            event = build_daily_loss_warning_event(
                current_loss=int(body.current_loss or -100_000),
                limit=int(body.limit or 200_000),
                pct=body.pct,
            )
        elif kind_str == NotificationKind.BROKER_ERROR.value:
            event = build_broker_error_event(
                broker=body.broker or "mock",
                operation=body.operation or "get_balance",
                message=body.message or "timeout",
            )
        elif kind_str == NotificationKind.REPEATED_REJECTION.value:
            event = build_repeated_rejection_event(
                count=int(body.count or 5),
                window_seconds=int(body.window_seconds or 60),
                threshold=int(body.threshold or 3),
            )
        elif kind_str == NotificationKind.MARGIN_RISK.value:
            event = build_margin_risk_event(
                used_pct=float(body.used_pct or 85.0),
                liquidation_distance_pct=body.liquidation_distance_pct,
            )
        elif kind_str == NotificationKind.RISK_AUDITOR_WARN.value:
            event = build_risk_auditor_event(
                audit_level=body.audit_level or "YELLOW",
                risk_score=int(body.risk_score or 60),
                summary=body.summary or "mock auditor warning",
            )
        else:
            return TestNotificationOut(
                ok=False, channel="unknown",
                error=f"unknown kind: {body.kind!r}",
            )
    except Exception as exc:  # noqa: BLE001
        return TestNotificationOut(
            ok=False, channel="unknown",
            error=f"build_event_failed:{type(exc).__name__}",
        )

    # dry_run=True면 실제 채널 호출 회피 — channel을 NoOp으로 교체.
    if body.dry_run:
        from app.notifications.channels import NoOpChannel
        from app.notifications.service import NotificationService
        service: Any = NotificationService(
            channel=NoOpChannel(),
            enabled=True,
            min_severity=int(NotificationSeverity.DEBUG),
            dedupe_window_seconds=0,
        )
    else:
        service = build_service_from_settings(get_settings())

    result = service.notify(event)
    return TestNotificationOut(**result.to_dict())


__all__ = ["router"]
