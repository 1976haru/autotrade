"""전략 엣지 재설계 연구 테스트 (CHECKLIST-05, 백테스트 전용, 실주문/자동적용 0)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.system import strategy_edge_redesign as se

client = TestClient(app)
_KST = timezone(timedelta(hours=9))


# ─────────── time bucket / mfe-mae ───────────


def test_time_bucket_kst():
    def k(h, m):
        return datetime(2026, 5, 20, h, m, tzinfo=_KST)
    assert se._time_bucket(k(9, 10)) == "09:00-09:30"
    assert se._time_bucket(k(9, 45)) == "09:30-10:30"
    assert se._time_bucket(k(11, 0)) == "10:30-12:00"
    assert se._time_bucket(k(13, 30)) == "13:00-14:30"
    assert se._time_bucket(k(15, 0)) == "14:30-15:20"
    assert se._time_bucket(None) == "OTHER"


def test_mfe_mae_bps():
    et = datetime(2026, 5, 20, 0, 30, tzinfo=timezone.utc)
    bars = [{"ts": et + timedelta(minutes=i), "high": 102, "low": 98, "close": 100}
            for i in range(5)]
    mfe, mae = se._mfe_mae_bps(100.0, et, bars)
    assert mfe > 0 and mae < 0           # favorable up, adverse down
    assert se._mfe_mae_bps(0.0, et, bars) == (0.0, 0.0)
    assert se._mfe_mae_bps(100.0, et, []) == (0.0, 0.0)


# ─────────── candidate entry conditions (look-ahead 금지) ───────────


class _MI:
    def __init__(self, **kw):
        self.current_price = kw.get("cp", 100.0)
        self.prev_close = kw.get("prev", 100.0)
        self.open_price = kw.get("open", 100.0)
        self.vwap = kw.get("vwap", 100.0)
        self.opening_range_high = kw.get("orh")
        self.opening_range_low = kw.get("orl")
        self.recent_closes = kw.get("rc", (100.0, 100.0, 100.0))


def test_cand_orb_confirmation_needs_sustained_breakout():
    # 직전 bar 도 ORH 위 + 거래량 확대 → BUY
    mi = _MI(cp=105, orh=104, orl=100, rc=(101, 104.5, 105))
    assert se._cand_orb_confirmation(mi, ve=1.3, gap=None, day_bars=[], i=0) is True
    # 거래량 부족 → 진입 금지
    assert se._cand_orb_confirmation(mi, ve=1.0, gap=None, day_bars=[], i=0) is False
    # 직전 bar 가 ORH 아래(첫 spike) → 진입 금지(false breakout 회피)
    mi2 = _MI(cp=105, orh=104, orl=100, rc=(101, 102, 105))
    assert se._cand_orb_confirmation(mi2, ve=1.3, gap=None, day_bars=[], i=0) is False


def test_cand_vwap_reclaim():
    mi = _MI(cp=101, vwap=100, rc=(99, 98.5, 101))   # dip below then reclaim
    assert se._cand_vwap_reclaim(mi, ve=1.2, gap=None, day_bars=[], i=0) is True
    mi2 = _MI(cp=101, vwap=100, rc=(101, 102, 101))  # never dipped
    assert se._cand_vwap_reclaim(mi2, ve=1.2, gap=None, day_bars=[], i=0) is False


def test_cand_gap_fade_filtered_capped():
    mi = _MI(cp=101, open=100)
    assert se._cand_gap_fade_filtered(mi, ve=1.1, gap=-0.03, day_bars=[], i=0) is True
    # 과도한 gap(−8%)은 제한 → 진입 금지
    assert se._cand_gap_fade_filtered(mi, ve=1.1, gap=-0.08, day_bars=[], i=0) is False
    # gap up 은 fade 대상 아님(long-only)
    assert se._cand_gap_fade_filtered(mi, ve=1.1, gap=0.03, day_bars=[], i=0) is False


def test_cand_high_vol_only_orb_no_lookahead():
    # opening range 폭 proxy 만 사용(당일 미래 미사용) — 2%+ 면 허용
    mi = _MI(cp=106, open=100, orh=104, orl=101)   # or width 3%
    assert se._cand_high_vol_only_orb(mi, ve=1.0, gap=None, day_bars=[], i=0) is True
    mi2 = _MI(cp=106, open=100, orh=101, orl=100.5)  # or width 0.5% → 저변동
    assert se._cand_high_vol_only_orb(mi2, ve=1.0, gap=None, day_bars=[], i=0) is False


def test_no_trade_filter():
    mi = _MI(rc=(100, 100.1, 100.05))   # 횡보
    assert se._no_trade(mi, ve=0.5, gap=None) is True   # 저거래량
    assert se._no_trade(mi, ve=1.5, gap=None) is True   # 횡보(<0.4%)
    mi2 = _MI(rc=(100, 103, 102))       # 충분한 변동
    assert se._no_trade(mi2, ve=1.5, gap=None) is False


# ─────────── verdict / OOS / rolling ───────────


def test_candidate_verdict_thresholds():
    assert se._candidate_verdict(0.8, None, None, None) == "REJECT"
    assert se._candidate_verdict(1.1, None, None, None) == "WATCH"
    assert se._candidate_verdict(1.2, 0.9, None, 1.0) == "CANDIDATE"
    assert se._candidate_verdict(1.4, 1.2, None, 1.1) == "STRONG_CANDIDATE"
    # PF>1.3 이나 OOS 미달 → CANDIDATE 강등
    assert se._candidate_verdict(1.4, 0.8, None, 1.0) == "CANDIDATE"


def _recs(days, net):
    out = []
    for d in days:
        out.append({"day": d, "net": net, "entry": 100.0, "symbol": "005930",
                    "ts": datetime(2026, 5, 1, tzinfo=timezone.utc)})
    return out


def test_oos_split_and_rolling():
    days = [f"2026-05-{d:02d}" for d in range(1, 41)]
    recs = _recs(days, 1.0)
    oos = se._oos_split(recs)
    assert oos["available"] is True
    roll = se._rolling(recs, train_n=20, test_n=10)
    assert roll["available"] is True and roll["windows"]


def test_exit_redesign_verdict_requires_pf_ge_1():
    """둘 다 PF<1 이면 EXIT_REDESIGN 아님(손실만 줄인 것) — 정직성 lock."""
    exit_struct = {"pf_by_structure": {"existing_1.0_1.5_30": 0.19, "wider_1.5_2.5_30": 0.34},
                   "evaluated_on": "X"}
    v, _c = se._overall_verdict([], [], {}, {"orb_baseline_pf": 0.2, "orb_filtered_pf": 0.2},
                                exit_struct, {"ORB": {"net_pf": 0.2}})
    assert v == "STRATEGY_EDGE_STILL_NOT_FOUND"
    # wider 가 PF≥1.0 으로 생존하면 EXIT_REDESIGN_NEEDED
    exit_struct2 = {"pf_by_structure": {"existing_1.0_1.5_30": 0.9, "wider_1.5_2.5_30": 1.25},
                    "evaluated_on": "X"}
    v2, _c2 = se._overall_verdict([], [], {}, {"orb_baseline_pf": 0.2, "orb_filtered_pf": 0.2},
                                  exit_struct2, {})
    assert v2 == "EXIT_REDESIGN_NEEDED"


def test_strategy_candidate_found_verdict():
    cand = {"ORB_CONFIRMATION": {"verdict": "STRONG_CANDIDATE"}}
    v, _c = se._overall_verdict(["ORB_CONFIRMATION"], [], cand, {}, {"pf_by_structure": {}}, {})
    assert v == "STRATEGY_CANDIDATE_FOUND"


# ─────────── insufficient data ───────────


def test_insufficient_trades(tmp_path):
    r = se.run_strategy_edge_redesign(one_min_dir=tmp_path / "n", five_min_dir=tmp_path / "n5",
                                      symbols=["005930"])
    assert r["available"] is False
    assert r["verdict"] == "BACKTEST_INFRA_INCOMPLETE"
    assert r["auto_apply_allowed"] is False
    assert r["is_live_authorization"] is False


# ─────────── API ───────────


def test_api_strategy_edge_redesign_latest():
    r = client.get("/api/system/strategy-edge-redesign/latest")
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
    assert client.post("/api/system/strategy-edge-redesign/latest").status_code in (404, 405)


# ─────────── no runtime apply / safety (static grep) ───────────


def test_no_runtime_order_imports():
    txt = (Path(__file__).resolve().parents[1]
           / "app/system/strategy_edge_redesign.py").read_text(encoding="utf-8")
    for bad in (".place_order(", "route_order(", "OrderExecutor(", "brokers.kis import",
                "brokers.mock", "cargo build", "tauri build", "ENABLE_LIVE_TRADING =",
                "KIS_IS_PAPER =", "auto_apply_allowed=True", "applied_to_runtime=True"):
        for line in txt.splitlines():
            assert bad not in line.split("#", 1)[0], f"banned {bad}"


def test_candidates_are_research_namespace_not_registered():
    """신규 후보가 런타임 strategy registry 에 등록되지 않음 — 분리 lock."""
    txt = (Path(__file__).resolve().parents[1]
           / "app/system/strategy_edge_redesign.py").read_text(encoding="utf-8")
    assert "STRATEGY_REGISTRY[" not in txt
    assert "register_strategy(" not in txt
    assert "LiveStrategyEngine" not in txt
