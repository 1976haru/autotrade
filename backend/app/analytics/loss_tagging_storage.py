"""LossReasonLog DB helpers (#79).

본 모듈은 LossReasonLog 테이블에 *append* 와 *review 갱신* 만 수행한다 —
삭제 / 원본 mutate 0건.

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order / 외부 HTTP / AI provider import 0건.
- DELETE / row 원본 필드 mutate 0건 — 본 helper 는 review_* 컬럼만 update.
- 본 helper 는 LossReasonLog 외 다른 테이블을 *변경하지 않는다*.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.loss_tagging import LossEstimateResult, category_of
from app.db.models import LossReasonLog


def _utc(ts: datetime | None) -> datetime:
    if ts is None:
        return datetime.now(timezone.utc)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def append_loss_reason_log(
    db: Session,
    result: LossEstimateResult,
    *,
    source_table: str,
    source_id:    int | None = None,
    strategy:     str | None = None,
    mode:         str | None = None,
) -> LossReasonLog:
    """추정 결과를 LossReasonLog 에 append. 본 함수는 *생성* 만 — 갱신/삭제 X.

    is_loss=False 면 row 작성 *안 함* — 손실 아닌 거래는 태깅 대상이 아니다.
    """
    if not result.is_loss:
        # 손실 아니면 저장 안 함 — caller 에게 None 반환.
        return None  # type: ignore[return-value]

    row = LossReasonLog(
        source_table=source_table,
        source_id=source_id,
        symbol=result.symbol,
        strategy=strategy,
        mode=mode,
        trade_pnl=result.trade_pnl,
        is_loss=True,
        primary_tag=(result.primary_tag.value if result.primary_tag else None),
        primary_category=(
            category_of(result.primary_tag).value if result.primary_tag else None
        ),
        tags=[t.value for t in result.tags],
        rationale=list(result.rationale),
        confidence=int(result.confidence),
        is_estimated=True,  # invariant
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def review_loss_reason_log(
    db: Session,
    *,
    log_id: int,
    review_status: str,
    reviewed_by: str | None = None,
    review_note: str | None = None,
) -> LossReasonLog | None:
    """운영자가 "추정 맞음/아님" review 추가. 본 row 의 *추정 데이터*는
    *변경하지 않는다* — 오직 review_* 컬럼만 갱신.

    review_status 권장 값: "agreed" / "disagreed" / "needs_more_data" /
    "investigating".
    """
    stmt = select(LossReasonLog).where(LossReasonLog.id == int(log_id))
    row = db.execute(stmt).scalar_one_or_none()
    if row is None:
        return None
    row.review_status = (review_status or "")[:16]
    row.reviewed_by   = (reviewed_by or "")[:64] or None
    row.review_note   = (review_note or "")[:500] or None
    row.reviewed_at   = _utc(None)
    db.commit()
    db.refresh(row)
    return row


def list_recent_loss_reasons(
    db: Session,
    *,
    limit: int = 50,
    strategy: str | None = None,
    symbol:   str | None = None,
) -> list[LossReasonLog]:
    """최근 손실 태그 목록. read-only — 본 함수는 add/commit/delete 호출 0건."""
    stmt = select(LossReasonLog).where(LossReasonLog.is_loss.is_(True))
    if strategy:
        stmt = stmt.where(LossReasonLog.strategy == strategy)
    if symbol:
        stmt = stmt.where(LossReasonLog.symbol == symbol)
    stmt = stmt.order_by(LossReasonLog.created_at.desc()).limit(max(1, int(limit)))
    return list(db.execute(stmt).scalars())


def summarize_loss_reasons(
    db: Session,
    *,
    days: int = 7,
    strategy: str | None = None,
) -> dict[str, Any]:
    """집계 — 태그별 발생 횟수 + 손익 합. read-only."""
    from datetime import timedelta
    cutoff = _utc(None) - timedelta(days=max(1, days))

    stmt = select(LossReasonLog).where(
        LossReasonLog.is_loss.is_(True),
        LossReasonLog.created_at >= cutoff,
    )
    if strategy:
        stmt = stmt.where(LossReasonLog.strategy == strategy)
    rows: Iterable[LossReasonLog] = db.execute(stmt).scalars()

    tag_bucket: dict[str, dict[str, Any]] = {}
    cat_bucket: dict[str, int] = {}
    strategy_bucket: dict[str, dict[str, Any]] = {}
    total = 0
    pnl_sum = 0
    primary_count: dict[str, int] = {}

    for r in rows:
        total += 1
        pnl_sum += int(r.trade_pnl or 0)
        if r.primary_tag:
            primary_count[r.primary_tag] = primary_count.get(r.primary_tag, 0) + 1
        if r.primary_category:
            cat_bucket[r.primary_category] = cat_bucket.get(r.primary_category, 0) + 1
        for tag in (r.tags or []):
            slot = tag_bucket.setdefault(tag, {"count": 0, "pnl_sum": 0})
            slot["count"]   += 1
            slot["pnl_sum"] += int(r.trade_pnl or 0)
        if r.strategy:
            s = strategy_bucket.setdefault(
                r.strategy, {"count": 0, "pnl_sum": 0, "top_tags": {}},
            )
            s["count"]   += 1
            s["pnl_sum"] += int(r.trade_pnl or 0)
            if r.primary_tag:
                s["top_tags"][r.primary_tag] = (
                    s["top_tags"].get(r.primary_tag, 0) + 1
                )

    top_tags = sorted(
        ({"tag": k, **v} for k, v in tag_bucket.items()),
        key=lambda d: (-d["count"], d["tag"]),
    )

    top_primary = sorted(
        ({"tag": k, "count": v} for k, v in primary_count.items()),
        key=lambda d: (-d["count"], d["tag"]),
    )

    by_strategy = []
    for name, agg in strategy_bucket.items():
        top = sorted(
            ({"tag": k, "count": v} for k, v in agg["top_tags"].items()),
            key=lambda d: (-d["count"], d["tag"]),
        )
        by_strategy.append({
            "strategy": name, "count": agg["count"],
            "pnl_sum": agg["pnl_sum"], "top_tags": top[:5],
        })
    by_strategy.sort(key=lambda x: -x["count"])

    return {
        "days":          days,
        "loss_count":    total,
        "pnl_sum":       pnl_sum,
        "top_tags":      top_tags[:10],
        "top_primary":   top_primary[:10],
        "by_category":   cat_bucket,
        "by_strategy":   by_strategy,
        "is_estimated":  True,
        "note": (
            "본 요약은 *추정* 손실 원인입니다. 확정 원인이 아니며 "
            "운영자 검토가 필요합니다. 태그를 투자 조언으로 사용 금지."
        ),
    }
