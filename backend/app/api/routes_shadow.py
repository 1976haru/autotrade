"""#43: ShadowTrade read-only surface — LIVE_SHADOW signal-only ledger.

LIVE_SHADOW 모드에서 RiskManager가 모든 주문을 REJECTED로 변환하는 동안 동시에
기록되는 ShadowTrade row(`route_order` 참고)를 운영자가 frontend에서 조회할 수
있도록 read-only endpoint 노출.

**절대 원칙 준수:**
- 새 broker 호출 0건. broker 인스턴스는 본 모듈에서 import조차 하지 않는다.
- 새 AI 실행 경로 0건.
- 새 RiskManager / PermissionGate 분기 0건.
- 본 모듈은 DB SELECT만 수행한다.

**ShadowTrade 의미:** 실제 주문이 아닌 *추정 기록*. `actual_broker_order_sent`
invariant False — broker.place_order는 LIVE_SHADOW에서 절대 호출되지 않는다.
`estimated_fill_price`는 latest_price proxy로 시작하며 실제 체결 품질과 다를 수
있다 (orderbook depth / 호가 공백 / 부분체결 / 슬리피지 미반영). UI/문서에서
이 invariant + warning을 명시한다.
"""

from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ShadowTrade
from app.db.session import get_db


router = APIRouter(prefix="/shadow", tags=["shadow"])


_VALID_WOULD_HAVE = {"APPROVED", "REJECTED"}


class ShadowTradeOut(BaseModel):
    id:                       int
    created_at:               datetime
    audit_id:                 int
    mode:                     str
    requested_by_ai:          bool
    symbol:                   str
    side:                     str
    quantity:                 int
    order_type:               str
    limit_price:              int | None = None
    latest_price:             int
    would_have_decision:      str
    would_have_reasons:       list[str]
    actual_broker_order_sent: bool
    estimated_fill_price:     int
    estimated_slippage_bps:   float
    estimation_method:        str
    confidence_note:          str | None = None
    strategy:                 str | None = None
    trade_reason:             str | None = None
    source:                   str | None = None
    client_order_id:          str | None = None


class ShadowSummaryOut(BaseModel):
    """LIVE_SHADOW 신호의 사후 분석용 요약. UI top-of-Dashboard 카드에 사용.

    `actual_broker_orders_sent`는 invariant 0 — 본 PR에서 ShadowTrade 작성
    경로 어디에서도 True로 set되지 않는다. 운영자가 0이 아닌 값을 보면 즉시
    incident — DB 조회 자체가 invariant 검증 역할.
    """
    total:                       int
    would_have_approved_count:   int
    would_have_rejected_count:   int
    by_strategy:                 dict[str, int]
    avg_estimated_slippage_bps:  float
    actual_broker_orders_sent:   int
    invariant_note:              str


def _to_out(row: ShadowTrade) -> ShadowTradeOut:
    return ShadowTradeOut(
        id=row.id,
        created_at=row.created_at,
        audit_id=row.audit_id,
        mode=row.mode,
        requested_by_ai=row.requested_by_ai,
        symbol=row.symbol,
        side=row.side,
        quantity=row.quantity,
        order_type=row.order_type,
        limit_price=row.limit_price,
        latest_price=row.latest_price,
        would_have_decision=row.would_have_decision,
        would_have_reasons=list(row.would_have_reasons or []),
        actual_broker_order_sent=row.actual_broker_order_sent,
        estimated_fill_price=row.estimated_fill_price,
        estimated_slippage_bps=row.estimated_slippage_bps,
        estimation_method=row.estimation_method,
        confidence_note=row.confidence_note,
        strategy=row.strategy,
        trade_reason=row.trade_reason,
        source=row.source,
        client_order_id=row.client_order_id,
    )


@router.get("/trades", response_model=list[ShadowTradeOut])
def list_shadow_trades(
    limit:               int = Query(50, ge=1, le=200),
    offset:              int = Query(0, ge=0),
    symbol:              str | None = Query(None, max_length=16),
    strategy:            str | None = Query(None, max_length=64),
    would_have_decision: str | None = Query(
        None, description=f"필터: one of {sorted(_VALID_WOULD_HAVE)}"
    ),
    db:                  Session = Depends(get_db),
) -> list[ShadowTradeOut]:
    """Shadow trade 목록 (created_at desc). 빈 목록은 []."""
    stmt = select(ShadowTrade).order_by(ShadowTrade.id.desc())
    if symbol:
        stmt = stmt.where(ShadowTrade.symbol == symbol)
    if strategy:
        stmt = stmt.where(ShadowTrade.strategy == strategy)
    if would_have_decision is not None and would_have_decision in _VALID_WOULD_HAVE:
        stmt = stmt.where(ShadowTrade.would_have_decision == would_have_decision)
    stmt = stmt.offset(offset).limit(limit)
    rows = db.execute(stmt).scalars().all()
    return [_to_out(r) for r in rows]


@router.get("/summary", response_model=ShadowSummaryOut)
def shadow_summary(db: Session = Depends(get_db)) -> ShadowSummaryOut:
    """Shadow trade 요약 통계. Dashboard 카드에서 1회 호출.

    actual_broker_orders_sent는 항상 0 — DB에서 True인 row가 발견되면
    invariant 위반(즉시 incident). 본 카운트가 0이 아닌 경우 운영자에게
    즉시 surface하도록 frontend에서 강조 표시.
    """
    decision_rows = db.execute(
        select(ShadowTrade.would_have_decision, func.count(ShadowTrade.id))
        .group_by(ShadowTrade.would_have_decision)
    ).all()
    decision_counts: Counter[str] = Counter()
    for decision, n in decision_rows:
        decision_counts[decision] += int(n or 0)

    strategy_rows = db.execute(
        select(ShadowTrade.strategy, func.count(ShadowTrade.id))
        .group_by(ShadowTrade.strategy)
    ).all()
    by_strategy: dict[str, int] = {}
    for strategy, n in strategy_rows:
        key = strategy if strategy else "(미명시)"
        by_strategy[key] = by_strategy.get(key, 0) + int(n or 0)

    total = sum(decision_counts.values())
    would_have_approved = decision_counts.get("APPROVED", 0)
    would_have_rejected = decision_counts.get("REJECTED", 0)

    avg_slippage_row = db.execute(
        select(func.avg(ShadowTrade.estimated_slippage_bps))
    ).scalar()
    avg_slippage = float(avg_slippage_row or 0.0)

    actual_sent = db.execute(
        select(func.count(ShadowTrade.id)).where(
            ShadowTrade.actual_broker_order_sent.is_(True)
        )
    ).scalar()
    actual_sent_count = int(actual_sent or 0)

    return ShadowSummaryOut(
        total=total,
        would_have_approved_count=would_have_approved,
        would_have_rejected_count=would_have_rejected,
        by_strategy=by_strategy,
        avg_estimated_slippage_bps=avg_slippage,
        actual_broker_orders_sent=actual_sent_count,
        invariant_note=(
            "LIVE_SHADOW 기록은 실제 주문이 아닙니다. "
            "broker.place_order 호출 0건이 invariant — "
            "actual_broker_orders_sent가 0이 아닐 경우 즉시 incident."
        ),
    )
