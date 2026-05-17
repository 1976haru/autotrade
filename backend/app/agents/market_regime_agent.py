"""#4-04: Market Regime Agent — 장세 판단 + 장세별 전략 선택 필터.

현재 장세 (TREND_UP / TREND_DOWN / SIDEWAYS / HIGH_VOLATILITY / LOW_LIQUIDITY /
CHOPPY / UNKNOWN) 를 *advisory* 로 판단하고, 4-02 의
`StrategyCombinationRecommendation` 위에 장세별 필터를 적용한다.

## 기존 `app.agents.market_regime` 과의 관계

기존 `app.agents.market_regime` (#225) 은 *주문 결정 단계*에서 사용하는 trade-flow
filter (10 regime — `TREND_UP/TREND_DOWN/CHOPPY/HIGH_VOLATILITY/LOW_LIQUIDITY/
GAP_DAY/NEWS_DRIVEN/RISK_OFF/OPENING_CHAOS/LATE_DAY_FADE`). 본 모듈은 *AI Agent
선택 단계* 의 advisory 분류 (7 regime — user spec 기준) 로 **별개 책임**:

- 기존 모듈: 단일 주문 의사결정에 사용 (실시간 filter).
- 본 모듈: 일일 전략 조합 추천에 사용 (advisory 분류).

두 모듈은 import 의존 0건 — 운영자 혼선 방지를 위해 분리.

## 절대 invariant (CLAUDE.md 절대 원칙 1~5 상속)

1. **본 분류 / 필터는 *주문 신호가 아니다*** — `is_order_signal=False` 불변.
2. **자동 적용 0건** — `auto_apply_allowed=False` 불변.
3. **실거래 허가 0건** — `is_live_authorization=False` 불변.
4. **자동 paper trader 시작 0건** — `auto_start_paper_trader=False` 불변.
5. **broker / OrderExecutor / route_order import 0건** — 정적 grep.
6. **외부 HTTP / AI SDK / LLM import 0건** — 결정론적 휴리스틱.
7. **DB write 0건** — read-only.
8. **`MarketRegime` enum 에 BUY/SELL/HOLD 주문 방향 0개**.

## 7 장세 (user spec)

| Regime | 의미 |
|---|---|
| `TREND_UP` | 강한 상승 추세 |
| `TREND_DOWN` | 강한 하락 추세 |
| `SIDEWAYS` | 횡보 — 명확한 방향 없음 |
| `HIGH_VOLATILITY` | 변동성 급증 — sizing 축소 필요 |
| `LOW_LIQUIDITY` | 거래대금 부족 — 슬리피지 위험 |
| `CHOPPY` | 무방향 + 잦은 반전 |
| `UNKNOWN` | 데이터 부족 / 분류 불가 — WATCH_ONLY |

## 장세별 전략 정책

| Regime | Preferred (점수 우대) | Watchlist (HOLD 권고) | Blocked (EXCLUDE 권고) |
|---|---|---|---|
| `TREND_UP` | sma_crossover, volume_breakout, orb_vwap, pullback_rebreak | (없음) | (없음) |
| `TREND_DOWN` | (없음 — defensive) | rsi_reversion | volume_breakout, orb_vwap |
| `SIDEWAYS` | rsi_reversion, vwap_strategy | sma_crossover, pullback_rebreak | volume_breakout, orb_vwap |
| `HIGH_VOLATILITY` | (없음 — size 축소) | rsi_reversion, vwap_strategy, sma_crossover, pullback_rebreak | orb_vwap, volume_breakout |
| `LOW_LIQUIDITY` | (없음) | rsi_reversion, vwap_strategy | orb_vwap, volume_breakout, sma_crossover, pullback_rebreak |
| `CHOPPY` | (없음) | rsi_reversion, vwap_strategy | sma_crossover, pullback_rebreak, orb_vwap, volume_breakout |
| `UNKNOWN` | (없음 — WATCH_ONLY) | (모든 전략) | (없음 — 보수적 보류) |

## 과최적화와 우선순위

본 모듈의 `apply_regime_filter` 는 *기존 `recommended_combo` 에 남은 전략* 만
처리한다 — 4-03 의 `apply_overfit_filter` 가 OVERFIT_RISK 를 이미 제거한 뒤라면
본 모듈이 그것을 *원복하지 않는다*. **과최적화 차단이 항상 우선** (테스트 lock).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from app.agents.base import (
    AgentBase,
    AgentContext,
    AgentDecision,
    AgentMetadata,
    AgentOutput,
    AgentRole,
)
from app.agents.strategy_combination_recommender import (
    OverallRecommendation,
    StrategyAction,
    StrategyCombinationRecommendation,
    StrategyDecision,
)


REGIME_SCHEMA_VERSION = "1.0"


# ─────────────────────────────────────────────────────────────────────────────
# 1. Enums
# ─────────────────────────────────────────────────────────────────────────────


class MarketRegime(StrEnum):
    """7 장세 — *주문 방향* 0개."""
    TREND_UP        = "TREND_UP"
    TREND_DOWN      = "TREND_DOWN"
    SIDEWAYS        = "SIDEWAYS"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_LIQUIDITY   = "LOW_LIQUIDITY"
    CHOPPY          = "CHOPPY"
    UNKNOWN         = "UNKNOWN"


_REGIME_LABEL_KO: dict[MarketRegime, str] = {
    MarketRegime.TREND_UP:        "상승 추세 — momentum / breakout 우선",
    MarketRegime.TREND_DOWN:      "하락 추세 — 신규 진입 축소, 손실 제한 우선",
    MarketRegime.SIDEWAYS:        "횡보 — mean-reversion 우선, breakout 보류",
    MarketRegime.HIGH_VOLATILITY: "변동성 급증 — 신규 진입 축소, size 축소 권고",
    MarketRegime.LOW_LIQUIDITY:   "거래대금 부족 — 대부분 신규 진입 보류, 슬리피지 위험",
    MarketRegime.CHOPPY:          "무방향 + 잦은 반전 — 추세추종 보류, mean-reversion 만 제한 검토",
    MarketRegime.UNKNOWN:         "장세 분류 불가 — Paper 자동 시작 금지, WATCH_ONLY",
}


# 전략 → 정책 매핑 — 6개 등록 전략 (registry_metadata 와 일치).
REGIME_STRATEGY_POLICY: dict[MarketRegime, dict[str, set[str]]] = {
    MarketRegime.TREND_UP: {
        "preferred": {"sma_crossover", "volume_breakout", "orb_vwap", "pullback_rebreak"},
        "watchlist": set(),
        "blocked":   set(),
    },
    MarketRegime.TREND_DOWN: {
        "preferred": set(),
        "watchlist": {"rsi_reversion"},
        "blocked":   {"volume_breakout", "orb_vwap"},
    },
    MarketRegime.SIDEWAYS: {
        "preferred": {"rsi_reversion", "vwap_strategy"},
        "watchlist": {"sma_crossover", "pullback_rebreak"},
        "blocked":   {"volume_breakout", "orb_vwap"},
    },
    MarketRegime.HIGH_VOLATILITY: {
        "preferred": set(),
        "watchlist": {"rsi_reversion", "vwap_strategy",
                       "sma_crossover", "pullback_rebreak"},
        "blocked":   {"orb_vwap", "volume_breakout"},
    },
    MarketRegime.LOW_LIQUIDITY: {
        "preferred": set(),
        "watchlist": {"rsi_reversion", "vwap_strategy"},
        "blocked":   {"orb_vwap", "volume_breakout",
                       "sma_crossover", "pullback_rebreak"},
    },
    MarketRegime.CHOPPY: {
        "preferred": set(),
        "watchlist": {"rsi_reversion", "vwap_strategy"},
        "blocked":   {"sma_crossover", "pullback_rebreak",
                       "orb_vwap", "volume_breakout"},
    },
    MarketRegime.UNKNOWN: {
        "preferred": set(),
        "watchlist": {"sma_crossover", "rsi_reversion", "vwap_strategy",
                       "orb_vwap", "volume_breakout", "pullback_rebreak"},
        "blocked":   set(),
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# 2. Classifier input + report
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MarketStateInput:
    """장세 분류 입력 — 모든 필드 optional, 부족 시 UNKNOWN.

    *주의*: 본 dataclass 는 시장 metric 만 받는다 — API key / Secret /
    계좌번호 필드 0개 (테스트 lock).
    """

    trend_direction:    str   | None = None    # "UP" / "DOWN" / "SIDEWAYS" / None
    volatility_pct:     float | None = None    # daily ATR / 종가 ratio (예: 0.03)
    liquidity_score:    float | None = None    # 0~1, 1=normal, 0=very low
    momentum_score:     float | None = None    # -1..1
    choppiness_index:   float | None = None    # 0~1, >0.6 → choppy

    # 임계 — runtime override 가능.
    high_volatility_threshold: float = 0.04   # 4% daily ATR
    low_liquidity_threshold:   float = 0.30
    choppiness_threshold:      float = 0.60


@dataclass(frozen=True)
class MarketRegimeReport:
    """장세 분류 결과 + 장세별 전략 정책 carry."""

    generated_at:           str
    schema_version:         str
    regime:                 MarketRegime
    confidence:             float                  # 0~1
    reasons:                list[str]              = field(default_factory=list)
    risk_flags:             list[str]              = field(default_factory=list)
    allowed_strategies:     list[str]              = field(default_factory=list)  # preferred
    blocked_strategies:     list[str]              = field(default_factory=list)
    watchlist_strategies:   list[str]              = field(default_factory=list)
    operator_note:          str | None             = None
    advisory_disclaimer:    str                    = (
        "본 장세 분류는 *advisory* — 자동 paper trader 시작 / 자동 실거래 활성화"
        " 를 수행하지 않습니다. 운영자가 본 결과를 *참고*하여 BotControl 흐름에서"
        " 명시 시작. is_order_signal=False / auto_apply_allowed=False / "
        "is_live_authorization=False / auto_start_paper_trader=False."
    )
    metadata:               dict[str, Any]         = field(default_factory=dict)

    # 절대 invariant.
    is_order_signal:        bool = False
    auto_apply_allowed:     bool = False
    is_live_authorization:  bool = False
    auto_start_paper_trader: bool = False

    def __post_init__(self) -> None:
        for name, val in (
            ("is_order_signal",         self.is_order_signal),
            ("auto_apply_allowed",      self.auto_apply_allowed),
            ("is_live_authorization",   self.is_live_authorization),
            ("auto_start_paper_trader", self.auto_start_paper_trader),
        ):
            if val is not False:
                raise ValueError(f"MarketRegimeReport.{name} must be False.")
        if not isinstance(self.regime, MarketRegime):
            raise ValueError("regime must be MarketRegime.")
        if not isinstance(self.advisory_disclaimer, str) or not self.advisory_disclaimer:
            raise ValueError("advisory_disclaimer must be non-empty.")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be in [0,1], got {self.confidence}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at":           self.generated_at,
            "schema_version":         self.schema_version,
            "regime":                 self.regime.value,
            "regime_label_ko":        _REGIME_LABEL_KO[self.regime],
            "confidence":             float(self.confidence),
            "reasons":                list(self.reasons),
            "risk_flags":             list(self.risk_flags),
            "allowed_strategies":     list(self.allowed_strategies),
            "blocked_strategies":     list(self.blocked_strategies),
            "watchlist_strategies":   list(self.watchlist_strategies),
            "operator_note":          self.operator_note,
            "advisory_disclaimer":    self.advisory_disclaimer,
            "metadata":               dict(self.metadata),
            # invariants.
            "is_order_signal":        False,
            "auto_apply_allowed":     False,
            "is_live_authorization":  False,
            "auto_start_paper_trader": False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 3. 분류기
# ─────────────────────────────────────────────────────────────────────────────


def _build_report(
    regime: MarketRegime,
    *,
    confidence: float,
    reasons: list[str],
    risk_flags: list[str] | None = None,
    operator_note: str | None = None,
    metadata: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> MarketRegimeReport:
    policy = REGIME_STRATEGY_POLICY[regime]
    if now is None:
        now = datetime.now(timezone.utc)
    return MarketRegimeReport(
        generated_at=now.isoformat(),
        schema_version=REGIME_SCHEMA_VERSION,
        regime=regime,
        confidence=confidence,
        reasons=reasons,
        risk_flags=risk_flags or [],
        allowed_strategies=sorted(policy["preferred"]),
        blocked_strategies=sorted(policy["blocked"]),
        watchlist_strategies=sorted(policy["watchlist"]),
        operator_note=operator_note,
        metadata=dict(metadata or {}),
    )


def classify_market_regime(
    state: MarketStateInput | None = None,
    *,
    now: datetime | None = None,
) -> MarketRegimeReport:
    """입력 metric → MarketRegimeReport — 결정론적 분류.

    분류 순서 (가장 보수적부터):
    1. `liquidity_score < low_liquidity_threshold` → `LOW_LIQUIDITY`
    2. `volatility_pct > high_volatility_threshold` → `HIGH_VOLATILITY`
    3. `choppiness_index > choppiness_threshold` → `CHOPPY`
    4. `trend_direction == "UP"` → `TREND_UP`
    5. `trend_direction == "DOWN"` → `TREND_DOWN`
    6. `trend_direction == "SIDEWAYS"` → `SIDEWAYS`
    7. 그 외 (입력 없음 / 불명확) → `UNKNOWN`
    """
    state = state or MarketStateInput()
    reasons: list[str] = []
    risk_flags: list[str] = []

    # 1) LOW_LIQUIDITY 가장 우선.
    if state.liquidity_score is not None and \
            state.liquidity_score < state.low_liquidity_threshold:
        reasons.append(
            f"liquidity_score={state.liquidity_score:.2f} < threshold "
            f"{state.low_liquidity_threshold:.2f}"
        )
        risk_flags.append("low_liquidity_slippage_risk")
        return _build_report(
            MarketRegime.LOW_LIQUIDITY,
            confidence=0.85,
            reasons=reasons,
            risk_flags=risk_flags,
            operator_note=(
                "거래대금 부족 — 슬리피지 위험. 대부분 전략 신규 진입 보류 권고."
            ),
            now=now,
        )

    # 2) HIGH_VOLATILITY.
    if state.volatility_pct is not None and \
            state.volatility_pct > state.high_volatility_threshold:
        reasons.append(
            f"volatility_pct={state.volatility_pct:.4f} > threshold "
            f"{state.high_volatility_threshold:.4f}"
        )
        risk_flags.append("high_volatility_size_reduce")
        return _build_report(
            MarketRegime.HIGH_VOLATILITY,
            confidence=0.80,
            reasons=reasons,
            risk_flags=risk_flags,
            operator_note=(
                "변동성 급증 — sizing 축소 + stop-loss 민감 전략 보류 권고."
            ),
            now=now,
        )

    # 3) CHOPPY.
    if state.choppiness_index is not None and \
            state.choppiness_index > state.choppiness_threshold:
        reasons.append(
            f"choppiness_index={state.choppiness_index:.2f} > threshold "
            f"{state.choppiness_threshold:.2f}"
        )
        return _build_report(
            MarketRegime.CHOPPY,
            confidence=0.70,
            reasons=reasons,
            operator_note=(
                "무방향 + 잦은 반전 — 추세추종 전략 보류, mean-reversion 만 제한 검토."
            ),
            now=now,
        )

    # 4~6) trend direction.
    td = (state.trend_direction or "").upper()
    if td == "UP":
        reasons.append("trend_direction=UP")
        return _build_report(
            MarketRegime.TREND_UP,
            confidence=0.75,
            reasons=reasons,
            operator_note="상승 추세 — momentum / breakout 계열 우선 검토.",
            now=now,
        )
    if td == "DOWN":
        reasons.append("trend_direction=DOWN")
        risk_flags.append("downtrend_defensive")
        return _build_report(
            MarketRegime.TREND_DOWN,
            confidence=0.75,
            reasons=reasons,
            risk_flags=risk_flags,
            operator_note="하락 추세 — 공격적 신규 진입 축소, 손실 제한 우선.",
            now=now,
        )
    if td == "SIDEWAYS":
        reasons.append("trend_direction=SIDEWAYS")
        return _build_report(
            MarketRegime.SIDEWAYS,
            confidence=0.70,
            reasons=reasons,
            operator_note="횡보 — mean-reversion 계열 우선, breakout 보류.",
            now=now,
        )

    # 7) UNKNOWN.
    reasons.append("insufficient_market_state_data")
    risk_flags.append("unknown_regime_watch_only")
    return _build_report(
        MarketRegime.UNKNOWN,
        confidence=0.30,
        reasons=reasons,
        risk_flags=risk_flags,
        operator_note=(
            "장세 분류 불가 — Paper 자동 시작 금지. 운영자 수동 판단 필요."
        ),
        now=now,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. 4-02 위에 regime filter 적용
# ─────────────────────────────────────────────────────────────────────────────


def _decision_key(d: StrategyDecision) -> tuple:
    return (
        d.strategy, d.symbol,
        tuple(sorted((str(k), str(v)) for k, v in (d.params or {}).items())),
    )


def _with_regime_reason(
    d: StrategyDecision, *, new_action: StrategyAction, regime_reason: str,
) -> StrategyDecision:
    return StrategyDecision(
        strategy=d.strategy, symbol=d.symbol, params=dict(d.params),
        action=new_action,
        paper_candidate_status=d.paper_candidate_status,
        score=d.score,
        risk_flags=list(d.risk_flags),
        reasons=list(d.reasons) + [regime_reason],
    )


def apply_regime_filter(
    recommendation: StrategyCombinationRecommendation,
    regime_report:  MarketRegimeReport,
    *,
    now: datetime | None = None,
) -> StrategyCombinationRecommendation:
    """기존 추천 위에 장세 필터 적용.

    - `regime.blocked` 전략 → `recommended_combo` 에서 제거 → `excluded` 로 이동.
    - `regime.watchlist` 전략 → `recommended_combo` 에서 제거 → `held` 로 이동.
    - `regime.preferred` 전략 → 유지 (점수 가산은 본 PR 시점 미적용 — 후속 PR).
    - UNKNOWN regime: 모든 추천 후보 → watchlist (held) → `WATCH_ONLY` 효과.

    **과최적화 우선순위**: 본 함수는 *현재 recommended_combo* 만 처리 — 4-03 이
    이미 OVERFIT_RISK 를 제거했다면 본 함수가 그것을 *원복하지 않는다*.

    *원본 객체 변경 0건* — 새 dataclass 반환.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    policy = REGIME_STRATEGY_POLICY[regime_report.regime]
    blocked   = policy["blocked"]
    watchlist = policy["watchlist"]

    new_recommended: list[StrategyDecision] = []
    new_held:        list[StrategyDecision] = list(recommendation.held)
    new_excluded:    list[StrategyDecision] = list(recommendation.excluded)

    for d in recommendation.recommended_combo:
        if d.strategy in blocked:
            new_excluded.append(_with_regime_reason(
                d, new_action=StrategyAction.EXCLUDE,
                regime_reason=(
                    f"regime_filter: {regime_report.regime.value} 에서 "
                    f"{d.strategy} 차단"
                ),
            ))
        elif d.strategy in watchlist:
            new_held.append(_with_regime_reason(
                d, new_action=StrategyAction.HOLD,
                regime_reason=(
                    f"regime_filter: {regime_report.regime.value} 에서 "
                    f"{d.strategy} watchlist (보류)"
                ),
            ))
        else:
            new_recommended.append(d)

    # decisions 재구성 — 원본 순서 유지 + action 갱신.
    new_decisions: list[StrategyDecision] = []
    for d in recommendation.decisions:
        if d.action == StrategyAction.RECOMMEND and d.strategy in blocked:
            new_decisions.append(_with_regime_reason(
                d, new_action=StrategyAction.EXCLUDE,
                regime_reason=f"regime_filter: {regime_report.regime.value} blocked",
            ))
        elif d.action == StrategyAction.RECOMMEND and d.strategy in watchlist:
            new_decisions.append(_with_regime_reason(
                d, new_action=StrategyAction.HOLD,
                regime_reason=f"regime_filter: {regime_report.regime.value} watchlist",
            ))
        else:
            new_decisions.append(d)

    # Overall 재계산.
    if new_recommended:
        overall = OverallRecommendation.HAS_RECOMMENDATIONS
    elif new_held:
        overall = OverallRecommendation.ALL_HOLD
    elif new_excluded:
        overall = OverallRecommendation.NO_CANDIDATES_TODAY
    else:
        overall = OverallRecommendation.NO_CANDIDATES_TODAY

    operator_notes = list(recommendation.operator_notes)
    if regime_report.operator_note:
        operator_notes.append(f"regime: {regime_report.operator_note}")
    if regime_report.regime == MarketRegime.UNKNOWN and new_recommended:
        # UNKNOWN 이면 안전하게 추천 차단 후 watchlist 로.
        for d in new_recommended:
            new_held.append(_with_regime_reason(
                d, new_action=StrategyAction.HOLD,
                regime_reason="regime_filter: UNKNOWN — WATCH_ONLY",
            ))
        new_recommended = []
        overall = OverallRecommendation.ALL_HOLD
        operator_notes.append("UNKNOWN 장세 — Paper 자동 시작 금지, 모든 후보 보류.")

    if regime_report.regime == MarketRegime.HIGH_VOLATILITY:
        operator_notes.append("HIGH_VOLATILITY — 변동성 급증 경고. sizing 축소 권고.")
    if regime_report.regime == MarketRegime.LOW_LIQUIDITY:
        operator_notes.append("LOW_LIQUIDITY — 슬리피지 위험. 대부분 추천 차단.")

    reasons_no_candidate = list(recommendation.reasons_no_candidate)
    if not new_recommended and (blocked or watchlist):
        reasons_no_candidate.append(
            f"all_candidates_demoted_by_regime_filter:{regime_report.regime.value}"
        )

    # 장세 컨텍스트 carry (user spec 의 fields).
    regime_context = {
        "market_regime":              regime_report.regime.value,
        "regime_confidence":          float(regime_report.confidence),
        "regime_reasons":             list(regime_report.reasons),
        "regime_risk_flags":          list(regime_report.risk_flags),
        "regime_allowed_strategies":  list(regime_report.allowed_strategies),
        "regime_blocked_strategies":  list(regime_report.blocked_strategies),
        "regime_watchlist_strategies": list(regime_report.watchlist_strategies),
        "regime_operator_note":       regime_report.operator_note,
    }

    return StrategyCombinationRecommendation(
        generated_at=now.isoformat(),
        schema_version=recommendation.schema_version,
        overall_recommendation=overall,
        recommended_combo=new_recommended,
        held=new_held,
        excluded=new_excluded,
        decisions=new_decisions,
        reasons_no_candidate=reasons_no_candidate,
        operator_notes=operator_notes,
        regime_context=regime_context,
        metadata={
            **dict(recommendation.metadata),
            "regime_filter_applied":  True,
            "regime":                 regime_report.regime.value,
            "regime_confidence":      float(regime_report.confidence),
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. Agent (AgentBase 호환)
# ─────────────────────────────────────────────────────────────────────────────


_AGENT_METADATA = AgentMetadata(
    name="market_regime_agent",
    role=AgentRole.OBSERVER,
    description=(
        "시장 metric (변동성 / 거래대금 / 추세 / choppiness) 으로 7 장세를 "
        "advisory 분류. 4-02 추천 위에 장세별 필터 적용 (선호 / watchlist / "
        "차단). 본 agent 는 *주문 신호 / LLM 호출 / broker 호출* 을 수행하지 "
        "않는다 (advisory only)."
    ),
    inputs=[
        "AgentContext.extra['market_state'] (MarketStateInput) — 시장 metric.",
        "AgentContext.extra['recommendation'] (StrategyCombinationRecommendation) "
        "— 필터 적용 대상 (옵션).",
    ],
    outputs=[
        "AgentOutput(decision=OBSERVE, summary, reasons, risk_flags, "
        "metadata['regime_report'] + optional metadata['filtered_recommendation']).",
    ],
    forbidden=[
        "broker.place_order", "route_order", "OrderExecutor",
        "anthropic / openai / httpx / requests",
        "BUY / SELL / HOLD signal", "auto paper trader start",
    ],
    can_execute_order=False,
)


class MarketRegimeAgent(AgentBase):
    """Market Regime Agent — 장세 분류 + 4-02 필터 wrapper."""

    @property
    def metadata(self) -> AgentMetadata:
        return _AGENT_METADATA

    def run(self, context: AgentContext) -> AgentOutput:
        extra = context.extra or {}
        state = extra.get("market_state")
        if not isinstance(state, MarketStateInput):
            state = MarketStateInput()
        regime_report = classify_market_regime(state)

        # 옵션: 추천 객체가 함께 주입되면 필터링까지 수행.
        recommendation = extra.get("recommendation")
        filtered_dict: dict[str, Any] | None = None
        if isinstance(recommendation, StrategyCombinationRecommendation):
            filtered = apply_regime_filter(recommendation, regime_report)
            filtered_dict = filtered.to_dict()

        summary = (
            f"market regime: {regime_report.regime.value} "
            f"(confidence={regime_report.confidence:.2f}) — advisory."
        )
        reasons: list[str] = []
        reasons.append(f"regime={regime_report.regime.value}")
        for r in regime_report.reasons[:3]:
            reasons.append(f"reason: {r}")
        if regime_report.operator_note:
            reasons.append(f"operator_note: {regime_report.operator_note}")

        return AgentOutput(
            role=AgentRole.OBSERVER,
            decision=AgentDecision.OBSERVE,
            summary=summary,
            reasons=reasons,
            risk_flags=list(regime_report.risk_flags),
            metadata={
                "regime_report":              regime_report.to_dict(),
                "filtered_recommendation":    filtered_dict,
                "advisory_only":              True,
                "is_order_signal":            False,
                "auto_apply_allowed":         False,
                "is_live_authorization":      False,
                "auto_start_paper_trader":    False,
            },
        )
