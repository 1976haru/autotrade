"""백테스트 엔진 (#23 보강).

기본 단순 봉 단위 백테스트 + 체결 모델 / 비용 모델 (config 주입 시).

- config=None: legacy 동작 — 신호 봉 close에 체결 (same_close), 비용 미반영.
- config=BacktestConfig(...): execution_model / execution_delay_bars / slippage /
  commission / tax 적용. 마지막 봉에서 신호가 나서 execution bar가 없으면 체결
  하지 않는다 (exit_on_last_bar=True면 잔여 포지션은 마지막 봉 close에 강제 청산).

CLAUDE.md 절대 원칙 — 본 엔진은 broker / RiskManager / PermissionGate /
OrderExecutor를 import하지 않는다. 본 PR은 routes/엔진/types만 변경하며 주문
경로 / LIVE flag는 건드리지 않는다.
"""

from __future__ import annotations

from app.strategies.base import Strategy
from app.backtest.types import BacktestConfig, BacktestResult, Bar, Signal, Trade


class BacktestEngine:
    """단순 봉 단위 백테스트 엔진.

    - 종목당 동시 0 또는 1 포지션 (롱 전용).
    - exit_on_last_bar=True (기본)면 미청산 포지션을 마지막 봉 close에 강제 청산.
    """

    def __init__(self, initial_cash: int = 10_000_000, quantity: int = 1):
        if initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self.initial_cash = initial_cash
        self.quantity = quantity

    # ------------------------------------------------------------------
    # legacy 경로 (config 미제공) — 기존 호출자 / 테스트 호환성 유지
    # ------------------------------------------------------------------

    def run(
        self,
        bars:     list[Bar],
        strategy: Strategy,
        config:   BacktestConfig | None = None,
    ) -> BacktestResult:
        if config is None:
            return self._run_legacy(bars, strategy)
        return self._run_with_config(bars, strategy, config)

    def _run_legacy(self, bars: list[Bar], strategy: Strategy) -> BacktestResult:
        """기존 동작 — same_close 체결, 비용 0."""
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

    # ------------------------------------------------------------------
    # config 경로 — execution model + 비용 반영
    # ------------------------------------------------------------------

    def _run_with_config(
        self,
        bars:     list[Bar],
        strategy: Strategy,
        config:   BacktestConfig,
    ) -> BacktestResult:
        cash = self.initial_cash
        trades: list[Trade] = []

        # 진입 상태 — signal_price와 execution_price를 분리해 보관.
        open_entry_ts: object = None
        open_entry_signal_price: int | None = None
        open_entry_exec_price:   int | None = None
        open_entry_fee:          int = 0
        open_entry_slip_cost:    int = 0

        for i, bar in enumerate(bars):
            signal = strategy.on_bar(bars[: i + 1])

            if signal == Signal.BUY and open_entry_signal_price is None:
                exec_idx = self._execution_index(i, len(bars), config)
                if exec_idx is None:
                    continue  # 마지막 봉 신호 — 체결 X
                exec_bar = bars[exec_idx]
                signal_price = bar.close
                raw_price    = self._execution_price(exec_bar, config, is_buy=True)
                exec_price   = self._apply_slippage(raw_price, config.slippage_bps, is_buy=True)
                slip = (exec_price - raw_price) * self.quantity
                fee  = exec_price * self.quantity * config.commission_bps // 10000
                cost = exec_price * self.quantity + fee
                if cash >= cost:
                    cash -= cost
                    open_entry_ts = exec_bar.timestamp
                    open_entry_signal_price = signal_price
                    open_entry_exec_price   = exec_price
                    open_entry_fee          = fee
                    open_entry_slip_cost    = max(0, slip)
            elif signal == Signal.SELL and open_entry_signal_price is not None:
                exec_idx = self._execution_index(i, len(bars), config)
                if exec_idx is None:
                    continue  # 마지막 봉 신호 — 체결 X (강제 청산은 루프 후)
                exec_bar = bars[exec_idx]
                signal_price = bar.close
                raw_price    = self._execution_price(exec_bar, config, is_buy=False)
                exec_price   = self._apply_slippage(raw_price, config.slippage_bps, is_buy=False)
                exit_slip = (raw_price - exec_price) * self.quantity
                exit_fee  = exec_price * self.quantity * config.commission_bps // 10000
                tax = exec_price * self.quantity * config.tax_bps // 10000
                proceeds = exec_price * self.quantity - exit_fee - tax
                cash += proceeds

                gross = (signal_price - open_entry_signal_price) * self.quantity
                fees_total  = open_entry_fee + exit_fee
                slip_total  = open_entry_slip_cost + max(0, exit_slip)
                net = gross - fees_total - tax - slip_total

                trades.append(Trade(
                    symbol=exec_bar.symbol,
                    entry_ts=open_entry_ts,
                    entry_price=open_entry_exec_price,
                    exit_ts=exec_bar.timestamp,
                    exit_price=exec_price,
                    quantity=self.quantity,
                    pnl=net,
                    entry_signal_price=open_entry_signal_price,
                    exit_signal_price=signal_price,
                    fees=fees_total,
                    taxes=tax,
                    slippage_cost=slip_total,
                ))

                open_entry_ts = None
                open_entry_signal_price = None
                open_entry_exec_price   = None
                open_entry_fee          = 0
                open_entry_slip_cost    = 0

        # 잔여 포지션 강제 청산 (옵션) — 마지막 봉 close 사용 (다음 봉이 없으므로
        # execution_model과 무관하게 close 기준).
        if open_entry_signal_price is not None and bars and config.exit_on_last_bar:
            last = bars[-1]
            signal_price = last.close
            raw_price    = last.close
            exec_price   = self._apply_slippage(raw_price, config.slippage_bps, is_buy=False)
            exit_slip = (raw_price - exec_price) * self.quantity
            exit_fee  = exec_price * self.quantity * config.commission_bps // 10000
            tax = exec_price * self.quantity * config.tax_bps // 10000
            proceeds = exec_price * self.quantity - exit_fee - tax
            cash += proceeds

            gross = (signal_price - open_entry_signal_price) * self.quantity
            fees_total = open_entry_fee + exit_fee
            slip_total = open_entry_slip_cost + max(0, exit_slip)
            net = gross - fees_total - tax - slip_total

            trades.append(Trade(
                symbol=last.symbol,
                entry_ts=open_entry_ts,
                entry_price=open_entry_exec_price,
                exit_ts=last.timestamp,
                exit_price=exec_price,
                quantity=self.quantity,
                pnl=net,
                entry_signal_price=open_entry_signal_price,
                exit_signal_price=signal_price,
                fees=fees_total,
                taxes=tax,
                slippage_cost=slip_total,
            ))

        return BacktestResult(
            trades=trades,
            initial_cash=self.initial_cash,
            final_cash=cash,
            bars_processed=len(bars),
        )

    # ------------------------------------------------------------------
    # execution model helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _execution_index(i: int, n: int, config: BacktestConfig) -> int | None:
        """현재 신호 인덱스 i에서 체결할 봉 인덱스. 범위 밖이면 None.

        - same_close + allow_same_bar_execution=True → i (동일 봉)
        - 그 외 → i + execution_delay_bars
        """
        if config.allow_same_bar_execution and config.execution_delay_bars == 0:
            return i if i < n else None
        target = i + max(1, config.execution_delay_bars)
        return target if target < n else None

    @staticmethod
    def _execution_price(bar: Bar, config: BacktestConfig, *, is_buy: bool) -> int:
        """체결 모델별 raw 체결 가격 (slippage 반영 전)."""
        m = config.execution_model
        if m == "same_close":
            return bar.close
        if m == "next_close":
            return bar.close
        if m == "next_open":
            return bar.open
        if m == "conservative":
            # BUY는 더 비싼 쪽, SELL은 더 싼 쪽.
            return max(bar.open, bar.close) if is_buy else min(bar.open, bar.close)
        # __post_init__에서 검증되므로 도달 불가 — 안전 측 fallback.
        return bar.close

    @staticmethod
    def _apply_slippage(price: int, slippage_bps: int, *, is_buy: bool) -> int:
        if slippage_bps <= 0:
            return price
        # round half-up 의미로 정수화.
        delta = price * slippage_bps // 10000
        if is_buy:
            return price + delta
        return max(1, price - delta)

