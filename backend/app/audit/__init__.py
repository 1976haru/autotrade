"""체크리스트 #68: app/audit/ — 통합 감사 이벤트 facade.

기존 도메인 테이블(OrderAuditLog / PendingApproval / AgentDecisionLog 등)을
*대체하지 않고*, 그 위에 cross-cutting timeline + Secret redaction + append-
only invariant를 제공한다.

자세한 정책: [`docs/audit_log_policy.md`](../../../docs/audit_log_policy.md).
"""

from app.audit.events import (
    AuditEventInput,
    AuditEventNotFoundError,
    EventType,
    SecretLeakError,
    Severity,
    SourceKind,
    archive_event,
    build_ai_proposal_event,
    build_approval_decision_event,
    build_emergency_stop_event,
    build_risk_block_event,
    build_signal_event,
    log_audit_event,
)

__all__ = [
    "AuditEventInput",
    "AuditEventNotFoundError",
    "EventType",
    "SecretLeakError",
    "Severity",
    "SourceKind",
    "archive_event",
    "build_ai_proposal_event",
    "build_approval_decision_event",
    "build_emergency_stop_event",
    "build_risk_block_event",
    "build_signal_event",
    "log_audit_event",
]
