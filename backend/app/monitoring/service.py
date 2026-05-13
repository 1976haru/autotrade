"""MonitoringService — 서버 / API / 데이터 / 주문 안정성 read-only 집계 (#70).

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order / live order API import 0건.
- DB는 read-only SELECT만 (INSERT/UPDATE/DELETE 0건 — 정적 grep 가드).
- 알림은 *후보*만 만든다. 실제 송신은 호출자가 `NotificationService.notify` 호출.
- 본 서비스는 절대 raise 하지 않는다 — 어떤 collector 실패도 UNKNOWN 메트릭으로
  surface (시스템 안정성 우선).
- Secret / API Key / 계좌번호를 메트릭/메시지에 노출하지 않는다.

스레숄드(default — 운영자가 조절 가능):
- API error rate WARN ≥ 5%, CRITICAL ≥ 20% (5분 윈도우)
- Order failure rate WARN ≥ 30%, CRITICAL ≥ 60% (24h 윈도우, 최소 5건)
- Approval queue oldest age WARN ≥ 10분, CRITICAL ≥ 30분
- Data freshness WARN: bar / quote / feed 1개 이상 stale → WARN
- DB ping fail → CRITICAL
- Notification service unconfigured + LIVE_*: WARN (운영자 안내)
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.models import EmergencyStopEvent, OrderAuditLog, PendingApproval
from app.monitoring.api_metrics import ApiMetricsRegistry, get_api_metrics
from app.monitoring.types import (
    AlertCandidate,
    Metric,
    MetricStatus,
    MonitoringSnapshot,
)


# ---------- thresholds ----------


@dataclass(frozen=True)
class MonitoringThresholds:
    """모니터링 임계치 — 운영자가 환경변수로 조절 가능 (#70 후속).

    이번 PR에서는 코드 default로 시작. ENV override는 후속 PR.
    """
    api_error_rate_window_seconds: int   = 300
    api_error_rate_warn:           float = 0.05
    api_error_rate_critical:       float = 0.20

    order_failure_window_minutes:  int   = 24 * 60
    order_failure_min_orders:      int   = 5
    order_failure_warn:            float = 0.30
    order_failure_critical:        float = 0.60

    approval_age_warn_minutes:     int   = 10
    approval_age_critical_minutes: int   = 30

    risk_event_window_minutes:     int   = 60
    risk_event_warn_count:         int   = 3
    risk_event_critical_count:     int   = 8


# ---------- process start time ----------


_PROCESS_STARTED_AT_EPOCH = time.time()


def _started_at_iso() -> str:
    return datetime.fromtimestamp(
        _PROCESS_STARTED_AT_EPOCH, tz=timezone.utc,
    ).isoformat()


# ---------- service ----------


class MonitoringService:
    """모니터링 read-only 집계 서비스.

    공개 API:
    - collect_server() → Metric
    - collect_db(db)   → Metric
    - collect_api_error_rate() → Metric
    - collect_order_failure_rate(db) → Metric
    - collect_approval_queue(db) → Metric
    - collect_risk_events(db) → Metric
    - collect_data_freshness(provider) → Metric (provider 정보 carry only)
    - collect_notification(status) → Metric (NotificationService.status() carry)
    - snapshot(db, *, notification_status, market_provider) → MonitoringSnapshot
    """

    def __init__(
        self,
        thresholds:    MonitoringThresholds | None = None,
        api_metrics:   ApiMetricsRegistry | None   = None,
    ) -> None:
        self.thresholds  = thresholds or MonitoringThresholds()
        self.api_metrics = api_metrics or get_api_metrics()

    # ---------- server ----------

    def collect_server(self) -> Metric:
        try:
            uptime_seconds = max(0.0, time.time() - _PROCESS_STARTED_AT_EPOCH)
            return Metric(
                name="server",
                status=MetricStatus.OK,
                value={
                    "started_at":      _started_at_iso(),
                    "uptime_seconds":  round(uptime_seconds, 1),
                    "pid":             os.getpid(),
                },
                message="시스템 정상",
            )
        except Exception as exc:  # noqa: BLE001
            return Metric(
                name="server", status=MetricStatus.UNKNOWN,
                message=f"server metric error: {type(exc).__name__}",
            )

    # ---------- db ----------

    def collect_db(self, db: Session) -> Metric:
        """DB 연결성 — SELECT 1 ping.

        실패 시 CRITICAL. INSERT/UPDATE/DELETE 절대 호출하지 않는다.
        """
        try:
            db.execute(text("SELECT 1")).scalar()
            return Metric(
                name="database", status=MetricStatus.OK,
                value={"reachable": True},
                message="DB 정상",
            )
        except Exception as exc:  # noqa: BLE001
            return Metric(
                name="database", status=MetricStatus.CRITICAL,
                value={"reachable": False},
                message=f"DB ping 실패: {type(exc).__name__}",
            )

    # ---------- api error rate ----------

    def collect_api_error_rate(self) -> Metric:
        try:
            snap = self.api_metrics.snapshot(
                window_seconds=self.thresholds.api_error_rate_window_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            return Metric(
                name="api_error_rate", status=MetricStatus.UNKNOWN,
                message=f"api metrics snapshot 실패: {type(exc).__name__}",
            )

        rate = float(snap.get("error_rate", 0.0) or 0.0)
        calls = int(snap.get("calls", 0) or 0)

        if calls == 0:
            status = MetricStatus.UNKNOWN
            msg    = "최근 API 호출 없음 — 측정 불가"
        elif rate >= self.thresholds.api_error_rate_critical:
            status = MetricStatus.CRITICAL
            msg    = "API 오류율 증가 (CRITICAL)"
        elif rate >= self.thresholds.api_error_rate_warn:
            status = MetricStatus.WARN
            msg    = "API 오류율 증가 주의"
        else:
            status = MetricStatus.OK
            msg    = "API 오류율 정상"

        return Metric(
            name="api_error_rate",
            status=status,
            value=snap,
            threshold={
                "warn":     self.thresholds.api_error_rate_warn,
                "critical": self.thresholds.api_error_rate_critical,
                "window":   self.thresholds.api_error_rate_window_seconds,
            },
            message=msg,
        )

    # ---------- order failure rate ----------

    def collect_order_failure_rate(self, db: Session) -> Metric:
        """OrderAuditLog 최근 윈도우에서 REJECTED 비율.

        APPROVED + NEEDS_APPROVAL은 *성공 흐름*으로 본다 (broker 차단 X).
        REJECTED만 failure로 카운트.
        """
        try:
            from datetime import timedelta
            now = datetime.now(timezone.utc)
            since = now - timedelta(minutes=self.thresholds.order_failure_window_minutes)
            stmt_total = select(func.count()).select_from(OrderAuditLog).where(
                OrderAuditLog.created_at >= since,
            )
            stmt_fail = select(func.count()).select_from(OrderAuditLog).where(
                OrderAuditLog.created_at >= since,
                OrderAuditLog.decision == "REJECTED",
            )
            total = int(db.execute(stmt_total).scalar() or 0)
            fail  = int(db.execute(stmt_fail).scalar() or 0)
        except Exception as exc:  # noqa: BLE001
            return Metric(
                name="order_failure_rate", status=MetricStatus.UNKNOWN,
                message=f"OrderAuditLog 조회 실패: {type(exc).__name__}",
            )

        rate = (fail / total) if total else 0.0
        if total < self.thresholds.order_failure_min_orders:
            status = MetricStatus.UNKNOWN
            msg    = "최근 주문 표본 부족 — 측정 불가"
        elif rate >= self.thresholds.order_failure_critical:
            status = MetricStatus.CRITICAL
            msg    = "주문 실패율 매우 높음"
        elif rate >= self.thresholds.order_failure_warn:
            status = MetricStatus.WARN
            msg    = "주문 실패율 증가"
        else:
            status = MetricStatus.OK
            msg    = "주문 실패율 정상"

        return Metric(
            name="order_failure_rate",
            status=status,
            value={
                "total":         total,
                "failed":        fail,
                "rate":          round(rate, 4),
                "window_minutes": self.thresholds.order_failure_window_minutes,
            },
            threshold={
                "warn":       self.thresholds.order_failure_warn,
                "critical":   self.thresholds.order_failure_critical,
                "min_orders": self.thresholds.order_failure_min_orders,
            },
            message=msg,
        )

    # ---------- approval queue ----------

    def collect_approval_queue(self, db: Session) -> Metric:
        """PendingApproval — pending 개수 + 가장 오래된 row의 age.

        status='PENDING'만 카운트. APPROVED/REJECTED/CANCELLED/EXPIRED는 제외.
        """
        try:
            stmt = select(PendingApproval).where(
                PendingApproval.status == "PENDING",
            )
            rows = list(db.execute(stmt).scalars())
        except Exception as exc:  # noqa: BLE001
            return Metric(
                name="approval_queue", status=MetricStatus.UNKNOWN,
                message=f"PendingApproval 조회 실패: {type(exc).__name__}",
            )

        now = datetime.now(timezone.utc)
        oldest_minutes = 0.0
        for row in rows:
            created = row.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age_min = (now - created).total_seconds() / 60.0
            if age_min > oldest_minutes:
                oldest_minutes = age_min

        pending_count = len(rows)

        if pending_count == 0:
            status = MetricStatus.OK
            msg    = "승인 대기 없음"
        elif oldest_minutes >= self.thresholds.approval_age_critical_minutes:
            status = MetricStatus.CRITICAL
            msg    = "승인 대기 매우 오래됨"
        elif oldest_minutes >= self.thresholds.approval_age_warn_minutes:
            status = MetricStatus.WARN
            msg    = "승인 대기 오래됨"
        else:
            status = MetricStatus.OK
            msg    = "승인 대기 정상"

        return Metric(
            name="approval_queue",
            status=status,
            value={
                "pending_count":   pending_count,
                "oldest_age_minutes": round(oldest_minutes, 1),
            },
            threshold={
                "warn_minutes":     self.thresholds.approval_age_warn_minutes,
                "critical_minutes": self.thresholds.approval_age_critical_minutes,
            },
            message=msg,
        )

    # ---------- risk events ----------

    def collect_risk_events(self, db: Session) -> Metric:
        """EmergencyStopEvent 최근 N분 발생 개수.

        잦은 토글은 운영 이상 신호 — flapping detection.
        """
        try:
            from datetime import timedelta
            since = datetime.now(timezone.utc) - timedelta(
                minutes=self.thresholds.risk_event_window_minutes,
            )
            stmt = select(func.count()).select_from(EmergencyStopEvent).where(
                EmergencyStopEvent.created_at >= since,
            )
            count = int(db.execute(stmt).scalar() or 0)
        except Exception as exc:  # noqa: BLE001
            return Metric(
                name="risk_events", status=MetricStatus.UNKNOWN,
                message=f"EmergencyStopEvent 조회 실패: {type(exc).__name__}",
            )

        if count >= self.thresholds.risk_event_critical_count:
            status = MetricStatus.CRITICAL
            msg    = "리스크 이벤트 빈발 — 즉시 점검"
        elif count >= self.thresholds.risk_event_warn_count:
            status = MetricStatus.WARN
            msg    = "리스크 이벤트 증가 — 모니터링 권장"
        else:
            status = MetricStatus.OK
            msg    = "리스크 이벤트 정상 범위"

        return Metric(
            name="risk_events",
            status=status,
            value={
                "count":          count,
                "window_minutes": self.thresholds.risk_event_window_minutes,
            },
            threshold={
                "warn":     self.thresholds.risk_event_warn_count,
                "critical": self.thresholds.risk_event_critical_count,
            },
            message=msg,
        )

    # ---------- data freshness ----------

    def collect_data_freshness(
        self,
        *,
        provider:           str,
        stale_max_age:      int,
        sample_status:      dict | None = None,
    ) -> Metric:
        """데이터 freshness 메트릭 — provider 정보 carry.

        본 메서드는 *어댑터별* freshness 평가를 직접 수행하지 *않는다*. 호출자
        (routes_monitoring)가 freshness 모듈로 평가한 표본 결과를 carry한다.

        sample_status가 None이면 status=UNKNOWN (측정 불가).
        sample_status가 {"is_stale": bool, ...}이면 해당 값으로 결정.
        """
        if sample_status is None:
            return Metric(
                name="data_freshness", status=MetricStatus.UNKNOWN,
                value={"provider": provider, "stale_max_age_seconds": stale_max_age},
                message="시세 freshness 표본 없음 — 측정 불가",
            )

        is_stale = bool(sample_status.get("is_stale", False))
        status   = MetricStatus.WARN if is_stale else MetricStatus.OK
        msg      = "데이터 지연 주의" if is_stale else "데이터 정상"
        return Metric(
            name="data_freshness",
            status=status,
            value={
                "provider":              provider,
                "stale_max_age_seconds": stale_max_age,
                "sample":                sample_status,
            },
            message=msg,
        )

    # ---------- notification ----------

    def collect_notification(self, status_dict: dict | None) -> Metric:
        """NotificationService.status() dict를 carry해서 metric 생성.

        - enabled=False면 INFO 수준 메시지 — CRITICAL 아니다.
        - channel_configured=False지만 enabled=True면 WARN ("알림 설정 필요").
        """
        if status_dict is None:
            return Metric(
                name="notification", status=MetricStatus.UNKNOWN,
                message="알림 서비스 상태 없음",
            )

        enabled    = bool(status_dict.get("enabled", False))
        configured = bool(status_dict.get("channel_configured", False))

        if not enabled:
            return Metric(
                name="notification", status=MetricStatus.OK,
                value=status_dict,
                message="알림 비활성 (운영자가 명시적으로 꺼둠)",
            )
        if not configured:
            return Metric(
                name="notification", status=MetricStatus.WARN,
                value=status_dict,
                message="알림 설정 필요",
            )
        return Metric(
            name="notification", status=MetricStatus.OK,
            value=status_dict,
            message="알림 정상",
        )

    # ---------- snapshot ----------

    def snapshot(
        self,
        db: Session,
        *,
        notification_status: dict | None = None,
        market_provider:     str         = "mock",
        market_stale_max_age: int        = 60,
        data_sample:         dict | None = None,
    ) -> MonitoringSnapshot:
        """전체 메트릭 수집 + 알림 후보 생성.

        snapshot은 raise 하지 않는다. 개별 collector 실패는 UNKNOWN 메트릭으로
        carry — 시스템 안정성 우선.
        """
        metrics: list[Metric] = []

        # 순서: 서버 → DB → API → 주문 → 승인 → 리스크 → 데이터 → 알림.
        metrics.append(self.collect_server())
        metrics.append(self.collect_db(db))
        metrics.append(self.collect_api_error_rate())
        metrics.append(self.collect_order_failure_rate(db))
        metrics.append(self.collect_approval_queue(db))
        metrics.append(self.collect_risk_events(db))
        metrics.append(self.collect_data_freshness(
            provider=market_provider,
            stale_max_age=market_stale_max_age,
            sample_status=data_sample,
        ))
        metrics.append(self.collect_notification(notification_status))

        overall = MetricStatus.worst([m.status for m in metrics])
        alerts  = self._derive_alerts(metrics)
        return MonitoringSnapshot(
            overall=overall,
            metrics=metrics,
            alerts=alerts,
        )

    # ---------- alerts ----------

    def _derive_alerts(self, metrics: list[Metric]) -> list[AlertCandidate]:
        """WARN / CRITICAL 메트릭을 알림 후보로 변환.

        OK / UNKNOWN은 후보 미생성 — UNKNOWN은 데이터 부족이지 장애가 아니다.
        호출자가 NotificationService.notify로 실제 송신 여부 결정.
        """
        candidates: list[AlertCandidate] = []
        for m in metrics:
            if m.status not in (MetricStatus.WARN, MetricStatus.CRITICAL):
                continue
            candidates.append(AlertCandidate(
                severity=m.status.value,
                kind=m.name,
                title=f"[{m.status.value}] {m.name}",
                message=m.message,
                dedupe_key=f"monitoring:{m.name}:{m.status.value}",
            ))
        return candidates


def notify_alerts(
    service: Any,
    alerts:  list[AlertCandidate],
) -> list[dict[str, Any]]:
    """후보 알림을 NotificationService로 위임 — 실패 무시.

    `service`는 `NotificationService` 호환 객체 (`notify(event)` 메서드).
    None이면 즉시 빈 리스트 반환 — 시스템 안정성 우선.

    본 helper는 raise하지 않는다. 호출자가 monitoring health endpoint /
    background scheduler에서 마음 편히 사용할 수 있다.
    """
    if service is None or not alerts:
        return []

    try:
        from app.notifications.types import (
            NotificationEvent,
            NotificationKind,
            NotificationSeverity,
        )
    except Exception:  # noqa: BLE001
        return []

    results: list[dict[str, Any]] = []
    for cand in alerts:
        try:
            sev = (
                NotificationSeverity.CRITICAL
                if cand.severity == "CRITICAL"
                else NotificationSeverity.WARN
            )
            kind_map = {
                "data_freshness":     NotificationKind.DATA_STALE,
                "approval_queue":     NotificationKind.APPROVAL_PENDING,
                "risk_events":        NotificationKind.RISK_AUDITOR_WARN,
                "order_failure_rate": NotificationKind.REPEATED_REJECTION,
                "api_error_rate":     NotificationKind.BROKER_ERROR,
                "database":           NotificationKind.BROKER_ERROR,
                "server":             NotificationKind.BROKER_ERROR,
                "notification":       NotificationKind.TEST,
            }
            kind = kind_map.get(cand.kind, NotificationKind.TEST)
            event = NotificationEvent(
                kind=kind, severity=sev,
                title=cand.title, message=cand.message,
                dedupe_key=cand.dedupe_key,
            )
            res = service.notify(event)
            results.append(res.to_dict() if hasattr(res, "to_dict") else {"ok": True})
        except Exception as exc:  # noqa: BLE001
            results.append({
                "ok":    False,
                "error": f"{type(exc).__name__}:{exc}",
                "kind":  cand.kind,
            })
    return results
