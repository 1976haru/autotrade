"""1분봉 intrabar execution realism 테스트 (백테스트 전용, 실주문 0건)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.backtest.intrabar_execution import (
    CostModel,
    ExecutionConfidence,
    ExecutionSource,
    ExitReason,
    IntrabarExecutionResult,
    simulate_intrabar_execution,
)

T0 = datetime(2026, 5, 22, 1, 0, tzinfo=timezone.utc)


def _b(min_off, o, h, lo, c):
    return {"timestamp": (T0 + timedelta(minutes=min_off)).isoformat(),
            "open": o, "high": h, "low": lo, "close": c}


def _sim(**kw):
    base = dict(side="BUY", entry_time=T0.isoformat(), entry_price=100.0,
                stop_price=98.0, target_price=103.0, max_hold_minutes=30,
                bars_5m=[])
    base.update(kw)
    return simulate_intrabar_execution(**base)


def test_target_first_buy():
    r = _sim(bars_5m=[_b(0, 100, 100.5, 99.8, 100.2)],
             bars_1m=[_b(0, 100, 100.5, 99.9, 100.2), _b(1, 100.2, 103, 100, 102.5)])
    assert r.exit_reason == ExitReason.TARGET_HIT.value
    assert r.exit_price == 103.0
    assert r.execution_source == ExecutionSource.ONE_MINUTE_REPLAY.value
    assert r.execution_confidence == ExecutionConfidence.HIGH.value


def test_stop_first_buy():
    r = _sim(bars_1m=[_b(0, 100, 100.2, 98.0, 98.5)], bars_5m=[_b(0, 100, 100.2, 98, 98.5)])
    assert r.exit_reason == ExitReason.STOP_HIT.value
    assert r.exit_price == 98.0


def test_5m_both_touch_resolved_by_1m_target_first():
    # 5분봉 한 캔들에서 stop·target 둘 다 닿음, 1분봉은 target 먼저.
    r = _sim(bars_5m=[_b(0, 100, 103, 98, 99)],
             bars_1m=[_b(0, 100, 100.5, 100, 100.3), _b(1, 100.3, 103, 100, 102),
                      _b(2, 102, 102.5, 98, 98.5)])
    assert r.exit_reason == ExitReason.TARGET_HIT.value
    assert r.execution_source == ExecutionSource.ONE_MINUTE_REPLAY.value


def test_1m_simultaneous_touch_stop_first():
    # 1분봉 한 캔들 안에서도 동시 터치 → 보수적 stop-first.
    r = _sim(bars_5m=[_b(0, 100, 103, 98, 99)],
             bars_1m=[_b(0, 100, 103, 98, 99)])
    assert r.exit_reason == ExitReason.AMBIGUOUS_STOP_FIRST.value
    assert r.exit_price == 98.0


def test_no_1m_fallback_low_confidence():
    r = _sim(bars_5m=[_b(0, 100, 100.5, 99.5, 100.2), _b(5, 100.2, 103, 100, 102)],
             bars_1m=None)
    assert r.execution_source == ExecutionSource.FIVE_MINUTE_FALLBACK.value
    assert r.execution_confidence == ExecutionConfidence.LOW.value


def test_no_1m_fallback_ambiguous_conservative():
    r = _sim(bars_5m=[_b(0, 100, 103, 98, 100)], bars_1m=None)
    assert r.exit_reason == ExitReason.AMBIGUOUS_STOP_FIRST.value
    assert r.execution_source == ExecutionSource.AMBIGUOUS_CONSERVATIVE.value
    assert r.execution_confidence == ExecutionConfidence.LOW.value


def test_costs_reflected():
    r = _sim(bars_1m=[_b(0, 100, 102, 99.5, 101.5), _b(1, 101.5, 102.5, 100, 102)],
             target_price=102.0, stop_price=97.0,
             cost=CostModel(commission_bps=1.5, tax_bps=18, slippage_bps=5))
    assert r.exit_reason == ExitReason.TARGET_HIT.value
    assert r.gross_pnl == pytest.approx(2.0, abs=1e-6)   # 102-100
    assert r.tax_paid > 0 and r.slippage_paid > 0 and r.cost_paid > 0
    assert r.net_pnl < r.gross_pnl                       # 비용 차감


def test_slippage_stress_higher():
    common = dict(bars_1m=[_b(0, 100, 103, 99, 102)], target_price=102.0)
    base = _sim(**common, cost=CostModel(slippage_bps=5))
    stress = _sim(**common, cost=CostModel(slippage_bps=10))
    assert stress.slippage_paid > base.slippage_paid
    assert stress.net_pnl < base.net_pnl


def test_sell_short_target():
    # SELL: entry 100, stop 102(위), target 98(아래). low 가 98 닿음 → target.
    r = _sim(side="SELL", stop_price=102.0, target_price=98.0,
             bars_1m=[_b(0, 100, 100.2, 98.0, 98.5)],
             bars_5m=[_b(0, 100, 100.2, 98, 98.5)])
    assert r.exit_reason == ExitReason.TARGET_HIT.value
    assert r.exit_price == 98.0
    assert r.gross_pnl == pytest.approx(2.0, abs=1e-6)   # entry-exit = 100-98


def test_max_hold_exit_when_no_touch():
    r = _sim(bars_1m=[_b(0, 100, 100.5, 99.8, 100.1), _b(1, 100.1, 100.6, 99.9, 100.3)],
             stop_price=95.0, target_price=110.0)
    assert r.exit_reason == ExitReason.MAX_HOLD_EXIT.value


def test_result_invariant_live_auth_false():
    with pytest.raises(ValueError):
        IntrabarExecutionResult(
            exit_time=None, exit_price=1, exit_reason="X", gross_pnl=0, net_pnl=0,
            cost_paid=0, slippage_paid=0, tax_paid=0, hold_minutes=0,
            execution_source="X", execution_confidence="LOW",
            is_live_authorization=True)


def test_module_no_order_imports():
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "app/backtest/intrabar_execution.py"
    txt = src.read_text(encoding="utf-8")
    for bad in ("place_order", "route_order(", "OrderExecutor", "brokers.kis",
                "cargo build", "tauri build"):
        for line in txt.splitlines():
            assert bad not in line.split("#", 1)[0], f"banned: {bad}"
