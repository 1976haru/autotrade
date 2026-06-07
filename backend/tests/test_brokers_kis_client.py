import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import app.brokers.kis_client as kc
from app.brokers.kis_client import (
    LIVE_HOST,
    PAPER_HOST,
    KisApiError,
    KisAuthError,
    KisClient,
    KisTokenRateLimitedError,
)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _isolated_token_cache(tmp_path, monkeypatch):
    # T1⒞ 디스크 캐시가 테스트 간/실환경과 섞이지 않게 tmp 로 격리.
    monkeypatch.setattr(kc, "_token_cache_path", lambda: tmp_path / "kis_token_cache.json")
    yield


def test_constructor_requires_credentials():
    with pytest.raises(KisAuthError):
        KisClient(app_key="", app_secret="s")
    with pytest.raises(KisAuthError):
        KisClient(app_key="k", app_secret="")


def test_paper_vs_live_base_url():
    assert KisClient("k", "s", is_paper=True).base_url == PAPER_HOST
    assert KisClient("k", "s", is_paper=False).base_url == LIVE_HOST


def _token_handler(seen: list, token: str = "tok-1", expires_in: int = 86400):
    """MockTransport handler that records calls and returns a token."""
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({
            "method": request.method,
            "path":   request.url.path,
            "headers": dict(request.headers),
        })
        if request.url.path.endswith("/oauth2/tokenP"):
            return httpx.Response(200, json={
                "access_token": token,
                "token_type":   "Bearer",
                "expires_in":   expires_in,
            })
        if request.url.path.endswith("/quotations/inquire-price"):
            return httpx.Response(200, json={"output": {"stck_prpr": "75000"}})
        return httpx.Response(404, json={"detail": f"unmocked {request.url.path}"})
    return handler


def test_token_is_fetched_once_and_cached():
    seen = []
    transport = httpx.MockTransport(_token_handler(seen))
    c = KisClient("k", "s", is_paper=True, transport=transport)
    t1 = run(c._ensure_token())
    t2 = run(c._ensure_token())
    assert t1 == "tok-1"
    assert t2 == "tok-1"
    token_calls = [s for s in seen if s["path"].endswith("/oauth2/tokenP")]
    assert len(token_calls) == 1


def test_token_refreshes_when_expired():
    seen = []
    transport = httpx.MockTransport(_token_handler(seen, token="tok-1", expires_in=86400))
    c = KisClient("k", "s", is_paper=True, transport=transport)
    run(c._ensure_token())
    # Force expiry (in-memory + disk) — 둘 다 만료여야 재발급.
    c._token_expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    kc._token_cache_path().unlink(missing_ok=True)
    run(c._ensure_token())
    token_calls = [s for s in seen if s["path"].endswith("/oauth2/tokenP")]
    assert len(token_calls) == 2


def test_token_endpoint_failure_raises():
    def handler(request):
        if request.url.path.endswith("/oauth2/tokenP"):
            return httpx.Response(500, text="server error")
        return httpx.Response(200, json={})
    transport = httpx.MockTransport(handler)
    c = KisClient("k", "s", is_paper=True, transport=transport)
    with pytest.raises(KisAuthError, match="500"):
        run(c._ensure_token())


def test_token_response_missing_access_token_raises():
    def handler(request):
        return httpx.Response(200, json={"token_type": "Bearer"})
    transport = httpx.MockTransport(handler)
    c = KisClient("k", "s", is_paper=True, transport=transport)
    with pytest.raises(KisAuthError, match="missing access_token"):
        run(c._ensure_token())


# ── T1: 레이트리밋 자기파괴 루프 방지 ──────────────────────────────────────────

def _egw_handler(seen: list):
    """tokenP 가 항상 EGW00133(403) — 레이트리밋 시뮬레이션."""
    def handler(request):
        seen.append(request.url.path)
        if request.url.path.endswith("/oauth2/tokenP"):
            return httpx.Response(403, json={"error_code": "EGW00133",
                                             "error_description": "1분당 1회"})
        return httpx.Response(200, json={})
    return handler


