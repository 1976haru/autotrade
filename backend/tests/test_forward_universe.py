"""KIS-INTRADAY-FORWARD-UNIVERSE-REBUILD-01 — selector / verdict / no-look-ahead / 안전 테스트.

합성 입력 + pure-helper 단위. 실제 수집 데이터 비의존.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.backtest.strategy_council_backtest import OHLCVBar
from app.system import forward_universe_selector as fus
from app.system import forward_universe_validation as fuv

_KST = timezone(timedelta(hours=9))
_SRC = Path(__file__).resolve().parents[1] / "app"


# ─────────── rebalance dates ───────────

def test_rebalance_dates_monthly():
    days = [date(2026, 1, 5), date(2026, 1, 20), date(2026, 2, 3), date(2026, 3, 2)]
    out = fus.rebalance_dates(days, "monthly")
    assert out == [date(2026, 1, 5), date(2026, 2, 3), date(2026, 3, 2)]


def test_rebalance_dates_weekly():
    days = [date(2026, 1, d) for d in range(1, 13)]
    out = fus.rebalance_dates(days, "weekly")
    assert out[0] == days[0] and out[1] == days[5]  # 5거래일 간격.


# ─────────── select_universe ───────────

def _scores(n=20):
    return {f"{i:06d}": {"score": float(n - i), "penalties": [], "trades": 10,
                         "cost_adj_expectancy": 0.01, "win_rate": 0.55} for i in range(n)}


def test_select_top5_and_top10():
    sc = _scores(20)
    u5, e5, m5 = fus.select_universe("FORWARD_SCORE_TOP5", sc, size=5)
    u10, _, _ = fus.select_universe("FORWARD_SCORE_TOP10", sc, size=10)
    assert len(u5) <= 5 and len(u10) <= 10
    assert m5["look_ahead"] is False


def test_static_baseline_all_is_none():
    u, e, m = fus.select_universe("STATIC_BASELINE_ALL", _scores())
    assert u is None and m["look_ahead"] is False


def test_static_in_sample_top10_flagged_lookahead():
    u, e, m = fus.select_universe("STATIC_IN_SAMPLE_TOP10", _scores(),
                                  in_sample_top10=frozenset({"000000", "000001"}))
    assert m["look_ahead"] is True
    assert u == frozenset({"000000", "000001"})


def test_exclude_bottom_30():
    sc = _scores(20)
    u, e, m = fus.select_universe("FORWARD_EXCLUDE_BOTTOM_30", sc)
    assert len(u) == 14 and len(e) == 6   # 하위 30% 제외.


def test_conservative_drops_penalised():
    sc = _scores(20)
    sc["000000"]["penalties"] = ["low_trades"]
    sc["000000"]["cost_adj_expectancy"] = -0.1
    u, e, m = fus.select_universe("FORWARD_CONSERVATIVE_UNIVERSE", sc, size=10)
    assert "000000" not in u


# ─────────── score computation (no future ref by construction) ───────────

def test_compute_scores_low_trades_penalty():
    # 합성 lookback: 거래 거의 없음 → low_trades penalty.
    bars, signals = [], {}
    for s in ("000001", "000002"):
        for i in range(30):
            ts = datetime(2026, 1, 5, 9, 0, tzinfo=_KST) + timedelta(minutes=5 * i)
            bars.append(OHLCVBar(symbol=s, timestamp=ts, open=100, high=100, low=100,
                                 close=100, volume=1000))
    sc = fus.compute_symbol_scores(bars, signals)
    assert set(sc) == {"000001", "000002"}
    for v in sc.values():
        assert "score" in v and "penalties" in v
        assert "low_trades" in v["penalties"]   # 신호 없음 → 거래 0.


# ─────────── chain / verdict ───────────

def test_chain_and_verdict():
    c = fuv._chain([5.0, -2.0, 3.0])
    assert c["periods"] == 3 and c["positive_periods"] == 2
    assert fuv._verdict({"forward_return_pct": -1.0, "forward_mdd_pct": 10, "total_trades": 200,
                         "positive_ratio": 0.5}) == fuv.UNIVERSE_FAIL
    assert fuv._verdict({"forward_return_pct": 6.0, "forward_mdd_pct": 12, "total_trades": 150,
                         "positive_ratio": 0.6}) == fuv.UNIVERSE_PAPER_CANDIDATE
    assert fuv._verdict({"forward_return_pct": 3.0, "forward_mdd_pct": 18, "total_trades": 80,
                         "positive_ratio": 0.5}) == fuv.UNIVERSE_WATCH
    assert fuv._verdict({"forward_return_pct": 0.5, "forward_mdd_pct": 18, "total_trades": 80,
                         "positive_ratio": 0.4}) == fuv.UNIVERSE_WEAK


# ─────────── dataclass invariants ───────────

def _report(**kw):
    base = dict(
        generated_at="t", data_source="x", rule_locked_before_test=True, score_formula={},
        selector_results=(), lookback_rebalance_grid=(), agent_combo=(), defense_combo=(),
        static_vs_forward={}, repeated_selected=(), repeated_excluded=(), missed_opportunity=(),
        best_selector={}, top_by_return=(), most_stable=(), overfit_warning={},
        final_universe_verdict=fuv.UNIVERSE_FAIL, paper_rehearsal_recommendation="x",
        exe_rebuild_recommendation="x", next_steps=(), conclusions=())
    base.update(kw)
    return fuv.ForwardUniverseReport(**base)


def test_report_rejects_unsafe():
    _report()
    for bad in ({"is_live_authorization": True}, {"live_trading_recommendation": True},
                {"exe_build_executed": True}, {"auto_apply_allowed": True},
                {"final_universe_verdict": "BOGUS"}):
        with pytest.raises(ValueError):
            _report(**bad)


# ─────────── static guards ───────────

@pytest.mark.parametrize("modfile", [
    "system/forward_universe_selector.py", "system/forward_universe_validation.py",
])
def test_no_order_or_exe(modfile):
    src = (_SRC / modfile).read_text(encoding="utf-8")
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
           / "run_forward_universe_validation.py").read_text(encoding="utf-8")
    for bad in ("route_order(", ".place_order(", "OrderExecutor(", "tauri build", "cargo build"):
        assert bad not in src


# ─────────── endpoint ───────────

def test_forward_universe_endpoint(client, safe_default_flags):
    r = client.get("/api/system/forward-universe/latest")
    assert r.status_code == 200, r.text
    d = r.json()
    assert "final_universe_verdict" in d
    assert "exe_rebuild_recommendation" in d
    assert d["is_live_authorization"] is False
    assert d["live_trading_recommendation"] is False
    assert d["exe_build_executed"] is False
    assert d["no_profit_guarantee"] is True
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", r.text)
    assert not re.search(r"\b\d{8}-\d{2}\b", r.text)


def test_forward_universe_endpoint_get_only(client, safe_default_flags):
    assert client.post("/api/system/forward-universe/latest", json={}).status_code in (404, 405)
