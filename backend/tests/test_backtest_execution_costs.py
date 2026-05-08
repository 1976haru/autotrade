"""BacktestConfig + execution model + 비용 모델 테스트 (#23)."""

from datetime import datetime, timedelta, timezone

import pytest

from app.backtest.engine import BacktestEngine
from app.backtest.types import BacktestConfig, Bar, Signal
from app.strategies.base import Strategy


_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bar(i: int, o: int = 100, h: int | None = None, low: int | None = None,
         c: int = 100, v: int = 1000) -> Bar:
    """h/low 미지정 시 (open, close)에서 안전하게 유도."""
    if h is None:
        h = max(o, c) + 5
    if low is None:
        low = max(1, min(o, c) - 5)
    return Bar(
        symbol="TEST",
        timestamp=_BASE + timedelta(days=i),
        open=o, high=h, low=low, close=c, volume=v,
    )


class _FixedSignals(Strategy):
    def __init__(self, signals):
        self._signals = list(signals)
        self._idx = 0

    def on_bar(self, bars):
        s = self._signals[self._idx] if self._idx < len(self._signals) else Signal.HOLD
        self._idx += 1
        return s


# ---------- BacktestConfig validation ----------


def test_config_default_execution_model_is_next_open():
    cfg = BacktestConfig()
    assert cfg.execution_model == "next_open"
    assert cfg.execution_delay_bars == 1
    assert cfg.allow_same_bar_execution is False
    assert cfg.slippage_bps == 0
    assert cfg.commission_bps == 0
    assert cfg.tax_bps == 0


def test_config_rejects_unknown_execution_model():
    with pytest.raises(ValueError, match="execution_model"):
        BacktestConfig(execution_model="foo")


def test_config_rejects_negative_costs():
    with pytest.raises(ValueError):
        BacktestConfig(slippage_bps=-1)
    with pytest.raises(ValueError):
        BacktestConfig(commission_bps=-1)
    with pytest.raises(ValueError):
        BacktestConfig(tax_bps=-1)


def test_config_same_close_forces_allow_same_bar_and_zero_delay():
    cfg = BacktestConfig(execution_model="same_close")
    assert cfg.allow_same_bar_execution is True
    assert cfg.execution_delay_bars == 0


# ---------- execution model dispatch ----------


def test_legacy_run_when_no_config_uses_same_close():
    """config 미제공 — 기존 same_close 동작 유지."""
    bars = [_bar(0, 100, 110, 90, 100), _bar(1, 110, 120, 100, 110)]
    eng = BacktestEngine(initial_cash=1_000_000, quantity=1)
    res = eng.run(bars, _FixedSignals([Signal.BUY, Signal.SELL]))
    assert res.trades[0].entry_price == 100  # bar0 close
    assert res.trades[0].exit_price  == 110  # bar1 close


def test_next_open_executes_at_next_bar_open():
    """BUY @ bar0 → 체결 @ bar1.open. SELL @ bar2 → 체결 @ bar3.open."""
    bars = [
        _bar(0, o=100, h=105, low=98,  c=102),
        _bar(1, o=110, h=115, low=109, c=113),  # BUY 체결가 = 110
        _bar(2, o=115, h=120, low=114, c=118),
        _bar(3, o=120, h=125, low=118, c=122),  # SELL 체결가 = 120
    ]
    eng = BacktestEngine(initial_cash=1_000_000, quantity=1)
    cfg = BacktestConfig(execution_model="next_open")
    res = eng.run(bars, _FixedSignals([Signal.BUY, Signal.HOLD, Signal.SELL, Signal.HOLD]),
                  config=cfg)
    assert res.trades[0].entry_price == 110
    assert res.trades[0].exit_price  == 120


def test_next_close_executes_at_next_bar_close():
    bars = [
        _bar(0, o=100, h=105, low=98,  c=102),
        _bar(1, o=110, h=115, low=109, c=113),  # BUY 체결가 = 113
        _bar(2, o=115, h=120, low=114, c=118),
        _bar(3, o=120, h=125, low=118, c=122),  # SELL 체결가 = 122
    ]
    eng = BacktestEngine(initial_cash=1_000_000, quantity=1)
    cfg = BacktestConfig(execution_model="next_close")
    res = eng.run(bars, _FixedSignals([Signal.BUY, Signal.HOLD, Signal.SELL, Signal.HOLD]),
                  config=cfg)
    assert res.trades[0].entry_price == 113
    assert res.trades[0].exit_price  == 122


