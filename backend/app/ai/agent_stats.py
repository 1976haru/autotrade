"""AI agent self-evaluation stats (162, MUST).

지능형 에이전트의 의사결정 품질을 운영자가 평가할 수 있도록 audit log 기반의
read-only 통계 산출. RiskManager 흐름과 분리 — 본 모듈은 어떤 주문 결정에도
영향 X.

주요 metric:
- total_proposals: 윈도우 내 requested_by_ai=True audit row 수.
- decision_breakdown: APPROVED / REJECTED / NEEDS_APPROVAL 카운트.
- approval_rate: APPROVED / (APPROVED + REJECTED). NEEDS_APPROVAL 제외.
- avg_confidence: 통과한 (executed=True) 주문의 평균 signal_confidence.
  None인 row는 평균 산출에서 제외.
- per_strategy: strategy별로 동일 metric 분리. 운영자가 어느 에이전트 전략이
  잘 작동하는지 비교 가능.
- top_rejection_reasons: rejected 주문의 reason category 빈도 — 어떤 가드가
  가장 자주 막는지 (confidence / notional / emergency_stop / 등).

설계: backlog 항목 11(`Strategy Scoreboard FE 확장`)이 strategy 단위 평가를
다룬다면 본 모듈은 *AI agent 단위* 평가에 집중 — strategy=ai_*인 audit만 본다.
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.feedback import compute_historical_accuracy
from app.db.models import OrderAuditLog


# 165: confidence histogram bucket 경계. 4구간으로 모든 0..100 커버.
_HISTOGRAM_BUCKETS: list[tuple[str, int, int]] = [
    ("0-25",   0,  25),
    ("25-50",  25, 50),
    ("50-75",  50, 75),
    ("75-100", 75, 101),  # 상한 포함을 위해 101.
]


def _bucket_for_confidence(c: int) -> str | None:
    for label, lo, hi in _HISTOGRAM_BUCKETS:
        if lo <= c < hi:
            return label
    return None


_REASON_CATEGORIES: list[tuple[tuple[str, ...], str]] = [
    (("emergency",), "emergency_stop"),
    (("stale",), "stale_price"),
    (("ai signal confidence",), "low_confidence"),
    (("missing reasoning",), "missing_reasoning"),
    (("rate limit",), "rate_limit"),
    (("max_order_notional", "order notional"), "notional"),
    (("max_positions", "max positions"), "max_positions"),
    (("symbol exposure",), "symbol_exposure"),
    (("daily loss",), "daily_loss"),
    (("insufficient cash",), "insufficient_cash"),
    (("live trading",), "live_disabled"),
    (("ai execution is not allowed",), "ai_mode_disabled"),
    (("live_shadow",), "shadow_mode"),
]


def _categorize_reason(reason: str) -> str:
    """reason 문자열을 거친 카테고리로 분류 — top_rejection_reasons 집계용.
    매핑이 자유 텍스트라 substring 기반. 운영자가 분포만 파악."""
    r = reason.lower()
    for needles, category in _REASON_CATEGORIES:
        if any(n in r for n in needles):
            return category
    return "other"


def compute_ai_agent_stats(
    db:             Session,
    *,
    lookback_days:  int = 7,
    now:            datetime | None = None,
) -> dict:
    """AI agent 통계. requested_by_ai=True 행만 본다.

    `lookback_days <= 0`이면 전체 기간 (cap 없음 — 운영자 의도적 사용).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if lookback_days > 0:
        cutoff = now - timedelta(days=lookback_days)
        rows = db.execute(
            select(OrderAuditLog).where(
                OrderAuditLog.requested_by_ai.is_(True),
                OrderAuditLog.created_at > cutoff,
            )
        ).scalars().all()
    else:
        rows = db.execute(
            select(OrderAuditLog).where(
                OrderAuditLog.requested_by_ai.is_(True),
            )
        ).scalars().all()

    decision_counts = Counter(r.decision for r in rows)
    approved = decision_counts.get("APPROVED", 0)
    rejected = decision_counts.get("REJECTED", 0)
    pending  = decision_counts.get("NEEDS_APPROVAL", 0)

    decided = approved + rejected
    approval_rate = approved / decided if decided > 0 else 0.0

    # 평균 confidence — 통과한 (executed=True) row 중 confidence 있는 것만.
    confidences = [
        r.signal_confidence for r in rows
        if r.executed and r.signal_confidence is not None
    ]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    # rejection reason 카테고리.
    reason_categories: Counter[str] = Counter()
    for r in rows:
        if r.decision != "REJECTED":
            continue
        for reason in (r.reasons or []):
            reason_categories[_categorize_reason(reason)] += 1

    # per_strategy 분리.
    by_strategy: dict[str, dict] = defaultdict(lambda: {
        "total":     0,
        "approved":  0,
        "rejected":  0,
        "pending":   0,
        "avg_confidence": 0.0,
        "_conf_sum": 0.0,
        "_conf_n":   0,
    })
    for r in rows:
        s = r.strategy or "(unknown)"
        cur = by_strategy[s]
        cur["total"] += 1
        if r.decision == "APPROVED":
            cur["approved"] += 1
        elif r.decision == "REJECTED":
            cur["rejected"] += 1
        elif r.decision == "NEEDS_APPROVAL":
            cur["pending"] += 1
        if r.executed and r.signal_confidence is not None:
            cur["_conf_sum"] += r.signal_confidence
            cur["_conf_n"]   += 1

    # 165: confidence histogram — 모든 윈도우 내 row의 signal_confidence 분포.
    # confidence가 None인 row는 별도 카운터.
    histogram = {label: 0 for label, _, _ in _HISTOGRAM_BUCKETS}
    histogram_missing = 0
    for r in rows:
        c = r.signal_confidence
        if c is None:
            histogram_missing += 1
            continue
        bucket = _bucket_for_confidence(c)
        if bucket is not None:
            histogram[bucket] += 1

    per_strategy = []
    for s, cur in by_strategy.items():
        cur_avg = cur["_conf_sum"] / cur["_conf_n"] if cur["_conf_n"] > 0 else 0.0
        decided_s = cur["approved"] + cur["rejected"]

        # 165: strategy별 realized PnL — 163 compute_historical_accuracy를 재사용해
        # 같은 lookback 윈도우에서 win/loss/total_pnl 산출. (unknown) strategy는
        # NULL row라 매칭할 strategy가 없으므로 0.
        if s == "(unknown)":
            wins, losses, realized_pnl = 0, 0, 0
        else:
            acc = compute_historical_accuracy(
                db, strategy=s, lookback_days=lookback_days, now=now,
            )
            wins, losses, realized_pnl = acc.wins, acc.losses, acc.realized_pnl

        per_strategy.append({
            "strategy":       s,
            "total":          cur["total"],
            "approved":       cur["approved"],
            "rejected":       cur["rejected"],
            "pending":        cur["pending"],
            "approval_rate":  cur["approved"] / decided_s if decided_s > 0 else 0.0,
            "avg_confidence": cur_avg,
            "wins":           wins,
            "losses":         losses,
            "realized_pnl":   realized_pnl,
        })
    per_strategy.sort(key=lambda x: x["total"], reverse=True)

    return {
        "lookback_days":     lookback_days,
        "total_proposals":   len(rows),
        "approved":          approved,
        "rejected":          rejected,
        "needs_approval":    pending,
        "approval_rate":     approval_rate,
        "avg_confidence":    avg_confidence,
        "top_rejection_reasons": dict(reason_categories.most_common()),
        "per_strategy":      per_strategy,
        # 165 신규
        "confidence_histogram":         histogram,
        "confidence_histogram_missing": histogram_missing,
    }
