"""테마 브리핑 2단계 — 전일 미국 테마 ETF/지수 등락률(정보 표시 전용).

정보 표시만 검증한다: 추천 문구 없음, 자동 토글 없음, 매핑 없음/조회 실패 graceful.
"""
from __future__ import annotations

import asyncio

import app.performance.theme_briefing as tb


def _run(coro):
    return asyncio.run(coro)


def setup_function():
    tb.reset_theme_briefing_cache_for_tests()


def test_all_20_themes_present_in_catalog_order():
    async def fetch(tickers):
        return {}

    out = _run(tb.get_theme_briefing(now_ts=1000.0, fetcher=fetch))
    ids = [t["theme_id"] for t in out["themes"]]
    assert ids == [
        "semiconductor", "bio", "automobile", "finance", "secondary_battery",
        "internet", "defense", "shipbuilding", "power_nuclear", "entertainment_media",
        "cosmetics", "food_beverage", "steel_materials", "construction_infra",
        "telecom_network", "gaming", "robot_ai", "retail_consumer",
        "energy_chemical", "transport_logistics", "other",
    ]


def test_direct_mapping_ok_returns_change_pct_and_session_date():
    async def fetch(tickers):
        assert "^SOX" in tickers
        return {"^SOX": {"change_pct": -3.2, "session_date": "2026-07-03"}}

    out = _run(tb.get_theme_briefing(now_ts=1000.0, fetcher=fetch))
    by = {t["theme_id"]: t for t in out["themes"]}
    sox = by["semiconductor"]
    assert sox["mapping_quality"] == "DIRECT"
    assert sox["status"] == "OK"
    assert sox["proxies"] == [{"ticker": "^SOX", "change_pct": -3.2, "status": "OK"}]
    assert out["session_date_us"] == "2026-07-03"


def test_no_mapping_themes_marked_none_with_empty_proxies():
    async def fetch(tickers):
        return {}

    out = _run(tb.get_theme_briefing(now_ts=1000.0, fetcher=fetch))
    by = {t["theme_id"]: t for t in out["themes"]}
    for theme_id in ("shipbuilding", "cosmetics", "other"):
        assert by[theme_id]["mapping_quality"] == "NONE"
        assert by[theme_id]["proxies"] == []
        assert by[theme_id]["status"] == "NO_MAPPING"


def test_partial_mapping_shows_each_proxy_independently_no_composite():
    async def fetch(tickers):
        return {
            "URA": {"change_pct": 1.1, "session_date": "2026-07-03"},
            "XLU": {"change_pct": -0.4, "session_date": "2026-07-03"},
        }

    out = _run(tb.get_theme_briefing(now_ts=1000.0, fetcher=fetch))
    by = {t["theme_id"]: t for t in out["themes"]}
    power = by["power_nuclear"]
    assert power["mapping_quality"] == "PARTIAL"
    tickers = {p["ticker"]: p["change_pct"] for p in power["proxies"]}
    assert tickers == {"URA": 1.1, "XLU": -0.4}


def test_fetch_failure_per_ticker_graceful_not_all_or_nothing():
    async def fetch(tickers):
        return {"^SOX": {"change_pct": 0.5, "session_date": "2026-07-03"}}  # 나머지는 누락

    out = _run(tb.get_theme_briefing(now_ts=1000.0, fetcher=fetch))
    by = {t["theme_id"]: t for t in out["themes"]}
    assert by["semiconductor"]["status"] == "OK"
    assert by["finance"]["status"] == "FETCH_ERROR"
    assert by["finance"]["proxies"] == [{"ticker": "XLF", "change_pct": None, "status": "FETCH_ERROR"}]


def test_all_fetch_fail_all_mapped_themes_fetch_error():
    """이전 성공 데이터가 *없는* 최초 호출 — 이 경우엔 FETCH_ERROR로 표시할 수밖에 없다."""
    async def fetch(tickers):
        raise RuntimeError("network down")

    out = _run(tb.get_theme_briefing(now_ts=1000.0, fetcher=fetch))
    mapped = [t for t in out["themes"] if t["mapping_quality"] != "NONE"]
    assert mapped  # sanity: 매핑된 테마가 존재
    assert all(t["status"] == "FETCH_ERROR" for t in mapped)
    assert out["session_date_us"] is None


def test_total_failure_after_prior_success_keeps_previous_data_stale():
    """★이전에 성공한 데이터가 있는 상태에서 전체 fetch가 실패하면 이전 값을 유지해야 한다.

    과거 버그: 실패 시 전 종목 FETCH_ERROR로 덮어써 어제자 유효한 값(예: ^SOX -3.2%)까지
    지워버렸다. 수정 후: 이전 데이터를 stale=True로 그대로 반환.
    """
    async def good_fetch(tickers):
        return {"^SOX": {"change_pct": -3.2, "session_date": "2026-07-03"}}

    out1 = _run(tb.get_theme_briefing(now_ts=1000.0, fetcher=good_fetch))
    assert out1["session_date_us"] == "2026-07-03"
    assert out1["stale"] is False

    async def failing_fetch(tickers):
        raise RuntimeError("network down")

    # TTL(12h) 경과 후 재시도 시점에 전체 실패
    out2 = _run(tb.get_theme_briefing(now_ts=1000.0 + 13 * 3600, fetcher=failing_fetch))
    assert out2["session_date_us"] == "2026-07-03", "전체 실패해도 이전 기준일이 유지돼야 함"
    by = {t["theme_id"]: t for t in out2["themes"]}
    assert by["semiconductor"]["status"] == "OK", "이전 값이 FETCH_ERROR로 덮이면 안 됨"
    assert by["semiconductor"]["proxies"][0]["change_pct"] == -3.2
    assert out2["stale"] is True
    assert out2["stale_reason"] == "FETCH_FAILED"


