"""5-02: 실전 주문 = manual approval 전용 Gate (read-only, advisory).

**핵심 원칙**: 실전(LIVE) 주문은 **환경변수 하나로 켤 수 없다.** `ENABLE_LIVE_TRADING`
또는 `ENABLE_AI_EXECUTION` 가 true 여도, 다음을 *모두* 통과하기 전에는 실전 주문이
검토조차 되지 않는다:
  1. Live Capital Review 승인
  2. 운영자 Manual Approval
  3. Operator Approval (operator + reason + timestamp)
  4. Symbol Whitelist (비어있지 않고, 주문 종목이 포함)
  5. Max order notional 설정
  6. Daily live limit 설정
그리고 Paper 승인 / Paper capital 은 *절대* Live 승인으로 재사용되지 않는다.

**본 모듈은 실전 활성화 기능이 아니다.** 모든 조건이 충족되어도
`is_live_authorization` / `broker_order_sent` / `order_created` 는 *항상 False*
이며 실제 실전 주문을 생성하지 않는다(검토 readiness 표시 전용). 실제 실전
주문 경로는 별도 옵트인 PR + 운영자 명시 승인 이후에만 검토된다.

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order / KIS live endpoint / 외부 HTTP / AI SDK
  import 0건.
- 안전 flag(`ENABLE_LIVE_TRADING` 등) 변경 0건.
- Secret / API key / 계좌번호 원문 carry 0건.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.modes import OperationMode
from app.permission.live_capital_guard import (
    LIVE_CAPITAL_REVIEW_REQUIRED,
    detect_paper_capital_in_payload,
    is_live_order_mode,
)

# ─────────────────────────────────────────────────────────────────────────────
# reason_code 상수
# ─────────────────────────────────────────────────────────────────────────────

NOT_A_LIVE_ORDER                 = "NOT_A_LIVE_ORDER"
LIVE_MANUAL_APPROVAL_REQUIRED    = "LIVE_MANUAL_APPROVAL_REQUIRED"
OPERATOR_APPROVAL_REQUIRED       = "OPERATOR_APPROVAL_REQUIRED"
SYMBOL_WHITELIST_REQUIRED        = "SYMBOL_WHITELIST_REQUIRED"
SYMBOL_NOT_WHITELISTED           = "SYMBOL_NOT_WHITELISTED"
MAX_ORDER_NOTIONAL_REQUIRED      = "MAX_ORDER_NOTIONAL_REQUIRED"
DAILY_LIVE_LIMIT_REQUIRED        = "DAILY_LIVE_LIMIT_REQUIRED"
PAPER_APPROVAL_NOT_LIVE_APPROVAL = "PAPER_APPROVAL_NOT_LIVE_APPROVAL"
# 모든 조건 충족 — *검토* 가능(실제 활성화 아님).
LIVE_MANUAL_GATE_REVIEW_READY    = "LIVE_MANUAL_GATE_REVIEW_READY"

_MESSAGES_KO: dict[str, str] = {
    NOT_A_LIVE_ORDER:
        "실전 주문 요청이 아닙니다 (Paper / 모의 경로).",
    LIVE_MANUAL_APPROVAL_REQUIRED:
        "실전 주문은 운영자 수동 승인 없이는 허용되지 않습니다.",
    OPERATOR_APPROVAL_REQUIRED:
        "실전 주문에는 운영자/사유/시각이 포함된 Operator Approval 이 필요합니다.",
    SYMBOL_WHITELIST_REQUIRED:
        "실전 주문에는 Symbol Whitelist 설정이 필요합니다.",
    SYMBOL_NOT_WHITELISTED:
        "주문 종목이 Symbol Whitelist 에 없습니다.",
    MAX_ORDER_NOTIONAL_REQUIRED:
        "실전 주문에는 Max order notional 설정이 필요합니다.",
    DAILY_LIVE_LIMIT_REQUIRED:
        "실전 주문에는 Daily live limit 설정이 필요합니다.",
    PAPER_APPROVAL_NOT_LIVE_APPROVAL:
        "Paper 승인/자금은 실전 승인으로 사용할 수 없습니다.",
    LIVE_CAPITAL_REVIEW_REQUIRED:
        "실전 주문에는 별도 Live 자금 검토가 필요합니다.",
    LIVE_MANUAL_GATE_REVIEW_READY:
        "모든 수동 승인 조건이 충족되었습니다 — 실전 주문은 별도 검토 가능 "
        "(자동 활성화 아님).",
}


# ─────────────────────────────────────────────────────────────────────────────
# 입력 DTO — Secret / 계좌번호 필드 없음
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LiveManualApprovalInput:
    """실전 주문 manual approval gate 입력. *현재값* 을 입력으로 받는다.

    operator 이름은 식별자(secret 아님). KIS app_key/secret/계좌번호 필드는
    의도적으로 없다 (오용/노출 차단).
    """

    mode: OperationMode | str | None = None
    live_order_requested: bool = False
    broker_order_type: Optional[str] = None  # "KIS_LIVE" / "KIS_PAPER" / ...
    kis_is_paper: bool = True

    # 단순 env flag — 이것만으로는 절대 허용되지 않음.
    enable_live_trading: bool = False
    enable_ai_execution: bool = False

    # 필수 승인 조건들.
    live_capital_review_approved: bool = False
    manual_approval_present: bool = False
    operator_name: Optional[str] = None
    operator_reason: Optional[str] = None
    operator_approved_at: Optional[str] = None  # ISO timestamp

    symbol: Optional[str] = None
    symbol_whitelist: tuple[str, ...] = ()
    max_order_notional_configured: bool = False
    daily_live_limit_configured: bool = False

    # Paper 재사용 오용 차단.
    paper_approval_reused_as_live: bool = False
    order_payload: Any = None  # paper capital 필드 감지용


@dataclass(frozen=True)
class LiveManualApprovalResult:
    """gate 결과. **order-creation 불변은 항상 False.**"""

    approved: bool
    reason_code: str
    reason_message: str
    mode: str

    requires_live_capital_review: bool
    requires_manual_approval: bool
    requires_operator_approval: bool
    requires_symbol_whitelist: bool
    requires_max_order_notional: bool
    requires_daily_live_limit: bool

    detected_paper_fields: list[str] = field(default_factory=list)

    # 불변 — gate 통과(approved=True)여도 절대 주문을 만들지 않는다.
    is_live_authorization: bool = False
    broker_order_sent: bool = False
    order_created: bool = False
    broker_order_no: Optional[str] = None
    contains_secret: bool = False
    is_order_signal: bool = False

    def __post_init__(self) -> None:
        for name in ("is_live_authorization", "broker_order_sent",
                     "order_created", "contains_secret", "is_order_signal"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be False (gate never authorizes orders)")
        if self.broker_order_no is not None:
            raise ValueError("broker_order_no must be None (no order created)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved":                     bool(self.approved),
            "reason_code":                  self.reason_code,
            "reason_message":               self.reason_message,
            "mode":                         self.mode,
            "requires_live_capital_review": bool(self.requires_live_capital_review),
            "requires_manual_approval":     bool(self.requires_manual_approval),
            "requires_operator_approval":   bool(self.requires_operator_approval),
            "requires_symbol_whitelist":    bool(self.requires_symbol_whitelist),
            "requires_max_order_notional":  bool(self.requires_max_order_notional),
            "requires_daily_live_limit":    bool(self.requires_daily_live_limit),
            "detected_paper_fields":        list(self.detected_paper_fields),
            "is_live_authorization":        False,
            "broker_order_sent":            False,
            "order_created":                False,
            "broker_order_no":              None,
            "contains_secret":              False,
            "is_order_signal":              False,
        }


def _is_live_order_request(inp: LiveManualApprovalInput) -> bool:
    """실전 주문 요청으로 간주되는가."""
    if is_live_order_mode(inp.mode):
        return True
    if inp.live_order_requested:
        return True
    if str(inp.broker_order_type or "").upper() == "KIS_LIVE":
        return True
    if inp.kis_is_paper is False:
        return True
    return False


def _has_operator_approval(inp: LiveManualApprovalInput) -> bool:
    return bool(
        (inp.operator_name or "").strip()
        and (inp.operator_reason or "").strip()
        and (inp.operator_approved_at or "").strip()
    )


def evaluate_live_manual_approval_gate(
    inp: LiveManualApprovalInput,
) -> LiveManualApprovalResult:
    """실전 주문 manual approval gate 평가.

    실전 주문 요청이 아니면 NOT_A_LIVE_ORDER (Paper 경로 무회귀 — 차단 아님).
    실전 주문 요청이면 모든 필수 조건을 검사하고, 하나라도 없으면 차단한다.
    `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` 가 true 여도 *우회되지 않는다.*
    """
    mode_str = inp.mode.value if isinstance(inp.mode, OperationMode) else str(inp.mode)
    detected = detect_paper_capital_in_payload(inp.order_payload)

    # 비-실전 요청 — Paper / 모의 경로. gate 가 차단하지 않는다.
    if not _is_live_order_request(inp):
        return LiveManualApprovalResult(
            approved=False,
            reason_code=NOT_A_LIVE_ORDER,
            reason_message=_MESSAGES_KO[NOT_A_LIVE_ORDER],
            mode=mode_str,
            requires_live_capital_review=False,
            requires_manual_approval=False,
            requires_operator_approval=False,
            requires_symbol_whitelist=False,
            requires_max_order_notional=False,
            requires_daily_live_limit=False,
            detected_paper_fields=detected,
        )

    # ── 실전 주문 요청 — 모든 필수 조건 검사 (우선순위 순). ──
    # 공통 requires_* 는 항상 True (실전 주문에 필요한 조건이므로).
    common = dict(
        mode=mode_str,
        requires_live_capital_review=True,
        requires_manual_approval=True,
        requires_operator_approval=True,
        requires_symbol_whitelist=True,
        requires_max_order_notional=True,
        requires_daily_live_limit=True,
        detected_paper_fields=detected,
    )

    def _block(code: str) -> LiveManualApprovalResult:
        return LiveManualApprovalResult(
            approved=False, reason_code=code,
            reason_message=_MESSAGES_KO[code], **common,
        )

    # 1. Paper 승인/자금을 live 로 재사용 — 절대 금지.
    if inp.paper_approval_reused_as_live or detected:
        return _block(PAPER_APPROVAL_NOT_LIVE_APPROVAL)
    # 2. Live Capital Review.
    if not inp.live_capital_review_approved:
        return _block(LIVE_CAPITAL_REVIEW_REQUIRED)
    # 3. Manual Approval.
    if not inp.manual_approval_present:
        return _block(LIVE_MANUAL_APPROVAL_REQUIRED)
    # 4. Operator Approval (operator + reason + timestamp).
    if not _has_operator_approval(inp):
        return _block(OPERATOR_APPROVAL_REQUIRED)
    # 5. Symbol Whitelist 존재.
    if not inp.symbol_whitelist:
        return _block(SYMBOL_WHITELIST_REQUIRED)
    # 6. 주문 종목이 whitelist 에 포함.
    if not inp.symbol or inp.symbol not in inp.symbol_whitelist:
        return _block(SYMBOL_NOT_WHITELISTED)
    # 7. Max order notional 설정.
    if not inp.max_order_notional_configured:
        return _block(MAX_ORDER_NOTIONAL_REQUIRED)
    # 8. Daily live limit 설정.
    if not inp.daily_live_limit_configured:
        return _block(DAILY_LIVE_LIMIT_REQUIRED)

    # 모든 조건 충족 — *검토* 가능. 그러나 실제 주문은 생성되지 않는다
    # (is_live_authorization / broker_order_sent / order_created 모두 False).
    return LiveManualApprovalResult(
        approved=True,
        reason_code=LIVE_MANUAL_GATE_REVIEW_READY,
        reason_message=_MESSAGES_KO[LIVE_MANUAL_GATE_REVIEW_READY],
        **common,
    )


__all__ = [
    "NOT_A_LIVE_ORDER",
    "LIVE_MANUAL_APPROVAL_REQUIRED",
    "OPERATOR_APPROVAL_REQUIRED",
    "SYMBOL_WHITELIST_REQUIRED",
    "SYMBOL_NOT_WHITELISTED",
    "MAX_ORDER_NOTIONAL_REQUIRED",
    "DAILY_LIVE_LIMIT_REQUIRED",
    "PAPER_APPROVAL_NOT_LIVE_APPROVAL",
    "LIVE_CAPITAL_REVIEW_REQUIRED",
    "LIVE_MANUAL_GATE_REVIEW_READY",
    "LiveManualApprovalInput",
    "LiveManualApprovalResult",
    "evaluate_live_manual_approval_gate",
]
