"""Analytics routes (#79).

CLAUDE.md 절대 원칙:
- 본 라우트는 *판단 / 추정 결과*만 반환한다 — broker / OrderExecutor /
  route_order 호출 0건.
- LIVE flag / mode 변경 0건.
- 손실 태그는 *추정값*이며 *확정 원인이 아니다*. 응답에 invariant 명시.
- DELETE 엔드포인트 *없음* — append + review 만.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.analytics.loss_tagging import (
    LossEstimateInput,
    LossEstimateResult,
    estimate_loss_reasons,
)
from app.analytics.loss_tagging_storage import (
    append_loss_reason_log,
    list_recent_loss_reasons,
    review_loss_reason_log,
    summarize_loss_reasons,
)
from app.db.session import get_db


router = APIRouter(prefix="/analytics", tags=["analytics"])


# ---------- estimate ----------


class LossEstimatePayload(BaseModel):
    symbol:                          str  = Field(..., min_length=1, max_length=16)
    side:                            str  = "BUY"
    entry_price:                     float
    exit_price:                      float
    quantity:                        int

    # strategy / pattern.
    stop_price:                      float | None = None
    target_price:                    float | None = None
    entry_vwap:                      float | None = None
    hold_minutes:                    int | None   = None
    failed_breakout_pattern:         bool = False
    false_rebreak_pattern:           bool = False
    reverse_signal_at_exit:          bool = False
    time_stop_threshold_minutes:     int  = 180

    # execution.
    entry_volume:                    int | None = None
    exit_volume:                     int | None = None
    slippage_bps:                    float | None = None
    partial_fill_ratio:              float | None = None
    gap_ratio:                       float | None = None

    # market.
    kospi_return:                    float | None = None
    sector_return:                   float | None = None
    regime_at_entry:                 str | None = None
    regime_at_exit:                  str | None = None
    volatility_pct:                  float | None = None

    # risk.
    daily_loss_limit_breached:       bool = False
    emergency_stop_active:           bool = False
    over_exposure:                   bool = False

    # data.
    data_stale_at_entry:             bool = False
    data_stale_at_exit:              bool = False
    bad_quote_count:                 int  = 0
    missing_bar_count:               int  = 0

    # agent.
    ai_entry_confidence:             int | None = None
    news_theme_active_at_entry:      bool = False
    news_theme_faded_at_exit:        bool = False

    # 저장 옵션 — true면 LossReasonLog 에 append.
    persist:                         bool = False
    source_table:                    str  = "manual"
    source_id:                       int | None = None
    strategy:                        str | None = None
    mode:                            str | None = None


class LossEstimateResultPayload(BaseModel):
    symbol:                  str
    is_loss:                 bool
    trade_pnl:               int
    tags:                    list[str]
    categories:              list[str]
    primary_tag:             str | None
    primary_category:        str | None
    confidence:              int
    rationale:               list[str]
    is_estimated:            bool = Field(True,  description="invariant — 본 결과는 *추정값*")
    is_order_signal:         bool = Field(False, description="invariant — 주문 신호 X")
    is_investment_advice:    bool = Field(False, description="invariant — 투자 조언 X")
    live_flag_changed:       bool = Field(False)
    mode_changed:            bool = Field(False)
    persisted_log_id:        int | None = None
    generated_at:            datetime


@router.post("/loss-tags/estimate", response_model=LossEstimateResultPayload)
def estimate_loss_tags(
    payload: LossEstimatePayload,
    db: Session = Depends(get_db),
) -> LossEstimateResultPayload:
    """단일 거래 손실 원인 *추정*.

    `persist=true` 면 LossReasonLog 에 append (`source_table` / `source_id` 필수
    권장). 손실 아닌 거래는 저장 안 함.
    """
    try:
        inp = LossEstimateInput(
            symbol=payload.symbol,
            side=payload.side,
            entry_price=payload.entry_price,
            exit_price=payload.exit_price,
            quantity=payload.quantity,
            stop_price=payload.stop_price,
            target_price=payload.target_price,
            entry_vwap=payload.entry_vwap,
            hold_minutes=payload.hold_minutes,
            failed_breakout_pattern=payload.failed_breakout_pattern,
            false_rebreak_pattern=payload.false_rebreak_pattern,
            reverse_signal_at_exit=payload.reverse_signal_at_exit,
            time_stop_threshold_minutes=payload.time_stop_threshold_minutes,
            entry_volume=payload.entry_volume,
            exit_volume=payload.exit_volume,
            slippage_bps=payload.slippage_bps,
            partial_fill_ratio=payload.partial_fill_ratio,
            gap_ratio=payload.gap_ratio,
            kospi_return=payload.kospi_return,
            sector_return=payload.sector_return,
            regime_at_entry=payload.regime_at_entry,
            regime_at_exit=payload.regime_at_exit,
            volatility_pct=payload.volatility_pct,
            daily_loss_limit_breached=payload.daily_loss_limit_breached,
            emergency_stop_active=payload.emergency_stop_active,
            over_exposure=payload.over_exposure,
            data_stale_at_entry=payload.data_stale_at_entry,
            data_stale_at_exit=payload.data_stale_at_exit,
            bad_quote_count=payload.bad_quote_count,
            missing_bar_count=payload.missing_bar_count,
            ai_entry_confidence=payload.ai_entry_confidence,
            news_theme_active_at_entry=payload.news_theme_active_at_entry,
            news_theme_faded_at_exit=payload.news_theme_faded_at_exit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid loss estimate input: {e}")

    result: LossEstimateResult = estimate_loss_reasons(inp)
    persisted_id: int | None = None
    if payload.persist and result.is_loss:
        row = append_loss_reason_log(
            db, result,
            source_table=payload.source_table or "manual",
            source_id=payload.source_id,
            strategy=payload.strategy,
            mode=payload.mode,
        )
        persisted_id = int(row.id) if row is not None else None

    data = result.to_dict()
    data["persisted_log_id"] = persisted_id
    return LossEstimateResultPayload(**data)


# ---------- summary ----------


class LossReasonSummaryResultPayload(BaseModel):
    days:          int
    loss_count:    int
    pnl_sum:       int
    top_tags:      list[dict]
    top_primary:   list[dict]
    by_category:   dict[str, int]
    by_strategy:   list[dict]
    is_estimated:  bool = Field(True, description="invariant — *추정* 자료")
    note:          str


@router.get(
    "/loss-tags/summary",
    response_model=LossReasonSummaryResultPayload,
)
def loss_tags_summary(
    days:     int = Query(default=7, ge=1, le=365),
    strategy: str | None = Query(default=None, max_length=64),
    db: Session = Depends(get_db),
) -> LossReasonSummaryResultPayload:
    """기간 내 손실 태그 집계 (read-only)."""
    return LossReasonSummaryResultPayload(
        **summarize_loss_reasons(db, days=days, strategy=strategy),
    )


# ---------- recent ----------


class LossReasonLogPayload(BaseModel):
    id:                int
    created_at:        datetime
    source_table:      str
    source_id:         int | None
    symbol:            str
    strategy:          str | None
    mode:              str | None
    trade_pnl:         int
    is_loss:           bool
    primary_tag:       str | None
    primary_category:  str | None
    tags:              list[str]
    rationale:         list[str]
    confidence:        int
    is_estimated:      bool = Field(True, description="invariant — *추정* row")
    review_status:     str | None
    reviewed_by:       str | None
    review_note:       str | None
    reviewed_at:       datetime | None


class LossReasonRecentResultPayload(BaseModel):
    items:        list[LossReasonLogPayload]
    is_estimated: bool = Field(True, description="invariant — *추정* 자료")
    note:         str


@router.get(
    "/loss-tags/recent",
    response_model=LossReasonRecentResultPayload,
)
def loss_tags_recent(
    limit:    int = Query(default=50, ge=1, le=500),
    strategy: str | None = Query(default=None, max_length=64),
    symbol:   str | None = Query(default=None, max_length=16),
    db: Session = Depends(get_db),
) -> LossReasonRecentResultPayload:
    rows = list_recent_loss_reasons(
        db, limit=limit, strategy=strategy, symbol=symbol,
    )
    return LossReasonRecentResultPayload(
        items=[
            LossReasonLogPayload(
                id=r.id,
                created_at=r.created_at,
                source_table=r.source_table,
                source_id=r.source_id,
                symbol=r.symbol,
                strategy=r.strategy,
                mode=r.mode,
                trade_pnl=r.trade_pnl,
                is_loss=r.is_loss,
                primary_tag=r.primary_tag,
                primary_category=r.primary_category,
                tags=list(r.tags or []),
                rationale=list(r.rationale or []),
                confidence=int(r.confidence or 0),
                is_estimated=True,
                review_status=r.review_status,
                reviewed_by=r.reviewed_by,
                review_note=r.review_note,
                reviewed_at=r.reviewed_at,
            )
            for r in rows
        ],
        note="본 목록은 *추정* 손실 원인이며 확정 원인이 아닙니다.",
    )


# ---------- review (operator note) ----------


class LossReasonReviewPayload(BaseModel):
    review_status: str = Field(..., max_length=16)
    reviewed_by:   str | None = Field(default=None, max_length=64)
    review_note:   str | None = Field(default=None, max_length=500)


@router.patch(
    "/loss-tags/{log_id}/review",
    response_model=LossReasonLogPayload,
)
def loss_tags_review(
    log_id: int,
    payload: LossReasonReviewPayload,
    db: Session = Depends(get_db),
) -> LossReasonLogPayload:
    """운영자가 "추정 맞음/아님" review 추가.

    *원본 추정 데이터는 갱신되지 않는다* — review_* 컬럼만 update. 삭제 API
    없음.
    """
    row = review_loss_reason_log(
        db,
        log_id=log_id,
        review_status=payload.review_status,
        reviewed_by=payload.reviewed_by,
        review_note=payload.review_note,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="loss reason log not found")
    return LossReasonLogPayload(
        id=row.id,
        created_at=row.created_at,
        source_table=row.source_table,
        source_id=row.source_id,
        symbol=row.symbol,
        strategy=row.strategy,
        mode=row.mode,
        trade_pnl=row.trade_pnl,
        is_loss=row.is_loss,
        primary_tag=row.primary_tag,
        primary_category=row.primary_category,
        tags=list(row.tags or []),
        rationale=list(row.rationale or []),
        confidence=int(row.confidence or 0),
        is_estimated=True,
        review_status=row.review_status,
        reviewed_by=row.reviewed_by,
        review_note=row.review_note,
        reviewed_at=row.reviewed_at,
    )
