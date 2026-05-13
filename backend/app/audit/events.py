"""체크리스트 #68: 통합 감사 이벤트 facade.

기존 도메인별 테이블(OrderAuditLog / PendingApproval / AgentDecisionLog /
EmergencyStopEvent / VirtualOrder / FuturesOrderAuditLog)을 *대체하지 않고*
cross-cutting timeline + Secret redaction + append-only invariant를 제공.

절대 원칙 (CLAUDE.md + #68):
1. `log_audit_event(...)`는 *raise하지 않는다* — caller는 try/except 없이도 부를
   수 있어야 한다. 알림 hook과 동일한 안전성. 단, Secret 패턴 발견 시는 명시적
   `SecretLeakError`로 거부 (fail-closed) — Secret 누출이 silent해지지 않게.
   호출자가 *반드시* `try/except SecretLeakError`로 감싸 처리.
2. 본 모듈은 broker / OrderExecutor / route_order import 0건.
3. 본 모듈은 *기존 audit 테이블을 수정 / 삭제하지 않는다*. 새 audit_event row만
   INSERT.
4. row delete 0건 — `archive_event` 함수만 노출. `archived=True`로 표시.
5. Secret 패턴은 redaction이 아닌 *거부* — 호출자가 sanitize한 후 다시 시도해야.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditEvent


# ====================================================================
# Enums (StrEnum — JSON / API surface에 그대로 사용 가능)
# ====================================================================


class EventType(StrEnum):
    """감사 이벤트 분류. 새 type 추가는 자유 — 운영자가 timeline filter로 사용."""
    SIGNAL              = "SIGNAL"
    ORDER_REQUEST       = "ORDER_REQUEST"
    APPROVAL_DECISION   = "APPROVAL_DECISION"
    RISK_BLOCK          = "RISK_BLOCK"
    AI_PROPOSAL         = "AI_PROPOSAL"
    EMERGENCY_STOP      = "EMERGENCY_STOP"
    VIRTUAL_ORDER       = "VIRTUAL_ORDER"
    FUTURES_RISK        = "FUTURES_RISK"
    NOTIFICATION        = "NOTIFICATION"
    OPERATOR_NOTE       = "OPERATOR_NOTE"
    STRATEGY_CHANGE     = "STRATEGY_CHANGE"
    DATA_QUALITY        = "DATA_QUALITY"
    SYSTEM              = "SYSTEM"


class Severity(StrEnum):
    INFO     = "INFO"
    WARN     = "WARN"
    CRITICAL = "CRITICAL"
    SECURITY = "SECURITY"


class SourceKind(StrEnum):
    """이벤트 발생 주체. AgentMemory와 SourceKind 중복하지 않게 별도 enum."""
    STRATEGY  = "STRATEGY"
    AI        = "AI"
    MANUAL    = "MANUAL"
    SYSTEM    = "SYSTEM"
    OPERATOR  = "OPERATOR"
    SCHEDULER = "SCHEDULER"


# ====================================================================
# Secret patterns (fail-closed — redaction 아님)
# ====================================================================


class SecretLeakError(ValueError):
    """audit row INSERT 직전 Secret 패턴 검출 시 raise. 호출자가 sanitize 후
    재시도해야 함. 본 PR은 redaction이 아닌 *거부* 정책 — Secret 누출이 silent
    하게 audit row에 들어가는 사고를 차단."""


# AgentMemory.sanitize_text()와 의도적으로 유사 — 한 곳에서 통합하려면 후속 PR.
# 본 PR 시점에는 audit facade에 보수적으로 자체 룰 정의.
_SECRET_PATTERNS = [
    # API key 일반
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),               # OpenAI / Anthropic style
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{10,}\b", re.IGNORECASE),
    # KIS
    re.compile(r"\bPST[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bKIS_APP_KEY\s*=\s*[A-Za-z0-9_-]{8,}", re.IGNORECASE),
    re.compile(r"\bKIS_APP_SECRET\s*=\s*[A-Za-z0-9_/+=]{16,}", re.IGNORECASE),
    re.compile(r"\bKIS_ACCOUNT_NO\s*=\s*\d{6,}", re.IGNORECASE),
    # Anthropic / OpenAI env style
    re.compile(r"\bANTHROPIC_API_KEY\s*=\s*\S+", re.IGNORECASE),
    re.compile(r"\bOPENAI_API_KEY\s*=\s*\S+", re.IGNORECASE),
    # Telegram
    re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{20,}\b"),         # bot token shape
    re.compile(r"\bTELEGRAM_BOT_TOKEN\s*=\s*\S+", re.IGNORECASE),
    re.compile(r"\bTELEGRAM_CHAT_ID\s*=\s*-?\d{5,}", re.IGNORECASE),
    # 한국 계좌번호 / 주민등록번호 / 카드 (보수적 — false positive 가능)
    # 한국 계좌번호 형식 다양: 3-2-5 / 3-3-6 등. \d{4,} 로 5자리 이상 suffix 포착.
    re.compile(r"\b\d{3}-\d{2}-\d{4,}\b"),
    re.compile(r"\b\d{6}-\d{7}\b"),
    re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),
]


def _scan_for_secret(value: Any) -> tuple[bool, str]:
    """str / dict / list를 재귀 검사. 패턴 발견 시 (True, 매칭 단어 한 줄)."""
    if value is None:
        return False, ""
    if isinstance(value, str):
        for pat in _SECRET_PATTERNS:
            if pat.search(value):
                return True, f"matched_pattern: {pat.pattern[:48]!r}"
        return False, ""
    if isinstance(value, dict):
        for k, v in value.items():
            # 키 이름도 검사 — TELEGRAM_BOT_TOKEN: '...' 같은 케이스 차단
            if isinstance(k, str):
                hit, why = _scan_for_secret(k)
                if hit:
                    return True, f"key={k!r}; {why}"
            hit, why = _scan_for_secret(v)
            if hit:
                return True, f"key={k!r}; {why}"
        return False, ""
    if isinstance(value, (list, tuple, set)):
        for item in value:
            hit, why = _scan_for_secret(item)
            if hit:
                return True, why
        return False, ""
    # 그 외 type — int / bool / float / datetime 등은 검사 대상 X
    return False, ""


# ====================================================================
# Public API — log_audit_event
# ====================================================================


@dataclass(frozen=True)
class AuditEventInput:
    """log_audit_event의 표준 입력 dataclass."""
    event_type:  EventType | str
    summary:     str
    severity:    Severity | str   = Severity.INFO
    source:      SourceKind | str = SourceKind.SYSTEM
    actor:       str | None       = None
    symbol:      str | None       = None
    strategy:    str | None       = None
    mode:        str | None       = None
    target_kind: str | None       = None
    target_id:   int | None       = None
    reason:      str | None       = None
    details:     dict[str, Any] | None = None


def log_audit_event(
    db: Session,
    *,
    event_type:  EventType | str,
    summary:     str,
    severity:    Severity | str   = Severity.INFO,
    source:      SourceKind | str = SourceKind.SYSTEM,
    actor:       str | None       = None,
    symbol:      str | None       = None,
    strategy:    str | None       = None,
    mode:        str | None       = None,
    target_kind: str | None       = None,
    target_id:   int | None       = None,
    reason:      str | None       = None,
    details:     dict[str, Any] | None = None,
    commit:      bool             = True,
) -> AuditEvent:
    """단일 감사 이벤트를 audit_event 테이블에 영구화.

    Secret 패턴이 summary / reason / details에 발견되면 `SecretLeakError`로
    raise. 호출자는 반드시 try/except로 감싸야 한다 (`raises a clear error to
    keep secret leaks loud, not silent`).

    Args:
        commit: True면 즉시 commit. False면 호출자가 트랜잭션 관리.

    Returns:
        영구화된 AuditEvent row (id, created_at 채워짐).

    Raises:
        SecretLeakError: summary / reason / details에 Secret 패턴 발견 시.
    """
    # Secret 검사 — fail-closed
    for label, candidate in (
        ("summary", summary), ("reason", reason), ("details", details),
        ("actor", actor), ("symbol", symbol), ("strategy", strategy),
    ):
        hit, why = _scan_for_secret(candidate)
        if hit:
            raise SecretLeakError(
                f"audit event field {label!r} contains forbidden secret pattern "
                f"({why}). caller must sanitize before logging."
            )

    # str / enum 정규화 (StrEnum은 str의 subclass라 .value 안 써도 안전하지만
    # 명시적 변환으로 column 길이 검사가 깔끔)
    row = AuditEvent(
        event_type=str(event_type),
        severity=str(severity),
        source=str(source),
        actor=actor,
        symbol=symbol,
        strategy=strategy,
        mode=mode,
        target_kind=target_kind,
        target_id=target_id,
        summary=summary[:255],
        reason=(reason or None) if reason is None else reason[:255],
        details=details,
        # archived는 default False — 본 함수는 그것을 변경하지 않음
    )
    db.add(row)
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


# ====================================================================
# archive_event — delete를 *대체*. row는 보존, archived=True만 set.
# ====================================================================


class AuditEventNotFoundError(LookupError):
    """archive 대상 row가 없거나 이미 archived. 호출자는 404로 surface."""


def archive_event(
    db: Session,
    event_id: int,
    *,
    archived_by: str | None = None,
    note:        str | None = None,
    commit:      bool       = True,
) -> AuditEvent:
    """audit_event row를 archive 처리. delete가 아니며 row는 영구 보존.

    이미 archived 인 row는 멱등 — 같은 결과 반환 (archived_by / note는
    덮어쓰지 *않는다*; 최초 archive 정보 보존).
    """
    row = db.execute(
        select(AuditEvent).where(AuditEvent.id == event_id)
    ).scalar_one_or_none()
    if row is None:
        raise AuditEventNotFoundError(f"audit_event {event_id} not found")
    if row.archived:
        return row  # 멱등
    row.archived     = True
    row.archived_at  = datetime.now(timezone.utc)
    row.archived_by  = (archived_by or None) if archived_by is None else archived_by[:64]
    row.archive_note = (note or None) if note is None else note[:255]
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


# ====================================================================
# Helper builders — 호출자 편의 (직접 dict 안 만들도록)
# ====================================================================


def build_signal_event(
    *, symbol: str, action: str, strategy: str,
    confidence: int | None = None,
    reasons: list[str] | None = None,
) -> AuditEventInput:
    """전략 / Agent 신호 이벤트."""
    return AuditEventInput(
        event_type=EventType.SIGNAL,
        severity=Severity.INFO,
        source=SourceKind.STRATEGY,
        symbol=symbol,
        strategy=strategy,
        summary=f"signal {action} on {symbol}",
        details={
            "action":     action,
            "confidence": confidence,
            "reasons":    list(reasons or []),
        },
    )


def build_risk_block_event(
    *, symbol: str | None, reasons: list[str],
    requested_by_ai: bool = False, audit_id: int | None = None,
) -> AuditEventInput:
    """RiskManager가 차단한 주문 (REJECTED / BLOCKED)."""
    return AuditEventInput(
        event_type=EventType.RISK_BLOCK,
        severity=Severity.WARN,
        source=SourceKind.AI if requested_by_ai else SourceKind.STRATEGY,
        symbol=symbol,
        summary="risk manager blocked order",
        reason=" / ".join(reasons[:3]) if reasons else None,
        target_kind="OrderAuditLog" if audit_id else None,
        target_id=audit_id,
        details={"requested_by_ai": bool(requested_by_ai),
                 "reasons": list(reasons)},
    )


def build_ai_proposal_event(
    *, symbol: str, side: str, quantity: int, model: str | None = None,
    confidence: int | None = None,
    supporting_reasons: list[str] | None = None,
    opposing_reasons:   list[str] | None = None,
    risk_note: str | None = None,
    target_kind: str | None = None, target_id: int | None = None,
) -> AuditEventInput:
    """AI 제안 — 본 enum은 'AI는 직접 주문 안 함' invariant 명시.

    호출자는 본 helper를 통해서만 AI 이벤트를 생성 권장. raw details에 Secret
    문자열을 우연히 끼우는 사고를 피하기 위해 confidence / supporting / opposing
    / risk_note만 받는다.
    """
    return AuditEventInput(
        event_type=EventType.AI_PROPOSAL,
        severity=Severity.INFO,
        source=SourceKind.AI,
        symbol=symbol,
        summary=f"AI proposed {side} {quantity}x {symbol}",
        target_kind=target_kind,
        target_id=target_id,
        details={
            "side":               side,
            "quantity":           int(quantity),
            "model":              model,
            "confidence":         confidence,
            "supporting_reasons": list(supporting_reasons or []),
            "opposing_reasons":   list(opposing_reasons or []),
            "risk_note":          risk_note,
            "is_order_intent":    False,
        },
    )


def build_emergency_stop_event(
    *, enabled: bool, level: str | None = None,
    reason_code: str | None = None,
    decided_by: str | None = None,
    note: str | None = None,
    target_id: int | None = None,
) -> AuditEventInput:
    """Emergency stop 토글 이벤트. ON=CRITICAL, OFF=INFO."""
    return AuditEventInput(
        event_type=EventType.EMERGENCY_STOP,
        severity=Severity.CRITICAL if enabled else Severity.INFO,
        source=SourceKind.OPERATOR if decided_by else SourceKind.SYSTEM,
        actor=decided_by,
        summary=(
            f"emergency stop ENABLED at {level}"
            if enabled
            else "emergency stop DISABLED"
        ),
        reason=reason_code,
        target_kind="EmergencyStopEvent" if target_id else None,
        target_id=target_id,
        details={
            "enabled": bool(enabled),
            "level":   level,
            "note":    note,
        },
    )


def build_approval_decision_event(
    *, approval_id: int, decision: str,
    decided_by: str | None = None,
    note: str | None = None,
    requested_by_ai: bool = False,
    symbol: str | None = None,
) -> AuditEventInput:
    """결재 큐의 approve / reject / cancel / expire 이벤트."""
    return AuditEventInput(
        event_type=EventType.APPROVAL_DECISION,
        severity=Severity.INFO if decision == "APPROVED" else Severity.WARN,
        source=SourceKind.OPERATOR if decided_by else SourceKind.SYSTEM,
        actor=decided_by,
        symbol=symbol,
        summary=f"approval {decision} (#{approval_id})",
        reason=note,
        target_kind="PendingApproval",
        target_id=approval_id,
        details={
            "decision":        decision,
            "requested_by_ai": bool(requested_by_ai),
        },
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
