"""2-12: AgentDecisionLog meta 표준 빌더 — tick 별 판단 근거 보존.

Agent Council 의 4전략 vote 상세 + 최종 판단 근거를 AgentDecisionLog.meta 의
*표준 구조* 로 만든다. decision_episode(canonical 상세) 와 핵심값이 일치하도록
동일 소스(KisPaperAutoDecision / council carry)에서 추출하며, secret-safe.

## 절대 invariant (CLAUDE.md)
- 본 모듈은 *메타 직렬화 전용* — broker / OrderExecutor / route_order / 외부 HTTP
  import 0건. 주문을 만들지 않는다.
- secret / API key / 계좌번호 carry 0건 — 반환 *전* `sanitize_dict`(fail-closed).
- 항상 `is_live_authorization=False` / `contains_secret=False` /
  `broker_order_type=KIS_PAPER` 강제. raw KIS 응답 전체 저장 0건(allowlist 필드만).
"""

from __future__ import annotations

from typing import Any

from app.agents.agent_memory import sanitize_dict


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def build_agent_decision_log_meta(
    *,
    decision: Any = None,
    reason_code: str | None = None,
    broker_order_no: str | None = None,
    submitted: bool = False,
    dry_run: bool = False,
    broker_order_sent: bool = False,
    order_created: bool = False,
    audit_id: int | None = None,
    episode_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """KisPaperAutoDecision(+ 결과 파라미터) → AgentDecisionLog.meta 표준 dict.

    `decision` 은 KisPaperAutoDecision (duck-typed) — selected_strategies /
    confidence / quality_score / risk_profile / risk_veto_result /
    exit_plan_validation / exit_plan / sell_reason_code / votes / risk_flags 를
    carry. secret-safe sanitize 적용 후 반환.
    """
    d = decision
    final_action = (getattr(d, "side", None) or "HOLD") if d is not None else "HOLD"
    votes = list(getattr(d, "votes", []) or []) if d is not None else []
    meta: dict[str, Any] = {
        # 식별/연결.
        "episode_id":        episode_id,
        "final_action":      final_action,
        "reason_code":       reason_code,
        # 4전략 vote 상세 + 판단 근거.
        "votes":             votes,
        "selected_strategies": list(getattr(d, "selected_strategies", []) or []) if d else [],
        "risk_profile":      getattr(d, "risk_profile", None) if d else None,
        "confidence":        _f(getattr(d, "confidence", None)) if d else None,
        "quality_score":     int(getattr(d, "quality_score", 0) or 0) if d else 0,
        "risk_flags":        list(getattr(d, "risk_flags", []) or []) if d else [],
        "risk_veto_result":  dict(getattr(d, "risk_veto_result", {}) or {}) if d else {},
        "exit_plan_validation": dict(getattr(d, "exit_plan_validation", {}) or {}) if d else {},
        "sell_reason_code":  getattr(d, "sell_reason_code", None) if d else None,
        "sell_reason_category": getattr(d, "sell_reason_category", None) if d else None,
        # 주문 결과 / 안전 invariant.
        "order_created":     bool(order_created),
        "submitted":         bool(submitted),
        "dry_run":           bool(dry_run),
        "broker_order_no":   broker_order_no,
        "audit_id":          audit_id,
        "broker_order_type": "KIS_PAPER",
        "broker_order_sent": bool(broker_order_sent),
        "is_live_authorization": False,
        "contains_secret":   False,
    }
    if extra:
        for k, v in extra.items():
            if k not in meta:
                meta[k] = v
    # secret-safe (fail-closed) — raw KIS 응답/계좌/secret 적중 시 raise.
    return sanitize_dict(meta, field_name="agent_decision_log_meta")


__all__ = ["build_agent_decision_log_meta"]
