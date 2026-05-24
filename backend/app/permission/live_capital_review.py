"""#72 / 9-03: Live Capital Review (read-only, 주문 승인 아님).

실전 주문한도는 Paper 자금과 *분리* 되며, operator approval + max_order_notional +
daily_live_limit + symbol whitelist 가 모두 없으면 live order 가 차단된다. 본 모듈은
이를 *검토 상태* 로 요약 표시한다 — **Live Capital Review 는 주문 승인이 아니다.**

#42 `live_manual_approval_gate.evaluate_live_manual_approval_gate` 를 *그대로 재사용*
한다 (필수 조건 6종: live capital review / manual approval / operator approval /
symbol whitelist / max order notional / daily live limit). 본 모듈은 그 결과를 사람이
읽는 review 상태(MISSING / INCOMPLETE / READY)로 변환할 뿐, 새 권한 로직을 만들지
않는다.

**검토를 기록해도 주문은 생성되지 않는다** — order_created / broker_order_sent /
is_live_authorization 항상 False.

CLAUDE.md 가드 (정적 grep 으로 lock):
- broker / OrderExecutor / 단일 주문 라우터 / KIS 어댑터 import 0건.
- broker 주문/취소 호출 0건, DB write 0건, 외부 HTTP / AI SDK import 0건.
- secret/계좌번호 carry 0건 (operator 이름/사유는 식별자, 원문 secret 아님).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.permission.live_manual_approval_gate import (
    LiveManualApprovalInput,
    evaluate_live_manual_approval_gate,
)

# review 상태.
REVIEW_MISSING = "MISSING"        # live 요청 아님 / 검토 입력 없음.
REVIEW_INCOMPLETE = "INCOMPLETE"  # live 요청이나 필수 조건 미충족.
REVIEW_READY = "READY"            # 모든 필수 조건 충족 (검토 readiness — 주문 아님).

DISCLAIMER_KO = (
    "Live Capital Review 는 주문 승인이 아닙니다. 모든 조건이 충족(READY)되어도 실제 "
    "주문은 생성되지 않으며, 별도 Manual Approval / Canary / place_order 가드를 거쳐야 "
    "합니다. Paper 자금은 실전 자금으로 사용되지 않습니다. 수익을 보장하지 않습니다."
)


@dataclass(frozen=True)
class LiveCapitalReview:
    """실전 자본 검토 상태 — read-only. 주문 승인/생성 아님."""

    review_present:    bool
    approval_status:   str            # MISSING / INCOMPLETE / READY
    reason_code:       str
    reason_message:    str
    requirements:      dict[str, bool]   # 조건별 충족 여부.
    max_order_notional_configured: bool
    daily_live_limit_configured:   bool
    symbol_whitelist_count:        int
    operator_approval_present:     bool
    paper_capital_separated:       bool   # Paper 자금이 live 로 안 새는지 (항상 True 의도).

    # 불변 — 검토는 주문을 만들지 않는다.
    is_order_signal:       bool = False
    is_live_authorization: bool = False
    broker_order_sent:     bool = False
    order_created:         bool = False
    contains_secret:       bool = False

    def __post_init__(self) -> None:
        for name in ("is_order_signal", "is_live_authorization",
                     "broker_order_sent", "order_created", "contains_secret"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be False (review 는 주문 승인 아님)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_present":   bool(self.review_present),
            "approval_status":  self.approval_status,
            "reason_code":      self.reason_code,
            "reason_message":   self.reason_message,
            "requirements":     dict(self.requirements),
            "max_order_notional_configured": bool(self.max_order_notional_configured),
            "daily_live_limit_configured":   bool(self.daily_live_limit_configured),
            "symbol_whitelist_count":        int(self.symbol_whitelist_count),
            "operator_approval_present":     bool(self.operator_approval_present),
            "paper_capital_separated":       bool(self.paper_capital_separated),
            "is_order_signal":       False,
            "is_live_authorization": False,
            "broker_order_sent":     False,
            "order_created":         False,
            "contains_secret":       False,
            "disclaimer":            DISCLAIMER_KO,
        }


def build_live_capital_review(inp: LiveManualApprovalInput) -> LiveCapitalReview:
    """#42 gate 결과 → Live Capital Review 상태 요약 (주문 생성 0건).

    Paper 자금은 live 로 사용되지 않으며(분리), gate 가 detect 한 paper capital 필드는
    review 에서 *경고* 로만 carry (한도 계산에 사용 안 함).
    """
    gate = evaluate_live_manual_approval_gate(inp)

    # operator approval 충족: 이름 + 사유 + 시각 모두 존재.
    operator_present = bool(inp.operator_name and inp.operator_reason
                            and inp.operator_approved_at and inp.manual_approval_present)
    whitelist_count = len(inp.symbol_whitelist or ())

    requirements = {
        "live_capital_review": bool(inp.live_capital_review_approved),
        "manual_approval":     bool(inp.manual_approval_present),
        "operator_approval":   operator_present,
        "symbol_whitelist":    whitelist_count > 0,
        "max_order_notional":  bool(inp.max_order_notional_configured),
        "daily_live_limit":    bool(inp.daily_live_limit_configured),
    }

    # live 요청이 아니면 review MISSING (Paper 경로 — 차단 아님).
    if gate.reason_code == "NOT_A_LIVE_ORDER":
        status = REVIEW_MISSING
        review_present = False
    elif gate.approved:
        status = REVIEW_READY
        review_present = True
    else:
        status = REVIEW_INCOMPLETE
        review_present = any(requirements.values())

    # paper capital 이 live 주문 payload 에 섞였는지 — 분리 여부 (섞였어도 한도엔 미사용).
    paper_separated = True   # 본 review 는 paper capital 을 live 한도로 절대 사용 안 함.

    return LiveCapitalReview(
        review_present=review_present,
        approval_status=status,
        reason_code=gate.reason_code,
        reason_message=gate.reason_message,
        requirements=requirements,
        max_order_notional_configured=bool(inp.max_order_notional_configured),
        daily_live_limit_configured=bool(inp.daily_live_limit_configured),
        symbol_whitelist_count=whitelist_count,
        operator_approval_present=operator_present,
        paper_capital_separated=paper_separated,
    )


__all__ = [
    "REVIEW_MISSING", "REVIEW_INCOMPLETE", "REVIEW_READY",
    "LiveCapitalReview", "build_live_capital_review",
]
