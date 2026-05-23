"""2-08: RiskOfficerAgent — risk_flags 합산 + risk_profile 허용치 초과 시 HOLD 강등.

Agent Council 의 4전략 vote + 최종 잠정 판단에서 수집한 risk_flags 를 RiskOfficer
가 합산(dedupe)하고, risk_profile 별 허용 개수를 초과하면 BUY/SELL 을 HOLD 로
강등(veto)한다. **AGGRESSIVE 도 무제한 진입 불가** — 허용치를 넘으면 강등된다.

## 절대 invariant (CLAUDE.md)
- 본 모듈은 *판단 보조(advisory)* — broker / OrderExecutor / route_order / 외부
  HTTP import 0건, 주문을 만들거나 전송하지 않는다.
- 본 모듈은 RiskManager / PermissionGate 를 *대체하거나 우회하지 않는다* —
  Agent Council 단계의 보수적 사전 필터일 뿐, 실제 주문은 여전히 sanctioned
  경로(RiskManager → PermissionGate → OrderExecutor)를 모두 통과해야 한다.
- `RiskVetoResult.is_order_signal=False` / `is_live_authorization=False` 영구.
- 결정적(deterministic).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# risk_profile 별 허용 risk_flag 개수 (초과 시 HOLD).
# CONSERVATIVE: 0 (1개 이상이면 HOLD)
# BALANCED:     1 (2개 이상이면 HOLD)
# AGGRESSIVE:   2 (3개 이상이면 HOLD)  ← AGGRESSIVE 도 무제한 아님.
MAX_RISK_FLAGS_BY_PROFILE: dict[str, int] = {
    "CONSERVATIVE": 0,
    "BALANCED":     1,
    "AGGRESSIVE":   2,
}
_DEFAULT_MAX_RISK_FLAGS = 1   # 알 수 없는 profile 은 BALANCED 수준으로 보수 적용.

REASON_RISK_OFFICER_VETO = "RISK_OFFICER_VETO"
REASON_RISK_FLAGS_EXCEEDED = "RISK_FLAGS_EXCEEDED"   # alias (동일 의미)


def aggregate_risk_flags(*flag_sources: Any) -> list[str]:
    """여러 risk_flags 소스(list/tuple)를 합산 + dedupe + 정렬.

    각 source 는 문자열 list 또는 None. 빈 문자열/None 은 무시.
    """
    seen: set[str] = set()
    for src in flag_sources:
        if not src:
            continue
        if isinstance(src, (list, tuple, set)):
            for f in src:
                if f:
                    seen.add(str(f))
        elif isinstance(src, str):
            seen.add(src)
    return sorted(seen)


def max_risk_flags_for(risk_profile: str) -> int:
    return MAX_RISK_FLAGS_BY_PROFILE.get(
        str(risk_profile or "").upper(), _DEFAULT_MAX_RISK_FLAGS)


@dataclass(frozen=True)
class RiskVetoResult:
    """RiskOfficer 판단 결과 — 주문 신호 아님."""

    veto_applied:     bool
    pre_veto_action:  str
    final_action:     str
    risk_flags:       list[str]
    risk_flag_count:  int
    max_risk_flags:   int
    risk_profile:     str
    reason_code:      str | None = None
    reason:           str = ""

    is_order_signal:       bool = False
    is_live_authorization: bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("RiskVetoResult.is_order_signal must be False")
        if self.is_live_authorization is not False:
            raise ValueError("RiskVetoResult.is_live_authorization must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "veto_applied":     bool(self.veto_applied),
            "pre_veto_action":  self.pre_veto_action,
            "final_action":     self.final_action,
            "risk_flags":       list(self.risk_flags),
            "risk_flag_count":  int(self.risk_flag_count),
            "max_risk_flags":   int(self.max_risk_flags),
            "risk_profile":     self.risk_profile,
            "reason_code":      self.reason_code,
            "reason":           self.reason,
            "is_order_signal":       False,
            "is_live_authorization": False,
        }


def evaluate_risk_officer_veto(
    *,
    action: str,
    risk_flags: list[str] | None,
    risk_profile: str,
    max_risk_flags: int | None = None,
    extra_risk_flags: list[str] | None = None,
) -> RiskVetoResult:
    """risk_flags 합산 → 허용치 초과 + action 이 BUY/SELL 이면 HOLD 강등.

    `max_risk_flags` 미지정 시 risk_profile 기본값 사용. HOLD 는 그대로 HOLD 유지.
    """
    profile = str(risk_profile or "").upper()
    flags = aggregate_risk_flags(risk_flags, extra_risk_flags)
    maxf = int(max_risk_flags) if max_risk_flags is not None else max_risk_flags_for(profile)
    act = str(action or "").upper()

    over_limit = len(flags) > maxf
    veto = over_limit and act in ("BUY", "SELL")
    final = "HOLD" if veto else act

    if veto:
        reason = (f"RiskOfficer veto: risk_flags {len(flags)}개 > 허용 {maxf}"
                  f"({profile}) — {act} → HOLD 강등")
        reason_code = REASON_RISK_OFFICER_VETO
    else:
        reason = ""
        reason_code = None

    return RiskVetoResult(
        veto_applied=veto, pre_veto_action=act, final_action=final,
        risk_flags=flags, risk_flag_count=len(flags), max_risk_flags=maxf,
        risk_profile=profile, reason_code=reason_code, reason=reason,
    )


__all__ = [
    "RiskVetoResult", "evaluate_risk_officer_veto", "aggregate_risk_flags",
    "max_risk_flags_for", "MAX_RISK_FLAGS_BY_PROFILE",
    "REASON_RISK_OFFICER_VETO", "REASON_RISK_FLAGS_EXCEEDED",
]
