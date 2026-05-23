"""P-20: Paper capital ↔ Live capital 분리 가드 (read-only, advisory).

**핵심 원칙**: Paper 자금 설정(시드머니 / 종목당 투자금 / 일일 매수 한도 /
최대 보유 / risk profile)은 *가상 자금* 이며, 실전 주문 금액 / 수량 / 한도로
*자동 연결되면 안 된다*. Live 전환 시에는 별도 live capital review + manual
approval + promotion gate 가 필요하다.

본 모듈은:
- Live 주문 경로의 요청 payload 에 paper capital 필드가 섞여 들어오면 *감지* 하고
  "Live 주문 한도로 사용하지 않음"(`paper_capital_ignored=True`) 을 명시한다.
- live capital 이 별도 검토/승인되지 않았으면 차단 (`LIVE_CAPITAL_REVIEW_
  REQUIRED`).
- **본 PR 은 실전 활성화 기능이 아니다** — `live_capital_approved` 는 항상
  False, `is_live_authorization` 은 항상 False (placeholder + 오용 차단 전용).

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order / 외부 HTTP / AI SDK import 0건.
- 안전 flag (`ENABLE_LIVE_TRADING` 등) 변경 0건.
- paper capital 값을 live order notional 계산에 *사용하지 않는다* (감지/차단만).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.modes import OperationMode

# ─────────────────────────────────────────────────────────────────────────────
# reason_code 상수 (사용자 요청서 §1.7 / §2.4)
# ─────────────────────────────────────────────────────────────────────────────

PAPER_CAPITAL_NOT_LIVE_CAPITAL    = "PAPER_CAPITAL_NOT_LIVE_CAPITAL"
LIVE_CAPITAL_REVIEW_REQUIRED      = "LIVE_CAPITAL_REVIEW_REQUIRED"
LIVE_ORDER_NOTIONAL_NOT_CONFIGURED = "LIVE_ORDER_NOTIONAL_NOT_CONFIGURED"
LIVE_CAPITAL_PERMISSION_DENIED    = "LIVE_CAPITAL_PERMISSION_DENIED"
# 비-live 모드에서 paper capital 은 정상 사용 — 차단 아님.
PAPER_MODE_PAPER_CAPITAL_OK       = "PAPER_MODE_PAPER_CAPITAL_OK"

_MESSAGES_KO: dict[str, str] = {
    PAPER_CAPITAL_NOT_LIVE_CAPITAL:     "Paper 자금 설정은 실전 주문 한도가 아닙니다.",
    LIVE_CAPITAL_REVIEW_REQUIRED:       "실전 주문에는 별도 Live 자금 검토가 필요합니다.",
    LIVE_ORDER_NOTIONAL_NOT_CONFIGURED: "실전 주문 금액(live order notional)이 설정되지 않았습니다.",
    LIVE_CAPITAL_PERMISSION_DENIED:     "실전 자금 권한이 거부되었습니다.",
    PAPER_MODE_PAPER_CAPITAL_OK:        "현재 설정은 Paper / AI Paper 전용입니다.",
}


# live capital review 상태.
REVIEW_REQUIRED       = "REQUIRED"
REVIEW_NOT_CONFIGURED = "NOT_CONFIGURED"
REVIEW_APPROVED       = "APPROVED"


# 실전 주문 *발신* 이 가능한 (또는 시도되는) 모드 — paper capital 자동 사용 금지.
_LIVE_ORDER_MODES = frozenset({
    OperationMode.LIVE_MANUAL_APPROVAL,
    OperationMode.LIVE_AI_ASSIST,
    OperationMode.LIVE_AI_EXECUTION,
})

# paper capital 로 식별되는 payload 키 — live 주문 한도로 *오용 금지*.
PAPER_CAPITAL_FIELD_NAMES = frozenset({
    "paper_capital_settings",
    "capital_settings",
    "total_paper_capital",
    "per_symbol_allocation",
    "max_daily_buy_amount",
    "max_positions",
    "max_symbol_weight_pct",
    "allow_additional_buy",
    "paper_initial_cash",
    "initial_cash",
    "risk_profile",
})


def is_live_order_mode(mode: OperationMode | str | None) -> bool:
    """LIVE_MANUAL_APPROVAL / LIVE_AI_ASSIST / LIVE_AI_EXECUTION 여부.

    SIMULATION / PAPER / LIVE_SHADOW 는 *실주문 발신 모드가 아님* — paper
    capital 을 (paper sizing 에) 정상 사용한다.
    """
    if mode is None:
        return False
    if isinstance(mode, str):
        try:
            mode = OperationMode(mode)
        except ValueError:
            return False
    return mode in _LIVE_ORDER_MODES


def detect_paper_capital_in_payload(payload: Any) -> list[str]:
    """payload 에 섞여 들어온 paper capital 필드명을 감지 (정렬 list)."""
    if not isinstance(payload, dict):
        return []
    found = {k for k in payload.keys() if str(k) in PAPER_CAPITAL_FIELD_NAMES}
    return sorted(found)


@dataclass(frozen=True)
class LiveCapitalReviewResult:
    """Live 자금 검토 결과 — Paper capital 오용 차단 + placeholder.

    **본 PR 에서 `live_capital_approved` / `is_live_authorization` 은 항상
    False** (실전 활성화 기능 아님).
    """

    allowed:                  bool
    reason_code:              str
    reason_message:           str
    mode:                     str
    paper_capital_ignored:    bool
    live_capital_review_status: str
    detected_paper_fields:    list[str] = field(default_factory=list)

    live_capital_approved:    bool = False
    is_live_authorization:    bool = False
    is_order_signal:          bool = False
    is_paper_capital_input:   bool = False

    def __post_init__(self) -> None:
        if self.live_capital_approved is not False:
            raise ValueError("live_capital_approved must be False (placeholder PR)")
        if self.is_live_authorization is not False:
            raise ValueError("is_live_authorization must be False")
        if self.is_order_signal is not False:
            raise ValueError("is_order_signal must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed":                    bool(self.allowed),
            "reason_code":                self.reason_code,
            "reason_message":             self.reason_message,
            "mode":                       self.mode,
            "paper_capital_ignored":      bool(self.paper_capital_ignored),
            "live_capital_review_status": self.live_capital_review_status,
            "detected_paper_fields":      list(self.detected_paper_fields),
            "live_capital_approved":      False,
            "is_live_authorization":      False,
            "is_order_signal":            False,
            "is_paper_capital_input":     bool(self.is_paper_capital_input),
        }


def evaluate_live_capital_authorization(
    *,
    mode: OperationMode | str | None,
    order_payload: Any = None,
    live_capital_approved: bool = False,
    live_order_notional_configured: bool = False,
) -> LiveCapitalReviewResult:
    """Live 주문 자금 권한 평가 — Paper capital 오용 차단 + review 게이트.

    - 비-live 모드(SIMULATION/PAPER/LIVE_SHADOW): paper capital 은 paper sizing
      에 정상 사용 → allowed=True, `PAPER_MODE_PAPER_CAPITAL_OK`,
      `paper_capital_ignored=False`. (실전 권한 부여 아님.)
    - live 주문 모드: paper capital 은 *항상 무시* (`paper_capital_ignored=True`).
      우선순위:
        1. payload 에 paper capital 필드 발견 → PAPER_CAPITAL_NOT_LIVE_CAPITAL
        2. live order notional 미설정 → LIVE_ORDER_NOTIONAL_NOT_CONFIGURED
        3. live capital 미승인 → LIVE_CAPITAL_REVIEW_REQUIRED
      **본 PR 에서 `live_capital_approved` 가 True 여도 코드 경로상 실전 주문은
      생성되지 않는다** (RiskManager / PermissionGate / OrderExecutor 가 별도
      정책으로 계속 차단).
    """
    detected = detect_paper_capital_in_payload(order_payload)
    mode_str = mode.value if isinstance(mode, OperationMode) else str(mode)

    if not is_live_order_mode(mode):
        return LiveCapitalReviewResult(
            allowed=True,
            reason_code=PAPER_MODE_PAPER_CAPITAL_OK,
            reason_message=_MESSAGES_KO[PAPER_MODE_PAPER_CAPITAL_OK],
            mode=mode_str,
            paper_capital_ignored=False,
            live_capital_review_status=REVIEW_NOT_CONFIGURED,
            detected_paper_fields=detected,
            is_paper_capital_input=bool(detected),
        )

    # ── live 주문 모드: paper capital 은 절대 live 한도로 사용하지 않는다. ──
    review_status = (
        REVIEW_APPROVED if live_capital_approved
        else (REVIEW_NOT_CONFIGURED if not live_order_notional_configured
              else REVIEW_REQUIRED)
    )

    if detected:
        code = PAPER_CAPITAL_NOT_LIVE_CAPITAL
    elif not live_order_notional_configured:
        code = LIVE_ORDER_NOTIONAL_NOT_CONFIGURED
    elif not live_capital_approved:
        code = LIVE_CAPITAL_REVIEW_REQUIRED
    else:
        # 도달 불가 (본 PR 에서 approved=True 라도 별도 게이트가 차단) — 안전 측.
        code = LIVE_CAPITAL_PERMISSION_DENIED

    return LiveCapitalReviewResult(
        allowed=False,
        reason_code=code,
        reason_message=_MESSAGES_KO[code],
        mode=mode_str,
        paper_capital_ignored=True,
        live_capital_review_status=review_status,
        detected_paper_fields=detected,
        is_paper_capital_input=bool(detected),
    )


__all__ = [
    "PAPER_CAPITAL_NOT_LIVE_CAPITAL",
    "LIVE_CAPITAL_REVIEW_REQUIRED",
    "LIVE_ORDER_NOTIONAL_NOT_CONFIGURED",
    "LIVE_CAPITAL_PERMISSION_DENIED",
    "PAPER_MODE_PAPER_CAPITAL_OK",
    "PAPER_CAPITAL_FIELD_NAMES",
    "REVIEW_REQUIRED", "REVIEW_NOT_CONFIGURED", "REVIEW_APPROVED",
    "LiveCapitalReviewResult",
    "is_live_order_mode",
    "detect_paper_capital_in_payload",
    "evaluate_live_capital_authorization",
]
