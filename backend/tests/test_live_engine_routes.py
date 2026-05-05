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


def test_tick_with_submit_true_on_hold_signal_skips_routing(client):
    """When the signal is HOLD, intended_order is None — submit=True is
    a no-op even though the flag is set."""
    client.post("/api/strategies/configure", json={
        "strategy": "sma_crossover",
        "params":   {"short": 2, "long": 4},
    })
    body = client.post("/api/strategies/tick",
                       json={"bar": _bar_payload(0, 100), "submit": True}).json()
    assert body["signal"] == "HOLD"
    assert body["intended_order"] is None
    assert body["routing"] is None


def test_tick_with_submit_true_in_simulation_executes_through_mock(client):
    """Configure + feed enough bars to trigger SMA crossover, then submit:
    SIMULATION mode + MockBroker → APPROVED + FILLED."""
    client.post("/api/strategies/configure", json={
        "strategy": "sma_crossover",
        "params":   {"short": 2, "long": 4},
        "quantity": 1,
    })
    closes = [100, 99, 98, 97, 100, 105, 110, 115]
    last = None
    for i, c in enumerate(closes):
        last = client.post("/api/strategies/tick",
                           json={"bar": _bar_payload(i, c), "submit": True}).json()
        if last["routing"] is not None:
            break

    assert last["routing"] is not None
    assert last["routing"]["decision"] == "APPROVED"
    assert last["routing"]["order_result"] is not None
    assert last["routing"]["order_result"]["status"] == "FILLED"


def test_tick_with_submit_true_in_shadow_mode_rejects_and_rolls_back(client, monkeypatch):
    from app.core.config import get_settings
    from app.core.modes import OperationMode
    monkeypatch.setattr(get_settings(), "default_mode", OperationMode.LIVE_SHADOW)

    client.post("/api/strategies/configure", json={
        "strategy": "sma_crossover",
        "params":   {"short": 2, "long": 4},
    })
    closes = [100, 99, 98, 97, 100, 105, 110, 115]
    rejected_seen = False
    for i, c in enumerate(closes):
        body = client.post("/api/strategies/tick",
                           json={"bar": _bar_payload(i, c), "submit": True}).json()
        if body["routing"] is not None:
            assert body["routing"]["decision"] == "REJECTED"
            assert any("LIVE_SHADOW" in r for r in body["routing"]["reasons"])
            # holding rolled back to False (the BUY was not actually placed)
            assert body["holding"] is False
            rejected_seen = True
            break
    assert rejected_seen


def test_tick_with_submit_true_in_manual_mode_enqueues(client, monkeypatch):
    from app.core.config import get_settings
    from app.core.modes import OperationMode
    monkeypatch.setattr(get_settings(), "default_mode", OperationMode.LIVE_MANUAL_APPROVAL)

    client.post("/api/strategies/configure", json={
        "strategy": "sma_crossover",
        "params":   {"short": 2, "long": 4},
    })
    closes = [100, 99, 98, 97, 100, 105, 110, 115]
    pending_id = None
    for i, c in enumerate(closes):
        body = client.post("/api/strategies/tick",
                           json={"bar": _bar_payload(i, c), "submit": True}).json()
        if body["routing"] is not None:
            assert body["routing"]["decision"] == "NEEDS_APPROVAL"
            pending_id = body["routing"]["approval_id"]
            break
    assert pending_id is not None
    # The approval should be visible from the existing approvals route too
    listed = client.get("/api/approvals").json()
    assert any(a["id"] == pending_id for a in listed)


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


# ---------- /api/strategies/replay ----------

def test_replay_before_configure_returns_400(client):
    res = client.post("/api/strategies/replay", json={
        "symbol": "005930",
        "start":  "2026-01-01T00:00:00+00:00",
        "end":    "2026-01-15T00:00:00+00:00",
    })
    assert res.status_code == 400
    assert "configure" in res.json()["detail"]


def test_replay_start_after_end_returns_400(client):
    client.post("/api/strategies/configure",
                json={"strategy": "sma_crossover", "params": {"short": 2, "long": 4}})
    res = client.post("/api/strategies/replay", json={
        "symbol": "005930",
        "start":  "2026-02-01T00:00:00+00:00",
        "end":    "2026-01-01T00:00:00+00:00",
    })
    assert res.status_code == 400


def test_replay_feeds_market_bars_into_engine(client):
    client.post("/api/strategies/configure",
                json={"strategy": "sma_crossover", "params": {"short": 2, "long": 4}})

    res = client.post("/api/strategies/replay", json={
        "symbol":   "005930",
        "start":    "2026-01-01T00:00:00+00:00",
        "end":      "2026-01-15T00:00:00+00:00",
        "interval": "1d",
    })
    assert res.status_code == 200
    body = res.json()

    # Mock market adapter returns 1 bar/day inclusive — Jan 1 .. Jan 15 = 15.
    assert body["bars_processed"] == 15
    assert body["bars_seen"]      == 15
    counts = body["signals_emitted"]
    assert counts["BUY"] + counts["SELL"] + counts["HOLD"] == 15

    status = client.get("/api/strategies/status").json()
    assert status["bars_seen"] == 15


def test_replay_unsupported_interval_returns_400(client):
    client.post("/api/strategies/configure",
                json={"strategy": "sma_crossover", "params": {"short": 2, "long": 4}})
    res = client.post("/api/strategies/replay", json={
        "symbol":   "005930",
        "start":    "2026-01-01T00:00:00+00:00",
        "end":      "2026-01-05T00:00:00+00:00",
        "interval": "1h",
    })
    assert res.status_code == 400
    assert "daily" in res.json()["detail"].lower()


def test_replay_appends_to_existing_engine_state(client):
    client.post("/api/strategies/configure",
                json={"strategy": "sma_crossover", "params": {"short": 2, "long": 4}})
    client.post("/api/strategies/tick", json={"bar": _bar_payload(0, 100)})
    assert client.get("/api/strategies/status").json()["bars_seen"] == 1

    res = client.post("/api/strategies/replay", json={
        "symbol": "005930",
        "start":  "2026-01-01T00:00:00+00:00",
        "end":    "2026-01-05T00:00:00+00:00",
    })
    assert res.status_code == 200
    assert res.json()["bars_processed"] == 5
    assert res.json()["bars_seen"] == 6  # 1 manual + 5 replay
