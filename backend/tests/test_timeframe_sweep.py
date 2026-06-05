"""시간축 × 전략 스윕 + 데이터 검증 테스트 (체크리스트 작업4 / WF).

- collect_backtest_trades 가 run_strategy_council_backtest 와 일관된 신호를 노출.
- 비용 적용 후 PF 가 적용 전보다 악화(또는 동일) — 비용은 알파를 깎는다.
- 1d(하루 1봉) 데이터는 일중 전략 신호 0 → applicable=False, N/A 정직 표시.
- 분석 전용 불변: is_order_signal / is_live_authorization / auto_apply_allowed /
  contains_secret 항상 False.
- dataset_validation: 정상/잘못된 OHLC/단봉 점프/거래량0 분류.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.backtest.cost_model import UNFILLED_RATE, cost_drag_bps, round_trip_cost_fraction
from app.backtest.strategy_council_backtest import (
    AGENT_COUNCIL,
    SINGLE_STRATEGIES,
    BacktestInput,
    OHLCVBar,
    collect_backtest_trades,
    run_strategy_council_backtest,
)
from app.backtest.timeframe_sweep import (
    PF_TARGET,
    SWEEP_NOT_APPLICABLE,
    SweepMatrix,
    TimeframeSweepResult,
    run_sweep_matrix,
    run_timeframe_sweep,
)

_KST = timezone(timedelta(hours=9))


def _intraday_bars(*, days: int = 6, bars_per_day: int = 40, symbol: str = "000001",
                   start_price: float = 10000.0, drift: float = 0.0015) -> list[OHLCVBar]:
    """일중 상승 추세가 있는 합성 분봉 — 일부 전략이 BUY 를 내도록."""
    out: list[OHLCVBar] = []
    base = datetime(2025, 5, 12, 9, 0, tzinfo=_KST)
    price = start_price
    for d in range(days):
        day0 = base + timedelta(days=d)
        price = start_price * (1 + 0.002 * d)
        for i in range(bars_per_day):
            ts = day0 + timedelta(minutes=5 * i)
            o = price
            c = price * (1 + drift)
            h = max(o, c) * 1.001
            lo = min(o, c) * 0.999
            out.append(OHLCVBar(symbol=symbol, timestamp=ts, open=o, high=h,
                                low=lo, close=c, volume=1000.0 + 10 * i))
            price = c
    return out


def _daily_bars(*, days: int = 120, symbol: str = "000002") -> list[OHLCVBar]:
    """하루 1봉(일봉) — 일중 전략은 신호를 못 만든다."""
    out: list[OHLCVBar] = []
    base = datetime(2025, 1, 2, 0, 0, tzinfo=_KST)
    price = 10000.0
    for d in range(days):
        ts = base + timedelta(days=d)
        o = price
        c = price * 1.001
        out.append(OHLCVBar(symbol=symbol, timestamp=ts, open=o, high=c * 1.002,
                            low=o * 0.998, close=c, volume=100000.0))
        price = c
    return out


# ── collect_backtest_trades ↔ run 일관성 ────────────────────────────────────


def test_collect_matches_run_signal_counts():
    bars = _intraday_bars()
    inp = BacktestInput(bars=tuple(bars))
    collected = collect_backtest_trades(inp)
    rep = run_strategy_council_backtest(inp)
    for s in SINGLE_STRATEGIES:
        collected_buy = sum(1 for t in collected.strat_trades[s] if t.signal == "BUY")
        assert collected_buy == rep.strategies[s].signal_counts["BUY"]
    # council 신호 수 일관성.
    collected_council_buy = sum(1 for t in collected.council_trades if t.signal == "BUY")
    assert collected_council_buy == rep.council.performance.signal_counts["BUY"]


def test_collect_trades_are_not_order_signals():
    bars = _intraday_bars()
    collected = collect_backtest_trades(BacktestInput(bars=tuple(bars)))
    for t in collected.council_trades:
        assert t.is_order_signal is False
        assert t.is_live_authorization is False


# ── 비용 전/후 ──────────────────────────────────────────────────────────────


def test_cost_constants():
    # 2026 개정: 거래세 0.18% → 0.20% (코스피/코스닥 동일). 왕복 31bps → 33bps.
    assert cost_drag_bps() == pytest.approx(33.0)
    assert round_trip_cost_fraction() == pytest.approx(0.0033)
    assert UNFILLED_RATE == 0.05


def test_after_cost_pf_not_better_than_before():
    bars = _intraday_bars(days=8, bars_per_day=50)
    res = run_timeframe_sweep("5m", bars)
    assert res.applicable is True
    for name, sr in res.strategies.items():
        pf_b = sr.before_cost.get("profit_factor")
        pf_a = sr.after_cost.get("profit_factor")
        if pf_b is not None and pf_a is not None:
            # 비용은 알파를 깎는다 — 적용 후 PF 가 적용 전보다 좋아질 수 없다.
            assert pf_a <= pf_b + 1e-9
        # after-cost 거래수 ≤ before (미체결 제외).
        assert sr.after_cost["trade_count"] <= sr.before_cost["trade_count"]


def test_after_cost_average_return_lower():
    bars = _intraday_bars(days=8, bars_per_day=50)
    res = run_timeframe_sweep("5m", bars)
    for sr in res.strategies.values():
        ar_b = sr.before_cost.get("average_return")
        ar_a = sr.after_cost.get("average_return")
        if ar_b is not None and ar_a is not None:
            assert ar_a <= ar_b + 1e-9


# ── 1d N/A ──────────────────────────────────────────────────────────────────


def test_daily_bars_not_applicable():
    bars = _daily_bars(days=120)
    res = run_timeframe_sweep("1d", bars)
    assert res.applicable is False
    assert res.reason_code == SWEEP_NOT_APPLICABLE
    assert res.bars_per_day_median == 1.0
    for sr in res.strategies.values():
        assert sr.signal_counts["BUY"] == 0
        assert sr.after_cost["profit_factor"] is None


# ── 매트릭스 / 불변 ──────────────────────────────────────────────────────────


def test_sweep_matrix_rows_and_promising():
    bars5 = _intraday_bars(days=6, bars_per_day=40, symbol="000001")
    bars1d = _daily_bars(days=120, symbol="000001")
    matrix = run_sweep_matrix({"5m": bars5, "1d": bars1d})
    rows = matrix.matrix_rows()
    # 5m × 5전략 + 1d × 5전략 = 10 행.
    assert len(rows) == 10
    # 1d 행은 applicable=False.
    assert all(not r["applicable"] for r in rows if r["timeframe"] == "1d")
    # promising 은 meets_pf_target 만 + applicable.
    for r in matrix.promising_combos():
        assert r["meets_pf_target"] is True
        assert r["applicable"] is True
        assert r["pf_after"] >= PF_TARGET


def test_invariants_locked():
    bars = _intraday_bars()
    res = run_timeframe_sweep("5m", bars)
    d = res.to_dict()
    for k in ("is_order_signal", "is_live_authorization", "auto_apply_allowed", "contains_secret"):
        assert d[k] is False
    matrix = run_sweep_matrix({"5m": bars})
    md = matrix.to_dict()
    for k in ("is_order_signal", "is_live_authorization", "auto_apply_allowed", "contains_secret"):
        assert md[k] is False


def test_invariant_violation_raises():
    with pytest.raises(ValueError):
        TimeframeSweepResult(
            timeframe="5m", horizon="close", symbol_count=1, bar_count=1,
            bars_per_day_median=1.0, applicable=False, reason_code="x", note="",
            strategies={}, council_vs_best_single={}, is_order_signal=True,
        )
    with pytest.raises(ValueError):
        SweepMatrix(generated_at="now", results={}, is_live_authorization=True)


# ── dataset_validation ──────────────────────────────────────────────────────


def _write_csv(path, rows: list[str]) -> None:
    path.write_text("timestamp,open,high,low,close,volume,symbol\n" + "\n".join(rows) + "\n",
                    encoding="utf-8")


def test_validate_clean_csv(tmp_path):
    from app.market_data.dataset_validation import validate_symbol_csv
    base = datetime(2025, 1, 2, 9, 0, tzinfo=_KST)
    rows = []
    p = 10000.0
    for d in range(30):
        for i in range(40):
            ts = (base + timedelta(days=d, minutes=5 * i)).isoformat()
            rows.append(f"{ts},{p},{p*1.001},{p*0.999},{p*1.0005},1000,000123")
            p *= 1.0001
    f = tmp_path / "000123_5m.csv"
    _write_csv(f, rows)
    res = validate_symbol_csv(str(f), min_bars=100, min_days=20)
    assert res["symbol"] == "000123"   # 데이터 symbol 컬럼 권위 사용
    assert res["status"] in ("OK", "WARN")
    assert res["passes"] is True


def test_validate_bad_ohlc_fails(tmp_path):
    from app.market_data.dataset_validation import validate_symbol_csv
    base = datetime(2025, 1, 2, 9, 0, tzinfo=_KST)
    rows = []
    for i in range(150):
        ts = (base + timedelta(minutes=5 * i)).isoformat()
        # high < low — 잘못된 OHLC.
        rows.append(f"{ts},10000,9000,11000,10000,1000,000999")
    f = tmp_path / "000999_5m.csv"
    _write_csv(f, rows)
    res = validate_symbol_csv(str(f))
    assert res["status"] == "FAIL"
    assert res["passes"] is False


def test_validate_detects_big_jump(tmp_path):
    from app.market_data.dataset_validation import JUMP_FAIL, validate_symbol_csv
    base = datetime(2025, 1, 2, 9, 0, tzinfo=_KST)
    rows = []
    p = 10000.0
    for i in range(150):
        ts = (base + timedelta(minutes=5 * i)).isoformat()
        if i == 75:
            p = p * 2.0  # 100% 점프 → JUMP_FAIL 초과.
        rows.append(f"{ts},{p},{p*1.001},{p*0.999},{p},1000,000888")
    f = tmp_path / "000888_5m.csv"
    _write_csv(f, rows)
    res = validate_symbol_csv(str(f))
    assert res["max_bar_jump_pct"] >= JUMP_FAIL
    assert res["status"] == "FAIL"
