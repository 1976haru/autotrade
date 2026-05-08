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
class BacktestConfig:
    """체결 모델 + 비용 모델 (#23).

    기본값은 보수적 — `next_open` + `execution_delay_bars=1`. 단 BacktestEngine
    호출 시 config를 명시하지 않으면 legacy(same_close) 경로를 그대로 쓴다 —
    기존 테스트/호출자 호환성 유지. 본 PR 신규 호출자는 명시적으로 config를
    주입한다.

    execution_model:
      - "same_close":   신호 봉의 close에 체결. **승격 평가 사용 금지** —
                        단순 검증/비교 외엔 사용 X.
      - "next_open":    신호 봉의 다음 봉 open. 권장 기본.
      - "next_close":   신호 봉의 다음 봉 close.
      - "conservative": BUY는 max(open, close), SELL은 min(open, close) — 불리한 가격.

    execution_delay_bars: 신호와 체결 사이의 봉 지연. 1이 기본 (다음 봉 체결).
    allow_same_bar_execution: True면 same_close 시 신호 봉 자체에 체결 허용.
    slippage_bps: BUY는 +, SELL은 - 방향. 1bps = 0.01%.
    commission_bps: BUY/SELL notional 양쪽에 적용.
    tax_bps: SELL notional에만 적용 (한국 거래세 가정).
    exit_on_last_bar: True면 미청산 포지션을 마지막 봉에서 강제 청산 (legacy).
    """
    execution_model:           str  = "next_open"
    execution_delay_bars:      int  = 1
    allow_same_bar_execution:  bool = False
    slippage_bps:              int  = 0
    commission_bps:            int  = 0
    tax_bps:                   int  = 0
    exit_on_last_bar:          bool = True

    def __post_init__(self):
        if self.execution_model not in (
            "same_close", "next_open", "next_close", "conservative",
        ):
            raise ValueError(
                f"unknown execution_model: {self.execution_model!r}. "
                "must be 'same_close' / 'next_open' / 'next_close' / 'conservative'."
            )
        if self.execution_delay_bars < 0:
            raise ValueError("execution_delay_bars must be >= 0")
        if self.slippage_bps < 0 or self.commission_bps < 0 or self.tax_bps < 0:
            raise ValueError("slippage/commission/tax bps must be non-negative")
        if self.execution_model == "same_close" and not self.allow_same_bar_execution:
            # same_close는 신호 봉 자체 체결이 의도 — allow_same_bar_execution 강제.
            object.__setattr__(self, "allow_same_bar_execution", True)
            object.__setattr__(self, "execution_delay_bars", 0)


@dataclass(frozen=True)
class Trade:
    symbol:      str
    entry_ts:    datetime
    entry_price: int  # 체결 가격 (slippage 반영 후)
    exit_ts:     datetime
    exit_price:  int  # 체결 가격 (slippage 반영 후)
    quantity:    int
    # 호환성 유지용 — gross 또는 net 의미는 비용 부재 시 동일.
    # 비용이 적용되면 net_pnl = gross_pnl - fees - taxes - slippage_cost.
    pnl:         int

    # 신호 시점의 reference price (체결가와 다를 수 있음). config 미제공 시 None.
    entry_signal_price: int | None = None
    exit_signal_price:  int | None = None

    # 비용 분해 (config 미제공 시 모두 0).
    fees:           int = 0   # entry + exit commission
    taxes:          int = 0   # SELL 거래세
    slippage_cost:  int = 0   # |slippage_bps × notional| BUY+SELL 합산

    @property
    def gross_pnl(self) -> int:
        """비용 미반영 손익 (slippage도 미반영 — signal 가격 기준)."""
        ent = self.entry_signal_price if self.entry_signal_price is not None else self.entry_price
        ex  = self.exit_signal_price  if self.exit_signal_price  is not None else self.exit_price
        return (ex - ent) * self.quantity

    @property
    def net_pnl(self) -> int:
        """비용 반영 손익 — pnl 필드와 동일 (호환성)."""
        return self.pnl


@dataclass
class BacktestResult:
    trades:         list[Trade] = field(default_factory=list)
    initial_cash:   int = 0
    final_cash:     int = 0
    bars_processed: int = 0

    @property
    def total_pnl(self) -> int:
        return self.final_cash - self.initial_cash

    # ---------- 비용 모델 (#23) — 비용 미반영 시 모두 0 ----------

    @property
    def total_fees(self) -> int:
        return sum(t.fees for t in self.trades)

    @property
    def total_taxes(self) -> int:
        return sum(t.taxes for t in self.trades)

    @property
    def total_slippage(self) -> int:
        return sum(t.slippage_cost for t in self.trades)

    @property
    def gross_pnl(self) -> int:
        """비용 미반영 손익 (slippage도 reference price 기준)."""
        return sum(t.gross_pnl for t in self.trades)

    @property
    def net_pnl(self) -> int:
        """비용 반영 손익. 비용 부재 시 gross_pnl과 동일."""
        return sum(t.net_pnl for t in self.trades)

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
