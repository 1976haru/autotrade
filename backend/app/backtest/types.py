from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Signal(StrEnum):
    BUY  = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class Bar:
    symbol:    str
    timestamp: datetime
    open:      int
    high:      int
    low:       int
    close:     int
    volume:    int


@dataclass(frozen=True)
class Trade:
    symbol:      str
    entry_ts:    datetime
    entry_price: int
    exit_ts:     datetime
    exit_price:  int
    quantity:    int
    pnl:         int


@dataclass
class BacktestResult:
    trades:         list[Trade] = field(default_factory=list)
    initial_cash:   int = 0
    final_cash:     int = 0
    bars_processed: int = 0

    @property
    def total_pnl(self) -> int:
        return self.final_cash - self.initial_cash

    @property
    def win_count(self) -> int:
        return sum(1 for t in self.trades if t.pnl > 0)

    @property
    def loss_count(self) -> int:
        return sum(1 for t in self.trades if t.pnl <= 0)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return self.win_count / len(self.trades)

    @property
    def max_drawdown(self) -> int:
        """누적 PnL 곡선의 최대 peak-to-trough 낙폭(절대값)."""
        peak = 0
        running = 0
        max_dd = 0
        for t in self.trades:
            running += t.pnl
            if running > peak:
                peak = running
            dd = peak - running
            if dd > max_dd:
                max_dd = dd
        return max_dd
