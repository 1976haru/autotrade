"""#52 / 6-07: AI 판단 설명 가능성 (read-only, 표시 전용).

사용자가 *AI 가 왜 매수/매도/보류했는지* 이해할 수 있도록, 기존
`AgentCouncilDecision.to_dict()`(또는 episode.council) 에 *이미 있는* 필드에서
사람이 읽기 쉬운 설명을 *유도* 한다. 새로운 판단 로직을 만들지 않으며, 누락된
필드는 친절한 fallback 문구로 대체한다(구버전 episode 호환).

산출 항목: entry_reason / counter_reason / exit_plan 설명 / risk_flags 설명 /
risk_veto 설명 / exit_plan_validation 설명 / sell_reason 설명 / 최종 판단 이유 /
selected_strategies / quality_score / confidence / market_regime / time_phase.

**본 모듈은 설명/표시 전용이다 — 주문 신호가 아니며 실거래 권한이 아니다.**

CLAUDE.md 가드 (정적 grep 으로 lock):
- broker / OrderExecutor / 단일 주문 라우터 / paper_trader / KIS 어댑터 import 0건.
- broker 주문/취소 호출 0건, DB write 0건, 외부 HTTP / AI SDK import 0건.
- `DecisionExplanation.is_order_signal=False` / `is_live_authorization=False` /
  `contains_secret=False` 불변.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# fallback 문구 (필드 누락 시 — 에러 대신 친절한 안내).
ENTRY_REASON_FALLBACK = "진입 근거 미기록"
COUNTER_REASON_FALLBACK = "반대 근거 미기록"
EXIT_PLAN_FALLBACK = "청산 계획 미기록"
RISK_FLAGS_FALLBACK = "위험 플래그 없음"
RISK_VETO_FALLBACK = "RiskOfficer veto 없음"
SELL_REASON_FALLBACK = "매도 사유 미기록"
FINAL_REASON_FALLBACK = "최종 판단 이유 미기록"

DISCLAIMER_KO = (
    "이 설명은 분석/설명 전용이며 주문 버튼이 아닙니다. 실전 전환 승인과 무관하며, "
    "수익을 보장하지 않습니다."
)

_ACTION_KO = {"BUY": "매수", "SELL": "매도", "HOLD": "보류"}


def _num(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _strategies_of(votes: list[dict], *, signal: str, exclude_zero: bool = True) -> list[dict]:
    out: list[dict] = []
    for v in votes or []:
        if not isinstance(v, dict):
            continue
        if str(v.get("signal")) != signal:
            continue
        if exclude_zero and _num(v.get("score")) in (None, 0.0):
            continue
        out.append(v)
    return out


@dataclass(frozen=True)
class DecisionExplanation:
    """AI 판단 설명 — UI 안전 payload. secret 0건."""

    final_action:        str
    final_action_ko:     str
    final_reason:        str
    entry_reason:        str
    counter_reason:      str
    exit_plan_text:      str
    risk_flags_text:     str
    risk_veto_text:      str
    exit_plan_validation_text: str
    sell_reason_text:    str
    selected_strategies: list[str]
    quality_score:       int | None
    confidence:          float | None
    market_regime:       str
    time_phase:          str
    explanation_summary: str

    is_order_signal:       bool = False
    is_live_authorization: bool = False
    contains_secret:       bool = False

    def __post_init__(self) -> None:
        for name in ("is_order_signal", "is_live_authorization", "contains_secret"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be False (설명은 표시 전용)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_action":         self.final_action,
            "final_action_ko":      self.final_action_ko,
            "final_reason":         self.final_reason,
            "entry_reason":         self.entry_reason,
            "counter_reason":       self.counter_reason,
            "exit_plan_text":       self.exit_plan_text,
            "risk_flags_text":      self.risk_flags_text,
            "risk_veto_text":       self.risk_veto_text,
            "exit_plan_validation_text": self.exit_plan_validation_text,
            "sell_reason_text":     self.sell_reason_text,
            "selected_strategies":  list(self.selected_strategies),
            "quality_score":        self.quality_score,
            "confidence":           self.confidence,
            "market_regime":        self.market_regime,
            "time_phase":           self.time_phase,
            "explanation_summary":  self.explanation_summary,
            "is_order_signal":       False,
            "is_live_authorization": False,
            "contains_secret":       False,
            "disclaimer":            DISCLAIMER_KO,
        }


def _entry_reason(final_action: str, votes: list[dict], reason: str) -> str:
    if final_action == "HOLD":
        return "보류 — 강한 진입 신호 없음" if not reason else f"보류 근거: {reason[:120]}"
    supporting = _strategies_of(votes, signal=final_action)
    if not supporting:
        return ENTRY_REASON_FALLBACK
    names = ", ".join(str(v.get("strategy")) for v in supporting)
    act_ko = _ACTION_KO.get(final_action, final_action)
    top = max(supporting, key=lambda v: _num(v.get("score")) or 0.0)
    top_reason = str(top.get("reason") or "").strip()
    base = f"진입 근거: {names} 가 같은 방향({act_ko}) 신호"
    return f"{base} — {top_reason}" if top_reason else base


def _counter_reason(final_action: str, votes: list[dict]) -> str:
    # 최종 방향과 *반대* 신호를 낸 전략 (HOLD 는 반대 카운트에서 제외).
    opposing: list[dict] = []
    for v in votes or []:
        if not isinstance(v, dict):
            continue
        sig = str(v.get("signal"))
        if sig in ("HOLD", str(final_action)):
            continue
        if _num(v.get("score")) in (None, 0.0):
            continue
        opposing.append(v)
    if not opposing:
        return COUNTER_REASON_FALLBACK
    parts = []
    for v in opposing:
        r = str(v.get("reason") or "").strip()
        parts.append(f"{v.get('strategy')}({v.get('signal')})" + (f": {r}" if r else ""))
    return "반대 근거: " + " / ".join(parts)


def _exit_plan_text(exit_plan: dict, has_exit_plan: bool) -> str:
    if not isinstance(exit_plan, dict) or not exit_plan or not has_exit_plan:
        return EXIT_PLAN_FALLBACK
    sl = exit_plan.get("stop_loss_pct", exit_plan.get("stop_loss"))
    tp = exit_plan.get("take_profit_pct", exit_plan.get("take_profit"))
    rr = exit_plan.get("risk_reward_ratio", exit_plan.get("risk_reward"))
    bits = []
    if sl is not None:
        bits.append(f"손절 {sl}%")
    if tp is not None:
        bits.append(f"익절 {tp}%")
    if rr is not None:
        bits.append(f"RR {rr}")
    if exit_plan.get("trailing_stop"):
        bits.append("트레일링 스탑")
    return "청산 계획: " + ", ".join(bits) if bits else EXIT_PLAN_FALLBACK


def _risk_flags_text(risk_flags: list) -> str:
    flags = [str(f) for f in (risk_flags or []) if f]
    if not flags:
        return RISK_FLAGS_FALLBACK
    return "리스크 플래그: " + ", ".join(flags)


def _risk_veto_text(risk_veto: dict) -> str:
    if not isinstance(risk_veto, dict) or not risk_veto.get("veto_applied"):
        return RISK_VETO_FALLBACK
    reason = str(risk_veto.get("reason") or risk_veto.get("reason_code") or "위험 플래그 초과")
    return f"RiskOfficer veto: {reason} → HOLD 강등"


def _exit_plan_validation_text(validation: dict, final_action: str, pre_action: str | None) -> str:
    if not isinstance(validation, dict) or not validation:
        return ""
    valid = validation.get("valid")
    if valid is False or (pre_action == "BUY" and final_action != "BUY"):
        rc = validation.get("reason_code") or "exit_plan invalid"
        return f"BUY 차단: {rc} (exit_plan 검증 실패)"
    return ""


def _sell_reason_text(sell_reason: dict, final_action: str) -> str:
    if final_action != "SELL":
        return ""
    if not isinstance(sell_reason, dict) or not sell_reason.get("reason_code"):
        return SELL_REASON_FALLBACK
    rc = sell_reason.get("reason_code")
    msg = str(sell_reason.get("message") or "").strip()
    return f"SELL 사유: {rc}" + (f" — {msg}" if msg else "")


def build_decision_explanation(
    council: dict[str, Any] | None,
    *,
    market_regime: str | None = None,
    time_phase: str | None = None,
) -> DecisionExplanation:
    """council dict(없으면 빈 dict) → 사람이 읽는 설명. 필드 누락 시 fallback."""
    c = council if isinstance(council, dict) else {}
    final_action = str(c.get("final_action") or "HOLD")
    votes = c.get("votes") if isinstance(c.get("votes"), list) else []
    reason = str(c.get("reason") or "")

    entry = _entry_reason(final_action, votes, reason)
    counter = _counter_reason(final_action, votes)
    exit_text = _exit_plan_text(c.get("exit_plan") or {}, bool(c.get("has_exit_plan")))
    flags_text = _risk_flags_text(c.get("risk_flags") or [])
    veto_text = _risk_veto_text(c.get("risk_veto_result") or {})
    epv_text = _exit_plan_validation_text(
        c.get("exit_plan_validation") or {}, final_action, c.get("pre_exit_plan_action"))
    sell_text = _sell_reason_text(c.get("sell_reason") or {}, final_action)

    regime = str(market_regime or c.get("market_regime") or "UNKNOWN")
    phase = str(time_phase or c.get("time_phase") or "UNKNOWN")
    act_ko = _ACTION_KO.get(final_action, final_action)
    summary = (f"최종 판단: {act_ko}({final_action}) — "
               f"{(reason or FINAL_REASON_FALLBACK)[:100]}")

    return DecisionExplanation(
        final_action=final_action,
        final_action_ko=act_ko,
        final_reason=reason or FINAL_REASON_FALLBACK,
        entry_reason=entry,
        counter_reason=counter,
        exit_plan_text=exit_text,
        risk_flags_text=flags_text,
        risk_veto_text=veto_text,
        exit_plan_validation_text=epv_text,
        sell_reason_text=sell_text,
        selected_strategies=[str(s) for s in (c.get("selected_strategies") or [])],
        quality_score=(int(c["quality_score"]) if c.get("quality_score") is not None else None),
        confidence=_num(c.get("confidence")),
        market_regime=regime,
        time_phase=phase,
        explanation_summary=summary,
    )


__all__ = [
    "DecisionExplanation",
    "build_decision_explanation",
    "ENTRY_REASON_FALLBACK", "COUNTER_REASON_FALLBACK", "EXIT_PLAN_FALLBACK",
    "RISK_FLAGS_FALLBACK", "RISK_VETO_FALLBACK", "SELL_REASON_FALLBACK",
]
