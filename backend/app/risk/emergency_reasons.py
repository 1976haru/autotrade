"""Emergency Stop Reason Taxonomy (153, MUST).

CLAUDE.md '감사 로그를 우선한다' — 운영자가 emergency_stop을 토글한 이유를
구조화된 코드로 기록해 사후 분석과 reason별 집계가 가능하게 한다.

코드는 자유 문자열 대신 `EmergencyStopReason` 열거형을 강제 — 미등록 코드는
거부한다 (validation은 routes_risk에서 수행).
"""

from enum import StrEnum


class EmergencyStopReason(StrEnum):
    """긴급 정지 토글 사유. NULL은 (a) 0011 마이그레이션 이전 기록 또는 (b)
    운영자가 사유를 명시하지 않은 토글 — frontend는 dropdown 선택을 강제하지만
    backwards compat 보존."""
    MANUAL_OPERATOR             = "manual_operator"             # 운영자 수동 정지
    DAILY_LOSS_LIMIT            = "daily_loss_limit"            # 일일 손실 한도
    DATA_STALE                  = "data_stale"                  # 시세 stale 검출 (143)
    BROKER_ERROR                = "broker_error"                # broker 응답 이상
    REPEATED_ORDER_FAILURE      = "repeated_order_failure"      # 연속 주문 실패
    ABNORMAL_SLIPPAGE           = "abnormal_slippage"           # 비정상 슬리피지
    AGENT_WARNING               = "agent_warning"               # AI Agent 경고
    MARGIN_RISK                 = "margin_risk"                 # 선물 증거금 위험
    FUTURES_LIQUIDATION_RISK    = "futures_liquidation_risk"    # 강제청산 임박


# Pydantic / API 검증에서 한번에 사용하기 위한 set.
EMERGENCY_STOP_REASONS: set[str] = {r.value for r in EmergencyStopReason}


def is_valid_reason(code: str | None) -> bool:
    """None은 허용 (legacy / 미명시). 그 외엔 등록된 코드만 통과."""
    if code is None:
        return True
    return code in EMERGENCY_STOP_REASONS
