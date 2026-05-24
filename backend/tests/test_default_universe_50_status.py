"""7-02: 기본 Universe 50개 상태 테스트.

핵심 invariant:
- user_symbols 없음/빈배열 → DEFAULT_UNIVERSE_50 (50개, fallback)
- user_symbols 유효 → USER_WATCHLIST (fallback 미사용)
- 중복 제거 / invalid(6자리 아님) 제거
- user_symbols 전부 invalid → fallback (NO_VALID_SYMBOLS)
- symbols_preview 길이 제한, 후보군 0개 → NO_UNIVERSE_SYMBOLS
- API: universe_source/count/symbols_preview 존재, contains_secret=False,
  broker 호출 0건
"""

from __future__ import annotations

from app.universe import universe_status as us
from app.universe.universe_status import build_universe_status as build
from app.universe.default_universe import FALLBACK_MARKET_CAP_TOP50


def test_no_user_symbols_uses_default_50():
    s = build(user_symbols=None)
    assert s.universe_source == us.DEFAULT_UNIVERSE_50
    assert s.universe_count == 50
    assert s.fallback_used is True
    assert s.reason_code == us.NO_USER_WATCHLIST


def test_empty_list_uses_default_50():
    s = build(user_symbols=[])
    assert s.universe_source == us.DEFAULT_UNIVERSE_50
    assert s.fallback_used is True


def test_user_symbols_use_watchlist():
    s = build(user_symbols=["005930", "000660"])
    assert s.universe_source == us.USER_WATCHLIST
    assert s.universe_count == 2
    assert s.fallback_used is False
    assert s.reason_code == us.USER_WATCHLIST_OK


def test_user_symbols_no_fallback_when_present():
    s = build(user_symbols=["005930"])
    assert s.fallback_used is False
    assert s.universe_source == us.USER_WATCHLIST


def test_default_universe_is_exactly_50():
    assert len(FALLBACK_MARKET_CAP_TOP50) == 50
    s = build(user_symbols=None)
    assert s.universe_count == 50


def test_symbols_preview_limited():
    s = build(user_symbols=None, preview_limit=5)
    assert len(s.symbols_preview) == 5
    # 전체 50개를 preview 로 펼치지 않음.
    assert len(s.symbols_preview) < s.universe_count


def test_duplicates_removed():
    s = build(user_symbols=["005930", "005930", "000660"])
    assert s.universe_count == 2
    assert s.duplicate_removed_count == 1


def test_invalid_symbols_removed():
    s = build(user_symbols=["005930", "BADCODE", "12", "000660"])
    assert s.universe_source == us.USER_WATCHLIST
    assert set(s.symbols_preview) >= {"005930", "000660"}
    assert s.universe_count == 2
    assert "BADCODE" in s.invalid_symbols_removed
    assert "12" in s.invalid_symbols_removed


def test_all_invalid_falls_back():
    s = build(user_symbols=["BADCODE", "XX"])
    assert s.fallback_used is True
    assert s.universe_source == us.FALLBACK_DEFAULT_UNIVERSE_50
    assert s.reason_code == us.NO_VALID_SYMBOLS
    assert s.universe_count == 50


def test_all_invalid_no_fallback_is_empty():
    s = build(user_symbols=["BADCODE"], fallback_on_all_invalid=False)
    assert s.universe_source == us.EMPTY
    assert s.universe_count == 0
    assert s.reason_code == us.NO_UNIVERSE_SYMBOLS


def test_is_valid_kr_symbol():
    assert us.is_valid_kr_symbol("005930") is True
    assert us.is_valid_kr_symbol("00593") is False
    assert us.is_valid_kr_symbol("ABCDEF") is False
    assert us.is_valid_kr_symbol("") is False


def test_safety_invariants():
    s = build(user_symbols=None)
    assert s.is_order_signal is False
    assert s.is_investment_advice is False
    assert s.contains_secret is False
    d = s.to_dict()
    assert d["contains_secret"] is False
    assert "symbols_preview" in d and "universe_source" in d and "universe_count" in d


def test_no_broker_imports():
    src = open(us.__file__, encoding="utf-8").read()
    for forbidden in ("from app.brokers", "from app.execution", ".place_order(",
                      "route_order(", "import httpx", "import requests"):
        assert forbidden not in src


# ── API ──────────────────────────────────────────────────────────────────────


def test_api_universe_status_fields(client):
    r = client.get("/api/auto-paper/universe-status")
    assert r.status_code == 200
    body = r.json()
    for k in ("universe_source", "universe_count", "symbols_preview",
              "fallback_used", "reason_code"):
        assert k in body, f"missing {k}"
    assert body["is_live_authorization"] is False
    assert body["contains_secret"] is False


def test_api_no_user_watchlist_defaults_to_50(client):
    # 테스트 DB 에 watchlist 없음 → 기본 50 fallback.
    body = client.get("/api/auto-paper/universe-status").json()
    assert body["universe_source"] in (us.DEFAULT_UNIVERSE_50,)
    assert body["universe_count"] == 50
    assert body["fallback_used"] is True


def test_api_no_broker_order(client):
    client.get("/api/auto-paper/universe-status")
    assert len(client.test_broker.orders) == 0


def test_api_no_secret_in_response(client):
    import json as _json
    text = _json.dumps(client.get("/api/auto-paper/universe-status").json(),
                       ensure_ascii=False)
    for forbidden in ("kis_app_secret", "Bearer ", "sk-ant-", "access_token="):
        assert forbidden not in text
