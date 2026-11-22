import asyncio
import json

import httpx

from app.brokers.kis_client import KisApiError, PAPER_HOST
from scripts import kis_paper_readonly_diagnostic as diagnostic


def run(coro):
    return asyncio.run(coro)


def test_error_summary_extracts_kis_codes_without_raw_response():
    details = diagnostic._safe_error_details(
        KisApiError("KIS orderable rt_cd=1 msg_cd=EGW12345 msg1=private-account-detail")
    )
    assert details["status"] == "FAIL"
    assert details["kis_rt_cd"] == "1"
    assert details["kis_msg_cd"] == "EGW12345"
    assert details["http_status"] == 200
    assert "private-account-detail" not in json.dumps(details)


def test_runtime_validation_error_has_safe_summary():
    details = diagnostic._safe_error_details(
        RuntimeError("KIS orderable calculation price does not match the KIS quote; account=1234567890")
    )
    assert details["sanitized_error_summary"] == "KIS 주문가능 계산가격과 KIS 시세가 일치하지 않습니다."
    assert "1234567890" not in json.dumps(details)


def test_http_status_is_reported_without_body():
    details = diagnostic._safe_error_details(
        KisApiError("KIS orderable-cash endpoint returned 403: token=secret")
    )
    assert details["http_status"] == 403
    assert "secret" not in json.dumps(details)


def test_structured_orderable_error_keeps_request_id_but_no_response_body():
    exc = KisApiError(
        "KIS orderable-cash endpoint returned 500",
        diagnostic={
            "http_status": 500, "kis_rt_cd": "1", "kis_msg_cd": "SERVER500",
            "sanitized_error_summary": "KIS server returned an HTTP 5xx error; response body omitted.",
            "server_request_id": "req-safe-123", "api_path": "/uapi/domestic-stock/v1/trading/inquire-psbl-order",
            "tr_id": "VTTC8908R", "order_division": "01",
        },
    )
    details = diagnostic._safe_error_details(exc)
    assert details["http_status"] == 500
    assert details["kis_msg_cd"] == "SERVER500"
    assert details["server_request_id"] == "req-safe-123"
    assert "response body omitted" in details["sanitized_error_summary"]


def test_premarket_http_failure_is_fail_closed_without_retry():
    calls = []

    async def inquiry():
        calls.append("one request")
        raise KisApiError(
            "KIS orderable-cash endpoint returned 500",
            diagnostic={
                "http_status": 500,
                "sanitized_error_summary": "KIS server returned an HTTP 5xx error; response body omitted.",
            },
        )

    result = run(diagnostic._capture_query(inquiry, lambda _v: {}, lambda _v: 1))
    assert result["status"] == "FAIL"
    assert result["http_status"] == 500
    assert result["result_count"] is None
    assert calls == ["one request"]


def test_failed_query_does_not_prevent_later_independent_query():
    called = []

    async def fail():
        raise RuntimeError("READ_ONLY_GUARD_BLOCKED_NON_READ_REQUEST")

    async def succeed():
        called.append(True)
        return [1, 2]

    failed = run(diagnostic._capture_query(fail, lambda _v: {}, lambda _v: 0))
    passed = run(diagnostic._capture_query(succeed, lambda value: {"values": value}, len))

    assert failed["status"] == "FAIL"
    assert failed["result_count"] is None
    assert passed["status"] == "PASS"
    assert passed["result_count"] == 2
    assert called == [True]


def test_order_counts_distinguish_filled_partial_open_and_canceled():
    rows = [
        {"ord_qty": 3, "filled": 3, "remaining": 0, "side": "BUY", "canceled": False},
        {"ord_qty": 4, "filled": 2, "remaining": 2, "side": "SELL", "canceled": False},
        {"ord_qty": 2, "filled": 0, "remaining": 2, "side": "BUY", "canceled": False},
        {"ord_qty": 2, "filled": 1, "remaining": 0, "side": "SELL", "canceled": True},
    ]
    assert diagnostic._order_counts(rows) == {
        "total": 4, "filled": 1, "partial": 1, "open": 2,
        "open_buy": 1, "open_sell": 1, "canceled": 1,
    }


def test_readonly_guard_allows_paper_get_and_blocks_order_cancel_post():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"ok": True})

    original = diagnostic._install_get_only_guard()

    async def exercise():
        async with httpx.AsyncClient(base_url=PAPER_HOST, transport=httpx.MockTransport(handler)) as client:
            response = await client.get("/uapi/domestic-stock/v1/trading/inquire-balance")
            assert response.status_code == 200
            for path in (
                "/uapi/domestic-stock/v1/trading/order-cash",
                "/uapi/domestic-stock/v1/trading/order-rvsecncl",
            ):
                try:
                    await client.post(path)
                except RuntimeError as exc:
                    assert str(exc) == "READ_ONLY_GUARD_BLOCKED_NON_READ_REQUEST"
                else:  # pragma: no cover - safety invariant
                    raise AssertionError("write request was not blocked")

    try:
        run(exercise())
    finally:
        httpx.AsyncClient.request = original
    assert seen == [("GET", "/uapi/domestic-stock/v1/trading/inquire-balance")]
