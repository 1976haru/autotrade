"""체크리스트 #64: NotificationService — dedupe / severity gate / safe send.

CLAUDE.md 절대 원칙:
- `notify()`는 *raise하지 않는다*. 어떤 호출자도 try/except 없이 부를 수 있어야.
- 실제 송신은 channel에 위임 — 본 서비스는 *정책 게이트*만.

게이트 (순서대로):
1. enabled=false → skipped (always_send_critical=True여도 적용).
2. event.severity < min_severity → skipped.
3. dedupe_key가 dedupe_window_seconds 안에 이미 발송 → skipped.
4. channel.send(event) 호출.

dedupe storage는 in-memory dict — 멀티 인스턴스 / 영구화는 후속 (Redis).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.notifications.channels import NoOpChannel
from app.notifications.types import (
    NotificationChannel,
    NotificationEvent,
    NotificationSeverity,
    SendResult,
)


@dataclass
class NotificationService:
    """알림 게이트.

    fields:
    - channel: NotificationChannel (NoOpChannel이면 항상 noop_channel skip)
    - enabled: false면 모든 이벤트 skipped
    - min_severity: int — event.severity 미만이면 skipped
    - dedupe_window_seconds: 0이면 dedupe 비활성
    - always_send_critical: True면 CRITICAL은 dedupe도 우회

    invariants:
    - notify(event)는 raise하지 *않는다*. 어떤 예외도 SendResult로 carry.
    - service는 token / chat_id / Secret를 *직접* 보관하지 않는다 — channel에만.
    """
    channel:               NotificationChannel
    enabled:               bool  = True
    min_severity:          int   = int(NotificationSeverity.INFO)
    dedupe_window_seconds: int   = 60
    always_send_critical:  bool  = True

    # in-memory dedupe — {dedupe_key: last_sent_at_epoch}
    _last_sent: dict[str, float] = field(default_factory=dict)

    def notify(self, event: NotificationEvent) -> SendResult:
        """단일 이벤트 발송. 절대 raise하지 않는다."""
        try:
            return self._notify_inner(event)
        except Exception as exc:  # noqa: BLE001 — service must not raise
            return SendResult(
                ok=False,
                channel=self._channel_name(),
                error=f"{type(exc).__name__}:{exc}",
            )

    def _notify_inner(self, event: NotificationEvent) -> SendResult:
        channel_name = self._channel_name()

        # 1) enabled
        if not self.enabled:
            return SendResult(
                ok=True, channel=channel_name,
                skipped_reason="notifications_disabled",
            )

        # 2) severity gate
        if int(event.severity) < int(self.min_severity):
            return SendResult(
                ok=True, channel=channel_name,
                skipped_reason="below_min_severity",
            )

        # 3) dedupe
        if (event.dedupe_key
                and self.dedupe_window_seconds > 0
                and not (self.always_send_critical
                         and int(event.severity) >= int(NotificationSeverity.CRITICAL))):
            now = time.time()
            last = self._last_sent.get(event.dedupe_key)
            if last is not None and (now - last) < self.dedupe_window_seconds:
                return SendResult(
                    ok=True, channel=channel_name,
                    skipped_reason="deduped",
                )

        # 4) channel.send — channel은 raise하지 않게 만들어졌지만 방어용으로 try.
        try:
            result = self.channel.send(event)
        except Exception as exc:  # noqa: BLE001
            return SendResult(
                ok=False, channel=channel_name,
                error=f"{type(exc).__name__}:{exc}",
            )

        # 발송 성공 시 dedupe 기록 (skipped는 기록 X — 다음 시도가 막히지 않게)
        if result.ok and result.skipped_reason is None and event.dedupe_key:
            self._last_sent[event.dedupe_key] = time.time()
        return result

    def _channel_name(self) -> str:
        return getattr(self.channel, "name", "unknown")

    def status(self) -> dict[str, Any]:
        """frontend / API status endpoint가 표시할 dict. *Secret 미포함*."""
        return {
            "enabled":               self.enabled,
            "channel":               self._channel_name(),
            "channel_configured":    self.channel.is_configured(),
            "min_severity":          int(self.min_severity),
            "min_severity_name":     NotificationSeverity(self.min_severity).name
                if int(self.min_severity) in NotificationSeverity._value2member_map_  # noqa: SLF001
                else "INFO",
            "dedupe_window_seconds": int(self.dedupe_window_seconds),
            "always_send_critical":  bool(self.always_send_critical),
        }


# ---------- factory ----------


def build_service_from_settings(settings: Any) -> NotificationService:
    """Settings로부터 NotificationService 인스턴스 생성. NoOpChannel fallback."""
    from app.notifications.channels import build_channel_from_settings

    enabled = bool(getattr(settings, "notifications_enabled", False))
    channel = build_channel_from_settings(settings)
    if isinstance(channel, NoOpChannel):
        # NoOpChannel일 땐 enabled=False로 강제 — status에서 명확히 표시.
        enabled = False
    min_sev = NotificationSeverity.from_string(
        getattr(settings, "notifications_min_severity", "INFO"),
    )
    return NotificationService(
        channel=channel,
        enabled=enabled,
        min_severity=int(min_sev),
        dedupe_window_seconds=int(
            getattr(settings, "notifications_dedupe_window_seconds", 60),
        ),
        always_send_critical=bool(
            getattr(settings, "notifications_always_send_critical", True),
        ),
    )
