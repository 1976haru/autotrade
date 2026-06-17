"""청산 round-trip → 진입 episode outcome 라벨링 (집계 계층). 학습 expectancy 선결."""
from __future__ import annotations

import ast
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import AgentDecisionEpisode
import app.analytics.outcome_labeler as ol

NOW = datetime(2026, 6, 17, 4, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _entry_episode(db, *, audit_id, selected, eid="e1", sym="005930"):
    db.add(AgentDecisionEpisode(
        episode_id=eid, symbol=sym, final_action="BUY", audit_id=audit_id,
        council={"selected_strategies": selected}))
    db.commit()


def _rt(audit_id, net, cost, closed=None):
    return SimpleNamespace(entry_audit_ids=[audit_id], net_pnl=net, buy_cost=cost,
                           quantity=10, closed_at_kst=closed or date(2026, 6, 17))


def test_labels_entry_episode_with_return_and_strategy(db, monkeypatch):
    _entry_episode(db, audit_id=975, selected=["ORB", "VWAP"])
    monkeypatch.setattr("app.performance.performance.compute_round_trips",
                        lambda d: [_rt(975, net=5000, cost=100000)])
    n = ol.label_closed_round_trips(db, now=NOW)
    assert n == 1
    ep = db.query(AgentDecisionEpisode).filter_by(episode_id="e1").first()
    assert ep.outcome["status"] == "FILLED"
    assert ep.outcome["realized_pnl"] == 5.0   # 5000/100000 = +5%
    assert ep.outcome["label"] == "WIN"
    assert ep.outcome["source"] == ol.OUTCOME_SOURCE
    # ★selected_strategies(진입 voters) 보존 → 기법별 귀속 가능(ORB/VWAP)
    assert (ep.council or {}).get("selected_strategies") == ["ORB", "VWAP"]


def test_idempotent_no_double_label(db, monkeypatch):
    _entry_episode(db, audit_id=975, selected=["ORB"])
    monkeypatch.setattr("app.performance.performance.compute_round_trips",
                        lambda d: [_rt(975, net=-2000, cost=100000)])
    assert ol.label_closed_round_trips(db, now=NOW) == 1   # 첫 라벨(LOSS)
    assert ol.label_closed_round_trips(db, now=NOW) == 0   # 재실행 시 skip(멱등)
    ep = db.query(AgentDecisionEpisode).filter_by(episode_id="e1").first()
    assert ep.outcome["label"] == "LOSS" and ep.outcome["realized_pnl"] == -2.0


def test_old_closed_skipped_no_backfill(db, monkeypatch):
    _entry_episode(db, audit_id=975, selected=["ORB"])
    monkeypatch.setattr("app.performance.performance.compute_round_trips",
                        lambda d: [_rt(975, net=5000, cost=100000, closed=date(2026, 6, 1))])
    assert ol.label_closed_round_trips(db, now=NOW, lookback_days=2) == 0   # 소급 안 함


def test_integration_strategy_expectancy_not_degenerate(db, monkeypatch):
    # 라벨 후 strategy_performance 가 기법별 win/payoff 를 산출(degenerate 해소).
    for i, (net, sel) in enumerate([(5000, ["ORB"]), (-2000, ["ORB"]), (3000, ["VWAP"])]):
        _entry_episode(db, audit_id=900 + i, selected=sel, eid=f"e{i}")
    monkeypatch.setattr("app.performance.performance.compute_round_trips",
                        lambda d: [_rt(900, 5000, 100000), _rt(901, -2000, 100000), _rt(902, 3000, 100000)])
    ol.label_closed_round_trips(db, now=NOW)
    from app.analytics.strategy_performance import calculate_strategy_performance
    eps = [{"symbol": e.symbol, "final_action": e.final_action,
            "selected_strategies": (e.council or {}).get("selected_strategies"),
            "outcome": e.outcome, "council": e.council}
           for e in db.query(AgentDecisionEpisode).all()]
    rep = calculate_strategy_performance(eps)
    blocks = {b["strategy"]: b for b in rep.strategies}
    assert blocks["ORB"]["win_rate"] == 0.5   # ORB 2거래 1승1패 → 50%(degenerate 해소)
    assert blocks["ORB"]["trade_count"] == 2
    assert blocks["VWAP"]["win_rate"] == 1.0 and blocks["VWAP"]["trade_count"] == 1
    assert blocks["ORB"]["expectancy"] is not None


def test_static_no_council_route_order_import():
    src = (Path(__file__).resolve().parents[1] / "app" / "analytics" / "outcome_labeler.py").read_text(encoding="utf-8")
    mods = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.ImportFrom): mods.append(n.module or "")
        elif isinstance(n, ast.Import): mods += [a.name for a in n.names]
    for bad in ("agent_council", "order_router", "order_executor", "route_order", "driver_bridge", "brokers"):
        assert not any(bad in (m or "") for m in mods), f"forbidden import: {bad}"
