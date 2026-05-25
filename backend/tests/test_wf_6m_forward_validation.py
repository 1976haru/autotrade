"""KIS-INTRADAY-FORWARD-VALIDATION-01 — forward 검증 helper / verdict / 안전 테스트.

합성 입력 + pure-helper 단위. 실제 수집 데이터 비의존.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.backtest.strategy_council_backtest import OHLCVBar
from app.system import wf_6m_forward_validation as fv

_KST = timezone(timedelta(hours=9))
_SRC = Path(__file__).resolve().parents[1] / "app"


def _bar(sym, y, m, d, hh, mm, px):
    return OHLCVBar(symbol=sym, timestamp=datetime(y, m, d, hh, mm, tzinfo=_KST),
                    open=px, high=px, low=px, close=px, volume=1000)


# ─────────── _chain ───────────

def test_chain_compounds_returns():
    c = fv._chain([10.0, -5.0, 10.0])
    # 10M*1.1*0.95*1.1 = 11,495,000 → +14.95%
    assert abs(c["forward_return_pct"] - 14.95) < 0.1
    assert c["windows"] == 3
    assert c["positive_windows"] == 2
    assert c["worst_window_pct"] == -5.0
    assert c["forward_mdd_pct"] >= 0


def test_chain_empty():
    c = fv._chain([])
    assert c["forward_return_pct"] == 0.0
    assert c["positive_ratio"] is None


# ─────────── slicing ───────────

def test_slice_dates_and_symbols():
    bars = [_bar("A", 2026, 1, 5, 9, 30, 100), _bar("A", 2026, 2, 5, 9, 30, 100),
            _bar("B", 2026, 1, 5, 9, 30, 100)]
    signals = {(b.symbol, b.timestamp.isoformat()): {"x": 1} for b in bars}
    jan = {datetime(2026, 1, 5, tzinfo=_KST).date()}
    sb, ss = fv._slice_dates(bars, signals, jan)
    assert len(sb) == 2 and len(ss) == 2     # Jan bars only (A, B).
    sb2, ss2 = fv._slice_symbols(bars, signals, {"A"})
    assert len(sb2) == 2 and all(b.symbol == "A" for b in sb2)


def test_universe_for():
    ctx = {"grade_map": {"A": "GO", "B": "TUNE", "C": "EXCLUDE"},
           "ranked": ["A", "B", "D", "E"]}
    assert fv._universe_for("ALL", ctx) is None
    assert fv._universe_for("EXCLUDE_REMOVED", ctx) == frozenset({"A", "B"})
    assert fv._universe_for("GO_TUNE_TOP10", ctx) == frozenset({"A", "B", "D", "E"})


# ─────────── verdict ───────────

def _monthly(cname, *, fr, pf, mdd, trades, posr):
    return {cname: {"forward_return_pct": fr, "forward_mdd_pct": mdd, "median_pf": pf,
                    "total_trades": trades, "positive_ratio": posr}}


def test_verdict_fail_on_negative():
    m = {c: {"forward_return_pct": -3.0, "forward_mdd_pct": 20.0, "median_pf": 0.9,
             "total_trades": 200, "positive_ratio": 0.3} for c in fv.RULE_CANDIDATES}
    v, best, cs = fv._forward_verdict(m, {"insufficient": True}, {"insufficient": True})
    assert v == fv.FORWARD_FAIL


def test_verdict_paper_rehearsal_confirmed():
    m = {c: {"forward_return_pct": 1.0, "forward_mdd_pct": 25.0, "median_pf": 1.0,
             "total_trades": 50, "positive_ratio": 0.3} for c in fv.RULE_CANDIDATES}
    # 한 후보만 강하게.
    m["A_top10_riskveto_daily"] = {"forward_return_pct": 8.0, "forward_mdd_pct": 12.0,
                                   "median_pf": 1.25, "total_trades": 150, "positive_ratio": 0.7}
    anchored = {"A_top10_riskveto_daily": {"forward_return_pct": 4.0}}
    holdout = {"A_top10_riskveto_daily": {"defended": True}}
    v, best, cs = fv._forward_verdict(m, anchored, holdout)
    assert v == fv.PAPER_REHEARSAL_CONFIRMED
    assert best["best_candidate"] == "A_top10_riskveto_daily"


def test_verdict_watch_mid():
    m = {c: {"forward_return_pct": 3.0, "forward_mdd_pct": 18.0, "median_pf": 1.12,
             "total_trades": 120, "positive_ratio": 0.5} for c in fv.RULE_CANDIDATES}
    v, best, cs = fv._forward_verdict(m, {"insufficient": True}, {"insufficient": True})
    assert v == fv.FORWARD_WATCH


# ─────────── dataclass invariants ───────────

def _report(**kw):
    base = dict(
        generated_at="t", data_source="x", rule_locked_before_validation=True,
        rule_candidates={}, monthly_forward={}, anchored_forward={}, symbol_split={},
        worst_month_holdout={}, agent_forward={}, decay_analysis={}, overfit_warning={},
        final_forward_verdict=fv.FORWARD_FAIL, paper_rehearsal_recommendation="x",
        exe_rebuild_recommendation="x", next_steps=(), conclusions=())
    base.update(kw)
    return fv.ForwardReport(**base)


def test_report_rejects_unsafe():
    _report()  # ok
    with pytest.raises(ValueError):
        _report(is_live_authorization=True)
    with pytest.raises(ValueError):
        _report(live_trading_recommendation=True)
    with pytest.raises(ValueError):
        _report(exe_build_executed=True)
    with pytest.raises(ValueError):
        _report(final_forward_verdict="BOGUS")


def test_report_safe_invariants_default():
    r = _report()
    assert r.is_live_authorization is False
    assert r.live_trading_recommendation is False
    assert r.exe_build_executed is False
    assert r.do_not_auto_apply is True
    assert r.no_profit_guarantee is True


# ─────────── static guards ───────────

def test_module_no_order_calls_or_exe():
    src = (_SRC / "system/wf_6m_forward_validation.py").read_text(encoding="utf-8")
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
           / "run_wf_6m_forward_validation.py").read_text(encoding="utf-8")
    for bad in ("route_order(", ".place_order(", "OrderExecutor(", "tauri build", "cargo build"):
        assert bad not in src


# ─────────── endpoint ───────────

def test_forward_endpoint(client, safe_default_flags):
    r = client.get("/api/system/forward-validation/latest")
    assert r.status_code == 200, r.text
    d = r.json()
    assert "final_forward_verdict" in d
    assert "exe_rebuild_recommendation" in d
    assert d["is_live_authorization"] is False
    assert d["live_trading_recommendation"] is False
    assert d["exe_build_executed"] is False
    assert d["no_profit_guarantee"] is True
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", r.text)
    assert not re.search(r"\b\d{8}-\d{2}\b", r.text)


def test_forward_endpoint_get_only(client, safe_default_flags):
    assert client.post("/api/system/forward-validation/latest", json={}).status_code in (404, 405)
