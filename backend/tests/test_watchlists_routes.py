"""Watchlist CRUD + CSV import 테스트 (#18).

검증:
- create / list / get / patch / delete
- item add / remove
- symbol 정규화 (trim + uppercase)
- 200개 한도
- 중복 거부 (한국어 메시지)
- CSV import (added / skipped / invalid + summary)
- summary endpoint (active + top 5)
- watchlist는 broker / RiskManager 분기에 영향 없음 (간접 — 라우터 분리)
"""

from sqlalchemy import select

from app.db.models import Watchlist, WatchlistItem


# ---------- CRUD ----------


def test_list_watchlists_empty(client):
    res = client.get("/api/watchlists")
    assert res.status_code == 200
    body = res.json()
    assert body["watchlists"] == []
    assert body["max_items"] == 200
    assert body["recommended_items"] == 50


def test_create_watchlist_minimal(client):
    res = client.post("/api/watchlists", json={"name": "코어 단타"})
    assert res.status_code == 201
    body = res.json()
    assert body["name"] == "코어 단타"
    assert body["is_active"] is False
    assert body["item_count"] == 0
    assert body["items"] == []


def test_create_watchlist_strips_whitespace_in_name(client):
    res = client.post("/api/watchlists", json={"name": "  단타  "})
    assert res.status_code == 201
    assert res.json()["name"] == "단타"


def test_create_watchlist_rejects_empty_name(client):
    res = client.post("/api/watchlists", json={"name": "   "})
    assert res.status_code == 400
    assert "이름을 입력" in res.json()["detail"]


def test_create_watchlist_with_active_flag_deactivates_others(client):
    a = client.post("/api/watchlists",
                    json={"name": "A", "is_active": True}).json()
    b = client.post("/api/watchlists",
                    json={"name": "B", "is_active": True}).json()

    listing = client.get("/api/watchlists").json()["watchlists"]
    by_id = {w["id"]: w for w in listing}
    assert by_id[a["id"]]["is_active"] is False
    assert by_id[b["id"]]["is_active"] is True


def test_get_one_returns_detail_with_items(client):
    w = client.post("/api/watchlists", json={"name": "단타"}).json()
    client.post(f"/api/watchlists/{w['id']}/items", json={"symbol": "005930"})
    client.post(f"/api/watchlists/{w['id']}/items", json={"symbol": "000660"})

    res = client.get(f"/api/watchlists/{w['id']}")
    assert res.status_code == 200
    detail = res.json()
    assert detail["item_count"] == 2
    assert {it["symbol"] for it in detail["items"]} == {"005930", "000660"}


def test_get_one_404_for_missing(client):
    res = client.get("/api/watchlists/99999")
    assert res.status_code == 404


def test_patch_renames_watchlist(client):
    w = client.post("/api/watchlists", json={"name": "old"}).json()
    res = client.patch(f"/api/watchlists/{w['id']}", json={"name": "new"})
    assert res.status_code == 200
    assert res.json()["name"] == "new"


def test_patch_activates_watchlist(client):
    a = client.post("/api/watchlists", json={"name": "A", "is_active": True}).json()
    b = client.post("/api/watchlists", json={"name": "B"}).json()

    res = client.patch(f"/api/watchlists/{b['id']}", json={"is_active": True})
    assert res.status_code == 200
    assert res.json()["is_active"] is True

    detail = client.get(f"/api/watchlists/{a['id']}").json()
    assert detail["is_active"] is False


def test_delete_cascades_items(client):
    w = client.post("/api/watchlists", json={"name": "to-delete"}).json()
    client.post(f"/api/watchlists/{w['id']}/items", json={"symbol": "005930"})

    res = client.delete(f"/api/watchlists/{w['id']}")
    assert res.status_code == 204

    with client.test_db_factory() as db:
        assert db.execute(select(Watchlist)).scalars().all() == []
        assert db.execute(select(WatchlistItem)).scalars().all() == []


# ---------- Items + normalization ----------


def test_add_item_normalizes_symbol_to_uppercase_trimmed(client):
    w = client.post("/api/watchlists", json={"name": "x"}).json()
    res = client.post(f"/api/watchlists/{w['id']}/items",
                      json={"symbol": "  aapl  "})
    assert res.status_code == 201
    assert res.json()["symbol"] == "AAPL"


