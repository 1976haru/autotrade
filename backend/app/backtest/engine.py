from app.strategies.base import Strategy
from app.backtest.types import Bar, BacktestResult, Signal, Trade


class BacktestEngine:
    """단순 봉 단위 백테스트 엔진.

    - 동시에 보유하는 포지션은 종목당 0 또는 1개 (롱 전용)
    - 체결가는 신호가 발생한 봉의 종가
    - 마지막 봉까지 미청산 포지션은 마지막 봉 종가에 강제 청산
    """

    def __init__(self, initial_cash: int = 10_000_000, quantity: int = 1):
        if initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self.initial_cash = initial_cash
        self.quantity = quantity

    def run(self, bars: list[Bar], strategy: Strategy) -> BacktestResult:
        cash = self.initial_cash
        trades: list[Trade] = []
        open_entry_ts = None
        open_entry_price: int | None = None

        for i, bar in enumerate(bars):
            signal = strategy.on_bar(bars[: i + 1])
            if signal == Signal.BUY and open_entry_price is None:
                cost = bar.close * self.quantity
                if cash >= cost:
                    cash -= cost
                    open_entry_ts = bar.timestamp
                    open_entry_price = bar.close
            elif signal == Signal.SELL and open_entry_price is not None:
                cash += bar.close * self.quantity
                trades.append(Trade(
                    symbol=bar.symbol,
                    entry_ts=open_entry_ts,
                    entry_price=open_entry_price,
                    exit_ts=bar.timestamp,
                    exit_price=bar.close,
                    quantity=self.quantity,
                    pnl=(bar.close - open_entry_price) * self.quantity,
                ))
                open_entry_ts = None
                open_entry_price = None

        if open_entry_price is not None and bars:
            last = bars[-1]
            cash += last.close * self.quantity
            trades.append(Trade(
                symbol=last.symbol,
                entry_ts=open_entry_ts,
                entry_price=open_entry_price,
                exit_ts=last.timestamp,
                exit_price=last.close,
                quantity=self.quantity,
                pnl=(last.close - open_entry_price) * self.quantity,
            ))

        return BacktestResult(
            trades=trades,
            initial_cash=self.initial_cash,
            final_cash=cash,
            bars_processed=len(bars),
        )
