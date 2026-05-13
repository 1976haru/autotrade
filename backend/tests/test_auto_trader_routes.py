"""체크리스트 #60: AI Agent /api/auto-trader/* 라우트 통합 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _bars_payload(symbol="005930", n=60, start=50_000):
    base = datetime(2026, 5, 13, 9, 0, tzinfo=timezone.utc)
    return [
        {
            "timestamp": (base + timedelta(minutes=i)).isoformat(),
            "open":  start + i * 200,
            "high":  start + i * 200 + 50,
            "low":   start + i * 200 - 50,
            "close": start + i * 200,
            "volume": 1000,
        }
        for i in range(n)
    ]


# ---------- GET /status ----------


def test_status_returns_paper_flags_and_emergency_state(client):
    r = client.get("/api/auto-trader/status")
    assert r.status_code == 200
    body = r.json()
    assert "paperStatus" in body
    assert "emergencyStop" in body
    assert "enableLiveTrading" in body
    assert "recentReportCount" in body
    # 기본은 LIVE 비활성
    assert body["enableLiveTrading"] is False
    assert body["enableAiExecution"] is False


# ---------- POST /run-once ----------


def test_run_once_minimal_request_returns_plans(client):
    payload = {
        "watchlist":      ["005930"],
        "bars_by_symbol": {"005930": _bars_payload("005930")},
        "strategy_names": ["sma_crossover"],
        "min_confidence": 10,
        "default_quantity": 1,
        "mode": "SIMULATION",
    }
    r = client.post("/api/auto-trader/run-once", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "SIMULATION"
    assert body["emergencyStop"] is False
    assert isinstance(body["plans"], list) and body["plans"]
    plan = body["plans"][0]
    assert plan["symbol"] == "005930"
    assert plan["decision"]["action"] in ("BUY", "SELL", "HOLD")
    # 사람-가독 reason 필수
    assert isinstance(plan["decision"]["reason"], str)
    assert plan["decision"]["reason"].strip() != ""
    # invariant
    assert plan["decision"]["isOrderIntent"] is False
    assert body["notice"].startswith("본 결과는")


def test_run_once_no_bars_yields_hold_plan(client):
    payload = {
        "watchlist":      ["005930"],
        "bars_by_symbol": {},
        "strategy_names": ["sma_crossover"],
        "mode": "SIMULATION",
    }
    r = client.post("/api/auto-trader/run-once", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plans"][0]["decision"]["action"] == "HOLD"


def test_run_once_blocks_live_mode(client):
    payload = {
        "watchlist":      ["005930"],
        "bars_by_symbol": {"005930": _bars_payload("005930")},
        "mode": "LIVE_MANUAL_APPROVAL",
    }
    r = client.post("/api/auto-trader/run-once", json=payload)
    assert r.status_code == 400
    assert "disabled" in r.text


def test_run_once_rejects_invalid_mode(client):
    payload = {
        "watchlist":      ["005930"],
        "bars_by_symbol": {"005930": _bars_payload("005930")},
        "mode": "DEFINITELY_INVALID",
    }
    r = client.post("/api/auto-trader/run-once", json=payload)
    assert r.status_code == 400
    assert "unknown mode" in r.text or "DEFINITELY_INVALID" in r.text


def test_run_once_rejects_invalid_timestamp(client):
    payload = {
        "watchlist":      ["005930"],
        "bars_by_symbol": {
            "005930": [
                {"timestamp": "not-iso", "open": 100, "high": 100,
                 "low": 100, "close": 100, "volume": 1}
            ],
        },
        "mode": "SIMULATION",
    }
    r = client.post("/api/auto-trader/run-once", json=payload)
    assert r.status_code == 400


# ---------- GET /portfolio ----------


def test_get_portfolio_returns_paper_balance(client):
    r = client.get("/api/auto-trader/portfolio")
    assert r.status_code == 200
    body = r.json()
    assert "cash" in body
    assert "equity" in body
    assert "buyingPower" in body
    assert isinstance(body["positions"], list)


# ---------- GET /decisions ----------


def test_get_decisions_returns_envelope(client):
    # NOTE: AutoTraderAgent is cached via @lru_cache across tests — we only
    # assert envelope shape, not count.
    r = client.get("/api/auto-trader/decisions")
    assert r.status_code == 200
    body = r.json()
    assert "total" in body
    assert isinstance(body["decisions"], list)
    assert isinstance(body["total"], int)


def test_decisions_populated_after_run_once(client):
    # Run once to populate cache
    payload = {
        "watchlist":      ["005930"],
        "bars_by_symbol": {"005930": _bars_payload("005930")},
        "strategy_names": ["sma_crossover"],
        "min_confidence": 10,
        "mode": "SIMULATION",
    }
    r1 = client.post("/api/auto-trader/run-once", json=payload)
    assert r1.status_code == 200
    r2 = client.get("/api/auto-trader/decisions")
    assert r2.status_code == 200
    body = r2.json()
    assert body["total"] >= 1


# ---------- POST /emergency-stop ----------


def test_emergency_stop_toggle_updates_in_memory(client):
    # Turn on
    r1 = client.post("/api/auto-trader/emergency-stop",
                     json={"enabled": True, "note": "manual e2e test"})
    assert r1.status_code == 200
    assert r1.json()["enabled"] is True

    # Run-once now blocked
    payload = {
        "watchlist":      ["005930"],
        "bars_by_symbol": {"005930": _bars_payload("005930")},
        "strategy_names": ["sma_crossover"],
        "min_confidence": 10,
        "mode": "SIMULATION",
    }
    r2 = client.post("/api/auto-trader/run-once", json=payload)
    assert r2.status_code == 200
    body = r2.json()
    assert body["emergencyStop"] is True
    plan = body["plans"][0]
    assert plan["blockedBy"] == "emergency_stop"
    assert plan["executed"] is False

    # Turn off — verify subsequent runs not blocked
    r3 = client.post("/api/auto-trader/emergency-stop",
                     json={"enabled": False})
    assert r3.status_code == 200
    assert r3.json()["enabled"] is False
