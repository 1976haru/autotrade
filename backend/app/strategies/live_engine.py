from dataclasses import dataclass, replace

from sqlalchemy.orm import Session

from app.strategies.base import Strategy
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
    `quality` (136) is the {strength, confidence} 0-100 advisory pair from
    quality.signal_quality — both 0 for HOLD signals.
    """
    bar:            Bar
    signal:         Signal
    intended_order: OrderRequest | None
    routing:        OrderRoutingResult | None = None
    quality:        dict | None = None


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
        strategy_name: str | None = None,
    ):
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self.strategy = strategy
        self.quantity = quantity
        # 138: registry 키와 동일한 이름 (예: 'sma_crossover'). LiveEngine이
        # 만드는 OrderRequest.strategy에 자동 채워지고 audit row에 surface.
        # configure 라우트가 알고 있는 이름이라 외부에서 주입.
        self.strategy_name = strategy_name
        self._bars: list[Bar] = []
        self._holding = False  # logical position state, not broker truth
        self._entry_price: int | None = None     # set on BUY, cleared on SELL
        self._last_price:  int | None = None     # last seen bar.close, mark price
        self._prev_entry_price: int | None = None  # snapshot for SELL rollback
        self._broker = broker
        self._risk   = risk
        self._db     = db
        self._mode   = mode

    def run_tick(self, bar: Bar) -> TickResult:
        self._bars.append(bar)
        self._last_price = bar.close
        signal = self.strategy.on_bar(self._bars)
        intended: OrderRequest | None = None

        if signal == Signal.BUY and not self._holding:
            intended = OrderRequest(
                symbol=bar.symbol,
                side=OrderSide.BUY,
                quantity=self.quantity,
                order_type=OrderType.MARKET,
                # 134: 전략 엔진이 직접 만든 주문은 사유가 명백 — audit row의
                # trade_reason으로 자동 surface해 사후 분석 시 'A주문이 왜
                # 들어갔나'가 즉답된다.
                trade_reason="strategy_signal",
                # 138: 어느 전략이 만든 주문인지 audit row까지 carry.
                strategy=self.strategy_name,
            )
            self._entry_price = bar.close
            self._holding = True
        elif signal == Signal.SELL and self._holding:
            intended = OrderRequest(
                symbol=bar.symbol,
                side=OrderSide.SELL,
                quantity=self.quantity,
                order_type=OrderType.MARKET,
                trade_reason="strategy_signal",
                strategy=self.strategy_name,
            )
            # Snapshot for rollback — without this a rejected SELL would leave
            # the engine in "holding but no entry_price" state, breaking PnL.
            self._prev_entry_price = self._entry_price
            self._entry_price = None
            self._holding = False

        # 136: signal quality는 advisory — 신호를 차단하지 않고 운영자에게
        # 강도/신뢰도 두 축으로 점수 노출. HOLD 신호도 0/0으로 채워 응답
        # 클라이언트가 '신호 없음'과 '약한 신호'를 명확히 구분 가능.
        from app.strategies.quality import signal_quality
        from app.market.regime import matches_required_regime
        regime = self.current_regime
        required = getattr(self.strategy, "required_regime", "any")
        quality = signal_quality(self._bars, signal,
                                 regime_matches=matches_required_regime(regime, required))

        return TickResult(bar=bar, signal=signal, intended_order=intended,
                          quality=quality)

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
            self.rollback_intent(result.intended_order)

        return replace(result, routing=routing)

    def rollback_intent(self, order: OrderRequest) -> None:
        """Revert the optimistic position state set by run_tick.

        Call this when an intended order was rejected by Risk or otherwise
        not actually placed, so the engine doesn't keep believing it holds
        shares the broker never bought (or that it's flat after a rejected
        sell). Restores `_entry_price` symmetrically with `_holding`.
        """
        if order.side == OrderSide.BUY:
            self._holding = False
            self._entry_price = None
        else:
            self._holding = True
            self._entry_price = self._prev_entry_price
            self._prev_entry_price = None

    @property
    def holding(self) -> bool:
        return self._holding

    @property
    def bars_seen(self) -> int:
        return len(self._bars)

    @property
    def entry_price(self) -> int | None:
        """Price at which the current open position was entered (None when flat)."""
        return self._entry_price

    @property
    def last_price(self) -> int | None:
        """Close of the most recent bar fed to run_tick — the mark price."""
        return self._last_price

    @property
    def unrealized_pnl(self) -> int | None:
        """(last_price - entry_price) * quantity. None when flat or no marks yet."""
        if not self._holding or self._entry_price is None or self._last_price is None:
            return None
        return (self._last_price - self._entry_price) * self.quantity

    @property
    def unrealized_pnl_pct(self) -> float | None:
        """Fractional return on the open position, signed. None when flat."""
        if (not self._holding or self._entry_price is None or self._last_price is None
                or self._entry_price == 0):
            return None
        return (self._last_price - self._entry_price) / self._entry_price

    @property
    def current_regime(self) -> str:
        """135: 현재 누적된 봉을 기반으로 추정한 시장 체제. < 20봉이면 'any'."""
        # local import — circular import 회피 (regime은 순수 함수라 부담 없음)
        from app.market.regime import classify_regime
        return classify_regime(self._bars)

    @property
    def regime_matches_strategy(self) -> bool:
        """135: 현재 체제가 strategy.required_regime과 호환되는지 advisory.
        False여도 신호는 그대로 — UI가 경고만 표시한다."""
        from app.market.regime import matches_required_regime
        required = getattr(self.strategy, "required_regime", "any")
        return matches_required_regime(self.current_regime, required)

    def start(self) -> None:
        raise NotImplementedError(_STUB_MESSAGE)

    def stop(self) -> None:
        raise NotImplementedError(_STUB_MESSAGE)
