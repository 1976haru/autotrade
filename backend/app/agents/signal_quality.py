"""Signal Quality Gate — Agent-aware scoring (226, MUST).

기존 strategies/quality.py(136)는 strategy 자체 강도·신뢰도만 평가.
본 모듈은 Agent Council 출력까지 합쳐 종합 quality_score 산출 + 통과/거절
권고를 만든다. 운영자가 스마트폰에서 한 줄 요약으로 인지하도록 operator_summary
까지 포함.

평가 항목 (각 0-100, weight 가중 평균):
  - signal_strength:        전략 신호 자체 강도 (gap_pct 기반)
  - regime_fit:             현재 장세 적합성 (allowed_strategies 매칭)
  - agent_agreement:        Agent들의 의견 일치도
  - scenario_stress:        ScenarioStressAgent 점수
  - exit_plan_quality:      stop/take_profit 명시 여부 + 리스크-리워드 비율
  - sizing_safety:          requested_qty가 RiskManager 허용 안인지
  - data_freshness:         시세 freshness (시간차)
  - duplicate_signal:       같은 chain에서 중복 신호 여부

출력:
  quality_score: 0-100
  quality_grade: A / B / C / D / F
  approval_recommendation: APPROVE / NEEDS_REVIEW / REJECT
  rejection_reasons: list[str]
  min_required_score: int (운영자 임계값)
  operator_summary: list[str] (3줄)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SignalQualityResult:
    quality_score:           int
    quality_grade:           str
    approval_recommendation: str
    rejection_reasons:       list[str] = field(default_factory=list)
    min_required_score:      int       = 60
    breakdown:               dict[str, int] = field(default_factory=dict)
    operator_summary:        list[str] = field(default_factory=list)


# 각 항목의 가중치 — 합 1.0. 보수적 운용 기조: agent_agreement / scenario_stress /
# sizing_safety가 핵심.
_WEIGHTS: dict[str, float] = {
    "signal_strength":   0.10,
    "regime_fit":        0.15,
    "agent_agreement":   0.20,
    "scenario_stress":   0.15,
    "exit_plan_quality": 0.10,
    "sizing_safety":     0.20,
    "data_freshness":    0.05,
    "duplicate_penalty": 0.05,  # 100=신선, 0=중복
}


def evaluate_signal_quality(
    *,
    signal_strength:    int = 0,
    regime_fit:         int = 0,
    agent_agreement:    int = 0,
    scenario_stress:    int = 0,
    exit_plan_quality:  int = 0,
    sizing_safety:      int = 0,
    data_freshness:     int = 100,
    duplicate_penalty:  int = 100,
    min_required_score: int = 60,
) -> SignalQualityResult:
    """모든 입력은 0-100. 미입력 항목은 0 (= 정보 없음 = 감점)."""
    breakdown = {
        "signal_strength":   _clamp(signal_strength),
        "regime_fit":        _clamp(regime_fit),
        "agent_agreement":   _clamp(agent_agreement),
        "scenario_stress":   _clamp(scenario_stress),
        "exit_plan_quality": _clamp(exit_plan_quality),
        "sizing_safety":     _clamp(sizing_safety),
        "data_freshness":    _clamp(data_freshness),
        "duplicate_penalty": _clamp(duplicate_penalty),
    }

    score = sum(breakdown[k] * w for k, w in _WEIGHTS.items())
    score = int(round(score))
    score = max(0, min(100, score))

    grade = _grade_from_score(score)
    rejection_reasons = _collect_rejections(breakdown)

    if score < min_required_score:
        recommendation = "REJECT"
    elif rejection_reasons:
        recommendation = "NEEDS_REVIEW"
    elif score >= 80:
        recommendation = "APPROVE"
    else:
        recommendation = "NEEDS_REVIEW"

    summary = _operator_summary(score, grade, recommendation, rejection_reasons)

    return SignalQualityResult(
        quality_score=score,
        quality_grade=grade,
        approval_recommendation=recommendation,
        rejection_reasons=rejection_reasons,
        min_required_score=min_required_score,
        breakdown=breakdown,
        operator_summary=summary,
    )


def _clamp(v: int) -> int:
    return max(0, min(100, int(v)))


def _grade_from_score(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


# 항목별로 미달 임계값 — 이 임계 미만이면 운영자가 봐야 할 risk reason.
# 빠진 항목은 거절 사유에 포함하지 않는다 (sizing_safety < 50처럼 명백한 건만).
_MIN_PER_ITEM = {
    "agent_agreement":   50,
    "sizing_safety":     50,
    "scenario_stress":   30,
    "data_freshness":    30,
    "duplicate_penalty": 50,
}


def _collect_rejections(breakdown: dict[str, int]) -> list[str]:
    out: list[str] = []
    for key, threshold in _MIN_PER_ITEM.items():
        if breakdown.get(key, 0) < threshold:
            out.append(f"{key}<{threshold}({breakdown.get(key, 0)})")
    return out


def _operator_summary(
    score: int, grade: str, recommendation: str, rejection_reasons: list[str],
) -> list[str]:
    """스마트폰 3줄 요약."""
    line1 = f"신호 품질 {grade} ({score}점)"
    if recommendation == "APPROVE":
        line2 = "✓ 승인 권고"
    elif recommendation == "REJECT":
        line2 = "✗ 거절 — 임계 미달"
    else:
        line2 = "⚠ 사람 검토 필요"
    if rejection_reasons:
        line3 = "사유: " + ", ".join(rejection_reasons[:2])
    else:
        line3 = "주요 항목 모두 통과"
    return [line1, line2, line3]
