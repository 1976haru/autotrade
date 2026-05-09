"""194: read-only surface for FuturesOrderAuditLog (169).

CLAUDE.md 절대 원칙 준수:
- 새 broker 호출 0건 (`MockFuturesBroker`만 호출하는 다른 모듈이 row를 만든다).
- LIVE 활성화 0건 — 본 모듈은 SELECT만.
- `ENABLE_FUTURES_LIVE_TRADING=false` 환경에서도 UI가 mock 데이터를 보여줄 수 있도록.

#48: `/futures/margin/preview` 엔드포인트 추가 — `FuturesMarginRule` /
`LeverageLimitRule` / `LiquidationRiskRule`을 read-only로 호출해 운영자가
주문 체결 *전*에 증거금 / 레버리지 / 강제청산 위험을 사전 시뮬할 수 있다.
broker / DB 변경 0건 — 순수 산출 함수.
"""

from collections import Counter
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import FuturesOrderAuditLog
from app.db.session import get_db
from app.futures.margin_rules import (
    FuturesMarginRule,
    LeverageLimitRule,
    LiquidationRiskRule,
    MarginRuleDecision,
)
from app.futures.risk import FuturesRiskPolicy
from app.futures.types import (
    FuturesOrderRequest,
    FuturesPosition,
    FuturesPositionSide,
    FuturesSide,
    FuturesOrderType,
)

router = APIRouter(prefix="/futures", tags=["futures"])


class FuturesOrderOut(BaseModel):
    id:                int
    created_at:        datetime
    mode:              str
    contract:          str
    side:              str
    quantity:          int
    order_type:        str
    limit_price:       int | None  = None
    leverage:          float
    decision:          str
    reasons:           list
    executed:          bool
    broker_status:     str | None  = None
    filled_quantity:   int
    avg_fill_price:    int | None  = None
    margin_delta:      int
    liquidation_price: int | None  = None
    forced_liquidation: bool
    message:           str


class FuturesOrderSummary(BaseModel):
    """선물 주문 카운트 + 강제청산 수 + 누적 margin 변동.

    forced_liquidation_count는 운영자에게 가장 중요한 지표 — 0이 정상.
    """
    total:                    int
    by_decision:              dict[str, int]
    forced_liquidation_count: int
    executed_count:           int
    cumulative_margin_delta:  int


def _to_out(row: FuturesOrderAuditLog) -> FuturesOrderOut:
    return FuturesOrderOut(
        id=row.id,
        created_at=row.created_at,
        mode=row.mode,
        contract=row.contract,
        side=row.side,
        quantity=row.quantity,
        order_type=row.order_type,
        limit_price=row.limit_price,
        leverage=float(row.leverage or 1.0),
        decision=row.decision,
        reasons=list(row.reasons or []),
        executed=bool(row.executed),
        broker_status=row.broker_status,
        filled_quantity=int(row.filled_quantity or 0),
        avg_fill_price=row.avg_fill_price,
        margin_delta=int(row.margin_delta or 0),
        liquidation_price=row.liquidation_price,
        forced_liquidation=bool(row.forced_liquidation),
        message=row.message or "",
    )


@router.get("/orders", response_model=list[FuturesOrderOut])
def list_futures_orders(
    limit:    int = Query(50, ge=1, le=200),
    offset:   int = Query(0, ge=0),
    contract: str | None  = Query(None, max_length=32),
    decision: str | None  = Query(None),
    forced:   bool | None = Query(None, description="True → forced_liquidation만 / False → 일반만"),
    db:       Session = Depends(get_db),
) -> list[FuturesOrderOut]:
    stmt = select(FuturesOrderAuditLog).order_by(FuturesOrderAuditLog.id.desc())
    if contract:
        stmt = stmt.where(FuturesOrderAuditLog.contract == contract)
    if decision:
        stmt = stmt.where(FuturesOrderAuditLog.decision == decision)
    if forced is not None:
        stmt = stmt.where(FuturesOrderAuditLog.forced_liquidation == forced)
    stmt = stmt.offset(offset).limit(limit)
    return [_to_out(r) for r in db.execute(stmt).scalars().all()]


@router.get("/orders/summary", response_model=FuturesOrderSummary)
def futures_orders_summary(db: Session = Depends(get_db)) -> FuturesOrderSummary:
    decisions = db.execute(
        select(FuturesOrderAuditLog.decision, func.count(FuturesOrderAuditLog.id))
        .group_by(FuturesOrderAuditLog.decision)
    ).all()
    by_decision: dict[str, int] = {}
    total = 0
    for decision, n in decisions:
        c = int(n or 0)
        by_decision[decision] = c
        total += c
    forced = db.execute(
        select(func.count(FuturesOrderAuditLog.id))
        .where(FuturesOrderAuditLog.forced_liquidation.is_(True))
    ).scalar_one() or 0
    executed = db.execute(
        select(func.count(FuturesOrderAuditLog.id))
        .where(FuturesOrderAuditLog.executed.is_(True))
    ).scalar_one() or 0
    margin_sum = db.execute(
        select(func.coalesce(func.sum(FuturesOrderAuditLog.margin_delta), 0))
    ).scalar_one() or 0
    return FuturesOrderSummary(
        total=total,
        by_decision=Counter(by_decision),
        forced_liquidation_count=int(forced),
        executed_count=int(executed),
        cumulative_margin_delta=int(margin_sum),
    )


