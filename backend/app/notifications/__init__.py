"""체크리스트 #64: 운영자 알림 패키지.

CLAUDE.md 절대 원칙:
1. 본 패키지는 broker / OrderExecutor / route_order 어떤 것도 호출하지 않는다.
2. Telegram Bot Token / chat_id / KIS / Anthropic Secret을 응답 / 로그 / frontend
   응답에 노출하지 않는다.
3. 알림 실패는 *주문 / 리스크 판단을 깨뜨리지 않는다* — 모든 호출자는
   try/except로 감싸야 하고, NotificationService.notify는 raise하지 않는다.
4. 위험 알림 (CRITICAL / WARN) 우선, 주문 성공 알림 (INFO) 후순위 또는 미발송.
5. timeout/retry 제한 필수 — 외부 네트워크 호출이 backend 응답을 지연시키지 않게.

외부 export:
    - NotificationEvent / NotificationSeverity / NotificationChannel
    - TelegramChannel / NoOpChannel
    - NotificationService — dedupe + min_severity + safe send
    - build_emergency_stop_event / build_data_stale_event /
      build_approval_pending_event / build_daily_loss_warning_event /
      build_broker_error_event / build_repeated_rejection_event /
      build_margin_risk_event
"""

from app.notifications.types import (
    NotificationChannel,
    NotificationEvent,
    NotificationKind,
    NotificationSeverity,
    SendResult,
)
from app.notifications.channels import (
    NoOpChannel,
    TelegramChannel,
)
from app.notifications.service import NotificationService
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

__all__ = [
    "NotificationChannel",
    "NotificationEvent",
    "NotificationKind",
    "NotificationSeverity",
    "NotificationService",
    "NoOpChannel",
    "SendResult",
    "TelegramChannel",
    "build_approval_pending_event",
    "build_broker_error_event",
    "build_daily_loss_warning_event",
    "build_data_stale_event",
    "build_emergency_stop_event",
    "build_margin_risk_event",
    "build_repeated_rejection_event",
    "build_risk_auditor_event",
]
