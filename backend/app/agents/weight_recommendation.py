"""P-29: Agent 전략 가중치 개선 *후보* 추천 — 자동 적용 절대 금지.

P-28 전략별 성과(StrategyPerformanceReport) 를 바탕으로 ORB / MOMENTUM / GAP /
VWAP 가중치 조정 *후보* 를 추천한다. 성과가 좋은 전략은 비중 상향, 나쁜 전략은
하향 후보를 제시하되 **운영자 승인 전에는 절대 실제 Agent 설정에 반영하지 않는다**.

## 절대 invariant (CLAUDE.md)
- 본 모듈은 *추천 전용* — broker / OrderExecutor / route_order / 외부 HTTP /
  실 계좌 조회 import 0건, Agent 설정(STRATEGY_WEIGHTS) 을 *변경하지 않는다*.
- **자동 적용 금지** — `WeightRecommendation.auto_apply_allowed=False` /
  `requires_operator_approval=True` 영구. apply / 저장 / mutation 경로 0건.
- 추천 결과는 *주문 신호가 아니다* — `is_order_signal=False` /
  `is_live_authorization=False` / `uses_account_balance=False` 영구.
- secret / API key / 계좌번호 carry 0건. 결정적(deterministic).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# 기본 현재 가중치 (agent_council.STRATEGY_WEIGHTS 와 정합 — 합 100).
DEFAULT_CURRENT_WEIGHTS: dict[str, int] = {
    "MOMENTUM": 30, "VWAP": 25, "ORB": 25, "GAP": 20,
}
SINGLE_STRATEGIES: tuple[str, ...] = ("ORB", "MOMENTUM", "GAP", "VWAP")

MIN_WEIGHT = 10
MAX_WEIGHT = 50

# action.
ACTION_INCREASE = "INCREASE"
ACTION_DECREASE = "DECREASE"
ACTION_KEEP     = "KEEP"
ACTION_NEEDS_MORE_DATA = "NEEDS_MORE_DATA"

# status.
STATUS_RECOMMENDATION_ONLY = "RECOMMENDATION_ONLY"
STATUS_NEEDS_MORE_DATA     = "NEEDS_MORE_DATA"

# 표본 임계.
MIN_TOTAL_EVALUATED = 10    # 추천 활성화 최소 평가 episode 수
MIN_STRATEGY_SAMPLE = 5     # 전략별 신뢰 표본 (미만이면 delta 제한)

# risk_profile 별 한 번에 허용하는 최대 delta.
_MAX_DELTA_BY_PROFILE: dict[str, int] = {
    "CONSERVATIVE": 5, "BALANCED": 8, "AGGRESSIVE": 10,
}
_SMALL_SAMPLE_MAX_DELTA = 5


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class WeightRecommendation:
    """전략 가중치 추천 *후보* — 자동 적용 / 주문 신호 / 실거래 권한 아님."""

    recommendation_id: str
    created_at:        str
    status:            str
    lookback_count:    int
    risk_profile:      str
    market_regime:     str
    current_weights:     dict[str, int] = field(default_factory=dict)
    recommended_weights: dict[str, int] = field(default_factory=dict)
    deltas:              dict[str, int] = field(default_factory=dict)
    strategy_actions:    list[dict[str, Any]] = field(default_factory=list)
    expected_effect:     str = ""
    warning:             str | None = None
    note:                str = ""

    requires_operator_approval: bool = True
    auto_apply_allowed:    bool = False
    contains_secret:       bool = False
    uses_account_balance:  bool = False
    is_order_signal:       bool = False
    is_live_authorization: bool = False

    def __post_init__(self) -> None:
        if self.requires_operator_approval is not True:
            raise ValueError("WeightRecommendation.requires_operator_approval must be True")
        if self.auto_apply_allowed is not False:
            raise ValueError("WeightRecommendation.auto_apply_allowed must be False")
        if self.contains_secret is not False:
            raise ValueError("WeightRecommendation.contains_secret must be False")
        if self.uses_account_balance is not False:
            raise ValueError("WeightRecommendation.uses_account_balance must be False")
        if self.is_order_signal is not False:
            raise ValueError("WeightRecommendation.is_order_signal must be False")
        if self.is_live_authorization is not False:
            raise ValueError("WeightRecommendation.is_live_authorization must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation_id":   self.recommendation_id,
            "created_at":          self.created_at,
            "status":              self.status,
            "lookback_count":      self.lookback_count,
            "risk_profile":        self.risk_profile,
            "market_regime":       self.market_regime,
            "current_weights":     dict(self.current_weights),
            "recommended_weights": dict(self.recommended_weights),
            "deltas":              dict(self.deltas),
            "strategy_actions":    list(self.strategy_actions),
            "expected_effect":     self.expected_effect,
            "warning":             self.warning,
            "note":                self.note,
            "requires_operator_approval": True,
            "auto_apply_allowed":    False,
            "contains_secret":       False,
            "uses_account_balance":  False,
            "is_order_signal":       False,
            "is_live_authorization": False,
            "advisory_disclaimer": (
                "이 추천은 분석용이며 자동 적용되지 않습니다. 운영자 승인 전에는 "
                "Agent 가중치가 변경되지 않습니다. 본 추천은 주문 신호가 아니며 "
                "실제 계좌정보를 사용하지 않습니다."
            ),
        }


def _strategy_block_map(performance_report: Any) -> dict[str, dict[str, Any]]:
    """StrategyPerformanceReport(또는 dict) → 전략별 블록 맵."""
    if hasattr(performance_report, "strategies"):
        blocks = performance_report.strategies
    elif isinstance(performance_report, dict):
        blocks = performance_report.get("strategies", [])
    else:
        blocks = []
    return {str(b.get("strategy")).upper(): b for b in blocks if isinstance(b, dict)}


def _fitness(block: dict[str, Any]) -> tuple[float, list[str]]:
    """전략 성과 블록 → fitness([-1,+1]) + 사유 목록."""
    reasons: list[str] = []
    pf = block.get("profit_factor")
    win_rate = float(block.get("win_rate") or 0.0)
    payoff = block.get("payoff_ratio")
    mdd = float(block.get("max_drawdown") or 0.0)
    streak = int(block.get("max_consecutive_losses") or 0)
    avg_loss = float(block.get("average_loss") or 0.0)

    # profit_factor: None(손실 0 + 이익>0) 은 강한 양수로 간주.
    if pf is None:
        pf_comp = 0.4
        reasons.append("손실 거래 없음(profit factor 무한) — 강한 양수")
    else:
        pf_comp = _clamp((float(pf) - 1.0) * 0.4, -0.4, 0.4)
        if pf >= 1.5:
            reasons.append(f"profit factor {round(float(pf), 2)} 우수")
        elif pf < 1.0:
            reasons.append(f"profit factor {round(float(pf), 2)} 부진")

    wr_comp = _clamp((win_rate - 0.5) * 0.6, -0.3, 0.3)
    if win_rate >= 0.6:
        reasons.append(f"승률 {round(win_rate * 100, 1)}% 양호")
    elif win_rate < 0.4:
        reasons.append(f"승률 {round(win_rate * 100, 1)}% 저조")

    if payoff is None:
        payoff_comp = 0.0
    else:
        payoff_comp = _clamp((float(payoff) - 1.0) * 0.2, -0.2, 0.2)
        if payoff >= 1.5:
            reasons.append(f"손익비 {round(float(payoff), 2)} 우수")

    dd_penalty = _clamp(mdd * 0.4, 0.0, 0.4)
    if mdd >= 0.2:
        reasons.append(f"MDD {round(mdd * 100, 1)}% 높음 — 감점")
    streak_penalty = _clamp(max(0, streak - 2) * 0.1, 0.0, 0.3)
    if streak >= 3:
        reasons.append(f"연속 손실 {streak}회 — 감점")
    if avg_loss < -1.0:
        reasons.append(f"평균손실 {round(avg_loss, 2)}% 큼 — 감점")

    fitness = _clamp(pf_comp + wr_comp + payoff_comp - dd_penalty - streak_penalty,
                     -1.0, 1.0)
    return fitness, reasons


def _normalize_to_100(weights: dict[str, float]) -> dict[str, int]:
    """float 가중치 → 합 100 정수 (min/max 준수, 결정적 잔차 분배)."""
    keys = sorted(weights.keys())
    w = {k: _clamp(weights[k], MIN_WEIGHT, MAX_WEIGHT) for k in keys}
    total = sum(w.values())
    if total <= 0:
        base = 100 // len(keys)
        ints = {k: base for k in keys}
    else:
        scaled = {k: w[k] * 100.0 / total for k in keys}
        ints = {k: int(round(_clamp(scaled[k], MIN_WEIGHT, MAX_WEIGHT))) for k in keys}
    diff = 100 - sum(ints.values())
    i = 0
    guard = 0
    while diff != 0 and guard < 10_000:
        k = keys[i % len(keys)]
        if diff > 0 and ints[k] < MAX_WEIGHT:
            ints[k] += 1
            diff -= 1
        elif diff < 0 and ints[k] > MIN_WEIGHT:
            ints[k] -= 1
            diff += 1
        i += 1
        guard += 1
    return ints


def recommend_strategy_weights(
    performance_report: Any,
    *,
    current_weights: dict[str, int] | None = None,
    lookback_count: int = 100,
    risk_profile: str = "BALANCED",
    market_regime: str = "ALL",
    now: datetime | None = None,
) -> WeightRecommendation:
    """성과 리포트 → 가중치 추천 후보 (deterministic, 예외 0).

    **자동 적용 금지** — 본 함수는 *후보* 만 산출하며 Agent 설정을 변경하지 않는다.
    실제 계좌 잔고가 아니라 episode 추정 성과(P-28) 만 사용.
    """
    current = {k.upper(): int(v) for k, v in (current_weights or DEFAULT_CURRENT_WEIGHTS).items()}
    # 누락 전략은 default 로 보강.
    for s in SINGLE_STRATEGIES:
        current.setdefault(s, DEFAULT_CURRENT_WEIGHTS.get(s, 25))
    profile = str(risk_profile or "BALANCED").upper()
    regime = str(market_regime or "ALL").upper()
    max_delta = _MAX_DELTA_BY_PROFILE.get(profile, 8)

    blocks = _strategy_block_map(performance_report)
    total_eval = 0
    if hasattr(performance_report, "evaluated_episodes"):
        total_eval = int(performance_report.evaluated_episodes)
    elif isinstance(performance_report, dict):
        total_eval = int(performance_report.get("evaluated_episodes", 0) or 0)

    created_at = (now or _now())
    rid_seed = f"{lookback_count}|{profile}|{regime}|{sorted(current.items())}"
    rid = "wr-" + hashlib.sha1(rid_seed.encode("utf-8")).hexdigest()[:12]  # noqa: S324
    created_iso = created_at.isoformat()

    # 표본 부족 → 추천 보류(현재 가중치 유지).
    if total_eval < MIN_TOTAL_EVALUATED:
        actions = []
        for s in SINGLE_STRATEGIES:
            actions.append({
                "strategy": s, "action": ACTION_NEEDS_MORE_DATA, "score": 0.0,
                "current_weight": current[s], "recommended_weight": current[s],
                "delta": 0, "sample_count": int((blocks.get(s) or {}).get("evaluated_count", 0)),
                "reasons": ["평가 표본 부족 — 추천 보류"],
            })
        return WeightRecommendation(
            recommendation_id=rid, created_at=created_iso,
            status=STATUS_NEEDS_MORE_DATA, lookback_count=int(lookback_count),
            risk_profile=profile, market_regime=regime,
            current_weights=dict(current), recommended_weights=dict(current),
            deltas={s: 0 for s in SINGLE_STRATEGIES}, strategy_actions=actions,
            expected_effect="표본이 부족하여 가중치 변경을 권하지 않습니다.",
            warning="BLOCKED_BY_INSUFFICIENT_SAMPLE — 평가 episode 가 부족합니다.",
            note=f"평가 episode {total_eval} < 최소 {MIN_TOTAL_EVALUATED}건.",
        )

    # 전략별 fitness → 잠정 delta.
    raw_targets: dict[str, float] = {}
    action_meta: dict[str, dict[str, Any]] = {}
    for s in SINGLE_STRATEGIES:
        block = blocks.get(s) or {}
        sample = int(block.get("evaluated_count", 0) or 0)
        if sample < MIN_STRATEGY_SAMPLE:
            raw_targets[s] = float(current[s])
            action_meta[s] = {
                "action": ACTION_NEEDS_MORE_DATA, "score": 0.0, "delta": 0,
                "sample_count": sample,
                "reasons": [f"평가 표본 {sample}건 < {MIN_STRATEGY_SAMPLE}건 — 변경 보류"],
            }
            continue
        fitness, reasons = _fitness(block)
        cap = min(max_delta, _SMALL_SAMPLE_MAX_DELTA) if sample < MIN_STRATEGY_SAMPLE * 2 else max_delta
        delta = int(round(fitness * cap))
        delta = int(_clamp(delta, -max_delta, max_delta))
        if delta > 0:
            action = ACTION_INCREASE
        elif delta < 0:
            action = ACTION_DECREASE
        else:
            action = ACTION_KEEP
        raw_targets[s] = float(current[s] + delta)
        action_meta[s] = {"action": action, "score": round(fitness, 4),
                          "delta": delta, "sample_count": sample, "reasons": reasons}

    recommended = _normalize_to_100(raw_targets)
    deltas = {s: int(recommended[s] - current[s]) for s in SINGLE_STRATEGIES}

    strategy_actions = []
    for s in SINGLE_STRATEGIES:
        meta = action_meta[s]
        strategy_actions.append({
            "strategy": s, "action": meta["action"], "score": meta["score"],
            "current_weight": current[s], "recommended_weight": recommended[s],
            "delta": deltas[s], "sample_count": meta["sample_count"],
            "reasons": meta["reasons"],
        })

    # Agent Council vs 단일 전략 warning carry.
    warning = None
    if hasattr(performance_report, "agent_vs_single"):
        warning = (performance_report.agent_vs_single or {}).get("warning")
    elif isinstance(performance_report, dict):
        warning = (performance_report.get("agent_vs_single") or {}).get("warning")

    inc = [s for s in SINGLE_STRATEGIES if deltas[s] > 0]
    dec = [s for s in SINGLE_STRATEGIES if deltas[s] < 0]
    parts = []
    if inc:
        parts.append("비중 상향 후보: " + ", ".join(inc))
    if dec:
        parts.append("비중 하향 후보: " + ", ".join(dec))
    expected = ("; ".join(parts) + " — 성과가 좋은 전략 비중을 높이고 부진한 전략을 "
                "낮추면 기대 profit factor 개선 가능(추정)." if parts
                else "현재 가중치 유지를 권합니다(뚜렷한 우열 없음).")

    return WeightRecommendation(
        recommendation_id=rid, created_at=created_iso,
        status=STATUS_RECOMMENDATION_ONLY, lookback_count=int(lookback_count),
        risk_profile=profile, market_regime=regime,
        current_weights=dict(current), recommended_weights=dict(recommended),
        deltas=deltas, strategy_actions=strategy_actions,
        expected_effect=expected, warning=warning,
        note="추천 후보일 뿐 자동 적용되지 않습니다 — 운영자 승인 + 별도 PR 필요.",
    )


__all__ = [
    "WeightRecommendation", "recommend_strategy_weights",
    "DEFAULT_CURRENT_WEIGHTS", "SINGLE_STRATEGIES",
    "MIN_WEIGHT", "MAX_WEIGHT", "MIN_TOTAL_EVALUATED", "MIN_STRATEGY_SAMPLE",
    "ACTION_INCREASE", "ACTION_DECREASE", "ACTION_KEEP", "ACTION_NEEDS_MORE_DATA",
    "STATUS_RECOMMENDATION_ONLY", "STATUS_NEEDS_MORE_DATA",
]
