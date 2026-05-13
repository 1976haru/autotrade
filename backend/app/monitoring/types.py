"""Monitoring DTOs — #70.

순수 dataclass / enum. broker / OrderExecutor / 외부 HTTP 어떤 것도 import 0건.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class MetricStatus(StrEnum):
    """단일 메트릭의 상태. UI에서 OK/WARN/CRITICAL 색상으로 매핑.

    UNKNOWN은 데이터 부족 / 측정 불가 — 시스템 안정성 관점에서 ERROR가 아니라
    *주의*로 표시 (frontend에서 회색).
    """
    OK       = "OK"
    WARN     = "WARN"
    CRITICAL = "CRITICAL"
    UNKNOWN  = "UNKNOWN"

    @classmethod
    def worst(cls, statuses: list["MetricStatus"]) -> "MetricStatus":
        """여러 메트릭 중 가장 나쁜 상태 — overall status 계산용.

        CRITICAL > WARN > OK > UNKNOWN. UNKNOWN은 OK보다 약한 정보(데이터
        부족)이지만 *시스템 정상* 으로 단정짓지 않도록 OK보다 나쁘게 취급한다.
        """
        if not statuses:
            return cls.UNKNOWN
        order = {cls.CRITICAL: 4, cls.WARN: 3, cls.UNKNOWN: 2, cls.OK: 1}
        return max(statuses, key=lambda s: order.get(s, 0))


@dataclass(frozen=True)
class Metric:
    """단일 메트릭 값.

    invariants:
    - `value`는 수치 / 문자열 / dict — JSON serializable.
    - `message`는 사용자 메시지 (한국어 OK). Secret 패턴 미포함 (호출자 책임).
    """
    name:       str
    status:     MetricStatus
    value:      Any            = None
    threshold:  Any            = None
    message:    str            = ""
    extra:      dict[str, Any] = field(default_factory=dict)
    measured_at: datetime      = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":        self.name,
            "status":      self.status.value,
            "value":       self.value,
            "threshold":   self.threshold,
            "message":     self.message,
            "extra":       dict(self.extra),
            "measured_at": self.measured_at.isoformat(),
        }


@dataclass(frozen=True)
class AlertCandidate:
    """알림 후보 — NotificationService로 전달될 수 있는 후보.

    *본 객체 생성만으로 알림이 발송되지 않는다* — 호출자가 명시적으로
    NotificationService.notify()를 호출해야 한다. 시스템 안정성 우선:
    monitoring이 알림 시스템 실패로 깨지면 안 된다.
    """
    severity:   str          # "WARN" | "CRITICAL"
    kind:       str          # "data_stale" | "order_failure_rate" | ...
    title:      str
    message:    str
    dedupe_key: str          # 같은 사건 중복 발송 방지

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity":   self.severity,
            "kind":       self.kind,
            "title":      self.title,
            "message":    self.message,
            "dedupe_key": self.dedupe_key,
        }


@dataclass(frozen=True)
class MonitoringSnapshot:
    """전체 모니터링 스냅샷.

    `metrics`는 개별 메트릭 리스트, `overall`은 그 중 가장 나쁜 상태.
    `alerts`는 alert candidate 리스트 — 호출자가 notify로 전달 결정.
    """
    overall:     MetricStatus
    metrics:     list[Metric]
    alerts:      list[AlertCandidate]
    generated_at: datetime    = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall":      self.overall.value,
            "metrics":      [m.to_dict() for m in self.metrics],
            "alerts":       [a.to_dict() for a in self.alerts],
            "generated_at": self.generated_at.isoformat(),
        }
