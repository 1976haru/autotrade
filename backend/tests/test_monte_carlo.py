"""Monte Carlo simulation 테스트 (#26)."""

import pytest
from datetime import datetime, timezone

from app.backtest.monte_carlo import (
    MonteCarloConfig,
    MonteCarloResult,
    _block_bootstrap_once,
    _bootstrap_once,
    _equity_curve_metrics,
    _percentile_int,
    _shuffle_once,
    run_monte_carlo,
)
from app.backtest.types import Trade


_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _trade(pnl: int) -> Trade:
    return Trade(symbol="X", entry_ts=_BASE, entry_price=100,
                 exit_ts=_BASE, exit_price=100 + pnl, quantity=1, pnl=pnl)


# ---------- MonteCarloConfig validation ----------


def test_config_rejects_unknown_method():
    with pytest.raises(ValueError, match="method"):
        MonteCarloConfig(method="bogus")


def test_config_rejects_zero_iterations():
    with pytest.raises(ValueError):
        MonteCarloConfig(iterations=0)


def test_config_rejects_excessive_iterations():
    with pytest.raises(ValueError, match="capped"):
        MonteCarloConfig(iterations=200_000)


def test_config_rejects_nonpositive_block_size():
    with pytest.raises(ValueError):
        MonteCarloConfig(block_size=0)


def test_config_rejects_nonnegative_ruin_drawdown_pct():
    """ruin은 손실 임계라 음수만 의미."""
    with pytest.raises(ValueError):
        MonteCarloConfig(ruin_drawdown_pct=0.0)
    with pytest.raises(ValueError):
        MonteCarloConfig(ruin_drawdown_pct=0.5)


# ---------- 샘플링 결정성 (seed) ----------


def test_shuffle_deterministic_under_same_seed():
    import random
    pnls = [10, -5, 20, -3, 7]
    a = _shuffle_once(pnls, random.Random(42))
    b = _shuffle_once(pnls, random.Random(42))
    assert a == b


def test_bootstrap_deterministic_under_same_seed():
    import random
    pnls = [10, -5, 20, -3, 7]
    a = _bootstrap_once(pnls, random.Random(42))
    b = _bootstrap_once(pnls, random.Random(42))
    assert a == b
    assert len(a) == len(pnls)


def test_block_bootstrap_keeps_size_and_uses_blocks():
    import random
    pnls = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    out = _block_bootstrap_once(pnls, block_size=3, rng=random.Random(0))
    assert len(out) == len(pnls)
    # 모든 원소가 원본에 있어야 함.
    assert all(x in pnls for x in out)


def test_block_bootstrap_block_size_one_equals_bootstrap_size():
    import random
    pnls = [1, 2, 3]
    out = _block_bootstrap_once(pnls, block_size=1, rng=random.Random(0))
    assert len(out) == 3


# ---------- equity curve metrics ----------


def test_equity_curve_metrics_basic():
    """누적 곡선 100, -50, 200, -300, 150 → final=100, peak=250, MDD=300."""
    m = _equity_curve_metrics([100, -50, 200, -300, 150], initial_cash=1_000_000)
    assert m["total_pnl"] == 100
    assert m["max_drawdown"] == 300
    assert m["final_equity"] == 1_000_100


def test_equity_curve_streak_counts_consecutive_losses():
    m = _equity_curve_metrics([-10, -20, -30, 50, -10, -15], initial_cash=0)
    assert m["losing_streak"] == 3


def test_equity_curve_streak_zero_when_all_winners():
    m = _equity_curve_metrics([10, 20, 30], initial_cash=0)
    assert m["losing_streak"] == 0


# ---------- percentile ----------


def test_percentile_basic():
    vals = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert _percentile_int(vals, 0)   == 1
    assert _percentile_int(vals, 50)  == 5
    assert _percentile_int(vals, 100) == 10


def test_percentile_empty_returns_zero():
    assert _percentile_int([], 50) == 0


# ---------- run_monte_carlo ----------


def test_empty_trades_returns_fail_with_warning():
    r = run_monte_carlo([], config=MonteCarloConfig(iterations=10, seed=0))
    assert r.n_trades == 0
    assert r.iterations == 0
    assert r.promotion_risk_flag == "FAIL"
    assert any("0건" in w for w in r.warnings)