def test_conservative_buy_uses_max_open_close_sell_uses_min():
    bars = [
        _bar(0, c=100),
        _bar(1, o=110, h=115, low=108, c=120),  # BUY: max(110,120)=120 (불리)
        _bar(2, c=130),
        _bar(3, o=140, h=145, low=128, c=130),  # SELL: min(140,130)=130 (불리)
    ]
    eng = BacktestEngine(initial_cash=1_000_000, quantity=1)
    cfg = BacktestConfig(execution_model="conservative")
    res = eng.run(bars, _FixedSignals([Signal.BUY, Signal.HOLD, Signal.SELL, Signal.HOLD]),
                  config=cfg)
    assert res.trades[0].entry_price == 120  # 더 비싸게 산다
    assert res.trades[0].exit_price  == 130  # 더 싸게 판다


def test_same_close_with_explicit_config_executes_same_bar():
    bars = [_bar(0, c=100), _bar(1, c=110)]
    eng = BacktestEngine(initial_cash=1_000_000, quantity=1)
    cfg = BacktestConfig(execution_model="same_close")
    res = eng.run(bars, _FixedSignals([Signal.BUY, Signal.SELL]), config=cfg)
    assert res.trades[0].entry_price == 100
    assert res.trades[0].exit_price  == 110


def test_last_bar_signal_does_not_execute_with_next_open():
    """마지막 봉의 BUY는 execution bar가 없어 체결되지 않는다."""
    bars = [_bar(0, c=100), _bar(1, c=110)]
    eng = BacktestEngine(initial_cash=1_000_000, quantity=1)
    cfg = BacktestConfig(execution_model="next_open", exit_on_last_bar=False)
    res = eng.run(bars, _FixedSignals([Signal.HOLD, Signal.BUY]), config=cfg)
    assert res.trades == []


def test_open_position_force_closed_at_last_bar_when_enabled():
    """exit_on_last_bar=True (기본) — 잔여 포지션이 마지막 봉에서 강제 청산."""
    bars = [_bar(0, c=100), _bar(1, o=110, c=115), _bar(2, c=130)]
    eng = BacktestEngine(initial_cash=1_000_000, quantity=1)
    cfg = BacktestConfig(execution_model="next_open")
    res = eng.run(bars, _FixedSignals([Signal.BUY, Signal.HOLD, Signal.HOLD]), config=cfg)
    assert len(res.trades) == 1
    # 마지막 봉의 close에 강제 청산 — 본 테스트 상수에서 raw_price = bar.close
    assert res.trades[0].exit_price == 130


# ---------- costs: slippage / commission / tax ----------


def test_buy_slippage_increases_entry_price():
    bars = [_bar(0, c=100), _bar(1, o=1000, c=1000), _bar(2, o=1000, c=1000)]
    eng = BacktestEngine(initial_cash=10_000_000, quantity=1)
    cfg = BacktestConfig(execution_model="next_open", slippage_bps=100)  # 1%
    res = eng.run(bars, _FixedSignals([Signal.BUY, Signal.HOLD, Signal.SELL]), config=cfg)
    # next_open BUY 체결가 = 1000 * 1.01 = 1010
    assert res.trades[0].entry_price == 1010


def test_sell_slippage_decreases_exit_price():
    bars = [_bar(0, c=100), _bar(1, o=1000, c=1000), _bar(2, o=1000, c=1000)]
    eng = BacktestEngine(initial_cash=10_000_000, quantity=1)
    cfg = BacktestConfig(execution_model="next_open", slippage_bps=100)
    res = eng.run(bars, _FixedSignals([Signal.BUY, Signal.SELL, Signal.HOLD]), config=cfg)
    # SELL signal at i=1, execute at bar2.open=1000 * 0.99 = 990
    assert res.trades[0].exit_price == 990


def test_commission_reduces_final_cash_and_records_in_trade():
    """signal_price도 동일하게 1000으로 두어 gross=0, 비용만 측정."""
    bars = [_bar(0, c=1000), _bar(1, o=1000, c=1000),
            _bar(2, o=1000, c=1000), _bar(3, o=1000, c=1000)]
    eng = BacktestEngine(initial_cash=10_000_000, quantity=1)
    cfg = BacktestConfig(execution_model="next_open", commission_bps=10)  # 0.1%
    res = eng.run(bars, _FixedSignals([Signal.BUY, Signal.HOLD, Signal.SELL, Signal.HOLD]),
                  config=cfg)
    # 매수: 1000 * 0.001 = 1, 매도: 1000 * 0.001 = 1, 합 2.
    assert res.trades[0].fees == 2
    # gross_pnl = 0 (1000 → 1000), fees=2 → net = -2.
    assert res.trades[0].pnl == -2


