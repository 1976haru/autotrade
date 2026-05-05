import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.brokers.kis_client import (
    LIVE_HOST,
    PAPER_HOST,
    KisApiError,
    KisAuthError,
    KisClient,
)


def run(coro):
    return asyncio.run(coro)


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
    # Force expiry
    c._token_expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
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
    assert call["headers"]["tr_id"] == "VTTC8001R"
    assert call["params"]["CANO"] == "12345678"
    assert call["params"]["ACNT_PRDT_CD"] == "01"
    assert call["params"]["INQR_STRT_DT"]
    assert call["params"]["INQR_END_DT"]


def test_inquire_daily_ccld_live_uses_live_tr_id():
    seen = []
    c = KisClient("k", "s", is_paper=False, transport=httpx.MockTransport(_ccld_handler(seen)))
    run(c.inquire_daily_ccld("12345678", "01"))
    call = [s for s in seen if s["path"].endswith("/inquire-daily-ccld")][0]
    assert call["headers"]["tr_id"] == "TTTC8001R"


def test_inquire_daily_ccld_endpoint_failure_raises():
    def handler(request):
        if request.url.path.endswith("/oauth2/tokenP"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})
        return httpx.Response(503, text="upstream down")
    c = KisClient("k", "s", is_paper=True, transport=httpx.MockTransport(handler))
    with pytest.raises(KisApiError, match="503"):
        run(c.inquire_daily_ccld("12345678", "01"))
