"""#46 / 6-01: 4전략 + Agent Council 백테스트 테스트.

핵심 invariant:
- ORB / Momentum / Gap / VWAP vote + Agent Council final_action 산출.
- BUY 손익 풀 / SELL 방향 평가(신규 숏 아님) / HOLD 라벨 분리.
- 5/10/30/60/close horizon + win_rate / 평균수익·손실 / 손익비 / PF / MDD /
  연속손실 / expectancy + market_regime · time_phase 버킷.
- Agent Council vs best single 비교.
- 데이터 부족 → BACKTEST_INSUFFICIENT_DATA.
- broker / OrderExecutor / route_order / KIS import·호출 0건, secret 노출 0건,
  is_order_signal=False / is_live_authorization=False.
"""

from __future__ import annotations

import json

import pytest

from app.backtest import strategy_council_backtest as scb
from app.backtest.strategy_council_backtest import (
    BACKTEST_INSUFFICIENT_DATA,
    BACKTEST_OK,
    SINGLE_STRATEGIES,
    BacktestInput,
    BacktestReport,
    BacktestTrade,
    evaluate_strategy_vote_performance,
    load_ohlcv_from_records,
    render_markdown_report,
    run_strategy_council_backtest,
    summarize_backtest_report,
)

from tests.fixtures.backtest_ohlcv import (
    duplicate_timestamp_records,
    full_scenario_records,
    insufficient_records,
    make_orb_breakout_day,
    make_trend_day,
    to_csv_text,
)


@pytest.fixture(scope="module")
def report() -> BacktestReport:
    bars = load_ohlcv_from_records(full_scenario_records("005930"))
    return run_strategy_council_backtest(BacktestInput(bars=tuple(bars)))


# ── 1~5. 4 전략 + Council 산출 ────────────────────────────────────────────────


@pytest.mark.parametrize("strat", ["ORB", "MOMENTUM", "GAP", "VWAP"])
def test_each_strategy_evaluated(report, strat):
    assert strat in report.strategies
    r = report.strategies[strat]
    counts = r.signal_counts
    assert set(counts) == {"BUY", "SELL", "HOLD"}
    assert sum(counts.values()) > 0


def test_orb_produces_buy_signals():
    # ORB 돌파 fixture → ORB BUY 신호가 존재.
    bars = load_ohlcv_from_records(make_orb_breakout_day("005930", "2026-05-18"))
    rep = run_strategy_council_backtest(BacktestInput(bars=tuple(bars)))
    assert rep.strategies["ORB"].signal_counts["BUY"] > 0


def test_agent_council_final_action_evaluated(report):
    assert report.council is not None
    fac = report.council.final_action_counts
    assert set(fac) >= {"BUY", "SELL", "HOLD"}
    assert sum(fac.values()) > 0


# ── 6~8. vote / selected_strategies / confidence / quality_score 저장 ────────


def test_council_carries_selected_confidence_quality(report):
    c = report.council
    assert c.avg_confidence is not None
    assert c.avg_quality_score is not None
    assert isinstance(c.selected_strategies_freq, dict)
    # selected_strategies 빈도는 4 단일 전략 키를 가진다.
    assert set(c.selected_strategies_freq) == set(SINGLE_STRATEGIES)


def test_council_trade_carries_selected_strategies():
    bars = load_ohlcv_from_records(make_trend_day("005930", "2026-05-11", direction="up"))
    rep = run_strategy_council_backtest(BacktestInput(bars=tuple(bars)))
    # 모든 council trade 가 confidence/quality_score/selected_strategies 필드를 가짐.
    d = rep.council.to_dict()
    assert "selected_strategies_freq" in d
    assert "avg_confidence" in d and "avg_quality_score" in d


# ── 9~11. BUY / SELL / HOLD 라벨링 ───────────────────────────────────────────


def test_buy_sell_hold_separated():
    # 손익 풀(BUY) 과 SELL 방향 평가가 분리.
    trades = [
        _trade("ORB", "BUY", {"close": 0.02}),
        _trade("ORB", "BUY", {"close": -0.01}),
        _trade("ORB", "SELL", {"close": -0.03}),
        _trade("ORB", "HOLD", {"close": 0.0}),
    ]
    r = evaluate_strategy_vote_performance(
        trades, strategy="ORB", primary_horizon="close",
        horizon_labels=("close",), quantity=1)
    assert r.signal_counts == {"BUY": 2, "SELL": 1, "HOLD": 1}
    # BUY 풀은 2건, SELL 은 방향 평가로 분리.
    assert r.by_horizon["close"]["trade_count"] == 2
    assert r.sell_direction["count"] == 1
    assert "신규 숏 아님" in r.sell_direction["interpretation"]


def test_sell_direction_hit_rate():
    trades = [
        _trade("VWAP", "SELL", {"close": -0.02}),   # 하락 적중.
        _trade("VWAP", "SELL", {"close": 0.01}),    # 미적중.
    ]
    r = evaluate_strategy_vote_performance(
        trades, strategy="VWAP", primary_horizon="close",
        horizon_labels=("close",), quantity=1)
    assert r.sell_direction["count"] == 2
    assert r.sell_direction["down_hit_count"] == 1
    assert r.sell_direction["down_hit_rate"] == 0.5


