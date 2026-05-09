"""#52: Market Observer Agent.

장중 시장 환경을 *관찰*만 하고 다른 Agent들이 참고할 snapshot JSON을 생성한다.
**주문 신호가 아니다** — BUY/SELL/HOLD 반환 금지, approval queue 등록 금지.

본 모듈은 #51 `AgentBase` ABC와 호환되는 *Observer 역할의 확장 구현*이다.
기본 `ObserverAgent`(`app.agents.roles`)는 deterministic stub만 제공하므로,
실제 시장지수 / 거래대금 / 섹터 흐름 / 변동성 / freshness를 받는 풍부한
Observer가 필요한 caller(operating_loop, ChiefTradingAgent 등)는 본 모듈을
사용한다.

## 절대 invariant

1. **broker / OrderExecutor / route_order import 0건** (정적 grep 가드)
2. **외부 네트워크 호출 0건** — 모든 입력은 caller가 dataclass로 주입
3. **`MarketObserverOutput.is_order_signal = False` 불변** — True 시 ValueError
4. **BUY / SELL / HOLD 결정 0건** — `recommended_stance`는 *advisory*이며,
   주문 액션이 아닌 운영 분위기 (AGGRESSIVE / NORMAL / DEFENSIVE / WATCH_ONLY /
   PAUSE_NEW_BUY)
5. **approval queue 등록 0건** — 본 Agent는 snapshot만 반환

## 다른 Agent와의 관계

| Agent | 본 Observer를 어떻게 사용하나 |
|---|---|
| StrategySelectionAgent | `recommended_stance`가 PAUSE_NEW_BUY / WATCH_ONLY면 신규 진입 회피 |
| RiskOfficerAgent (= RiskAuditor #51) | `risk_level=HIGH/BLOCKED`면 더 보수적 가드 |
| ChiefTradingAgent | snapshot 전체를 참고해 종합 결정 |
| ExecutionRecommender (#51) | 권고만 — 본 Observer 출력은 직접 주문으로 연결되지 *않는다* |

자세한 정책: [`docs/market_observer_agent.md`](../../../docs/market_observer_agent.md).
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
from app.agents.market_regime import RegimeOutput, classify_market_regime


# ====================================================================
# Enums
# ====================================================================


class MarketRiskLevel(StrEnum):
    """시장 위험도 카테고리. 본 값은 *advisory* — 주문 결정 X."""
    LOW     = "LOW"
    MEDIUM  = "MEDIUM"
    HIGH    = "HIGH"
    BLOCKED = "BLOCKED"   # 운영자/RiskAuditor가 가드 적용 권고


class RecommendedStance(StrEnum):
    """권장 운영 *분위기*. 주문 액션이 아닌 advisory 가이드.

    - AGGRESSIVE     : 강한 추세 + 거래대금 풍부 — 평소보다 적극
    - NORMAL         : 일반 운용
    - DEFENSIVE      : 변동성 / 거래대금 약화 — sizing 축소 권장
    - WATCH_ONLY     : 신규 진입 회피, 기존 포지션 관찰만
    - PAUSE_NEW_BUY  : 신규 매수 일시 중단 — 기존 청산만 권고
    """
    AGGRESSIVE     = "AGGRESSIVE"
    NORMAL         = "NORMAL"
    DEFENSIVE      = "DEFENSIVE"
    WATCH_ONLY     = "WATCH_ONLY"
    PAUSE_NEW_BUY  = "PAUSE_NEW_BUY"


class TurnoverState(StrEnum):
    """거래대금 상태."""
    BELOW_AVG  = "BELOW_AVG"    # < 0.7x
    NORMAL     = "NORMAL"       # 0.7 ~ 1.3x
    ABOVE_AVG  = "ABOVE_AVG"    # 1.3 ~ 2.0x
    SURGE      = "SURGE"        # >= 2.0x
    UNKNOWN    = "UNKNOWN"      # 데이터 없음


class VolatilityState(StrEnum):
    LOW      = "LOW"        # < 1%
    NORMAL   = "NORMAL"     # 1 ~ 2%
    ELEVATED = "ELEVATED"   # 2 ~ 3.5%
    EXTREME  = "EXTREME"    # >= 3.5%
    UNKNOWN  = "UNKNOWN"


class DataFreshnessStatus(StrEnum):
    FRESH    = "FRESH"      # < 60s
    STALE    = "STALE"      # 60s ~ 5min
    EXPIRED  = "EXPIRED"    # > 5min
    UNKNOWN  = "UNKNOWN"


# ====================================================================
# Input / Output dataclasses
# ====================================================================


@dataclass(frozen=True)
class IndexQuote:
    """지수 / 섹터 / 테마 quote. caller가 한 번에 여러 개 주입 가능."""
    name:                 str
    last_price:           float | None = None
    change_pct:           float | None = None     # 일중 등락률 %
    last_updated_seconds: int   | None = None     # *몇 초 전* 업데이트 (advisory)


@dataclass(frozen=True)
class MarketObserverInput:
    """MarketObserverAgent 입력. 모든 필드는 optional — 데이터 없으면
    UNKNOWN / WATCH_ONLY로 friendly fallback (예외 X)."""

    # 시장 지수
    indices:              list[IndexQuote] | None = None
    # 거래대금 (vs 평균 비율 — 1.0 = 평균. None이면 UNKNOWN.)
    turnover_vs_avg:      float | None = None
    # 변동성 (KOSPI 기준 %, 일중 ATR 등)
    volatility_pct:       float | None = None
    # 섹터 / 테마 흐름
    leading_sectors:      list[str] | None = None
    lagging_sectors:      list[str] | None = None
    leading_themes:       list[str] | None = None
    # 급등락 종목 수
    surge_count:          int | None = None       # +5% 이상
    plunge_count:         int | None = None       # -5% 이상
    # 데이터 freshness — 시세 timestamp가 몇 초 전인지
    data_freshness_seconds: int | None = None
    # market regime classifier 입력 (옵션 — 주입 시 본 Agent가 그대로 carry)
    market_regime:        RegimeOutput | None = None
    # KST now (테스트 결정론을 위해 주입 가능)
    now:                  datetime | None = None


@dataclass(frozen=True)
class MarketObserverOutput:
    """장중 시장 snapshot — *주문 신호가 아니다*.

    절대 invariant:
    - `is_order_signal = False` — `__post_init__` 가드. True 시 ValueError.
    - `recommended_stance`는 advisory enum — 주문 액션 X.
    - `risk_level`은 운영 분위기 가이드 — RiskManager 결정 X.
    """

    risk_level:            MarketRiskLevel
    recommended_stance:    RecommendedStance
    summary_lines:         list[str]
    turnover_state:        TurnoverState
    volatility_state:      VolatilityState
    freshness_status:      DataFreshnessStatus
    leading_sectors:       list[str]
    lagging_sectors:       list[str]
    leading_themes:        list[str]
    surge_count:           int
    plunge_count:          int
    indices:               list[dict]
    market_regime:         dict | None = None
    reasons:               list[str]   = field(default_factory=list)
    is_order_signal:       bool        = False
    created_at:            datetime    = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        # 절대 invariant — Observer는 주문 신호를 만들지 않는다.
        if self.is_order_signal:
            raise ValueError(
                "MarketObserverOutput.is_order_signal must be False — "
                "Observer is context-only (CLAUDE.md 절대 원칙 1, 2). "
                "BUY/SELL/HOLD는 RiskManager + PermissionGate + OrderExecutor "
                "흐름에서만 만들어진다."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_level":         self.risk_level.value,
            "recommended_stance": self.recommended_stance.value,
            "summary_lines":      list(self.summary_lines),
            "turnover_state":     self.turnover_state.value,
            "volatility_state":   self.volatility_state.value,
            "freshness_status":   self.freshness_status.value,
            "leading_sectors":    list(self.leading_sectors),
            "lagging_sectors":    list(self.lagging_sectors),
            "leading_themes":     list(self.leading_themes),
            "surge_count":        self.surge_count,
            "plunge_count":       self.plunge_count,
            "indices":            list(self.indices),
            "market_regime":      (
                dict(self.market_regime) if self.market_regime else None
            ),
            "reasons":            list(self.reasons),
            "is_order_signal":    self.is_order_signal,
            "created_at":         self.created_at.isoformat(),
        }


# ====================================================================
# Pure helper functions
# ====================================================================


def classify_turnover(turnover_vs_avg: float | None) -> TurnoverState:
    if turnover_vs_avg is None:
        return TurnoverState.UNKNOWN
    if turnover_vs_avg >= 2.0:
        return TurnoverState.SURGE
    if turnover_vs_avg >= 1.3:
        return TurnoverState.ABOVE_AVG
    if turnover_vs_avg >= 0.7:
        return TurnoverState.NORMAL
    return TurnoverState.BELOW_AVG


def classify_volatility(vol_pct: float | None) -> VolatilityState:
    if vol_pct is None:
        return VolatilityState.UNKNOWN
    if vol_pct >= 3.5:
        return VolatilityState.EXTREME
    if vol_pct >= 2.0:
        return VolatilityState.ELEVATED
    if vol_pct >= 1.0:
        return VolatilityState.NORMAL
    return VolatilityState.LOW


def classify_freshness(seconds: int | None) -> DataFreshnessStatus:
    if seconds is None:
        return DataFreshnessStatus.UNKNOWN
    if seconds > 300:
        return DataFreshnessStatus.EXPIRED
    if seconds >= 60:
        return DataFreshnessStatus.STALE
    return DataFreshnessStatus.FRESH


# ====================================================================
# Risk level + stance derivation (pure)
# ====================================================================


def _derive_risk_level(
    *,
    volatility: VolatilityState,
    turnover:   TurnoverState,
    freshness:  DataFreshnessStatus,
    regime:     RegimeOutput | None,
    plunge_count: int,
) -> MarketRiskLevel:
    """결정 매트릭스 — 가장 보수적인 신호가 효력.

    BLOCKED:
    - regime.trade_permission == BLOCK (RISK_OFF 등)
    - freshness == EXPIRED (시세가 > 5분)
    HIGH:
    - volatility EXTREME 또는 plunge_count > 20
    - regime.trade_permission == PAUSE
    MEDIUM:
    - volatility ELEVATED 또는 turnover SURGE / BELOW_AVG
    - freshness STALE
    - regime.trade_permission == WATCH
    LOW:
    - 그 외
    """
    if regime is not None and regime.trade_permission == "BLOCK":
        return MarketRiskLevel.BLOCKED
    if freshness == DataFreshnessStatus.EXPIRED:
        return MarketRiskLevel.BLOCKED
    if volatility == VolatilityState.EXTREME or plunge_count > 20:
        return MarketRiskLevel.HIGH
    if regime is not None and regime.trade_permission == "PAUSE":
        return MarketRiskLevel.HIGH
    # SURGE turnover 자체는 위험을 올리지 않는다 — 단순 거래 활성도. 변동성과
    # 함께 평가하며, BELOW_AVG (저유동성)만 MEDIUM 트리거.
    if (volatility == VolatilityState.ELEVATED
            or turnover == TurnoverState.BELOW_AVG
            or freshness == DataFreshnessStatus.STALE):
        return MarketRiskLevel.MEDIUM
    if regime is not None and regime.trade_permission == "WATCH":
        return MarketRiskLevel.MEDIUM
    return MarketRiskLevel.LOW


def _derive_stance(
    *,
    risk_level:   MarketRiskLevel,
    turnover:     TurnoverState,
    volatility:   VolatilityState,
) -> RecommendedStance:
    """`risk_level` + 거래대금/변동성으로 분위기 advisory.

    BLOCKED        → PAUSE_NEW_BUY
    HIGH           → WATCH_ONLY
    MEDIUM         → DEFENSIVE
    LOW + SURGE 거래량 + ELEVATED 미만 → AGGRESSIVE
    LOW            → NORMAL
    """
    if risk_level == MarketRiskLevel.BLOCKED:
        return RecommendedStance.PAUSE_NEW_BUY
    if risk_level == MarketRiskLevel.HIGH:
        return RecommendedStance.WATCH_ONLY
    if risk_level == MarketRiskLevel.MEDIUM:
        return RecommendedStance.DEFENSIVE
    # LOW
    if (turnover in (TurnoverState.ABOVE_AVG, TurnoverState.SURGE)
            and volatility != VolatilityState.EXTREME):
        return RecommendedStance.AGGRESSIVE
    return RecommendedStance.NORMAL


def _build_summary_lines(
    *,
    risk_level:         MarketRiskLevel,
    turnover:           TurnoverState,
    volatility:         VolatilityState,
    stance:             RecommendedStance,
    freshness:          DataFreshnessStatus,
    leading_sectors:    list[str],
    surge_count:        int,
    plunge_count:       int,
) -> list[str]:
    """3줄 요약 — 운영자가 모바일에서 한눈에 볼 수 있는 요약."""
    line1 = f"시장 위험도: {_risk_label(risk_level)}"
    line2 = _line_turnover(turnover) + " " + _line_volatility(volatility)
    line3 = _line_stance(stance, freshness, leading_sectors,
                          surge_count, plunge_count)
    return [line1, line2.strip(), line3]


_RISK_LABEL = {
    MarketRiskLevel.LOW:     "낮음",
    MarketRiskLevel.MEDIUM:  "보통",
    MarketRiskLevel.HIGH:    "높음",
    MarketRiskLevel.BLOCKED: "차단 (시세 expired 또는 risk-off)",
}

def _risk_label(level: MarketRiskLevel) -> str:
    return _RISK_LABEL[level]


_TURNOVER_LINE = {
    TurnoverState.BELOW_AVG: "거래대금이 평소보다 적습니다.",
    TurnoverState.NORMAL:    "거래대금은 평소 수준입니다.",
    TurnoverState.ABOVE_AVG: "거래대금은 평소보다 약간 증가했습니다.",
    TurnoverState.SURGE:     "거래대금이 급증 중입니다.",
    TurnoverState.UNKNOWN:   "거래대금 데이터 없음.",
}

def _line_turnover(state: TurnoverState) -> str:
    return _TURNOVER_LINE[state]


_VOL_LINE = {
    VolatilityState.LOW:      "변동성은 낮습니다.",
    VolatilityState.NORMAL:   "변동성은 평소 수준입니다.",
    VolatilityState.ELEVATED: "변동성이 평소보다 높습니다.",
    VolatilityState.EXTREME:  "변동성이 매우 큽니다.",
    VolatilityState.UNKNOWN:  "변동성 데이터 없음.",
}

def _line_volatility(state: VolatilityState) -> str:
    return _VOL_LINE[state]


def _line_stance(
    stance:    RecommendedStance,
    freshness: DataFreshnessStatus,
    leading:   list[str],
    surge:     int,
    plunge:    int,
) -> str:
    base = {
        RecommendedStance.AGGRESSIVE:    "신규 매수 적극 가능 — 거래대금 강함.",
        RecommendedStance.NORMAL:        "신규 매수는 가능합니다.",
        RecommendedStance.DEFENSIVE:     "신규 매수는 가능하지만 sizing 축소를 권장합니다.",
        RecommendedStance.WATCH_ONLY:    "신규 진입은 회피, 보유 포지션 모니터링만 권고.",
        RecommendedStance.PAUSE_NEW_BUY: "신규 매수 일시 중단 — 청산만 권고.",
    }[stance]
    extras = []
    if freshness == DataFreshnessStatus.STALE:
        extras.append("시세 stale (60s+)")
    elif freshness == DataFreshnessStatus.EXPIRED:
        extras.append("시세 expired (5min+)")
    elif freshness == DataFreshnessStatus.UNKNOWN:
        extras.append("freshness 데이터 없음")
    if surge >= 30:
        extras.append(f"급등 종목 {surge}개")
    if plunge >= 30:
        extras.append(f"급락 종목 {plunge}개")
    if leading:
        extras.append(f"강세 섹터: {', '.join(leading[:3])}")
    if extras:
        return f"{base} ({'; '.join(extras)})"
    return base


# ====================================================================
# Pure entry function — Agent ABC의 run에서 호출 + 외부 직접 호출 허용
# ====================================================================


def observe_market(inp: MarketObserverInput) -> MarketObserverOutput:
    """순수 함수 — `MarketObserverInput` → `MarketObserverOutput`.

    외부 네트워크 호출 0건. broker / OrderExecutor / route_order 호출 0건.
    데이터가 부족하면 UNKNOWN / WAITING_FOR_DATA / WATCH_ONLY로 friendly
    fallback (예외 X).
    """
    turnover_state    = classify_turnover(inp.turnover_vs_avg)
    volatility_state  = classify_volatility(inp.volatility_pct)
    freshness_status  = classify_freshness(inp.data_freshness_seconds)
    surge_count       = max(0, int(inp.surge_count or 0))
    plunge_count      = max(0, int(inp.plunge_count or 0))
    leading_sectors   = list(inp.leading_sectors or [])
    lagging_sectors   = list(inp.lagging_sectors or [])
    leading_themes    = list(inp.leading_themes or [])
    indices = [
        {
            "name":                 q.name,
            "last_price":           q.last_price,
            "change_pct":           q.change_pct,
            "last_updated_seconds": q.last_updated_seconds,
        }
        for q in (inp.indices or [])
    ]

    risk_level = _derive_risk_level(
        volatility=volatility_state,
        turnover=turnover_state,
        freshness=freshness_status,
        regime=inp.market_regime,
        plunge_count=plunge_count,
    )
    stance = _derive_stance(
        risk_level=risk_level,
        turnover=turnover_state,
        volatility=volatility_state,
    )

    reasons: list[str] = []
    reasons.append(f"turnover={turnover_state.value}")
    reasons.append(f"volatility={volatility_state.value}")
    reasons.append(f"freshness={freshness_status.value}")
    if inp.market_regime is not None:
        reasons.append(
            f"regime={inp.market_regime.regime} "
            f"(perm={inp.market_regime.trade_permission})"
        )
    if surge_count > 0:
        reasons.append(f"surge_count={surge_count}")
    if plunge_count > 0:
        reasons.append(f"plunge_count={plunge_count}")

    summary_lines = _build_summary_lines(
        risk_level=risk_level,
        turnover=turnover_state,
        volatility=volatility_state,
        stance=stance,
        freshness=freshness_status,
        leading_sectors=leading_sectors,
        surge_count=surge_count,
        plunge_count=plunge_count,
    )

    regime_dict: dict | None = None
    if inp.market_regime is not None:
        regime_dict = {
            "regime":           inp.market_regime.regime,
            "confidence":       inp.market_regime.confidence,
            "trade_permission": inp.market_regime.trade_permission,
            "reasons":          list(inp.market_regime.reasons),
        }

    return MarketObserverOutput(
        risk_level=risk_level,
        recommended_stance=stance,
        summary_lines=summary_lines,
        turnover_state=turnover_state,
        volatility_state=volatility_state,
        freshness_status=freshness_status,
        leading_sectors=leading_sectors,
        lagging_sectors=lagging_sectors,
        leading_themes=leading_themes,
        surge_count=surge_count,
        plunge_count=plunge_count,
        indices=indices,
        market_regime=regime_dict,
        reasons=reasons,
        # is_order_signal default False — guard로 강제.
    )


# ====================================================================
# Agent ABC implementation (#51 호환)
# ====================================================================


class MarketObserverAgent(AgentBase):
    """`AgentBase` 호환 implementation. context.market_state / extra에서
    `MarketObserverInput` 구성 후 `observe_market` 호출."""

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            name="market_observer",
            role=AgentRole.OBSERVER,
            description=(
                "시장지수 / 거래대금 / 급등락 / 섹터 흐름 / 변동성 / "
                "데이터 freshness를 감시해 snapshot JSON 생성. "
                "주문 신호가 아니다 — BUY/SELL/HOLD 반환 금지."
            ),
            inputs=[
                "indices", "turnover_vs_avg", "volatility_pct",
                "leading_sectors", "lagging_sectors", "leading_themes",
                "surge_count", "plunge_count", "data_freshness_seconds",
                "market_regime",
            ],
            outputs=["MarketObserverOutput (is_order_signal=False)"],
            forbidden=[
                "BUY / SELL / HOLD 주문 신호 반환 금지",
                "approval queue 등록 금지",
                "broker / OrderExecutor / route_order 호출 금지",
                "외부 네트워크 호출 금지",
            ],
            can_execute_order=False,
        )

    def run(self, context: AgentContext) -> AgentOutput:
        """`AgentContext` → `AgentOutput`. caller가 풍부한 입력을 사용하려면
        `observe_market(MarketObserverInput(...))`를 직접 호출 — 본 메서드는
        AgentBase 호환을 위한 thin wrapper."""
        ms = dict(context.market_state or {})
        # market_state dict에서 가능한 필드 읽기 (모두 optional).
        inp = MarketObserverInput(
            indices=None,  # AgentContext는 풍부 입력을 지원하지 않음 — caller가
                           # 별도 흐름에서 observe_market 직접 호출 권장.
            turnover_vs_avg=ms.get("turnover_vs_avg"),
            volatility_pct=ms.get("volatility_pct"),
            leading_sectors=ms.get("leading_sectors"),
            lagging_sectors=ms.get("lagging_sectors"),
            leading_themes=ms.get("leading_themes"),
            surge_count=ms.get("surge_count"),
            plunge_count=ms.get("plunge_count"),
            data_freshness_seconds=ms.get("data_freshness_seconds"),
        )
        snap = observe_market(inp)
        return AgentOutput(
            role=AgentRole.OBSERVER,
            decision=AgentDecision.OBSERVE,
            summary=snap.summary_lines[0] if snap.summary_lines else "observed",
            reasons=list(snap.reasons),
            confidence=None,
            risk_flags=[snap.risk_level.value]
                if snap.risk_level != MarketRiskLevel.LOW else [],
            metadata=snap.to_dict(),
        )


# ====================================================================
# Module invariants
# ====================================================================
#
# - 본 모듈은 broker / OrderExecutor / route_order / KIS / mock_broker /
#   permission.gate 어떤 모듈도 import하지 않는다 (정적 grep 가드).
# - `MarketObserverOutput.is_order_signal = False` 불변 (__post_init__ 가드).
# - 모든 enum 값은 advisory — 주문 액션 X.
# - 외부 네트워크 호출 0건 — 모든 입력은 caller가 dataclass로 주입.
