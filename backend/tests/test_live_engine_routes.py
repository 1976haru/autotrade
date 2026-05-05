from datetime import datetime, timedelta, timezone

import pytest

from app.api.routes_live_engine import _reset_state


_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bar_payload(i: int, close: int, symbol: str = "005930") -> dict:
    return {
        "symbol":    symbol,
        "timestamp": (_BASE + timedelta(days=i)).isoformat(),
        "open":      close,
        "high":      close,
        "low":       close,
        "close":     close,
        "volume":    1,
    }


@pytest.fixture(autouse=True)
def _isolate_engine_state():
    """The route stores its engine in a module-level singleton, so reset
    before each test to keep them independent."""
    _reset_state()
    yield
    _reset_state()


def test_status_when_not_configured(client):
    res = client.get("/api/strategies/status")
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is False
    assert body["bars_seen"] == 0
    assert body["holding"] is False


def test_configure_with_sma_crossover_succeeds(client):
    res = client.post("/api/strategies/configure", json={
        "strategy": "sma_crossover",
        "params":   {"short": 2, "long": 4},
        "quantity": 5,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert body["strategy"] == "sma_crossover"
    assert body["quantity"] == 5
    assert body["bars_seen"] == 0


def test_configure_with_unknown_strategy_returns_400(client):
    res = client.post("/api/strategies/configure", json={"strategy": "nope"})
    assert res.status_code == 400
    assert "unknown strategy" in res.json()["detail"]


def test_configure_with_bad_params_returns_400(client):
    res = client.post("/api/strategies/configure", json={
        "strategy": "sma_crossover",
        "params":   {"short": 10, "long": 5},
    })
    assert res.status_code == 400
    assert "invalid params" in res.json()["detail"]


def test_configure_with_zero_quantity_returns_400(client):
    res = client.post("/api/strategies/configure", json={
        "strategy": "sma_crossover",
        "quantity": 0,
    })
    assert res.status_code == 400


def test_tick_before_configure_returns_400(client):
    res = client.post("/api/strategies/tick", json={"bar": _bar_payload(0, 100)})
    assert res.status_code == 400
    assert "configure" in res.json()["detail"]


def test_tick_advances_bars_seen_and_returns_signal(client):
    client.post("/api/strategies/configure", json={
        "strategy": "sma_crossover",
        "params":   {"short": 2, "long": 4},
        "quantity": 1,
    })

    closes = [100, 99, 98, 97, 100, 105, 110]
    last_response = None
    for i, c in enumerate(closes):
        res = client.post("/api/strategies/tick", json={"bar": _bar_payload(i, c)})
        assert res.status_code == 200
        last_response = res.json()
        assert last_response["bars_seen"] == i + 1

    # By the time the SMA cross fires, the engine should be holding a position
    # and have emitted at least one BUY intended_order along the way.
    status = client.get("/api/strategies/status").json()
    assert status["bars_seen"] == len(closes)
    # The last tick after the crossover may or may not have been HOLD; just
    # confirm the response shape is consistent.
    assert last_response["signal"] in ("BUY", "SELL", "HOLD")


def test_tick_with_submit_true_returns_501(client):
    client.post("/api/strategies/configure", json={
        "strategy": "sma_crossover",
        "params":   {"short": 2, "long": 4},
    })
    res = client.post("/api/strategies/tick",
                      json={"bar": _bar_payload(0, 100), "submit": True})
    assert res.status_code == 501
    assert "follow-up" in res.json()["detail"]


def test_reset_clears_state(client):
    client.post("/api/strategies/configure", json={
        "strategy": "sma_crossover",
        "params":   {"short": 2, "long": 4},
    })
    client.post("/api/strategies/tick", json={"bar": _bar_payload(0, 100)})
    assert client.get("/api/strategies/status").json()["bars_seen"] == 1

    res = client.post("/api/strategies/reset")
    assert res.status_code == 200
    assert res.json()["configured"] is False

    # tick after reset should now be 400 again
    res = client.post("/api/strategies/tick", json={"bar": _bar_payload(0, 100)})
    assert res.status_code == 400


def test_buy_signal_returns_intended_order_with_market_buy(client):
    client.post("/api/strategies/configure", json={
        "strategy": "sma_crossover",
        "params":   {"short": 2, "long": 4},
        "quantity": 3,
    })
    closes = [100, 99, 98, 97, 100, 105, 110, 115]
    found_buy = False
    for i, c in enumerate(closes):
        body = client.post("/api/strategies/tick", json={"bar": _bar_payload(i, c)}).json()
        if body["signal"] == "BUY":
            assert body["intended_order"] is not None
            assert body["intended_order"]["side"] == "BUY"
            assert body["intended_order"]["quantity"] == 3
            assert body["intended_order"]["order_type"] == "MARKET"
            found_buy = True
            break
    assert found_buy, "expected an SMA-crossover BUY signal in the test bars"
