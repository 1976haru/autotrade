import math
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

    @property
    def avg_win(self) -> float:
        wins = [t.pnl for t in self.trades if t.pnl > 0]
        return sum(wins) / len(wins) if wins else 0.0

    @property
    def avg_loss(self) -> float:
        """음수 또는 0. pnl == 0 도 손실 쪽으로 분류 (win_rate 와 동일한 분류)."""
        losses = [t.pnl for t in self.trades if t.pnl <= 0]
        return sum(losses) / len(losses) if losses else 0.0

    @property
    def profit_factor(self) -> float | None:
        """gross_winnings / |gross_losses|. 손실이 없거나 거래가 없으면 None.

        +inf 대신 None 을 반환해 JSON 직렬화 시 안전하게 처리되도록 한다.
        """
        gross_win  = sum(t.pnl for t in self.trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self.trades if t.pnl < 0))
        if gross_loss == 0:
            return None
        return gross_win / gross_loss

    @property
    def sharpe_ratio(self) -> float | None:
        """체결당(per-trade) 샤프 비 = mean(returns) / stdev(returns).

        trade return = pnl / (entry_price * quantity) — 명목 자본 대비 비율.
        봉 간격은 엔진이 알지 못하므로 연환산(annualization)은 하지 않는다.
        거래 < 2 또는 stdev == 0 은 정의되지 않으므로 None.
        """
        if len(self.trades) < 2:
            return None
        returns = [t.pnl / (t.entry_price * t.quantity) for t in self.trades]
        n = len(returns)
        mean = sum(returns) / n
        variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
        if variance == 0:
            return None
        return mean / math.sqrt(variance)
