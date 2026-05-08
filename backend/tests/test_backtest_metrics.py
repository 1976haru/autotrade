"""metrics.py 단위 테스트 + 라우트 응답 신규 필드 검증 (#24)."""

import json
from datetime import datetime, timezone

from app.backtest.metrics import (
    avg_loss,
    avg_loss_legacy,
    avg_win,
    equity_curve,
    expectancy,
    extract_trade_pnl,
    flat_count,
    hourly_pnl,
    loss_count,
    max_consecutive_losses,
    max_consecutive_wins,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
    summarize_metrics,
    total_pnl,
    win_count,
    win_rate,
)
from app.backtest.types import Trade


_BASE = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)


def _trade(pnl, *, hour=None, ep=100, q=1):
    """테스트용 trade. hour=None이면 _BASE 시각, 정수면 그 시각의 hour로 고정."""
    ts = _BASE if hour is None else _BASE.replace(hour=hour)
    return Trade(
        symbol="X", entry_ts=ts, entry_price=ep,
        exit_ts=ts, exit_price=ep + pnl, quantity=q, pnl=pnl,
    )


# ---------- 안전 처리 ----------


def test_extract_pnl_handles_none():
    assert extract_trade_pnl(None) == 0


def test_extract_pnl_falls_back_to_dict():
    assert extract_trade_pnl({"pnl": 50}) == 50


def test_empty_trades_returns_safe_defaults():
    assert total_pnl([]) == 0
    assert win_count([]) == 0
    assert loss_count([]) == 0
    assert flat_count([]) == 0
    assert win_rate([]) == 0.0
    assert avg_win([]) == 0.0
    assert avg_loss([]) == 0.0
    assert expectancy([]) == 0.0
    assert profit_factor([]) is None
    assert sharpe_ratio([]) is None
    assert max_drawdown([]) == 0
    assert max_consecutive_losses([]) == 0
    assert max_consecutive_wins([]) == 0
    assert hourly_pnl([]) == {}


# ---------- 기본 통계 ----------


def test_total_pnl_and_counts():
    ts = [_trade(100), _trade(-50), _trade(0), _trade(200), _trade(-30)]
    assert total_pnl(ts)  == 220
    assert win_count(ts)  == 2
    assert loss_count(ts) == 2
    assert flat_count(ts) == 1


def test_win_rate_includes_flats_in_denominator():
    ts = [_trade(100), _trade(0), _trade(0)]
    assert abs(win_rate(ts) - (1 / 3)) < 1e-9


def test_avg_win_and_loss_split_by_sign_only():
    ts = [_trade(100), _trade(-40), _trade(60), _trade(-20), _trade(0)]
    assert avg_win(ts)  == 80.0    # (100+60)/2
    assert avg_loss(ts) == -30.0   # (-40-20)/2  (flat 제외)


def test_avg_loss_legacy_includes_flats():
    """기존 BacktestResult.avg_loss와 동일 의미 (pnl <= 0 평균)."""
    ts = [_trade(100), _trade(-40), _trade(0)]
    # legacy: (-40 + 0) / 2 = -20
    assert avg_loss_legacy(ts) == -20.0
    # 신규: only -40 → -40
    assert avg_loss(ts) == -40.0


# ---------- 핵심 지표 ----------


def test_expectancy_uses_strict_loss_only():
    """expectancy = win_rate × avg_win + loss_rate × avg_loss (음수 손실 유지)."""
    ts = [_trade(100), _trade(-50)]
    # win_rate=0.5, avg_win=100, loss_rate=0.5, avg_loss=-50
    # expectancy = 0.5*100 + 0.5*-50 = 25
    assert expectancy(ts) == 25.0


def test_expectancy_zero_when_only_flats():
    ts = [_trade(0), _trade(0)]
    assert expectancy(ts) == 0.0


def test_profit_factor_basic():
    ts = [_trade(100), _trade(-50), _trade(50), _trade(-25)]
    # gross_win=150, gross_loss=75 → 2.0
    assert profit_factor(ts) == 2.0


