"""에이전트 학습효과 측정 모듈 — 정직성/과적합/비용벽/안전 invariant 테스트."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.agents.learning_measurement import (
    DecisionRecord,
    LearningVerdict,
    LearningMeasurementReport,
    measure_learning,
    report_to_dict,
    COST_WALL_ROUNDTRIP_BPS,
)

MODULE = Path(__file__).resolve().parents[1] / "app" / "agents" / "learning_measurement.py"
DAY = 86400.0


def _recs(pnls, conf=0.7, qual=70.0, start=1_700_000_000.0):
    """pnls: list of realized_pnl_pct (None=open). 하루 간격 BUY 기록."""
    return [DecisionRecord(ts_epoch=start + i * DAY, action="BUY",
                           realized_pnl_pct=p, confidence=conf, quality_score=qual)
            for i, p in enumerate(pnls)]


# ---------- safety / honesty invariants ----------
def test_no_broker_or_db_write_imports():
    src = MODULE.read_text(encoding="utf-8")
    for pat in [r"^\s*from app\.brokers", r"^\s*from app\.execution",
                r"\.place_order\(", r"route_order\(", r"db\.add\(", r"db\.commit\(",
                r"^\s*import httpx", r"^\s*import requests"]:
        assert not re.search(pat, src, re.MULTILINE), f"forbidden: {pat}"


def test_report_invariants_always_false():
    rep = measure_learning(_recs([0.01] * 30))
    assert rep.assumes_improvement is False
    assert rep.is_order_signal is False
    assert rep.is_live_authorization is False
    assert rep.auto_apply_allowed is False


def test_cannot_construct_assuming_improvement():
    with pytest.raises(ValueError):
        LearningMeasurementReport(
            verdict=LearningVerdict.NO_TREND, periods=(), initial=None, later=None,
            delta_avg_pnl_pp=None, delta_win_rate_pp=None, delta_quality=None,
            cost_wall_bps=33.0, overfit_suspected=False, n_total=0,
            assumes_improvement=True)


def test_cost_wall_is_33bps():
    assert COST_WALL_ROUNDTRIP_BPS == 33.0
    rep = measure_learning(_recs([0.01] * 30))
    assert rep.cost_wall_bps == 33.0
    assert report_to_dict(rep)["cost_wall_bps"] == 33.0


# ---------- insufficient data ----------
def test_insufficient_data_when_few_closed():
    rep = measure_learning(_recs([0.01] * 5))  # 5 < default 20
    assert rep.verdict == LearningVerdict.INSUFFICIENT_DATA
    assert rep.initial is None


def test_open_trades_not_counted():
    rep = measure_learning(_recs([None] * 30))  # all open
    assert rep.verdict == LearningVerdict.INSUFFICIENT_DATA  # 0 closed


# ---------- honest trend detection ----------
def test_improving_above_wall():
    # initial losing, later winning above 0 (cost-inclusive realized)
    pnls = [-0.01] * 15 + [0.02] * 15
    rep = measure_learning(pnls and _recs(pnls))
    assert rep.verdict == LearningVerdict.IMPROVING_ABOVE_WALL
    assert rep.delta_avg_pnl_pp is not None and rep.delta_avg_pnl_pp > 0
    assert rep.later.above_cost_wall is True


def test_improving_below_wall():
    # later better than initial but still negative avg -> below wall
    pnls = [-0.03] * 15 + [-0.005] * 15
    rep = measure_learning(_recs(pnls))
    assert rep.verdict == LearningVerdict.IMPROVING_BELOW_WALL
    assert rep.later.above_cost_wall is False


def test_degrading():
    pnls = [0.02] * 15 + [-0.02] * 15
    rep = measure_learning(_recs(pnls))
    assert rep.verdict == LearningVerdict.DEGRADING
    assert rep.delta_avg_pnl_pp < 0


def test_no_trend_flat():
    rep = measure_learning(_recs([0.0001] * 30))
    assert rep.verdict in (LearningVerdict.NO_TREND, LearningVerdict.IMPROVING_ABOVE_WALL,
                           LearningVerdict.DEGRADING)
    # flat ~ delta near zero
    assert abs(rep.delta_avg_pnl_pp or 0) < 1.0


# ---------- overfit guard ----------
def test_overfit_suspected_only_recent_good():
    # 5 buckets of 10 each: first 4 buckets <=0, last bucket strongly +
    pnls = ([-0.01] * 40) + ([0.03] * 10)
    rep = measure_learning(_recs(pnls), n_buckets=5)
    assert rep.overfit_suspected is True
    assert rep.verdict == LearningVerdict.OVERFIT_SUSPECTED
    assert any("과적합" in n for n in rep.notes)


def test_consistent_improvement_not_flagged_overfit():
    # gradual improvement across buckets, not just last
    pnls = [-0.02]*10 + [-0.005]*10 + [0.005]*10 + [0.01]*10 + [0.015]*10
    rep = measure_learning(_recs(pnls), n_buckets=5)
    assert rep.overfit_suspected is False


# ---------- structure ----------
def test_periods_and_dict_shape():
    rep = measure_learning(_recs([0.01, -0.01] * 20), n_buckets=4)
    assert len(rep.periods) == 4
    d = report_to_dict(rep)
    assert set(d) >= {"verdict", "periods", "initial", "later", "cost_wall_bps",
                      "overfit_suspected", "assumes_improvement"}
    assert d["assumes_improvement"] is False
    # periods carry timestamps for trend plotting
    assert all(p["from"] and p["to"] for p in d["periods"])


def test_confidence_quality_tracked():
    recs = [DecisionRecord(ts_epoch=1_700_000_000.0 + i * DAY, action="BUY",
                           realized_pnl_pct=0.01, confidence=0.5 + i * 0.01,
                           quality_score=60 + i) for i in range(30)]
    rep = measure_learning(recs)
    assert rep.initial.avg_confidence is not None
    assert rep.later.avg_quality > rep.initial.avg_quality  # quality rising
