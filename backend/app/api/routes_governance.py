"""Governance routes (#27, #72, #73).

CLAUDE.md 절대 원칙 — 본 라우트는 *판단 결과*만 반환한다. 실제 모드 변경,
broker 호출, LIVE flag 변경 0건. DB write 0건.

#72 Paper Gate         : `/governance/paper-gate/evaluate`
#73 Live Manual Gate   : `/governance/live-manual-gate/evaluate`
                         `/governance/live-manual-gate/period-summary`

PASS 라벨은 *진입 검토 가능* 을 의미하며 **실거래 자동 허가가 아니다**.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.governance.live_manual_gate import (
    LiveManualGateInput,
    LiveManualGateResult,
    evaluate_live_manual_gate,
)
from app.governance.live_manual_gate_collector import summarize_live_manual_period
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


# ---------- #73 Live Manual Gate ----------


class LiveManualGateInputPayload(BaseModel):
    """Live Manual Gate 평가 입력.

    안전 플래그 / opt-in / 한도 / 운영 로그를 *입력으로 받아*만 사용. 본
    endpoint는 어떤 설정값도 *변경하지 않는다* — 운영자가 입력으로 *현재값* 만
    전달.
    """
    strategy_name:                 str  = Field(..., min_length=1, max_length=64)
    period_start:                  datetime | None = None
    period_end:                    datetime | None = None

    paper_gate_passed:             bool = False
    promotion_gate_passed:         bool = False
    user_explicit_opt_in:          bool = False
    approval_required:             bool = False
    ai_execution_enabled:          bool = False
    futures_live_enabled:          bool = False
    enable_live_trading:           bool = False

    current_max_order_notional_krw: int = 0
    current_max_daily_loss_krw:     int = 0
    current_max_open_positions:     int = 0
    allowed_symbols:                list[str] = Field(default_factory=list)

    operating_days:                 int = 0
    total_live_manual_orders:       int = 0
    approved_orders:                int = 0
    rejected_orders:                int = 0
    expired_or_cancelled_orders:    int = 0
    approval_bypass_attempts:       int = 0
    audit_missing_count:            int = 0
    system_errors:                  int = 0
    emergency_stops_in_period:      int = 0


class LiveManualGateResultPayload(BaseModel):
    strategy_name:           str
    period_start:            datetime
    period_end:              datetime
    verdict:                 str
    passed_criteria:         list[str]
    blocked_criteria:        list[str]
    cautions:                list[str]
    required_actions:        list[str]
    metrics:                 dict
    thresholds:              dict
    next_step:               str
    is_live_authorization:   bool = Field(False, description="invariant — 항상 false (PASS != 실거래 허가)")
    is_order_signal:         bool = Field(False, description="invariant — Live Manual Gate는 BUY/SELL/HOLD 신호 아님")
    live_flag_changed:       bool = Field(False, description="invariant — 안전 플래그 미변경")
    mode_changed:            bool = Field(False, description="invariant — 모드 미변경")
    generated_at:            datetime


@router.post("/live-manual-gate/evaluate", response_model=LiveManualGateResultPayload)
def evaluate_live_manual_gate_endpoint(
    payload: LiveManualGateInputPayload,
) -> LiveManualGateResultPayload:
    """Live Manual Gate readiness 평가. read-only — 안전 플래그 / 모드 변경 0건.

    PASS는 LIVE_MANUAL_APPROVAL 모드 진입 *검토 가능* 상태를 의미하며 실거래
    자동 허가가 아니다. 실제 LIVE 활성화는 별도 옵트인 PR + 사용자 명시 승인
    필요.
    """
    end   = payload.period_end   or datetime.now(timezone.utc)
    start = payload.period_start or (end - timedelta(days=30))

    try:
        inp = LiveManualGateInput(
            strategy_name=payload.strategy_name,
            period_start=start,
            period_end=end,
            paper_gate_passed=payload.paper_gate_passed,
            promotion_gate_passed=payload.promotion_gate_passed,
            user_explicit_opt_in=payload.user_explicit_opt_in,
            approval_required=payload.approval_required,
            ai_execution_enabled=payload.ai_execution_enabled,
            futures_live_enabled=payload.futures_live_enabled,
            enable_live_trading=payload.enable_live_trading,
            current_max_order_notional_krw=payload.current_max_order_notional_krw,
            current_max_daily_loss_krw=payload.current_max_daily_loss_krw,
            current_max_open_positions=payload.current_max_open_positions,
            allowed_symbols=tuple(payload.allowed_symbols),
            operating_days=payload.operating_days,
            total_live_manual_orders=payload.total_live_manual_orders,
            approved_orders=payload.approved_orders,
            rejected_orders=payload.rejected_orders,
            expired_or_cancelled_orders=payload.expired_or_cancelled_orders,
            approval_bypass_attempts=payload.approval_bypass_attempts,
            audit_missing_count=payload.audit_missing_count,
            system_errors=payload.system_errors,
            emergency_stops_in_period=payload.emergency_stops_in_period,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid live manual gate input: {e}")

    result: LiveManualGateResult = evaluate_live_manual_gate(inp)
    return LiveManualGateResultPayload(**result.to_dict())


class LiveManualPeriodSummaryPayload(BaseModel):
    period_start:              datetime
    period_end:                datetime
    total_live_manual_orders:  int
    approved_orders:           int
    needs_approval_orders:     int
    rejected_orders:           int
    pending_approval_rows:     int
    approved_via_queue:        int
    expired_or_cancelled:      int
    approval_bypass_attempts:  int
    emergency_stops_in_period: int
    operating_days:            int


@router.get(
    "/live-manual-gate/period-summary",
    response_model=LiveManualPeriodSummaryPayload,
)
def live_manual_period_summary(
    period_start: datetime | None = Query(default=None),
    period_end:   datetime | None = Query(default=None),
    db:           Session = Depends(get_db),
) -> LiveManualPeriodSummaryPayload:
    """LIVE_MANUAL_APPROVAL 운영 로그 read-only 요약.

    `period_start` 미지정 시 backend 가 last 30 days 자동 적용.
    """
    end   = period_end   or datetime.now(timezone.utc)
    start = period_start or (end - timedelta(days=30))
    summary = summarize_live_manual_period(db, start_date=start, end_date=end)
    return LiveManualPeriodSummaryPayload(**summary)


# ---------- #74 AI Assist Gate ----------

from app.governance.ai_assist_gate import (
    AIAssistGateInput,
    AIAssistGateResult,
    evaluate_ai_assist_gate,
)


class AIAssistGateInputPayload(BaseModel):
    """AI Assist Gate 평가 입력.

    수익 메트릭 (approved_expectancy / winning_pnl_sum / losing_pnl_sum /
    win_count / loss_count)은 *별도 trade ledger* 또는 운영자 입력으로 명시.
    confidence_calibration 도 운영자 또는 collector 산출 carry.
    """
    strategy_name:                 str  = Field(..., min_length=1, max_length=64)
    period_start:                  datetime | None = None
    period_end:                    datetime | None = None

    proposal_count:                int   = 0
    approved_proposals:            int   = 0
    risk_rejected_proposals:       int   = 0
    operator_rejected_proposals:   int   = 0
    expired_or_cancelled:          int   = 0

    approved_expectancy:           float = 0.0
    approved_winning_pnl_sum:      int   = 0
    approved_losing_pnl_sum:       int   = 0
    approved_win_count:            int   = 0
    approved_loss_count:           int   = 0

    confidence_calibration:        float = 0.0
    avg_confidence:                float | None = None
    rejected_but_would_have_won:   int   = 0

    ai_decision_audit_drift:       int   = 0
    emergency_stops_in_period:     int   = 0
    active_days:                   int   = 0

    failure_reason_counts:         dict[str, int] = Field(default_factory=dict)


class AIAssistGateResultPayload(BaseModel):
    strategy_name:           str
    period_start:            datetime
    period_end:              datetime
    verdict:                 str
    passed_criteria:         list[str]
    failed_criteria:         list[str]
    cautions:                list[str]
    failure_reason_tags:     dict[str, int]
    metrics:                 dict
    thresholds:              dict
    next_step:               str
    is_live_authorization:   bool = Field(False, description="invariant — 항상 false (PASS != LIVE_AI_EXECUTION 허가)")
    is_order_signal:         bool = Field(False, description="invariant — Gate는 BUY/SELL/HOLD 신호 아님")
    is_investment_advice:    bool = Field(False, description="invariant — 시스템 검증 자료 (투자 조언 아님)")
    live_flag_changed:       bool = Field(False, description="invariant — LIVE flag 미변경")
    mode_changed:            bool = Field(False, description="invariant — 모드 미변경")
    generated_at:            datetime


@router.post("/ai-assist-gate/evaluate", response_model=AIAssistGateResultPayload)
def evaluate_ai_assist_gate_endpoint(
    payload: AIAssistGateInputPayload,
) -> AIAssistGateResultPayload:
    """AI Assist Gate 평가. read-only — broker / DB write / LIVE flag 변경 0건.

    **PASS는 `LIVE_AI_EXECUTION` 자동 허가가 아니다** — AI 자동매매 활성화는
    `AIExecutionGate`(#45) + 별도 옵트인 PR + 사용자 명시 승인 필요.
    """
    end   = payload.period_end   or datetime.now(timezone.utc)
    start = payload.period_start or (end - timedelta(days=28))

    try:
        inp = AIAssistGateInput(
            strategy_name=payload.strategy_name,
            period_start=start,
            period_end=end,
            proposal_count=payload.proposal_count,
            approved_proposals=payload.approved_proposals,
            risk_rejected_proposals=payload.risk_rejected_proposals,
            operator_rejected_proposals=payload.operator_rejected_proposals,
            expired_or_cancelled=payload.expired_or_cancelled,
            approved_expectancy=payload.approved_expectancy,
            approved_winning_pnl_sum=payload.approved_winning_pnl_sum,
            approved_losing_pnl_sum=payload.approved_losing_pnl_sum,
            approved_win_count=payload.approved_win_count,
            approved_loss_count=payload.approved_loss_count,
            confidence_calibration=payload.confidence_calibration,
            avg_confidence=payload.avg_confidence,
            rejected_but_would_have_won=payload.rejected_but_would_have_won,
            ai_decision_audit_drift=payload.ai_decision_audit_drift,
            emergency_stops_in_period=payload.emergency_stops_in_period,
            active_days=payload.active_days,
            failure_reason_counts=payload.failure_reason_counts,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid ai assist gate input: {e}")

    result: AIAssistGateResult = evaluate_ai_assist_gate(inp)
    return AIAssistGateResultPayload(**result.to_dict())


# ---------- #75 AI Execution Activation Gate ----------

from datetime import time as _dt_time

from app.governance.ai_execution_gate import (
    AIExecutionGateInput,
    AIExecutionActivationGateResult,
    evaluate_ai_execution_gate,
    get_policy_summary,
)


class AIExecutionGateInputPayload(BaseModel):
    """AI Execution Activation Gate 평가 입력 (read-only).

    안전 플래그 / opt-in / 한도 / 운영 로그는 *입력으로*만 받는다 — 본 endpoint
    는 어떤 값도 mutate 하지 않으며, 선물 AI execution은 본 게이트가 *영구*
    허용하지 않으므로 `futures_target=True` 이면 BLOCKED.
    """
    strategy_name:                 str  = Field(..., min_length=1, max_length=64)

    promotion_gate_passed:         bool = False
    paper_gate_passed:             bool = False
    ai_assist_gate_passed:         bool = False
    live_manual_gate_passed:       bool = False

    user_explicit_opt_in:          bool = False
    enable_live_trading:           bool = False
    enable_ai_execution:           bool = False
    enable_futures_live_trading:   bool = False

    live_manual_days:              int  = 0
    ai_assist_days:                int  = 0

    risk_manager_active:           bool = False
    order_guard_active:            bool = False
    ai_permission_gate_active:     bool = False
    audit_log_complete:            bool = False
    kill_switch_ready:             bool = False
    circuit_breaker_configured:    bool = False

    current_max_order_notional_krw: int = 0
    current_max_daily_loss_krw:     int = 0
    current_max_daily_order_count:  int = 0
    current_max_open_positions:     int = 0
    allowed_symbols:                list[str] = Field(default_factory=list)

    explicit_time_window_set:       bool = False
    window_start_kst:               str | None = None
    window_end_kst:                 str | None = None

    ai_confidence_threshold:        int  = 0
    signal_quality_threshold:       int  = 0

    system_errors:                  int  = 0
    audit_missing_count:            int  = 0
    approval_bypass_attempts:       int  = 0

    futures_target:                 bool = False


class AIExecutionGateResultPayload(BaseModel):
    strategy_name:           str
    evaluated_at:            datetime
    verdict:                 str
    passed_criteria:         list[str]
    blocked_criteria:        list[str]
    cautions:                list[str]
    required_actions:        list[str]
    metrics:                 dict
    thresholds:              dict
    next_step:               str
    is_live_authorization:   bool = Field(False, description="invariant — 항상 false (READY_FOR_REVIEW != 활성화)")
    is_order_signal:         bool = Field(False, description="invariant — BUY/SELL/HOLD 신호 아님")
    is_investment_advice:    bool = Field(False, description="invariant — 시스템 검증 자료")
    futures_allowed:         bool = Field(False, description="invariant — 선물 AI 자동 실행 영구 차단")
    live_flag_changed:       bool = Field(False, description="invariant — 안전 플래그 미변경")
    mode_changed:            bool = Field(False, description="invariant — 모드 미변경")
    generated_at:            datetime


def _parse_hhmm(raw: str | None) -> _dt_time | None:
    if not raw:
        return None
    try:
        h, m = raw.split(":")
        return _dt_time(int(h), int(m))
    except Exception:  # noqa: BLE001
        return None


@router.post("/ai-execution-gate/evaluate", response_model=AIExecutionGateResultPayload)
def evaluate_ai_execution_gate_endpoint(
    payload: AIExecutionGateInputPayload,
) -> AIExecutionGateResultPayload:
    """AI Execution Activation Gate 평가. read-only — broker / DB write /
    LIVE flag 변경 0건.

    **READY_FOR_REVIEW 판정은 실제 활성화가 아니다.** 활성화는 별도 옵트인
    PR + 사용자 명시 승인 + 초소액 canary + 즉시 kill switch 모두 필요.
    """
    try:
        inp = AIExecutionGateInput(
            strategy_name=payload.strategy_name,
            promotion_gate_passed=payload.promotion_gate_passed,
            paper_gate_passed=payload.paper_gate_passed,
            ai_assist_gate_passed=payload.ai_assist_gate_passed,
            live_manual_gate_passed=payload.live_manual_gate_passed,
            user_explicit_opt_in=payload.user_explicit_opt_in,
            enable_live_trading=payload.enable_live_trading,
            enable_ai_execution=payload.enable_ai_execution,
            enable_futures_live_trading=payload.enable_futures_live_trading,
            live_manual_days=payload.live_manual_days,
            ai_assist_days=payload.ai_assist_days,
            risk_manager_active=payload.risk_manager_active,
            order_guard_active=payload.order_guard_active,
            ai_permission_gate_active=payload.ai_permission_gate_active,
            audit_log_complete=payload.audit_log_complete,
            kill_switch_ready=payload.kill_switch_ready,
            circuit_breaker_configured=payload.circuit_breaker_configured,
            current_max_order_notional_krw=payload.current_max_order_notional_krw,
            current_max_daily_loss_krw=payload.current_max_daily_loss_krw,
            current_max_daily_order_count=payload.current_max_daily_order_count,
            current_max_open_positions=payload.current_max_open_positions,
            allowed_symbols=tuple(payload.allowed_symbols),
            explicit_time_window_set=payload.explicit_time_window_set,
            window_start_kst=_parse_hhmm(payload.window_start_kst),
            window_end_kst=_parse_hhmm(payload.window_end_kst),
            ai_confidence_threshold=payload.ai_confidence_threshold,
            signal_quality_threshold=payload.signal_quality_threshold,
            system_errors=payload.system_errors,
            audit_missing_count=payload.audit_missing_count,
            approval_bypass_attempts=payload.approval_bypass_attempts,
            futures_target=payload.futures_target,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid ai execution gate input: {e}")

    result: AIExecutionActivationGateResult = evaluate_ai_execution_gate(inp)
    return AIExecutionGateResultPayload(**result.to_dict())


@router.get("/ai-execution-gate/policy")
def get_ai_execution_gate_policy() -> dict:
    """기본 제한 + required gates + futures_allowed=false 정보 (read-only)."""
    return get_policy_summary()


# ---------- #77 Alpha Decay Monitor ----------

from app.governance.alpha_decay import (
    AlphaDecayInput,
    AlphaDecayResult,
    StrategyMetricsSnapshot,
    evaluate_alpha_decay,
)


class _SnapshotPayload(BaseModel):
    trade_count:            int   = 0
    expectancy:             float = 0.0
    profit_factor:          float | None = None
    win_rate:               float = 0.0
    max_drawdown:           int   = 0
    max_consecutive_losses: int   = 0


class AlphaDecayInputPayload(BaseModel):
    """Alpha Decay 평가 입력 (read-only).

    baseline = 검증 단계 통과 시점의 성과, recent = 최근 운용 통계.
    """
    strategy_name:             str = Field(..., min_length=1, max_length=64)
    baseline:                  _SnapshotPayload
    recent:                    _SnapshotPayload
    baseline_regime:           str | None = None
    recent_regime:             str | None = None
    recent_data_quality_score: float | None = None
    operator_note:             str | None = Field(default=None, max_length=500)


class AlphaDecayResultPayload(BaseModel):
    strategy_name:           str
    score:                   int
    status:                  str
    kind:                    str
    degraded_signals:        list[str]
    cautions:                list[str]
    recommended_action:      str
    metrics:                 dict
    thresholds:              dict
    is_order_signal:         bool = Field(False, description="invariant — 항상 false")
    auto_disable:            bool = Field(False, description="invariant — 자동 비활성 절대 금지")
    auto_apply_allowed:      bool = Field(False, description="invariant — advisory only")
    live_flag_changed:       bool = Field(False)
    mode_changed:            bool = Field(False)
    generated_at:            datetime


@router.post("/alpha-decay/evaluate", response_model=AlphaDecayResultPayload)
def evaluate_alpha_decay_endpoint(
    payload: AlphaDecayInputPayload,
) -> AlphaDecayResultPayload:
    """Alpha Decay 평가. read-only — DB write / broker 호출 / 전략 비활성화 0건.

    DISABLE_CANDIDATE 라벨은 *비활성 후보 표시*일 뿐 자동 비활성/삭제가 아니다.
    전략 변경은 운영자 수동 결정 + 별도 PR 절차 필요.
    """
    def _snap(p: _SnapshotPayload) -> StrategyMetricsSnapshot:
        return StrategyMetricsSnapshot(
            trade_count=p.trade_count,
            expectancy=p.expectancy,
            profit_factor=p.profit_factor,
            win_rate=p.win_rate,
            max_drawdown=p.max_drawdown,
            max_consecutive_losses=p.max_consecutive_losses,
        )

    try:
        inp = AlphaDecayInput(
            strategy_name=payload.strategy_name,
            baseline=_snap(payload.baseline),
            recent=_snap(payload.recent),
            baseline_regime=payload.baseline_regime,
            recent_regime=payload.recent_regime,
            recent_data_quality_score=payload.recent_data_quality_score,
            operator_note=payload.operator_note,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid alpha decay input: {e}")

    result: AlphaDecayResult = evaluate_alpha_decay(inp)
    return AlphaDecayResultPayload(**result.to_dict())


# ---------- #80 Pre-market Check ----------

from app.governance.pre_market_check import (
    PreMarketCheckInput,
    PreMarketCheckResult,
    evaluate_pre_market_check,
)


class PreMarketCheckPayload(BaseModel):
    """Pre-market 점검 입력 (read-only).

    안전 플래그 / 시스템 상태 / 게이트 결과는 *현재값 carry* — 본 endpoint 는
    어떤 값도 mutate 하지 않는다.
    """
    mode:                              str   = "SIMULATION"
    strict:                            bool  = False
    include_optional:                  bool  = True

    api_reachable:                     bool  = True
    db_reachable:                      bool  = True

    broker_ready:                      bool | None = None
    kis_is_paper:                      bool  = True
    kis_credentials_present:           bool | None = None

    market_data_provider:              str   = "mock"
    data_freshness_ok:                 bool | None = None
    stale_symbol_count:                int   = 0

    watchlist_item_count:              int   = 0
    active_strategy_count:             int   = 0

    risk_policy_configured:            bool  = True
    daily_loss_limit_configured:       bool  = True
    daily_loss_used_ratio:             float = 0.0
    position_limits_configured:        bool  = True

    emergency_stop_active:             bool  = False
    kill_switch_level:                 str   = "OFF"

    ai_permission_gate_active:         bool  = True
    ai_execution_enabled:              bool  = False
    enable_live_trading:               bool  = False
    enable_futures_live_trading:       bool  = False

    notification_configured:           bool  = False

    paper_gate_pass:                   bool | None = None
    live_manual_gate_pass:             bool | None = None
    ai_assist_gate_pass:               bool | None = None
    ai_execution_gate_ready:           bool | None = None

    manual_ack:                        bool  = False
    manual_ack_by:                     str   = Field(default="", max_length=64)
    manual_ack_note:                   str   = Field(default="", max_length=500)


class PreMarketCheckItemPayload(BaseModel):
    name:        str
    category:    str
    status:      str
    required:    bool
    message:     str
    detail:      dict


class PreMarketCheckResultPayload(BaseModel):
    mode:                str
    verdict:             str
    start_allowed:       bool
    items:               list[PreMarketCheckItemPayload]
    failed_required:     list[str]
    warnings:            list[str]
    required_actions:    list[str]
    manual_ack_recorded: bool
    manual_ack_by:       str
    manual_ack_note:     str
    is_order_signal:     bool = Field(False, description="invariant — 항상 false")
    live_flag_changed:   bool = Field(False, description="invariant — flag 미변경")
    mode_changed:        bool = Field(False, description="invariant — 모드 미변경")
    generated_at:        datetime


@router.post(
    "/pre-market-check",
    response_model=PreMarketCheckResultPayload,
)
def pre_market_check_post(
    payload: PreMarketCheckPayload,
) -> PreMarketCheckResultPayload:
    """장 시작 전 자동 점검 (POST — operator 가 현재 상태를 명시 입력).

    read-only — broker / DB write / 모드 / flag 변경 0건. manual_ack 는
    *기록*만 — required FAIL 우회 불가.
    """
    try:
        inp = PreMarketCheckInput(**payload.model_dump())
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"invalid pre_market input: {e}")
    result: PreMarketCheckResult = evaluate_pre_market_check(inp)
    return PreMarketCheckResultPayload(**result.to_dict())


@router.get(
    "/pre-market-check",
    response_model=PreMarketCheckResultPayload,
)
def pre_market_check_get(
    mode:             str  = "SIMULATION",
    strict:           bool = False,
    include_optional: bool = True,
) -> PreMarketCheckResultPayload:
    """기본 SIMULATION default 로 dry-run 점검 (read-only).

    운영자 / 시스템 상태를 명시 입력 없이 default 값으로만 평가. 실제 운영
    상태는 POST endpoint 또는 별도 collector 로 채워서 사용 권장.
    """
    inp = PreMarketCheckInput(
        mode=mode, strict=strict, include_optional=include_optional,
    )
    result = evaluate_pre_market_check(inp)
    return PreMarketCheckResultPayload(**result.to_dict())
