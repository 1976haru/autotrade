"""종목군 × 시장국면 다변화 백테스트 테스트 (CHECKLIST-05, 백테스트 전용, 실주문/자동적용 0).

요청서의 test_universe_manifest / test_market_regime_manifest / test_universe_regime_backtest /
test_universe_regime_report / test_universe_regime_api / test_no_runtime_apply 를 본 파일에
통합 (universe_regime / universe_manifest / market_regime 키워드로 -k 매칭).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.market_data import market_regime_diversification as mr
from app.market_data import universe_diversification as ud
from app.system import universe_regime_backtest as urb

client = TestClient(app)


# ─────────── universe manifest ───────────


def test_universe_groups_five_groups_min_5_symbols():
    assert set(ud.UNIVERSE_GROUPS) == {
        "LARGE_CAP_CORE", "MID_CAP_LIQUID", "KOSDAQ_LIQUID", "HIGH_VOL_THEME", "ETF_PROXY"}
    for g, members in ud.UNIVERSE_GROUPS.items():
        assert len(members) >= 5, g
        for code, name in members:
            assert len(code) == 6 and code.isdigit()
            assert name


def test_all_symbols_dedup():
    syms = ud.all_symbols()
    assert len(syms) == len(set(syms))   # dedup (overlaps 042700/277810)
    sg = ud.symbol_groups()
    assert "MID_CAP_LIQUID" in sg["042700"] and "HIGH_VOL_THEME" in sg["042700"]


def test_universe_manifest_data_presence(tmp_path):
    (tmp_path / "005930_5m.csv").write_text("timestamp,open,high,low,close,volume\n")
    (tmp_path / "069500_5m.csv").write_text("timestamp,open,high,low,close,volume\n")
    m = ud.build_universe_manifest([tmp_path])
    assert m["is_research_only"] is True
    assert m["groups"]["LARGE_CAP_CORE"]["available_count"] >= 1
    assert m["total_present"] >= 2
    assert m["auto_apply_allowed"] is False
    # 미수집 종목은 failed 에 기록.
    assert any(":" in f for f in m["failed_symbols"])


# ─────────── market regime manifest ───────────


def test_classify_regime_labels():
    # 강한 상승: mom>5% + 당일 양봉.
    series = [{"open": 100, "close": 100, "high": 100.5, "low": 99.5} for _ in range(6)]
    series.append({"open": 106, "close": 107, "high": 107.5, "low": 105.5})
    assert mr.classify_regime(series, 6) == "STRONG_UPTREND"
    # 고변동: 당일 range > 4%.
    hv = [{"open": 100, "close": 100, "high": 100.1, "low": 99.9} for _ in range(6)]
    hv.append({"open": 100, "close": 100, "high": 104, "low": 99})
    assert mr.classify_regime(hv, 6) == "HIGH_VOLATILITY"
    # 하락.
    dn = [{"open": 100, "close": 100, "high": 100.2, "low": 99.8} for _ in range(6)]
    dn.append({"open": 97, "close": 96, "high": 97.1, "low": 95.9})
    assert mr.classify_regime(dn, 6) == "DOWNTREND"


def test_regime_manifest_lookahead_flagged():
    rmap = {"2026-05-01": "SIDEWAYS", "2026-05-02": "STRONG_UPTREND"}
    man = mr.build_regime_manifest(rmap, "069500")
    assert man["regime_is_lookahead"] is True   # 사후 attribution 명시
    assert man["trading_days"] == 2
    assert set(man["regimes"]) == set(mr.REGIMES)
    assert man["auto_apply_allowed"] is False


# ─────────── backtest helpers / verdict ───────────


def test_strategy_block_empty():
    assert urb._strategy_block([])["net_pf"] is None


def test_verdict_need_more_data():
    gr = {"LARGE_CAP_CORE": {"present": 10, "four_strategy": {"ORB": {"net_pf": 0.2}},
                             "council": {"net_pf": 0.6}, "council_risk_filter_pf": 0.6,
                             "mean_reversion": {}},
          "ETF_PROXY": {"present": 0}}
    v, _c, _s, _f = urb._verdict(gr, {})
    assert v == "NEED_MORE_DATA"   # 데이터 그룹 < 3


def test_verdict_group_specific_edge():
    def grp(pf):
        return {"present": 8, "four_strategy": {"ORB": {"net_pf": pf}, "MOMENTUM": {"net_pf": 0.5}},
                "council": {"net_pf": 0.6}, "council_risk_filter_pf": 0.6, "mean_reversion": {},
                "best_single_strategy": "ORB", "best_single_pf": pf}
    gr = {"LARGE_CAP_CORE": grp(0.2), "MID_CAP_LIQUID": grp(0.3), "KOSDAQ_LIQUID": grp(1.4)}
    v, _c, surv, _f = urb._verdict(gr, {})
    assert v == "GROUP_SPECIFIC_EDGE_FOUND"
    assert any("KOSDAQ_LIQUID" in s for s in surv)


def test_verdict_universe_edge_not_found():
    def grp():
        return {"present": 8, "four_strategy": {"ORB": {"net_pf": 0.2}, "MOMENTUM": {"net_pf": 0.2}},
                "council": {"net_pf": 0.5}, "council_risk_filter_pf": 0.4, "mean_reversion": {},
                "best_single_strategy": "ORB", "best_single_pf": 0.2}
    gr = {"A": grp(), "B": grp(), "C": grp()}
    v, _c, _s, fails = urb._verdict(gr, {})
    assert v == "UNIVERSE_EDGE_NOT_FOUND"
    assert len(fails) == 3


# ─────────── insufficient data ───────────


def test_insufficient_symbols(tmp_path):
    r = urb.run_universe_regime_backtest(data_dirs=[tmp_path], run_council=False)
    assert r["available"] is False
    assert r["verdict"] == "NEED_MORE_DATA"
    assert r["auto_apply_allowed"] is False
    assert r["is_live_authorization"] is False


# ─────────── API ───────────


def test_api_universe_regime_latest():
    r = client.get("/api/system/universe-regime-backtest/latest")
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
    assert client.post("/api/system/universe-regime-backtest/latest").status_code in (404, 405)


# ─────────── no runtime apply / registration (static grep) ───────────


def test_no_runtime_apply():
    for mod in ("app/system/universe_regime_backtest.py",
                "app/market_data/universe_diversification.py",
                "app/market_data/market_regime_diversification.py"):
        txt = (Path(__file__).resolve().parents[1] / mod).read_text(encoding="utf-8")
        assert "STRATEGY_REGISTRY[" not in txt
        assert "register_strategy(" not in txt
        for line in txt.splitlines():
            code = line.split("#", 1)[0]
            if code.lstrip().startswith(("import ", "from ")):
                assert "LiveStrategyEngine" not in code
                assert "app.execution" not in code
            for bad in (".place_order(", "route_order(", "OrderExecutor(", "cargo build",
                        "tauri build", "ENABLE_LIVE_TRADING =", "KIS_IS_PAPER =",
                        "auto_apply_allowed=True", "applied_to_runtime=True"):
                assert bad not in code, f"{mod}: banned {bad}"
