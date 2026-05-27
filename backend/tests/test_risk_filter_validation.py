"""RISK_FILTER_ONLY 검증 테스트 (백테스트 전용, 실주문 0건, 자동 적용 0)."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.system import risk_filter_validation as rf

client = TestClient(app)


def test_kept_by_filter_rule():
    assert rf._kept_by_filter({"net_edge_bps": 10, "low_liq": False}) is True
    assert rf._kept_by_filter({"net_edge_bps": -1, "low_liq": False}) is False  # net_edge<=0
    assert rf._kept_by_filter({"net_edge_bps": 10, "low_liq": True}) is False   # 저유동성


def test_fixed_rule_no_lookahead_no_reorder():
    assert rf.RISK_FILTER_RULE["uses_lookahead_feature"] is False
    assert "earliest" in rf.RISK_FILTER_RULE["reorder"].lower()


def _m(pf, mdd, exp, n=49, syms=9):
    return {"profit_factor": pf, "mdd_pct": mdd, "expectancy": exp, "trade_count": n,
            "selected_count": n, "selected_symbols": [f"s{i}" for i in range(syms)]}


def _stress(b5, f5, b7, f7, b10, f10):
    return {"5.0bps": {"baseline": b5, "filter": f5},
            "7.0bps": {"baseline": b7, "filter": f7},
            "10.0bps": {"baseline": b10, "filter": f10},
            "15.0bps": {"baseline": 0.9, "filter": 1.0}}


def _rolling(all_ge=True):
    return [{"window": "w1", "baseline_pf": 0.9, "filter_pf": 1.9, "filter_ge_baseline": all_ge},
            {"window": "w2", "baseline_pf": 0.5, "filter_pf": 0.9, "filter_ge_baseline": all_ge},
            {"window": "w3", "baseline_pf": 1.1, "filter_pf": 1.3, "filter_ge_baseline": all_ge}]


def _fo(concentrated=True):
    return {"loss_concentrated_in_removed": concentrated}


def test_verdict_ready_for_paper_rehearsal_candidate():
    base = _m(1.279, 20.7, 14.0, n=74, syms=10)
    filt = _m(1.372, 16.8, 20.2)
    v, _r = rf._verdict(base, filt, _rolling(True),
                        _stress(1.279, 1.372, 1.192, 1.286, 1.073, 1.169), _fo())
    assert v == "RISK_FILTER_READY_FOR_PAPER_REHEARSAL_CANDIDATE"


def test_verdict_oos_validated_when_rolling_inconsistent():
    base = _m(1.279, 20.7, 14.0, n=74, syms=10)
    filt = _m(1.372, 16.8, 20.2)
    v, _r = rf._verdict(base, filt, _rolling(False),  # rolling 일부 실패
                        _stress(1.279, 1.372, 1.192, 1.286, 1.073, 1.169), _fo())
    assert v == "RISK_FILTER_OOS_VALIDATED"


def test_verdict_candidate_when_stress_fails():
    base = _m(1.279, 20.7, 14.0, n=74, syms=10)
    filt = _m(1.372, 16.8, 20.2)
    # 10bps 에서 filter < baseline → stress 실패 → CANDIDATE
    v, _r = rf._verdict(base, filt, _rolling(True),
                        _stress(1.279, 1.372, 1.192, 1.286, 1.20, 1.05), _fo())
    assert v == "RISK_FILTER_CANDIDATE"


def test_verdict_rejected_when_no_improvement():
    base = _m(1.5, 10.0, 30.0, n=74, syms=10)
    filt = _m(1.2, 14.0, 10.0)   # PF↓ MDD↑
    v, _r = rf._verdict(base, filt, _rolling(False),
                        _stress(1.5, 1.2, 1.4, 1.1, 1.3, 1.0), _fo())
    assert v == "RISK_FILTER_REJECTED"


def test_insufficient_trades(tmp_path):
    r = rf.validate_risk_filter(one_min_dir=tmp_path / "n", five_min_dir=tmp_path / "n5",
                                symbols=["005930"])
    assert r["available"] is False
    assert r["auto_apply_allowed"] is False
    assert r["applied_to_runtime"] is False
    assert r["is_live_authorization"] is False


def test_api_latest():
    r = client.get("/api/system/risk-filter-validation/latest")
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
    assert client.post("/api/system/risk-filter-validation/latest").status_code in (404, 405)


def test_module_no_order_imports():
    txt = (Path(__file__).resolve().parents[1] / "app/system/risk_filter_validation.py").read_text(encoding="utf-8")
    for bad in (".place_order(", "route_order(", "OrderExecutor(", "brokers.kis import",
                "cargo build", "tauri build"):
        for line in txt.splitlines():
            assert bad not in line.split("#", 1)[0], f"banned: {bad}"
