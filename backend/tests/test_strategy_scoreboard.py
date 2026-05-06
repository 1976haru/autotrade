"""Strategy scoreboard tests (137, 144, MUST)."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import BacktestRun, OrderAuditLog
from app.strategies.scoreboard import (
    compute_live_strategy_pnl,
    compute_strategy_scoreboard,
)


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


# ---------- 144: live PnL FIFO pair matching ----------

def _audit(strategy="sma_crossover", symbol="005930", side="BUY",
           qty=1, fill_price=100, executed=True):
    """체결된 audit row를 생성. compute_live_strategy_pnl이 보는 4개 컬럼만 채움."""
    return OrderAuditLog(
        mode="LIVE_MANUAL_APPROVAL",
        symbol=symbol, side=side, quantity=qty,
        order_type="MARKET", latest_price=fill_price,
        decision="APPROVED", reasons=[],
        strategy=strategy,
        executed=executed,
        broker_status="FILLED",
        filled_quantity=qty if executed else 0,
        avg_fill_price=fill_price if executed else None,
    )


def test_live_pnl_simple_buy_sell_pair():
    """가장 단순한 케이스 — BUY 1주 @ 100, SELL 1주 @ 110 → 1 trade, +10 PnL, 1 win."""
    Session = _make_session()
    with Session() as db:
        db.add_all([
            _audit(side="BUY",  qty=1, fill_price=100),
            _audit(side="SELL", qty=1, fill_price=110),
        ])
        db.commit()
        live = compute_live_strategy_pnl(db)
    assert live == {"sma_crossover": {"trades": 1, "pnl": 10, "wins": 1, "losses": 0}}


def test_live_pnl_loss_counted():
    """손실 거래 — SELL 가격 < BUY 가격."""
    Session = _make_session()
    with Session() as db:
        db.add_all([
            _audit(side="BUY",  qty=1, fill_price=100),
            _audit(side="SELL", qty=1, fill_price=85),
        ])
        db.commit()
        live = compute_live_strategy_pnl(db)
    assert live["sma_crossover"] == {"trades": 1, "pnl": -15, "wins": 0, "losses": 1}


def test_live_pnl_breakeven_counts_as_loss():
    """본전(PnL=0)은 wins에 포함하지 않는다 — wins/losses 합 = trades 유지."""
    Session = _make_session()
    with Session() as db:
        db.add_all([
            _audit(side="BUY",  qty=1, fill_price=100),
            _audit(side="SELL", qty=1, fill_price=100),
        ])
        db.commit()
        live = compute_live_strategy_pnl(db)
    assert live["sma_crossover"] == {"trades": 1, "pnl": 0, "wins": 0, "losses": 1}


def test_live_pnl_partial_fill_partial_exit_fifo():
    """BUY 5 @ 100, SELL 3 @ 110 → trade 1 (PnL=30, win). 잔여 BUY 2주는
    open position이라 trade에 포함 X."""
    Session = _make_session()
    with Session() as db:
        db.add_all([
            _audit(side="BUY",  qty=5, fill_price=100),
            _audit(side="SELL", qty=3, fill_price=110),
        ])
        db.commit()
        live = compute_live_strategy_pnl(db)
    assert live["sma_crossover"] == {"trades": 1, "pnl": 30, "wins": 1, "losses": 0}


def test_live_pnl_one_sell_consumes_multiple_buys_fifo():
    """BUY 2 @ 100, BUY 3 @ 120, SELL 5 @ 130 → 1 trade (5주 청산, FIFO).
    PnL = (130-100)*2 + (130-120)*3 = 60 + 30 = 90."""
    Session = _make_session()
    with Session() as db:
        db.add_all([
            _audit(side="BUY",  qty=2, fill_price=100),
            _audit(side="BUY",  qty=3, fill_price=120),
            _audit(side="SELL", qty=5, fill_price=130),
        ])
        db.commit()
        live = compute_live_strategy_pnl(db)
    assert live["sma_crossover"] == {"trades": 1, "pnl": 90, "wins": 1, "losses": 0}


def test_live_pnl_separates_by_strategy_and_symbol():
    """다른 strategy / symbol 조합은 페어매칭이 격리된다 — 한쪽 SELL이 다른쪽
    BUY를 소진하지 않는다."""
    Session = _make_session()
    with Session() as db:
        db.add_all([
            # sma_crossover / 005930 한 쌍.
            _audit(strategy="sma_crossover", symbol="005930", side="BUY",  qty=1, fill_price=100),
            _audit(strategy="sma_crossover", symbol="005930", side="SELL", qty=1, fill_price=110),
            # rsi_revert / 005930 한 쌍 (다른 strategy → 분리).
            _audit(strategy="rsi_revert",   symbol="005930", side="BUY",  qty=1, fill_price=200),
            _audit(strategy="rsi_revert",   symbol="005930", side="SELL", qty=1, fill_price=180),
            # sma_crossover / 000660 한 쌍 (다른 symbol → 분리).
            _audit(strategy="sma_crossover", symbol="000660", side="BUY",  qty=2, fill_price=50),
            _audit(strategy="sma_crossover", symbol="000660", side="SELL", qty=2, fill_price=60),
        ])
        db.commit()
        live = compute_live_strategy_pnl(db)
    sma = live["sma_crossover"]
    assert sma["trades"] == 2
    assert sma["pnl"]    == 10 + 20  # 005930: +10, 000660: +20
    assert sma["wins"]   == 2
    rsi = live["rsi_revert"]
    assert rsi["trades"] == 1
    assert rsi["pnl"]    == -20


def test_live_pnl_skips_unexecuted_and_strategyless_rows():
    """executed=False, strategy=None, avg_fill_price=None인 row는 스킵."""
    Session = _make_session()
    with Session() as db:
        db.add_all([
            _audit(side="BUY", qty=1, fill_price=100, executed=False),  # 미체결
            OrderAuditLog(  # strategy NULL
                mode="SIMULATION", symbol="005930", side="BUY", quantity=1,
                order_type="MARKET", latest_price=100, decision="APPROVED",
                reasons=[], strategy=None, executed=True, broker_status="FILLED",
                filled_quantity=1, avg_fill_price=100,
            ),
            # 정상 페어 — 위 두 행은 무시되고 이 페어만 집계.
            _audit(side="BUY",  qty=1, fill_price=100),
            _audit(side="SELL", qty=1, fill_price=110),
        ])
        db.commit()
        live = compute_live_strategy_pnl(db)
    assert live == {"sma_crossover": {"trades": 1, "pnl": 10, "wins": 1, "losses": 0}}


def test_live_pnl_naked_sell_without_open_buy_is_ignored():
    """잔량 BUY가 없는 상태에서 SELL이 들어오면 (운영 사고) 집계에 영향 X.
    invariant: 부분 매칭만 되어도 그 부분에 대해서는 PnL 산출."""
    Session = _make_session()
    with Session() as db:
        db.add_all([
            _audit(side="SELL", qty=1, fill_price=110),  # naked — 무시
            _audit(side="BUY",  qty=2, fill_price=100),
            _audit(side="SELL", qty=3, fill_price=110),  # 2주만 매칭, 1주는 naked
        ])
        db.commit()
        live = compute_live_strategy_pnl(db)
    # 두 번째 SELL이 BUY 2주를 매칭 → trade 1건, PnL = 20.
    assert live["sma_crossover"] == {"trades": 1, "pnl": 20, "wins": 1, "losses": 0}


def test_live_pnl_pair_order_follows_id_sequence():
    """row insertion order = id order. id 순서대로 BUY/SELL을 처리해야
    여러 round-trip이 섞여도 결정적 결과."""
    Session = _make_session()
    with Session() as db:
        db.add_all([
            _audit(side="BUY",  qty=1, fill_price=100),
            _audit(side="SELL", qty=1, fill_price=110),
            _audit(side="BUY",  qty=1, fill_price=120),
            _audit(side="SELL", qty=1, fill_price=115),  # 손실
            _audit(side="BUY",  qty=1, fill_price=130),
            _audit(side="SELL", qty=1, fill_price=140),
        ])
        db.commit()
        live = compute_live_strategy_pnl(db)
    sma = live["sma_crossover"]
    assert sma["trades"] == 3
    assert sma["pnl"]    == 10 + (-5) + 10
    assert sma["wins"]   == 2
    assert sma["losses"] == 1


# ---------- 144: scoreboard combines backtest + live ----------

def test_scoreboard_includes_live_fields_for_strategies_with_only_backtest():
    """backtest만 있는 전략은 live_* 필드가 모두 0."""
    Session = _make_session()
    with Session() as db:
        db.add(_run("only_bt", total_pnl=100, win=5, loss=5))
        db.commit()
        sb = compute_strategy_scoreboard(db)
    row = sb[0]
    assert row["strategy"]      == "only_bt"
    assert row["total_pnl"]     == 100
    assert row["live_trades"]   == 0
    assert row["live_pnl"]      == 0
    assert row["live_wins"]     == 0
    assert row["live_losses"]   == 0
    assert row["live_win_rate"] == 0.0


def test_scoreboard_includes_strategies_with_only_live_data():
    """backtest 없이 live 거래만 있는 전략도 응답에 포함된다."""
    Session = _make_session()
    with Session() as db:
        db.add_all([
            _audit(strategy="only_live", side="BUY",  qty=1, fill_price=100),
            _audit(strategy="only_live", side="SELL", qty=1, fill_price=110),
        ])
        db.commit()
        sb = compute_strategy_scoreboard(db)
    by = {r["strategy"]: r for r in sb}
    row = by["only_live"]
    assert row["runs"]        == 0
    assert row["total_pnl"]   == 0
    assert row["live_trades"] == 1
    assert row["live_pnl"]    == 10
    assert row["live_wins"]   == 1


def test_scoreboard_combines_backtest_and_live_for_same_strategy():
    """같은 전략의 backtest + live가 한 row에 합쳐진다 — 별개 컬럼으로 surface."""
    Session = _make_session()
    with Session() as db:
        db.add(_run("dual", total_pnl=500, win=10, loss=5))
        db.add_all([
            _audit(strategy="dual", side="BUY",  qty=1, fill_price=100),
            _audit(strategy="dual", side="SELL", qty=1, fill_price=130),
        ])
        db.commit()
        sb = compute_strategy_scoreboard(db)
    row = sb[0]
    assert row["strategy"]   == "dual"
    assert row["runs"]       == 1
    assert row["total_pnl"]  == 500          # backtest
    assert row["live_trades"] == 1
    assert row["live_pnl"]    == 30          # live
    assert row["wins"]        == 10          # backtest
    assert row["live_wins"]   == 1


def test_scoreboard_sort_uses_combined_pnl():
    """정렬은 backtest_pnl + live_pnl desc — backtest만 보다가 live 결과로 순위가
    뒤집히는 케이스를 운영자가 즉시 발견."""
    Session = _make_session()
    with Session() as db:
        # A: backtest 100, live -200 → 합 -100
        db.add(_run("A", total_pnl=100))
        db.add_all([
            _audit(strategy="A", side="BUY",  qty=1, fill_price=300),
            _audit(strategy="A", side="SELL", qty=1, fill_price=100),
        ])
        # B: backtest 50, live +100 → 합 150
        db.add(_run("B", total_pnl=50))
        db.add_all([
            _audit(strategy="B", side="BUY",  qty=1, fill_price=100),
            _audit(strategy="B", side="SELL", qty=1, fill_price=200),
        ])
        db.commit()
        sb = compute_strategy_scoreboard(db)
    assert [r["strategy"] for r in sb] == ["B", "A"]


def test_scoreboard_endpoint_surface_live_fields(client):
    """/api/strategies/scoreboard 응답에 live_* 필드가 surface."""
    with client.test_db_factory() as db:
        db.add(_run("strat", total_pnl=100, win=5, loss=5))
        db.add_all([
            _audit(strategy="strat", side="BUY",  qty=1, fill_price=100),
            _audit(strategy="strat", side="SELL", qty=1, fill_price=125),
        ])
        db.commit()
    body = client.get("/api/strategies/scoreboard").json()
    assert len(body) == 1
    row = body[0]
    assert row["strategy"]    == "strat"
    assert row["total_pnl"]   == 100
    assert row["live_trades"] == 1
    assert row["live_pnl"]    == 25
    assert row["live_wins"]   == 1
