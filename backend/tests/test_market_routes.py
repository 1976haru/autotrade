from sqlalchemy import select

from app.db.models import MarketBar


def test_first_request_hits_upstream_and_populates_cache(client):
    res = client.get(
        "/api/market/bars",
        params={
            "symbol":   "005930",
            "start":    "2026-01-01T00:00:00+00:00",
            "end":      "2026-01-05T00:00:00+00:00",
            "interval": "1d",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "upstream"
    assert body["count"] == 5
    assert body["interval"] == "1d"
    assert body["bars"][0]["symbol"] == "005930"

    with client.test_db_factory() as db:
        rows = db.execute(select(MarketBar)).scalars().all()
        assert len(rows) == 5
        assert {r.symbol for r in rows} == {"005930"}


def test_second_request_returns_from_cache(client):
    params = {
        "symbol":   "005930",
        "start":    "2026-01-01T00:00:00+00:00",
        "end":      "2026-01-05T00:00:00+00:00",
        "interval": "1d",
    }
    first = client.get("/api/market/bars", params=params).json()
    assert first["source"] == "upstream"
    second = client.get("/api/market/bars", params=params).json()
    assert second["source"] == "cache"
    assert second["count"] == first["count"]
    assert [b["close"] for b in second["bars"]] == [b["close"] for b in first["bars"]]


def test_start_after_end_returns_400(client):
    res = client.get(
        "/api/market/bars",
        params={
            "symbol": "005930",
            "start":  "2026-01-10T00:00:00+00:00",
            "end":    "2026-01-01T00:00:00+00:00",
        },
    )
    assert res.status_code == 400


def test_invalid_interval_returns_422(client):
    res = client.get(
        "/api/market/bars",
        params={
            "symbol":   "005930",
            "start":    "2026-01-01T00:00:00+00:00",
            "end":      "2026-01-05T00:00:00+00:00",
            "interval": "bogus",
        },
    )
    assert res.status_code == 422


def test_adapter_unsupported_interval_returns_400(client):
    res = client.get(
        "/api/market/bars",
        params={
            "symbol":   "005930",
            "start":    "2026-01-01T00:00:00+00:00",
            "end":      "2026-01-05T00:00:00+00:00",
            "interval": "1h",
        },
    )
    assert res.status_code == 400
    assert "daily interval" in res.json()["detail"]
