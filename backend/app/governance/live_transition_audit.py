"""5-05: Live 전환 감사 로그 (append-only, read/append-only).

운영자가 *언제, 어떤 조건으로* 실전 전환을 검토/거절/철회했는지 추적하기 위한
append-only 감사 로그. operator / reason / timestamp / action + 게이트 snapshot
(capital review / risk profile / symbol whitelist / max notional / daily limit /
Paper Gate verdict / Canary Gate verdict / Manual Approval)을 기록한다.

**본 모듈은 실전 전환을 승인하거나 실전 주문을 생성하지 않는다.** 감사 로그는
주문 신호가 아니며(`is_order_signal=False`), 기록이 있어도
`is_live_authorization` / `broker_order_sent` / `order_created` 는 *항상 False*.
기록 후에도 Live Capital Review(#41) + Manual Approval(#42) + Canary Gate(#43) +
Paper Gate 성과 기준(#44)이 별도로 필요하다.

append-only 원칙:
- 기존 record 를 *수정/삭제하지 않는다* — update/delete 메서드·API 0건.
- 정정이 필요하면 새 `LIVE_AUDIT_NOTE_ADDED` 또는 `LIVE_APPROVAL_REVOKED`
  이벤트를 추가한다. `previous_audit_id` 로 연결.
- secret/account/API key 원문 저장 0건 — 입력은 sanitize(fail-closed)를 통과해야
  기록되며, 적중 시 기록 거부(SecretLeakError).

CLAUDE.md 절대 원칙: broker / OrderExecutor / route_order / KIS live endpoint /
외부 HTTP / AI SDK import·호출 0건. 안전 flag 변경 0건.
"""

from __future__ import annotations

import json as _json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Optional

from app.agents.agent_memory import SecretLeakError, sanitize_dict, sanitize_text

# agent_memory sanitize 가 놓치는 형식 보강 (KIS 8-2 계좌번호 등). 감사 로그는
# 보안상 *더 엄격* — 적중 시 fail-closed.
_EXTRA_SECRET_PATTERNS = [
    re.compile(r"\b\d{8}-\d{2}\b"),                    # KIS 계좌번호 8-2
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}", re.I),
    re.compile(r"(?:access_token|refresh_token)\s*[:=]\s*[A-Za-z0-9._\-]{8,}", re.I),
]

# ── 표준 action — "APPROVED"(실제 주문 승인 오해 소지) 대신 REVIEW/READY 표현. ──
STANDARD_ACTIONS = frozenset({
    "LIVE_REVIEW_REQUESTED",
    "LIVE_REVIEW_REJECTED",
    "LIVE_REVIEW_READY_RECORDED",
    "LIVE_CANARY_REVIEW_REQUESTED",
    "LIVE_CANARY_REVIEW_REJECTED",
    "LIVE_APPROVAL_REVOKED",
    "LIVE_AUDIT_NOTE_ADDED",
})

# ── reason_code ──────────────────────────────────────────────────────────────
LIVE_AUDIT_RECORDED         = "LIVE_AUDIT_RECORDED"
LIVE_AUDIT_OPERATOR_REQUIRED = "LIVE_AUDIT_OPERATOR_REQUIRED"
LIVE_AUDIT_REASON_REQUIRED  = "LIVE_AUDIT_REASON_REQUIRED"
LIVE_AUDIT_INVALID_ACTION   = "LIVE_AUDIT_INVALID_ACTION"
LIVE_AUDIT_SECRET_BLOCKED   = "LIVE_AUDIT_SECRET_BLOCKED"


