from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import BacktestRun


_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bar_payload(i: int, close: int, symbol: str = "X") -> dict:
    return {
        "symbol":    symbol,
        "timestamp": (_BASE + timedelta(days=i)).isoformat(),
        "open":      close,
        "high":      close,
        "low":       close,
        "close":     close,
        "volume":    1,
    }


def test_run_persists_and_returns_metrics(client):
    closes = [100, 99, 98, 97, 100, 105, 110, 108, 112, 115]
    body = {
        "strategy": "sma_crossover",
        "params":   {"short": 2, "long": 4},
        "initial_cash": 1_000_000,
        "quantity": 10,
        "bars": [_bar_payload(i, c) for i, c in enumerate(closes)],
    }
    res = client.post("/api/backtest/run", json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["run_id"] >= 1
    assert data["strategy"] == "sma_crossover"
    assert data["bars_processed"] == len(closes)
    assert data["initial_cash"] == 1_000_000
    assert isinstance(data["trades"], list)
    assert data["win_count"] + data["loss_count"] == len(data["trades"])
    assert data["total_pnl"] == data["final_cash"] - data["initial_cash"]

    with client.test_db_factory() as db:
        runs = db.execute(select(BacktestRun)).scalars().all()
        assert len(runs) == 1
        stored = runs[0]
        assert stored.strategy == "sma_crossover"
        assert stored.params == {"short": 2, "long": 4}
        assert stored.bars_processed == len(closes)
        assert len(stored.trades_json) == len(data["trades"])
        for stored_t, resp_t in zip(stored.trades_json, data["trades"]):
            assert stored_t["symbol"]      == resp_t["symbol"]
            assert stored_t["entry_price"] == resp_t["entry_price"]
            assert stored_t["exit_price"]  == resp_t["exit_price"]
            assert stored_t["quantity"]    == resp_t["quantity"]
            assert stored_t["pnl"]         == resp_t["pnl"]


def test_run_then_get_returns_same_payload(client):
    body = {
        "strategy": "sma_crossover",
        "params":   {"short": 2, "long": 4},
        "bars": [_bar_payload(i, c) for i, c in enumerate([100, 99, 98, 97, 100, 105, 110])],
    }
    posted = client.post("/api/backtest/run", json=body).json()
    res = client.get(f"/api/backtest/runs/{posted['run_id']}")
    assert res.status_code == 200
    fetched = res.json()
    assert fetched["run_id"] == posted["run_id"]
    assert fetched["trades"] == posted["trades"]
    assert fetched["total_pnl"] == posted["total_pnl"]
    assert fetched["win_rate"] == posted["win_rate"]


def test_get_unknown_run_returns_404(client):
    res = client.get("/api/backtest/runs/9999")
    assert res.status_code == 404


def test_unknown_strategy_returns_400(client):
    body = {"strategy": "unknown_thing", "bars": [_bar_payload(0, 100)]}
    res = client.post("/api/backtest/run", json=body)
    assert res.status_code == 400
    assert "unknown strategy" in res.json()["detail"]


def test_invalid_strategy_params_returns_400(client):
    body = {
        "strategy": "sma_crossover",
        "params":   {"short": 10, "long": 5},
        "bars":     [_bar_payload(0, 100)],
    }
    res = client.post("/api/backtest/run", json=body)
    assert res.status_code == 400
    assert "invalid params" in res.json()["detail"]


def test_empty_bars_returns_400(client):
    body = {"strategy": "sma_crossover", "bars": []}
    # FastAPI/pydantic accept empty list; route handler enforces non-empty
    res = client.post("/api/backtest/run", json=body)
    assert res.status_code == 400
