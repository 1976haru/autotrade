"""평균회귀 exit 구조 연구 테스트 (CHECKLIST-05, 백테스트 전용, 실주문/자동적용 0).

요청서의 test_mean_reversion_exit_{candidates,oos,report,api} + no_runtime_exit_registration
을 본 파일에 통합 (mean_reversion_exit 키워드로 -k 매칭).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.system import mean_reversion_exit as me

client = TestClient(app)
_UTC = timezone.utc


def _bars1(entry_ts, *, lows, highs):
    """1분봉 생성 — lows/highs 시퀀스로 intrabar 경로 구성."""
    out = []
    for k, (lo, hi) in enumerate(zip(lows, highs)):
        out.append({"ts": entry_ts + timedelta(minutes=k), "high": hi, "low": lo,
                    "close": (hi + lo) / 2, "open": (hi + lo) / 2})
    return out


# ─────────── exit candidates (simulate_exit) ───────────


def test_exit_plans_predefined_small_set():
    """exit 후보가 사전 정의된 소수 (grid search 아님)."""
    assert "EXISTING_TREND" in me._EXIT_PLANS
    assert "SMALL_T30_S60" in me._EXIT_PLANS
    assert "VWAP_REVERSION" in me._EXIT_PLANS
    assert len(me._EXIT_PLANS) <= 12   # 과도한 grid 아님


def test_simulate_exit_target_hit():
    et = datetime(2026, 5, 20, 0, 30, tzinfo=_UTC)
    # +50bps target 도달.
    bars = _bars1(et, lows=[99.8, 99.9], highs=[100.2, 100.6])
    r = me.simulate_exit(100.0, et, bars, target_price=100.5, stop_price=99.2, max_hold_min=30)
    assert r["exit_reason"] == "TARGET"
    assert r["gross"] > 0


def test_simulate_exit_stop_hit():
    et = datetime(2026, 5, 20, 0, 30, tzinfo=_UTC)
    bars = _bars1(et, lows=[99.0, 98.5], highs=[100.1, 100.2])
    r = me.simulate_exit(100.0, et, bars, target_price=101.5, stop_price=99.4, max_hold_min=30)
    assert r["exit_reason"] == "STOP"
    assert r["net"] < 0


def test_simulate_exit_time_exit():
    et = datetime(2026, 5, 20, 0, 30, tzinfo=_UTC)
    bars = _bars1(et, lows=[99.9, 99.95], highs=[100.05, 100.1])  # 목표/손절 미도달
    r = me.simulate_exit(100.0, et, bars, target_price=105, stop_price=95, max_hold_min=2)
    assert r["exit_reason"] == "TIME"


def test_simulate_exit_breakeven_moves_stop():
    et = datetime(2026, 5, 20, 0, 30, tzinfo=_UTC)
    # +30bps 도달(breakeven) 후 하락 → breakeven stop.
    bars = _bars1(et, lows=[100.0, 99.5], highs=[100.4, 100.1])
    r = me.simulate_exit(100.0, et, bars, target_price=100.5, stop_price=99.2,
                         max_hold_min=30, breakeven_at_bps=30)
    assert r["exit_reason"] == "BREAKEVEN"


def test_simulate_exit_partial():
    et = datetime(2026, 5, 20, 0, 30, tzinfo=_UTC)
    bars = _bars1(et, lows=[100.0, 99.9], highs=[100.4, 100.6])  # +30 절반, +50 나머지
    r = me.simulate_exit(100.0, et, bars, target_price=100.5, stop_price=99.2,
                         max_hold_min=30, partial_at_bps=30)
    assert r["exit_reason"] == "PARTIAL"


def test_simulate_exit_cost_floor_small_target_net_negative():
    """왕복 비용(31bps) > +30bps 목표 → target 도달해도 net 손실 (핵심 발견)."""
    et = datetime(2026, 5, 20, 0, 30, tzinfo=_UTC)
    bars = _bars1(et, lows=[99.9], highs=[100.35])
    r = me.simulate_exit(100.0, et, bars, target_price=100.3, stop_price=99.4, max_hold_min=30)
    assert r["exit_reason"] == "TARGET"
    assert r["net"] < 0           # +30bps gross < 31bps cost


def test_plan_prices_uses_vwap_and_range():
    ev = {"entry": 100.0, "vwap": 100.5, "range_hi": 102.0, "range_lo": 99.0}
    vp = me._plan_prices("VWAP_REVERSION", ev)
    assert vp["target_price"] == 100.5    # vwap target
    rp = me._plan_prices("RANGE_MID", ev)
    assert rp["target_price"] == 100.5    # (102+99)/2 = 100.5, > entry → mid target
    assert rp["stop_price"] == 99.0       # range low


# ─────────── combo verdict / OOS ───────────


def test_combo_verdict_reject_when_pf_below_1():
    c = {"trade_count": 100, "net_pf": 0.4, "oos_pf": 0.3,
         "slippage_stress": {"10.0bps": 0.3}, "symbol_split": {"even_pf": 0.4, "odd_pf": 0.4},
         "mdd_improved_vs_existing": True, "expectancy_bps": -5}
    assert me._combo_verdict(c) == "REJECT"


def test_combo_verdict_oos_validated():
    c = {"trade_count": 100, "net_pf": 1.4, "oos_pf": 1.2,
         "slippage_stress": {"10.0bps": 1.1}, "symbol_split": {"even_pf": 1.1, "odd_pf": 1.2},
         "mdd_improved_vs_existing": True, "expectancy_bps": 10}
    assert me._combo_verdict(c) == "EXIT_OOS_VALIDATED"


def test_combo_verdict_cost_fragile():
    c = {"trade_count": 100, "net_pf": 1.1, "oos_pf": 1.0,
         "slippage_stress": {"10.0bps": 0.8}, "symbol_split": {"even_pf": 1.1, "odd_pf": 1.0},
         "mdd_improved_vs_existing": True, "expectancy_bps": 5}
    assert me._combo_verdict(c) == "EXIT_COST_FRAGILE"


def test_overall_verdict_ladder():
    v, _c = me._overall_verdict([("A", "X")], [], [], [("A", "X")], [])
    assert v == "EXIT_OOS_VALIDATED"
    v2, _c2 = me._overall_verdict([], [], [], [], [])
    assert v2 == "EXIT_REDESIGN_REJECTED"
    v3, _c3 = me._overall_verdict([], [], [("A", "Y")], [("A", "Y")], [])
    assert v3 == "EXIT_COST_FRAGILE"


# ─────────── insufficient data ───────────


def test_insufficient_trades(tmp_path):
    r = me.run_mean_reversion_exit(one_min_dir=tmp_path / "n", five_min_dir=tmp_path / "n5",
                                   symbols=["005930"])
    assert r["available"] is False
    assert r["verdict"] == "BACKTEST_INFRA_INCOMPLETE"
    assert r["auto_apply_allowed"] is False
    assert r["is_live_authorization"] is False


# ─────────── API (report) ───────────


def test_api_mean_reversion_exit_latest():
    r = client.get("/api/system/mean-reversion-exit/latest")
    assert r.status_code == 200
    d = r.json()
    assert d["auto_apply_allowed"] is False
    assert d["applied_to_runtime"] is False
    assert d["is_live_authorization"] is False
    assert d["no_profit_guarantee"] is True
    assert "verdict" in d
    import re
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", r.text)
    assert not re.search(r"\b\d{8}-\d{2}\b", r.text)


def test_api_get_only():
    assert client.post("/api/system/mean-reversion-exit/latest").status_code in (404, 405)


# ─────────── no runtime apply / exit registration (static grep) ───────────


def test_no_runtime_exit_registration():
    txt = (Path(__file__).resolve().parents[1]
           / "app/system/mean_reversion_exit.py").read_text(encoding="utf-8")
    assert "STRATEGY_REGISTRY[" not in txt
    assert "register_strategy(" not in txt
    for line in txt.splitlines():
        code = line.split("#", 1)[0]
        if code.lstrip().startswith(("import ", "from ")):
            assert "LiveStrategyEngine" not in code
            assert "app.strategies" not in code
        for bad in (".place_order(", "route_order(", "OrderExecutor(", "brokers.kis import",
                    "brokers.mock", "cargo build", "tauri build", "ENABLE_LIVE_TRADING =",
                    "KIS_IS_PAPER =", "auto_apply_allowed=True", "applied_to_runtime=True"):
            assert bad not in code, f"banned {bad}"