def test_add_item_rejects_empty_symbol(client):
    w = client.post("/api/watchlists", json={"name": "x"}).json()
    res = client.post(f"/api/watchlists/{w['id']}/items", json={"symbol": "   "})
    assert res.status_code == 400
    assert "종목코드를 입력" in res.json()["detail"]


def test_add_item_rejects_too_long_symbol(client):
    w = client.post("/api/watchlists", json={"name": "x"}).json()
    too_long = "A" * 17
    res = client.post(f"/api/watchlists/{w['id']}/items", json={"symbol": too_long})
    assert res.status_code == 400
    assert "너무 깁니다" in res.json()["detail"]


def test_add_item_rejects_duplicate_symbol(client):
    w = client.post("/api/watchlists", json={"name": "x"}).json()
    client.post(f"/api/watchlists/{w['id']}/items", json={"symbol": "005930"})
    res = client.post(f"/api/watchlists/{w['id']}/items", json={"symbol": "005930"})
    assert res.status_code == 400
    assert "이미 등록" in res.json()["detail"]


def test_add_item_404_for_missing_watchlist(client):
    res = client.post("/api/watchlists/99999/items", json={"symbol": "005930"})
    assert res.status_code == 404


def test_add_item_blocks_at_200_cap(client):
    w = client.post("/api/watchlists", json={"name": "big"}).json()
    # 200개 채우기. 운영 환경에선 비현실적 — 테스트 시간 절약 위해 직접 DB 삽입.
    with client.test_db_factory() as db:
        for i in range(200):
            db.add(WatchlistItem(watchlist_id=w["id"], symbol=f"S{i:05d}"))
        db.commit()

    res = client.post(f"/api/watchlists/{w['id']}/items", json={"symbol": "EXTRA"})
    assert res.status_code == 400
    assert "최대 200개" in res.json()["detail"]


def test_remove_item(client):
    w = client.post("/api/watchlists", json={"name": "x"}).json()
    item = client.post(f"/api/watchlists/{w['id']}/items",
                       json={"symbol": "005930"}).json()
    res = client.delete(f"/api/watchlists/{w['id']}/items/{item['id']}")
    assert res.status_code == 204

    detail = client.get(f"/api/watchlists/{w['id']}").json()
    assert detail["items"] == []


def test_remove_item_404_when_id_belongs_to_other_watchlist(client):
    a = client.post("/api/watchlists", json={"name": "A"}).json()
    b = client.post("/api/watchlists", json={"name": "B"}).json()
    item_a = client.post(f"/api/watchlists/{a['id']}/items",
                         json={"symbol": "005930"}).json()

    res = client.delete(f"/api/watchlists/{b['id']}/items/{item_a['id']}")
    assert res.status_code == 404


# ---------- CSV import ----------


def _csv_payload(text: str) -> dict:
    return {"csv": text}


def test_csv_import_basic_added(client):
    w = client.post("/api/watchlists", json={"name": "x"}).json()
    csv_body = "symbol,name\n005930,삼성전자\n000660,SK하이닉스\n"
    res = client.post(f"/api/watchlists/{w['id']}/import-csv",
                      json=_csv_payload(csv_body))
    assert res.status_code == 200
    body = res.json()
    assert body["added"] == 2
    assert body["skipped"] == 0
    assert body["invalid"] == 0
    assert body["total_after_import"] == 2

    detail = client.get(f"/api/watchlists/{w['id']}").json()
    by_symbol = {it["symbol"]: it for it in detail["items"]}
    assert by_symbol["005930"]["name"] == "삼성전자"


def test_csv_import_skips_duplicates(client):
    w = client.post("/api/watchlists", json={"name": "x"}).json()
    client.post(f"/api/watchlists/{w['id']}/items", json={"symbol": "005930"})

    csv_body = "symbol\n005930\n000660\n"
    res = client.post(f"/api/watchlists/{w['id']}/import-csv",
                      json=_csv_payload(csv_body))
    body = res.json()
    assert body["added"] == 1
    assert body["skipped"] == 1
    assert body["invalid"] == 0


