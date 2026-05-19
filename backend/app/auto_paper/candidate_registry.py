"""#PaperCandidateWire: 최종 Paper 후보 ↔ Auto Paper Loop 연결.

3-15 의 `FinalCandidateReport.candidates` (PaperCandidate list) 를 운영자
승인을 거친 후에만 *Auto Paper Loop 의 active_candidate* 로 등록한다.

**자동 활성화 0건** — 후보가 있어도 `approve(candidate_id, approved_by)` 가
호출되기 전까지는 어떤 후보도 Paper Auto Loop 에 사용되지 않는다. 후보가
`HIGH_RISK` / `BLOCK` 등 위험 라벨을 carry 하면 승인 자체 차단.

## 흐름

```
FinalCandidateReport (3-15)
    → load_candidates(report) — 모두 PENDING_APPROVAL 으로 등록
    → operator approve(candidate_id, "approved_by_xxx")
        → ApprovalStatus.APPROVED
        → active_candidate() 가 *가장 최근 APPROVED* 후보 반환
    → AutoPaperLoop.tick() — consumer runner 가 active_candidate 를
      Agent input 으로 변환 → bridge → ledger + AgentDecisionLog
```

## 안전 invariant

1. broker / OrderExecutor / route_order import 0건.
2. `ManagedCandidate.is_order_signal/auto_apply_allowed/is_live_authorization
   =False` 영구. `requires_operator_approval=True` 영구 (PaperCandidate 상속).
3. `approve()` 는 HIGH_RISK / BLOCK 라벨 carry 후보 시 `ApprovalBlockedError`.
4. registry 는 *in-memory* — 운영자 PC 재기동 시 다시 승인 필요 (감사 보존).
5. DB write 0건, secret 0건, settings mutation 0건.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from app.analytics.final_paper_candidates import (
    FinalCandidateReport,
    PaperCandidate,
)


CANDIDATE_REGISTRY_SCHEMA_VERSION = "1.0"


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────


class ApprovalStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED         = "APPROVED"
    REJECTED         = "REJECTED"


class ReadinessState(StrEnum):
    """Auto Paper Loop 가 candidate 측면에서 보는 readiness 라벨.

    *AutoPaperState 와 별개* — 6 state 모델은 그대로 유지하고, 본 라벨은
    `AutoPaperStatus.candidate_readiness` metadata 로 carry.
    """
    NO_CANDIDATE      = "NO_CANDIDATE"
    WAITING_APPROVAL  = "WAITING_APPROVAL"
    CANDIDATE_READY   = "CANDIDATE_READY"


class ApprovalBlockedError(RuntimeError):
    """위험 라벨 carry 후보를 승인 시도 — 차단."""


class CandidateNotFoundError(KeyError):
    """존재하지 않는 candidate_id."""


# ─────────────────────────────────────────────────────────────────────────────
# Data
# ─────────────────────────────────────────────────────────────────────────────


# 위험 라벨 — 승인 자체 차단.
_BLOCK_RISK_LABELS = frozenset({"HIGH_RISK", "BLOCK", "BLOCKED_REGIME",
                                "FAIL", "OVERFIT_RISK", "STRESS_FAILED"})


@dataclass(frozen=True)
class ManagedCandidate:
    """`PaperCandidate` + approval 상태 + audit log."""
    candidate_id:           str               # name + symbol 기반 (또는 명시)
    candidate:              PaperCandidate
    status:                 ApprovalStatus    = ApprovalStatus.PENDING_APPROVAL
    approved_by:            str | None         = None
    approved_at:            str | None         = None
    rejected_by:            str | None         = None
    rejected_at:            str | None         = None
    decision_notes:         list[str]          = field(default_factory=list)
    loaded_at:              str                = ""

    # 절대 invariant.
    is_order_signal:        bool = False
    auto_apply_allowed:     bool = False
    is_live_authorization:  bool = False

    def __post_init__(self) -> None:
        for name, val in (
            ("is_order_signal",       self.is_order_signal),
            ("auto_apply_allowed",    self.auto_apply_allowed),
            ("is_live_authorization", self.is_live_authorization),
        ):
            if val is not False:
                raise ValueError(f"ManagedCandidate.{name} must be False.")
        if not isinstance(self.status, ApprovalStatus):
            raise ValueError("status must be ApprovalStatus.")
        # PaperCandidate invariant 상속 — requires_operator_approval=True.
        if self.candidate.requires_operator_approval is not True:
            raise ValueError(
                "ManagedCandidate.candidate.requires_operator_approval must be True"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id":           self.candidate_id,
            "candidate":              self.candidate.to_dict(),
            "status":                 self.status.value,
            "approved_by":            self.approved_by,
            "approved_at":            self.approved_at,
            "rejected_by":            self.rejected_by,
            "rejected_at":            self.rejected_at,
            "decision_notes":         list(self.decision_notes),
            "loaded_at":              self.loaded_at,
            "is_order_signal":        False,
            "auto_apply_allowed":     False,
            "is_live_authorization":  False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _candidate_id_for(c: PaperCandidate) -> str:
    """deterministic id — name + symbol + rank."""
    return f"{c.name}::{c.symbol}::rank{c.rank}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _has_block_risk(candidate: PaperCandidate) -> tuple[bool, list[str]]:
    """위험 라벨 carry 여부 + 사유."""
    flags = list(candidate.risk_flags or [])
    reasons: list[str] = []
    for f in flags:
        if f.upper() in _BLOCK_RISK_LABELS:
            reasons.append(f"risk_flag={f}")
    for verdict_name, value in (
        ("paper_candidate_status", candidate.paper_candidate_status),
        ("walk_forward_verdict",   candidate.walk_forward_verdict),
        ("stress_verdict",         candidate.stress_verdict),
        ("combo_verdict",          candidate.combo_verdict),
        ("regime_combo_verdict",   candidate.regime_combo_verdict),
        ("combo_risk_verdict",     candidate.combo_risk_verdict),
    ):
        if value in _BLOCK_RISK_LABELS:
            reasons.append(f"{verdict_name}={value}")
    return bool(reasons), reasons


# ─────────────────────────────────────────────────────────────────────────────
# Registry — in-memory, thread-safe
# ─────────────────────────────────────────────────────────────────────────────


class CandidateRegistry:
    """*in-memory* candidate store.

    `load_candidates()` 가 호출될 때마다 기존 PENDING 후보는 *교체* 되지만,
    APPROVED 후보는 *명시 reset* 전까지 보존. 운영자가 단계적으로 후보를
    검토할 수 있게 함.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # candidate_id → ManagedCandidate (insertion order = rank order)
        self._candidates: dict[str, ManagedCandidate] = {}

    # ── Public API ──

    def reset(self) -> None:
        """모든 후보 제거 — 테스트 / 운영자 명시 reset."""
        with self._lock:
            self._candidates.clear()

    def load_candidates(self, report: FinalCandidateReport) -> list[ManagedCandidate]:
        """`FinalCandidateReport` → registry 에 PENDING 등록.

        기존 *PENDING* 후보는 같은 candidate_id 일 때 교체 (재로드).
        기존 *APPROVED / REJECTED* 후보는 보존 — 운영자가 명시 reset 하지 않는 한
        승인 이력을 지키기 위함.
        """
        loaded: list[ManagedCandidate] = []
        with self._lock:
            for c in report.candidates:
                cid = _candidate_id_for(c)
                existing = self._candidates.get(cid)
                # APPROVED / REJECTED 보존, PENDING 만 교체.
                if existing and existing.status != ApprovalStatus.PENDING_APPROVAL:
                    loaded.append(existing)
                    continue
                managed = ManagedCandidate(
                    candidate_id=cid,
                    candidate=c,
                    status=ApprovalStatus.PENDING_APPROVAL,
                    loaded_at=_now_iso(),
                )
                self._candidates[cid] = managed
                loaded.append(managed)
        return loaded

    def list_candidates(self) -> list[ManagedCandidate]:
        with self._lock:
            return list(self._candidates.values())

    def get(self, candidate_id: str) -> ManagedCandidate:
        with self._lock:
            m = self._candidates.get(candidate_id)
            if m is None:
                raise CandidateNotFoundError(candidate_id)
            return m

    def approve(
        self,
        candidate_id: str,
        approved_by:  str,
        note:         str | None = None,
    ) -> ManagedCandidate:
        """후보 승인 — 위험 라벨 검사 후 APPROVED 로 전이.

        Raises:
            CandidateNotFoundError: id 없음.
            ApprovalBlockedError: 위험 라벨 carry — 승인 차단.
            RuntimeError: 이미 REJECTED 인 후보.
        """
        if not approved_by:
            raise ValueError("approved_by must be non-empty")
        with self._lock:
            m = self._candidates.get(candidate_id)
            if m is None:
                raise CandidateNotFoundError(candidate_id)
            if m.status == ApprovalStatus.REJECTED:
                raise RuntimeError(
                    f"candidate {candidate_id!r} is REJECTED — cannot approve"
                )
            blocked, reasons = _has_block_risk(m.candidate)
            if blocked:
                raise ApprovalBlockedError(
                    f"candidate {candidate_id!r} has block-risk labels: "
                    + "; ".join(reasons)
                )
            notes = list(m.decision_notes)
            if note:
                notes.append(f"approve: {note}")
            new = replace(
                m,
                status=ApprovalStatus.APPROVED,
                approved_by=approved_by,
                approved_at=_now_iso(),
                decision_notes=notes,
            )
            self._candidates[candidate_id] = new
            return new

    def reject(
        self,
        candidate_id: str,
        rejected_by:  str,
        note:         str | None = None,
    ) -> ManagedCandidate:
        if not rejected_by:
            raise ValueError("rejected_by must be non-empty")
        with self._lock:
            m = self._candidates.get(candidate_id)
            if m is None:
                raise CandidateNotFoundError(candidate_id)
            if m.status == ApprovalStatus.APPROVED:
                raise RuntimeError(
                    f"candidate {candidate_id!r} is APPROVED — must revoke first "
                    f"(not supported in this PR)"
                )
            notes = list(m.decision_notes)
            if note:
                notes.append(f"reject: {note}")
            new = replace(
                m,
                status=ApprovalStatus.REJECTED,
                rejected_by=rejected_by,
                rejected_at=_now_iso(),
                decision_notes=notes,
            )
            self._candidates[candidate_id] = new
            return new

    def active_candidate(self) -> ManagedCandidate | None:
        """현재 *가장 최근* APPROVED 후보 — Auto Paper Loop 가 소비.

        rank 1 (composite_score 최상) APPROVED 가 있으면 그를 우선. 동률 시 가장
        최근 approved_at 우선.
        """
        with self._lock:
            approved = [
                m for m in self._candidates.values()
                if m.status == ApprovalStatus.APPROVED
            ]
        if not approved:
            return None
        return sorted(
            approved,
            key=lambda m: (m.candidate.rank, -(_iso_to_epoch(m.approved_at) or 0)),
        )[0]

    def readiness_state(self) -> ReadinessState:
        with self._lock:
            n = len(self._candidates)
            n_approved = sum(
                1 for m in self._candidates.values()
                if m.status == ApprovalStatus.APPROVED
            )
            n_pending = sum(
                1 for m in self._candidates.values()
                if m.status == ApprovalStatus.PENDING_APPROVAL
            )
        if n == 0:
            return ReadinessState.NO_CANDIDATE
        if n_approved >= 1:
            return ReadinessState.CANDIDATE_READY
        if n_pending >= 1:
            return ReadinessState.WAITING_APPROVAL
        # all REJECTED.
        return ReadinessState.NO_CANDIDATE

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version":  CANDIDATE_REGISTRY_SCHEMA_VERSION,
                "readiness_state": self.readiness_state().value,
                "total":           len(self._candidates),
                "pending":         sum(
                    1 for m in self._candidates.values()
                    if m.status == ApprovalStatus.PENDING_APPROVAL
                ),
                "approved":        sum(
                    1 for m in self._candidates.values()
                    if m.status == ApprovalStatus.APPROVED
                ),
                "rejected":        sum(
                    1 for m in self._candidates.values()
                    if m.status == ApprovalStatus.REJECTED
                ),
                "candidates":      [m.to_dict() for m in self._candidates.values()],
                "active_candidate_id": (
                    self.active_candidate().candidate_id
                    if self.active_candidate() is not None else None
                ),
                "is_order_signal":       False,
                "auto_apply_allowed":    False,
                "is_live_authorization": False,
            }


def _iso_to_epoch(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return int(datetime.fromisoformat(iso).timestamp())
    except (ValueError, TypeError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────────────────────


_registry_singleton: CandidateRegistry | None = None
_singleton_lock = threading.Lock()


def get_candidate_registry() -> CandidateRegistry:
    global _registry_singleton
    with _singleton_lock:
        if _registry_singleton is None:
            _registry_singleton = CandidateRegistry()
        return _registry_singleton


def reset_candidate_registry_for_tests() -> None:
    """테스트 전용 — 매 test 사이 격리."""
    global _registry_singleton
    with _singleton_lock:
        _registry_singleton = CandidateRegistry()


__all__ = [
    "CANDIDATE_REGISTRY_SCHEMA_VERSION",
    "ApprovalStatus",
    "ReadinessState",
    "ApprovalBlockedError",
    "CandidateNotFoundError",
    "ManagedCandidate",
    "CandidateRegistry",
    "get_candidate_registry",
    "reset_candidate_registry_for_tests",
]