def test_egw00133_sets_backoff_and_skips_kis_within_window():
    # ⒝ 첫 발급은 KIS 호출 → EGW00133. 백오프 창 안의 다음 호출은 KIS 무호출 + 즉시 실패.
    seen = []
    c = KisClient("k", "s", is_paper=True, transport=httpx.MockTransport(_egw_handler(seen)))
    with pytest.raises(KisTokenRateLimitedError):
        run(c._ensure_token())
    assert len([p for p in seen if p.endswith("/oauth2/tokenP")]) == 1
    # 창 안 5회 연타 → KIS 추가 호출 0 (폭격 차단).
    for _ in range(5):
        with pytest.raises(KisTokenRateLimitedError):
            run(c._ensure_token())
    assert len([p for p in seen if p.endswith("/oauth2/tokenP")]) == 1  # 여전히 1


def test_backoff_window_expiry_allows_one_retry():
    seen = []
    c = KisClient("k", "s", is_paper=True, transport=httpx.MockTransport(_egw_handler(seen)))
    with pytest.raises(KisTokenRateLimitedError):
        run(c._ensure_token())
    # 백오프 창 경과 모사 → 다음 호출은 KIS 재호출(여전히 EGW → 다시 1회만).
    c._token_retry_not_before = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(KisTokenRateLimitedError):
        run(c._ensure_token())
    assert len([p for p in seen if p.endswith("/oauth2/tokenP")]) == 2


def test_concurrent_calls_issue_token_once():
    # ⒜ 동시 호출 N개 → 발급 1회만(락 직렬화).
    seen = []
    transport = httpx.MockTransport(_token_handler(seen, token="tok-x"))
    c = KisClient("k", "s", is_paper=True, transport=transport)

    async def many():
        return await asyncio.gather(*[c._ensure_token() for _ in range(8)])

    toks = run(many())
    assert all(t == "tok-x" for t in toks)
    assert len([p for p in seen if p["path"].endswith("/oauth2/tokenP")]) == 1


def test_disk_cache_reused_across_instances_no_refetch():
    # ⒞ 인스턴스 A 가 발급·저장 → 새 인스턴스 B(재기동 모사)가 디스크에서 재사용(발급 0).
    seenA, seenB = [], []
    a = KisClient("k", "s", is_paper=True, transport=httpx.MockTransport(_token_handler(seenA, token="tok-disk")))
    run(a._ensure_token())
    b = KisClient("k", "s", is_paper=True, transport=httpx.MockTransport(_token_handler(seenB, token="tok-other")))
    tok = run(b._ensure_token())
    assert tok == "tok-disk"  # 디스크 값 재사용
    assert len([p for p in seenB if p["path"].endswith("/oauth2/tokenP")]) == 0  # 발급 0


def test_disk_cache_expired_falls_back_to_refetch():
    seen = []
    c = KisClient("k", "s", is_paper=True, transport=httpx.MockTransport(_token_handler(seen, token="tok-new")))
    # 만료된 디스크 엔트리 주입.
    kc._save_cached_token("k", True, "stale", datetime.now(timezone.utc) - timedelta(hours=1))
    tok = run(c._ensure_token())
    assert tok == "tok-new"  # 만료 → 재발급 폴백
    assert len([p for p in seen if p["path"].endswith("/oauth2/tokenP")]) == 1


def test_get_price_sends_required_kis_headers_and_params():
    seen = []
    transport = httpx.MockTransport(_token_handler(seen))
    c = KisClient("appkey-x", "appsecret-y", is_paper=True, transport=transport)
    raw = run(c.get_price("005930"))
    assert raw["output"]["stck_prpr"] == "75000"

    quote_calls = [s for s in seen if s["path"].endswith("/quotations/inquire-price")]
    assert len(quote_calls) == 1
    headers = quote_calls[0]["headers"]
    assert headers["authorization"] == "Bearer tok-1"
    assert headers["appkey"]    == "appkey-x"
    assert headers["appsecret"] == "appsecret-y"
    assert headers["tr_id"]     == "FHKST01010100"
    assert headers["custtype"]  == "P"


def test_get_price_endpoint_failure_raises_api_error():
    def handler(request):
        if request.url.path.endswith("/oauth2/tokenP"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})
        return httpx.Response(503, text="upstream down")
    transport = httpx.MockTransport(handler)
    c = KisClient("k", "s", is_paper=True, transport=transport)
    with pytest.raises(KisApiError, match="503"):
        run(c.get_price("005930"))


