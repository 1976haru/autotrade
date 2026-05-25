"""KIS-INTRADAY-50-6M-ROOT-CAUSE-REBUILD-01 — sim_v2 / verdict / 안전 테스트.

실제 수집 데이터에 의존하지 않는다 — 합성 bar + 주입 신호 + verdict 단위 테스트.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.backtest.strategy_council_backtest import OHLCVBar
from app.system import wf_6m_rebuild_experiments as rb
from app.system.wf_6m_sim_v2 import SimV2Config, SimV2Result, run_sim_v2

_KST = timezone(timedelta(hours=9))
_SRC = Path(__file__).resolve().parents[1] / "app"


def _day(symbol, date, prices, *, minute0=0):
    y, m, d = int(date[:4]), int(date[4:6]), int(date[6:8])
    out = []
    for i, p in enumerate(prices):
        ts = datetime(y, m, d, 9, 0, tzinfo=_KST) + timedelta(minutes=5 * (i + minute0))
        out.append(OHLCVBar(symbol=symbol, timestamp=ts, open=p, high=p * 1.001,
                            low=p * 0.999, close=p, volume=10000))
    return out


def _sig(minute_of_day=545, council="BUY", single=("ORB",), stop=1.5, target=3.0,
         conf=0.8, bucket="09:00-09:30", fwd=0.01):
    return {"council_action": council, "confidence": conf, "quality_score": 70.0,
            "stop_pct": stop, "target_pct": target, "selected": ("ORB",),
            "single_buys": tuple(single), "minute_of_day": minute_of_day,
            "time_bucket": bucket, "fwd_eod_return": fwd}


def _winning_setup(symbols, date="20260120", n=10, signal_at=5):
    bars, signals = [], {}
    for s in symbols:
        prices = [10000.0] * (signal_at + 1) + [10300.0, 10400.0] + [10400.0] * (n - signal_at - 3)
        b = _day(s, date, prices[:n])
        bars += b
        kst = b[signal_at].timestamp.astimezone(_KST)
        signals[(s, b[signal_at].timestamp.isoformat())] = _sig(
            minute_of_day=kst.hour * 60 + kst.minute)
    return bars, signals


# ─────────────────────────── sim_v2 mechanics ───────────────────────────

def test_universe_filter_restricts_symbols():
    bars, signals = _winning_setup([f"{i:06d}" for i in range(4)])
    r = run_sim_v2(bars, signals, SimV2Config(universe=frozenset({"000000", "000001"})))
    # 2 종목만 universe → 거래도 2 이하.
    assert r.trade_count <= 2


def test_agent_off_uses_single_union():
    # council HOLD 이지만 single BUY → AGENT_OFF 는 진입, ENTRY_DECIDER 는 미진입.
    bars, signals = _winning_setup(["005930"])
    k = next(iter(signals))
    signals[k] = _sig(council="HOLD", single=("ORB",), minute_of_day=signals[k]["minute_of_day"])
    r_dec = run_sim_v2(bars, signals, SimV2Config(agent_mode="AGENT_ENTRY_DECIDER"))
    r_off = run_sim_v2(bars, signals, SimV2Config(agent_mode="AGENT_OFF"))
    assert r_dec.trade_count == 0
    assert r_off.trade_count == 1


def test_entry_cutoff_blocks_late_entries():
    # 신호가 14:00(=840분) 이후 → cutoff 13:00 이면 진입 0.
    bars = _day("005930", "20260120", [10000.0] * 70)  # 09:00~14:45
    b_idx = 62  # 약 14:10
    sigts = bars[b_idx].timestamp
    kst = sigts.astimezone(_KST)
    signals = {("005930", sigts.isoformat()): _sig(minute_of_day=kst.hour * 60 + kst.minute,
                                                   bucket="13:30-14:50")}
    r_open = run_sim_v2(bars, signals, SimV2Config())
    r_cut = run_sim_v2(bars, signals, SimV2Config(entry_cutoff_min=13 * 60))
    assert r_open.trade_count == 1
    assert r_cut.trade_count == 0


def test_cost_params_affect_cost_paid():
    bars, signals = _winning_setup(["005930"])
    r_hi = run_sim_v2(bars, signals, SimV2Config(slippage_bps=20.0))
    r_lo = run_sim_v2(bars, signals, SimV2Config(slippage_bps=0.0))
    assert r_hi.slippage_paid_total > r_lo.slippage_paid_total
    assert r_lo.slippage_paid_total == 0.0


def test_sim_v2_invariants_and_reject_unsafe():
    bars, signals = _winning_setup(["005930"])
    r = run_sim_v2(bars, signals, SimV2Config())
    assert r.is_live_authorization is False and r.broker_order_sent is False
    assert r.order_created is False and r.do_not_auto_apply is True
    with pytest.raises(ValueError):
        SimV2Result(
            config_label="x", total_return_pct=0, final_equity=1, profit_factor=None,
            max_drawdown_pct=None, expectancy=None, win_rate=None, payoff_ratio=None,
            trade_count=0, avg_trades_per_day=None, worst_day_pnl=None,
            worst_consecutive_losses=0, avg_hold_minutes=None, monthly_returns={},
            walk_forward_score=None, oos_positive=False, turnover=None, cost_paid_total=0,
            tax_paid_total=0, slippage_paid_total=0, trading_days=0,
            is_live_authorization=True)


def test_selection_mode_changes_fills_when_contended():
    # 6 종목 동시 BUY, 슬롯 5 → 선택 모드에 따라 어떤 5개가 담기는지 달라짐.
    syms = [f"{i:06d}" for i in range(6)]
    bars, signals = _winning_setup(syms)
    grade = {s: ("GO" if i < 2 else "EXCLUDE") for i, s in enumerate(syms)}
    r_e = run_sim_v2(bars, signals, SimV2Config(selection_mode="earliest_first"))
    r_g = run_sim_v2(bars, signals, SimV2Config(selection_mode="symbol_grade_rank", grade_map=grade))
    assert r_e.skipped_max_positions >= 1
    assert r_e.trade_count == 5 and r_g.trade_count == 5


# ─────────────────────────── verdict thresholds ───────────────────────────

def test_verdict_still_not_recommended():
    assert rb.verdict_for({"total_return_pct": -19.0, "profit_factor": 0.84,
                           "max_drawdown_pct": 29.6, "worst_consecutive_losses": 26,
                           "trade_count": 600, "oos_positive": False}) == rb.STILL_NOT_RECOMMENDED


def test_verdict_blocked_on_missing():
    assert rb.verdict_for({"total_return_pct": None, "profit_factor": None,
                           "max_drawdown_pct": None}) == rb.BLOCKED


def test_verdict_watchlist_only():
    assert rb.verdict_for({"total_return_pct": 3.0, "profit_factor": 1.08,
                           "max_drawdown_pct": 18.0, "worst_consecutive_losses": 14,
                           "trade_count": 200, "oos_positive": False}) == rb.WATCHLIST_ONLY


def test_verdict_paper_rehearsal_candidate():
    assert rb.verdict_for({"total_return_pct": 7.0, "profit_factor": 1.2,
                           "max_drawdown_pct": 12.0, "worst_consecutive_losses": 8,
                           "trade_count": 250, "oos_positive": True}) == rb.PAPER_REHEARSAL_CANDIDATE


def test_verdict_research_promising_requires_defense():
    base = {"total_return_pct": 12.0, "profit_factor": 1.35, "max_drawdown_pct": 9.0,
            "worst_consecutive_losses": 6, "trade_count": 300, "oos_positive": True}
    # 방어 미충족 → PAPER_REHEARSAL 이하.
    assert rb.verdict_for(base) == rb.PAPER_REHEARSAL_CANDIDATE
    # 방어 + agent hurt 감소 충족 → RESEARCH_PROMISING.
    assert rb.verdict_for(base, worst_month_defended=True,
                          agent_hurt_reduced=True) == rb.RESEARCH_PROMISING


def test_verdict_mdd_over_20_blocks_candidate():
    # 수익 좋아도 MDD>20 이면 STILL_NOT_RECOMMENDED.
    assert rb.verdict_for({"total_return_pct": 30.0, "profit_factor": 2.0,
                           "max_drawdown_pct": 25.0, "worst_consecutive_losses": 5,
                           "trade_count": 300, "oos_positive": True}) == rb.STILL_NOT_RECOMMENDED


# ─────────────────────────── static guards ───────────────────────────

@pytest.mark.parametrize("modfile", [
    "system/wf_6m_signal_extract.py",
    "system/wf_6m_sim_v2.py",
    "system/wf_6m_root_cause_analysis.py",
    "system/wf_6m_rebuild_experiments.py",
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


def test_scripts_read_only():
    for script in ("run_wf_6m_root_cause_analysis.py", "run_wf_6m_rebuild_experiments.py"):
        src = (Path(__file__).resolve().parents[2] / "scripts" / script).read_text(encoding="utf-8")
        for call in ("route_order(", ".place_order(", "/trading/order-cash", "OrderExecutor("):
            assert call not in src, f"{script} forbidden: {call}"


# ─────────────────────────── endpoints ───────────────────────────

def test_root_cause_endpoint(client, safe_default_flags):
    r = client.get("/api/system/wf-6m-root-cause/latest")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["is_live_authorization"] is False
    assert d["do_not_auto_apply"] is True
    assert d["no_profit_guarantee"] is True
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", r.text)


def test_rebuild_endpoint(client, safe_default_flags):
    r = client.get("/api/system/wf-6m-rebuild/latest")
    assert r.status_code == 200, r.text
    d = r.json()
    assert "final_verdict" in d
    assert d["is_live_authorization"] is False
    assert d["auto_apply_allowed"] is False
    assert d["no_profit_guarantee"] is True
    assert not re.search(r"\b\d{8}-\d{2}\b", r.text)


def test_endpoints_get_only(client, safe_default_flags):
    assert client.post("/api/system/wf-6m-root-cause/latest", json={}).status_code in (404, 405)
    assert client.post("/api/system/wf-6m-rebuild/latest", json={}).status_code in (404, 405)
