"""Governance routes (#27, #72).

CLAUDE.md 절대 원칙 — 본 라우트는 *판단 결과*만 반환한다. 실제 모드 변경,
broker 호출, LIVE flag 변경 0건. DB write 0건.

#72: Paper Gate evaluator endpoint 추가 — `/governance/paper-gate/evaluate`.
PASS 라벨은 *Live Manual Approval 검토 가능* 을 의미하며 **실거래 자동 허가가
아니다**. 본 endpoint도 안전 플래그 변경 0건.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.governance.paper_gate import (
    PaperGateInput,
    PaperGateResult,
    evaluate_paper_gate,
)
from app.governance.strategy_promotion import (
    PromotionInput,
    PromotionResult,
    PromotionStage,
    evaluate_promotion,
)


router = APIRouter(prefix="/governance", tags=["governance"])


# ---------- DTO ----------


class PromotionInputPayload(BaseModel):
    strategy_name:   str = Field(..., min_length=1, max_length=64)
    current_stage:   str
    target_stage:    str

    trade_count:           int   = 0
    expectancy:            float = 0.0
    profit_factor:         float | None = None
    max_drawdown:          int   = 0
    max_consecutive_losses: int  = 0
    win_rate:              float = 0.0
    initial_cash:          int   = 10_000_000
    cost_adjusted:         bool  = False
    slippage_adjusted:     bool  = False

    walk_forward_passed:        bool | None = None
    walk_forward_recommendation: str | None = None
    positive_fold_ratio:        float | None = None
    holdout_pnl:                int | None = None
    single_best_fold_pnl_share: float | None = None

    monte_carlo_run:        bool = False
    monte_carlo_risk_of_ruin: float | None = None
    monte_carlo_worst_5pct_mdd: int | None = None
    monte_carlo_longest_losing_streak: int | None = None

    data_quality_score:     float | None = None
    data_quality_grade:     str | None = None

    shadow_days:                  int = 0
    paper_days:                   int = 0
    live_manual_days:             int = 0
    daily_loss_limit_violations:  int = 0
    risk_policy_violations:       int = 0
    audit_log_missing_count:      int = 0
    partial_fill_audit_ok:        bool = True

    human_approved:    bool = False
    ai_recommended:    bool = False
    ai_recommendation_accuracy: float | None = None


class PromotionResultPayload(BaseModel):
    strategy_name:   str
    current_stage:   str
    target_stage:    str
    decision:        str
    failed_criteria: list[str]
    cautions:        list[str]
    warnings:        list[str]
    required_actions: list[str]
    passed_criteria: list[str]
    mode_changed:    bool = Field(default=False, description="invariant — 본 endpoint는 모드 미변경")
    live_flag_changed: bool = Field(default=False, description="invariant — LIVE flag 미변경")


@router.post("/strategy-promotion/evaluate", response_model=PromotionResultPayload)
def evaluate(payload: PromotionInputPayload) -> PromotionResultPayload:
    """Strategy Promotion Gate 평가. read-only — DB write / 모드 변경 / broker 호출 0건."""
    try:
        current = PromotionStage(payload.current_stage)
        target  = PromotionStage(payload.target_stage)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"unknown stage: {e}")

    inp_kwargs = payload.model_dump()
    inp_kwargs["current_stage"] = current
    inp_kwargs["target_stage"]  = target

    inp = PromotionInput(**inp_kwargs)
    result: PromotionResult = evaluate_promotion(inp)
    return PromotionResultPayload(**result.to_dict())


# ---------- #72 Paper Gate ----------


class PaperGateInputPayload(BaseModel):
    """Paper Gate 평가 입력. 본 엔드포인트는 read-only 판단만 수행한다.

    `period_start` / `period_end` 미지정 시 backend가 last 28 days 자동 적용.
    """
    strategy_name:                 str   = Field(..., min_length=1, max_length=64)
    period_start:                  datetime | None = None
    period_end:                    datetime | None = None

    trade_count:                   int   = 0
    active_days:                   int   = 0
    winning_pnl_sum:               int   = 0
    losing_pnl_sum:                int   = 0
    expectancy:                    float = 0.0
    max_drawdown_value:            int   = 0
    initial_cash:                  int   = 10_000_000

    loss_limit_violations:         int   = 0
    audit_missing_count:           int   = 0
    stale_or_duplicate_violations: int   = 0
    rejection_rate:                float = 0.0

    best_day_pnl_share:            float | None = None
    hourly_loss_top_share:         float | None = None
    paper_vs_backtest_pf_drift:    float | None = None
    fill_polling_consistent:       bool  = True
    client_order_id_idempotent:    bool  = True


class PaperGateResultPayload(BaseModel):
    strategy_name:           str
    period_start:            datetime
    period_end:              datetime
    verdict:                 str
    passed_criteria:         list[str]
    failed_criteria:         list[str]
    cautions:                list[str]
    metrics:                 dict
    thresholds:              dict
    next_step:               str
    is_live_authorization:   bool = Field(False, description="invariant — 항상 false (PASS != 실거래 허가)")
    is_order_signal:         bool = Field(False, description="invariant — Paper Gate는 BUY/SELL/HOLD 신호 아님")
    live_flag_changed:       bool = Field(False, description="invariant — LIVE flag 미변경")
    mode_changed:            bool = Field(False, description="invariant — mode 미변경")
    generated_at:            datetime


@router.post("/paper-gate/evaluate", response_model=PaperGateResultPayload)
def evaluate_paper_gate_endpoint(
    payload: PaperGateInputPayload,
) -> PaperGateResultPayload:
    """Paper Gate 평가. read-only — broker / DB write / LIVE flag mutate 0건.

    PASS verdict는 Live Manual Approval *검토 가능* 을 의미하며 **실거래 자동
    허가가 아니다** — 별도 옵트인 PR + 사용자 명시 승인 후에만 LIVE 진입.
    """
    from datetime import timedelta

    end   = payload.period_end   or datetime.now(timezone.utc)
    start = payload.period_start or (end - timedelta(days=28))

    try:
        inp = PaperGateInput(
            strategy_name=payload.strategy_name,
            period_start=start,
            period_end=end,
            trade_count=payload.trade_count,
            active_days=payload.active_days,
            winning_pnl_sum=payload.winning_pnl_sum,
            losing_pnl_sum=payload.losing_pnl_sum,
            expectancy=payload.expectancy,
            max_drawdown_value=payload.max_drawdown_value,
            initial_cash=payload.initial_cash,
            loss_limit_violations=payload.loss_limit_violations,
            audit_missing_count=payload.audit_missing_count,
            stale_or_duplicate_violations=payload.stale_or_duplicate_violations,
            rejection_rate=payload.rejection_rate,
            best_day_pnl_share=payload.best_day_pnl_share,
            hourly_loss_top_share=payload.hourly_loss_top_share,
            paper_vs_backtest_pf_drift=payload.paper_vs_backtest_pf_drift,
            fill_polling_consistent=payload.fill_polling_consistent,
            client_order_id_idempotent=payload.client_order_id_idempotent,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid paper gate input: {e}")

    result: PaperGateResult = evaluate_paper_gate(inp)
    return PaperGateResultPayload(**result.to_dict())
