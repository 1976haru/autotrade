"""Agent Council — 4 매매기법 투표 → 단일 BUY/SELL/HOLD 결정 (advisory).

ORB Breakout / Momentum / Gap Trading / VWAP 4 전략을 각 종목에 대해 평가해
`StrategyVote` 를 만들고, 가중 투표 + MarketRegime + RiskOfficer + ChiefTrading
역할을 거쳐 `AgentCouncilDecision` 을 산출한다. risk_profile(보수적/안정적/공격적)
에 따라 진입 임계가 조정된다.

**본 모듈은 주문을 *생성하거나 전송하지 않는다*** — broker / OrderExecutor /
route_order / kis_paper.auto_executor 를 *top-level import 하지 않으며* (정적
grep 가드), 결정은 advisory. KIS Paper Auto 로의 변환은 `to_kis_paper_decision`
이 lazy import 로 *데이터 객체* 만 만들고, 실제 주문은 caller(KIS Paper Auto
Executor)가 sanctioned route_order 경로로 위임한다.

invariant (테스트로 lock):
- `AgentCouncilDecision.is_order_signal=False` / `is_live_authorization=False` /
  `auto_apply_allowed=False` 영구.
- BUY 는 exit_plan 이 있어야만 가능 (없으면 HOLD).
- risk_flags 가 risk_profile 허용치 초과면 BUY 차단(HOLD).
- AGGRESSIVE 도 실거래 권한 0 — 자금/중복/한도/KIS Gate 우회 불가(별도 단계).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.agents.risk_profile import DEFAULT_RISK_PROFILE, RiskProfile, policy_for


class CouncilAction(StrEnum):
    BUY  = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


# 전략 ID + 기본 가중치 (사용자 요청서 §2-6).
STRATEGY_WEIGHTS: dict[str, int] = {
    "MOMENTUM": 30,
    "VWAP":     25,
    "ORB":      25,
    "GAP":      20,
}

# risk_profile 별 council 임계 (진입 게이트). min_confidence 는 risk_profile
# policy 의 min_confidence_threshold 와 정합, quality/risk_flags 는 council 정의.
_PROFILE_THRESHOLDS: dict[RiskProfile, dict[str, float]] = {
    RiskProfile.CONSERVATIVE: {"min_confidence": 0.70, "min_quality": 75, "max_risk_flags": 0},
    RiskProfile.BALANCED:     {"min_confidence": 0.55, "min_quality": 60, "max_risk_flags": 1},
    RiskProfile.AGGRESSIVE:   {"min_confidence": 0.45, "min_quality": 50, "max_risk_flags": 2},
}


# ─────────────────────────────────────────────────────────────────────────────
# 입력 / 출력 DTO
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StrategyMarketInput:
    """전략 평가 입력 — caller(파이프라인/시세 어댑터)가 채운다.

    데이터가 없으면 해당 전략은 HOLD + reason_code 반환 (silent 실패 0건).
    """

    symbol:              str
    current_price:       float | None = None
    prev_close:          float | None = None
    open_price:          float | None = None
    vwap:                float | None = None
    opening_range_high:  float | None = None
    opening_range_low:   float | None = None
    recent_closes:       tuple[float, ...] = ()      # 최근 종가 (오름차순, 마지막=최신)
    current_volume:      float | None = None
    avg_volume:          float | None = None
    market_regime:       str = "UNKNOWN"             # TREND_UP/DOWN/SIDEWAYS/HIGH_VOLATILITY/UNKNOWN
    regime_decision:     str = "ALLOW"               # ALLOW/REDUCE_SIZE/WATCH_ONLY/BLOCK_NEW_BUY


@dataclass(frozen=True)
class StrategyVote:
    """단일 전략 평가 결과 — advisory."""

    strategy:    str
    signal:      CouncilAction
    confidence:  float                 # 0~1
    score:       int                   # 0~100
    reason:      str
    risk_flags:  list[str] = field(default_factory=list)
    reason_code: str = ""

    def to_dict(self) -> dict[str, Any]:
        # P-23: 전략별 가중치 + 가중점수 carry (전략 성과 분석용).
        weight = STRATEGY_WEIGHTS.get(self.strategy, 0)
        weighted_score = round(weight * (int(self.score) / 100.0), 4)
        return {
            "strategy":       self.strategy,
            "signal":         self.signal.value,
            "confidence":     round(float(self.confidence), 4),
            "score":          int(self.score),
            "reason":         self.reason,
            "risk_flags":     list(self.risk_flags),
            "reason_code":    self.reason_code,
            "weight":         int(weight),
            "weighted_score": weighted_score,
        }


# P-23: 4 전략 canonical 순서 (episode votes 누락 0건 보장 기준).
STRATEGY_ORDER: tuple[str, ...] = ("ORB", "MOMENTUM", "GAP", "VWAP")


def placeholder_strategy_votes() -> list[dict[str, Any]]:
    """전략 평가가 아예 불가능한 경우(후보/시세 없음) 4 전략 placeholder vote.

    episode.votes 에 ORB/MOMENTUM/GAP/VWAP 4개가 *항상* 존재하도록 보장한다.
    실제 평가가 수행되면 caller 가 council.votes 의 to_dict() 를 사용한다.
    """
    out: list[dict[str, Any]] = []
    for strat in STRATEGY_ORDER:
        out.append({
            "strategy":       strat,
            "signal":         CouncilAction.HOLD.value,
            "confidence":     0.0,
            "score":          0,
            "reason":         "전략 평가에 필요한 데이터가 부족합니다.",
            "risk_flags":     ["DATA_UNAVAILABLE"],
            "reason_code":    "STRATEGY_DATA_UNAVAILABLE",
            "weight":         int(STRATEGY_WEIGHTS.get(strat, 0)),
            "weighted_score": 0.0,
        })
    return out


@dataclass(frozen=True)
class AgentCouncilDecision:
    """Agent Council 최종 결정 — advisory. broker 호출 0건."""

    symbol:         str
    final_action:   CouncilAction
    confidence:     float              # 0~1
    quality_score:  int                # 0~100
    selected_strategies: list[str]
    votes:          list[StrategyVote]
    reason:         str
    risk_flags:     list[str]
    risk_profile:   str
    market_regime:  str
    buy_score:      float
    sell_score:     float
    hold_score:     float
    has_exit_plan:  bool
    exit_plan:      dict[str, Any]
    metadata:       dict[str, Any] = field(default_factory=dict)
    # P-26: 매도(SELL) 사유 — final_action=SELL 일 때만 채워짐 (그 외 빈 dict).
    sell_reason:    dict[str, Any] = field(default_factory=dict)
    # 2-08: RiskOfficer veto 결과 (risk_flags 허용치 초과 시 HOLD 강등 정보).
    risk_veto_result: dict[str, Any] = field(default_factory=dict)

    is_order_signal:       bool = False
    auto_apply_allowed:    bool = False
    is_live_authorization: bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("AgentCouncilDecision.is_order_signal must be False")
        if self.auto_apply_allowed is not False:
            raise ValueError("AgentCouncilDecision.auto_apply_allowed must be False")
        if self.is_live_authorization is not False:
            raise ValueError("AgentCouncilDecision.is_live_authorization must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol":         self.symbol,
            "final_action":   self.final_action.value,
            "confidence":     round(float(self.confidence), 4),
            "quality_score":  int(self.quality_score),
            "selected_strategies": list(self.selected_strategies),
            "votes":          [v.to_dict() for v in self.votes],
            "reason":         self.reason,
            "risk_flags":     list(self.risk_flags),
            "risk_profile":   self.risk_profile,
            "market_regime":  self.market_regime,
            "buy_score":      round(float(self.buy_score), 2),
            "sell_score":     round(float(self.sell_score), 2),
            "hold_score":     round(float(self.hold_score), 2),
            "has_exit_plan":  bool(self.has_exit_plan),
            "exit_plan":      dict(self.exit_plan),
            "metadata":       dict(self.metadata),
            # P-26: 매도 사유 (SELL 일 때만 비어있지 않음).
            "sell_reason":    dict(self.sell_reason),
            # 2-08: RiskOfficer veto 결과 (위험 플래그 초과 → HOLD 강등).
            "risk_veto_result": dict(self.risk_veto_result),
            # P-23: 진입 임계 스냅샷 — "왜 BUY/왜 HOLD" 사후 분석용.
            "threshold_snapshot": self._threshold_snapshot(),
            "is_order_signal":       False,
            "auto_apply_allowed":    False,
            "is_live_authorization": False,
            "advisory_disclaimer": (
                "Agent Council 판단은 advisory — 주문 신호가 아니며 실거래 권한이 "
                "아닙니다. 실제 주문은 RiskManager / PermissionGate / KIS Paper "
                "Gate 를 모두 통과해야 합니다."
            ),
        }

    def _threshold_snapshot(self) -> dict[str, Any]:
        """진입 게이트 임계 스냅샷 (metadata.thresholds 기반)."""
        thr = self.metadata.get("thresholds", {}) if isinstance(self.metadata, dict) else {}
        return {
            "risk_profile":      self.risk_profile,
            "min_confidence":    thr.get("min_confidence"),
            "min_quality_score": thr.get("min_quality"),
            "max_risk_flags":    thr.get("max_risk_flags"),
        }

    def to_kis_paper_decision(self, *, quantity: int, price: int):
        """final_action 이 BUY/SELL 일 때만 KisPaperAutoDecision 생성. HOLD → None.

        lazy import — agent_council 은 kis_paper.auto_executor 를 top-level import
        하지 않는다 (정적 가드). 본 메서드는 *데이터 객체* 만 만들며 주문 전송 0건.
        """
        if self.final_action == CouncilAction.HOLD:
            return None
        from app.kis_paper.auto_executor import KisPaperAutoDecision
        return KisPaperAutoDecision(
            symbol=self.symbol, side=self.final_action.value,
            quantity=int(quantity), price=int(price),
            selected_strategies=list(self.selected_strategies),
            confidence=float(self.confidence), quality_score=int(self.quality_score),
            entry_reason=self.reason, has_exit_plan=bool(self.has_exit_plan),
            exit_plan=dict(self.exit_plan),
        )


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _score_from_magnitude(mag: float, *, full_at: float) -> int:
    """변동 크기 → 0~100 점수 (full_at 에서 100)."""
    if full_at <= 0:
        return 0
    return int(round(_clamp01(abs(mag) / full_at) * 100))


def _volume_risk_flags(inp: StrategyMarketInput) -> list[str]:
    flags: list[str] = []
    if (inp.current_volume is not None and inp.avg_volume
            and inp.avg_volume > 0 and inp.current_volume < inp.avg_volume * 0.5):
        flags.append("low_volume")
    if inp.market_regime == "HIGH_VOLATILITY":
        flags.append("high_volatility")
    return flags


# ─────────────────────────────────────────────────────────────────────────────
# 4 전략 evaluator (deterministic)
# ─────────────────────────────────────────────────────────────────────────────


def evaluate_orb(inp: StrategyMarketInput) -> StrategyVote:
    """ORB Breakout — opening range 돌파."""
    if (inp.current_price is None or inp.opening_range_high is None
            or inp.opening_range_low is None or inp.opening_range_high <= 0):
        return StrategyVote("ORB", CouncilAction.HOLD, 0.0, 0,
                            "ORB 데이터 부족 (opening range / 현재가 없음)",
                            reason_code="INSUFFICIENT_ORB_DATA")
    rng = max(inp.opening_range_high - inp.opening_range_low, 1e-9)
    if inp.current_price > inp.opening_range_high:
        mag = (inp.current_price - inp.opening_range_high) / rng
        sc = _score_from_magnitude(mag, full_at=0.5)
        return StrategyVote("ORB", CouncilAction.BUY, _clamp01(0.5 + mag), sc,
                            f"opening range 상단 돌파 (+{mag*100:.1f}% of range)",
                            risk_flags=_volume_risk_flags(inp))
    if inp.current_price < inp.opening_range_low:
        mag = (inp.opening_range_low - inp.current_price) / rng
        sc = _score_from_magnitude(mag, full_at=0.5)
        return StrategyVote("ORB", CouncilAction.SELL, _clamp01(0.5 + mag), sc,
                            f"opening range 하단 이탈 (-{mag*100:.1f}% of range)",
                            risk_flags=_volume_risk_flags(inp))
    return StrategyVote("ORB", CouncilAction.HOLD, 0.3, 30,
                        "opening range 내 — 돌파 미발생", reason_code="ORB_IN_RANGE")


def evaluate_momentum(inp: StrategyMarketInput) -> StrategyVote:
    """Momentum — 최근 종가 추세."""
    closes = inp.recent_closes
    if len(closes) < 3:
        return StrategyVote("MOMENTUM", CouncilAction.HOLD, 0.0, 0,
                            "Momentum 데이터 부족 (최근 종가 3개 미만)",
                            reason_code="INSUFFICIENT_MOMENTUM_DATA")
    base = closes[0]
    if base <= 0:
        return StrategyVote("MOMENTUM", CouncilAction.HOLD, 0.0, 0,
                            "Momentum 기준가 비정상", reason_code="INVALID_MOMENTUM_BASE")
    ret = (closes[-1] - base) / base
    sc = _score_from_magnitude(ret, full_at=0.05)   # 5% 변동에서 100점
    if ret > 0.01:
        return StrategyVote("MOMENTUM", CouncilAction.BUY, _clamp01(0.5 + ret * 5), sc,
                            f"단기 상승 모멘텀 +{ret*100:.1f}%",
                            risk_flags=_volume_risk_flags(inp))
    if ret < -0.01:
        return StrategyVote("MOMENTUM", CouncilAction.SELL, _clamp01(0.5 + abs(ret) * 5), sc,
                            f"단기 하락 모멘텀 {ret*100:.1f}%",
                            risk_flags=_volume_risk_flags(inp))
    return StrategyVote("MOMENTUM", CouncilAction.HOLD, 0.3, 30,
                        "모멘텀 미약 (±1% 이내)", reason_code="MOMENTUM_FLAT")


def evaluate_gap(inp: StrategyMarketInput) -> StrategyVote:
    """Gap Trading — 전일 종가 대비 갭 + gap-and-go."""
    if (inp.prev_close is None or inp.open_price is None or inp.prev_close <= 0):
        return StrategyVote("GAP", CouncilAction.HOLD, 0.0, 0,
                            "Gap 데이터 부족 (전일 종가 / 시가 없음)",
                            reason_code="INSUFFICIENT_GAP_DATA")
    gap = (inp.open_price - inp.prev_close) / inp.prev_close
    sc = _score_from_magnitude(gap, full_at=0.05)
    cur = inp.current_price if inp.current_price is not None else inp.open_price
    if gap > 0.02 and cur >= inp.open_price:
        return StrategyVote("GAP", CouncilAction.BUY, _clamp01(0.5 + gap * 5), sc,
                            f"갭상승 +{gap*100:.1f}% gap-and-go",
                            risk_flags=_volume_risk_flags(inp))
    if gap < -0.02:
        return StrategyVote("GAP", CouncilAction.SELL, _clamp01(0.5 + abs(gap) * 5), sc,
                            f"갭하락 {gap*100:.1f}%",
                            risk_flags=_volume_risk_flags(inp))
    return StrategyVote("GAP", CouncilAction.HOLD, 0.3, 30,
                        f"갭 미미 ({gap*100:.1f}%)", reason_code="GAP_SMALL")


def evaluate_vwap(inp: StrategyMarketInput) -> StrategyVote:
    """VWAP — 평균거래가격 대비 위치."""
    if inp.vwap is None or inp.vwap <= 0 or inp.current_price is None:
        return StrategyVote("VWAP", CouncilAction.HOLD, 0.0, 0,
                            "VWAP 데이터 부족", reason_code="INSUFFICIENT_VWAP_DATA")
    dev = (inp.current_price - inp.vwap) / inp.vwap
    sc = _score_from_magnitude(dev, full_at=0.03)
    if dev > 0.003:
        return StrategyVote("VWAP", CouncilAction.BUY, _clamp01(0.5 + dev * 8), sc,
                            f"VWAP 상회 +{dev*100:.2f}%",
                            risk_flags=_volume_risk_flags(inp))
    if dev < -0.003:
        return StrategyVote("VWAP", CouncilAction.SELL, _clamp01(0.5 + abs(dev) * 8), sc,
                            f"VWAP 하향 이탈 {dev*100:.2f}%",
                            risk_flags=_volume_risk_flags(inp))
    return StrategyVote("VWAP", CouncilAction.HOLD, 0.3, 30,
                        "VWAP 근접 — 방향성 약함", reason_code="VWAP_NEUTRAL")


_EVALUATORS = {
    "ORB": evaluate_orb, "MOMENTUM": evaluate_momentum,
    "GAP": evaluate_gap, "VWAP": evaluate_vwap,
}


def evaluate_all_strategies(inp: StrategyMarketInput) -> list[StrategyVote]:
    """4 전략 모두 평가 — 고정 순서(MOMENTUM/VWAP/ORB/GAP, 가중치 순)."""
    return [_EVALUATORS[s](inp) for s in ("MOMENTUM", "VWAP", "ORB", "GAP")]


# ─────────────────────────────────────────────────────────────────────────────
# Agent Council 결합
# ─────────────────────────────────────────────────────────────────────────────


def _exit_plan_for(profile: RiskProfile) -> dict[str, Any]:
    pol = policy_for(profile)
    sl = round(pol.default_stop_loss_pct * 100, 2)      # % 표기
    return {"stop_loss_pct": sl, "take_profit_pct": round(sl * 2, 2),
            "trailing_stop": False}


def run_agent_council(
    inp: StrategyMarketInput,
    *,
    risk_profile: RiskProfile | str | None = None,
    held_position: bool = False,
) -> AgentCouncilDecision:
    """4 전략 투표 → MarketRegime → RiskOfficer → ChiefTrading → 최종 결정.

    held_position=True 면 SELL 판단(청산)을 허용 — 보유 없으면 SELL 은 HOLD 로
    강등(naked SELL 방지). broker 호출 0건.
    """
    profile = policy_for(risk_profile).profile
    thr = _PROFILE_THRESHOLDS[profile]
    votes = evaluate_all_strategies(inp)

    # 1. 가중 투표 집계.
    buy_score = sell_score = hold_score = 0.0
    for v in votes:
        w = STRATEGY_WEIGHTS.get(v.strategy, 0)
        contrib = w * (v.score / 100.0)
        if v.signal == CouncilAction.BUY:
            buy_score += contrib
        elif v.signal == CouncilAction.SELL:
            sell_score += contrib
        else:
            hold_score += contrib

    # 2. MarketRegime — BLOCK_NEW_BUY / WATCH_ONLY → BUY 억제.
    regime_block_buy = inp.regime_decision in ("BLOCK_NEW_BUY", "WATCH_ONLY") \
        or inp.market_regime == "TREND_DOWN"

    # 3. RiskOfficer — risk_flags 집계 + quality 감점.
    all_flags: list[str] = []
    for v in votes:
        all_flags.extend(v.risk_flags)
    uniq_flags = sorted(set(all_flags))
    risk_penalty = min(len(uniq_flags) * 10, 40)

    # 4. 잠정 action — 최고 점수 bucket.
    top = max(
        (CouncilAction.BUY, buy_score),
        (CouncilAction.SELL, sell_score),
        (CouncilAction.HOLD, hold_score),
        key=lambda kv: kv[1],
    )[0]

    supporting = [v for v in votes if v.signal == top and v.score > 0]
    if supporting:
        avg_conf = sum(v.confidence for v in supporting) / len(supporting)
        avg_score = sum(v.score for v in supporting) / len(supporting)
    else:
        avg_conf, avg_score = 0.0, 0.0
    quality = max(0, int(round(avg_score)) - risk_penalty)
    confidence = _clamp01(avg_conf)

    exit_plan = _exit_plan_for(profile)
    reasons: list[str] = []

    # 5. ChiefTrading — 게이트 적용.
    final = top
    if top == CouncilAction.BUY:
        if regime_block_buy:
            final = CouncilAction.HOLD
            reasons.append(f"장세({inp.market_regime}/{inp.regime_decision})로 신규 BUY 억제")
        elif confidence < thr["min_confidence"]:
            final = CouncilAction.HOLD
            reasons.append(f"confidence {confidence:.2f} < 임계 {thr['min_confidence']:.2f}")
        elif quality < thr["min_quality"]:
            final = CouncilAction.HOLD
            reasons.append(f"quality_score {quality} < 임계 {int(thr['min_quality'])}")
        elif not exit_plan:
            final = CouncilAction.HOLD
            reasons.append("exit_plan 없음 — BUY 금지")
        else:
            reasons.append(
                f"BUY 채택 (buy_score={buy_score:.1f}, conf={confidence:.2f}, "
                f"quality={quality}, 전략={[v.strategy for v in supporting]})")
    elif top == CouncilAction.SELL:
        if not held_position:
            final = CouncilAction.HOLD
            reasons.append("보유 포지션 없음 — SELL 미적용(HOLD)")
        else:
            reasons.append(
                f"SELL 채택 (sell_score={sell_score:.1f}, 전략={[v.strategy for v in supporting]})")
    else:
        reasons.append("전략 신호 약함 / 중립 — HOLD")

    # 6. RiskOfficer veto — risk_flags 허용치 초과 시 BUY/SELL → HOLD 강등.
    #    AGGRESSIVE 도 무제한 진입 불가. RiskManager/PermissionGate 대체 아님(사전 필터).
    from app.agents.risk_officer import evaluate_risk_officer_veto
    veto = evaluate_risk_officer_veto(
        action=final.value, risk_flags=uniq_flags, risk_profile=profile.value,
        max_risk_flags=int(thr["max_risk_flags"]),
    )
    if veto.veto_applied:
        final = CouncilAction.HOLD
        reasons.append(veto.reason)

    selected = [v.strategy for v in votes if v.signal == final and v.score > 0] \
        if final != CouncilAction.HOLD else []

    # P-26: SELL 이면 매도 사유를 표준 reason_code 로 산출 (UNKNOWN 금지).
    sell_reason: dict[str, Any] = {}
    if final == CouncilAction.SELL:
        from app.agents.sell_reason import infer_sell_reason
        sell_reason = infer_sell_reason(
            votes=[v.to_dict() for v in votes],
            selected_strategies=selected,
            market_snapshot={"price": inp.current_price, "vwap": inp.vwap},
            exit_plan=exit_plan,
        ).to_dict()

    return AgentCouncilDecision(
        symbol=inp.symbol, final_action=final,
        confidence=confidence if final != CouncilAction.HOLD else _clamp01(avg_conf),
        quality_score=quality, selected_strategies=selected, votes=votes,
        reason="; ".join(reasons), risk_flags=uniq_flags,
        risk_profile=profile.value, market_regime=inp.market_regime,
        buy_score=buy_score, sell_score=sell_score, hold_score=hold_score,
        has_exit_plan=bool(exit_plan) and final == CouncilAction.BUY,
        exit_plan=(exit_plan if final == CouncilAction.BUY else {}),
        sell_reason=sell_reason,
        risk_veto_result=veto.to_dict(),
        metadata={"weights": dict(STRATEGY_WEIGHTS),
                  "thresholds": dict(thr),
                  "risk_penalty": risk_penalty,
                  "held_position": bool(held_position)},
    )


__all__ = [
    "CouncilAction", "STRATEGY_WEIGHTS",
    "StrategyMarketInput", "StrategyVote", "AgentCouncilDecision",
    "evaluate_orb", "evaluate_momentum", "evaluate_gap", "evaluate_vwap",
    "evaluate_all_strategies", "run_agent_council",
    "DEFAULT_RISK_PROFILE",
]
