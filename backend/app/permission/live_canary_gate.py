"""5-03: 실전 Canary Gate — 최소금액/최소종목/1일 1건 설계 (read-only, advisory).

충분한 KIS 모의/Paper 검증 후 *제한적* 실전 테스트(canary)를 검토할 때도,
canary 가 무제한으로 열리지 않도록 다음을 *모두* 통과해야만 canary 가
"검토 가능" 상태가 된다:

  - #42 Live Manual Approval Gate 전체 통과 (Live Capital Review + Manual
    Approval + Operator Approval + Symbol Whitelist + Max notional + Daily limit)
  - Paper Gate(#72) PASS + `can_review_live_canary`
  - LIVE_AI_EXECUTION 비활성 (canary 는 AI 자동 실행이 아니다)
  - 1일 1건 제한 (daily_order_count_limit == 1, today_live_order_count < limit)
  - 최소/최대 주문금액 설정 + 최대금액이 canary 상한 이하
  - daily live notional limit 설정
  - canary risk profile = CONSERVATIVE
  - canary 운용 기간(window) 활성

**본 모듈은 실전 canary 를 실행하지 않는다.** 모든 조건이 충족되어도
`canary_ready=True` 는 *검토 readiness* 일 뿐이며 `is_live_authorization` /
`broker_order_sent` / `order_created` 는 *항상 False*, `broker_order_no=None`.
실제 실전 주문 경로는 별도 옵트인 PR + 운영자 명시 승인 이후에만 검토된다.

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order / KIS live endpoint / 외부 HTTP / AI SDK
  import·호출 0건. 안전 flag(`.env`) 변경 0건. Secret/계좌번호 carry 0건.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.modes import OperationMode
from app.permission.live_manual_approval_gate import (
    NOT_A_LIVE_ORDER,
    LiveManualApprovalInput,
    evaluate_live_manual_approval_gate,
)

# canary 절대 상한 — 무제한 방지 (운영자가 더 낮출 수는 있으나 초과 불가).
CANARY_MAX_ALLOWED_ORDER_NOTIONAL_KRW = 30_000
CANARY_DAILY_ORDER_LIMIT = 1  # 1일 1건.
CANARY_REQUIRED_RISK_PROFILE = "CONSERVATIVE"

# ── reason_code ──────────────────────────────────────────────────────────────
CANARY_MANUAL_GATE_REQUIRED        = "CANARY_MANUAL_GATE_REQUIRED"
CANARY_PAPER_GATE_REQUIRED         = "CANARY_PAPER_GATE_REQUIRED"
CANARY_REVIEW_NOT_AVAILABLE        = "CANARY_REVIEW_NOT_AVAILABLE"
CANARY_AI_EXECUTION_BLOCKED        = "CANARY_AI_EXECUTION_BLOCKED"
CANARY_DAILY_ORDER_LIMIT_REQUIRED  = "CANARY_DAILY_ORDER_LIMIT_REQUIRED"
CANARY_DAILY_ORDER_LIMIT_EXCEEDED  = "CANARY_DAILY_ORDER_LIMIT_EXCEEDED"
CANARY_MIN_ORDER_NOTIONAL_REQUIRED = "CANARY_MIN_ORDER_NOTIONAL_REQUIRED"
CANARY_MAX_ORDER_NOTIONAL_REQUIRED = "CANARY_MAX_ORDER_NOTIONAL_REQUIRED"
CANARY_MAX_ORDER_NOTIONAL_TOO_HIGH = "CANARY_MAX_ORDER_NOTIONAL_TOO_HIGH"
CANARY_DAILY_NOTIONAL_LIMIT_REQUIRED = "CANARY_DAILY_NOTIONAL_LIMIT_REQUIRED"
CANARY_RISK_PROFILE_REQUIRED       = "CANARY_RISK_PROFILE_REQUIRED"
CANARY_WINDOW_REQUIRED             = "CANARY_WINDOW_REQUIRED"
CANARY_REVIEW_READY                = "CANARY_REVIEW_READY"

_MESSAGES_KO: dict[str, str] = {
    NOT_A_LIVE_ORDER:                   "실전 주문 요청이 아닙니다 (Paper/모의).",
    CANARY_MANUAL_GATE_REQUIRED:        "실전 canary 전에 Manual Approval Gate 조건을 모두 통과해야 합니다.",
    CANARY_PAPER_GATE_REQUIRED:         "실전 canary 는 Paper Gate 통과 후에만 검토 가능합니다.",
    CANARY_REVIEW_NOT_AVAILABLE:        "canary 검토 가능 상태(can_review_live_canary)가 아닙니다.",
    CANARY_AI_EXECUTION_BLOCKED:        "LIVE_AI_EXECUTION 은 canary gate 통과 전 허용되지 않습니다.",
    CANARY_DAILY_ORDER_LIMIT_REQUIRED:  "canary 는 1일 1건 제한(daily_order_count_limit=1)이 필요합니다.",
    CANARY_DAILY_ORDER_LIMIT_EXCEEDED:  "오늘 canary 주문 건수가 이미 한도에 도달했습니다.",
    CANARY_MIN_ORDER_NOTIONAL_REQUIRED: "canary 최소 주문금액 설정이 필요합니다.",
    CANARY_MAX_ORDER_NOTIONAL_REQUIRED: "canary 최대 주문금액 설정이 필요합니다.",
    CANARY_MAX_ORDER_NOTIONAL_TOO_HIGH: "canary 최대 주문금액이 허용 상한을 초과합니다.",
    CANARY_DAILY_NOTIONAL_LIMIT_REQUIRED: "canary daily live notional limit 설정이 필요합니다.",
    CANARY_RISK_PROFILE_REQUIRED:       "canary 는 보수적(CONSERVATIVE) risk profile 이 필요합니다.",
    CANARY_WINDOW_REQUIRED:             "canary 운용 기간(window)이 활성 상태여야 합니다.",
    CANARY_REVIEW_READY:                "모든 canary 조건 충족 — 실전 canary 는 별도 검토 가능 (자동 실행 아님).",
}


@dataclass(frozen=True)
class LiveCanaryGateInput:
    """실전 canary gate 입력. Secret/계좌번호 필드 없음."""

    mode: OperationMode | str | None = None
    live_order_requested: bool = False
    broker_order_type: Optional[str] = None
    kis_is_paper: bool = True

    enable_live_trading: bool = False
    enable_ai_execution: bool = False  # canary 에서는 반드시 비활성.

    # #42 manual approval 전제 조건.
    live_capital_review_approved: bool = False
    manual_approval_present: bool = False
    operator_name: Optional[str] = None
    operator_reason: Optional[str] = None
    operator_approved_at: Optional[str] = None
    symbol: Optional[str] = None
    symbol_whitelist: tuple[str, ...] = ()
    max_order_notional_configured: bool = False
    daily_live_limit_configured: bool = False
    paper_approval_reused_as_live: bool = False
    order_payload: Any = None

    # Paper Gate 전제.
    paper_gate_passed: bool = False
    can_review_live_canary: bool = False

    # canary 전용 한도.
    daily_order_count_limit: int = 0
    today_live_order_count: int = 0
    min_order_notional_krw: int = 0
    max_order_notional_krw: int = 0
    daily_live_notional_limit_krw: int = 0
    canary_risk_profile: Optional[str] = None
    canary_window_active: bool = False


@dataclass(frozen=True)
class LiveCanaryGateResult:
    """canary gate 결과. order-creation 불변은 항상 False."""

    canary_ready: bool
    reason_code: str
    reason_message: str
    mode: str

    paper_gate_passed: bool
    manual_gate_passed: bool

    # 불변 — canary_ready=True 여도 주문 0건.
    is_live_authorization: bool = False
    broker_order_sent: bool = False
    order_created: bool = False
    broker_order_no: Optional[str] = None
    contains_secret: bool = False
    is_order_signal: bool = False
    enable_ai_execution_allowed: bool = False  # 항상 False (canary≠AI 실행).

    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("is_live_authorization", "broker_order_sent", "order_created",
                     "contains_secret", "is_order_signal",
                     "enable_ai_execution_allowed"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be False (canary gate never executes orders)")
        if self.broker_order_no is not None:
            raise ValueError("broker_order_no must be None (no order created)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "canary_ready":            bool(self.canary_ready),
            "reason_code":             self.reason_code,
            "reason_message":          self.reason_message,
            "mode":                    self.mode,
            "paper_gate_passed":       bool(self.paper_gate_passed),
            "manual_gate_passed":      bool(self.manual_gate_passed),
            "is_live_authorization":   False,
            "broker_order_sent":       False,
            "order_created":           False,
            "broker_order_no":         None,
            "contains_secret":         False,
            "is_order_signal":         False,
            "enable_ai_execution_allowed": False,
            "detail":                  dict(self.detail),
        }


def _manual_input(inp: LiveCanaryGateInput) -> LiveManualApprovalInput:
    return LiveManualApprovalInput(
        mode=inp.mode,
        live_order_requested=inp.live_order_requested,
        broker_order_type=inp.broker_order_type,
        kis_is_paper=inp.kis_is_paper,
        enable_live_trading=inp.enable_live_trading,
        enable_ai_execution=inp.enable_ai_execution,
        live_capital_review_approved=inp.live_capital_review_approved,
        manual_approval_present=inp.manual_approval_present,
        operator_name=inp.operator_name,
        operator_reason=inp.operator_reason,
        operator_approved_at=inp.operator_approved_at,
        symbol=inp.symbol,
        symbol_whitelist=inp.symbol_whitelist,
        max_order_notional_configured=inp.max_order_notional_configured,
        daily_live_limit_configured=inp.daily_live_limit_configured,
        paper_approval_reused_as_live=inp.paper_approval_reused_as_live,
        order_payload=inp.order_payload,
    )


def evaluate_live_canary_gate(inp: LiveCanaryGateInput) -> LiveCanaryGateResult:
    """실전 canary gate 평가. 기본 차단 — 모든 조건 충족 시에만 검토 readiness."""
    mode_str = inp.mode.value if isinstance(inp.mode, OperationMode) else str(inp.mode)
    manual = evaluate_live_manual_approval_gate(_manual_input(inp))

    def _result(ready: bool, code: str, manual_passed: bool) -> LiveCanaryGateResult:
        return LiveCanaryGateResult(
            canary_ready=ready,
            reason_code=code,
            reason_message=_MESSAGES_KO.get(code, manual.reason_message),
            mode=mode_str,
            paper_gate_passed=bool(inp.paper_gate_passed),
            manual_gate_passed=manual_passed,
            detail={
                "manual_gate_reason": manual.reason_code,
                "daily_order_count_limit": int(inp.daily_order_count_limit),
                "today_live_order_count": int(inp.today_live_order_count),
            },
        )

    # 0. 실전 주문 요청이 아니면 canary 미적용 (Paper 무회귀).
    if manual.reason_code == NOT_A_LIVE_ORDER:
        return _result(False, NOT_A_LIVE_ORDER, manual_passed=False)

    # 1. #42 manual approval gate 전체 통과 필수.
    if not manual.approved:
        # manual gate 의 구체 reason_code 를 그대로 노출 (capital review / manual /
        # operator / whitelist / max notional / daily limit 중 미충족).
        return _result(False, manual.reason_code, manual_passed=False)

    # 2. Paper Gate 통과 + canary 검토 가능.
    if not inp.paper_gate_passed:
        return _result(False, CANARY_PAPER_GATE_REQUIRED, manual_passed=True)
    if not inp.can_review_live_canary:
        return _result(False, CANARY_REVIEW_NOT_AVAILABLE, manual_passed=True)

    # 3. LIVE_AI_EXECUTION 은 canary 에서 불가 (canary 는 AI 자동 실행 아님).
    if inp.enable_ai_execution:
        return _result(False, CANARY_AI_EXECUTION_BLOCKED, manual_passed=True)

    # 4. 1일 1건 제한.
    if inp.daily_order_count_limit != CANARY_DAILY_ORDER_LIMIT:
        return _result(False, CANARY_DAILY_ORDER_LIMIT_REQUIRED, manual_passed=True)
    if inp.today_live_order_count >= inp.daily_order_count_limit:
        return _result(False, CANARY_DAILY_ORDER_LIMIT_EXCEEDED, manual_passed=True)

    # 5. 최소/최대 주문금액.
    if inp.min_order_notional_krw <= 0:
        return _result(False, CANARY_MIN_ORDER_NOTIONAL_REQUIRED, manual_passed=True)
    if inp.max_order_notional_krw <= 0 or inp.max_order_notional_krw < inp.min_order_notional_krw:
        return _result(False, CANARY_MAX_ORDER_NOTIONAL_REQUIRED, manual_passed=True)
    if inp.max_order_notional_krw > CANARY_MAX_ALLOWED_ORDER_NOTIONAL_KRW:
        return _result(False, CANARY_MAX_ORDER_NOTIONAL_TOO_HIGH, manual_passed=True)

    # 6. daily live notional limit.
    if inp.daily_live_notional_limit_krw <= 0:
        return _result(False, CANARY_DAILY_NOTIONAL_LIMIT_REQUIRED, manual_passed=True)

    # 7. canary risk profile (보수적).
    if str(inp.canary_risk_profile or "").upper() != CANARY_REQUIRED_RISK_PROFILE:
        return _result(False, CANARY_RISK_PROFILE_REQUIRED, manual_passed=True)

    # 8. canary 운용 기간 활성.
    if not inp.canary_window_active:
        return _result(False, CANARY_WINDOW_REQUIRED, manual_passed=True)

    # 모든 조건 충족 — *검토* readiness. 실제 주문은 생성되지 않는다.
    return _result(True, CANARY_REVIEW_READY, manual_passed=True)


__all__ = [
    "CANARY_MAX_ALLOWED_ORDER_NOTIONAL_KRW",
    "CANARY_DAILY_ORDER_LIMIT",
    "CANARY_REQUIRED_RISK_PROFILE",
    "CANARY_MANUAL_GATE_REQUIRED",
    "CANARY_PAPER_GATE_REQUIRED",
    "CANARY_REVIEW_NOT_AVAILABLE",
    "CANARY_AI_EXECUTION_BLOCKED",
    "CANARY_DAILY_ORDER_LIMIT_REQUIRED",
    "CANARY_DAILY_ORDER_LIMIT_EXCEEDED",
    "CANARY_MIN_ORDER_NOTIONAL_REQUIRED",
    "CANARY_MAX_ORDER_NOTIONAL_REQUIRED",
    "CANARY_MAX_ORDER_NOTIONAL_TOO_HIGH",
    "CANARY_DAILY_NOTIONAL_LIMIT_REQUIRED",
    "CANARY_RISK_PROFILE_REQUIRED",
    "CANARY_WINDOW_REQUIRED",
    "CANARY_REVIEW_READY",
    "NOT_A_LIVE_ORDER",
    "LiveCanaryGateInput",
    "LiveCanaryGateResult",
    "evaluate_live_canary_gate",
]