_BALANCE_RESPONSE = {
    "output1": [
        {
            "pdno": "005930",
            "prdt_name": "삼성전자",
            "hldg_qty": "10",
            "pchs_avg_pric": "75100.0000",
            "prpr": "75500",
        },
        {
            "pdno": "000660",
            "prdt_name": "SK하이닉스",
            "hldg_qty": "5",
            "pchs_avg_pric": "182000.0000",
            "prpr": "180500",
        },
    ],
    "output2": [
        {
            "dnca_tot_amt":  "5234800",
            "tot_evlu_amt": "10000000",
        },
    ],
    "rt_cd": "0",
    "msg1": "정상처리되었습니다.",
}


def _balance_handler(seen: list, response: dict | None = None):
    response = response or _BALANCE_RESPONSE
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({
            "method":  request.method,
            "path":    request.url.path,
            "params":  dict(request.url.params),
            "headers": dict(request.headers),
        })
        if request.url.path.endswith("/oauth2/tokenP"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})
        if request.url.path.endswith("/inquire-balance"):
            return httpx.Response(200, json=response)
        return httpx.Response(404)
    return handler


def test_inquire_balance_paper_uses_paper_tr_id():
    seen = []
    c = KisClient("k", "s", is_paper=True, transport=httpx.MockTransport(_balance_handler(seen)))
    raw = run(c.inquire_balance("12345678", "01"))
    assert raw == _BALANCE_RESPONSE

    bal = [s for s in seen if s["path"].endswith("/inquire-balance")][0]
    assert bal["headers"]["tr_id"] == "VTTC8434R"
    assert bal["params"]["CANO"] == "12345678"
    assert bal["params"]["ACNT_PRDT_CD"] == "01"
    assert bal["params"]["INQR_DVSN"] == "02"


def test_inquire_balance_live_uses_live_tr_id():
    seen = []
    c = KisClient("k", "s", is_paper=False, transport=httpx.MockTransport(_balance_handler(seen)))
    run(c.inquire_balance("12345678", "01"))
    bal = [s for s in seen if s["path"].endswith("/inquire-balance")][0]
    assert bal["headers"]["tr_id"] == "TTTC8434R"


def test_inquire_balance_endpoint_failure_raises_api_error():
    def handler(request):
        if request.url.path.endswith("/oauth2/tokenP"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})
        return httpx.Response(500, text="internal error")
    c = KisClient("k", "s", is_paper=True, transport=httpx.MockTransport(handler))
    with pytest.raises(KisApiError, match="500"):
        run(c.inquire_balance("12345678", "01"))


_DAILY_CCLD_RESPONSE = {
    "output1": [
        {
            "odno": "0001", "pdno": "005930",
            "sll_buy_dvsn_cd": "02",
            "ord_qty": "1", "tot_ccld_qty": "1",
            "avg_prvs": "75000", "cncl_yn": "N",
        },
    ],
}


def _ccld_handler(seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({
            "method":  request.method,
            "path":    request.url.path,
            "params":  dict(request.url.params),
            "headers": dict(request.headers),
        })
        if request.url.path.endswith("/oauth2/tokenP"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})
        if request.url.path.endswith("/inquire-daily-ccld"):
            return httpx.Response(200, json=_DAILY_CCLD_RESPONSE)
        return httpx.Response(404)
    return handler


def test_inquire_daily_ccld_paper_uses_paper_tr_id():
    seen = []
    c = KisClient("k", "s", is_paper=True, transport=httpx.MockTransport(_ccld_handler(seen)))
    raw = run(c.inquire_daily_ccld("12345678", "01"))
    assert raw == _DAILY_CCLD_RESPONSE
    call = [s for s in seen if s["path"].endswith("/inquire-daily-ccld")][0]
    # 현행 KIS TR id (구형 VTTC8001R 은 빈 결과만 반환 — 2026-06-05 실측 회귀 가드).
    assert call["headers"]["tr_id"] == "VTTC0081R"
    assert call["params"]["CANO"] == "12345678"
    assert call["params"]["ACNT_PRDT_CD"] == "01"
    assert call["params"]["INQR_STRT_DT"]
    assert call["params"]["INQR_END_DT"]


