"""Strategy scoreboard tests (137, MUST)."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import BacktestRun
from app.strategies.scoreboard import compute_strategy_scoreboard


def _make_session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


def _run(strategy="sma_crossover", total_pnl=0, win=0, loss=0):
    return BacktestRun(
        created_at=datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc),
        strategy=strategy,
        params={}, initial_cash=10_000_000, quantity=1, bars_processed=100,
        final_cash=10_000_000 + total_pnl, total_pnl=total_pnl,
        win_count=win, loss_count=loss, max_drawdown=0,
        data_source="bars", data_symbol="005930",
    )


def test_scoreboard_empty_when_no_runs():
    Session = _make_session()
    with Session() as db:
        assert compute_strategy_scoreboard(db) == []


def test_scoreboard_groups_by_strategy_and_aggregates():
    Session = _make_session()
    with Session() as db:
        db.add_all([
            _run("sma_crossover", total_pnl=100, win=6, loss=4),
            _run("sma_crossover", total_pnl=300, win=4, loss=6),
            _run("rsi_revert",   total_pnl=-50, win=2, loss=8),
        ])
        db.commit()
        sb = compute_strategy_scoreboard(db)
    by = {s["strategy"]: s for s in sb}
    sma = by["sma_crossover"]
    assert sma["runs"]      == 2
    assert sma["total_pnl"] == 400
    assert sma["avg_pnl"]   == 200
    assert sma["best_pnl"]  == 300
    assert sma["worst_pnl"] == 100
    assert sma["wins"]      == 10
    assert sma["losses"]    == 10
    assert sma["win_rate"]  == 0.5
    rsi = by["rsi_revert"]
    assert rsi["runs"]      == 1
    assert rsi["total_pnl"] == -50
    assert rsi["worst_pnl"] == -50


def test_scoreboard_sorted_by_total_pnl_desc():
    Session = _make_session()
    with Session() as db:
        db.add_all([
            _run("loser",  total_pnl=-200),
            _run("winner", total_pnl=1000),
            _run("medium", total_pnl=100),
        ])
        db.commit()
        sb = compute_strategy_scoreboard(db)
    assert [s["strategy"] for s in sb] == ["winner", "medium", "loser"]


def test_scoreboard_handles_empty_strategy_as_unknown():
    """schema는 strategy NOT NULL이지만 빈 문자열은 허용 — '(unknown)'으로 분류."""
    Session = _make_session()
    with Session() as db:
        db.add(_run("", total_pnl=10, win=1, loss=0))
        db.commit()
        sb = compute_strategy_scoreboard(db)
    assert sb[0]["strategy"] == "(unknown)"


def test_scoreboard_zero_trades_yields_zero_win_rate():
    Session = _make_session()
    with Session() as db:
        db.add(_run("flat", total_pnl=0, win=0, loss=0))
        db.commit()
        sb = compute_strategy_scoreboard(db)
    assert sb[0]["win_rate"] == 0.0


# HTTP integration
def test_scoreboard_endpoint_returns_aggregated_rows(client):
    with client.test_db_factory() as db:
        db.add_all([
            _run("a", total_pnl=100, win=5, loss=5),
            _run("b", total_pnl=-50, win=3, loss=7),
        ])
        db.commit()
    res = client.get("/api/strategies/scoreboard")
    assert res.status_code == 200
    body = res.json()
    assert [e["strategy"] for e in body] == ["a", "b"]
    assert body[0]["total_pnl"] == 100
    assert body[1]["total_pnl"] == -50


def test_scoreboard_endpoint_empty(client):
    res = client.get("/api/strategies/scoreboard")
    assert res.status_code == 200
    assert res.json() == []
