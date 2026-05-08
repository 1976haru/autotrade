"""Signal Explainability layer (#33).

전략 신호 → SignalQualityGate → MarketRegimeFilter → RiskManager →
PermissionGate → Agent → 운영자에 이르는 의사 결정 사슬의 *근거*를 구조화해
저장/조회할 수 있게 한다.

본 패키지는 *주문 실행 레이어가 아니다* — read/write **audit 설명 레이어**.
broker / RiskManager / PermissionGate / OrderExecutor / route_order 어떤
함수도 호출하지 않으며, 기존 OrderAuditLog / AgentDecisionLog /
PendingApproval 테이블 스키마를 변경하지 않는다.
"""

from app.explainability.reasons import (
    ExplainStatus,
    MissingExplanationError,
    ReasonCategory,
    ReasonSeverity,
    ReasonStatus,
    SignalExplanation,
    SignalReason,
    classify_final_status,
    compose_signal_explanation,
    extract_reasons_from_audit_row,
    require_explanation_before_order,
    summarize_reasons,
)


__all__ = [
    "ExplainStatus",
    "MissingExplanationError",
    "ReasonCategory",
    "ReasonSeverity",
    "ReasonStatus",
    "SignalExplanation",
    "SignalReason",
    "classify_final_status",
    "compose_signal_explanation",
    "extract_reasons_from_audit_row",
    "require_explanation_before_order",
    "summarize_reasons",
]
