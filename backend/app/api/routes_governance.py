"""Governance routes (#27).

CLAUDE.md 절대 원칙 — 본 라우트는 *판단 결과*만 반환한다. 실제 모드 변경,
broker 호출, LIVE flag 변경 0건. DB write 0건.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

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