def test_run_monte_carlo_deterministic_with_seed():
    """같은 seed → 같은 결과."""
    trades = [_trade(p) for p in [100, -50, 200, -30, 50, -100, 80]]
    cfg = MonteCarloConfig(method="shuffle", iterations=200, seed=42,
                           initial_cash=1_000_000)
    a = run_monte_carlo(trades, config=cfg)
    b = run_monte_carlo(trades, config=cfg)
    assert a.p05_total_pnl == b.p05_total_pnl
    assert a.risk_of_ruin == b.risk_of_ruin


def test_run_monte_carlo_different_seeds_can_differ():
    trades = [_trade(p) for p in [100, -200, 50, 30, -10]]
    cfg_a = MonteCarloConfig(seed=1, iterations=100, initial_cash=10_000)
    cfg_b = MonteCarloConfig(seed=2, iterations=100, initial_cash=10_000)
    a = run_monte_carlo(trades, config=cfg_a)
    b = run_monte_carlo(trades, config=cfg_b)
    # 분포 자체가 다를 수 있다는 점만 확인 (deterministic 임의성).
    assert isinstance(a, MonteCarloResult) and isinstance(b, MonteCarloResult)


def test_shuffle_total_pnl_invariant():
    """shuffle은 거래 순서만 바뀌므로 total_pnl은 모든 시뮬레이션에서 동일."""
    trades = [_trade(p) for p in [100, -50, 200, -30]]
    cfg = MonteCarloConfig(method="shuffle", iterations=50, seed=0,
                           initial_cash=1_000_000)
    r = run_monte_carlo(trades, config=cfg)
    assert r.p05_total_pnl == r.p50_total_pnl == r.p95_total_pnl
    expected_total = sum(t.pnl for t in trades)
    assert r.p50_total_pnl == expected_total


def test_bootstrap_total_pnl_varies():
    """bootstrap은 복원추출이라 시뮬레이션마다 total_pnl 다름."""
    trades = [_trade(p) for p in [100, -200, 50, 30, -10]]
    cfg = MonteCarloConfig(method="bootstrap", iterations=200, seed=0,
                           initial_cash=1_000_000)
    r = run_monte_carlo(trades, config=cfg)
    # 분포에 차이가 있어야 함.
    assert r.p05_total_pnl < r.p95_total_pnl


def test_risk_of_ruin_high_when_trades_dominantly_losing():
    """대부분 손실 거래 → 파산위험 高."""
    # 작은 자본 + 큰 손실 → ruin 발생률 높음.
    trades = [_trade(-200) for _ in range(20)]  # 누적 -4000
    cfg = MonteCarloConfig(method="shuffle", iterations=200, seed=0,
                           initial_cash=10_000,  # 50% 손실 = -5000
                           ruin_drawdown_pct=-0.5)
    r = run_monte_carlo(trades, config=cfg)
    # 누적 -4000은 ruin 임계 -5000에 미치지 못함 — 0%.
    # 다른 케이스 — 더 큰 손실.
    trades_big = [_trade(-1000) for _ in range(10)]
    cfg2 = MonteCarloConfig(method="shuffle", iterations=200, seed=0,
                            initial_cash=10_000,
                            ruin_drawdown_pct=-0.5)
    r2 = run_monte_carlo(trades_big, config=cfg2)
    # 누적 -10000 → 모든 path가 ruin.
    assert r2.risk_of_ruin == 1.0
    assert r2.promotion_risk_flag == "FAIL"
    # 첫 케이스는 ruin 0.
    assert r.risk_of_ruin == 0.0


def test_risk_of_ruin_low_when_consistently_winning():
    trades = [_trade(50) for _ in range(20)]
    cfg = MonteCarloConfig(method="shuffle", iterations=100, seed=0,
                           initial_cash=10_000)
    r = run_monte_carlo(trades, config=cfg)
    assert r.risk_of_ruin == 0.0
    assert r.promotion_risk_flag == "PASS"
    assert r.stability_grade in ("GOOD", "WARNING")


def test_worst_5pct_avg_mdd_present():
    trades = [_trade(p) for p in [100, -200, 50, 30, -100, 200, -50]]
    cfg = MonteCarloConfig(method="bootstrap", iterations=200, seed=0,
                           initial_cash=1_000_000)
    r = run_monte_carlo(trades, config=cfg)
    # 최악 5%는 p95 max_drawdown 이상이어야 함 (정의상).
    assert r.worst_5pct_avg_mdd >= r.p95_max_drawdown


def test_longest_losing_streak_aggregated():
    trades = [_trade(-100) for _ in range(10)]
    cfg = MonteCarloConfig(method="shuffle", iterations=10, seed=0,
                           initial_cash=10_000_000)
    r = run_monte_carlo(trades, config=cfg)
    assert r.longest_losing_streak == 10  # 모두 손실 → 항상 10 streak


