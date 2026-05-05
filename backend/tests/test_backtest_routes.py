from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import BacktestRun, MarketBar


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


# ---------- bar validation (bars mode only) ----------

def _full_bar(i: int, *, symbol="X", o=100, h=110, low=95, c=105, v=1000) -> dict:
    return {
        "symbol":    symbol,
        "timestamp": (_BASE + timedelta(days=i)).isoformat(),
        "open": o, "high": h, "low": low, "close": c, "volume": v,
    }


def test_bars_with_mixed_symbols_returns_400(client):
    body = {
        "strategy": "sma_crossover",
        "bars": [_full_bar(0, symbol="A"), _full_bar(1, symbol="B")],
    }
    res = client.post("/api/backtest/run", json=body)
    assert res.status_code == 400
    assert "share one symbol" in res.json()["detail"]


def test_bars_with_non_increasing_timestamps_returns_400(client):
    body = {
        "strategy": "sma_crossover",
        "bars": [_full_bar(0), _full_bar(0)],  # duplicate timestamp
    }
    res = client.post("/api/backtest/run", json=body)
    assert res.status_code == 400
    assert "ascending" in res.json()["detail"]


def test_bars_with_descending_timestamps_returns_400(client):
    body = {
        "strategy": "sma_crossover",
        "bars": [_full_bar(1), _full_bar(0)],
    }
    res = client.post("/api/backtest/run", json=body)
    assert res.status_code == 400


def test_bars_with_high_below_low_returns_400(client):
    body = {
        "strategy": "sma_crossover",
        "bars": [_full_bar(0, h=80, low=120)],
    }
    res = client.post("/api/backtest/run", json=body)
    assert res.status_code == 400
    assert "high" in res.json()["detail"] and "low" in res.json()["detail"]


def test_bars_with_open_outside_high_low_returns_400(client):
    body = {
        "strategy": "sma_crossover",
        "bars": [_full_bar(0, o=200, h=110, low=95, c=105)],
    }
    res = client.post("/api/backtest/run", json=body)
    assert res.status_code == 400
    assert "open" in res.json()["detail"]


def test_bars_with_close_outside_high_low_returns_400(client):
    body = {
        "strategy": "sma_crossover",
        "bars": [_full_bar(0, o=100, h=110, low=95, c=200)],
    }
    res = client.post("/api/backtest/run", json=body)
    assert res.status_code == 400
    assert "close" in res.json()["detail"]


def test_bars_with_zero_price_returns_400(client):
    body = {
        "strategy": "sma_crossover",
        "bars": [_full_bar(0, o=0, h=10, low=0, c=5)],
    }
    res = client.post("/api/backtest/run", json=body)
    assert res.status_code == 400
    assert "positive" in res.json()["detail"]


def test_bars_with_negative_volume_returns_400(client):
    body = {
        "strategy": "sma_crossover",
        "bars": [_full_bar(0, v=-1)],
    }
    res = client.post("/api/backtest/run", json=body)
    assert res.status_code == 400
    assert "volume" in res.json()["detail"]


def test_well_formed_bars_pass_validation(client):
    """Sanity check that the validator does not reject reasonable bars."""
    body = {
        "strategy": "sma_crossover",
        "params":   {"short": 2, "long": 4},
        "bars": [_full_bar(i, o=100+i, h=110+i, low=95+i, c=105+i, v=1000)
                 for i in range(5)],
    }
    res = client.post("/api/backtest/run", json=body)
    assert res.status_code == 200


def test_run_with_bars_records_data_source_bars(client):
    body = {
        "strategy": "sma_crossover",
        "params":   {"short": 2, "long": 4},
        "bars": [_bar_payload(i, c) for i, c in enumerate([100, 99, 98, 97, 100, 105, 110])],
    }
    data = client.post("/api/backtest/run", json=body).json()
    assert data["data_source"] == "bars"
    assert data["data_symbol"] is None
    assert data["data_start"] is None
    assert data["data_end"] is None
    assert data["data_interval"] is None


def test_run_with_market_range_fetches_and_persists_metadata(client):
    body = {
        "strategy": "sma_crossover",
        "params":   {"short": 2, "long": 4},
        "symbol":   "005930",
        "start":    "2026-01-01T00:00:00+00:00",
        "end":      "2026-01-15T00:00:00+00:00",
        "interval": "1d",
    }
    res = client.post("/api/backtest/run", json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["data_source"] == "market"
    assert data["data_symbol"] == "005930"
    assert data["data_interval"] == "1d"
    assert data["bars_processed"] == 15

    with client.test_db_factory() as db:
        bars_in_cache = db.execute(select(MarketBar)).scalars().all()
        assert len(bars_in_cache) == 15
        assert all(b.symbol == "005930" for b in bars_in_cache)


def test_run_with_market_range_uses_cache_on_repeat(client):
    body = {
        "strategy": "sma_crossover",
        "params":   {"short": 2, "long": 4},
        "symbol":   "005930",
        "start":    "2026-01-01T00:00:00+00:00",
        "end":      "2026-01-10T00:00:00+00:00",
    }
    first  = client.post("/api/backtest/run", json=body).json()
    second = client.post("/api/backtest/run", json=body).json()
    assert first["trades"] == second["trades"]

    with client.test_db_factory() as db:
        cached = db.execute(select(MarketBar)).scalars().all()
        assert len(cached) == 10  # not 20 — second call hit cache, no duplicates


def test_run_must_provide_bars_or_market_range(client):
    res = client.post("/api/backtest/run", json={"strategy": "sma_crossover"})
    assert res.status_code == 400
    assert "must provide" in res.json()["detail"]


def test_run_cannot_provide_both_bars_and_market_range(client):
    body = {
        "strategy": "sma_crossover",
        "bars":     [_bar_payload(0, 100)],
        "symbol":   "005930",
        "start":    "2026-01-01T00:00:00+00:00",
        "end":      "2026-01-05T00:00:00+00:00",
    }
    res = client.post("/api/backtest/run", json=body)
    assert res.status_code == 400
    assert "not both" in res.json()["detail"]


def test_run_with_market_unsupported_interval_returns_400(client):
    body = {
        "strategy": "sma_crossover",
        "params":   {"short": 2, "long": 4},
        "symbol":   "005930",
        "start":    "2026-01-01T00:00:00+00:00",
        "end":      "2026-01-05T00:00:00+00:00",
        "interval": "1h",
    }
    res = client.post("/api/backtest/run", json=body)
    assert res.status_code == 400
    assert "daily" in res.json()["detail"].lower()


def test_run_with_market_start_after_end_returns_400(client):
    body = {
        "strategy": "sma_crossover",
        "symbol":   "005930",
        "start":    "2026-02-01T00:00:00+00:00",
        "end":      "2026-01-01T00:00:00+00:00",
    }
    res = client.post("/api/backtest/run", json=body)
    assert res.status_code == 400


def test_get_run_returns_market_metadata(client):
    body = {
        "strategy": "sma_crossover",
        "params":   {"short": 2, "long": 4},
        "symbol":   "005930",
        "start":    "2026-01-01T00:00:00+00:00",
        "end":      "2026-01-10T00:00:00+00:00",
    }
    posted = client.post("/api/backtest/run", json=body).json()
    fetched = client.get(f"/api/backtest/runs/{posted['run_id']}").json()
    assert fetched["data_source"] == "market"
    assert fetched["data_symbol"] == "005930"
    assert fetched["data_interval"] == "1d"
    assert fetched["data_start"].startswith("2026-01-01")
