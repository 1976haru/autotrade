"""#49: FuturesStrategyBase contract.

선물 전략의 공식 ABC. 주식 [`app.strategies.base.Strategy`](../../strategies/base.py)
(#28)와 **분리**된 별개 계층 — 선물은 양방향 포지션, *계약 수*, 증거금,
레버리지, 만기, 롤오버, 틱가치 같은 차원이 신호 시그니처에 반영되어야 하기
때문. 단일 ABC로 통합하면 모든 메서드가 optional 필드로 부풀고, 운영자가
주식/선물 신호를 혼동할 위험이 커진다.

## 분리 원칙

| 항목 | 주식 (`app.strategies`) | 선물 (`app.futures.strategies`, 본 모듈) |
|---|---|---|
| ABC | `Strategy` / `StrategyBase` | `FuturesStrategyBase` |
| signal | `StrategySignal(action, sizing_hint, ...)` | `FuturesSignal(action, contract, contract_sizing, exit_plan, rollover, ...)` |
| sizing | 주식 수 (`SizingHint.quantity`) | 계약 수 (`FuturesContractSizingHint.contracts`) |
| exit | `ExitPlan` (% 기반) | `FuturesExitPlan` (% + ticks + leverage-aware) |
| 만기/롤오버 | 없음 | `FuturesRolloverPlan` (close+open 페어, advisory) |
| 양방향 | 단방향 (BUY/SELL) | 양방향 (LONG/SHORT 진입 + 명시 청산) |

## 절대 invariant (테스트로 강제)

1. `FuturesStrategyBase`는 주식 `Strategy` / `StrategyBase`를 상속하지 않는다 —
   MRO 분리. `tests/test_futures_strategies.py::test_*_does_not_inherit_stock_strategy`.
2. 본 모듈은 broker / OrderExecutor / route_order / KIS / mock_broker 어떤
   것도 import하지 않는다 — 정적 grep 가드.
3. 모든 `FuturesSignal`은 `is_order_intent = False` (불변) — 신호는 *추천*
   이며, 실제 주문은 `FuturesRiskManager` + `AiPermissionGate`(#39) +
   `FuturesMarginRule`(#48)을 통과한 뒤 별도 흐름에서만 만들어진다.
4. 본 PR 시점 모든 mock 전략은 *최대 1계약* 권장 — `contracts ≤ 1`.
5. 자동 롤오버 *주문* 발신 코드 0건 — `FuturesRolloverPlan`은 advisory plan
   dataclass일 뿐이며, broker 호출을 트리거하지 않는다.

## 관련 문서

- [`docs/futures_strategy_contract.md`](../../../../docs/futures_strategy_contract.md) — 본 contract의 정책 (#49)
- [`docs/futures_scope.md`](../../../../docs/futures_scope.md) — 선물 1차 범위 (#46)
- [`docs/futures_broker_contract.md`](../../../../docs/futures_broker_contract.md) — `FuturesBrokerAdapter` (#47)
- [`docs/futures_margin_risk.md`](../../../../docs/futures_margin_risk.md) — Margin/Leverage/Liquidation rules (#48)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.backtest.types import Bar


# ====================================================================
# Signal action — 양방향 LONG/SHORT 진입 + 명시 청산
# ====================================================================


class FuturesSignalAction(StrEnum):
    """선물 신호 종류. 주식의 `SignalAction`과 *별개* enum — 선물은 LONG/SHORT
    진입을 분리해 표현하고, HEDGE(헤지 진입)와 ROLLOVER(만기 임박 advisory)를
    추가한다.

    어떤 값도 broker 주문 *결정*을 의미하지 않는다 — Strategy는 *추천*만 한다.
    """
    OPEN_LONG    = "OPEN_LONG"     # 신규 LONG 진입 후보
    OPEN_SHORT   = "OPEN_SHORT"    # 신규 SHORT 진입 후보
    CLOSE_LONG   = "CLOSE_LONG"    # 보유 LONG 청산 후보
    CLOSE_SHORT  = "CLOSE_SHORT"   # 보유 SHORT 청산 후보
    HEDGE        = "HEDGE"         # 헤지 진입 후보 (equity exposure 보정)
    ROLLOVER     = "ROLLOVER"      # 만기 임박 — close 근월물 + open 차월물 advisory
    REDUCE_SIZE  = "REDUCE_SIZE"   # 위험도 증가 — 계약 수 축소 advisory
    WATCH        = "WATCH"         # 모니터링 (조건 근접)
    NO_SIGNAL    = "NO_SIGNAL"     # 무신호


# ====================================================================
# Sizing — 계약 수 기반
# ====================================================================


@dataclass(frozen=True)
class FuturesContractSizingHint:
    """권장 계약 수. 최종 계약 수는 `FuturesRiskManager` + `FuturesMarginRule`
    (#48)이 결정 — Strategy는 *추천*만 한다.

    필드:
    - `contracts` — 권장 계약 수 (정수). 본 PR 시점 mock 전략은 모두 ≤ 1.
    - `risk_pct_of_equity` — equity 대비 위험 한도(%). stop loss 도달 시 손실이
      자본의 X% 이하 권장.
    - `max_leverage_hint` — Strategy가 권장하는 leverage. 정책 한도(`FuturesRiskPolicy.
      max_leverage`)와 contract 시장 한도 중 작은 값과 다시 비교.
    - `reduce_only` — 청산 의도. True면 호출자가 close-only로 처리.
    - `note` — 사람이 읽을 수 있는 한 줄 사유.
    """

    contracts:           int   = 1
    risk_pct_of_equity:  float | None = None
    max_leverage_hint:   float | None = None
    reduce_only:         bool  = False
    note:                str | None = None

    def __post_init__(self) -> None:
        if self.contracts < 0:
            raise ValueError(f"contracts must be >= 0, got {self.contracts}")
        if self.contracts > 1:
            # 본 PR 안전 가드 — mock 전략 + 초기 단계는 1계약 이하만 권장.
            # 운영자가 의도적으로 초과하려면 별도 옵트인 PR이 필요하다.
            raise ValueError(
                f"contracts must be <= 1 in #49 mock phase, got {self.contracts}. "
                "Future strategies must use small contract sizes until margin "
                "reconciliation is complete (see docs/futures_strategy_contract.md §4)."
            )

    def to_dict(self) -> dict:
        return {
            "contracts":          self.contracts,
            "risk_pct_of_equity": self.risk_pct_of_equity,
            "max_leverage_hint":  self.max_leverage_hint,
            "reduce_only":        self.reduce_only,
            "note":               self.note,
        }


# ====================================================================
# Exit plan — leverage-aware
# ====================================================================


@dataclass(frozen=True)
class FuturesExitPlan:
    """선물 청산 계획. 주식 `ExitPlan`과 별개 — leverage / liquidation 거리에
    민감해 추가 필드가 필요.

    필드:
    - `take_profit_pct` / `stop_loss_pct` — 진입가 기준 % (LONG/SHORT 모두 절대값)
    - `take_profit_ticks` / `stop_loss_ticks` — 호가 단위 기반 (선물 호가 단위표)
    - `time_exit_bars` — N봉 후 자동 청산 advisory
    - `liquidation_buffer_pct` — `LiquidationRiskRule`(#48) 임계 referencing
      운영자 가이드 — 본 값보다 mark가 가까워지면 운영자에게 경고
    - `invalidation` / `rule_summary` — 사람이 읽을 한 줄
    """

    take_profit_pct:        float | None = None
    stop_loss_pct:          float | None = None
    take_profit_ticks:      int | None   = None
    stop_loss_ticks:        int | None   = None
    time_exit_bars:         int | None   = None
    liquidation_buffer_pct: float | None = None
    invalidation:           str | None   = None
    rule_summary:           str | None   = None

    def to_dict(self) -> dict:
        return {
            "take_profit_pct":        self.take_profit_pct,
            "stop_loss_pct":          self.stop_loss_pct,
            "take_profit_ticks":      self.take_profit_ticks,
            "stop_loss_ticks":        self.stop_loss_ticks,
            "time_exit_bars":         self.time_exit_bars,
            "liquidation_buffer_pct": self.liquidation_buffer_pct,
            "invalidation":           self.invalidation,
            "rule_summary":           self.rule_summary,
        }


# ====================================================================
# Rollover plan — advisory only, no auto execution
# ====================================================================


@dataclass(frozen=True)
class FuturesRolloverPlan:
    """근월물 → 차월물 롤오버 advisory plan. **broker 호출을 트리거하지 않는다**
    (`futures_broker_contract.md`(#47) §8 자동 롤오버 금지 invariant).

    Strategy가 만기 임박을 감지하면 본 plan을 함께 반환하고, 운영자가 close +
    open을 *수동으로* 결정한다. 본 dataclass의 어떤 필드도 *주문 객체*가 아니다.

    필드:
    - `close_contract` — 청산할 근월물 code
    - `open_contract` — 신규 진입할 차월물 code
    - `days_to_expiry` — 잔존 일수 (advisory — 영업일 캘린더 미반영, 근사)
    - `recommended_window` — 권장 롤오버 시간대 (예: "expiry-7d ~ expiry-3d")
    - `rule_summary` — 사람이 읽을 한 줄
    """

    close_contract:      str
    open_contract:       str
    days_to_expiry:      int | None = None
    recommended_window:  str | None = None
    rule_summary:        str | None = None

    def to_dict(self) -> dict:
        return {
            "close_contract":      self.close_contract,
            "open_contract":       self.open_contract,
            "days_to_expiry":      self.days_to_expiry,
            "recommended_window":  self.recommended_window,
            "rule_summary":        self.rule_summary,
        }


# ====================================================================
# Explanation
# ====================================================================


@dataclass(frozen=True)
class FuturesSignalExplanation:
    """사람이 읽을 수 있는 신호 설명. audit / Agent context로 carry 가능."""

    summary:        str
    reasons:        list[str] = field(default_factory=list)
    confidence:     int | None = None     # 0~100
    indicators:     dict | None = None    # 자유 raw indicator dict
    required_regime: str | None = None
    risk_note:      str | None = None     # leverage / margin 관련 주의사항

    def to_dict(self) -> dict:
        return {
            "summary":         self.summary,
            "reasons":         list(self.reasons),
            "confidence":      self.confidence,
            "indicators":      dict(self.indicators) if self.indicators else None,
            "required_regime": self.required_regime,
            "risk_note":       self.risk_note,
        }


# ====================================================================
# Strategy context / signal / validation
# ====================================================================


@dataclass(frozen=True)
class FuturesStrategyContext:
    """`FuturesStrategyBase.generate_signal` 입력.

    필드:
    - `bars` — 최근 봉 (선물 contract 기준)
    - `contract` — 현재 평가 대상 contract code (예: `"KOSPI200_2503"`)
    - `expiry` — contract 만기 datetime (KST 권장). `FuturesContractSpec.expiry`
      에서 carry된다.
    - `account_equity` — 운영자 자본 (sizing 산출 용)
    - `current_position_contracts` — 보유 계약 수 (LONG positive / SHORT negative)
    - `equity_exposure_krw` — equity 노출 (헤지 전략 입력)
    - `regime` — MarketRegime classifier 결과 (advisory)
    - `extra` — 자유 dict (Agent / Quality 등)
    """

    bars:                       list[Bar]
    contract:                   str | None = None
    expiry:                     datetime | None = None
    account_equity:             int | None = None
    current_position_contracts: int = 0
    equity_exposure_krw:        int | None = None
    regime:                     str | None = None
    extra:                      dict | None = None


@dataclass(frozen=True)
class FuturesValidationResult:
    """Strategy가 context를 받을 수 있는지 사전 점검."""
    ok:        bool
    reasons:   list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "reasons": list(self.reasons)}


@dataclass(frozen=True)
class FuturesSignal:
    """`FuturesStrategyBase.generate_signal`의 결과.

    **절대 주문 객체가 아님** — `is_order_intent = False` 불변. 호출자는
    `FuturesRiskManager` → `AiPermissionGate`(#39) → `FuturesMarginRule`(#48) →
    (LIVE 어댑터, 본 PR 미존재)를 거쳐야 실제 주문이 만들어진다.
    """

    action:          FuturesSignalAction
    contract:        str | None = None
    contract_sizing: FuturesContractSizingHint | None = None
    exit_plan:       FuturesExitPlan | None = None
    rollover:        FuturesRolloverPlan | None = None
    explanation:     FuturesSignalExplanation | None = None
    # invariant — Strategy 신호는 *주문 의도가 아니다*. 항상 False; 코드/테스트
    # 로 강제. 호출자가 True로 바꾸려면 별도 옵트인 PR이 필요.
    is_order_intent: bool = False

    def __post_init__(self) -> None:
        if self.is_order_intent:
            raise ValueError(
                "FuturesSignal.is_order_intent must be False — strategies are "
                "advisory only (CLAUDE.md 절대 원칙 1, 2). To execute an order, "
                "go through FuturesRiskManager + AiPermissionGate (#39) + "
                "FuturesMarginRule (#48)."
            )

    def to_dict(self) -> dict:
        return {
            "action":          self.action.value,
            "contract":        self.contract,
            "contract_sizing": (
                self.contract_sizing.to_dict() if self.contract_sizing else None
            ),
            "exit_plan":       self.exit_plan.to_dict() if self.exit_plan else None,
            "rollover":        self.rollover.to_dict() if self.rollover else None,
            "explanation":     self.explanation.to_dict() if self.explanation else None,
            "is_order_intent": self.is_order_intent,
        }


# ====================================================================
# Abstract base class
# ====================================================================


@dataclass(frozen=True)
class FuturesStrategyMetadata:
    """Strategy 식별 메타데이터.

    `kind`는 사람이 읽을 분류 라벨 (예: `"trend"`, `"breakout"`, `"hedge"`).
    `category` 자유 dict — 예: `{"market": "domestic_futures", "horizon": "intraday"}`.
    """
    name:        str
    kind:        str
    description: str
    category:    dict | None = None

    def to_dict(self) -> dict:
        return {
            "name":        self.name,
            "kind":        self.kind,
            "description": self.description,
            "category":    dict(self.category) if self.category else None,
        }


class FuturesStrategyBase(ABC):
    """선물 전용 Strategy ABC.

    구현체는 4개 메서드를 채운다:
    - `metadata` (property) — 식별 정보
    - `validate(context)` — context 사용 가능 여부 사전 점검
    - `generate_signal(context)` — 핵심 — 양방향 LONG/SHORT/HEDGE/ROLLOVER 후보
    - `explain(context, signal)` — 운영자/Agent용 사람이 읽을 설명

    절대 invariant:
    - 본 ABC를 상속한 어떤 클래스도 broker / OrderExecutor / route_order를
      직접 호출해서는 안 된다.
    - 모든 `FuturesSignal`은 `is_order_intent = False` (dataclass 자체 가드).
    - 본 PR 시점 모든 mock 전략은 `contracts ≤ 1` (`FuturesContractSizingHint`
      자체 가드).
    """

    @property
    @abstractmethod
    def metadata(self) -> FuturesStrategyMetadata:
        raise NotImplementedError

    def validate(
        self, context: FuturesStrategyContext,
    ) -> FuturesValidationResult:
        """default — bars가 비어 있지 않은지만 검사. 구현체가 override 가능."""
        if not context.bars:
            return FuturesValidationResult(ok=False, reasons=["bars is empty"])
        return FuturesValidationResult(ok=True)

    @abstractmethod
    def generate_signal(
        self, context: FuturesStrategyContext,
    ) -> FuturesSignal:
        raise NotImplementedError

    def explain(
        self, context: FuturesStrategyContext, signal: FuturesSignal,
    ) -> FuturesSignalExplanation | None:
        """default — signal에 explanation이 이미 있으면 그대로 반환."""
        return signal.explanation


# ====================================================================
# Module invariants
# ====================================================================
#
# - 본 모듈은 `app.brokers.*` / `app.execution.executor` / `app.execution.order_router` /
#   `app.futures.mock` 어떤 모듈도 import하지 않는다. 신호는 *순수 의사결정*.
# - `FuturesSignal.is_order_intent = False` — dataclass __post_init__ 가드.
# - `FuturesContractSizingHint.contracts ≤ 1` — dataclass __post_init__ 가드.
# - 자동 롤오버 *주문* 발신 0건 — `FuturesRolloverPlan`은 plan dataclass.
#
# 위 invariant는 `tests/test_futures_strategies.py`로 강제.
