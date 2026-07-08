"""최종 다전략 + Council 백테스트 테스트 (백테스트 전용, 실주문/자동적용 0)."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.backtest.cost_model import (
    DEFAULT_COST,
    BacktestCostModel,
    apply_round_trip_cost,
    cost_drag_bps,
)
from app.system import final_multi_strategy_backtest as fb

client = TestClient(app)


# ─────────── cost model ───────────


def test_cost_model_defaults():
    assert DEFAULT_COST.commission_bps == 1.5
    # 2026 개정: 거래세 0.18% → 0.20% (0.05% 거래세 + 0.15% 농특세, 코스피/코스닥 동일)
    assert DEFAULT_COST.tax_bps == 20.0
    assert DEFAULT_COST.slippage_bps == 5.0
    # 왕복 = 1.5*2 + 20 + 5*2 = 33 bps
    assert cost_drag_bps() == 33.0


def test_apply_round_trip_cost_buy():
    c = apply_round_trip_cost("BUY", 100.0, 102.0)
    assert c["gross"] == 2.0
    assert c["tax"] > 0 and c["slippage"] > 0 and c["commission"] > 0
    assert c["net"] < c["gross"]


def test_cost_model_with_slippage_stress():
    base = apply_round_trip_cost("BUY", 100, 102, DEFAULT_COST)
    stress = apply_round_trip_cost("BUY", 100, 102, DEFAULT_COST.with_slippage(15))
    assert stress["slippage"] > base["slippage"]
    assert stress["net"] < base["net"]


# ─────────── metrics / grade / verdict ───────────


def _t(net, entry=70000.0, day="2026-05-20", win=None, hold=10.0):
    return {"net": net, "gross": net + 50, "net_5m": net, "slip5": 30.0,
            "entry": entry, "day": day, "symbol": "005930", "hold": hold,
            "vol_exp": 1.5, "low_liq": False, "net_edge_bps": 100.0}


def test_metrics_basic():
    m = fb._metrics([_t(100), _t(-50), _t(80)], "net")
    assert m["trade_count"] == 3
    assert m["win_rate"] == round(2 / 3, 4)
    assert m["profit_factor"] == round(180 / 50, 3)
    assert m["worst_consecutive_losses"] >= 1


def test_grade_thresholds():
    assert fb._grade({"profit_factor": 1.3, "mdd_pct": 10}) == "KEEP"
    assert fb._grade({"profit_factor": 1.1, "mdd_pct": 10}) == "WATCH"
    assert fb._grade({"profit_factor": 1.3, "mdd_pct": 30}) == "TUNE"
    assert fb._grade({"profit_factor": 0.8, "mdd_pct": 10}) == "WEAK_OR_EXCLUDE"
    assert fb._grade({"profit_factor": None, "mdd_pct": 5}) == "WEAK_OR_EXCLUDE"


def _ps(pf):  # per-strategy stub
    return {"intrabar_1m": {"profit_factor": pf},
            "intrabar_1m_risk_filter": {"profit_factor": pf}}


def test_verdict_strategy_edge_not_found():
    per = {n: _ps(0.2) for n in ("ORB", "MOMENTUM", "GAP", "VWAP")}
    v, _c = fb._verdict(per, {"profit_factor": 0.6}, {"profit_factor": 0.6}, 0.2, 0.2)
    assert v == "STRATEGY_EDGE_NOT_FOUND"


def test_verdict_single_strategy_candidate():
    per = {"ORB": _ps(1.4), "MOMENTUM": _ps(0.9), "GAP": _ps(0.8), "VWAP": _ps(0.7)}
    v, _c = fb._verdict(per, {"profit_factor": 1.0}, {"profit_factor": 1.0}, 1.4, 0.95)
    assert v in ("SINGLE_STRATEGY_CANDIDATE", "RISK_FILTER_CANDIDATE")


def test_verdict_council_candidate():
    per = {"ORB": _ps(1.3), "MOMENTUM": _ps(1.0), "GAP": _ps(0.9), "VWAP": _ps(0.8)}
    v, _c = fb._verdict(per, {"profit_factor": 1.4}, {"profit_factor": 1.5}, 1.3, 1.0)
    assert v == "COUNCIL_CANDIDATE"


def test_insufficient_trades(tmp_path):
    r = fb.run_final_multi_strategy_backtest(one_min_dir=tmp_path / "n",
                                             five_min_dir=tmp_path / "n5",
                                             symbols=["005930"])
    assert r["available"] is False
    assert r["verdict"] == "BACKTEST_INFRA_INCOMPLETE"
    assert r["auto_apply_allowed"] is False
    assert r["is_live_authorization"] is False


# ─────────── API ───────────


def test_api_final_backtest_latest():
    r = client.get("/api/system/final-backtest-report/latest")
    assert r.status_code == 200
    d = r.json()
    assert d["auto_apply_allowed"] is False
    assert d["applied_to_runtime"] is False
    assert d["is_live_authorization"] is False
    assert d["no_profit_guarantee"] is True
    assert "verdict" in d
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", r.text)
    assert not re.search(r"\b\d{8}-\d{2}\b", r.text)


def test_api_get_only():
    assert client.post("/api/system/final-backtest-report/latest").status_code in (404, 405)


# ─────────── no runtime apply / safety ───────────


def test_no_runtime_order_imports():
    for mod in ("app/system/final_multi_strategy_backtest.py", "app/backtest/cost_model.py"):
        txt = (Path(__file__).resolve().parents[1] / mod).read_text(encoding="utf-8")
        for bad in (".place_order(", "route_order(", "OrderExecutor(", "brokers.kis import",
                    "cargo build", "tauri build", "auto_apply_allowed=True",
                    "applied_to_runtime=True"):
            for line in txt.splitlines():
                assert bad not in line.split("#", 1)[0], f"{mod}: banned {bad}"
