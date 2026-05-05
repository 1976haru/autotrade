from dataclasses import dataclass, replace

from sqlalchemy.orm import Session

from app.backtest.strategy import Strategy
from app.backtest.types import Bar, Signal
from app.brokers.base import BrokerAdapter, OrderRequest, OrderSide, OrderType
from app.core.modes import OperationMode
from app.execution.order_router import OrderRoutingResult, route_order
from app.risk.risk_manager import RiskDecision, RiskManager


_STUB_MESSAGE = (
    "LiveStrategyEngine.start/stop are stub. Real-time market data "
    "ingest is added in a follow-up PR. Until then, run_tick() generates "
    "intended orders and submit_tick() optionally routes them through the "
    "Risk/Permission/Executor pipeline."
)


@dataclass(frozen=True)
class TickResult:
    """Outcome of running the strategy on a single new bar.

    `intended_order` is None when the signal is HOLD or when the engine is
    already in the requested state (long position open on BUY, flat on SELL).
    `routing` is set only when submit_tick() has run the intended order
    through the Risk/Permission/Executor pipeline.
    """
    bar:            Bar
    signal:         Signal
    intended_order: OrderRequest | None
    routing:        OrderRoutingResult | None = None


class LiveStrategyEngine:
    """라이브 신호 엔진 — skeleton + pipeline 연결.

    Backtest engine과 같은 Strategy 인터페이스를 사용하되 실시간 봉을 받아
    돌리는 골격. CLAUDE.md 절대 원칙에 따라 다음을 명시적으로 따른다:
    - AI는 broker.place_order를 직접 호출하지 않는다.
    - 모든 주문은 RiskManager → PermissionGate → OrderExecutor를 거쳐야 한다.
    - LIVE_AI_EXECUTION은 기본 비활성화.

    제공하는 것:
    - run_tick(bar):     동기, 신호 + 의도된 OrderRequest까지만. broker 호출 X.
    - submit_tick(bar):  비동기, run_tick + route_order로 파이프라인까지 통과.
                         broker/risk/db/mode를 ctor에 주입한 경우에만 가능.

    여전히 stub인 것:
    - start/stop: 실제 폴링/구독 루프는 별도 PR. 지금은 NotImplementedError.

    submit_tick은 기본 `requested_by_ai=False` — Strategy ABC는 룰 기반이므로
    AI가 아니다. AI 기반 신호 엔진을 추가할 때 명시적으로 True를 넘겨야
    RiskManager의 AI 실행 가드(`enable_ai_execution`)가 작동한다.
    """

    def __init__(
        self,
        strategy: Strategy,
        *,
        quantity: int = 1,
        broker:   BrokerAdapter | None = None,
        risk:     RiskManager | None   = None,
        db:       Session | None       = None,
        mode:     OperationMode | None = None,
    ):
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self.strategy = strategy
        self.quantity = quantity
        self._bars: list[Bar] = []
        self._holding = False  # logical position state, not broker truth
        self._broker = broker
        self._risk   = risk
        self._db     = db
        self._mode   = mode

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

    async def submit_tick(self, bar: Bar, *, requested_by_ai: bool = False) -> TickResult:
        result = self.run_tick(bar)
        if result.intended_order is None:
            return result
        if self._broker is None or self._risk is None or self._db is None or self._mode is None:
            raise RuntimeError(
                "submit_tick requires broker, risk, db, and mode to be configured "
                "on the engine"
            )

        routing = await route_order(
            order=result.intended_order,
            requested_by_ai=requested_by_ai,
            mode=self._mode,
            broker=self._broker,
            risk=self._risk,
            db=self._db,
        )

        # If the order was rejected, roll back the local position state so the
        # engine doesn't think it owns shares the broker never bought.
        if routing.decision == RiskDecision.REJECTED:
            if result.intended_order.side == OrderSide.BUY:
                self._holding = False
            else:
                self._holding = True

        return replace(result, routing=routing)

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