def test_csv_import_invalid_rows(client):
    """too-long symbol + whitespace-only는 invalid. 진짜 빈 줄은 csv 표준에
    의해 자동 skip되므로 invalid에 들어가지 않는다 (errors에도 없음)."""
    w = client.post("/api/watchlists", json={"name": "x"}).json()
    too_long = "A" * 17
    csv_body = f"symbol\n005930\n{too_long}\n   \n"
    res = client.post(f"/api/watchlists/{w['id']}/import-csv",
                      json=_csv_payload(csv_body))
    body = res.json()
    assert body["added"] == 1
    assert body["invalid"] == 2
    assert len(body["errors"]) == 2


def test_csv_import_normalizes_symbol_case(client):
    w = client.post("/api/watchlists", json={"name": "x"}).json()
    csv_body = "symbol\n  aapl  \nMsft\n"
    res = client.post(f"/api/watchlists/{w['id']}/import-csv",
                      json=_csv_payload(csv_body))
    assert res.status_code == 200
    detail = client.get(f"/api/watchlists/{w['id']}").json()
    assert {it["symbol"] for it in detail["items"]} == {"AAPL", "MSFT"}


def test_csv_import_requires_symbol_column(client):
    w = client.post("/api/watchlists", json={"name": "x"}).json()
    csv_body = "name,market\nfoo,KRX\n"
    res = client.post(f"/api/watchlists/{w['id']}/import-csv",
                      json=_csv_payload(csv_body))
    assert res.status_code == 400
    assert "symbol" in res.json()["detail"]


def test_csv_import_rejects_empty_body(client):
    w = client.post("/api/watchlists", json={"name": "x"}).json()
    res = client.post(f"/api/watchlists/{w['id']}/import-csv",
                      json=_csv_payload("   "))
    assert res.status_code == 400


def test_csv_import_full_columns(client):
    w = client.post("/api/watchlists", json={"name": "x"}).json()
    csv_body = (
        "symbol,name,market,sector,note\n"
        "005930,삼성전자,KOSPI,반도체,코어\n"
    )
    res = client.post(f"/api/watchlists/{w['id']}/import-csv",
                      json=_csv_payload(csv_body))
    assert res.status_code == 200
    detail = client.get(f"/api/watchlists/{w['id']}").json()
    item = detail["items"][0]
    assert item["symbol"] == "005930"
    assert item["name"]   == "삼성전자"
    assert item["market"] == "KOSPI"
    assert item["sector"] == "반도체"
    assert item["note"]   == "코어"


def test_csv_import_blocks_at_200_cap(client):
    w = client.post("/api/watchlists", json={"name": "big"}).json()
    # 199개 미리 채우기 — CSV가 1개만 추가하고 나머지는 invalid 처리되도록.
    with client.test_db_factory() as db:
        for i in range(199):
            db.add(WatchlistItem(watchlist_id=w["id"], symbol=f"S{i:05d}"))
        db.commit()

    csv_body = "symbol\nNEW1\nNEW2\nNEW3\n"  # 3개 추가 시도, 1개만 통과
    res = client.post(f"/api/watchlists/{w['id']}/import-csv",
                      json=_csv_payload(csv_body))
    body = res.json()
    assert body["added"] == 1
    assert body["invalid"] >= 1
    assert body["total_after_import"] == 200


def test_csv_import_via_text_body(client):
    """JSON wrapper 없이 raw text/csv body도 허용한다."""
    w = client.post("/api/watchlists", json={"name": "x"}).json()
    res = client.post(
        f"/api/watchlists/{w['id']}/import-csv",
        content="symbol\n005930\n",
        headers={"Content-Type": "text/csv"},
    )
    assert res.status_code == 200
    assert res.json()["added"] == 1


# ---------- Summary ----------


def test_summary_no_active(client):
    res = client.get("/api/watchlists/summary")
    assert res.status_code == 200
    body = res.json()
    assert body["active"] is None
    assert body["active_item_count"] == 0
    assert body["top_symbols"] == []
    assert body["watchlist_count"] == 0
    assert body["max_items"] == 200
    assert body["recommended_items"] == 50


def test_summary_returns_active_top_5(client):
    w = client.post("/api/watchlists",
                    json={"name": "core", "is_active": True}).json()
    for sym in ["005930", "000660", "035720", "035420", "207940", "068270"]:
        client.post(f"/api/watchlists/{w['id']}/items", json={"symbol": sym})

    body = client.get("/api/watchlists/summary").json()
    assert body["active"]["name"] == "core"
    assert body["active_item_count"] == 6
    assert len(body["top_symbols"]) == 5  # top 5만
    assert body["watchlist_count"] == 1