def test_profit_factor_none_when_no_losses():
    ts = [_trade(100), _trade(50)]
    assert profit_factor(ts) is None


def test_profit_factor_none_when_empty():
    assert profit_factor([]) is None


def test_sharpe_none_for_single_trade():
    assert sharpe_ratio([_trade(100)]) is None


def test_sharpe_none_when_zero_variance():
    """모두 같은 return → stdev=0."""
    assert sharpe_ratio([_trade(10), _trade(10)]) is None


def test_sharpe_positive_when_returns_skew_up():
    s = sharpe_ratio([_trade(20), _trade(-10)])
    assert s is not None and s > 0


def test_sharpe_finite_only():
    """NaN/inf는 None으로 sanitize."""
    s = sharpe_ratio([_trade(10), _trade(20)])  # 동일 부호
    # 결과가 None 또는 finite float
    assert s is None or (s == s)  # NaN check via self-equality


def test_max_drawdown_basic():
    ts = [_trade(100), _trade(-50), _trade(200), _trade(-300), _trade(150)]
    # cum: 100, 50, 250, -50, 100. peak=250, trough=-50 → DD=300
    assert max_drawdown(ts) == 300


# ---------- 연속 구간 ----------


def test_max_consecutive_losses_basic():
    ts = [_trade(-10), _trade(-20), _trade(-30), _trade(50), _trade(-10), _trade(-15)]
    assert max_consecutive_losses(ts) == 3


def test_max_consecutive_wins_basic():
    ts = [_trade(10), _trade(20), _trade(-10), _trade(30), _trade(40), _trade(50)]
    assert max_consecutive_wins(ts) == 3


def test_consecutive_with_flats_breaks_streak():
    """flat은 streak를 끊는 보수적 의미."""
    ts = [_trade(-10), _trade(0), _trade(-10), _trade(-10)]
    assert max_consecutive_losses(ts) == 2


def test_consecutive_zero_when_no_streak():
    ts = [_trade(0), _trade(0), _trade(0)]
    assert max_consecutive_wins(ts) == 0
    assert max_consecutive_losses(ts) == 0


# ---------- 시간대별 손익 ----------


def test_hourly_pnl_aggregates_by_exit_hour_utc():
    ts = [
        _trade(100, hour=9),
        _trade(-50, hour=9),
        _trade(200, hour=15),
        _trade(-30, hour=15),
    ]
    out = hourly_pnl(ts)
    assert out[9]  == 50    # 100 - 50
    assert out[15] == 170   # 200 - 30


def test_hourly_pnl_marks_unknown_when_exit_ts_missing():
    """exit_ts가 None인 dict 거래는 -1 키로 분리."""
    ts = [{"pnl": 10, "exit_ts": None}, {"pnl": -20, "exit_ts": None}]
    out = hourly_pnl(ts)
    assert out == {-1: -10}


def test_hourly_pnl_handles_naive_datetime_as_utc():
    naive_trade = Trade(
        symbol="X",
        entry_ts=datetime(2026, 5, 18, 9, 0),
        entry_price=100,
        exit_ts=datetime(2026, 5, 18, 12, 30),  # naive UTC 가정
        exit_price=110, quantity=1, pnl=10,
    )
    assert hourly_pnl([naive_trade]) == {12: 10}


# ---------- equity curve ----------


def test_equity_curve_includes_initial_point():
    out = equity_curve([_trade(50), _trade(-20)], initial_cash=1_000_000)
    # [{None, 1M}, {ts, 1M+50}, {ts, 1M+30}]
    assert len(out) == 3
    assert out[0]["timestamp"] is None
    assert out[0]["equity"] == 1_000_000.0
    assert out[-1]["equity"] == 1_000_030.0


def test_equity_curve_sorts_by_exit_ts():
    """exit_ts 오름차순 정렬 — 시간 역순 입력도 정상 처리."""
    later = _trade(50, hour=15)
    earlier = _trade(-20, hour=10)
    out = equity_curve([later, earlier])
    # earlier(10시)가 먼저: 0 → -20 → 30
    assert out[1]["equity"] == -20.0
    assert out[2]["equity"] == 30.0


