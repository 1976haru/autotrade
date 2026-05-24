"""#49 / 6-04: 주문·체결 품질 + 차단 사유 집계 (read-only, 성과 대시보드용).

decision episode 목록에서 **체결 실패율 / 주문 거절률 / 부분체결률 / 차단 사유 TOP**
를 집계한다. 기존 `episode.order_quality_summary`(P-24) 와 `episode.council` 에 *이미
있는* 필드만 read-only 로 읽어 비율을 산출한다 — 실제 계좌 잔고 미사용, broker 호출 0.

**본 모듈은 성과 *표시* 전용이다 — 실거래 승인이 아니며 수익을 보장하지 않는다.**

CLAUDE.md 가드 (정적 grep 으로 lock):
- broker / OrderExecutor / 단일 주문 라우터 / paper_trader / KIS 어댑터 import 0건.
- broker 주문/취소 호출 0건, DB write 0건, 외부 HTTP / AI SDK import 0건.
- `OrderQualityMetrics.is_order_signal=False` / `is_live_authorization=False` /
  `contains_secret=False` 불변.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

# 주문이 *접수/체결 시도* 된 것으로 보는 상태 (분모).
_ORDER_STATUSES = {
    "SUBMITTED", "ACCEPTED", "FILLED", "PARTIALLY_FILLED", "UNFILLED",
    "REJECTED", "ERROR", "CANCELLED", "EXPIRED",
}
_FILLED = {"FILLED"}
_PARTIAL = {"PARTIALLY_FILLED"}
_FAILURE = {"REJECTED", "ERROR"}          # 실패율 분자.
_REJECTED = {"REJECTED"}

DISCLAIMER_KO = (
    "이 지표는 분석/표시 전용입니다. 실전 전환 승인과 무관하며, 수익을 보장하지 "
    "않습니다. decision episode 기록 기반 추정이며 실제 계좌 잔고가 아닙니다."
)


def _ratio(n: int, d: int) -> float | None:
    return round(n / d, 4) if d > 0 else None


def _order_status(ep: dict) -> str | None:
    q = ep.get("order_quality_summary")
    if isinstance(q, dict) and q.get("order_status"):
        return str(q["order_status"]).upper()
    # full kis_order_result.order_quality fallback.
    kor = ep.get("kis_order_result")
    if isinstance(kor, dict):
        oq = kor.get("order_quality")
        if isinstance(oq, dict) and oq.get("order_status"):
            return str(oq["order_status"]).upper()
    return None


def _slippage_bps(ep: dict) -> float | None:
    q = ep.get("order_quality_summary")
    if isinstance(q, dict) and q.get("slippage_bps") is not None:
        try:
            return float(q["slippage_bps"])
        except (TypeError, ValueError):
            return None
    return None


def _blocked_reason(ep: dict) -> str | None:
    """이 episode 가 *차단/거절/보류* 된 주된 사유 코드 (없으면 None).

    우선순위: 주문 거절 사유 > RiskOfficer veto > exit_plan 검증 실패 > HOLD reason_code.
    """
    status = _order_status(ep)
    council = ep.get("council") if isinstance(ep.get("council"), dict) else {}

    # 1) 주문 거절 — rejection_reason_code (full order_quality).
    if status in _FAILURE:
        kor = ep.get("kis_order_result")
        if isinstance(kor, dict):
            oq = kor.get("order_quality")
            if isinstance(oq, dict) and oq.get("rejection_reason_code"):
                return str(oq["rejection_reason_code"])
        return "ORDER_REJECTED"

    # 2) RiskOfficer veto.
    veto = council.get("risk_veto_result")
    if isinstance(veto, dict) and veto.get("veto_applied"):
        return str(veto.get("reason_code") or "RISK_OFFICER_VETO")

    # 3) exit_plan 검증 실패로 BUY 강등.
    epv = council.get("exit_plan_validation")
    if isinstance(epv, dict) and epv.get("valid") is False:
        return str(epv.get("reason_code") or "EXIT_PLAN_INVALID")

    # 4) HOLD 로 끝난 episode 의 reason_code.
    if str(ep.get("final_action")) == "HOLD" and ep.get("reason_code"):
        return str(ep["reason_code"])

    return None


@dataclass(frozen=True)
class OrderQualityMetrics:
    """주문·체결 품질 + 차단 사유 집계 — UI 안전 payload."""

    status:               str            # OK / INSUFFICIENT_DATA
    episode_count:        int
    order_count:          int
    filled_count:         int
    partial_fill_count:   int
    rejected_count:       int
    failure_count:        int
    fill_rate:            float | None
    order_failure_rate:   float | None
    rejected_rate:        float | None
    partial_fill_rate:    float | None
    avg_slippage_bps:     float | None
    blocked_reasons_top:  list[dict[str, Any]] = field(default_factory=list)
    status_breakdown:     dict[str, int] = field(default_factory=dict)

    is_order_signal:       bool = False
    is_live_authorization: bool = False
    contains_secret:       bool = False

    def __post_init__(self) -> None:
        for name in ("is_order_signal", "is_live_authorization", "contains_secret"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be False (지표는 표시 전용)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status":              self.status,
            "episode_count":       int(self.episode_count),
            "order_count":         int(self.order_count),
            "filled_count":        int(self.filled_count),
            "partial_fill_count":  int(self.partial_fill_count),
            "rejected_count":      int(self.rejected_count),
            "failure_count":       int(self.failure_count),
            "fill_rate":           self.fill_rate,
            "order_failure_rate":  self.order_failure_rate,
            "rejected_rate":       self.rejected_rate,
            "partial_fill_rate":   self.partial_fill_rate,
            "avg_slippage_bps":    self.avg_slippage_bps,
            "blocked_reasons_top": list(self.blocked_reasons_top),
            "status_breakdown":    dict(self.status_breakdown),
            "is_order_signal":       False,
            "is_live_authorization": False,
            "contains_secret":       False,
            "disclaimer":            DISCLAIMER_KO,
        }


def aggregate_order_quality(
    episodes: list[dict] | None, *, top_n: int = 5,
) -> OrderQualityMetrics:
    """episode 목록 → 주문 품질 비율 + 차단 사유 TOP (read-only)."""
    eps = episodes if isinstance(episodes, list) else []
    status_counter: Counter[str] = Counter()
    blocked_counter: Counter[str] = Counter()
    slippages: list[float] = []
    order_count = filled = partial = rejected = failure = 0

    for ep in eps:
        if not isinstance(ep, dict):
            continue
        status = _order_status(ep)
        if status in _ORDER_STATUSES:
            order_count += 1
            status_counter[status] += 1
            if status in _FILLED:
                filled += 1
            if status in _PARTIAL:
                partial += 1
            if status in _REJECTED:
                rejected += 1
            if status in _FAILURE:
                failure += 1
        sb = _slippage_bps(ep)
        if sb is not None:
            slippages.append(sb)
        br = _blocked_reason(ep)
        if br:
            blocked_counter[br] += 1

    avg_slip = round(sum(slippages) / len(slippages), 2) if slippages else None
    blocked_top = [{"reason": r, "count": n}
                   for r, n in blocked_counter.most_common(max(1, top_n))]
    status = "OK" if eps else "INSUFFICIENT_DATA"

    return OrderQualityMetrics(
        status=status,
        episode_count=len(eps),
        order_count=order_count,
        filled_count=filled,
        partial_fill_count=partial,
        rejected_count=rejected,
        failure_count=failure,
        fill_rate=_ratio(filled, order_count),
        order_failure_rate=_ratio(failure, order_count),
        rejected_rate=_ratio(rejected, order_count),
        partial_fill_rate=_ratio(partial, order_count),
        avg_slippage_bps=avg_slip,
        blocked_reasons_top=blocked_top,
        status_breakdown=dict(status_counter),
    )


__all__ = ["OrderQualityMetrics", "aggregate_order_quality"]
