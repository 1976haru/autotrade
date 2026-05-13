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


# ---------- avg_win / avg_loss ----------

def test_avg_win_and_loss_split_by_pnl_sign():
    r = BacktestResult(trades=[_trade(100), _trade(-40), _trade(60), _trade(-20)])
    assert r.avg_win  == 80.0   # (100 + 60) / 2
    assert r.avg_loss == -30.0  # (-40 + -20) / 2


def test_avg_win_and_loss_zero_when_no_trades():
    r = BacktestResult(trades=[])
    assert r.avg_win  == 0.0
    assert r.avg_loss == 0.0


def test_avg_loss_includes_break_even_trades():
    """pnl == 0 is classified as a loss (matches loss_count / win_rate)."""
    r = BacktestResult(trades=[_trade(50), _trade(0)])
    assert r.avg_win  == 50.0
    assert r.avg_loss == 0.0


# ---------- profit_factor ----------

def test_profit_factor_sums_winners_over_absolute_losers():
    r = BacktestResult(trades=[_trade(100), _trade(-50), _trade(50), _trade(-25)])
    # gross_win=150, gross_loss=75 → 2.0
    assert r.profit_factor == 2.0


def test_profit_factor_none_when_no_losses():
    r = BacktestResult(trades=[_trade(100), _trade(50)])
    assert r.profit_factor is None


def test_profit_factor_none_when_no_trades():
    assert BacktestResult(trades=[]).profit_factor is None


def test_profit_factor_excludes_break_even_from_loss_sum():
    # pnl == 0 contributes neither to gross_win nor gross_loss.
    r = BacktestResult(trades=[_trade(100), _trade(0), _trade(-50)])
    assert r.profit_factor == 2.0


# ---------- sharpe_ratio ----------

def test_sharpe_none_when_fewer_than_two_trades():
    assert BacktestResult(trades=[]).sharpe_ratio is None
    assert BacktestResult(trades=[_trade(100)]).sharpe_ratio is None


def test_sharpe_none_when_zero_variance():
    """Identical returns → stdev=0 → sharpe undefined."""
    # Both trades have identical returns: pnl=10 / (entry_price=100 * qty=1) = 0.1
    r = BacktestResult(trades=[_trade(10), _trade(10)])
    assert r.sharpe_ratio is None


def test_sharpe_positive_when_returns_skew_positive():
    """Two trades, +20% and -10%, mean = +5%. Sharpe should be positive."""
    r = BacktestResult(trades=[_trade(20), _trade(-10)])
    sharpe = r.sharpe_ratio
    assert sharpe is not None
    assert sharpe > 0


def test_sharpe_negative_when_returns_skew_negative():
    r = BacktestResult(trades=[_trade(-20), _trade(10)])
    sharpe = r.sharpe_ratio
    assert sharpe is not None
    assert sharpe < 0


def test_sharpe_uses_per_trade_returns_not_dollar_pnl():
    """Same dollar pnl with different entry prices yields different returns."""
    # Trade A: 10 / (100 * 1) = 0.10 return
    # Trade B: 10 / (200 * 1) = 0.05 return
    big_entry = Trade(
        symbol="X", entry_ts=_BASE, entry_price=200,
        exit_ts=_BASE, exit_price=210, quantity=1, pnl=10,
    )
    r = BacktestResult(trades=[_trade(10), big_entry])
    assert r.sharpe_ratio is not None
    # Mean = 0.075, stdev (sample) = sqrt(((0.10-0.075)^2 + (0.05-0.075)^2) / 1) = 0.025*sqrt(2)
    # Sharpe = 0.075 / (0.025*sqrt(2)) = 3 / sqrt(2) ≈ 2.121
    assert abs(r.sharpe_ratio - (0.075 / (0.025 * (2 ** 0.5)))) < 1e-9


# =====================================================================
# #65 추가: 입력 검증 + summarize_metrics + 빈 입력
# =====================================================================


def test_engine_rejects_non_positive_initial_cash():
    """initial_cash가 0 또는 음수면 즉시 ValueError — 운영자가 잘못된 백테스트
    을 모르고 돌리는 사고 방지."""
    import pytest
    with pytest.raises(ValueError, match="initial_cash must be positive"):
        BacktestEngine(initial_cash=0)
    with pytest.raises(ValueError, match="initial_cash must be positive"):
        BacktestEngine(initial_cash=-1)


def test_engine_rejects_non_positive_quantity():
    """quantity=0 또는 음수는 ValueError. 의미 있는 백테스트가 아님."""
    import pytest
    with pytest.raises(ValueError, match="quantity must be positive"):
        BacktestEngine(initial_cash=1_000_000, quantity=0)


def test_empty_bars_yields_zero_trades_and_full_cash():
    """빈 봉 list — 신호 평가 안 되어 trades=[], final_cash=initial_cash."""
    engine = BacktestEngine(initial_cash=1_000_000)
    result = engine.run([], _FixedSignals([]))
    assert result.trades == []
    assert result.final_cash == 1_000_000
    assert result.bars_processed == 0
    assert result.total_pnl == 0


def test_summarize_metrics_smoke():
    """BacktestResult.summarize_metrics()가 모든 핵심 지표를 dict로 노출."""
    r = BacktestResult(
        trades=[_trade(50), _trade(-20), _trade(30)],
        initial_cash=1_000_000, final_cash=1_000_060,
    )
    m = r.summarize_metrics()
    # 핵심 필드 존재 검증 — 정확값은 다른 테스트가 lock
    for key in ("trade_count", "win_count", "loss_count",
                "total_pnl", "win_rate", "profit_factor",
                "max_drawdown", "expectancy",
                "max_consecutive_losses", "max_consecutive_wins"):
        assert key in m, f"summarize_metrics missing key: {key}"
    assert m["trade_count"] == 3