def test_equity_curve_empty_returns_initial_only():
    out = equity_curve([], initial_cash=500_000)
    assert out == [{"timestamp": None, "equity": 500_000.0}]


# ---------- summary ----------


def test_summarize_metrics_includes_all_keys():
    ts = [_trade(100, hour=9), _trade(-50, hour=15)]
    out = summarize_metrics(ts, initial_cash=1_000_000)
    expected_keys = {
        "trade_count", "total_pnl", "win_count", "loss_count", "flat_count",
        "win_rate", "avg_win", "avg_loss", "expectancy",
        "profit_factor", "sharpe_ratio", "max_drawdown",
        "max_consecutive_wins", "max_consecutive_losses", "hourly_pnl",
        "initial_cash",
    }
    assert set(out.keys()) == expected_keys


def test_summarize_metrics_is_json_serializable():
    ts = [_trade(100, hour=9), _trade(-50, hour=15), _trade(0)]
    out = summarize_metrics(ts)
    # JSON 직렬화 — None / NaN / inf 없음.
    s = json.dumps(out, default=str)
    assert "NaN" not in s
    assert "Infinity" not in s


def test_summarize_metrics_empty_safe():
    out = summarize_metrics([])
    assert out["trade_count"] == 0
    assert out["profit_factor"] is None
    assert out["sharpe_ratio"] is None


# ---------- BacktestResult 위임 ----------


def test_backtest_result_exposes_new_metrics():
    from app.backtest.types import BacktestResult
    ts = [_trade(100, hour=9), _trade(-50, hour=15), _trade(-30, hour=15)]
    r = BacktestResult(trades=ts, initial_cash=1_000_000, final_cash=1_000_020)
    assert r.expectancy != 0.0
    assert r.max_consecutive_losses == 2
    assert r.max_consecutive_wins == 1
    assert r.flat_count == 0
    assert r.hourly_pnl == {9: 100, 15: -80}
    summary = r.summarize_metrics()
    assert summary["trade_count"] == 3


# ---------- API 응답 ----------


def _basic_bars_payload():
    return [
        {"symbol": "TEST", "timestamp": "2026-01-01T00:00:00+00:00",
         "open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
        {"symbol": "TEST", "timestamp": "2026-01-02T00:00:00+00:00",
         "open": 110, "high": 115, "low": 105, "close": 110, "volume": 1000},
        {"symbol": "TEST", "timestamp": "2026-01-03T00:00:00+00:00",
         "open": 120, "high": 125, "low": 115, "close": 120, "volume": 1000},
    ]


def test_api_run_response_includes_new_metrics(client):
    res = client.post("/api/backtest/run", json={
        "strategy": "sma_crossover", "initial_cash": 1_000_000, "quantity": 1,
        "bars": _basic_bars_payload(),
    })
    assert res.status_code == 200
    body = res.json()
    for key in ("expectancy", "flat_count", "max_consecutive_wins",
                "max_consecutive_losses", "hourly_pnl"):
        assert key in body, f"missing: {key}"
    # 거래가 있다면 hourly_pnl도 dict.
    assert isinstance(body["hourly_pnl"], dict)


def test_api_run_response_safe_when_no_trades(client):
    """전략이 거래를 만들지 않아도 신규 필드는 안전 default."""
    res = client.post("/api/backtest/run", json={
        "strategy": "sma_crossover",
        "params": {"short": 99, "long": 100},  # 거래 없도록 큰 윈도우
        "initial_cash": 1_000_000, "quantity": 1,
        "bars": _basic_bars_payload(),
    })
    assert res.status_code == 200
    body = res.json()
    assert body["expectancy"] == 0.0
    assert body["max_consecutive_wins"] == 0
    assert body["max_consecutive_losses"] == 0
    assert body["hourly_pnl"] == {}
