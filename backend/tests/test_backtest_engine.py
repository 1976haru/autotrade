from datetime import datetime, timedelta, timezone

from app.backtest.engine import BacktestEngine
from app.strategies.base import Strategy
from app.backtest.types import Bar, BacktestResult, Signal, Trade


_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bar(i: int, close: int) -> Bar:
    return Bar(
        symbol="TEST",
        timestamp=_BASE + timedelta(days=i),
        open=close, high=close, low=close, close=close,
        volume=1,
    )


def _trade(pnl: int) -> Trade:
    return Trade(
        symbol="X",
        entry_ts=_BASE, entry_price=100,
        exit_ts=_BASE,  exit_price=100 + pnl,
        quantity=1, pnl=pnl,
    )


class _FixedSignals(Strategy):
    def __init__(self, signals: list[Signal]):
        self._signals = list(signals)
        self._idx = 0

    def on_bar(self, bars):
        s = self._signals[self._idx] if self._idx < len(self._signals) else Signal.HOLD
        self._idx += 1
        return s


def test_no_signals_yields_no_trades():
    bars = [_bar(i, 100) for i in range(5)]
    result = BacktestEngine(initial_cash=1_000_000).run(bars, _FixedSignals([Signal.HOLD] * 5))
    assert result.trades == []
    assert result.final_cash == 1_000_000
    assert result.bars_processed == 5
    assert result.total_pnl == 0
    assert result.win_rate == 0.0


def test_buy_then_sell_records_trade_and_updates_cash():
    bars = [_bar(0, 100), _bar(1, 110), _bar(2, 105), _bar(3, 120)]
    strat = _FixedSignals([Signal.BUY, Signal.HOLD, Signal.HOLD, Signal.SELL])
    result = BacktestEngine(initial_cash=1_000_000, quantity=10).run(bars, strat)
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.entry_price == 100
    assert t.exit_price == 120
    assert t.quantity == 10
    assert t.pnl == 200
    assert result.final_cash == 1_000_000 + 200
    assert result.total_pnl == 200
    assert result.win_rate == 1.0


def test_buy_skipped_when_cash_insufficient():
    bars = [_bar(0, 1_000_000), _bar(1, 1_500_000)]
    strat = _FixedSignals([Signal.BUY, Signal.SELL])
    result = BacktestEngine(initial_cash=500_000, quantity=1).run(bars, strat)
    assert result.trades == []
    assert result.final_cash == 500_000


def test_sell_without_open_position_is_ignored():
    bars = [_bar(0, 100), _bar(1, 90)]
    result = BacktestEngine(initial_cash=1_000_000).run(bars, _FixedSignals([Signal.SELL, Signal.SELL]))
    assert result.trades == []
    assert result.final_cash == 1_000_000


def test_repeated_buy_does_not_stack_positions():
    bars = [_bar(0, 100), _bar(1, 110), _bar(2, 120), _bar(3, 130)]
    strat = _FixedSignals([Signal.BUY, Signal.BUY, Signal.BUY, Signal.SELL])
    result = BacktestEngine(initial_cash=1_000_000, quantity=1).run(bars, strat)
    assert len(result.trades) == 1
    assert result.trades[0].entry_price == 100
    assert result.trades[0].exit_price == 130


def test_open_position_force_closed_at_last_bar():
    bars = [_bar(0, 100), _bar(1, 130)]
    strat = _FixedSignals([Signal.BUY, Signal.HOLD])
    result = BacktestEngine(initial_cash=1_000_000, quantity=5).run(bars, strat)
    assert len(result.trades) == 1
    assert result.trades[0].exit_price == 130
    assert result.trades[0].pnl == 150
    assert result.final_cash == 1_000_000 + 150


def test_multiple_round_trips_accumulate_pnl():
    bars = [_bar(i, c) for i, c in enumerate([100, 120, 110, 130, 125, 140])]
    strat = _FixedSignals([Signal.BUY, Signal.SELL, Signal.BUY, Signal.SELL, Signal.BUY, Signal.SELL])
    result = BacktestEngine(initial_cash=1_000_000, quantity=1).run(bars, strat)
    assert len(result.trades) == 3
    assert [t.pnl for t in result.trades] == [20, 20, 15]
    assert result.total_pnl == 55


def test_result_metrics_on_constructed_trades():
    r = BacktestResult(
        trades=[_trade(100), _trade(-50), _trade(200), _trade(-300), _trade(150)],
        initial_cash=1_000_000,
        final_cash=1_000_000 + 100,
    )
    assert r.total_pnl == 100
    assert r.win_count == 3
    assert r.loss_count == 2
    assert abs(r.win_rate - 0.6) < 1e-9
    assert r.max_drawdown == 300


def test_max_drawdown_zero_when_only_winners():
    r = BacktestResult(trades=[_trade(50), _trade(20), _trade(10)])
    assert r.max_drawdown == 0