# ====================================================================
# #48: /futures/margin/preview — read-only margin / leverage / liquidation
# ====================================================================


class _PositionIn(BaseModel):
    """preview 요청에 carry할 기존 포지션 — broker 호출 없이 caller가 주입.

    UI는 `/api/futures/orders` 또는 broker.get_positions 결과를 그대로 본 모델
    형태로 보내면 된다.
    """
    contract:     str
    side:         Literal["LONG", "SHORT"]
    quantity:     int = Field(ge=0)
    entry_price:  int = Field(gt=0)
    market_price: int = Field(gt=0)
    margin_used:  int = Field(ge=0)


class MarginPreviewIn(BaseModel):
    """`/futures/margin/preview` 요청. broker 상태(margin_used / margin_available)
    + 후보 주문 + leverage + 기존 positions를 받는다."""

    contract:                 str
    side:                     Literal["BUY", "SELL"]
    quantity:                 int   = Field(gt=0)
    order_type:               Literal["MARKET", "LIMIT"] = "MARKET"
    limit_price:              int | None = Field(default=None, ge=0)
    mark_price:               int   = Field(gt=0)
    leverage:                 float = Field(gt=0)
    margin_used:              int   = Field(default=0, ge=0)
    margin_available:         int   = Field(default=0, ge=0)
    positions:                list[_PositionIn] = []
    contract_leverage_max:    float | None = Field(default=None, gt=0)


class _RuleOut(BaseModel):
    decision: Literal["PASS", "WARN", "BLOCK"]
    reasons:  list[str]
    warnings: list[str]
    metrics:  dict


class MarginPreviewOut(BaseModel):
    """preview 응답 — 세 Rule의 결과 + 종합 결정 + advisory metric."""
    leverage:    _RuleOut
    margin:      _RuleOut
    liquidation: _RuleOut
    overall:     Literal["PASS", "WARN", "BLOCK"]
    notice:      str


def _to_rule_out(res) -> _RuleOut:
    return _RuleOut(
        decision=res.decision.value,
        reasons=list(res.reasons),
        warnings=list(res.warnings),
        metrics=dict(res.metrics),
    )


@router.post("/margin/preview", response_model=MarginPreviewOut)
def margin_preview(payload: MarginPreviewIn) -> MarginPreviewOut:
    """주문이 체결되기 *전*에 증거금 / 레버리지 / 강제청산 위험을 read-only로 평가.

    **broker 호출 0건, DB 변경 0건, 실제 주문 0건.** 본 endpoint를 호출해도
    어떤 audit row나 ledger 변동이 발생하지 않는다 — UI 사전 시뮬 전용.
    """
    policy = FuturesRiskPolicy()  # default 정책 (ENABLE_FUTURES_LIVE_TRADING=False)
    leverage_rule = LeverageLimitRule(
        policy_max_leverage=policy.max_leverage,
        contract_leverage_max=payload.contract_leverage_max,
    )
    margin_rule = FuturesMarginRule(
        max_margin_used=policy.max_margin_used,
        maintenance_margin_pct=policy.maintenance_margin_pct,
    )
    liq_rule = LiquidationRiskRule(
        critical_pct=policy.liquidation_critical_pct,
        warning_pct=policy.liquidation_warning_pct,
        maintenance_margin_pct=policy.maintenance_margin_pct,
    )

    order = FuturesOrderRequest(
        contract=payload.contract,
        side=FuturesSide(payload.side),
        quantity=payload.quantity,
        order_type=FuturesOrderType(payload.order_type),
        limit_price=payload.limit_price,
    )
    positions = [
        FuturesPosition(
            contract=p.contract,
            side=FuturesPositionSide(p.side),
            quantity=p.quantity,
            entry_price=p.entry_price,
            market_price=p.market_price,
            margin_used=p.margin_used,
        )
        for p in payload.positions
    ]

    lev_res    = leverage_rule.check(payload.leverage)
    margin_res = margin_rule.check(
        order=order,
        margin_used=payload.margin_used,
        margin_available=payload.margin_available,
        mark_price=payload.mark_price,
        leverage=payload.leverage,
    )
    liq_res    = liq_rule.check(
        order=order, positions=positions,
        mark_price=payload.mark_price, leverage=payload.leverage,
    )

    # 종합 결정 — 가장 보수적인 결정.
    decisions = [lev_res.decision, margin_res.decision, liq_res.decision]
    if MarginRuleDecision.BLOCK in decisions:
        overall = "BLOCK"
    elif MarginRuleDecision.WARN in decisions:
        overall = "WARN"
    else:
        overall = "PASS"

    return MarginPreviewOut(
        leverage=_to_rule_out(lev_res),
        margin=_to_rule_out(margin_res),
        liquidation=_to_rule_out(liq_res),
        overall=overall,
        notice=(
            "선물 마진/레버리지/강제청산 위험을 read-only로 사전 평가합니다. "
            "broker 호출 0건, audit row 0건. 실제 주문 흐름은 별도 경로 "
            "(MockFuturesBroker / 가상 환경)에서만 작동합니다."
        ),
    )