class LiveAuditError(ValueError):
    """감사 기록 검증 실패 — reason_code carry."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class LiveTransitionAuditEntry:
    """불변 감사 record. 생성 후 수정 불가 (frozen)."""

    audit_id: str
    created_at: str
    operator: str
    action: str
    reason: str
    reason_code: str

    risk_profile: Optional[str] = None
    symbol_whitelist: tuple[str, ...] = ()
    max_order_notional: Optional[int] = None
    daily_live_limit: Optional[int] = None
    capital_review_snapshot: dict[str, Any] = field(default_factory=dict)
    paper_gate_verdict: dict[str, Any] = field(default_factory=dict)
    canary_gate_verdict: dict[str, Any] = field(default_factory=dict)
    manual_approval_snapshot: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    previous_audit_id: Optional[str] = None

    # 불변 — 감사 로그는 주문 신호/실전 승인이 아니다.
    is_order_signal: bool = False
    is_live_authorization: bool = False
    broker_order_sent: bool = False
    order_created: bool = False
    auto_apply_allowed: bool = False
    contains_secret: bool = False

    def __post_init__(self) -> None:
        for name in ("is_order_signal", "is_live_authorization", "broker_order_sent",
                     "order_created", "auto_apply_allowed", "contains_secret"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be False (audit log never authorizes)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id":                 self.audit_id,
            "created_at":               self.created_at,
            "operator":                 self.operator,
            "action":                   self.action,
            "reason":                   self.reason,
            "reason_code":              self.reason_code,
            "risk_profile":             self.risk_profile,
            "symbol_whitelist":         list(self.symbol_whitelist),
            "max_order_notional":       self.max_order_notional,
            "daily_live_limit":         self.daily_live_limit,
            "capital_review_snapshot":  dict(self.capital_review_snapshot),
            "paper_gate_verdict":       dict(self.paper_gate_verdict),
            "canary_gate_verdict":      dict(self.canary_gate_verdict),
            "manual_approval_snapshot": dict(self.manual_approval_snapshot),
            "notes":                    self.notes,
            "previous_audit_id":        self.previous_audit_id,
            "recorded":                 True,
            "is_order_signal":          False,
            "is_live_authorization":    False,
            "broker_order_sent":        False,
            "order_created":            False,
            "auto_apply_allowed":       False,
            "contains_secret":          False,
        }


class LiveTransitionAuditLog:
    """append-only 감사 로그 저장소. update/delete 메서드 *없음* (의도적)."""

    def __init__(self) -> None:
        self._entries: list[LiveTransitionAuditEntry] = []
        self._lock = RLock()

    def record(
        self,
        *,
        operator: str,
        action: str,
        reason: str,
        risk_profile: Optional[str] = None,
        symbol_whitelist: Optional[list[str]] = None,
        max_order_notional: Optional[int] = None,
        daily_live_limit: Optional[int] = None,
        capital_review_snapshot: Optional[dict] = None,
        paper_gate_verdict: Optional[dict] = None,
        canary_gate_verdict: Optional[dict] = None,
        manual_approval_snapshot: Optional[dict] = None,
        notes: str = "",
    ) -> LiveTransitionAuditEntry:
        """감사 이벤트 *추가만* (append). 검증 + sanitize(fail-closed) 후 기록.

        operator/reason 필수, action 은 STANDARD_ACTIONS, secret 적중 시 거부.
        """
        op = (operator or "").strip()
        if not op:
            raise LiveAuditError(LIVE_AUDIT_OPERATOR_REQUIRED, "operator 는 필수입니다.")
        rsn = (reason or "").strip()
        if not rsn:
            raise LiveAuditError(LIVE_AUDIT_REASON_REQUIRED, "reason 은 필수입니다.")
        if action not in STANDARD_ACTIONS:
            raise LiveAuditError(LIVE_AUDIT_INVALID_ACTION,
                                f"action 이 표준 집합에 없습니다: {action}")

        # 보강 패턴 스캔 (KIS 계좌번호 8-2 등) — agent_memory sanitize 가 놓치는
        # 형식을 raw 입력 전체에서 fail-closed 로 차단.
        _blob = " ".join([
            op, rsn, notes or "", str(risk_profile or ""),
            " ".join(symbol_whitelist or []),
            _json.dumps(capital_review_snapshot or {}, ensure_ascii=False),
            _json.dumps(paper_gate_verdict or {}, ensure_ascii=False),
            _json.dumps(canary_gate_verdict or {}, ensure_ascii=False),
            _json.dumps(manual_approval_snapshot or {}, ensure_ascii=False),
        ])
        for pat in _EXTRA_SECRET_PATTERNS:
            if pat.search(_blob):
                raise LiveAuditError(
                    LIVE_AUDIT_SECRET_BLOCKED,
                    "민감정보(계좌/토큰 형식)가 감지되어 기록을 거부했습니다.",
                )

        # sanitize (fail-closed) — secret-like 값 적중 시 SecretLeakError.
        try:
            op = sanitize_text(op, field_name="operator")
            rsn = sanitize_text(rsn, field_name="reason")
            notes_s = sanitize_text(notes or "", field_name="notes")
            rp = sanitize_text(risk_profile, field_name="risk_profile") if risk_profile else None
            wl = tuple(
                sanitize_text(s, field_name="symbol") for s in (symbol_whitelist or [])
            )
            cap = sanitize_dict(capital_review_snapshot or {}, field_name="capital_review")
            pg = sanitize_dict(paper_gate_verdict or {}, field_name="paper_gate")
            cg = sanitize_dict(canary_gate_verdict or {}, field_name="canary_gate")
            ma = sanitize_dict(manual_approval_snapshot or {}, field_name="manual_approval")
        except SecretLeakError as e:
            raise LiveAuditError(LIVE_AUDIT_SECRET_BLOCKED,
                                f"민감정보가 감지되어 기록을 거부했습니다: {e}") from e

        with self._lock:
            prev_id = self._entries[-1].audit_id if self._entries else None
            entry = LiveTransitionAuditEntry(
                audit_id=str(uuid.uuid4()),
                created_at=_utcnow_iso(),
                operator=op,
                action=action,
                reason=rsn,
                reason_code=LIVE_AUDIT_RECORDED,
                risk_profile=rp,
                symbol_whitelist=wl,
                max_order_notional=(int(max_order_notional)
                                    if max_order_notional is not None else None),
                daily_live_limit=(int(daily_live_limit)
                                  if daily_live_limit is not None else None),
                capital_review_snapshot=cap,
                paper_gate_verdict=pg,
                canary_gate_verdict=cg,
                manual_approval_snapshot=ma,
                notes=notes_s,
                previous_audit_id=prev_id,
            )
            self._entries.append(entry)
            return entry

    def recent(self, *, limit: int = 100) -> list[LiveTransitionAuditEntry]:
        with self._lock:
            limit = max(1, min(int(limit or 100), 500))
            return list(reversed(self._entries[-limit:]))

    def get(self, audit_id: str) -> Optional[LiveTransitionAuditEntry]:
        with self._lock:
            for e in self._entries:
                if e.audit_id == audit_id:
                    return e
            return None

    def count(self) -> int:
        with self._lock:
            return len(self._entries)


_LOG: LiveTransitionAuditLog | None = None
_LOG_LOCK = RLock()


def get_live_transition_audit_log() -> LiveTransitionAuditLog:
    global _LOG
    with _LOG_LOCK:
        if _LOG is None:
            _LOG = LiveTransitionAuditLog()
        return _LOG


def reset_live_transition_audit_log_for_tests() -> None:
    global _LOG
    with _LOG_LOCK:
        _LOG = LiveTransitionAuditLog()


__all__ = [
    "STANDARD_ACTIONS",
    "LIVE_AUDIT_RECORDED",
    "LIVE_AUDIT_OPERATOR_REQUIRED",
    "LIVE_AUDIT_REASON_REQUIRED",
    "LIVE_AUDIT_INVALID_ACTION",
    "LIVE_AUDIT_SECRET_BLOCKED",
    "LiveAuditError",
    "LiveTransitionAuditEntry",
    "LiveTransitionAuditLog",
    "get_live_transition_audit_log",
    "reset_live_transition_audit_log_for_tests",
]
