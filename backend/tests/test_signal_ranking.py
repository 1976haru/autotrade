"""Composite signal ranking 테스트 (백테스트 전용, 실주문 0건)."""

from __future__ import annotations

import pytest

from app.backtest.signal_ranking import (
    RankingResult,
    SignalCandidate,
    compute_composite_score,
    earliest_first,
    rank_signals,
)


def _c(symbol, ts, *, conf=0.7, q=70, move=40, cost=15, **kw):
    return SignalCandidate(symbol=symbol, timestamp=ts, confidence=conf, quality_score=q,
                           expected_move_bps=move, estimated_cost_bps=cost, **kw)


def test_higher_quality_beats_earlier_lower_quality():
    early_low = _c("000001", "2026-05-22T01:00:00+00:00", conf=0.5, q=50, move=20,
                   liquidity_score=40, regime_label="SIDEWAYS")
    late_high = _c("005930", "2026-05-22T01:00:30+00:00", conf=0.9, q=90, move=50,
                   liquidity_score=90, regime_label="TREND_UP", volume_expansion=2.5)
    res = rank_signals([early_low, late_high], max_slots=1, position_count=0)
    assert [s["symbol"] for s in res.selected] == ["005930"]
    # 선착순이면 반대로 早い 것을 고름.
    ef = earliest_first([early_low, late_high], max_slots=1)
    assert ef[0]["symbol"] == "000001"


def test_agent_veto_excluded():
    vetoed = _c("111111", "2026-05-22T01:00:00+00:00", conf=0.99, q=99, move=80,
                agent_risk_veto=True)
    ok = _c("005930", "2026-05-22T01:00:00+00:00")
    res = rank_signals([vetoed, ok], max_slots=5)
    syms = [s["symbol"] for s in res.selected]
    assert "111111" not in syms
    assert "005930" in syms
    assert res.vetoed_count == 1
    assert compute_composite_score(vetoed) == 0.0


def test_available_slots_respects_position_count():
    cands = [_c(f"00000{i}", "2026-05-22T01:00:00+00:00") for i in range(5)]
    res = rank_signals(cands, max_slots=5, position_count=3)
    assert res.available_slots == 2
    assert len(res.selected) == 2
    assert len(res.rejected) == 3


def test_group_cap():
    cands = [
        _c("000001", "t", q=90, symbol_group="A"),
        _c("000002", "t", q=85, symbol_group="A"),
        _c("000003", "t", q=80, symbol_group="B"),
    ]
    res = rank_signals(cands, max_slots=5, max_symbols_per_group=1)
    groups = [s["symbol_group"] for s in res.selected]
    assert groups.count("A") == 1
    assert res.group_capped_count >= 1


def test_selected_avg_score_higher_than_rejected():
    cands = [_c("000001", "t", conf=0.9, q=90, move=50, liquidity_score=90,
                regime_label="TREND_UP", volume_expansion=2.5),
             _c("000002", "t", conf=0.4, q=40, move=10, cost=18, liquidity_score=30,
                regime_label="TREND_DOWN")]
    res = rank_signals(cands, max_slots=1)
    assert res.selected_avg_score > res.rejected_avg_score


def test_negative_edge_low_score():
    bad = _c("000009", "t", move=10, cost=30)   # net edge 음수
    assert compute_composite_score(bad) < compute_composite_score(
        _c("000010", "t", move=50, cost=15))


def test_ranking_result_invariants():
    with pytest.raises(ValueError):
        RankingResult(selected=[], rejected=[], available_slots=0,
                      selected_avg_score=0, rejected_avg_score=0, vetoed_count=0,
                      group_capped_count=0, is_order_signal=True)
    res = rank_signals([_c("005930", "t")], max_slots=5)
    d = res.to_dict()
    assert d["is_order_signal"] is False
    assert d["auto_apply_allowed"] is False
    assert d["is_live_authorization"] is False


def test_module_no_order_imports():
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "app/backtest/signal_ranking.py"
    txt = src.read_text(encoding="utf-8")
    for bad in ("place_order", "route_order(", "OrderExecutor", "brokers.kis",
                "cargo build", "tauri build"):
        for line in txt.splitlines():
            assert bad not in line.split("#", 1)[0], f"banned: {bad}"
