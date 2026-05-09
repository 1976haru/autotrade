"""#45: AI Execution Gate read-only surface.

`/api/ai-execution/evaluate` — read-only 평가. 호출자가 AIExecutionInput을
보내면 게이트 판정만 반환한다. **broker 호출 0건, OrderExecutor 호출 0건,
실제 주문 0건**. UI/agent가 "지금 이 후보가 자동 실행될 수 있는지" pre-check
하는 용도.

`/api/ai-execution/policy` — 현재 정책 read-only 조회. 운영자가 한 화면에서
모든 게이트 조건을 확인. ENABLE_AI_EXECUTION 토글 / 활성화 버튼은 *추가하지
않는다* — 본 endpoint는 read-only 정보 surface 전용.

**절대 원칙:**
- 본 모듈은 broker / OrderExecutor / route_order를 import하지 않는다.
- ENABLE_AI_EXECUTION / ENABLE_LIVE_TRADING을 변경하는 코드 0건.
- 본 endpoint를 호출해도 어떤 audit row / approval row / order도 생성되지
  않는다 (read-only).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.modes import OperationMode
from app.risk.ai_execution_gate import (
    AIExecutionInput,
    AIExecutionPolicy,
    AIExecutionResult,
    build_policy_status,
    evaluate_ai_execution,
)


router = APIRouter(prefix="/ai-execution", tags=["ai", "risk"])


# ====================================================================
# Schemas
# ====================================================================


class AIExecutionEvaluateIn(BaseModel):
    """`/evaluate` 입력. 모든 필드는 read-only — 게이트 평가에만 사용."""

    mode:               str            = "LIVE_AI_EXECUTION"
    symbol:             str
    quantity:           int            = Field(gt=0)
    latest_price:       int            = Field(ge=0)
    confidence:         int            = Field(default=0, ge=0, le=100)
    quality_score:      int            = Field(default=0, ge=0, le=100)
    explanation:        str | None     = None
    target_price:       int | None     = Field(default=None, ge=0)
    stop_price:         int | None     = Field(default=None, ge=0)
    agent_name:         str | None     = None
    agent_chain_id:     str | None     = None
    strategy:           str | None     = None
    today_ai_order_count: int          = Field(default=0, ge=0)
    risk_passed:           bool        = False
    permission_passed:     bool        = False
    order_guard_passed:    bool        = False


class AIExecutionEvaluateOut(BaseModel):
    decision:    Literal["ALLOW", "CANARY_ONLY", "BLOCKED"]
    reasons:     list[str]
    passed:      list[str]
    audit_note:  str
    notional:    int
    is_canary:   bool
    actual_broker_order_sent: bool


class AIExecutionPolicyOut(BaseModel):
    enable_ai_execution:    bool
    enable_live_trading:    bool
    is_canary_mode:         bool
    min_confidence:         int
    min_quality_score:      int
    require_explanation:    bool
    require_exit_plan:      bool
    max_notional_per_order: int
    symbol_whitelist:       list[str]
    window_start_hour_kst:  int
    window_end_hour_kst:    int
    max_orders_per_day:     int
    live_ai_execution_disabled: bool
    canary_note:            str
    notice:                 str


# ====================================================================
# Policy resolution
# ====================================================================


def _current_policy() -> AIExecutionPolicy:
    """Settings → AIExecutionPolicy.

    본 PR에서는 새 env 변수를 추가하지 않는다 — 기존 `ENABLE_AI_EXECUTION` /
    `ENABLE_LIVE_TRADING`만 운영 게이트로 사용하고, 나머지 초기 한도는 보수적
    default를 그대로 적용. 운영자가 향후 별도 PR로 env 변수를 노출.
    """
    s = get_settings()
    return AIExecutionPolicy(
        enable_ai_execution=s.enable_ai_execution,
        enable_live_trading=s.enable_live_trading,
        # 나머지 필드는 dataclass default를 사용 (보수적 시작값).
    )


# ====================================================================
# Endpoints
# ====================================================================


@router.post("/evaluate", response_model=AIExecutionEvaluateOut)
def evaluate_route(payload: AIExecutionEvaluateIn) -> AIExecutionEvaluateOut:
    """AIExecutionGate를 호출해 read-only 판정을 반환.

    실제 주문은 절대 발생하지 않는다 — broker 호출 0건. caller는 응답의
    `decision`을 보고 다음 단계를 결정 (CANARY_ONLY는 기록만, BLOCKED는
    중단, ALLOW는 *향후 LIVE_AI_EXECUTION이 옵트인되면* 실행 가능).
    """
    try:
        mode = OperationMode(payload.mode)
    except ValueError:
        # 알 수 없는 mode는 BLOCKED로 surface — 422 대신 게이트가 자체 판정.
        return AIExecutionEvaluateOut(
            decision="BLOCKED",
            reasons=[f"unknown mode: {payload.mode}"],
            passed=[],
            audit_note=f"AI execution BLOCKED: unknown mode {payload.mode}",
            notional=payload.latest_price * payload.quantity,
            is_canary=False,
            actual_broker_order_sent=False,
        )

    inp = AIExecutionInput(
        mode=mode,
        symbol=payload.symbol,
        quantity=payload.quantity,
        latest_price=payload.latest_price,
        confidence=payload.confidence,
        quality_score=payload.quality_score,
        explanation=payload.explanation,
        target_price=payload.target_price,
        stop_price=payload.stop_price,
        agent_name=payload.agent_name,
        agent_chain_id=payload.agent_chain_id,
        strategy=payload.strategy,
        today_ai_order_count=payload.today_ai_order_count,
        risk_passed=payload.risk_passed,
        permission_passed=payload.permission_passed,
        order_guard_passed=payload.order_guard_passed,
    )
    result: AIExecutionResult = evaluate_ai_execution(
        inp=inp, policy=_current_policy(),
    )
    return AIExecutionEvaluateOut(
        decision=result.decision.value,
        reasons=list(result.reasons),
        passed=list(result.passed),
        audit_note=result.audit_note,
        notional=result.notional,
        is_canary=result.is_canary,
        actual_broker_order_sent=result.actual_broker_order_sent,
    )


@router.get("/policy", response_model=AIExecutionPolicyOut)
def policy_route() -> AIExecutionPolicyOut:
    """현재 AIExecutionPolicy를 read-only로 조회.

    UI는 본 응답을 그대로 표시 — ENABLE_AI_EXECUTION 토글 버튼 / 자동매매
    시작 버튼은 *명시적으로 추가하지 않는다*. 운영자가 정책을 바꾸려면
    env 변수 + 운영자 opt-in이 필요한 별도 절차를 거쳐야 한다.
    """
    status = build_policy_status(_current_policy())
    return AIExecutionPolicyOut(**status)