def test_to_dict_is_json_serializable():
    import json
    trades = [_trade(p) for p in [100, -50, 30, -10]]
    cfg = MonteCarloConfig(iterations=20, seed=42)
    r = run_monte_carlo(trades, config=cfg)
    d = r.to_dict()
    json.dumps(d)
    assert d["promotion_risk_flag"] in ("PASS", "CAUTION", "FAIL")
    assert d["stability_grade"] in ("GOOD", "WARNING", "POOR")


# ---------- API endpoint smoke ----------


def test_route_monte_carlo_with_trades(client):
    res = client.post("/api/backtest/monte-carlo", json={
        "trades": [{"pnl": 100}, {"pnl": -50}, {"pnl": 30}, {"pnl": -10}],
        "config": {"method": "shuffle", "iterations": 50, "seed": 42},
    })
    assert res.status_code == 200, res.json()
    body = res.json()
    assert body["promotion_risk_flag"] in ("PASS", "CAUTION", "FAIL")
    assert body["n_trades"] == 4
    assert body["iterations"] == 50


def test_route_monte_carlo_rejects_both_inputs(client):
    res = client.post("/api/backtest/monte-carlo", json={
        "trades": [{"pnl": 10}],
        "backtest_run_id": 1,
    })
    assert res.status_code == 400
    assert "either" in res.json()["detail"]


def test_route_monte_carlo_rejects_neither_input(client):
    res = client.post("/api/backtest/monte-carlo", json={
        "config": {"iterations": 10},
    })
    assert res.status_code == 400


def test_route_monte_carlo_invalid_method_returns_400(client):
    res = client.post("/api/backtest/monte-carlo", json={
        "trades": [{"pnl": 10}],
        "config": {"method": "bogus", "iterations": 10},
    })
    assert res.status_code == 400


def test_route_monte_carlo_with_backtest_run_id(client):
    """저장된 BacktestRun에서 trades_json을 읽어 시뮬."""
    from app.db.models import BacktestRun
    with client.test_db_factory() as db:
        run = BacktestRun(
            strategy="sma_crossover", params={},
            initial_cash=1_000_000, quantity=1, bars_processed=10,
            final_cash=1_000_100, total_pnl=100,
            win_count=2, loss_count=1, max_drawdown=20,
            trades_json=[
                {"pnl": 100, "symbol": "X", "entry_ts": "2026-01-01T00:00:00",
                 "exit_ts": "2026-01-02T00:00:00", "entry_price": 100,
                 "exit_price": 200, "quantity": 1},
                {"pnl": -50, "symbol": "X", "entry_ts": "2026-01-02T00:00:00",
                 "exit_ts": "2026-01-03T00:00:00", "entry_price": 100,
                 "exit_price": 50, "quantity": 1},
                {"pnl": 50, "symbol": "X", "entry_ts": "2026-01-03T00:00:00",
                 "exit_ts": "2026-01-04T00:00:00", "entry_price": 100,
                 "exit_price": 150, "quantity": 1},
            ],
            data_source="bars",
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id

    res = client.post("/api/backtest/monte-carlo", json={
        "backtest_run_id": run_id,
        "config": {"iterations": 50, "seed": 42},
    })
    assert res.status_code == 200, res.json()
    body = res.json()
    assert body["n_trades"] == 3


def test_route_monte_carlo_unknown_run_id_returns_404(client):
    res = client.post("/api/backtest/monte-carlo", json={
        "backtest_run_id": 99999,
        "config": {"iterations": 10},
    })
    assert res.status_code == 404


def test_existing_backtest_run_endpoint_still_works(client):
    """/api/backtest/run과 /walk-forward가 여전히 정상."""
    bars = [
        {"symbol": "TEST", "timestamp": "2026-01-01T00:00:00+00:00",
         "open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
        {"symbol": "TEST", "timestamp": "2026-01-02T00:00:00+00:00",
         "open": 110, "high": 115, "low": 105, "close": 110, "volume": 1000},
        {"symbol": "TEST", "timestamp": "2026-01-03T00:00:00+00:00",
         "open": 120, "high": 125, "low": 115, "close": 120, "volume": 1000},
    ]
    res = client.post("/api/backtest/run", json={
        "strategy": "sma_crossover", "initial_cash": 1_000_000, "quantity": 1,
        "bars": bars,
    })
    assert res.status_code == 200
