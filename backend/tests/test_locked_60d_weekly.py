"""KIS-INTRADAY-60D-WEEKLY-FIXED-REVALIDATION-01 — lock 불변 / verdict / count_window / 안전 테스트.

합성 입력 + pure-helper 단위. 실제 수집 데이터 비의존.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.backtest.strategy_council_backtest import OHLCVBar
from app.system import forward_universe_validation as fuv
from app.system import locked_60d_weekly_validation as lk

_KST = timezone(timedelta(hours=9))
_SRC = Path(__file__).resolve().parents[1] / "app"


# ─────────── locked rule immutability ───────────

def test_locked_params_structure():
    p = lk.LOCKED_PARAMS
    assert lk.LOCKED_RULE_NAME == "FORWARD_UNIVERSE_60D_WEEKLY_LOCKED_V1"
    assert p["lookback_days"] == 60
    assert p["rebalance_frequency"] == "weekly"
    assert p["universe_size_primary"] == 10
    assert p["agent_mode"] == "AGENT_RISK_VETO_ONLY"
    assert p["selector"] == "FORWARD_STABLE_UNIVERSE"


def test_base_rule_matches_locked_params():
    assert lk._LOCKED_BASE_RULE["agent_mode"] == lk.LOCKED_PARAMS["agent_mode"]
    assert lk._LOCKED_BASE_RULE["daily_loss_stop_pct"] == 1.5


# ─────────── verdict thresholds ───────────

def _h(**kw):
    base = {"forward_return_pct": 0.0, "median_pf": 1.0, "forward_mdd_pct": 10.0, "total_trades": 80}
    base.update(kw)
    return base


def test_verdict_fail():
    assert lk._verdict(_h(forward_return_pct=-1), worst_month_defended=True, slippage_ok=True,
                       decay_ok=True) == lk.LOCKED_RULE_FAIL
    assert lk._verdict(_h(median_pf=1.0), worst_month_defended=True, slippage_ok=True,
                       decay_ok=True) == lk.LOCKED_RULE_FAIL  # pf<1.05


def test_verdict_watch():
    assert lk._verdict(_h(forward_return_pct=4, median_pf=1.12, forward_mdd_pct=14),
                       worst_month_defended=False, slippage_ok=False,
                       decay_ok=False) == lk.LOCKED_RULE_WATCH


def test_verdict_paper_candidate():
    assert lk._verdict(_h(forward_return_pct=6, median_pf=1.18, forward_mdd_pct=13, total_trades=80),
                       worst_month_defended=True, slippage_ok=True,
                       decay_ok=False) == lk.LOCKED_RULE_PAPER_CANDIDATE


def test_verdict_research_promising():
    assert lk._verdict(_h(forward_return_pct=9, median_pf=1.25, forward_mdd_pct=10, total_trades=120),
                       worst_month_defended=True, slippage_ok=True,
                       decay_ok=True) == lk.LOCKED_RULE_RESEARCH_PROMISING


def test_verdict_paper_blocked_without_defense_or_slippage():
    # 수익/PF 충분해도 worst-month 미방어 또는 slippage 붕괴면 PAPER 미만(WATCH).
    assert lk._verdict(_h(forward_return_pct=6, median_pf=1.18, forward_mdd_pct=13),
                       worst_month_defended=False, slippage_ok=True,
                       decay_ok=False) == lk.LOCKED_RULE_WATCH
    assert lk._verdict(_h(forward_return_pct=6, median_pf=1.18, forward_mdd_pct=13),
                       worst_month_defended=True, slippage_ok=False,
                       decay_ok=False) == lk.LOCKED_RULE_WATCH


# ─────────── count_window holdout (no look-ahead lookback) ───────────

def _synthetic():
    """3 종목 × 12 거래일 × 8 bar/day + 매일 1개 BUY 신호."""
    bars, signals = [], {}
    base = datetime(2026, 1, 5, 9, 0, tzinfo=_KST)
    for di in range(12):
        day = base + timedelta(days=di)
        if day.weekday() >= 5:
            continue
        for s in ("000001", "000002", "000003"):
            for bi in range(8):
                ts = day.replace(hour=9, minute=0) + timedelta(minutes=5 * bi)
                px = 100.0 + bi
                bars.append(OHLCVBar(symbol=s, timestamp=ts, open=px, high=px * 1.01,
                                     low=px * 0.99, close=px, volume=10000))
            sigts = day.replace(hour=9, minute=10)
            signals[(s, sigts.isoformat())] = {
                "council_action": "BUY", "confidence": 0.8, "quality_score": 70.0,
                "stop_pct": 1.5, "target_pct": 3.0, "selected": ("ORB",), "single_buys": ("ORB",),
                "minute_of_day": 9 * 60 + 10, "time_bucket": "09:00-09:30", "fwd_eod_return": 0.01}
    return bars, signals


def test_count_window_restricts_counted_periods():
    from app.system import forward_universe_selector as fus
    bars, signals = _synthetic()
    days = fus.trading_days(bars)
    # count_window = 빈 집합 → 계상 기간 0.
    r_empty = fuv.run_universe_backtest(
        bars, signals, selector="FORWARD_SCORE_TOP10", lookback_days=3, rebalance_freq="weekly",
        universe_size=3, rule={"agent_mode": "AGENT_OFF", "selection_mode": "composite_rank"},
        in_sample_top10=None, label="empty", count_window=set())
    assert r_empty["total_trades"] == 0
    assert r_empty["forward_return_pct"] == 0.0
    # count_window = 마지막 3일 → 일부만 계상 (전체보다 작거나 같음).
    r_full = fuv.run_universe_backtest(
        bars, signals, selector="FORWARD_SCORE_TOP10", lookback_days=3, rebalance_freq="weekly",
        universe_size=3, rule={"agent_mode": "AGENT_OFF", "selection_mode": "composite_rank"},
        in_sample_top10=None, label="full")
    r_last = fuv.run_universe_backtest(
        bars, signals, selector="FORWARD_SCORE_TOP10", lookback_days=3, rebalance_freq="weekly",
        universe_size=3, rule={"agent_mode": "AGENT_OFF", "selection_mode": "composite_rank"},
        in_sample_top10=None, label="last", count_window=set(days[-3:]))
    assert r_last["periods"] <= r_full["periods"]


# ─────────── dataclass invariants ───────────

def _report(**kw):
    base = dict(
        generated_at="t", data_source="x", rule_locked_before_validation=True,
        locked_rule_name=lk.LOCKED_RULE_NAME, locked_at="t", locked_parameters={}, score_formula={},
        no_look_ahead=True, additional_data_collected=False, original_6m_replay={}, last20_holdout={},
        last40_holdout={}, worst_month_holdout={}, symbol_split={}, slippage_stress={},
        compare_40d_monthly={}, risk_veto_recheck={}, defense_recheck={}, secondary_size_compare={},
        repeated_selected=(), repeated_excluded=(), missed_opportunity=(), train_test_decay={},
        final_verdict=lk.LOCKED_RULE_WEAK, paper_rehearsal_recommendation="x",
        exe_rebuild_recommendation="x", next_steps=(), conclusions=())
    base.update(kw)
    return lk.Locked60dReport(**base)


def test_report_rejects_unsafe():
    _report()
    for bad in ({"is_live_authorization": True}, {"live_trading_recommendation": True},
                {"exe_build_executed": True}, {"auto_apply_allowed": True},
                {"rule_locked_before_validation": False}, {"no_look_ahead": False},
                {"final_verdict": "BOGUS"}):
        with pytest.raises(ValueError):
            _report(**bad)


# ─────────── static guards ───────────

def test_module_no_order_or_exe():
    src = (_SRC / "system/locked_60d_weekly_validation.py").read_text(encoding="utf-8")
    for ln in src.splitlines():
        if re.match(r"^\s*(from|import)\s", ln):
            for mod in ("app.brokers", "app.execution", "order_router",
                        "anthropic", "openai", "httpx", "requests", "app.ai.client"):
                assert mod not in ln, f"forbidden import: {ln.strip()}"
    for bad in ("route_order(", ".place_order(", "OrderExecutor(", ".cancel_order(",
                "tauri build", "cargo build", "cargo tauri"):
        assert bad not in src


def test_script_no_order_or_exe():
    src = (Path(__file__).resolve().parents[2] / "scripts"
           / "run_locked_60d_weekly_validation.py").read_text(encoding="utf-8")
    for bad in ("route_order(", ".place_order(", "OrderExecutor(", "tauri build", "cargo build"):
        assert bad not in src


# ─────────── endpoint ───────────

def test_locked_endpoint(client, safe_default_flags):
    r = client.get("/api/system/locked-60d-weekly/latest")
    assert r.status_code == 200, r.text
    d = r.json()
    assert "final_verdict" in d
    assert "exe_rebuild_recommendation" in d
    assert d["is_live_authorization"] is False
    assert d["live_trading_recommendation"] is False
    assert d["exe_build_executed"] is False
    assert d["no_profit_guarantee"] is True
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", r.text)
    assert not re.search(r"\b\d{8}-\d{2}\b", r.text)


def test_locked_endpoint_get_only(client, safe_default_flags):
    assert client.post("/api/system/locked-60d-weekly/latest", json={}).status_code in (404, 405)
