"""체크리스트 #64: NotificationEvent 표준 모델.

본 모듈은 broker / OrderExecutor / Secret 어떤 것도 import하지 않는다.
순수 dataclass + Protocol.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum, StrEnum
from typing import Any


# ---------- enums ----------


class NotificationSeverity(IntEnum):
    """심각도 — 정수로 비교 가능 (min_severity 게이트용).

    DEBUG(0) < INFO(10) < WARN(20) < CRITICAL(30).
    """
    DEBUG    = 0
    INFO     = 10
    WARN     = 20
    CRITICAL = 30

    @classmethod
    def from_string(cls, raw: str | None) -> "NotificationSeverity":
        if raw is None:
            return cls.INFO
        s = str(raw).upper()
        if s in cls.__members__:
            return cls[s]
        return cls.INFO


class NotificationKind(StrEnum):
    """알림 사유 분류 — UI / audit / dedupe key에서 사용."""
    EMERGENCY_STOP      = "emergency_stop"
    DATA_STALE          = "data_stale"
    APPROVAL_PENDING    = "approval_pending"
    DAILY_LOSS_WARNING  = "daily_loss_warning"
    BROKER_ERROR        = "broker_error"
    REPEATED_REJECTION  = "repeated_rejection"
    MARGIN_RISK         = "margin_risk"
    RISK_AUDITOR_WARN   = "risk_auditor_warn"
    DAILY_REPORT        = "daily_report"
    ORDER_SUCCESS       = "order_success"   # 후순위 / 기본 미발송
    TEST                = "test"


# ---------- event ----------


@dataclass(frozen=True)
class NotificationEvent:
    """단일 알림 이벤트.

    invariants:
    - Secret(token, key, secret, account number) 절대 미포함 — 생성자가
      `__post_init__`에서 message에 의심 패턴 있으면 ValueError.
    - dedupe_key는 같은 사건이 짧은 시간에 여러 번 발생할 때 발송을 억제하기
      위한 안정 식별자 (예: 'emergency_stop:on:ops1').
    """
    kind:        NotificationKind
    severity:    NotificationSeverity
    title:       str
    message:     str
    dedupe_key:  str | None        = None
    extra:       dict[str, Any]    = field(default_factory=dict)
    created_at:  datetime          = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def __post_init__(self) -> None:
        # invariant — Secret 의심 패턴 차단. 실제 운영 secret이 우연히 포함되는
        # 사고를 막기 위한 fail-closed.
        text = f"{self.title}\n{self.message}".lower()
        forbidden = [
            "kis_app_key", "kis_app_secret", "kis_account_no",
            "anthropic_api_key", "openai_api_key",
            "telegram_bot_token", "telegram_chat_id",
            "sk-", "bearer ",
        ]
        for needle in forbidden:
            if needle in text:
                raise ValueError(
                    f"NotificationEvent.message contains forbidden token "
                    f"pattern: {needle!r}"
                )
        if not (0 <= int(self.severity) <= 100):
            raise ValueError(
                f"NotificationEvent.severity invalid: {self.severity}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind":       self.kind.value,
            "severity":   int(self.severity),
            "severity_name": self.severity.name,
            "title":      self.title,
            "message":    self.message,
            "dedupe_key": self.dedupe_key,
            "extra":      dict(self.extra),
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class SendResult:
    """Channel.send 의 표준 결과. raise 하지 않고 본 객체로 응답."""
    ok:             bool
    channel:        str
    skipped_reason: str | None = None
    error:          str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok":             self.ok,
            "channel":        self.channel,
            "skipped_reason": self.skipped_reason,
            "error":          self.error,
        }


# ---------- channel protocol ----------


class NotificationChannel(ABC):
    """알림 채널 인터페이스. 구현체는 절대 raise하지 않고 SendResult 반환."""

    name: str = "abstract"

    @abstractmethod
    def is_configured(self) -> bool:
        """설정이 충분한지 (예: Telegram token + chat_id 존재). secret 값 자체
        는 반환하지 않는다 — boolean만."""

    @abstractmethod
    def send(self, event: NotificationEvent) -> SendResult:
        """이벤트를 채널로 전송. *raise 금지* — 실패는 SendResult.error로 carry."""