def test_tax_applies_to_sell_only():
    bars = [_bar(0, c=1000), _bar(1, o=1000, c=1000),
            _bar(2, o=1000, c=1000), _bar(3, o=1000, c=1000)]
    eng = BacktestEngine(initial_cash=10_000_000, quantity=1)
    cfg = BacktestConfig(execution_model="next_open", tax_bps=23)  # 0.23%
    res = eng.run(bars, _FixedSignals([Signal.BUY, Signal.HOLD, Signal.SELL, Signal.HOLD]),
                  config=cfg)
    # 매도세 = 1000 * 0.0023 = 2 (정수 truncate)
    assert res.trades[0].taxes == 2
    assert res.trades[0].fees  == 0   # commission_bps=0
    assert res.trades[0].pnl   == -2  # gross 0 - tax 2


def test_gross_vs_net_pnl_diverge_when_costs_active():
    bars = [_bar(0, c=100), _bar(1, o=1000, c=1000), _bar(2, o=1100, c=1100)]
    eng = BacktestEngine(initial_cash=10_000_000, quantity=10)
    cfg = BacktestConfig(execution_model="next_open",
                         commission_bps=10, tax_bps=20, slippage_bps=10)
    res = eng.run(bars, _FixedSignals([Signal.BUY, Signal.SELL, Signal.HOLD]), config=cfg)
    t = res.trades[0]
    assert t.gross_pnl != t.pnl  # 비용 발생
    assert res.gross_pnl  > res.net_pnl
    assert res.total_fees   > 0
    assert res.total_taxes  > 0
    assert res.total_slippage > 0


# ---------- routes (config compatibility) ----------


def _basic_bars_payload():
    return [
        {"symbol": "TEST", "timestamp": "2026-01-01T00:00:00+00:00",
         "open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
        {"symbol": "TEST", "timestamp": "2026-01-02T00:00:00+00:00",
         "open": 110, "high": 115, "low": 105, "close": 110, "volume": 1000},
        {"symbol": "TEST", "timestamp": "2026-01-03T00:00:00+00:00",
         "open": 120, "high": 125, "low": 115, "close": 120, "volume": 1000},
    ]


def test_route_run_without_config_keeps_legacy_behavior(client):
    res = client.post("/api/backtest/run", json={
        "strategy": "sma_crossover", "initial_cash": 1_000_000, "quantity": 1,
        "bars": _basic_bars_payload(),
    })
    assert res.status_code == 200
    body = res.json()
    # config 미제공 — 비용 0 invariant.
    assert body["total_fees"]    == 0
    assert body["total_taxes"]   == 0
    assert body["total_slippage"] == 0
    # 기존 legacy: gross == net (비용 0이므로).
    assert body["gross_pnl"] == body["net_pnl"]


def test_route_run_with_config_returns_cost_fields(client):
    res = client.post("/api/backtest/run", json={
        "strategy": "sma_crossover", "initial_cash": 10_000_000, "quantity": 10,
        "bars": _basic_bars_payload(),
        "config": {
            "execution_model": "next_open",
            "commission_bps": 10, "tax_bps": 20, "slippage_bps": 10,
        },
    })
    assert res.status_code == 200
    body = res.json()
    assert body["config"]["execution_model"] == "next_open"
    # 비용이 0 이상 발생.
    assert body["total_fees"] >= 0
    assert body["total_slippage"] >= 0


def test_route_run_with_invalid_execution_model_returns_400(client):
    res = client.post("/api/backtest/run", json={
        "strategy": "sma_crossover", "initial_cash": 1_000_000, "quantity": 1,
        "bars": _basic_bars_payload(),
        "config": {"execution_model": "vibes"},
    })
    assert res.status_code == 400


def test_route_compare_config_propagates(client):
    res = client.post("/api/backtest/compare", json={
        "strategy": "sma_crossover", "param_sets": [{}, {}],
        "initial_cash": 1_000_000, "quantity": 1,
        "bars": _basic_bars_payload(),
        "config": {"execution_model": "next_open", "commission_bps": 5},
    })
    assert res.status_code == 200
    body = res.json()
    for run in body["runs"]:
        assert run["config"]["execution_model"] == "next_open"
        assert run["config"]["commission_bps"]  == 5