def test_inquire_daily_ccld_live_uses_live_tr_id():
    seen = []
    c = KisClient("k", "s", is_paper=False, transport=httpx.MockTransport(_ccld_handler(seen)))
    run(c.inquire_daily_ccld("12345678", "01"))
    call = [s for s in seen if s["path"].endswith("/inquire-daily-ccld")][0]
    assert call["headers"]["tr_id"] == "TTTC0081R"


def test_inquire_daily_ccld_endpoint_failure_raises():
    def handler(request):
        if request.url.path.endswith("/oauth2/tokenP"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})
        return httpx.Response(503, text="upstream down")
    c = KisClient("k", "s", is_paper=True, transport=httpx.MockTransport(handler))
    with pytest.raises(KisApiError, match="503"):
        run(c.inquire_daily_ccld("12345678", "01"))


def _order_handler(seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({
            "method":  request.method,
            "path":    request.url.path,
            "headers": dict(request.headers),
            "body":    request.content.decode() if request.content else "",
        })
        if request.url.path.endswith("/oauth2/tokenP"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})
        if request.url.path.endswith("/order-cash"):
            return httpx.Response(200, json={
                "rt_cd": "0",
                "msg1":  "정상처리되었습니다.",
                "output": {"ODNO": "0000777", "ORD_TMD": "094530"},
            })
        return httpx.Response(404)
    return handler


def test_place_order_paper_buy_uses_paper_buy_tr_id_and_market_dvsn():
    seen = []
    c = KisClient("k", "s", is_paper=True, transport=httpx.MockTransport(_order_handler(seen)))
    raw = run(c.place_order("12345678", "01", "005930",
                            is_buy=True, quantity=1, order_type="market"))
    assert raw["output"]["ODNO"] == "0000777"

    call = [s for s in seen if s["path"].endswith("/order-cash")][0]
    assert call["method"] == "POST"
    assert call["headers"]["tr_id"] == "VTTC0802U"  # paper buy
    assert '"ORD_DVSN":"01"' in call["body"]        # market
    assert '"ORD_UNPR":"0"'  in call["body"]
    assert '"ORD_QTY":"1"'   in call["body"]
    assert '"PDNO":"005930"' in call["body"]


def test_place_order_paper_sell_uses_paper_sell_tr_id():
    seen = []
    c = KisClient("k", "s", is_paper=True, transport=httpx.MockTransport(_order_handler(seen)))
    run(c.place_order("12345678", "01", "005930",
                      is_buy=False, quantity=2, order_type="market"))
    call = [s for s in seen if s["path"].endswith("/order-cash")][0]
    assert call["headers"]["tr_id"] == "VTTC0801U"  # paper sell


def test_place_order_live_buy_uses_live_buy_tr_id():
    seen = []
    c = KisClient("k", "s", is_paper=False, transport=httpx.MockTransport(_order_handler(seen)))
    run(c.place_order("12345678", "01", "005930",
                      is_buy=True, quantity=1, order_type="market"))
    call = [s for s in seen if s["path"].endswith("/order-cash")][0]
    assert call["headers"]["tr_id"] == "TTTC0802U"


def test_place_order_limit_uses_zero_zero_dvsn_with_price():
    seen = []
    c = KisClient("k", "s", is_paper=True, transport=httpx.MockTransport(_order_handler(seen)))
    run(c.place_order("12345678", "01", "005930",
                      is_buy=True, quantity=1, order_type="limit", limit_price=75_000))
    call = [s for s in seen if s["path"].endswith("/order-cash")][0]
    assert '"ORD_DVSN":"00"'    in call["body"]
    assert '"ORD_UNPR":"75000"' in call["body"]


def test_place_order_limit_without_price_raises():
    c = KisClient("k", "s", is_paper=True, transport=httpx.MockTransport(_order_handler([])))
    with pytest.raises(ValueError, match="limit_price"):
        run(c.place_order("12345678", "01", "005930",
                          is_buy=True, quantity=1, order_type="limit"))


def test_place_order_endpoint_failure_raises():
    def handler(request):
        if request.url.path.endswith("/oauth2/tokenP"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})
        return httpx.Response(500, text="server down")
    c = KisClient("k", "s", is_paper=True, transport=httpx.MockTransport(handler))
    with pytest.raises(KisApiError, match="500"):
        run(c.place_order("12345678", "01", "005930",
                          is_buy=True, quantity=1, order_type="market"))
