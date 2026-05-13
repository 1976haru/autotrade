"""Live Manual 운영 로그 collector (#73) — read-only.

CLAUDE.md 절대 원칙:
- 본 모듈은 *조회만* 한다 — INSERT/UPDATE/DELETE 0건 (정적 grep 가드).
- broker / OrderExecutor / route_order / 외부 HTTP / AI SDK import 0건.
- `ENABLE_LIVE_TRADING` 등 안전 플래그 mutate 0건 (settings.* 미사용).

`summarize_live_manual_period(db, start, end)` 가 LIVE_MANUAL_APPROVAL 모드
운영 기간 동안의 OrderAuditLog + PendingApproval 흐름을 집계해 dict로 반환.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    EmergencyStopEvent,
    OrderAuditLog,
    PendingApproval,
)


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _is_live_manual(mode: str | None) -> bool:
    return bool(mode and mode.upper() == "LIVE_MANUAL_APPROVAL")


def summarize_live_manual_period(
    db: Session,
    *,
    start_date: datetime,
    end_date:   datetime,
) -> dict[str, Any]:
    """LIVE_MANUAL_APPROVAL 운영 기간 요약.

    return dict 필드:
    - total_live_manual_orders : LIVE_MANUAL_APPROVAL 모드의 OrderAuditLog row 수
    - approved_orders          : decision=APPROVED
    - needs_approval_orders    : decision=NEEDS_APPROVAL (큐 진입)
    - rejected_orders          : decision=REJECTED
    - approved_via_queue       : PendingApproval.status=APPROVED 의 count
    - expired_or_cancelled     : PendingApproval.status ∈ {EXPIRED, CANCELLED}
    - approval_bypass_attempts : APPROVED+executed 인데 NEEDS_APPROVAL 큐를 거치지
      않은 audit row 수 — *비정상 우회 의심* (정상 LIVE_MANUAL_APPROVAL 흐름은
      모든 주문이 큐를 거쳐야 함)
    - emergency_stops_in_period: 기간 내 EmergencyStopEvent count
    - operating_days           : APPROVED 주문이 발생한 영업일 수

    본 함수는 broker / OrderExecutor / 외부 API 호출 0건. DB SELECT only.
    """
    start = _utc(start_date)
    end   = _utc(end_date)

    # ---- 1) LIVE_MANUAL_APPROVAL audit rows ----
    stmt = select(OrderAuditLog).where(
        OrderAuditLog.created_at >= start,
        OrderAuditLog.created_at <= end,
    )
    rows = [r for r in db.execute(stmt).scalars() if _is_live_manual(r.mode)]

    approved        = [r for r in rows if str(r.decision).upper() == "APPROVED"]
    needs_approval  = [r for r in rows if str(r.decision).upper() == "NEEDS_APPROVAL"]
    rejected        = [r for r in rows if str(r.decision).upper() == "REJECTED"]

    # ---- 2) PendingApproval rows in period ----
    pa_stmt = select(PendingApproval).where(
        PendingApproval.created_at >= start,
        PendingApproval.created_at <= end,
    )
    pa_rows = [r for r in db.execute(pa_stmt).scalars()
               if r.mode and r.mode.upper() == "LIVE_MANUAL_APPROVAL"]
    pa_approved   = [r for r in pa_rows if str(r.status).upper() == "APPROVED"]
    pa_expired    = [r for r in pa_rows if str(r.status).upper() in ("EXPIRED", "CANCELLED")]
    pa_audit_ids  = {r.audit_id for r in pa_rows}

    # ---- 3) 우회 시도 — APPROVED + executed 인데 PendingApproval 큐를 거치지 않은 row ----
    bypass = [
        r for r in approved
        if r.executed and r.id not in pa_audit_ids
    ]

    # ---- 4) 기간 내 emergency stop event ----
    es_stmt = select(EmergencyStopEvent).where(
        EmergencyStopEvent.created_at >= start,
        EmergencyStopEvent.created_at <= end,
    )
    emergency_count = sum(1 for _ in db.execute(es_stmt).scalars())

    # ---- 5) 운영 일수 — APPROVED 주문 발생 일수 ----
    operating_days = len({_utc(r.created_at).date() for r in approved})

    return {
        "period_start":              start.isoformat(),
        "period_end":                end.isoformat(),
        "total_live_manual_orders":  len(rows),
        "approved_orders":           len(approved),
        "needs_approval_orders":     len(needs_approval),
        "rejected_orders":           len(rejected),
        "pending_approval_rows":     len(pa_rows),
        "approved_via_queue":        len(pa_approved),
        "expired_or_cancelled":      len(pa_expired),
        "approval_bypass_attempts":  len(bypass),
        "emergency_stops_in_period": int(emergency_count),
        "operating_days":            int(operating_days),
        # 본 collector는 system_errors / audit_missing 같은 외부 신호는 추적하지
        # 않는다 — 호출자가 모니터링(#70)에서 별도 carry.
    }
