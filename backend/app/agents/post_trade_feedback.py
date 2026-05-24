"""#50 / 6-05: PostTradeReview 피드백 루프 (advisory, 자동 적용 금지).

거래 후 복기(P-27) / outcome(P-25) 집계를 *다음 판단에 참고할* 피드백으로 요약하고,
동일 실패 요인이 반복되면 threshold 조정 *후보* 를 생성한다.

**복기 결과는 분석 정보다 — 자동으로 실전 전환을 허용하거나 주문을 생성하지 않으며,
threshold 추천은 운영자 승인 없이 자동 적용되지 않는다.**

CLAUDE.md 가드 (정적 grep 으로 lock):
- broker / OrderExecutor / 단일 주문 라우터 / paper_trader / KIS 어댑터 import 0건.
- broker 주문/취소 호출 0건, DB write 0건, 외부 HTTP / AI SDK import 0건.
- `FeedbackLoopReport.auto_apply_allowed=False` / `requires_operator_approval=True` /
  `is_order_signal=False` / `is_live_authorization=False` / `contains_secret=False` 불변.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 피드백 태그.
WINNING_SETUP = "WINNING_SETUP"
LOSING_SETUP = "LOSING_SETUP"
OVER_ENTRY = "OVER_ENTRY"
LATE_EXIT = "LATE_EXIT"
EARLY_EXIT = "EARLY_EXIT"
POOR_RISK_REWARD = "POOR_RISK_REWARD"
LOW_DATA_QUALITY = "LOW_DATA_QUALITY"
MARKET_REGIME_MISMATCH = "MARKET_REGIME_MISMATCH"

# threshold 추천을 만들 반복 임계 (기본 3회 이상).
DEFAULT_RECOMMEND_MIN = 3

DISCLAIMER_KO = (
    "복기 피드백은 다음 판단에 참고하는 분석 정보입니다. 자동으로 실전 전환을 "
    "허용하거나 주문을 생성하지 않으며, threshold 추천은 운영자 승인 없이 적용되지 "
    "않습니다. 수익을 보장하지 않습니다."
)

# threshold 추천 텍스트 (reason_code → message).
_RECOMMENDATION_TEXT = {
    OVER_ENTRY:    "신호 약할 때(quality_score 낮음) 진입 보류 강화 검토",
    LATE_EXIT:     "VWAP 이탈 시 청산 판단 강화 검토",
    EARLY_EXIT:    "이른 청산 방지 — 익절 목표 도달 전 청산 기준 재검토",
    POOR_RISK_REWARD: "RR(손익비) 낮은 셋업 진입 보류 검토",
    LOW_DATA_QUALITY: "PRICE_STALE / 데이터 품질 저하 시 진입 금지 유지",
    MARKET_REGIME_MISMATCH: "장세 부적합(BLOCK_NEW_BUY) 시 BUY 억제 유지",
    LOSING_SETUP:  "반복 손실 셋업 — 해당 전략 threshold 상향 검토",
}


def _sum(d: dict | None, *keys: str) -> int:
    if not isinstance(d, dict):
        return 0
    return sum(int(d.get(k, 0) or 0) for k in keys)


@dataclass(frozen=True)
class FeedbackTag:
    tag:      str
    count:    int
    severity: str            # INFO / WARN / HIGH
    message:  str

    def to_dict(self) -> dict[str, Any]:
        return {"tag": self.tag, "count": int(self.count),
                "severity": self.severity, "message": self.message}


@dataclass(frozen=True)
class ThresholdRecommendation:
    reason_code:                str
    message:                    str
    evidence_count:             int
    auto_apply_allowed:         bool = False
    requires_operator_approval: bool = True

    def __post_init__(self) -> None:
        if self.auto_apply_allowed is not False:
            raise ValueError("auto_apply_allowed must be False")
        if self.requires_operator_approval is not True:
            raise ValueError("requires_operator_approval must be True")

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason_code":                self.reason_code,
            "message":                    self.message,
            "evidence_count":             int(self.evidence_count),
            "auto_apply_allowed":         False,
            "requires_operator_approval": True,
        }


@dataclass(frozen=True)
class FeedbackLoopReport:
    """복기 피드백 요약 — read-only advisory."""

    status:            str            # OK / INSUFFICIENT_DATA
    sample_count:      int
    win_count:         int
    loss_count:        int
    neutral_count:     int
    feedback_tags:     list[dict[str, Any]]
    strategy_errors:   dict[str, int]
    threshold_recommendations: list[dict[str, Any]]
    feedback_summary:  str

    auto_apply_allowed:         bool = False
    requires_operator_approval: bool = True
    is_order_signal:            bool = False
    is_live_authorization:      bool = False
    contains_secret:            bool = False

    def __post_init__(self) -> None:
        if self.auto_apply_allowed is not False:
            raise ValueError("auto_apply_allowed must be False")
        if self.requires_operator_approval is not True:
            raise ValueError("requires_operator_approval must be True")
        for name in ("is_order_signal", "is_live_authorization", "contains_secret"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be False (피드백은 advisory)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status":            self.status,
            "sample_count":      int(self.sample_count),
            "win_count":         int(self.win_count),
            "loss_count":        int(self.loss_count),
            "neutral_count":     int(self.neutral_count),
            "feedback_tags":     list(self.feedback_tags),
            "strategy_errors":   dict(self.strategy_errors),
            "threshold_recommendations": list(self.threshold_recommendations),
            "feedback_summary":  self.feedback_summary,
            "auto_apply_allowed":         False,
            "requires_operator_approval": True,
            "is_order_signal":       False,
            "is_live_authorization": False,
            "contains_secret":       False,
            "disclaimer":            DISCLAIMER_KO,
        }


def _severity(count: int) -> str:
    if count >= DEFAULT_RECOMMEND_MIN:
        return "HIGH"
    if count >= 2:
        return "WARN"
    return "INFO"


def build_feedback_loop(
    summary: dict[str, Any] | None, *,
    strategy_errors: dict[str, int] | None = None,
    recommend_min: int = DEFAULT_RECOMMEND_MIN,
) -> FeedbackLoopReport:
    """`summarize_episodes` 출력(또는 동형 dict) → 피드백 요약 + threshold 추천.

    Args:
        summary: by_review_grade / by_review_tag / by_outcome_label /
            by_data_status / by_reason_code 를 가진 집계 dict.
        strategy_errors: 전략별 오류(손실) 카운트 (선택). 없으면 빈 dict.
    """
    s = summary if isinstance(summary, dict) else {}
    grade = s.get("by_review_grade") or {}
    rtag = s.get("by_review_tag") or {}
    olabel = s.get("by_outcome_label") or {}
    dstatus = s.get("by_data_status") or {}
    reason = s.get("by_reason_code") or {}

    win = _sum(grade, "GOOD")
    loss = _sum(grade, "BAD")
    neutral = _sum(grade, "NEUTRAL")
    sample = win + loss + neutral

    # 태그 카운트 집계 (review tag + outcome label + data status 매핑).
    counts: dict[str, int] = {
        WINNING_SETUP:  win + _sum(olabel, "PROFITABLE", "AVOIDED_LOSS"),
        LOSING_SETUP:   loss + _sum(olabel, "LOSS"),
        OVER_ENTRY:     _sum(rtag, "WEAK_SIGNAL_ENTRY", "EARLY_ENTRY"),
        LATE_EXIT:      _sum(rtag, "LATE_EXIT", "IMPROVE_EXIT_TIMING"),
        EARLY_EXIT:     _sum(olabel, "MISSED_OPPORTUNITY"),
        POOR_RISK_REWARD: _sum(rtag, "FALSE_BREAKOUT", "BAD_DECISION"),
        LOW_DATA_QUALITY: _sum(dstatus, "STALE", "FAIL", "STALE_PRICE")
                          + _sum(reason, "PRICE_STALE", "STALE_PRICE"),
        MARKET_REGIME_MISMATCH: _sum(reason, "BLOCK_NEW_BUY", "MARKET_REGIME_BLOCK",
                                     "REGIME_MISMATCH"),
    }

    feedback_tags = [
        FeedbackTag(tag=t, count=c, severity=_severity(c),
                    message=_RECOMMENDATION_TEXT.get(t, t)).to_dict()
        for t, c in counts.items() if c > 0
    ]

    # threshold 추천 — *실패성* 태그가 recommend_min 이상 반복 시 생성.
    failure_tags = (OVER_ENTRY, LATE_EXIT, EARLY_EXIT, POOR_RISK_REWARD,
                    LOW_DATA_QUALITY, MARKET_REGIME_MISMATCH, LOSING_SETUP)
    recs = [
        ThresholdRecommendation(
            reason_code=t, message=_RECOMMENDATION_TEXT.get(t, t),
            evidence_count=counts[t],
        ).to_dict()
        for t in failure_tags
        if counts.get(t, 0) >= max(1, int(recommend_min))
    ]

    status = "OK" if sample > 0 or any(c > 0 for c in counts.values()) else "INSUFFICIENT_DATA"
    summ = (f"표본 {sample}건 (승 {win} / 패 {loss} / 중립 {neutral}). "
            f"피드백 태그 {len(feedback_tags)}종, threshold 추천 {len(recs)}건 "
            "(운영자 승인 전 자동 적용 없음).")

    return FeedbackLoopReport(
        status=status, sample_count=sample, win_count=win, loss_count=loss,
        neutral_count=neutral, feedback_tags=feedback_tags,
        strategy_errors=dict(strategy_errors or {}),
        threshold_recommendations=recs, feedback_summary=summ,
    )


def feedback_penalty_for(report_dict: dict[str, Any] | None) -> int:
    """피드백 요약 → 다음 판단 quality 에 반영할 penalty (0~30).

    실패성 태그(HIGH severity)가 많을수록 penalty 증가 — quality 를 보수적으로 낮춰
    무조건 매수를 억제한다. 자동 적용이 아니라 *quality 계산 입력* 으로만 사용.
    """
    if not isinstance(report_dict, dict):
        return 0
    tags = report_dict.get("feedback_tags") or []
    high = sum(1 for t in tags if isinstance(t, dict) and t.get("severity") == "HIGH"
               and t.get("tag") in (OVER_ENTRY, LATE_EXIT, POOR_RISK_REWARD,
                                     LOW_DATA_QUALITY, MARKET_REGIME_MISMATCH, LOSING_SETUP))
    return min(high * 10, 30)


__all__ = [
    "WINNING_SETUP", "LOSING_SETUP", "OVER_ENTRY", "LATE_EXIT", "EARLY_EXIT",
    "POOR_RISK_REWARD", "LOW_DATA_QUALITY", "MARKET_REGIME_MISMATCH",
    "FeedbackTag", "ThresholdRecommendation", "FeedbackLoopReport",
    "build_feedback_loop", "feedback_penalty_for",
]
