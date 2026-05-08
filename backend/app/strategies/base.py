"""Strategy contract (#28).

Strategy는 *주문을 실행하지 않는다* — 신호와 설명만 생성한다. 실제 주문은
`route_order` 단일 진입점이 RiskManager → PermissionGate → OrderExecutor
순서로 처리한다 (CLAUDE.md 절대 원칙 2).

본 모듈이 정의하는 4개 인터페이스 (`generate_signal` / `calculate_size` /
`exit_rule` / `explain_signal`)는 Strategy가 만들 수 있는 *전부*다 — 어떤
구현체도 broker / RiskManager / PermissionGate / OrderExecutor /
`route_order`를 import하지 않는다.

기존 호환성:
- `Strategy(ABC).on_bar(bars) -> Signal`은 그대로 유지 (concrete 3개 + 기존
  BacktestEngine + LiveStrategyEngine이 의존).
- 본 PR이 추가하는 메서드(`generate_signal` 등)는 모두 *default impl*이 있어
  기존 concrete 전략은 수정 없이 새 인터페이스로 노출된다.
- `StrategyBase = Strategy` alias로 신규 호출자가 새 이름 사용 가능.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.backtest.types import Bar, Signal


# ---------- 새 신호 / 사이즈 / 청산 / 설명 DTO ----------


class SignalAction(StrEnum):
    """신호 종류 — Strategy가 반환할 수 있는 의도. 주문 결정이 아니다."""
    BUY        = "BUY"        # 신규 진입 신호
    SELL       = "SELL"       # 청산/매도 신호
    EXIT       = "EXIT"       # 명시적 청산 (보유 중 전략 무효화 등)
    WATCH      = "WATCH"      # 모니터링 — 진입 조건 근접
    NO_SIGNAL  = "NO_SIGNAL"  # 무신호


@dataclass(frozen=True)
class SizingHint:
    """권장 사이즈 힌트. 최종 수량은 RiskManager / PositionSizingAgent가 결정.

    Strategy는 *추천*만 한다 — `quantity` 또는 `position_size_pct` 둘 중 하나
    또는 둘 다. None이면 호출자가 default 적용.
    """
    quantity:           int | None = None
    position_size_pct:  float | None = None
    # 운영 자본 대비 위험 한도 — Stop loss까지의 손실이 자본의 X% 이하 권장.
    risk_pct:           float | None = None
    # 자금/리스크 부족 의도. True면 호출자가 quantity=0 또는 REDUCE 처리.
    reduce_only:        bool = False
    note:               str | None = None

    def to_dict(self) -> dict:
        return {
            "quantity":          self.quantity,
            "position_size_pct": self.position_size_pct,
            "risk_pct":          self.risk_pct,
            "reduce_only":       self.reduce_only,
            "note":              self.note,
        }


@dataclass(frozen=True)
class ExitPlan:
    """청산 계획 — 운영자/Agent가 보고 판단. 실제 주문은 RiskManager 흐름."""
    take_profit_pct:  float | None = None
    stop_loss_pct:    float | None = None
    time_exit_bars:   int | None  = None    # N봉 후 자동 청산
    invalidation:     str | None  = None    # 신호 무효화 조건 (코드/문장)
    rule_summary:     str | None  = None    # 사람이 읽을 한 줄

    def to_dict(self) -> dict:
        return {
            "take_profit_pct": self.take_profit_pct,
            "stop_loss_pct":   self.stop_loss_pct,
            "time_exit_bars":  self.time_exit_bars,
            "invalidation":    self.invalidation,
            "rule_summary":    self.rule_summary,
        }


@dataclass(frozen=True)
class SignalExplanation:
    """사람이 읽을 수 있는 신호 설명. audit / Agent context로 carry 가능."""
    summary:        str
    reasons:        list[str] = field(default_factory=list)
    # 0~100. None이면 미산출.
    confidence:     int | None = None
    # 원본 지표 — Strategy 내부에서 계산한 raw indicator 값. 자유 dict.
    indicators:     dict | None = None
    required_regime: str | None = None

    def to_dict(self) -> dict:
        return {
            "summary":         self.summary,
            "reasons":         list(self.reasons),
            "confidence":      self.confidence,
            "indicators":      dict(self.indicators) if self.indicators else None,
            "required_regime": self.required_regime,
        }


@dataclass(frozen=True)
class StrategyContext:
    """Strategy.generate_signal 입력 — 시장 데이터 + 운영 컨텍스트."""
    bars:           list[Bar]
    symbol:         str | None = None
    regime:         str | None = None    # MarketRegime classifier 결과
    watchlist:      list[str] | None = None
    account_equity: int | None = None    # 운영 자본 (calculate_size 용)
    extra:          dict | None = None    # 자유 인자 — Agent / Quality 등


@dataclass(frozen=True)
class ValidationResult:
    """Strategy가 context를 받을 수 있는지 사전 점검."""
    ok:        bool
    reasons:   list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "reasons": list(self.reasons)}


@dataclass(frozen=True)
class StrategySignal:
    """Strategy.generate_signal의 결과.

    절대 주문 객체가 아님 — `is_order_intent`는 항상 False. 호출자는 Risk →
    Permission → OrderExecutor를 거쳐야 실제 주문이 만들어진다.
    """
    action:        SignalAction
    symbol:        str | None = None
    sizing_hint:   SizingHint | None = None
    exit_plan:     ExitPlan | None = None
    explanation:   SignalExplanation | None = None
    # invariant — Strategy가 반환하는 신호는 *주문 의도가 아니다*. 본 필드는
    # 항상 False이며, 코드/테스트로 강제. 호출자가 True로 바꾸려면 별도 옵트인
    # PR이 필요하다.
    is_order_intent: bool = False

    def to_dict(self) -> dict:
        return {
            "action":          self.action.value,
            "symbol":          self.symbol,
            "sizing_hint":     self.sizing_hint.to_dict() if self.sizing_hint else None,
            "exit_plan":       self.exit_plan.to_dict() if self.exit_plan else None,
            "explanation":     self.explanation.to_dict() if self.explanation else None,
            "is_order_intent": self.is_order_intent,
        }


# ---------- legacy ↔ new signal adapter ----------


def to_legacy_signal(s: StrategySignal | None) -> Signal:
    """StrategySignal → backtest_engine이 사용하는 BUY/SELL/HOLD."""
    if s is None:
        return Signal.HOLD
    if s.action == SignalAction.BUY:
        return Signal.BUY
    if s.action in (SignalAction.SELL, SignalAction.EXIT):
        return Signal.SELL
    # WATCH / NO_SIGNAL → HOLD
    return Signal.HOLD


def from_legacy_signal(sig: Signal, *, symbol: str | None = None) -> StrategySignal:
    """legacy on_bar 결과 → StrategySignal."""
    if sig == Signal.BUY:
        action = SignalAction.BUY
    elif sig == Signal.SELL:
        action = SignalAction.SELL
    else:
        action = SignalAction.NO_SIGNAL
    return StrategySignal(action=action, symbol=symbol, is_order_intent=False)


# ---------- Strategy(ABC) — 기존 contract 유지 + 신규 인터페이스 추가 ----------


class Strategy(ABC):
    """공용 Strategy 추상클래스.

    구현체는 metadata + `on_bar`를 채운다. 신규 인터페이스
    (`generate_signal` / `calculate_size` / `exit_rule` / `explain_signal`)는
    default impl이 있어 기존 구현체는 수정 없이 노출된다.

    **절대 원칙** (CLAUDE.md):
    - Strategy는 broker / RiskManager / PermissionGate / OrderExecutor /
      `route_order`를 import하지 않는다.
    - Strategy 메서드는 어떤 side effect도 만들지 않는다 — 단순 신호와 설명.
    - 실제 주문 / 모드 변경 / LIVE flag 토글은 호출자(Agent / Strategy /
      Risk → Permission → Executor 흐름)의 책임.
    """

    # 사람이 읽는 metadata. routes_live_engine의 /api/strategies/registry로 노출.
    entry:        str = ""
    exit:         str = ""
    invalidation: str = ""
    required_regime: str = "any"
    risk_profile: dict = {}

    @abstractmethod
    def on_bar(self, bars: list[Bar]) -> Signal:
        """Legacy 인터페이스 — 현재 봉까지의 히스토리를 받아 BUY/SELL/HOLD 반환.

        기존 BacktestEngine / LiveStrategyEngine이 의존. 새 인터페이스를 쓰는
        호출자도 default `generate_signal`이 본 메서드를 호출한다.
        """

    # ---------- 신규 인터페이스 (default impl로 호환성 유지) ----------

    def generate_signal(self, context: StrategyContext) -> StrategySignal:
        """시장 데이터 + 운영 context → 구조화된 StrategySignal.

        default — `on_bar`를 호출해 legacy Signal을 받고 StrategySignal로 변환.
        절대 주문 객체를 반환하지 않는다 (`is_order_intent=False`).
        """
        legacy = self.on_bar(context.bars)
        return from_legacy_signal(legacy, symbol=context.symbol)

    def calculate_size(
        self,
        signal: StrategySignal,
        *,
        account_context: dict[str, Any] | None = None,
        risk_context:    dict[str, Any] | None = None,
    ) -> SizingHint:
        """권장 사이즈 힌트. 최종 수량은 RiskManager / PositionSizingAgent가 결정.

        default — `risk_profile.position_size_pct`를 그대로 noop으로 반환.
        구현체가 indicator 기반 사이징을 하고 싶으면 override.
        """
        pct = self.risk_profile.get("position_size_pct") if self.risk_profile else None
        risk_pct = self.risk_profile.get("stop_loss_pct") if self.risk_profile else None
        return SizingHint(
            position_size_pct=float(pct) if pct is not None else None,
            risk_pct=float(risk_pct) if risk_pct is not None else None,
        )

    def exit_rule(
        self,
        signal: StrategySignal,
        *,
        position_context: dict[str, Any] | None = None,
    ) -> ExitPlan:
        """청산 계획 — metadata 기반 default.

        구현체가 indicator 기반 stop / trail을 하고 싶으면 override.
        """
        risk = self.risk_profile or {}
        tp = risk.get("take_profit_pct")
        sl = risk.get("stop_loss_pct")
        return ExitPlan(
            take_profit_pct=float(tp) if tp is not None else None,
            stop_loss_pct=float(sl) if sl is not None else None,
            invalidation=self.invalidation or None,
            rule_summary=self.exit or None,
        )

    def explain_signal(
        self,
        signal: StrategySignal,
        *,
        context: StrategyContext | None = None,
    ) -> SignalExplanation:
        """사람이 읽을 수 있는 신호 설명. default — metadata로 작성."""
        reasons: list[str] = []
        if self.entry:
            reasons.append(f"entry: {self.entry}")
        if self.exit:
            reasons.append(f"exit: {self.exit}")
        if self.invalidation:
            reasons.append(f"invalidation: {self.invalidation}")
        return SignalExplanation(
            summary=f"{type(self).__name__} → {signal.action.value}",
            reasons=reasons,
            confidence=None,
            indicators=None,
            required_regime=self.required_regime or None,
        )

    def validate_context(self, context: StrategyContext) -> ValidationResult:
        """Strategy가 context를 받을 수 있는지 사전 점검. default OK.

        구현체가 최소 봉 수 / 특정 regime 필요 등을 명시할 수 있다.
        """
        if not context.bars:
            return ValidationResult(ok=False, reasons=["bars 비어있음"])
        return ValidationResult(ok=True)


# StrategyBase는 Strategy alias — 신규 호출자가 새 이름을 쓰고 싶을 때.
StrategyBase = Strategy
