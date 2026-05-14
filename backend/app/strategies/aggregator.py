"""Strategy Signal Aggregator.

4개 단타 전략(VolumeBreakout / PullbackRebreak / VWAPStrategy / ORB+VWAP)의
신호를 *종목 단위* 로 합쳐 하나의 ``AggregatedSignal`` 후보로 통합한다.

**본 모듈은 절대 주문을 만들지 않는다.** 최종 출력은 *주문이 아니라*
``app.ai.assist.AICandidate`` / ``ExecutionProposal`` 로 변환 *가능한* advisory
후보 데이터다. 모든 실 주문은 기존 sanctioned 경로(``route_order`` → RiskManager
→ PermissionGate → OrderExecutor)를 통과해야 한다.

## 통합 규칙 요약 (자세한 정책: ``docs/strategy_signal_aggregator.md``)

1. **같은 방향 합산**: 2개 이상 전략이 같은 ``SignalAction`` 을 내면 confidence
   가 상승한다. supporting count 별 boost 는 ``AggregatorPolicy`` 가 결정.
2. **VWAP EXIT/SELL 우선**: VWAP 계열(``vwap_strategy`` / ``orb_vwap``) 의
   ``EXIT`` / ``SELL`` 신호는 같은 종목의 ``BUY`` 표를 *항상* 압도. 손실 방어가
   진입보다 우선.
3. **RISK_OFF 차단**: ``market_regime == "RISK_OFF"`` 이면 어떤 BUY 도 후보가
   되지 않음. ``final_action`` 은 ``REJECT``.
4. **LOW_LIQUIDITY 강등**: ``market_regime == "LOW_LIQUIDITY"`` 면 BUY → WATCH
   강등 (후보 자격 박탈, 모니터링만).
5. **충돌 처리**: 같은 종목에 BUY 와 SELL/EXIT 이 동시에 있으면
   ``conflict_level`` 상승. HIGH 면 후보 자격(``candidate_qualified``) 박탈.
6. **단일 전략 가드**: 한 전략만 신호를 낸 경우 ``quality_score`` 가
   ``min_quality_score_single_strategy`` 이상이어야 후보 인정. 그 외는 WATCH.
7. **중복 합산**: 같은 ``(strategy_id, symbol)`` 의 vote 는 마지막 1건만 유효
   (오래된 vote 폐기).
8. **장세별 가중치**: ``regime_weights`` 가 ``TREND_UP`` 에서 VolumeBreakout /
   PullbackRebreak 가중, ``CHOPPY`` 에서 VWAP 가중, ``OPENING_CHAOS`` 에서
   ORB cooldown 강제, ``RISK_OFF`` 에서 모든 BUY 차단.

## 핵심 invariant (정적 grep 가드)

- broker / OrderExecutor / route_order / place_order / cancel_order import 0건
- ``AggregatedSignal.is_order_intent = False`` 불변(``__post_init__`` ValueError)
- ``StrategyAggregationResult.is_order_intent = False`` 불변(동일 가드)
- ``to_execution_proposal()`` 는 ``ExecutionProposal`` 의 ``is_order_intent`` /
  ``can_execute_order`` 가드를 그대로 carry — 본 helper 단계에서 우회 0건

## 미구현 영역

- 실시간 시세 호출: caller 가 미리 ``StrategyVote`` 로 변환해 주입.
- DB 영구화: 본 모듈은 stateless pure function. 결과 저장은 caller 책임.
- AI provider 호출: 0건 (anthropic / openai / httpx / requests import 0건).
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Iterable

from app.strategies.base import ExitPlan, SignalAction, SizingHint


# ====================================================================
# Enums
# ====================================================================


class AggregatedAction(StrEnum):
    """통합 후 최종 action. ``SignalAction`` 위에 ``REJECT`` 한 단계 추가 — 운영
    자가 *신호 없음* 과 *안전 차단* 을 명확히 구분 가능. 본 enum 은 결정 *데이터*
    이지 주문 결정이 아니다.
    """
    BUY        = "BUY"
    SELL       = "SELL"
    EXIT       = "EXIT"
    WATCH      = "WATCH"
    REJECT     = "REJECT"
    NO_SIGNAL  = "NO_SIGNAL"


class ConflictLevel(StrEnum):
    NONE   = "NONE"
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"


# 표준 market regime 키 — `app.agents.market_regime.classify_market_regime` 의
# 출력값과 일치. 본 모듈은 enum 으로 강제하지 않고 string match 만 사용.
_REGIME_RISK_OFF       = "RISK_OFF"
_REGIME_OPENING_CHAOS  = "OPENING_CHAOS"
_REGIME_LOW_LIQUIDITY  = "LOW_LIQUIDITY"
_REGIME_TREND_UP       = "TREND_UP"
_REGIME_CHOPPY         = "CHOPPY"

_VWAP_STRATEGY_IDS = ("vwap_strategy", "orb_vwap")


# ====================================================================
# Dataclasses
# ====================================================================


@dataclass(frozen=True)
class StrategyVote:
    """단일 전략이 한 종목에 대해 던지는 *vote*. 본 객체는 주문이 아니다 —
    ``StrategySignal`` 의 advisory 표현.
    """
    strategy_id:    str
    symbol:         str
    action:         SignalAction
    confidence:     int                  # 0~100
    quality_score:  int                  # 0~100
    reasons:        tuple[str, ...]      = ()
    risk_notes:     tuple[str, ...]      = ()
    indicators:     dict                 = field(default_factory=dict)
    sizing_hint:    SizingHint | None    = None
    exit_plan:      ExitPlan | None      = None
    # 이미 stale 검사를 통과했는지 — caller 가 표시. 아니면 합산 시 가중치 0.5.
    is_fresh:       bool                 = True
    # 표시용 — 가장 최근 vote 가 우선. None 이면 합산 시점 사용.
    voted_at:       datetime | None      = None

    def __post_init__(self) -> None:
        if not (0 <= int(self.confidence) <= 100):
            raise ValueError(
                f"StrategyVote.confidence must be 0-100, got {self.confidence}"
            )
        if not (0 <= int(self.quality_score) <= 100):
            raise ValueError(
                f"StrategyVote.quality_score must be 0-100, got {self.quality_score}"
            )
        if not self.strategy_id or not self.symbol:
            raise ValueError(
                "StrategyVote.strategy_id and symbol must be non-empty"
            )


@dataclass(frozen=True)
class SignalConflict:
    """같은 종목 안에서 상반된 vote 두 개. 운영자 / Agent 가 carry 해 audit 에
    기록 가능. 본 객체는 주문이 아니다.
    """
    symbol:      str
    strategy_a:  str
    strategy_b:  str
    action_a:    SignalAction
    action_b:    SignalAction
    severity:    ConflictLevel
    reason:      str


@dataclass(frozen=True)
class AggregatedSignal:
    """4개 전략 vote 를 합친 *최종 advisory 후보*. 주문이 아니다.

    invariant:
    - ``is_order_intent = False`` 불변 (``__post_init__`` ValueError 가드)
    - ``candidate_qualified`` 가 True 라도 *자동으로 주문이 만들어지지 않는다* —
      ``to_execution_proposal()`` 를 거쳐 ``ExecutionProposal`` 로 변환된 후에도
      여전히 ``ExecutionProposal.is_order_intent = False`` 가 유지된다.
    """
    symbol:                 str
    final_action:           AggregatedAction
    confidence:             int                  # 0~100
    quality_score:          int                  # 0~100
    supporting_strategies:  tuple[str, ...]
    opposing_strategies:    tuple[str, ...]
    neutral_strategies:     tuple[str, ...]
    reasons:                tuple[str, ...]
    risk_notes:             tuple[str, ...]
    conflict_level:         ConflictLevel
    recommended_strategy:   str | None           # 후보 자격 시 대표 전략 id
    entry_plan:             str | None           # 사람이 읽을 entry 요약
    exit_plan:              ExitPlan | None      # 가장 보수적 stop_loss 채택
    market_regime:          str | None
    candidate_qualified:    bool                 # approval queue 송출 가능 여부
    is_order_intent:        bool = False         # 항상 False 불변

    def __post_init__(self) -> None:
        if self.is_order_intent is not False:
            raise ValueError(
                "AggregatedSignal.is_order_intent must be False — "
                "본 신호는 주문 객체가 아닙니다."
            )
        if not (0 <= int(self.confidence) <= 100):
            raise ValueError(
                f"AggregatedSignal.confidence must be 0-100, got {self.confidence}"
            )
        if not (0 <= int(self.quality_score) <= 100):
            raise ValueError(
                f"AggregatedSignal.quality_score must be 0-100, got "
                f"{self.quality_score}"
            )


@dataclass(frozen=True)
class StrategyAggregationResult:
    """전체 종목 합산 결과. 주문이 아니다."""
    signals:        tuple[AggregatedSignal, ...]
    conflicts:      tuple[SignalConflict, ...]
    dropped:        tuple[tuple[str, str], ...]  # (symbol, reason) — 후보 탈락 사유
    market_regime:  str | None
    generated_at:   datetime
    is_order_intent: bool = False

    def __post_init__(self) -> None:
        if self.is_order_intent is not False:
            raise ValueError(
                "StrategyAggregationResult.is_order_intent must be False"
            )

    def qualified_candidates(self) -> tuple[AggregatedSignal, ...]:
        return tuple(s for s in self.signals if s.candidate_qualified)


# ====================================================================
# Policy
# ====================================================================


_DEFAULT_REGIME_WEIGHTS: dict[str, dict[str, float]] = {
    _REGIME_TREND_UP: {
        "volume_breakout":  1.3,
        "pullback_rebreak": 1.2,
        "vwap_strategy":    1.0,
        "orb_vwap":         1.0,
    },
    _REGIME_CHOPPY: {
        "vwap_strategy":    1.3,
        "orb_vwap":         0.9,
        "volume_breakout":  0.8,
        "pullback_rebreak": 0.8,
    },
    _REGIME_OPENING_CHAOS: {
        # ORB 는 cooldown 이후만 — 본 가중치는 cooldown 통과 시 1.0. cooldown
        # 미통과는 caller 가 vote 자체를 던지지 않거나, action=WATCH 로 넘긴다.
        "orb_vwap":         1.0,
        "volume_breakout":  0.7,
        "pullback_rebreak": 0.6,
        "vwap_strategy":    0.8,
    },
}


@dataclass(frozen=True)
class AggregatorPolicy:
    """합산 정책 knob. 모든 값은 *advisory* — 실제 주문 차단은 RiskManager."""

    # 단일 전략 후보 자격 임계 (quality_score). 미만이면 WATCH 강등.
    min_quality_score_single_strategy: int = 70

    # 합산 후 후보 자격 최소 confidence.
    min_confidence_to_qualify:         int = 50

    # 같은 방향 supporting 추가 1건 당 confidence boost.
    confidence_boost_per_supporter:    int = 7

    # 후보 자격을 유지할 수 있는 최대 conflict_level.
    max_conflict_for_candidate:        ConflictLevel = ConflictLevel.MEDIUM

    # 장세별 가중치 — strategy_id → weight.
    regime_weights:                    dict[str, dict[str, float]] = field(
        default_factory=lambda: {k: dict(v) for k, v in _DEFAULT_REGIME_WEIGHTS.items()}
    )

    # stale vote 가중치 — is_fresh=False 시.
    stale_weight:                      float = 0.5

    # OPENING_CHAOS regime 에서 orb_vwap 가 cooldown 미통과인 경우 caller 가
    # 표시 — vote.indicators["orb_cooldown_active"] = True 면 본 vote 는 합산에서
    # WATCH 로 강등.
    orb_cooldown_indicator_key:        str = "orb_cooldown_active"


# ====================================================================
# Aggregation
# ====================================================================


def _action_direction(a: SignalAction) -> str:
    """vote 의 *방향* — long / short / neutral. EXIT/SELL 은 short, BUY 는 long,
    WATCH / NO_SIGNAL 은 neutral."""
    if a == SignalAction.BUY:
        return "long"
    if a in (SignalAction.SELL, SignalAction.EXIT):
        return "short"
    return "neutral"


def _dedupe_latest(votes: Iterable[StrategyVote]) -> list[StrategyVote]:
    """같은 (strategy_id, symbol) 의 vote 는 가장 *나중* 만 유지.

    voted_at 이 None 이면 입력 순서를 보존 (OrderedDict 가 마지막 삽입을 우선).
    voted_at 이 있으면 더 큰 값을 채택.
    """
    keyed: "OrderedDict[tuple[str, str], StrategyVote]" = OrderedDict()
    for v in votes:
        key = (v.strategy_id, v.symbol)
        existing = keyed.get(key)
        if existing is None:
            keyed[key] = v
            continue
        # voted_at 비교 — 둘 다 있으면 큰 값, 한쪽만 있으면 그 쪽, 둘 다 없으면
        # 입력 순서 마지막을 유지.
        if v.voted_at is not None and existing.voted_at is not None:
            if v.voted_at >= existing.voted_at:
                keyed[key] = v
        elif v.voted_at is not None and existing.voted_at is None:
            keyed[key] = v
        elif v.voted_at is None and existing.voted_at is None:
            keyed[key] = v
        # else: 새 vote 의 voted_at 이 None, 기존엔 있음 — 기존 유지.
    return list(keyed.values())


def _apply_regime_filters(
    votes: list[StrategyVote], *, market_regime: str | None,
    policy: AggregatorPolicy,
) -> tuple[list[StrategyVote], list[tuple[str, str]]]:
    """장세 기반 사전 필터.

    Returns: (filtered_votes, regime_drops)
    """
    drops: list[tuple[str, str]] = []
    regime = (market_regime or "").upper()

    if regime == _REGIME_RISK_OFF:
        # 모든 BUY 차단 — 다른 방향은 통과 (EXIT/SELL/WATCH 는 손실 방어 정보).
        out: list[StrategyVote] = []
        for v in votes:
            if v.action == SignalAction.BUY:
                drops.append((v.symbol, f"RISK_OFF regime — {v.strategy_id} BUY 차단"))
                continue
            out.append(v)
        return out, drops

    if regime == _REGIME_OPENING_CHAOS:
        # orb_vwap 가 cooldown 미통과면 WATCH 로 강등.
        out = []
        for v in votes:
            if (
                v.strategy_id == "orb_vwap"
                and v.action == SignalAction.BUY
                and bool(v.indicators.get(policy.orb_cooldown_indicator_key))
            ):
                # vote 를 WATCH 로 치환 (cooldown 미통과 가드).
                out.append(StrategyVote(
                    strategy_id=v.strategy_id, symbol=v.symbol,
                    action=SignalAction.WATCH,
                    confidence=min(v.confidence, 30),
                    quality_score=v.quality_score,
                    reasons=v.reasons + ("OPENING_CHAOS cooldown 미통과 — WATCH 강등",),
                    risk_notes=v.risk_notes,
                    indicators=v.indicators,
                    sizing_hint=v.sizing_hint,
                    exit_plan=v.exit_plan,
                    is_fresh=v.is_fresh,
                    voted_at=v.voted_at,
                ))
            else:
                out.append(v)
        return out, drops

    return votes, drops


def _weight_for(
    strategy_id: str, *, market_regime: str | None, policy: AggregatorPolicy,
) -> float:
    """장세 + 전략 id 가중치. 기본 1.0."""
    if not market_regime:
        return 1.0
    table = policy.regime_weights.get(market_regime.upper(), {})
    return float(table.get(strategy_id, 1.0))


def _detect_conflicts(
    symbol_votes: list[StrategyVote],
) -> tuple[ConflictLevel, list[SignalConflict]]:
    """같은 종목 안 vote 들의 방향 충돌 등급."""
    longs  = [v for v in symbol_votes if _action_direction(v.action) == "long"]
    shorts = [v for v in symbol_votes if _action_direction(v.action) == "short"]
    if not longs or not shorts:
        return ConflictLevel.NONE, []

    conflicts: list[SignalConflict] = []
    # 가장 confidence 가 높은 long 과 short 1쌍을 대표 충돌로 기록.
    long_top  = max(longs,  key=lambda v: v.confidence)
    short_top = max(shorts, key=lambda v: v.confidence)

    # severity: 양쪽 모두 high confidence (>=70) 이면 HIGH, 한 쪽만 high 면 MEDIUM,
    # 둘 다 낮으면 LOW.
    if long_top.confidence >= 70 and short_top.confidence >= 70:
        severity = ConflictLevel.HIGH
    elif long_top.confidence >= 70 or short_top.confidence >= 70:
        severity = ConflictLevel.MEDIUM
    else:
        severity = ConflictLevel.LOW

    conflicts.append(SignalConflict(
        symbol=long_top.symbol,
        strategy_a=long_top.strategy_id, strategy_b=short_top.strategy_id,
        action_a=long_top.action,        action_b=short_top.action,
        severity=severity,
        reason=(
            f"방향 충돌: {long_top.strategy_id}({long_top.action.value} "
            f"conf={long_top.confidence}) vs {short_top.strategy_id}"
            f"({short_top.action.value} conf={short_top.confidence})"
        ),
    ))
    return severity, conflicts


def _pick_exit_plan(votes: list[StrategyVote]) -> ExitPlan | None:
    """후보 자격 vote 중 가장 보수적 stop_loss 채택. 없으면 None."""
    plans = [v.exit_plan for v in votes if v.exit_plan is not None]
    if not plans:
        return None
    # stop_loss_pct 가 작을수록 보수적 (손실 한도 더 엄격).
    def _sl_key(p: ExitPlan) -> float:
        return p.stop_loss_pct if p.stop_loss_pct is not None else float("inf")
    chosen = min(plans, key=_sl_key)
    return chosen


def _aggregate_for_symbol(
    symbol_votes: list[StrategyVote], *,
    market_regime: str | None, policy: AggregatorPolicy,
) -> tuple[AggregatedSignal | None, list[SignalConflict], str | None]:
    """단일 종목의 vote 묶음 → AggregatedSignal.

    Returns: (signal, conflicts, drop_reason). signal=None 이면 후보로 빼지
    않을 정도로 신호가 없음.
    """
    if not symbol_votes:
        return None, [], "vote 없음"

    symbol = symbol_votes[0].symbol
    regime_upper = (market_regime or "").upper()

    # 1. 충돌 감지
    conflict_level, conflicts = _detect_conflicts(symbol_votes)

    # 2. EXIT/SELL 우선 검사 — 손실 방어가 진입보다 우선.
    short_votes = [v for v in symbol_votes if _action_direction(v.action) == "short"]
    vwap_loss_votes = [
        v for v in short_votes if v.strategy_id in _VWAP_STRATEGY_IDS
    ]

    # 3. LOW_LIQUIDITY 강등 가드 — BUY 가 있어도 WATCH 강등.
    is_low_liquidity = regime_upper == _REGIME_LOW_LIQUIDITY

    if vwap_loss_votes:
        # VWAP EXIT 우선 — 가장 quality 높은 vote 채택.
        rep = max(vwap_loss_votes, key=lambda v: v.quality_score)
        long_votes = [v for v in symbol_votes if _action_direction(v.action) == "long"]
        supporting = tuple(v.strategy_id for v in short_votes)
        opposing   = tuple(v.strategy_id for v in long_votes)
        neutral    = tuple(
            v.strategy_id for v in symbol_votes
            if _action_direction(v.action) == "neutral"
        )
        reasons = (
            f"VWAP loss/EXIT 우선 — {rep.strategy_id} 손실 방어 신호가 "
            f"BUY ({', '.join(opposing) or '없음'}) 보다 우선",
            *rep.reasons,
        )
        risk_notes = tuple(
            note for v in short_votes for note in v.risk_notes
        )
        action = AggregatedAction.EXIT if rep.action == SignalAction.EXIT \
                                      else AggregatedAction.SELL
        return (
            AggregatedSignal(
                symbol=symbol,
                final_action=action,
                confidence=rep.confidence,
                quality_score=rep.quality_score,
                supporting_strategies=supporting,
                opposing_strategies=opposing,
                neutral_strategies=neutral,
                reasons=reasons,
                risk_notes=risk_notes,
                conflict_level=conflict_level,
                recommended_strategy=rep.strategy_id,
                entry_plan=None,
                exit_plan=rep.exit_plan,
                market_regime=market_regime,
                # 청산/EXIT advisory 는 항상 운영자 검토 대상 (queue 등록 자체는
                # caller 흐름이 결정 — 본 신호는 *후보* 자격을 부여).
                candidate_qualified=conflict_level != ConflictLevel.HIGH,
            ),
            conflicts,
            None,
        )

    # 4. BUY 합산 — long 방향 vote 들.
    long_votes = [v for v in symbol_votes if _action_direction(v.action) == "long"]
    watch_votes = [v for v in symbol_votes if v.action == SignalAction.WATCH]
    nosignal_votes = [v for v in symbol_votes if v.action == SignalAction.NO_SIGNAL]
    neutral_ids = tuple(v.strategy_id for v in watch_votes + nosignal_votes)

    if not long_votes:
        # short 없고 long 없음 — WATCH/NO_SIGNAL 만.
        if watch_votes:
            rep = max(watch_votes, key=lambda v: v.quality_score)
            return (
                AggregatedSignal(
                    symbol=symbol,
                    final_action=AggregatedAction.WATCH,
                    confidence=min(60, rep.confidence),
                    quality_score=rep.quality_score,
                    supporting_strategies=(),
                    opposing_strategies=(),
                    neutral_strategies=tuple(v.strategy_id for v in symbol_votes),
                    reasons=(f"WATCH only — {rep.strategy_id}", *rep.reasons),
                    risk_notes=rep.risk_notes,
                    conflict_level=conflict_level,
                    recommended_strategy=None,
                    entry_plan=None,
                    exit_plan=None,
                    market_regime=market_regime,
                    candidate_qualified=False,
                ),
                conflicts,
                None,
            )
        return None, conflicts, "BUY/SELL/EXIT/WATCH 표 없음 (NO_SIGNAL only)"

    # 5. weighted confidence / quality
    weights = [
        _weight_for(v.strategy_id, market_regime=market_regime, policy=policy)
        * (1.0 if v.is_fresh else policy.stale_weight)
        for v in long_votes
    ]
    total_w = sum(weights) or 1.0
    weighted_conf = sum(v.confidence * w for v, w in zip(long_votes, weights)) / total_w
    weighted_qual = sum(v.quality_score * w for v, w in zip(long_votes, weights)) / total_w

    # 6. supporting boost — 2개 이상이면 confidence 가산.
    supporter_count = len(long_votes)
    boost = policy.confidence_boost_per_supporter * max(0, supporter_count - 1)
    final_conf = int(max(0.0, min(100.0, weighted_conf + boost)))
    final_qual = int(max(0.0, min(100.0, weighted_qual)))

    # 7. LOW_LIQUIDITY 강등 — BUY 라도 WATCH 로 변환, 후보 자격 박탈.
    if is_low_liquidity:
        action = AggregatedAction.WATCH
        candidate_qualified = False
        reasons_extra = (
            f"LOW_LIQUIDITY regime — BUY({supporter_count}건) → WATCH 강등 "
            "(거래대금 부족)",
        )
    else:
        action = AggregatedAction.BUY
        # 단일 전략 가드.
        if supporter_count == 1 and final_qual < policy.min_quality_score_single_strategy:
            action = AggregatedAction.WATCH
            reasons_extra = (
                f"단일 전략({long_votes[0].strategy_id}) quality {final_qual} < "
                f"{policy.min_quality_score_single_strategy} — WATCH 강등",
            )
            candidate_qualified = False
        else:
            reasons_extra = ()
            # confidence 임계 + 충돌 한도 검사.
            below_conf = final_conf < policy.min_confidence_to_qualify
            conflict_too_high = _conflict_exceeds(conflict_level,
                                                   policy.max_conflict_for_candidate)
            if below_conf or conflict_too_high:
                candidate_qualified = False
                if below_conf:
                    reasons_extra = (
                        *reasons_extra,
                        f"통합 confidence {final_conf} < "
                        f"{policy.min_confidence_to_qualify} — 후보 자격 박탈",
                    )
                if conflict_too_high:
                    reasons_extra = (
                        *reasons_extra,
                        f"conflict_level={conflict_level.value} > "
                        f"max_for_candidate={policy.max_conflict_for_candidate.value} "
                        "— 후보 자격 박탈",
                    )
            else:
                candidate_qualified = True

    # 8. 대표 전략 — 가중 weight 가 가장 큰 전략.
    pairs = list(zip(long_votes, weights))
    rep_vote, _ = max(pairs, key=lambda pw: pw[1])

    # 9. reasons 통합.
    base_reasons = (
        f"BUY supporting {supporter_count}건 "
        f"({', '.join(v.strategy_id for v in long_votes)})",
    )
    if supporter_count >= 2:
        base_reasons += (
            f"+{boost} confidence boost (supporter > 1)",
        )
    per_strategy_reasons = tuple(
        f"[{v.strategy_id}] {r}" for v in long_votes for r in v.reasons
    )
    reasons = base_reasons + per_strategy_reasons + reasons_extra

    risk_notes = tuple(note for v in long_votes for note in v.risk_notes)
    if conflict_level != ConflictLevel.NONE:
        risk_notes = (
            f"opposing 방향 vote {len([v for v in symbol_votes if _action_direction(v.action) == 'short'])}건 — "
            f"conflict_level={conflict_level.value}",
            *risk_notes,
        )

    supporting = tuple(v.strategy_id for v in long_votes)
    opposing   = tuple(v.strategy_id for v in symbol_votes
                       if _action_direction(v.action) == "short")
    return (
        AggregatedSignal(
            symbol=symbol,
            final_action=action,
            confidence=final_conf,
            quality_score=final_qual,
            supporting_strategies=supporting,
            opposing_strategies=opposing,
            neutral_strategies=neutral_ids,
            reasons=reasons,
            risk_notes=risk_notes,
            conflict_level=conflict_level,
            recommended_strategy=rep_vote.strategy_id if action == AggregatedAction.BUY else None,
            entry_plan=rep_vote.indicators.get("entry_summary") if isinstance(
                rep_vote.indicators, dict
            ) else None,
            exit_plan=_pick_exit_plan(long_votes),
            market_regime=market_regime,
            candidate_qualified=candidate_qualified,
        ),
        conflicts,
        None,
    )


_CONFLICT_ORDER = {
    ConflictLevel.NONE:   0,
    ConflictLevel.LOW:    1,
    ConflictLevel.MEDIUM: 2,
    ConflictLevel.HIGH:   3,
}


def _conflict_exceeds(a: ConflictLevel, b: ConflictLevel) -> bool:
    return _CONFLICT_ORDER[a] > _CONFLICT_ORDER[b]


def aggregate_signals(
    votes: Iterable[StrategyVote], *,
    market_regime: str | None = None,
    policy: AggregatorPolicy | None = None,
    now: datetime | None = None,
) -> StrategyAggregationResult:
    """4개 전략 vote → 종목별 ``AggregatedSignal`` 묶음.

    본 함수는 pure — 외부 IO / broker / OrderExecutor 호출 0건. 결과는
    *주문 후보가 아니라 advisory 후보 데이터*.
    """
    pol = policy or AggregatorPolicy()
    generated_at = now or datetime.now(timezone.utc)

    # 1. dedup 같은 (strategy_id, symbol) 의 vote 는 가장 최신만.
    deduped = _dedupe_latest(votes)

    # 2. 장세 기반 사전 필터.
    after_regime, regime_drops = _apply_regime_filters(
        deduped, market_regime=market_regime, policy=pol,
    )

    # 3. 종목별 그룹.
    by_symbol: "OrderedDict[str, list[StrategyVote]]" = OrderedDict()
    for v in after_regime:
        by_symbol.setdefault(v.symbol, []).append(v)

    signals: list[AggregatedSignal] = []
    all_conflicts: list[SignalConflict] = []
    dropped: list[tuple[str, str]] = list(regime_drops)

    # 4. 종목별 합산.
    for symbol, sym_votes in by_symbol.items():
        signal, conflicts, drop_reason = _aggregate_for_symbol(
            sym_votes, market_regime=market_regime, policy=pol,
        )
        if signal is None:
            dropped.append((symbol, drop_reason or "신호 없음"))
            continue
        signals.append(signal)
        all_conflicts.extend(conflicts)

    # 5. RISK_OFF 에 의해 BUY 가 모두 제거됐고 short/EXIT 도 없는 종목 → REJECT
    #    advisory 추가 (운영자가 "장세 차단" 을 알 수 있도록).
    if (market_regime or "").upper() == _REGIME_RISK_OFF:
        signal_symbols = {s.symbol for s in signals}
        for v in deduped:
            if v.symbol in signal_symbols:
                continue
            # RISK_OFF 차단된 BUY 만 REJECT advisory 로 표시.
            if v.action == SignalAction.BUY:
                signals.append(AggregatedSignal(
                    symbol=v.symbol,
                    final_action=AggregatedAction.REJECT,
                    confidence=0,
                    quality_score=0,
                    supporting_strategies=(),
                    opposing_strategies=(),
                    neutral_strategies=(),
                    reasons=(
                        "RISK_OFF regime — 모든 BUY 차단",
                        f"[{v.strategy_id}] {' / '.join(v.reasons) if v.reasons else 'BUY 후보'}",
                    ),
                    risk_notes=("시장 전반 위험 회피 — 신규 진입 금지",),
                    conflict_level=ConflictLevel.NONE,
                    recommended_strategy=None,
                    entry_plan=None,
                    exit_plan=None,
                    market_regime=market_regime,
                    candidate_qualified=False,
                ))
                signal_symbols.add(v.symbol)

    return StrategyAggregationResult(
        signals=tuple(signals),
        conflicts=tuple(all_conflicts),
        dropped=tuple(dropped),
        market_regime=market_regime,
        generated_at=generated_at,
    )


# ====================================================================
# ExecutionRecommender 연계 helper
# ====================================================================


def to_execution_proposal(
    signal: AggregatedSignal, *,
    expires_in_seconds: int = 300,
    default_quantity:   int = 1,
    now:                datetime | None = None,
):
    """``AggregatedSignal`` → ``ExecutionProposal`` *변환 helper*.

    **반환값은 advisory payload — 주문이 아니다.** 변환 결과는 그대로
    ``ExecutionRecommender`` 의 precheck / submit helper 에 넘겨지며, 실 주문은
    여전히 ``submit_proposal`` 흐름(=``submit_candidate`` → ``route_order``) 을
    거쳐야만 발생한다.

    None 반환 조건:
    - ``final_action`` 이 BUY / SELL / EXIT 이 아님 (WATCH / REJECT / NO_SIGNAL)
    - ``candidate_qualified=False``

    quantity 는 caller 가 ``default_quantity`` 로 주입 — 본 helper 는 *사이즈
    결정자가 아니다*. 실제 수량은 RiskManager / PositionSizingAgent 가 별도
    경로에서 산출한다.
    """
    if not signal.candidate_qualified:
        return None
    if signal.final_action not in (
        AggregatedAction.BUY, AggregatedAction.SELL, AggregatedAction.EXIT,
    ):
        return None

    # 지연 import — 본 모듈은 top-level 에서 ExecutionProposal 을 import 하지
    # 않는다 (broker.base / OrderRequest 와 같은 주문 계열 객체와의 거리 유지).
    from app.agents.execution_recommender import (
        ExecutionProposal,
        ProposalOrderType,
        ProposalSide,
    )
    import uuid

    now_ts = now or datetime.now(timezone.utc)
    expires = now_ts.replace(microsecond=0)
    # timedelta 직접 import 회피 — datetime + 초 더하기는 timestamp 변환.
    from datetime import timedelta
    expires = now_ts + timedelta(seconds=int(expires_in_seconds))

    side = (
        ProposalSide.BUY if signal.final_action == AggregatedAction.BUY
        else ProposalSide.SELL
    )

    return ExecutionProposal(
        proposal_id=uuid.uuid4().hex,
        symbol=signal.symbol,
        side=side,
        quantity=max(1, int(default_quantity)),
        confidence=int(signal.confidence),
        expires_at=expires,
        order_type=ProposalOrderType.MARKET,
        quality_score=int(signal.quality_score),
        supporting_reasons=tuple(signal.reasons),
        opposing_reasons=tuple(
            f"opposing: {sid}" for sid in signal.opposing_strategies
        ),
        risk_note=" / ".join(signal.risk_notes) if signal.risk_notes else None,
        strategy=signal.recommended_strategy or "aggregator",
        market_regime=signal.market_regime,
    )


# ====================================================================
# Facade — 호출자 친화 wrapper
# ====================================================================


class StrategySignalAggregator:
    """Stateless facade around :func:`aggregate_signals` / :func:`to_execution_proposal`.

    본 클래스는 *순수 wrapper* — internal state 없음, broker / OrderExecutor /
    route_order 호출 0건. 호출자는 같은 ``AggregatorPolicy`` 를 인스턴스에 묶어
    여러 batch 를 처리할 수 있다.

    invariant:
    - 본 class 의 메서드는 **주문을 만들지 않는다** —
      ``aggregate(votes) -> StrategyAggregationResult`` 는 advisory.
    - ``to_proposal(signal)`` 의 반환값 ``ExecutionProposal`` 도
      ``is_order_intent=False`` / ``can_execute_order=False`` 그대로 carry.
    """

    def __init__(self, policy: AggregatorPolicy | None = None) -> None:
        self.policy = policy or AggregatorPolicy()

    def aggregate(
        self,
        votes: Iterable[StrategyVote],
        *,
        market_regime: str | None = None,
        now: datetime | None = None,
    ) -> StrategyAggregationResult:
        """4개 전략 vote → 종목별 ``AggregatedSignal`` 묶음. broker 호출 0건."""
        return aggregate_signals(
            votes, market_regime=market_regime, policy=self.policy, now=now,
        )

    def to_proposal(
        self,
        signal: AggregatedSignal,
        *,
        expires_in_seconds: int = 300,
        default_quantity: int = 1,
        now: datetime | None = None,
    ):
        """advisory ``AggregatedSignal`` → advisory ``ExecutionProposal``.

        반환값은 *주문이 아니다* (``ExecutionProposal.is_order_intent=False``).
        본 helper 도 broker 호출 0건.
        """
        return to_execution_proposal(
            signal,
            expires_in_seconds=expires_in_seconds,
            default_quantity=default_quantity,
            now=now,
        )
