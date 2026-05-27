"""Ranking 재설계 비교 테스트 (백테스트 전용, 실주문 0건)."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.system import ranking_redesign as rr

client = TestClient(app)


def _trade(symbol="005930", ts="2026-05-20T09:30:00+09:00", *, ve=1.5, regime="UPTREND",
           low_liq=False, net_edge=20.0, pnl1=1.0):
    return {"day": ts[:10], "symbol": symbol, "ts": ts, "regime": regime, "group": "LARGE_CAP",
            "entry": 70000.0, "vol_exp": ve, "low_liq": low_liq, "pnl1": pnl1,
            "win": 1 if pnl1 > 0 else 0, "confidence": 0.7, "quality": 70.0,
            "edge_bps": net_edge + rr._COST_BPS, "net_edge_bps": net_edge, "liquidity": 70.0}


# ─────────── selectors ───────────


def test_selectors_respect_slot_limit():
    day = [_trade(symbol=f"00000{i}", ts=f"2026-05-20T09:3{i}:00+09:00") for i in range(8)]
    for name, sel in rr.RANKING_CANDIDATES.items():
        out = sel(day, 5)
        assert len(out) <= 5, f"{name} exceeded slots"


def test_cost_edge_excludes_negative_and_low_liq():
    day = [_trade(symbol="A", net_edge=30), _trade(symbol="B", net_edge=-5),
           _trade(symbol="C", net_edge=40, low_liq=True)]
    out = rr._cost_edge_first(day, 5)
    syms = [t["symbol"] for t in out]
    assert syms == ["A"]   # B(net_edge<0), C(low_liq) 제외


def test_no_ranking_risk_veto_excludes_low_liq_only():
    day = [_trade(symbol="A", low_liq=False, net_edge=-5),  # 저edge 라도 유지(veto는 유동성만)
           _trade(symbol="B", low_liq=True)]
    out = rr._no_ranking_risk_veto_only(day, 5)
    assert [t["symbol"] for t in out] == ["A"]


def test_regime_aware_excludes_sideways_downtrend():
    day = [_trade(symbol="A", regime="UPTREND"), _trade(symbol="B", regime="SIDEWAYS"),
           _trade(symbol="C", regime="DOWNTREND"), _trade(symbol="D", regime="HIGH_VOLATILITY")]
    out = rr._regime_aware(day, 5)
    syms = {t["symbol"] for t in out}
    assert syms == {"A", "D"}


# ─────────── verdict (look-ahead 제외 + filter vs reorder) ───────────


def _m(pf, mdd, exp, n=40, syms=8):
    return {"profit_factor": pf, "mdd_pct": mdd, "expectancy": exp, "trade_count": n,
            "selected_count": n, "selected_symbols": [f"s{i}" for i in range(syms)]}


def test_verdict_filter_only_when_reorder_loses_to_filter():
    base = _m(1.28, 20.7, 14.0, n=74, syms=10)
    oos = {
        "EARLIEST_FIRST": base,
        "NO_RANKING_RISK_VETO_ONLY": _m(1.37, 16.9, 20.2),
        "SIMPLE_FILTER_THEN_EARLIEST": _m(1.37, 16.9, 20.2),
        "COST_EDGE_FIRST": _m(1.30, 15.5, 16.5),
        "LIQUIDITY_FIRST": _m(1.30, 17.1, 16.6),
        "REGIME_AWARE": _m(1.49, 15.7, 24.6),   # look-ahead → 무시돼야
        "CURRENT_COMPOSITE": _m(0.79, 31.5, -13.3),
    }
    stress = {n: {"10.0bps": (m["profit_factor"] - 0.1)} for n, m in oos.items()}
    verdict, best, rec = rr._verdict(oos, base, oos, stress)
    assert verdict == "RANKING_FILTER_ONLY_RECOMMENDED"
    assert best["name"] in ("NO_RANKING_RISK_VETO_ONLY", "SIMPLE_FILTER_THEN_EARLIEST")


def test_verdict_rejected_when_nothing_beats_earliest():
    base = _m(1.5, 10.0, 30.0, n=74, syms=10)
    oos = {"EARLIEST_FIRST": base,
           "NO_RANKING_RISK_VETO_ONLY": _m(1.2, 14, 10),
           "SIMPLE_FILTER_THEN_EARLIEST": _m(1.1, 15, 8),
           "COST_EDGE_FIRST": _m(1.0, 16, 5),
           "LIQUIDITY_FIRST": _m(0.9, 18, 2)}
    stress = {n: {"10.0bps": m["profit_factor"]} for n, m in oos.items()}
    verdict, _b, _r = rr._verdict(oos, base, oos, stress)
    assert verdict == "RANKING_REJECTED"


def test_verdict_oos_validated_clean_reorder_beats_filter():
    base = _m(1.0, 25, 5, n=74, syms=10)
    oos = {"EARLIEST_FIRST": base,
           "NO_RANKING_RISK_VETO_ONLY": _m(1.1, 20, 8),
           "SIMPLE_FILTER_THEN_EARLIEST": _m(1.1, 20, 8),
           "COST_EDGE_FIRST": _m(1.6, 12, 25, n=50, syms=8),   # clean reorder, 모두 개선
           "LIQUIDITY_FIRST": _m(1.2, 19, 10)}
    stress = {n: {"10.0bps": (m["profit_factor"])} for n, m in oos.items()}
    verdict, best, _r = rr._verdict(oos, base, oos, stress)
    assert verdict == "RANKING_OOS_VALIDATED"
    assert best["name"] == "COST_EDGE_FIRST"


def test_look_ahead_set_contains_regime_users():
    assert "REGIME_AWARE" in rr._LOOK_AHEAD
    assert "CURRENT_COMPOSITE" in rr._LOOK_AHEAD
    assert "EARLIEST_FIRST" not in rr._LOOK_AHEAD


def test_pearson():
    assert rr._pearson([1, 2, 3, 4], [1, 2, 3, 4]) == 1.0
    assert rr._pearson([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    assert rr._pearson([1, 1], [1, 2]) is None   # n<3


def test_insufficient_trades_rejected(tmp_path):
    r = rr.run_ranking_redesign(one_min_dir=tmp_path / "none", five_min_dir=tmp_path / "none5",
                                symbols=["005930"])
    assert r["available"] is False
    assert r["verdict"] == "RANKING_REJECTED"
    assert r["auto_apply_allowed"] is False
    assert r["is_live_authorization"] is False


# ─────────── API ───────────


def test_api_ranking_redesign_latest():
    r = client.get("/api/system/ranking-redesign/latest")
    assert r.status_code == 200
    d = r.json()
    assert d.get("auto_apply_allowed") is False
    assert d.get("applied_to_runtime") is False
    assert d.get("is_live_authorization") is False
    assert d.get("no_profit_guarantee") is True
    assert "verdict" in d
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", r.text)
    assert not re.search(r"\b\d{8}-\d{2}\b", r.text)


def test_api_get_only():
    assert client.post("/api/system/ranking-redesign/latest").status_code in (404, 405)


def test_module_no_order_imports():
    txt = (Path(__file__).resolve().parents[1] / "app/system/ranking_redesign.py").read_text(encoding="utf-8")
    for bad in (".place_order(", "route_order(", "OrderExecutor(", "brokers.kis import",
                "cargo build", "tauri build"):
        for line in txt.splitlines():
            assert bad not in line.split("#", 1)[0], f"banned: {bad}"