def test_stale_fallback_does_not_lock_cache_retries_next_call():
    """전체 실패로 stale 반환한 뒤에도 캐시(fetched_at)는 갱신 안 돼 다음 호출이 다시 시도한다."""
    calls = {"n": 0}

    async def good_fetch(tickers):
        return {"^SOX": {"change_pct": 1.0, "session_date": "2026-07-01"}}

    _run(tb.get_theme_briefing(now_ts=1000.0, fetcher=good_fetch))

    async def failing_fetch(tickers):
        calls["n"] += 1
        raise RuntimeError("down")

    _run(tb.get_theme_briefing(now_ts=1000.0 + 13 * 3600, fetcher=failing_fetch))
    _run(tb.get_theme_briefing(now_ts=1000.0 + 13 * 3600 + 60, fetcher=failing_fetch))
    assert calls["n"] == 2, "stale 반환 시 캐시를 갱신하지 않아 다음 호출도 재시도해야 함"


def test_ttl_cache_single_fetch():
    calls = {"n": 0}

    async def fetch(tickers):
        calls["n"] += 1
        return {"^SOX": {"change_pct": 0.0, "session_date": "2026-07-03"}}

    _run(tb.get_theme_briefing(now_ts=1000.0, fetcher=fetch))
    _run(tb.get_theme_briefing(now_ts=1000.0 + 60, fetcher=fetch))  # TTL(6h) 내, 같은 KST 날짜
    assert calls["n"] == 1


def test_day_rollover_forces_refetch_even_within_ttl():
    """★2026-07-07 "07-02 고착" 사고 재현: 재시작이 저녁(23:50 KST)에 일어나 TTL(6h)
    잔여가 남아있어도, KST 날짜가 바뀌고 컷오프(05:00 KST)를 지나면 강제 재조회해야 한다.
    """
    from datetime import datetime, timedelta, timezone
    kst = timezone(timedelta(hours=9))

    last_fetch = datetime(2026, 7, 6, 23, 50, tzinfo=kst).timestamp()
    calls = {"n": 0}

    async def old_fetch(tickers):
        calls["n"] += 1
        return {"^SOX": {"change_pct": -1.0, "session_date": "2026-07-02"}}

    _run(tb.get_theme_briefing(now_ts=last_fetch, fetcher=old_fetch))
    assert calls["n"] == 1

    next_check = datetime(2026, 7, 7, 5, 10, tzinfo=kst).timestamp()
    assert next_check - last_fetch < 6 * 3600, "TTL(6h) 안이어야 이 테스트의 취지가 성립"

    async def fresh_fetch(tickers):
        calls["n"] += 1
        return {"^SOX": {"change_pct": 1.9, "session_date": "2026-07-06"}}

    out = _run(tb.get_theme_briefing(now_ts=next_check, fetcher=fresh_fetch))
    assert calls["n"] == 2, "TTL 안이어도 날짜 롤오버+컷오프 지나면 재조회해야 함"
    assert out["session_date_us"] == "2026-07-06"


def test_no_refetch_before_cutoff_hour_even_if_date_changed():
    """날짜는 바뀌었지만 컷오프(05:00 KST) 전이면 아직 마감 전일 수 있어 재조회 안 한다
    (과도한 API 호출 방지 — 컷오프 이후에만 "하루 한 번 강제 재시도"가 발동)."""
    from datetime import datetime, timedelta, timezone
    kst = timezone(timedelta(hours=9))

    last_fetch = datetime(2026, 7, 6, 23, 50, tzinfo=kst).timestamp()
    calls = {"n": 0}

    async def fetch(tickers):
        calls["n"] += 1
        return {"^SOX": {"change_pct": -1.0, "session_date": "2026-07-02"}}

    _run(tb.get_theme_briefing(now_ts=last_fetch, fetcher=fetch))

    before_cutoff = datetime(2026, 7, 7, 4, 0, tzinfo=kst).timestamp()
    _run(tb.get_theme_briefing(now_ts=before_cutoff, fetcher=fetch))
    assert calls["n"] == 1, "컷오프 전엔 날짜가 바뀌어도 재조회 안 해야 함"


def test_no_recommendation_or_toggle_flags():
    async def fetch(tickers):
        return {}

    out = _run(tb.get_theme_briefing(now_ts=1000.0, fetcher=fetch))
    assert out["used_for_order"] is False
    assert out["used_for_theme_toggle"] is False
    assert out["is_live_authorization"] is False
    # 추천/판단성 문구가 응답 어디에도 없어야 한다.
    import json
    blob = json.dumps(out, ensure_ascii=False)
    for banned in ("추천", "매수", "제외", "쉬세요", "유망"):
        assert banned not in blob
