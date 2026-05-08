"""Theme routes — read-only surface + scan endpoint (#22).

CLAUDE.md 절대 원칙 — 모든 응답에서 used_for_order=False 유지.
어떤 endpoint도 BUY/SELL/HOLD 결정을 반환하지 않는다.
"""

from sqlalchemy import select

from app.db.models import ThemeSignal


def test_signals_endpoint_initially_empty(client):
    res = client.get("/api/themes/signals")
    assert res.status_code == 200
    body = res.json()
    assert body["signals"] == []
    assert body["used_for_order"] is False


def test_summary_endpoint_initially_empty(client):
    res = client.get("/api/themes/summary")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 0
    assert body["by_grade"] == {}
    assert body["top_themes"] == []
    assert body["used_for_order"] is False


def test_scan_returns_candidates_and_persists(client):
    res = client.post("/api/themes/scan", json={"limit": 10})
    assert res.status_code == 200
    body = res.json()

    # invariant
    assert body["used_for_order"] is False
    assert body["provider"] == "mock"
    assert body["is_provider_enabled"] is True

    # candidate_symbols만 반환 — order side 정보 없음.
    for c in body["candidate_symbols"]:
        assert set(c.keys()) == {"symbol", "themes", "best_score", "best_grade"}
        assert c["best_grade"] in {"STRONG", "WATCH"}

    # persisted
    with client.test_db_factory() as db:
        rows = db.execute(select(ThemeSignal)).scalars().all()
        assert len(rows) == body["persisted"] > 0
        assert all(r.used_for_order is False for r in rows)


def test_scan_with_universe_filter(client):
    res = client.post("/api/themes/scan",
                      json={"universe": ["005930"], "limit": 5})
    assert res.status_code == 200
    body = res.json()
    for c in body["candidate_symbols"]:
        assert c["symbol"] == "005930"


def test_scan_empty_universe_returns_no_candidates(client):
    res = client.post("/api/themes/scan",
                      json={"universe": [], "limit": 5})
    assert res.status_code == 200
    body = res.json()
    assert body["candidate_symbols"] == []


def test_signals_endpoint_grade_filter(client):
    client.post("/api/themes/scan", json={"limit": 10})
    res = client.get("/api/themes/signals?grade=STRONG")
    assert res.status_code == 200
    body = res.json()
    for s in body["signals"]:
        assert s["grade"] == "STRONG"
        assert s["used_for_order"] is False


def test_summary_after_scan_has_top_themes(client):
    client.post("/api/themes/scan", json={"limit": 10})
    res = client.get("/api/themes/summary")
    body = res.json()
    assert body["total"] > 0
    assert body["used_for_order"] is False


def test_scan_response_has_no_buy_sell_fields(client):
    """주문 필드(side / order_type / decision / quantity)가 응답에 없는지 확인."""
    res = client.post("/api/themes/scan", json={"limit": 5})
    body = res.json()

    # candidate_symbols
    for c in body["candidate_symbols"]:
        for forbidden in ("side", "order_type", "decision", "quantity",
                          "limit_price", "BUY", "SELL", "HOLD"):
            assert forbidden not in c, f"forbidden order field: {forbidden}"

    # records
    for r in body["records"]:
        for forbidden in ("side", "order_type", "decision", "quantity"):
            assert forbidden not in r, f"forbidden order field: {forbidden}"
