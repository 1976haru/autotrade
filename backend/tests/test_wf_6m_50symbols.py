"""WF-6M-50SYMBOLS-01 — 포트폴리오 자금곡선 시뮬 + 종합 검증 테스트.

실제 KIS 네트워크 / 수집 데이터에 의존하지 않는다 — 합성 bar + 주입 신호로 검증.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.backtest.portfolio_capital_sim import (
    PortfolioSimResult,
    SimConfig,
    run_portfolio_capital_sim,
)
from app.backtest.strategy_council_backtest import OHLCVBar
from app.system import wf_6m_50symbols_report as wf

_KST = timezone(timedelta(hours=9))
_SRC = Path(__file__).resolve().parents[1] / "app"


def _day_bars(symbol: str, date: str, prices: list[float]) -> list[OHLCVBar]:
    y, m, d = int(date[:4]), int(date[4:6]), int(date[6:8])
    bars = []
    for i, p in enumerate(prices):
        ts = datetime(y, m, d, 9, 0, tzinfo=_KST) + timedelta(minutes=5 * i)
        bars.append(OHLCVBar(symbol=symbol, timestamp=ts, open=p, high=p * 1.001,
                             low=p * 0.999, close=p, volume=10000))
    return bars


def _winning_day(symbol: str, date: str, n: int = 10, signal_at: int = 5):
    """signal_at 이후 +3% 목표를 치는 상승 시퀀스 + 신호 주입."""
    prices = [10000.0] * (signal_at + 1)
    # 신호 후 상승: target = entry*1.03 을 2 bar 안에 hit.
    prices += [10300.0, 10400.0] + [10400.0] * (n - signal_at - 3)
    bars = _day_bars(symbol, date, prices[:n])
    sig_ts = bars[signal_at].timestamp.isoformat()
    return bars, {(symbol, sig_ts): {"confidence": 0.9, "stop_pct": 1.5, "target_pct": 3.0}}


# ─────────────────────────── portfolio sim ───────────────────────────

def test_max_positions_cap_enforced():
    bars = []
    signals = {}
    for k in range(6):  # 6 종목 동시 신호 → 5 만 진입.
        b, s = _winning_day(f"{k:06d}", "20260520")
        bars += b
        signals.update(s)
    r = run_portfolio_capital_sim(bars, SimConfig(initial_capital=10_000_000,
                                                  max_position_notional=2_000_000), signals=signals)
    assert r.skipped_max_positions >= 1          # 6번째 진입 차단.
    assert r.total_trades == 5                   # 정확히 5 종목 진입.


def test_no_overnight_all_closed():
    bars, signals = _winning_day("005930", "20260520")
    r = run_portfolio_capital_sim(bars, SimConfig(), signals=signals)
    # 모든 포지션 청산 → 최종 자산 = 현금 (포지션 0).
    assert r.total_trades == 1
    assert r.avg_hold_minutes is not None


def test_winning_trade_hits_target_after_costs():
    bars, signals = _winning_day("005930", "20260520")
    r = run_portfolio_capital_sim(bars, SimConfig(), signals=signals)
    assert r.total_trades == 1
    assert r.win_rate == 1.0
    # 목표 +3% 지만 비용(수수료+세금+슬리피지) 차감 후 순이익 < gross.
    assert r.total_return_pct is not None and r.total_return_pct > 0


def test_cash_constraint_blocks_entry():
    # 자본 작게 → 일부 진입 불가.
    bars = []
    signals = {}
    for k in range(4):
        b, s = _winning_day(f"{k:06d}", "20260520")
        bars += b
        signals.update(s)
    r = run_portfolio_capital_sim(bars, SimConfig(
        initial_capital=2_500_000, max_position_notional=2_000_000,
        min_position_notional=1_000_000), signals=signals)
    # 2.5M 으로 최대 2개(2M+0.5M<1M 불가) → 1~2 진입, 나머지 현금부족 skip.
    assert r.total_trades <= 2
    assert r.skipped_no_cash >= 1


def test_sim_result_invariants():
    bars, signals = _winning_day("005930", "20260520")
    r = run_portfolio_capital_sim(bars, SimConfig(), signals=signals)
    assert r.is_live_authorization is False
    assert r.broker_order_sent is False
    assert r.order_created is False
    assert r.do_not_auto_apply is True
    assert r.no_profit_guarantee is True
    assert r.contains_secret is False


def test_sim_result_rejects_unsafe():
    with pytest.raises(ValueError):
        PortfolioSimResult(
            initial_capital=1, final_equity=1, total_return_pct=0, total_trades=0,
            trading_days=0, win_rate=None, avg_win=None, avg_loss=None, payoff_ratio=None,
            expectancy=None, profit_factor=None, max_drawdown_pct=None, max_drawdown_krw=None,
            daily_avg_trades=None, daily_avg_return_pct=None, weekly_avg_return_pct=None,
            monthly_avg_return_pct=None, worst_day_pnl=None, worst_day_return_pct=None,
            worst_consecutive_losses=0, avg_hold_minutes=None, skipped_no_cash=0,
            skipped_max_positions=0, equity_curve=(), monthly_returns={}, per_symbol=(),
            is_live_authorization=True)


# ─────────────────────────── grading + verdict ───────────────────────────

def test_grade_go():
    v = {"included": True, "profit_factor": 1.6, "expectancy": 100.0,
         "walk_forward_score": 55.0, "agent_value_verdict": "AGENT_ADDS_VALUE", "trades": 20}
    assert wf._grade_symbol(v, 50000) == wf.GO


def test_grade_exclude():
    v = {"included": True, "profit_factor": 0.7, "expectancy": -50.0,
         "walk_forward_score": 0.0, "agent_value_verdict": "AGENT_UNDERPERFORMS", "trades": 20}
    assert wf._grade_symbol(v, -30000) == wf.EXCLUDE


def test_grade_tune_when_wf_weak():
    v = {"included": True, "profit_factor": 1.5, "expectancy": 80.0,
         "walk_forward_score": 0.0, "agent_value_verdict": "AGENT_ADDS_VALUE", "trades": 20}
    assert wf._grade_symbol(v, 10000) == wf.TUNE


def test_grade_watch_low_trades():
    v = {"included": True, "profit_factor": 1.5, "expectancy": 80.0,
         "walk_forward_score": 55.0, "agent_value_verdict": "AGENT_ADDS_VALUE", "trades": 3}
    assert wf._grade_symbol(v, 10000) == wf.WATCH


def test_verdict_not_recommended_on_loss():
    v, live, _ = wf._final_verdict(
        enough_history=True, pass_symbols=40, total_trades=300, total_return=-5.0,
        pf=0.8, wf=0.0, mdd=20.0, agent_summary="AGENT_UNDERPERFORMS")
    assert v == wf.NOT_RECOMMENDED


def test_verdict_research_when_wf_collapses_but_positive():
    v, live, _ = wf._final_verdict(
        enough_history=True, pass_symbols=40, total_trades=300, total_return=8.0,
        pf=1.5, wf=0.0, mdd=5.0, agent_summary="AGENT_UNDERPERFORMS")
    assert v == wf.WORTH_MORE_RESEARCH


def test_verdict_paper_worthy_when_all_strong():
    v, live, _ = wf._final_verdict(
        enough_history=True, pass_symbols=40, total_trades=300, total_return=8.0,
        pf=1.5, wf=55.0, mdd=5.0, agent_summary="AGENT_ADDS_VALUE")
    assert v == wf.PAPER_REHEARSAL_WORTHY


def test_report_dataclass_rejects_unsafe():
    base = dict(
        generated_at="t", data_source="x", symbols_count=1, pass_symbols=1, trading_days=1,
        total_bars=1, bar_size_minutes=5.0, enough_history=False, initial_capital=1e7,
        final_equity=1e7, total_return_pct=0.0, daily_avg_trades=0.0, daily_avg_return_pct=0.0,
        weekly_avg_return_pct=0.0, monthly_avg_return_pct=0.0, win_rate=None, payoff_ratio=None,
        expectancy=None, profit_factor=None, max_drawdown_pct=None, worst_day_pnl=None,
        worst_consecutive_losses=0, avg_hold_minutes=None, skipped_max_positions=0,
        monthly_returns={}, equity_curve_points=0, grades={"GO": 0, "WATCH": 0, "TUNE": 0, "EXCLUDE": 0},
        top_10=(), bottom_10=(), exclude_recommended=(), agent_helped_symbols=(),
        agent_hurt_symbols=(), strategy_survival={}, strategy_alive=(), strategy_dead=(),
        council_better_than_best_single=None, regime_buckets={}, time_phase_buckets={},
        median_walk_forward_score=None, agent_value_summary="x", final_verdict=wf.RESEARCH_ONLY,
        live_possibility="x", strengths_top3=(), weaknesses_top3=(), agent_optimal_role="x",
        upgrade_directions=(), expansion_assessment={}, next_steps=())
    with pytest.raises(ValueError):
        wf.Wf6mReport(**{**base, "is_live_authorization": True})
    with pytest.raises(ValueError):
        wf.Wf6mReport(**{**base, "final_verdict": "BOGUS"})


# ─────────────────────────── static guards ───────────────────────────

@pytest.mark.parametrize("modfile", [
    "backtest/portfolio_capital_sim.py",
    "system/wf_6m_50symbols_report.py",
])
def test_modules_no_order_imports_or_calls(modfile):
    src = (_SRC / modfile).read_text(encoding="utf-8")
    for ln in src.splitlines():
        if re.match(r"^\s*(from|import)\s", ln):
            for mod in ("app.brokers", "app.execution", "order_router",
                        "anthropic", "openai", "httpx", "requests", "app.ai.client"):
                assert mod not in ln, f"{modfile} forbidden import: {ln.strip()}"
    for call in ("route_order(", ".place_order(", "OrderExecutor(", ".cancel_order("):
        assert call not in src, f"{modfile} forbidden call: {call}"


# ─────────────────────────── endpoint ───────────────────────────

def test_wf_6m_endpoint(client, safe_default_flags):
    r = client.get("/api/system/wf-6m-50symbols/latest")
    assert r.status_code == 200, r.text
    d = r.json()
    assert "final_verdict" in d
    assert d["is_live_authorization"] is False
    assert d["broker_order_sent"] is False
    assert d["order_created"] is False
    assert d["do_not_auto_apply"] is True
    assert d["no_profit_guarantee"] is True
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", r.text)
    assert not re.search(r"\b\d{8}-\d{2}\b", r.text)


def test_wf_6m_endpoint_get_only(client, safe_default_flags):
    assert client.post("/api/system/wf-6m-50symbols/latest", json={}).status_code in (404, 405)
