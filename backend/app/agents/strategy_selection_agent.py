"""Strategy Selection Agent (#85).

지능형 advisory Agent — 시장 상태(MarketRegime) 와 4개 단타 전략의 신호를
종합해 *최적 전략 조합* 을 선택하고, 제외된 전략과 그 사유를 carry 한다.

본 Agent 는:

- broker 를 직접 호출하지 **않는다** — ``BrokerAdapter`` / ``KisBrokerAdapter`` /
  ``MockBroker`` 어떤 클래스도 import 0건 (정적 grep 가드).
- ``OrderExecutor`` / ``route_order`` / approval queue submit 함수 어떤 것도
  호출하지 **않는다**.
- 출력(``StrategySelectionReport``) 은 *주문이 아니라* approval candidate 전
  단계의 advisory 데이터.
- ``to_execution_proposal_from_selection()`` helper 로 ``ExecutionRecommender``
  (#56) 흐름에 advisory payload 로 전달 가능 — 그 결과
  ``ExecutionProposal.is_order_intent = False`` / ``can_execute_order = False``
  그대로 carry.

## 통합 / 선택 규칙 (자세한 정책: ``docs/strategy_selection_agent.md``)

1. **장세 우선 차단** — RISK_OFF 면 모든 BUY 차단, LOW_LIQUIDITY 면 WATCH 강등.
   OPENING_CHAOS 에서 ``orb_vwap`` cooldown 미통과는 WATCH 강등.
2. **VWAP / Risk 우선** — ``vwap_strategy`` / ``orb_vwap`` 의 EXIT/SELL 은 같은
   종목 BUY 표를 *항상* 압도.
3. **충돌 처리** — long ↔ short 동시 vote 에 ``conflict_level`` 부여.
   ``HIGH`` 면 ``candidate_qualified=False`` (approval queue 등록 차단).
4. **장세 가중치** — TREND_UP 에서 ``volume_breakout`` / ``pullback_rebreak``
   가중, CHOPPY 에서 ``vwap_strategy`` 가중 (``AggregatorPolicy.regime_weights``).
5. **단일 전략 가드** — supporter 1건이면 ``quality_score >= 70`` 이상이어야
   후보, 미만이면 WATCH 강등.
6. **중복 dedup** — 같은 ``(strategy_id, symbol)`` vote 는 가장 최신만.
7. **선택 산출** — supporter 중 가중치가 가장 큰 strategy 를 ``selected_strategy``
   로, 차단된 전략은 ``BlockedStrategyEntry`` 로 carry.

## 핵심 invariant (정적 grep 가드)

- broker / OrderExecutor / route_order / place_order / cancel_order import 0건
- permission gate / ai.assist 모듈 + approval queue submit helper 호출 0건
- ``StrategySelectionReport.is_order_intent = False`` 불변
  (``__post_init__`` ValueError 가드)
- ``AgentBase.run()`` 가 반환하는 ``AgentOutput`` 도
  ``is_order_intent / can_execute_order = False`` 그대로 (base 가드).
- 외부 HTTP / AI SDK (anthropic / openai / httpx / requests) import 0건
- ``ENABLE_AI_EXECUTION`` / ``ENABLE_LIVE_TRADING`` 등 settings mutate 0건
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum

from app.agents.base import (
    AgentBase,
    AgentContext,
    AgentDecision,
    AgentMetadata,
    AgentOutput,
    AgentRole,
)
from app.strategies.aggregator import (
    AggregatedAction,
    AggregatedSignal,
    AggregatorPolicy,
    ConflictLevel,
    StrategyAggregationResult,
    StrategySignalAggregator,
    StrategyVote,
    to_execution_proposal,
)


# ====================================================================
# Enums / DTO
# ====================================================================


class BlockedReason(StrEnum):
    """전략이 후보에서 *제외* 된 사유. 본 enum 의 값은 운영자가 audit / UI 에서
    바로 인식할 수 있는 카테고리 — *주문 결정 라벨이 아니다*.
    """
    RISK_OFF_REGIME            = "RISK_OFF_REGIME"
    LOW_LIQUIDITY_REGIME       = "LOW_LIQUIDITY_REGIME"
    ORB_COOLDOWN_ACTIVE        = "ORB_COOLDOWN_ACTIVE"
    QUALITY_BELOW_THRESHOLD    = "QUALITY_BELOW_THRESHOLD"
    CONFIDENCE_BELOW_THRESHOLD = "CONFIDENCE_BELOW_THRESHOLD"
    CONFLICT_TOO_HIGH          = "CONFLICT_TOO_HIGH"
    OPPOSING_VWAP_PRIORITY     = "OPPOSING_VWAP_PRIORITY"
    NO_SIGNAL                  = "NO_SIGNAL"
    WATCH_ONLY                 = "WATCH_ONLY"


@dataclass(frozen=True)
class StrategyCandidate:
    """후보 전략 한 건. ``score`` 는 ``confidence * regime_weight`` (advisory only)."""
    strategy_id:    str
    symbol:         str
    action:         AggregatedAction
    confidence:     int
    quality_score:  int
    score:          float                  # 가중치 적용 점수
    is_supporting:  bool                   # 최종 selected 와 같은 방향인지
    reasons:        tuple[str, ...] = ()


@dataclass(frozen=True)
class BlockedStrategyEntry:
    """제외된 전략 + 사유. 운영자 UI 에 *왜 빠졌는지* 명시."""
    strategy_id:    str
    symbol:         str
    reason:         BlockedReason
    detail:         str
    action_voted:   AggregatedAction | None = None


@dataclass(frozen=True)
class StrategySelectionReport:
    """``StrategySelectionAgent`` 의 풍부한 출력 dataclass.

    invariant:
    - ``is_order_intent = False`` 불변 (``__post_init__`` ValueError 가드)
    - ``is_order_signal = False`` 명시 — UI 가 표시
    - ``can_execute_order = False`` 명시 — 본 리포트로 직접 주문 불가
    """
    symbol:                 str | None       # 단일 종목 분석이면 채움, 다종목이면 None
    market_regime:          str | None
    selected_strategy:      str | None       # 최종 채택 (없으면 None)
    final_action:           AggregatedAction
    confidence:             int
    quality_score:          int
    conflict_level:         ConflictLevel
    candidate_qualified:    bool
    candidates:             tuple[StrategyCandidate, ...]
    blocked:                tuple[BlockedStrategyEntry, ...]
    reasons:                tuple[str, ...]
    risk_notes:             tuple[str, ...]
    aggregated_signal:      AggregatedSignal | None
    # invariant flags
    is_order_intent:        bool = False
    is_order_signal:        bool = False
    can_execute_order:      bool = False
    generated_at:           datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def __post_init__(self) -> None:
        if self.is_order_intent is not False:
            raise ValueError(
                "StrategySelectionReport.is_order_intent must be False — "
                "본 리포트는 주문 객체가 아닙니다 (advisory only)."
            )
        if self.is_order_signal is not False:
            raise ValueError(
                "StrategySelectionReport.is_order_signal must be False — "
                "본 리포트는 주문 신호가 아닙니다."
            )
        if self.can_execute_order is not False:
            raise ValueError(
                "StrategySelectionReport.can_execute_order must be False — "
                "본 리포트는 직접 주문을 만들 수 없습니다."
            )
        if not (0 <= int(self.confidence) <= 100):
            raise ValueError(
                f"confidence must be 0-100, got {self.confidence}"
            )
        if not (0 <= int(self.quality_score) <= 100):
            raise ValueError(
                f"quality_score must be 0-100, got {self.quality_score}"
            )

    def to_dict(self) -> dict:
        return {
            "symbol":             self.symbol,
            "market_regime":      self.market_regime,
            "selected_strategy":  self.selected_strategy,
            "final_action":       self.final_action.value,
            "confidence":         int(self.confidence),
            "quality_score":      int(self.quality_score),
            "conflict_level":     self.conflict_level.value,
            "candidate_qualified": bool(self.candidate_qualified),
            "candidates": [
                {
                    "strategy_id":   c.strategy_id,
                    "symbol":        c.symbol,
                    "action":        c.action.value,
                    "confidence":    int(c.confidence),
                    "quality_score": int(c.quality_score),
                    "score":         float(c.score),
                    "is_supporting": bool(c.is_supporting),
                    "reasons":       list(c.reasons),
                }
                for c in self.candidates
            ],
            "blocked": [
                {
                    "strategy_id":  b.strategy_id,
                    "symbol":       b.symbol,
                    "reason":       b.reason.value,
                    "detail":       b.detail,
                    "action_voted": b.action_voted.value if b.action_voted else None,
                }
                for b in self.blocked
            ],
            "reasons":           list(self.reasons),
            "risk_notes":        list(self.risk_notes),
            "is_order_intent":   self.is_order_intent,
            "is_order_signal":   self.is_order_signal,
            "can_execute_order": self.can_execute_order,
            "generated_at":      self.generated_at.isoformat(),
        }


@dataclass(frozen=True)
class StrategySelectionInput:
    """Agent 입력. caller 가 votes + market_regime + policy 를 채워서 전달."""
    votes:          tuple[StrategyVote, ...]
    market_regime:  str | None              = None
    policy:         AggregatorPolicy | None = None
    # 단일 종목 분석이면 채움 — 다종목이면 None (Agent 는 입력 모든 종목에 대해
    # 합산하고, 그 중 후보 자격을 갖춘 첫 종목을 selected_strategy 의 *대표* 로
    # carry).
    focus_symbol:   str | None              = None


# ====================================================================
# Selection logic
# ====================================================================


def _regime_weight(
    strategy_id: str, *, market_regime: str | None, policy: AggregatorPolicy,
) -> float:
    if not market_regime:
        return 1.0
    table = policy.regime_weights.get(market_regime.upper(), {})
    return float(table.get(strategy_id, 1.0))


def _classify_blocked(
    votes: tuple[StrategyVote, ...],
    aggregated: AggregatedSignal,
    *,
    market_regime: str | None,
    policy: AggregatorPolicy,
) -> list[BlockedStrategyEntry]:
    """합산 결과를 기준으로 *제외된* 전략을 classify."""
    blocked: list[BlockedStrategyEntry] = []
    regime_upper = (market_regime or "").upper()
    supporting = set(aggregated.supporting_strategies)
    opposing   = set(aggregated.opposing_strategies)

    for v in votes:
        if v.strategy_id in supporting:
            continue  # 채택된 전략은 차단 아님

        # 1) RISK_OFF — BUY 가 차단된 경우
        if regime_upper == "RISK_OFF" and v.action.value == "BUY":
            blocked.append(BlockedStrategyEntry(
                strategy_id=v.strategy_id, symbol=v.symbol,
                reason=BlockedReason.RISK_OFF_REGIME,
                detail="RISK_OFF regime — 모든 BUY 차단",
                action_voted=AggregatedAction.BUY,
            ))
            continue

        # 2) LOW_LIQUIDITY — BUY 가 WATCH 로 강등
        if regime_upper == "LOW_LIQUIDITY" and v.action.value == "BUY":
            blocked.append(BlockedStrategyEntry(
                strategy_id=v.strategy_id, symbol=v.symbol,
                reason=BlockedReason.LOW_LIQUIDITY_REGIME,
                detail="LOW_LIQUIDITY regime — BUY → WATCH 강등 (거래대금 부족)",
                action_voted=AggregatedAction.WATCH,
            ))
            continue

        # 3) OPENING_CHAOS + orb_vwap cooldown
        if (
            regime_upper == "OPENING_CHAOS"
            and v.strategy_id == "orb_vwap"
            and bool(v.indicators.get(policy.orb_cooldown_indicator_key))
        ):
            blocked.append(BlockedStrategyEntry(
                strategy_id=v.strategy_id, symbol=v.symbol,
                reason=BlockedReason.ORB_COOLDOWN_ACTIVE,
                detail="OPENING_CHAOS cooldown 미통과 — orb_vwap WATCH 강등",
                action_voted=AggregatedAction.WATCH,
            ))
            continue

        # 4) VWAP/EXIT 우선이 BUY 를 압도한 경우 (opposing 에 들어감)
        if v.strategy_id in opposing and v.action.value == "BUY":
            blocked.append(BlockedStrategyEntry(
                strategy_id=v.strategy_id, symbol=v.symbol,
                reason=BlockedReason.OPPOSING_VWAP_PRIORITY,
                detail=(
                    "VWAP/EXIT 손실 방어 신호가 우선 — 본 BUY 표는 같은 종목에서 차단"
                ),
                action_voted=AggregatedAction.BUY,
            ))
            continue

        # 5) WATCH / NO_SIGNAL — 기본 분류
        if v.action.value == "WATCH":
            blocked.append(BlockedStrategyEntry(
                strategy_id=v.strategy_id, symbol=v.symbol,
                reason=BlockedReason.WATCH_ONLY,
                detail="WATCH only — 후보 자격 없음",
                action_voted=AggregatedAction.WATCH,
            ))
            continue
        if v.action.value == "NO_SIGNAL":
            blocked.append(BlockedStrategyEntry(
                strategy_id=v.strategy_id, symbol=v.symbol,
                reason=BlockedReason.NO_SIGNAL,
                detail="신호 없음",
                action_voted=AggregatedAction.NO_SIGNAL,
            ))
            continue

        # 6) conflict 가 너무 높아서 candidate_qualified=False 된 경우
        if (
            aggregated.conflict_level == ConflictLevel.HIGH
            and not aggregated.candidate_qualified
        ):
            blocked.append(BlockedStrategyEntry(
                strategy_id=v.strategy_id, symbol=v.symbol,
                reason=BlockedReason.CONFLICT_TOO_HIGH,
                detail=(
                    f"conflict_level={aggregated.conflict_level.value} — "
                    "approval queue 등록 차단"
                ),
                action_voted=AggregatedAction[v.action.value]
                if v.action.value in AggregatedAction.__members__ else None,
            ))
            continue

        # 7) 단일 전략 가드 / confidence 가드 — supporting 외 vote
        if v.action.value in ("BUY", "SELL", "EXIT"):
            if int(v.quality_score) < int(policy.min_quality_score_single_strategy):
                blocked.append(BlockedStrategyEntry(
                    strategy_id=v.strategy_id, symbol=v.symbol,
                    reason=BlockedReason.QUALITY_BELOW_THRESHOLD,
                    detail=(
                        f"quality_score={v.quality_score} < "
                        f"{policy.min_quality_score_single_strategy} — 후보 자격 박탈"
                    ),
                    action_voted=AggregatedAction[v.action.value]
                    if v.action.value in AggregatedAction.__members__ else None,
                ))
                continue
            if int(v.confidence) < int(policy.min_confidence_to_qualify):
                blocked.append(BlockedStrategyEntry(
                    strategy_id=v.strategy_id, symbol=v.symbol,
                    reason=BlockedReason.CONFIDENCE_BELOW_THRESHOLD,
                    detail=(
                        f"confidence={v.confidence} < "
                        f"{policy.min_confidence_to_qualify} — 후보 자격 박탈"
                    ),
                    action_voted=AggregatedAction[v.action.value]
                    if v.action.value in AggregatedAction.__members__ else None,
                ))
                continue

    return blocked


def _build_candidate(
    v: StrategyVote, aggregated: AggregatedSignal, *,
    market_regime: str | None, policy: AggregatorPolicy,
) -> StrategyCandidate:
    weight = _regime_weight(v.strategy_id, market_regime=market_regime, policy=policy)
    fresh_mult = 1.0 if v.is_fresh else policy.stale_weight
    score = float(v.confidence) * weight * fresh_mult
    is_sup = v.strategy_id in set(aggregated.supporting_strategies)

    # action enum 변환 — StrategyVote.action 은 SignalAction, AggregatedAction 에는
    # 같은 라벨이 있으므로 by-name 변환 가능 (BUY/SELL/EXIT/WATCH/NO_SIGNAL).
    try:
        aaction = AggregatedAction[v.action.value]
    except KeyError:
        aaction = AggregatedAction.NO_SIGNAL

    return StrategyCandidate(
        strategy_id=v.strategy_id, symbol=v.symbol, action=aaction,
        confidence=int(v.confidence), quality_score=int(v.quality_score),
        score=round(score, 3), is_supporting=is_sup,
        reasons=tuple(v.reasons),
    )


def select_strategies(input: StrategySelectionInput) -> StrategySelectionReport:
    """Strategy Selection Agent 의 *순수 함수* 본체.

    ``StrategySignalAggregator`` 로 종목별 ``AggregatedSignal`` 묶음을 만든 후,
    focus_symbol(또는 첫 후보 자격 종목) 기준으로 selected_strategy /
    candidates / blocked / conflict_level / final_action 을 정리.

    본 함수는 broker / OrderExecutor / route_order 호출 0건.
    """
    policy = input.policy or AggregatorPolicy()
    facade = StrategySignalAggregator(policy=policy)
    result: StrategyAggregationResult = facade.aggregate(
        input.votes, market_regime=input.market_regime,
    )

    # focus_symbol 결정 — 명시값 우선, 없으면 *후보 자격 있는 첫 신호 종목*,
    # 그것도 없으면 첫 신호 종목, 그것도 없으면 입력 vote 첫 symbol.
    candidates_qualified = result.qualified_candidates()
    focus_signal: AggregatedSignal | None = None
    if input.focus_symbol:
        focus_signal = next(
            (s for s in result.signals if s.symbol == input.focus_symbol),
            None,
        )
    if focus_signal is None and candidates_qualified:
        focus_signal = candidates_qualified[0]
    if focus_signal is None and result.signals:
        focus_signal = result.signals[0]

    if focus_signal is None:
        # 신호 자체가 없음 — 입력의 첫 vote symbol 을 carry 하거나 None.
        first_symbol = input.votes[0].symbol if input.votes else None
        return StrategySelectionReport(
            symbol=first_symbol,
            market_regime=input.market_regime,
            selected_strategy=None,
            final_action=AggregatedAction.NO_SIGNAL,
            confidence=0,
            quality_score=0,
            conflict_level=ConflictLevel.NONE,
            candidate_qualified=False,
            candidates=(),
            blocked=tuple(
                BlockedStrategyEntry(
                    strategy_id=v.strategy_id, symbol=v.symbol,
                    reason=BlockedReason.NO_SIGNAL,
                    detail="신호 없음 (NO_SIGNAL 만 존재)",
                    action_voted=AggregatedAction.NO_SIGNAL,
                )
                for v in input.votes
            ),
            reasons=("입력 vote 중 후보 자격 신호 없음",),
            risk_notes=(),
            aggregated_signal=None,
        )

    # 같은 symbol 의 vote 만 후보 / blocked 분류 대상.
    same_symbol_votes = tuple(
        v for v in input.votes if v.symbol == focus_signal.symbol
    )

    candidates = tuple(
        _build_candidate(v, focus_signal,
                         market_regime=input.market_regime, policy=policy)
        for v in same_symbol_votes
    )
    blocked = tuple(_classify_blocked(
        same_symbol_votes, focus_signal,
        market_regime=input.market_regime, policy=policy,
    ))

    # selected_strategy: 후보 자격 + recommended_strategy.
    selected = focus_signal.recommended_strategy if focus_signal.candidate_qualified else None

    return StrategySelectionReport(
        symbol=focus_signal.symbol,
        market_regime=focus_signal.market_regime or input.market_regime,
        selected_strategy=selected,
        final_action=focus_signal.final_action,
        confidence=int(focus_signal.confidence),
        quality_score=int(focus_signal.quality_score),
        conflict_level=focus_signal.conflict_level,
        candidate_qualified=bool(focus_signal.candidate_qualified),
        candidates=candidates,
        blocked=blocked,
        reasons=tuple(focus_signal.reasons),
        risk_notes=tuple(focus_signal.risk_notes),
        aggregated_signal=focus_signal,
    )


# ====================================================================
# ExecutionRecommender 연계 helper
# ====================================================================


def to_execution_proposal_from_selection(
    report: StrategySelectionReport, *,
    expires_in_seconds: int = 300,
    default_quantity:   int = 1,
    now:                datetime | None = None,
):
    """``StrategySelectionReport`` → ``ExecutionProposal`` *변환 helper*.

    **반환값은 advisory payload — 주문이 아니다.** 본 helper 는 aggregator 의
    ``to_execution_proposal`` 에 그대로 위임 — 본 모듈은 ``ExecutionProposal`` 을
    *직접* import 하지 않는다 (lazy import 그대로 carry, 정적 grep 가드 통과).

    None 반환:
    - ``report.aggregated_signal`` 이 None (신호 없음)
    - 또는 aggregator helper 의 None 조건 (candidate_qualified=False /
      action ∉ {BUY/SELL/EXIT})
    """
    if report.aggregated_signal is None:
        return None
    return to_execution_proposal(
        report.aggregated_signal,
        expires_in_seconds=expires_in_seconds,
        default_quantity=default_quantity,
        now=now,
    )


# ====================================================================
# AgentBase 호환 implementation
# ====================================================================


class StrategySelectionAgent(AgentBase):
    """``AgentBase`` 호환 implementation.

    ``context.extra["strategy_selection_input"]`` 가
    ``StrategySelectionInput`` 이면 그대로 사용. 그렇지 않으면 빈 입력 → NO_SIGNAL
    리포트. caller 가 풍부한 입력을 사용하려면 ``select_strategies(input)`` 을
    직접 호출하는 패턴을 권장.
    """

    role = AgentRole.STRATEGY_RESEARCHER  # 6역할 매핑: 전략 분석/추천

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            name="StrategySelectionAgent",
            role=self.role,
            description=(
                "4개 단타 전략 vote 와 MarketRegime 을 받아 최적 전략 조합을 "
                "선택. 출력은 *주문이 아니라* approval candidate 전 단계의 "
                "advisory 리포트."
            ),
            inputs=[
                "votes (StrategyVote 묶음)",
                "market_regime (TREND_UP / CHOPPY / RISK_OFF / LOW_LIQUIDITY / "
                "OPENING_CHAOS / ...)",
                "policy (AggregatorPolicy, optional)",
            ],
            outputs=[
                "selected_strategy",
                "candidates + score",
                "blocked + reason",
                "conflict_level",
                "final_action / confidence / quality_score",
                "is_order_signal=False (advisory only)",
            ],
            forbidden=[
                "broker / OrderExecutor / route_order 호출 금지",
                "approval queue submit helper / permission gate 호출 금지",
                "DB INSERT / UPDATE / DELETE 금지 (본 모듈 DB 접근 0건)",
                "외부 AI / HTTP 호출 금지",
            ],
            can_execute_order=False,
        )

    def run(self, context: AgentContext) -> AgentOutput:
        inp = (context.extra or {}).get("strategy_selection_input")
        if not isinstance(inp, StrategySelectionInput):
            inp = StrategySelectionInput(
                votes=(),
                market_regime=(context.market_state or {}).get("regime")
                if isinstance(context.market_state, dict) else None,
            )
        report = select_strategies(inp)

        # AgentOutput 으로 변환 — 풍부 데이터는 metadata 로 carry. EXIT/SELL 은
        # *손실 방어* 신호라 RECOMMEND(신규 진입 추천)가 아닌 WARN 으로 분류 —
        # 운영자가 신규 BUY 추천과 시각적으로 구분.
        if report.final_action in (
            AggregatedAction.EXIT, AggregatedAction.SELL,
        ):
            decision = AgentDecision.WARN
            summary = (
                f"{report.symbol} → {report.final_action.value} "
                f"(손실 방어 advisory)"
            )
        elif report.selected_strategy and report.candidate_qualified:
            decision = AgentDecision.RECOMMEND
            summary = (
                f"{report.symbol} → {report.final_action.value} "
                f"(추천 전략: {report.selected_strategy}, "
                f"conf={report.confidence})"
            )
        elif report.final_action == AggregatedAction.REJECT:
            decision = AgentDecision.REJECT
            summary = (
                f"{report.symbol} → REJECT "
                f"(regime={report.market_regime or '-'})"
            )
        elif report.final_action == AggregatedAction.WATCH:
            decision = AgentDecision.OBSERVE
            summary = f"{report.symbol} → WATCH (후보 자격 없음)"
        else:
            decision = AgentDecision.NO_OP
            summary = "선택할 전략 없음"

        risk_flags: list[str] = []
        if report.conflict_level == ConflictLevel.HIGH:
            risk_flags.append("conflict_high")
        if report.market_regime in {"RISK_OFF", "LOW_LIQUIDITY"}:
            risk_flags.append(f"regime:{report.market_regime}")
        if report.aggregated_signal is not None and report.aggregated_signal.risk_notes:
            risk_flags.append("has_risk_notes")

        return AgentOutput(
            role=self.role,
            decision=decision,
            summary=summary,
            reasons=list(report.reasons)[:8],
            confidence=report.confidence,
            risk_flags=risk_flags,
            metadata=report.to_dict(),
            # invariant — base dataclass 가드도 동일.
            is_order_intent=False,
            can_execute_order=False,
        )
