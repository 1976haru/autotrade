"""장중 평균회귀 전략 연구 테스트 (CHECKLIST-05, 백테스트 전용, 실주문/자동적용 0)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.research import mean_reversion_candidates as mc
from app.system import mean_reversion_strategy as mr

client = TestClient(app)


class _MI:
    def __init__(self, **kw):
        self.current_price = kw.get("cp", 100.0)
        self.prev_close = kw.get("prev", 100.0)
        self.open_price = kw.get("open", 100.0)
        self.vwap = kw.get("vwap", 100.0)
        self.opening_range_high = kw.get("orh")
        self.opening_range_low = kw.get("orl")
        self.recent_closes = kw.get("rc", (100.0, 100.0, 100.0))


class _Bar:
    def __init__(self, h, lo):
        self.high, self.low, self.close, self.open = h, lo, (h + lo) / 2, (h + lo) / 2


# ─────────── research-only invariants ───────────


def test_is_research_only_flag():
    assert mc.IS_RESEARCH_ONLY is True
    cat = mc.candidate_catalog()
    assert cat["is_research_only"] is True
    assert cat["long_only"] is True
    assert len(cat["candidates"]) == 6


def test_candidates_not_registered_in_runtime():
    """후보 모듈이 런타임 strategy registry / engine 과 *연결(import/호출)* 되지 않음 — 정적 lock.

    (docstring 이 분리를 *설명* 하는 텍스트는 허용 — 실제 import/호출만 금지.)
    """
    for mod in ("app/research/mean_reversion_candidates.py",
                "app/system/mean_reversion_strategy.py"):
        txt = (Path(__file__).resolve().parents[1] / mod).read_text(encoding="utf-8")
        assert "STRATEGY_REGISTRY[" not in txt
        assert "register_strategy(" not in txt
        # import / 사용 라인만 검사 (설명 docstring 제외).
        for line in txt.splitlines():
            code = line.split("#", 1)[0]
            if code.lstrip().startswith(("import ", "from ")):
                assert "LiveStrategyEngine" not in code
                assert "app.strategies" not in code
            assert "LiveStrategyEngine(" not in code


# ─────────── candidate entry conditions (long-only, look-ahead 금지) ───────────


def test_vwap_deviation_revert():
    mi = _MI(cp=99.5, vwap=100, rc=(99.2, 99.3, 99.5))  # 50bps 아래 + 반전
    assert mc.cand_vwap_deviation_revert(mi, ve=1.0, gap=None, day_bars=[], i=0) is True
    mi2 = _MI(cp=99.95, vwap=100, rc=(99.9, 99.9, 99.95))  # 5bps 만 아래 → 미달
    assert mc.cand_vwap_deviation_revert(mi2, ve=1.0, gap=None, day_bars=[], i=0) is False


def test_orb_failed_breakout_fade():
    mi = _MI(cp=101, orh=103, orl=100, rc=(101, 99.5, 101))  # ORL 아래 갔다 복귀
    assert mc.cand_orb_failed_breakout_fade(mi, ve=1.0, gap=None, day_bars=[], i=0) is True
    mi2 = _MI(cp=101, orh=103, orl=100, rc=(101, 100.5, 101))  # 이탈 없음
    assert mc.cand_orb_failed_breakout_fade(mi2, ve=1.0, gap=None, day_bars=[], i=0) is False


def test_gap_fade_mean_reversion_capped_and_longonly():
    mi = _MI(cp=98.5, open=98, prev=100)
    assert mc.cand_gap_fade_mean_reversion(mi, ve=1.0, gap=-0.03, day_bars=[], i=0) is True
    # 과도한 gap(−8%) 제외
    assert mc.cand_gap_fade_mean_reversion(mi, ve=1.0, gap=-0.08, day_bars=[], i=0) is False
    # gap up 은 long fade 대상 아님
    assert mc.cand_gap_fade_mean_reversion(mi, ve=1.0, gap=0.03, day_bars=[], i=0) is False


def test_volume_spike_reversion_needs_spike():
    mi = _MI(cp=99.4, vwap=100, rc=(99.1, 99.2, 99.4))
    assert mc.cand_volume_spike_reversion(mi, ve=2.5, gap=None, day_bars=[], i=0) is True
    assert mc.cand_volume_spike_reversion(mi, ve=1.2, gap=None, day_bars=[], i=0) is False  # spike 부족


def test_range_mid_reversion_lookahead_free():
    bars = [_Bar(101, 99) for _ in range(5)] + [_Bar(100.3, 99.0)]
    mi = _MI(cp=99.3, rc=(99.0, 99.1, 99.3))  # range 하단 근처 + 반전
    assert mc.cand_range_mid_reversion(mi, ve=1.0, gap=None, day_bars=bars, i=5) is True


def test_is_trend_day_filter():
    # 시가 100 -> 현재 103 (3% 이동) + 고점 부근 → 추세일
    up = [_Bar(103, 100) for _ in range(6)]
    mi = _MI(cp=103, open=100)
    assert mc.is_trend_day(mi, day_bars=up, i=5) is True
    flat = [_Bar(100.5, 99.5) for _ in range(6)]
    mi2 = _MI(cp=100, open=100)
    assert mc.is_trend_day(mi2, day_bars=flat, i=5) is False


# ─────────── orchestrator helpers ───────────


def test_cost_verdict():
    assert mr._cost_verdict(0.6, 0.3, {"10.0bps": 0.2}) == "NO_GROSS_EDGE"
    assert mr._cost_verdict(1.2, 0.9, {"10.0bps": 0.8}) == "COST_KILLS_EDGE"
    assert mr._cost_verdict(1.5, 1.1, {"10.0bps": 0.8}) == "COST_FRAGILE"
    assert mr._cost_verdict(1.6, 1.3, {"10.0bps": 1.1}) == "RESEARCH_CANDIDATE"


def test_candidate_research_verdict():
    assert mr._candidate_research_verdict(0.8, None, None, None) == "REJECT"
    assert mr._candidate_research_verdict(1.1, None, None, None) == "WATCH"
    assert mr._candidate_research_verdict(1.2, 0.9, None, 1.0) == "CANDIDATE"
    assert mr._candidate_research_verdict(1.4, 1.2, None, 1.1) == "STRONG_CANDIDATE"


def test_overall_verdict_ladder():
    # cost fragile
    cr = {"X": {"cost_verdict": "COST_FRAGILE", "oos": {}, "trade_count": 50}}
    v, _c = mr._overall_verdict(cr, [], [], [])
    assert v == "MEAN_REVERSION_COST_FRAGILE"
    # validated
    cr2 = {"X": {"cost_verdict": "RESEARCH_CANDIDATE", "oos": {"oos_pf": 1.2}, "trade_count": 40}}
    v2, _c2 = mr._overall_verdict(cr2, ["X"], [], [])
    assert v2 == "MEAN_REVERSION_OOS_VALIDATED"
    # needs more data
    cr3 = {"X": {"cost_verdict": "NO_GROSS_EDGE", "oos": {}, "trade_count": 10}}
    v3, _c3 = mr._overall_verdict(cr3, [], [], ["X"])
    assert v3 == "MEAN_REVERSION_NEEDS_MORE_DATA"
    # edge not found
    cr4 = {"X": {"cost_verdict": "NO_GROSS_EDGE", "oos": {}, "trade_count": 100,
                 "target_hit_ratio": 0.05}}
    v4, _c4 = mr._overall_verdict(cr4, [], [], [])
    assert v4 == "MEAN_REVERSION_EDGE_NOT_FOUND"


def test_symbol_split():
    recs = [{"symbol": "A", "net": 1.0}, {"symbol": "B", "net": -0.5},
            {"symbol": "C", "net": 0.3}]
    s = mr._symbol_split(recs, ["A", "B", "C"])
    assert s["even_n"] + s["odd_n"] == 3


# ─────────── insufficient data ───────────


def test_insufficient_trades(tmp_path):
    r = mr.run_mean_reversion_strategy(one_min_dir=tmp_path / "n", five_min_dir=tmp_path / "n5",
                                       symbols=["005930"])
    assert r["available"] is False
    assert r["verdict"] == "BACKTEST_INFRA_INCOMPLETE"
    assert r["auto_apply_allowed"] is False
    assert r["is_live_authorization"] is False


# ─────────── API ───────────


def test_api_mean_reversion_latest():
    r = client.get("/api/system/mean-reversion-strategy/latest")
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
    assert client.post("/api/system/mean-reversion-strategy/latest").status_code in (404, 405)


# ─────────── no runtime apply / safety (static grep) ───────────


def test_no_runtime_order_imports():
    for mod in ("app/research/mean_reversion_candidates.py",
                "app/system/mean_reversion_strategy.py"):
        txt = (Path(__file__).resolve().parents[1] / mod).read_text(encoding="utf-8")
        for bad in (".place_order(", "route_order(", "OrderExecutor(", "brokers.kis import",
                    "brokers.mock", "cargo build", "tauri build", "ENABLE_LIVE_TRADING =",
                    "KIS_IS_PAPER =", "auto_apply_allowed=True", "applied_to_runtime=True"):
            for line in txt.splitlines():
                assert bad not in line.split("#", 1)[0], f"{mod}: banned {bad}"
