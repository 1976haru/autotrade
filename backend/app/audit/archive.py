"""OrderAudit archival (168, MUST).

OrderAuditLog가 무한 누적되면 hot path query(/api/audit/orders, dashboard)
가 점점 느려진다. archived=True flag로 cold 분리 → hot query는 자동으로
필터.

본 모듈은 마킹 전용 — row 자체는 삭제하지 않는다. 운영자가 cold storage로
이주하거나 vacuum 결정은 별도. archive 후에도 모든 데이터는 그대로 — 사후
분석 / 회계 감사 가능성 보존.

호출 패턴:
- 운영자 명시 cron / 스크립트: `mark_orders_older_than_archived(db, days=180)`.
- 테스트: 명시적 `now`로 결정성.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.models import OrderAuditLog


def mark_orders_older_than_archived(
    db:     Session,
    *,
    days:   int,
    now:    datetime | None = None,
    dry_run: bool = False,
) -> int:
    """`days`일보다 오래된 archived=False row를 archived=True로 마크.

    반환: 영향받은 row 수. dry_run=True면 update 없이 매칭 카운트만.
    days <= 0이면 no-op (안전 측 — 0은 '검사 비활성' 의미와 일치).
    """
    if days <= 0:
        return 0
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    if dry_run:
        return int(db.query(OrderAuditLog).filter(
            OrderAuditLog.archived.is_(False),
            OrderAuditLog.created_at < cutoff,
        ).count())

    result = db.execute(
        update(OrderAuditLog)
        .where(
            OrderAuditLog.archived.is_(False),
            OrderAuditLog.created_at < cutoff,
        )
        .values(archived=True)
    )
    db.commit()
    return int(result.rowcount or 0)