# ── 12. 5/10/30/60/close horizon ─────────────────────────────────────────────


def test_all_horizons_present(report):
    labels = report.horizon_labels
    assert labels == ("h5", "h10", "h30", "h60", "close")
    for s in SINGLE_STRATEGIES:
        assert set(report.strategies[s].by_horizon) == set(labels)


# ── 13~19. 지표 정확성 (hand-crafted) ────────────────────────────────────────


def _trade(strategy, signal, horizon_returns, *, price=70_000.0, regime="TREND_UP",
           phase="MORNING"):
    return BacktestTrade(
        symbol="005930", timestamp="2026-05-11T00:30:00+00:00", strategy=strategy,
        signal=signal, signal_price=price, horizon_returns=dict(horizon_returns),
        primary_return=horizon_returns.get("close", 0.0), market_regime=regime,
        time_phase=phase, confidence=0.6, score=70.0)


def test_metric_math_known_values():
    # BUY: +1000, +2000, -500, -500 (KRW pnl via ret*price*qty), price=100000, qty=1.
    # ret: +0.01, +0.02, -0.005, -0.005
    trades = [
        _trade("ORB", "BUY", {"close": 0.01}, price=100_000),
        _trade("ORB", "BUY", {"close": 0.02}, price=100_000),
        _trade("ORB", "BUY", {"close": -0.005}, price=100_000),
        _trade("ORB", "BUY", {"close": -0.005}, price=100_000),
    ]
    r = evaluate_strategy_vote_performance(
        trades, strategy="ORB", primary_horizon="close",
        horizon_labels=("close",), quantity=1)
    # pnl: 1000, 2000, -500, -500.
    assert r.win_rate == 0.5                       # 2/4
    assert r.average_win == pytest.approx(1500.0)  # (1000+2000)/2
    assert r.average_loss == pytest.approx(-500.0)
    assert r.payoff_ratio == pytest.approx(3.0)    # 1500/500
    assert r.profit_factor == pytest.approx(3.0)   # 3000/1000
    assert r.max_consecutive_losses == 2
    # expectancy = 0.5*1500 + 0.5*(-500) = 500.
    assert r.expectancy == pytest.approx(500.0)
    # average_return = (0.01+0.02-0.005-0.005)/4 = 0.005
    assert r.average_return == pytest.approx(0.005)


def test_max_drawdown_computed():
    # pnl 순서: +1000, -3000, +500 → 누적 1000, -2000, -1500. peak=1000, trough=-2000 → MDD=3000.
    trades = [
        _trade("ORB", "BUY", {"close": 0.01}, price=100_000),
        _trade("ORB", "BUY", {"close": -0.03}, price=100_000),
        _trade("ORB", "BUY", {"close": 0.005}, price=100_000),
    ]
    r = evaluate_strategy_vote_performance(
        trades, strategy="ORB", primary_horizon="close",
        horizon_labels=("close",), quantity=1)
    assert r.max_drawdown == 3000


def test_profit_factor_none_when_no_losses():
    trades = [_trade("ORB", "BUY", {"close": 0.01}, price=100_000)]
    r = evaluate_strategy_vote_performance(
        trades, strategy="ORB", primary_horizon="close",
        horizon_labels=("close",), quantity=1)
    assert r.profit_factor is None   # 손실 0건 → None (JSON 안전).


# ── 20~21. market_regime / time_phase 버킷 ───────────────────────────────────


def test_by_market_regime_and_time_phase():
    trades = [
        _trade("ORB", "BUY", {"close": 0.02}, regime="TREND_UP", phase="MORNING"),
        _trade("ORB", "BUY", {"close": -0.01}, regime="TREND_DOWN", phase="MIDDAY"),
        _trade("ORB", "BUY", {"close": 0.03}, regime="TREND_UP", phase="MORNING"),
    ]
    r = evaluate_strategy_vote_performance(
        trades, strategy="ORB", primary_horizon="close",
        horizon_labels=("close",), quantity=1)
    assert set(r.by_market_regime) == {"TREND_UP", "TREND_DOWN"}
    assert r.by_market_regime["TREND_UP"]["count"] == 2
    assert r.by_market_regime["TREND_UP"]["win_rate"] == 1.0
    assert set(r.by_time_phase) == {"MORNING", "MIDDAY"}
    assert r.by_time_phase["MORNING"]["count"] == 2


def test_report_has_regime_and_phase_buckets(report):
    # 통합 리포트에서도 버킷이 채워진다 (BUY 신호가 있는 전략 기준).
    any_bucket = any(report.strategies[s].by_market_regime for s in SINGLE_STRATEGIES)
    assert any_bucket


# ── 22. Council vs best single 비교 ──────────────────────────────────────────


