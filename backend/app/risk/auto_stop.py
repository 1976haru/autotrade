"""Auto emergency_stop on consecutive rejections (182, MUST).

시스템 이상(LLM bug / broker 장애 / stale price 연속 발생)이 N건 연속 REJECTED
audit row를 만들어내면 운영자 개입 없이 자동으로 emergency_stop을 트리거한다.
153 reason_code='repeated_order_failure'를 사용해 EmergencyStopEvent에 영구화.

호출 위치: route_order의 audit commit 직후. 매 호출 비용은 audit log의 마지막
N건 query 1번 — single-digit ms.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EmergencyStopEvent, OrderAuditLog


def consecutive_rejection_count(
    db:    Session,
    *,
    limit: int,
) -> int:
    """가장 최근 N건의 audit row 중 연속으로 REJECTED인 prefix 길이.

    예: [REJECTED, REJECTED, APPROVED, REJECTED, REJECTED, REJECTED] (최신순)
        → 첫 2개 (가장 최근)가 REJECTED, 3번째가 APPROVED — 카운트 = 2.

    limit ≤ 0이면 0.
    """
    if limit <= 0:
        return 0
    rows = db.execute(
        select(OrderAuditLog.decision)
        .order_by(OrderAuditLog.id.desc())
        .limit(limit)
    ).scalars().all()
    count = 0
    for decision in rows:
        if decision == "REJECTED":
            count += 1
        else:
            break
    return count


def maybe_trigger_auto_stop(
    db:                Session,
    *,
    risk,                              # RiskManager (typing 회피로 forward)
    threshold:         int,
    decided_by:        str = "system",
) -> bool:
    """threshold 이상의 연속 REJECTED 발견 시 emergency_stop 토글 + Event 작성.

    이미 emergency_stop이 ON이면 no-op (중복 trigger 방지). 토글이 발생했으면
    True 반환.
    """
    if threshold <= 0 or risk.emergency_stop:
        return False
    count = consecutive_rejection_count(db, limit=threshold)
    if count < threshold:
        return False
    # 자동 토글.
    risk.set_emergency_stop(True)
    db.add(EmergencyStopEvent(
        created_at=datetime.now(timezone.utc),
        enabled=True,
        decided_by=decided_by,
        note=f"auto-triggered after {count} consecutive REJECTED orders",
        reason_code="repeated_order_failure",
    ))
    db.commit()
    return True
