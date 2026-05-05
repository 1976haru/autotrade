from dataclasses import dataclass

from app.backtest.strategy import Strategy
from app.backtest.types import Bar, Signal
from app.brokers.base import OrderRequest, OrderSide, OrderType


_STUB_MESSAGE = (
    "LiveStrategyEngine.start/stop are stub. Real-time market data "
    "ingest and Risk/Permission/Executor wiring land in a follow-up PR. "
    "Until then, run_tick() is the only entry point — it generates the "
    "intended order but does NOT submit anything to a broker."
)


@dataclass(frozen=True)
class TickResult:
    """Outcome of running the strategy on a single new bar.

    `intended_order` is None when the signal is HOLD or when the engine is
    already in the requested state (long position open on BUY, flat on SELL).
    The engine never submits orders — wiring through RiskManager,
    PermissionGate, and OrderExecutor lands in a separate PR.
    """
    bar:            Bar
    signal:         Signal
    intended_order: OrderRequest | None


class LiveStrategyEngine:
    """라이브 신호 엔진 — skeleton.

    Backtest engine과 같은 Strategy 인터페이스를 사용하되 실시간 봉을 받아
    돌리는 골격이다. CLAUDE.md 절대 원칙에 따라 다음을 명시적으로 따른다:
    - AI는 broker.place_order를 직접 호출하지 않는다.
    - 모든 주문은 RiskManager → PermissionGate → OrderExecutor를 거쳐야 한다.
    - LIVE_AI_EXECUTION은 기본 비활성화.

    이 PR이 포함하는 것:
    - run_tick(bar): 새 봉을 받아 strategy.on_bar()로 신호를 받고 의도한
      OrderRequest를 만든다. 절대 broker로 보내지 않는다.

    이 PR이 포함하지 않는 것 (별도 PR):
    - start/stop의 실제 폴링/구독 루프 (지금은 NotImplementedError)
    - 의도된 주문을 RiskManager + PermissionGate + OrderExecutor 파이프라인에
      태우는 wire-up
    - LIVE_SHADOW에서 신호 기록 vs 다른 모드에서 실행 분기
    """

    def __init__(self, strategy: Strategy, *, quantity: int = 1):
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self.strategy = strategy
        self.quantity = quantity
        self._bars: list[Bar] = []
        self._holding = False  # logical position state, not broker truth

    def run_tick(self, bar: Bar) -> TickResult:
        self._bars.append(bar)
        signal = self.strategy.on_bar(self._bars)
        intended: OrderRequest | None = None

        if signal == Signal.BUY and not self._holding:
            intended = OrderRequest(
                symbol=bar.symbol,
                side=OrderSide.BUY,
                quantity=self.quantity,
                order_type=OrderType.MARKET,
            )
            self._holding = True
        elif signal == Signal.SELL and self._holding:
            intended = OrderRequest(
                symbol=bar.symbol,
                side=OrderSide.SELL,
                quantity=self.quantity,
                order_type=OrderType.MARKET,
            )
            self._holding = False

        return TickResult(bar=bar, signal=signal, intended_order=intended)

    @property
    def holding(self) -> bool:
        return self._holding

    @property
    def bars_seen(self) -> int:
        return len(self._bars)

    def start(self) -> None:
        raise NotImplementedError(_STUB_MESSAGE)

    def stop(self) -> None:
        raise NotImplementedError(_STUB_MESSAGE)
