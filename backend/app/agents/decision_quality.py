"""#51 / 6-06: Agent decision quality_score 고도화 (advisory, HOLD 게이트).

무조건 매수를 방지하고 판단 품질을 개선하기 위해, 신호 일관성 / 데이터 신뢰도 /
리스크 / 장세 적합도 / exit_plan 품질 / 과거 review feedback 을 반영한 *고도화된*
quality breakdown 을 산출한다. quality 가 낮으면 BUY 를 HOLD 로 강등(권고)한다.

**본 모듈은 판단 *품질* 평가다 — 실제 주문을 생성하지 않으며, quality_score 만으로
실전 전환을 허가하지 않는다. 기존 RiskOfficer / exit_plan HOLD 정책을 우회하지
않는다 (그 뒤에 별도 `pre_quality_action` 으로 동작).**

CLAUDE.md 가드 (정적 grep 으로 lock):
- broker / OrderExecutor / 단일 주문 라우터 / paper_trader / KIS 어댑터 import 0건.
- broker 주문/취소 호출 0건, DB write 0건, 외부 HTTP / AI SDK import 0건.
- `DecisionQuality.is_order_signal=False` / `is_live_authorization=False` /
  `contains_secret=False` 불변.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# enhanced quality 가중치 (합 1.0).
_WEIGHTS = {
    "signal_consistency": 0.30,
    "data_reliability":   0.20,
    "risk":               0.20,
    "regime_fit":         0.15,
    "exit_plan":          0.15,
}

# quality gate HOLD 사유.
QUALITY_SCORE_LOW_HOLD = "QUALITY_SCORE_LOW_HOLD"


def _clamp(v: float, lo: int = 0, hi: int = 100) -> int:
    return int(max(lo, min(hi, round(v))))


def _grade(score: int) -> str:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    if score >= 45:
        return "D"
    return "F"


def signal_consistency_score(votes: list[dict], final_action: str) -> int:
    """4전략 vote 일치도 — 최종 방향과 같은 신호를 낸 비율 (0~100)."""
    vs = [v for v in (votes or []) if isinstance(v, dict)]
    if not vs:
        return 50   # 데이터 없음 — 중립.
    agreeing = sum(1 for v in vs
                   if str(v.get("signal")) == str(final_action)
                   and (v.get("score") or 0) > 0)
    base = (agreeing / max(1, len(vs))) * 100
    # 강한 합의(점수 높은 동의)면 가산.
    strong = sum(1 for v in vs
                 if str(v.get("signal")) == str(final_action)
                 and (v.get("score") or 0) >= 60)
    return _clamp(base + strong * 3)


def data_reliability_score(
    *, data_status: str | None = None, price_stale: bool | None = None,
    volume_ok: bool | None = None, regime_known: bool | None = None,
) -> int:
    """데이터 신뢰도 — 시세 존재 / stale / volume / regime 확정. 미상 시 중립.

    *데이터 부재(None) 는 과도하게 감점하지 않는다* (중립 70 기준) — 누락만으로
    BUY 를 강제 HOLD 하지 않기 위함.
    """
    if data_status is None and price_stale is None and volume_ok is None and regime_known is None:
        return 70   # 정보 없음 — 중립 (테스트/구버전 호환).
    score = 100
    if data_status is not None and str(data_status).upper() not in ("OK", "FRESH"):
        score -= 40
    if price_stale:
        score -= 40
    if volume_ok is False:
        score -= 20
    if regime_known is False:
        score -= 15
    return _clamp(score)


def risk_score(risk_flags: list, veto_applied: bool = False) -> int:
    """리스크 — risk_flags 수 + RiskOfficer veto (많을수록 낮음)."""
    n = len([f for f in (risk_flags or []) if f])
    score = 100 - min(n * 15, 60) - (30 if veto_applied else 0)
    return _clamp(score)


def regime_fit_score(market_regime: str | None, regime_decision: str | None,
                     final_action: str) -> int:
    """장세 적합도 — regime_decision / market_regime 와 최종 방향의 정합."""
    rd = str(regime_decision or "ALLOW").upper()
    mr = str(market_regime or "UNKNOWN").upper()
    is_buy = str(final_action) == "BUY"
    if rd == "BLOCK_NEW_BUY":
        return 30 if is_buy else 70
    if rd == "WATCH_ONLY":
        return 50
    if rd == "REDUCE_SIZE":
        return 70
    if mr == "TREND_DOWN" and is_buy:
        return 40
    if mr == "UNKNOWN":
        return 65
    return 90


def exit_plan_score(final_action: str, has_exit_plan: bool,
                    exit_plan_valid: bool | None) -> int:
    """exit_plan 품질 — BUY 는 유효 exit_plan 필수, 그 외는 중립."""
    if str(final_action) != "BUY":
        return 80
    if has_exit_plan and exit_plan_valid is not False:
        return 90
    return 20


@dataclass(frozen=True)
class DecisionQuality:
    """고도화된 판단 품질 결과 — advisory. 주문 신호 아님."""

    enhanced_quality_score: int
    quality_grade:          str
    breakdown:              dict[str, int]
    penalties:              dict[str, int]
    should_hold:            bool            # quality 낮음 → BUY HOLD 권고.
    reason_code:            str | None
    min_quality:            int
    final_action_input:     str
    notes:                  list[str] = field(default_factory=list)

    is_order_signal:       bool = False
    is_live_authorization: bool = False
    contains_secret:       bool = False

    def __post_init__(self) -> None:
        if not (0 <= self.enhanced_quality_score <= 100):
            raise ValueError("enhanced_quality_score must be 0~100")
        for name in ("is_order_signal", "is_live_authorization", "contains_secret"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be False (quality 는 advisory)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "enhanced_quality_score": int(self.enhanced_quality_score),
            "quality_grade":          self.quality_grade,
            "breakdown":              dict(self.breakdown),
            "penalties":              dict(self.penalties),
            "should_hold":            bool(self.should_hold),
            "reason_code":            self.reason_code,
            "min_quality":            int(self.min_quality),
            "final_action_input":     self.final_action_input,
            "notes":                  list(self.notes),
            "is_order_signal":        False,
            "is_live_authorization":  False,
            "contains_secret":        False,
        }


def compute_decision_quality(
    *,
    votes: list[dict],
    final_action: str,
    risk_flags: list | None = None,
    veto_applied: bool = False,
    market_regime: str | None = None,
    regime_decision: str | None = None,
    has_exit_plan: bool = False,
    exit_plan_valid: bool | None = None,
    min_quality: int = 60,
    # data reliability signals (없으면 중립).
    data_status: str | None = None,
    price_stale: bool | None = None,
    volume_ok: bool | None = None,
    regime_known: bool | None = None,
    # 과거 review feedback penalty (0~100, 차감).
    feedback_penalty: int = 0,
) -> DecisionQuality:
    """고도화된 quality breakdown + HOLD 권고 산출 (pure, 부작용 0)."""
    sc = signal_consistency_score(votes, final_action)
    dr = data_reliability_score(data_status=data_status, price_stale=price_stale,
                                volume_ok=volume_ok, regime_known=regime_known)
    rk = risk_score(risk_flags, veto_applied)
    rg = regime_fit_score(market_regime, regime_decision, final_action)
    ep = exit_plan_score(final_action, has_exit_plan, exit_plan_valid)

    breakdown = {
        "signal_consistency": sc, "data_reliability": dr, "risk": rk,
        "regime_fit": rg, "exit_plan": ep,
    }
    weighted = (sc * _WEIGHTS["signal_consistency"] + dr * _WEIGHTS["data_reliability"]
                + rk * _WEIGHTS["risk"] + rg * _WEIGHTS["regime_fit"]
                + ep * _WEIGHTS["exit_plan"])
    fb = max(0, int(feedback_penalty))
    enhanced = _clamp(weighted - fb)
    penalties = {"feedback_penalty": fb}

    # quality gate: BUY 인데 enhanced < min_quality → HOLD 권고.
    should_hold = (str(final_action) == "BUY" and enhanced < int(min_quality))
    reason = QUALITY_SCORE_LOW_HOLD if should_hold else None
    notes: list[str] = []
    if should_hold:
        notes.append(f"enhanced quality {enhanced} < 임계 {int(min_quality)} → BUY 보류 권고")

    return DecisionQuality(
        enhanced_quality_score=enhanced, quality_grade=_grade(enhanced),
        breakdown=breakdown, penalties=penalties, should_hold=should_hold,
        reason_code=reason, min_quality=int(min_quality),
        final_action_input=str(final_action), notes=notes,
    )


def compute_decision_quality_from_council(
    council: dict[str, Any] | None, *,
    min_quality: int = 60, data_status: str | None = None,
    price_stale: bool | None = None, volume_ok: bool | None = None,
    feedback_penalty: int = 0,
) -> DecisionQuality:
    """council dict → DecisionQuality (endpoint / UI 용 read-only 진입점)."""
    c = council if isinstance(council, dict) else {}
    veto = c.get("risk_veto_result") if isinstance(c.get("risk_veto_result"), dict) else {}
    epv = c.get("exit_plan_validation") if isinstance(c.get("exit_plan_validation"), dict) else {}
    regime_known = bool(c.get("market_regime")) and str(c.get("market_regime")).upper() != "UNKNOWN"
    return compute_decision_quality(
        votes=c.get("votes") if isinstance(c.get("votes"), list) else [],
        final_action=str(c.get("final_action") or "HOLD"),
        risk_flags=c.get("risk_flags") or [],
        veto_applied=bool(veto.get("veto_applied")),
        market_regime=c.get("market_regime"),
        regime_decision=(c.get("metadata") or {}).get("regime_decision"),
        has_exit_plan=bool(c.get("has_exit_plan")),
        exit_plan_valid=(epv.get("valid") if "valid" in epv else None),
        min_quality=min_quality, data_status=data_status, price_stale=price_stale,
        volume_ok=volume_ok, regime_known=regime_known, feedback_penalty=feedback_penalty,
    )


__all__ = [
    "DecisionQuality", "QUALITY_SCORE_LOW_HOLD",
    "compute_decision_quality", "compute_decision_quality_from_council",
    "signal_consistency_score", "data_reliability_score", "risk_score",
    "regime_fit_score", "exit_plan_score",
]