def test_council_vs_best_single_comparison(report):
    comp = report.comparison
    assert comp["metric"] == "expectancy"
    assert "council_better_than_best_single" in comp
    assert comp["best_single_strategy"] in SINGLE_STRATEGIES
    assert isinstance(comp["ranking"], list) and len(comp["ranking"]) == 4
    assert "실전 전환" in comp["note"]   # 실전 전환 근거 아님 안내.


# ── 23. 데이터 부족 ──────────────────────────────────────────────────────────


def test_insufficient_data_reason_code():
    bars = load_ohlcv_from_records(insufficient_records("005930"))
    rep = run_strategy_council_backtest(BacktestInput(bars=tuple(bars)))
    assert rep.reason_code == BACKTEST_INSUFFICIENT_DATA
    assert rep.insufficient_data is True
    assert rep.council is None


def test_sufficient_data_reason_ok(report):
    assert report.reason_code == BACKTEST_OK
    assert report.insufficient_data is False


# ── 24. deterministic ────────────────────────────────────────────────────────


def test_deterministic_results():
    bars = tuple(load_ohlcv_from_records(full_scenario_records("005930")))
    r1 = run_strategy_council_backtest(BacktestInput(bars=bars))
    r2 = run_strategy_council_backtest(BacktestInput(bars=bars))
    # generated_at 제외하고 동일.
    d1 = r1.to_dict()
    d2 = r2.to_dict()
    d1.pop("generated_at")
    d2.pop("generated_at")
    assert d1 == d2


# ── 로더: 중복 timestamp 제거 / 정렬 ─────────────────────────────────────────


def test_loader_dedup_and_sort():
    bars = load_ohlcv_from_records(duplicate_timestamp_records("005930"))
    # 10 bar + 2 중복 → 10 (dedup).
    assert len(bars) == 10
    ts = [b.timestamp for b in bars]
    assert ts == sorted(ts)


def test_loader_optional_columns():
    recs = [{
        "symbol": "005930", "timestamp": "2026-05-11T00:00:00+00:00",
        "open": 100, "high": 110, "low": 90, "close": 105, "volume": 1000,
        "vwap": 102.5, "market_regime": "trend_up", "time_phase": "morning",
    }]
    bars = load_ohlcv_from_records(recs)
    assert bars[0].vwap == 102.5
    assert bars[0].market_regime == "TREND_UP"   # upper 정규화.
    assert bars[0].time_phase == "MORNING"


# ── 25~29. 안전 / import 가드 / secret ───────────────────────────────────────


def test_no_forbidden_imports_or_calls():
    src = open(scb.__file__, encoding="utf-8").read()
    forbidden = (
        "from app.brokers", "from app.execution.order_router",
        "from app.execution.executor", "from app.execution.order_executor",
        "from app.execution.paper_trader", "from app.brokers.kis",
        "from app.brokers.mock_broker", "import anthropic", "import openai",
        "import httpx", "import requests",
        ".place_order(", ".cancel_order(", "route_order(",
    )
    for tok in forbidden:
        assert tok not in src, f"forbidden token in module: {tok}"


def test_report_invariants(report):
    assert report.is_order_signal is False
    assert report.is_live_authorization is False
    assert report.auto_apply_allowed is False
    assert report.contains_secret is False


def test_report_invariant_enforced():
    with pytest.raises(ValueError):
        BacktestReport(
            generated_at="x", symbol_count=0, bar_count=0, risk_profile="BALANCED",
            horizon_labels=("close",), primary_horizon="close", strategies={},
            council=None, comparison={}, reason_code=BACKTEST_OK,
            insufficient_data=False, is_live_authorization=True,
        )


def test_no_profit_guarantee_or_live_promotion_language(report):
    text = json.dumps(summarize_backtest_report(report), ensure_ascii=False)
    text += render_markdown_report(report)
    for forbidden in ("수익 보장", "실전 전환 승인", "원금 보장", "guaranteed",
                      "is_live_authorization\": true"):
        assert forbidden not in text


def test_no_secret_in_report(report):
    text = json.dumps(summarize_backtest_report(report), ensure_ascii=False)
    for forbidden in ("kis_app_secret", "access_token", "Bearer ", "sk-ant-",
                      "app_key", "app_secret"):
        assert forbidden not in text


def test_disclaimer_present(report):
    md = render_markdown_report(report)
    assert "Paper 성능 분석 전용" in md
    assert "실거래 권한을 부여하지 않습니다" in md


# ── CSV 라운드트립 ───────────────────────────────────────────────────────────


def test_csv_roundtrip(tmp_path):
    recs = full_scenario_records("005930")
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(to_csv_text(recs), encoding="utf-8")
    from app.backtest.strategy_council_backtest import load_ohlcv_from_csv
    bars = load_ohlcv_from_csv(str(csv_path))
    assert len(bars) > 100
    rep = run_strategy_council_backtest(BacktestInput(bars=tuple(bars)))
    assert rep.reason_code == BACKTEST_OK
