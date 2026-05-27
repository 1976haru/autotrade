"""Composite signal ranking — 먼저 온 신호가 아니라 *품질 높은* 신호가 슬롯 차지.

동시에 여러 BUY/SELL 후보가 발생할 때, earliest-first(선착순) 대신 composite score
상위 신호만 제한된 슬롯에 진입시킨다. 백테스트 전용 *순수 함수* — broker 주문 메서드 /
단일 주문 라우터 / 주문 실행기 / KIS API import 0건, 실주문 0건. 결과는 주문 신호가
아니라 *백테스트 슬롯 배정* — is_order_signal/auto_apply_allowed/is_live_authorization
항상 False.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence


@dataclass(frozen=True)
class RankingWeights:
    net_edge: float = 0.30
    confidence: float = 0.20
    quality: float = 0.20
    volume: float = 0.10
    liquidity: float = 0.10
    regime: float = 0.10


@dataclass(frozen=True)
class SignalCandidate:
    symbol: str
    strategy: str = ""
    timestamp: str | None = None
    side: str = "BUY"
    confidence: float = 0.0          # 0~1
    quality_score: float = 0.0       # 0~100
    expected_move_bps: float = 0.0
    estimated_cost_bps: float = 0.0
    volume_expansion: float = 1.0    # 1.0 = 평균
    volatility: float = 0.0          # bps proxy
    spread_bps: float = 0.0
    regime_label: str = "UNKNOWN"
    recent_drawdown: float = 0.0     # 0~1
    recent_consecutive_losses: int = 0
    symbol_group: str = ""
    liquidity_score: float = 50.0    # 0~100
    agent_risk_veto: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "strategy": self.strategy,
            "timestamp": self.timestamp, "side": self.side,
            "confidence": self.confidence, "quality_score": self.quality_score,
            "expected_move_bps": self.expected_move_bps,
            "estimated_cost_bps": self.estimated_cost_bps,
            "net_edge_bps": self.expected_move_bps - self.estimated_cost_bps,
            "agent_risk_veto": self.agent_risk_veto,
            "symbol_group": self.symbol_group,
        }


# regime 적합도 (BUY 기준 상승/횡보 선호).
_REGIME_FIT = {
    "TREND_UP": 100.0, "SIDEWAYS": 60.0, "UNKNOWN": 50.0,
    "HIGH_VOLATILITY": 40.0, "TREND_DOWN": 20.0,
}


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def compute_composite_score(c: SignalCandidate,
                            weights: RankingWeights | None = None) -> float:
    """0~100 composite score. agent_risk_veto → 0 (제외 후보)."""
    if c.agent_risk_veto:
        return 0.0
    w = weights or RankingWeights()

    net_edge = c.expected_move_bps - c.estimated_cost_bps
    # net_edge 30bps → 100점 스케일 (음수면 0).
    net_edge_score = _clamp(net_edge / 30.0 * 100.0)
    conf_score = _clamp(c.confidence * 100.0)
    quality = _clamp(c.quality_score)
    vol_score = _clamp((c.volume_expansion - 1.0) / 2.0 * 100.0)  # 3x → 100
    liquidity = _clamp(c.liquidity_score)
    regime = _REGIME_FIT.get(str(c.regime_label).upper(), 50.0)

    base = (w.net_edge * net_edge_score + w.confidence * conf_score
            + w.quality * quality + w.volume * vol_score
            + w.liquidity * liquidity + w.regime * regime)

    # 페널티 (감점).
    penalty = 0.0
    penalty += min(20.0, c.spread_bps * 0.5)              # 넓은 스프레드
    penalty += min(15.0, max(0.0, c.volatility - 50.0) * 0.2)  # 과도한 변동성
    penalty += min(20.0, c.recent_drawdown * 100.0 * 0.3)  # 최근 드로다운
    penalty += min(15.0, c.recent_consecutive_losses * 3.0)  # 연속손실

    return round(_clamp(base - penalty), 2)


@dataclass(frozen=True)
class RankingResult:
    selected: list[dict[str, Any]]
    rejected: list[dict[str, Any]]
    available_slots: int
    selected_avg_score: float
    rejected_avg_score: float
    vetoed_count: int
    group_capped_count: int
    is_order_signal: bool = False
    auto_apply_allowed: bool = False
    is_live_authorization: bool = False

    def __post_init__(self) -> None:
        for flag in ("is_order_signal", "auto_apply_allowed", "is_live_authorization"):
            if getattr(self, flag) is not False:
                raise ValueError(f"RankingResult.{flag} must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected": self.selected, "rejected": self.rejected,
            "available_slots": self.available_slots,
            "selected_avg_score": round(self.selected_avg_score, 2),
            "rejected_avg_score": round(self.rejected_avg_score, 2),
            "vetoed_count": self.vetoed_count,
            "group_capped_count": self.group_capped_count,
            "is_order_signal": False, "auto_apply_allowed": False,
            "is_live_authorization": False,
        }


def _avg(scores: Sequence[float]) -> float:
    return round(sum(scores) / len(scores), 2) if scores else 0.0


def rank_signals(
    candidates: Sequence[SignalCandidate],
    *,
    max_slots: int = 5,
    position_count: int = 0,
    max_symbols_per_group: int | None = None,
    weights: RankingWeights | None = None,
    min_score: float = 0.0,
) -> RankingResult:
    """composite score 상위 신호를 잔여 슬롯에 배정. veto/그룹캡/최소점수 필터."""
    available = max(0, int(max_slots) - int(position_count))
    scored = []
    vetoed = 0
    for c in candidates:
        sc = compute_composite_score(c, weights)
        d = c.to_dict()
        d["composite_score"] = sc
        if c.agent_risk_veto:
            vetoed += 1
            d["reject_reason"] = "AGENT_RISK_VETO"
        scored.append((sc, c, d))

    # score 내림차순 (동점이면 net_edge → timestamp 안정 정렬).
    scored.sort(key=lambda t: (t[0], t[2]["net_edge_bps"]), reverse=True)

    selected: list[dict] = []
    rejected: list[dict] = []
    group_count: dict[str, int] = {}
    group_capped = 0

    for sc, c, d in scored:
        if c.agent_risk_veto or sc < min_score:
            d.setdefault("reject_reason", "BELOW_MIN_SCORE")
            rejected.append(d)
            continue
        if len(selected) >= available:
            d["reject_reason"] = "NO_SLOT"
            rejected.append(d)
            continue
        if max_symbols_per_group and c.symbol_group:
            if group_count.get(c.symbol_group, 0) >= max_symbols_per_group:
                d["reject_reason"] = "GROUP_CAP"
                group_capped += 1
                rejected.append(d)
                continue
            group_count[c.symbol_group] = group_count.get(c.symbol_group, 0) + 1
        d["selected"] = True
        selected.append(d)

    return RankingResult(
        selected=selected, rejected=rejected, available_slots=available,
        selected_avg_score=_avg([d["composite_score"] for d in selected]),
        rejected_avg_score=_avg([d["composite_score"] for d in rejected]),
        vetoed_count=vetoed, group_capped_count=group_capped,
    )


def earliest_first(candidates: Sequence[SignalCandidate], *, max_slots: int = 5,
                   position_count: int = 0) -> list[dict[str, Any]]:
    """비교용 baseline — timestamp 선착순으로 슬롯 배정 (품질 무시)."""
    available = max(0, int(max_slots) - int(position_count))

    def _ts(c: SignalCandidate) -> datetime:
        try:
            dt = datetime.fromisoformat(str(c.timestamp))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            return datetime.max.replace(tzinfo=timezone.utc)

    ordered = sorted([c for c in candidates if not c.agent_risk_veto], key=_ts)
    out = []
    for c in ordered[:available]:
        d = c.to_dict()
        d["composite_score"] = compute_composite_score(c)
        d["selected"] = True
        out.append(d)
    return out
