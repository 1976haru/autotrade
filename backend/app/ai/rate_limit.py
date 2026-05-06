"""AI proposal rate limit (161, MUST).

지능형 에이전트의 flooding 방어. LLM bug / 무한 루프 / 동일 신호 반복으로
같은 (strategy, symbol)에 대해 짧은 시간 내 다수 제안이 만들어지는 사고를
audit log를 walk해 사전 차단한다.

설계 결정:
- 키: (strategy, symbol). 같은 전략의 같은 종목은 한 곳에서 묶어 카운트.
  side(BUY/SELL)를 키에 포함하지 않는 이유는 BUY-SELL 빠른 교대도 의심 신호.
- 윈도우: 슬라이딩(rolling). 마지막 N초 안의 audit row 카운트.
- 카운트 대상: requested_by_ai=True. 결정(APPROVED/REJECTED/NEEDS_APPROVAL)과
  체결 여부 무관 — '에이전트가 만들었다'는 사실 자체가 카운트 단위.
- 임계 0이면 비활성 (backwards compat).

Note: 본 모듈은 read-only 함수. 카운트가 임계를 넘었을 때 어떻게 처리할지
(REJECTED audit row 작성, 에이전트에 backoff 신호 등)는 caller 책임.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import OrderAuditLog


def count_recent_ai_proposals(
    db:               Session,
    *,
    strategy:         str | None,
    symbol:           str,
    window_seconds:   int,
    now:              datetime | None = None,
) -> int:
    """지난 `window_seconds` 동안의 AI 제안 카운트.

    `strategy=None`인 경우는 NULL 매칭 — 이론상 가능하지만 정상 흐름에서는
    AI 주문은 항상 strategy를 가진다. defensive하게 NULL도 매칭.
    """
    if window_seconds <= 0:
        return 0
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=window_seconds)

    stmt = (
        select(func.count(OrderAuditLog.id))
        .where(
            OrderAuditLog.requested_by_ai.is_(True),
            OrderAuditLog.symbol == symbol,
            OrderAuditLog.created_at > cutoff,
        )
    )
    if strategy is None:
        stmt = stmt.where(OrderAuditLog.strategy.is_(None))
    else:
        stmt = stmt.where(OrderAuditLog.strategy == strategy)

    return int(db.execute(stmt).scalar() or 0)


def check_rate_limit(
    db:               Session,
    *,
    strategy:         str | None,
    symbol:           str,
    window_seconds:   int,
    max_count:        int,
    now:              datetime | None = None,
) -> tuple[bool, int]:
    """rate limit 검사. (within_limit, current_count) 반환.

    `max_count <= 0`이면 검사 비활성 — 무조건 (True, 0).
    `current_count >= max_count`이면 (False, count). caller가 차단.
    """
    if max_count <= 0 or window_seconds <= 0:
        return True, 0
    count = count_recent_ai_proposals(
        db, strategy=strategy, symbol=symbol,
        window_seconds=window_seconds, now=now,
    )
    return count < max_count, count


# ---------- 177: global rate limit (all orders) ----------

def count_recent_orders(
    db:              Session,
    *,
    window_seconds:  int,
    now:             datetime | None = None,
) -> int:
    """지난 `window_seconds` 동안의 모든 OrderAuditLog 행 카운트 (requested_by_ai
    무관). 161의 AI-specific 카운트와 분리 — 시스템 전체 주문 빈도 가드용."""
    if window_seconds <= 0:
        return 0
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=window_seconds)
    from sqlalchemy import func as _func
    return int(
        db.execute(
            select(_func.count(OrderAuditLog.id))
            .where(OrderAuditLog.created_at > cutoff)
        ).scalar() or 0
    )


def check_global_rate_limit(
    db:              Session,
    *,
    window_seconds:  int,
    max_count:       int,
    now:             datetime | None = None,
) -> tuple[bool, int]:
    """177: 모든 주문 종류(strategy / AI / manual) 통합 rate limit. AI 전용
    161과 별개로 시스템 전체 주문 빈도 한도. caller가 차단."""
    if max_count <= 0 or window_seconds <= 0:
        return True, 0
    count = count_recent_orders(db, window_seconds=window_seconds, now=now)
    return count < max_count, count
