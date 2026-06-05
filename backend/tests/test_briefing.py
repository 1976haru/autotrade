"""B1/B2/B5: 아침 브리핑 — 시세 graceful-fail/캐시 + 헤드라인 재작성 0/stale."""
from __future__ import annotations

import asyncio

import app.performance.headlines as hl
import app.performance.market_briefing as mb


def _run(coro):
    return asyncio.run(coro)


# ── B1 미국 시세 ───────────────────────────────────────────────────────────────

def setup_function():
    mb.reset_briefing_markets_cache_for_tests()
    hl.reset_headlines_cache_for_tests()


def test_markets_per_instrument_graceful_fail():
    # sp500/나스닥만 응답, 나머지 누락 → 해당 칸만 available=false(전체 실패 아님).
    async def fetch(instruments):
        return {"sp500": {"value": 5432.1, "change_pct": 0.8},
                "nasdaq": {"value": 17000.0, "change_pct": -0.3}}

    out = _run(mb.get_briefing_markets(now_ts=1000.0, fetcher=fetch))
    by = {m["key"]: m for m in out["markets"]}
    assert by["sp500"]["available"] is True and by["sp500"]["value"] == 5432.1
    assert by["sp500"]["change_pct"] == 0.8
    assert by["dow"]["available"] is False and by["dow"]["value"] is None
    assert by["usdkrw"]["available"] is False        # FX 미검증 → graceful
    assert len(out["markets"]) == 5
    assert "KST" in out["note"]


def test_markets_ttl_cache_single_fetch():
    calls = {"n": 0}

    async def fetch(instruments):
        calls["n"] += 1
        return {"sp500": {"value": 5000.0, "change_pct": 0.0}}

    _run(mb.get_briefing_markets(now_ts=1000.0, fetcher=fetch))
    _run(mb.get_briefing_markets(now_ts=1000.0 + 60, fetcher=fetch))  # TTL 내
    assert calls["n"] == 1                                            # 실조회 1회


def test_markets_all_fail_all_unavailable():
    async def fetch(instruments):
        raise RuntimeError("overseas blocked")

    out = _run(mb.get_briefing_markets(now_ts=2000.0, fetcher=fetch))
    assert all(m["available"] is False for m in out["markets"])


# ── B2 헤드라인 ────────────────────────────────────────────────────────────────

_RSS = """<?xml version="1.0"?><rss><channel>
<item><title>경제 헤드라인 A</title><link>http://x/a</link><pubDate>Wed, 04 Jun 2026 12:00:00 +0900</pubDate></item>
<item><title>경제 헤드라인 B</title><link>http://x/b</link><pubDate>Wed, 04 Jun 2026 11:00:00 +0900</pubDate></item>
<item><title>경제 헤드라인 C</title><link>http://x/c</link><pubDate>Wed, 04 Jun 2026 10:00:00 +0900</pubDate></item>
</channel></rss>"""


def test_headlines_feed_order_and_titles_verbatim():
    async def fetch():
        return _RSS

    out = _run(hl.get_headlines(now_ts=1000.0, fetcher=fetch))
    titles = [h["title"] for h in out["headlines"]]
    assert titles == ["경제 헤드라인 A", "경제 헤드라인 B", "경제 헤드라인 C"]  # 순서·제목 그대로
    assert out["headlines"][0]["link"] == "http://x/a"
    assert out["headlines"][0]["published_kst"] == "06-04 12:00"
    assert out["available"] is True and out["stale"] is False
    assert "선별" not in out["source"]                # 'AI 선별' 라벨 아님


def test_headlines_stale_cache_on_failure():
    async def ok():
        return _RSS

    async def fail():
        raise RuntimeError("RSS down")

    _run(hl.get_headlines(now_ts=1000.0, fetcher=ok))           # 캐시 채움
    out = _run(hl.get_headlines(now_ts=1000.0 + 9999, fetcher=fail))  # TTL 만료 + 실패
    assert out["available"] is True and out["stale"] is True    # 옛 캐시 + 기준시각
    assert out["asof_kst"] == "06-04" or out["asof_kst"]        # 기준시각 carry
    assert len(out["headlines"]) == 3


def test_headlines_fail_no_cache_unavailable():
    async def fail():
        raise RuntimeError("RSS down")

    out = _run(hl.get_headlines(now_ts=5000.0, fetcher=fail))
    assert out["available"] is False and out["headlines"] == []


def test_parse_rss_caps_at_five():
    items = "".join(f"<item><title>H{i}</title><link>l{i}</link></item>" for i in range(10))
    parsed = hl.parse_rss(f"<rss><channel>{items}</channel></rss>")
    assert len(parsed) == 5
