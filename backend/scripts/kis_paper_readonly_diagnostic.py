"""Read-only KIS PAPER diagnostics for the official APPDATA installation.

Run from ``backend`` with the project's Python environment. This tool never
submits or cancels orders. HTTP non-GET requests are blocked before transport.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from collections.abc import Awaitable, Callable

import httpx
from dotenv import load_dotenv

_CURRENT_STAGE = "settings"


def _load_official_settings() -> Any:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not set")
    env_path = Path(appdata) / "Autotrade" / ".env"
    if not env_path.is_file() or not load_dotenv(env_path, override=True):
        raise RuntimeError("APPDATA Autotrade env file missing or could not be loaded")

    # Import the application from this checkout, but disable Pydantic's CWD
    # .env source so backend/.env cannot be merged into APPDATA configuration.
    backend_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend_root))

    from app.core.config import Settings, get_settings

    Settings.model_config = {**Settings.model_config, "env_file": None}
    get_settings.cache_clear()
    settings = get_settings()
    if str(settings.default_mode).upper().split(".")[-1] != "PAPER":
        raise RuntimeError("DEFAULT_MODE must be PAPER")
    if not settings.kis_is_paper:
        raise RuntimeError("KIS_IS_PAPER must be true")
    if settings.enable_live_trading:
        raise RuntimeError("ENABLE_LIVE_TRADING must be false")
    if settings.enable_kis_paper_auto_trading:
        raise RuntimeError("ENABLE_KIS_PAPER_AUTO_TRADING must be false for diagnostics")
    if not settings.kis_paper_auto_order_dry_run:
        raise RuntimeError("KIS_PAPER_AUTO_ORDER_DRY_RUN must be true")
    if not (settings.kis_app_key and settings.kis_app_secret and settings.kis_account_no):
        raise RuntimeError("PAPER KIS credentials are incomplete")
    if len(settings.kis_account_no) < 10:
        raise RuntimeError("PAPER account format is invalid")
    return settings


def _install_get_only_guard() -> Any:
    original_request = httpx.AsyncClient.request

    async def get_only(self: httpx.AsyncClient, method: str, url: Any, *args: Any, **kwargs: Any):
        host = str(getattr(getattr(self, "base_url", None), "host", "") or "").lower()
        path = str(url).split("?", 1)[0]
        if host != "openapivts.koreainvestment.com":
            raise RuntimeError("READ_ONLY_GUARD_BLOCKED_NON_PAPER_HOST")
        verb = str(method).upper()
        # Permit only OAuth token renewal. Trading, amend, and cancel requests
        # use different paths and remain blocked before reaching the transport.
        if verb != "GET" and not (verb == "POST" and path == "/oauth2/tokenP"):
            raise RuntimeError("READ_ONLY_GUARD_BLOCKED_NON_READ_REQUEST")
        return await original_request(self, method, url, *args, **kwargs)

    httpx.AsyncClient.request = get_only  # type: ignore[method-assign]
    return original_request


def _kst_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=9)))


def _safe_error_details(exc: Exception) -> dict[str, Any]:
    """Extract protocol codes without echoing response bodies or credentials."""
    structured = getattr(exc, "diagnostic", None)
    if isinstance(structured, dict) and structured:
        # The client attaches only allowlisted protocol metadata for the
        # orderable-cash endpoint; do not fall back to exception text here.
        return {
            "status": "FAIL", "error_type": type(exc).__name__,
            "sanitized_error_summary": structured.get(
                "sanitized_error_summary", "KIS read-only inquiry failed."
            ),
            "http_status": structured.get("http_status"),
            "kis_rt_cd": structured.get("kis_rt_cd"),
            "kis_msg_cd": structured.get("kis_msg_cd"),
            "server_request_id": structured.get("server_request_id"),
            "result_count": None,
        }
    message = str(exc)
    status = re.search(r"\b(?:returned|status(?:_code)?)\s*[:=]?\s*(\d{3})\b", message, re.I)
    rt_cd = re.search(r"\brt_cd\s*=\s*([A-Za-z0-9_-]+)", message, re.I)
    msg_cd = re.search(r"\bmsg_cd\s*=\s*([A-Za-z0-9_-]+)", message, re.I)
    if "READ_ONLY_GUARD_BLOCKED_NON_PAPER_HOST" in message:
        summary = "진단 안전장치가 PAPER 서버가 아닌 호스트를 차단했습니다."
    elif "READ_ONLY_GUARD_BLOCKED_NON_READ_REQUEST" in message:
        summary = "진단 안전장치가 주문 또는 취소 요청을 차단했습니다."
    elif "calculation price does not match" in message:
        summary = "KIS 주문가능 계산가격과 KIS 시세가 일치하지 않습니다."
    elif "missing fields:" in message:
        fields = re.search(r"missing fields:\s*([a-z0-9_, ]+)", message, re.I)
        summary = "KIS 주문가능 응답 필드 누락" + (f": {fields.group(1).strip()}" if fields else ".")
    elif rt_cd:
        summary = "KIS가 조회를 업무 오류 코드로 거부했습니다."
    elif status:
        summary = "KIS HTTP 요청이 성공 상태를 반환하지 않았습니다."
    elif isinstance(exc, httpx.TimeoutException):
        summary = "KIS 읽기 전용 요청 시간이 초과됐습니다."
    elif isinstance(exc, httpx.ConnectError):
        summary = "KIS 서버 연결을 설정하지 못했습니다."
    elif isinstance(exc, RuntimeError):
        summary = "진단 전제조건 또는 응답 검증에서 RuntimeError가 발생했습니다."
    else:
        summary = "읽기 전용 조회 또는 응답 검증에 실패했습니다."
    return {
        "status": "FAIL", "error_type": type(exc).__name__,
        "sanitized_error_summary": summary,
        "http_status": 200 if rt_cd else (int(status.group(1)) if status else None),
        "kis_rt_cd": rt_cd.group(1) if rt_cd else None,
        "kis_msg_cd": msg_cd.group(1) if msg_cd else None,
        "server_request_id": None,
        "result_count": None,
    }


async def _capture_query(
    query: Callable[[], Awaitable[Any]], summarize: Callable[[Any], dict[str, Any]],
    result_count: Callable[[Any], int],
) -> dict[str, Any]:
    checked = _kst_now().isoformat(timespec="seconds")
    try:
        value = await query()
        # KisClient returns only after HTTP 200 and KIS rt_cd=0 validation.
        return {
            "status": "PASS", "checked_at_kst": checked, "error_type": None,
            "sanitized_error_summary": None, "http_status": 200,
            "kis_rt_cd": "0", "kis_msg_cd": None,
            "server_request_id": None,
            "result_count": result_count(value), **summarize(value),
        }
    except Exception as exc:  # continue with other independent read-only queries
        return {"checked_at_kst": checked, **_safe_error_details(exc)}


def _order_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    active = [row for row in rows if not row.get("canceled")]
    return {
        "total": len(rows),
        "filled": sum(1 for row in active if row["ord_qty"] > 0 and row["filled"] >= row["ord_qty"]),
        "partial": sum(1 for row in active if row["filled"] > 0 and row["remaining"] > 0),
        "open": sum(1 for row in active if row["remaining"] > 0),
        "open_buy": sum(1 for row in active if row["remaining"] > 0 and row["side"] == "BUY"),
        "open_sell": sum(1 for row in active if row["remaining"] > 0 and row["side"] == "SELL"),
        "canceled": sum(1 for row in rows if row.get("canceled")),
    }


async def _run(symbol: str) -> dict[str, Any]:
    global _CURRENT_STAGE
    from app.brokers.kis import KisBrokerAdapter
    from app.brokers.kis_client import KisClient
    from app.core.rate_limiter import get_kis_rate_limiter
    from app.kis_paper.order_limits import resolve_kis_paper_order_limits, safe_market_buy_quantity

    global_input_block = None
    try:
        settings = _load_official_settings()
        limits = resolve_kis_paper_order_limits(settings)
        _install_get_only_guard()
        client = KisClient(settings.kis_app_key, settings.kis_app_secret, is_paper=True,
                           rate_limiter=get_kis_rate_limiter())
        broker = KisBrokerAdapter(client=client, is_paper=True)
    except Exception as exc:
        global_input_block = _safe_error_details(exc)
        items = {}
        for name in ("authentication", "balance", "positions", "daily_orders", "open_orders", "quote", "orderable"):
            items[name] = {
                "status": "BLOCKED", "checked_at_kst": _kst_now().isoformat(timespec="seconds"),
                "error_type": global_input_block["error_type"],
                "sanitized_error_summary": global_input_block["sanitized_error_summary"],
                "http_status": None, "kis_rt_cd": None, "kis_msg_cd": None, "result_count": None,
            }
        return {"overall_status": "BLOCKED", "items": items,
                "order_requests_sent": 0, "cancel_requests_sent": 0}

    now = _kst_now()
    _CURRENT_STAGE = "KIS PAPER authentication"
    auth = await _capture_query(client._ensure_token, lambda _token: {"authenticated": True}, lambda _token: 1)
    if auth["status"] == "PASS":
        # _ensure_token may reuse a valid APPDATA cache, so no HTTP status is
        # asserted unless an API response was actually made.
        auth["http_status"] = None
        auth["kis_rt_cd"] = None
        auth["sanitized_error_summary"] = "토큰 캐시 재사용 또는 PAPER 인증 성공"
    items: dict[str, Any] = {"authentication": auth}
    if auth["status"] != "PASS":
        for name in ("balance", "positions", "daily_orders", "open_orders", "quote", "orderable"):
            items[name] = {
                "status": "BLOCKED", "checked_at_kst": _kst_now().isoformat(timespec="seconds"),
                "error_type": None, "sanitized_error_summary": "인증 실패로 KIS 조회를 실행하지 않았습니다.",
                "http_status": None, "kis_rt_cd": None, "kis_msg_cd": None, "result_count": None,
            }
    else:
        _CURRENT_STAGE = "balance inquiry"
        items["balance"] = await _capture_query(
            broker.get_balance,
            lambda balance: {"cash_krw": int(balance.cash), "equity_krw": int(balance.equity)},
            lambda _value: 1,
        )
        _CURRENT_STAGE = "positions inquiry"
        items["positions"] = await _capture_query(
            broker.get_positions,
            lambda positions: {"positions": [
                {"symbol": p.symbol, "quantity": p.quantity, "sellable_quantity": p.sellable_quantity}
                for p in positions
            ]},
            len,
        )
        _CURRENT_STAGE = "KST daily order/fill inquiry"
        items["daily_orders"] = await _capture_query(
            lambda: broker.list_today_orders(kst_date=now.strftime("%Y%m%d")),
            _order_counts,
            len,
        )
        daily = items["daily_orders"]
        items["open_orders"] = {
            "status": daily["status"], "checked_at_kst": daily["checked_at_kst"],
            "error_type": daily["error_type"], "sanitized_error_summary": daily["sanitized_error_summary"],
            "http_status": daily["http_status"], "kis_rt_cd": daily["kis_rt_cd"],
            "kis_msg_cd": daily["kis_msg_cd"],
            "result_count": daily["open"] if daily["status"] == "PASS" else None,
            "buy": daily.get("open_buy"), "sell": daily.get("open_sell"),
        }

        _CURRENT_STAGE = "KIS PAPER quote"
        quote_holder: dict[str, Any] = {}
        async def get_quote():
            value = await broker.get_price(symbol)
            quote_holder["value"] = value
            if int(value.price) <= 0:
                raise RuntimeError("KIS quote returned an invalid price")
            return value
        items["quote"] = await _capture_query(
            get_quote, lambda q: {"symbol": symbol, "price_krw": int(q.price)}, lambda _q: 1,
        )

        if items["quote"]["status"] != "PASS":
            items["orderable"] = {
                "status": "BLOCKED", "checked_at_kst": _kst_now().isoformat(timespec="seconds"),
                "error_type": None, "sanitized_error_summary": "정상 KIS 시세가 없어 주문가능금액 조회를 생략했습니다.",
                "http_status": None, "kis_rt_cd": None, "kis_msg_cd": None, "result_count": None,
            }
        else:
            quote = quote_holder["value"]
            _CURRENT_STAGE = "symbol orderable-cash inquiry"
            def summarize_orderable(raw: dict[str, Any]) -> dict[str, Any]:
                def number(name: str) -> int | None:
                    value = raw.get(name)
                    if value in (None, ""):
                        return None
                    try:
                        return int(float(value))
                    except (TypeError, ValueError, OverflowError):
                        return None

                # Only no-credit fields are authoritative for this cash-only bot.
                # max_buy_* includes credit capacity and remains informational.
                required = (
                    "ord_psbl_cash", "nrcvb_buy_amt", "nrcvb_buy_qty",
                    "psbl_qty_calc_unpr",
                )
                missing = [name for name in required if number(name) is None]
                price = int(quote.price)
                calc_price = number("psbl_qty_calc_unpr")
                price_matches = calc_price == price
                no_credit = number("nrcvb_buy_amt")
                max_qty = number("nrcvb_buy_qty")
                reported_cash = number("ord_psbl_cash")
                complete = not missing and calc_price is not None and calc_price > 0
                quantities: dict[str, int | None] = {
                    "quantity_for_1m_cap": None,
                    "quantity_for_2m_cap": None,
                }
                if complete:
                    common = {
                        "quote_price_krw": price,
                        "calculation_price_krw": calc_price,
                        "no_credit_buy_quantity": max_qty,
                        "no_credit_buy_amount_krw": no_credit,
                        "orderable_cash_krw": reported_cash,
                        "cost_reserve_bps": limits.cost_reserve_bps,
                        "remaining_total_budget_krw": limits.total_budget_krw,
                    }
                    quantities = {
                        "quantity_for_1m_cap": safe_market_buy_quantity(
                            target_budget_krw=min(1_000_000, limits.total_budget_krw), **common,
                        ),
                        "quantity_for_2m_cap": safe_market_buy_quantity(
                            target_budget_krw=min(2_000_000, limits.total_budget_krw), **common,
                        ),
                    }
                safe_2m_qty = quantities["quantity_for_2m_cap"]
                if safe_2m_qty is None or safe_2m_qty <= 0 or calc_price is None:
                    fully_funded_2m_slots = 0
                else:
                    safe_2m_unit = max(price, calc_price)
                    reserved_per_safe_2m_slot = safe_2m_qty * safe_2m_unit
                    reserved_per_safe_2m_slot += (
                        reserved_per_safe_2m_slot * limits.cost_reserve_bps + 9_999
                    ) // 10_000
                    fully_funded_2m_slots = min(
                        limits.max_positions,
                        limits.total_budget_krw // max(1, reserved_per_safe_2m_slot),
                    )
                return {
                    "api_response_validation": "PASS",
                    "required_cash_fields_validation": "PASS" if complete else "FAIL",
                    "safe_quantity_validation": "PASS" if complete else "BLOCKED",
                    "orderable_validation": "PASS" if complete else "FAIL",
                    "validation_summary": (
                        "Missing or invalid no-credit capacity fields: " + ",".join(missing)
                        if not complete else None
                    ),
                    "symbol": symbol, "quote_price_krw": price,
                    "ord_psbl_cash_krw": reported_cash, "nrcvb_buy_amt_krw": no_credit,
                    "nrcvb_buy_qty": max_qty, "max_buy_amt_krw": number("max_buy_amt"),
                    "max_buy_qty": number("max_buy_qty"), "calculation_price_krw": calc_price,
                    "quote_and_calculation_price_match": price_matches,
                    "calculation_price_difference_krw": (calc_price - price) if calc_price is not None else None,
                    "conservative_unit_price_krw": max(price, calc_price) if calc_price is not None else None,
                    "order_division": "01_market_capacity",
                    "policy_version": limits.policy_version, "internal_total_budget_krw": limits.total_budget_krw,
                    "internal_per_symbol_budget_krw": limits.per_symbol_krw,
                    "internal_max_order_krw": limits.max_order_krw, "internal_max_positions": limits.max_positions,
                    "internal_daily_buy_count": limits.daily_buy_count,
                    "internal_daily_buy_limit_krw": limits.daily_buy_krw,
                    "cost_reserve_bps": limits.cost_reserve_bps,
                    **quantities,
                    "fully_funded_2m_slots_with_cost_reserve": fully_funded_2m_slots,
                    "missing_fields": missing,
                }
            orderable = await _capture_query(
                lambda: client.inquire_orderable_cash(
                    *broker._split_account(), symbol=symbol, price=int(quote.price), order_division="01"
                ), summarize_orderable, lambda _raw: 1,
            )
            orderable.update({
                "api_path": "/uapi/domestic-stock/v1/trading/inquire-psbl-order",
                "tr_id": "VTTC8908R",
                "order_division": "01",
                "order_price_valid": isinstance(quote.price, (int, float)) and int(quote.price) > 0,
            })
            if "api_response_validation" not in orderable:
                api_ok = orderable.get("http_status") == 200 and orderable.get("kis_rt_cd") == "0"
                orderable["api_response_validation"] = "PASS" if api_ok else (
                    "FAIL" if orderable.get("http_status") is not None else "BLOCKED"
                )
                orderable["required_cash_fields_validation"] = "FAIL" if api_ok else "BLOCKED"
                orderable["safe_quantity_validation"] = "BLOCKED"
            if orderable.get("required_cash_fields_validation") == "FAIL":
                orderable["status"] = "FAIL"
                orderable["error_type"] = "ResponseValidationError"
                orderable["sanitized_error_summary"] = orderable.get("validation_summary")
            items["orderable"] = orderable

    statuses = [item["status"] for item in items.values()]
    overall = "FAIL" if "FAIL" in statuses else "BLOCKED" if "BLOCKED" in statuses else "PASS"
    return {
        "overall_status": overall, "query_time_kst": _kst_now().isoformat(timespec="seconds"),
        "trading_date_kst": now.strftime("%Y%m%d"), "paper": True, "live_trading_enabled": False,
        "auto_order_enabled": False, "dry_run": True, "shared_rate_limiter": True,
        "items": items, "order_requests_sent": 0, "cancel_requests_sent": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only KIS PAPER account diagnostics")
    parser.add_argument("--symbol", default="005930", help="Domestic stock code (default: 005930)")
    args = parser.parse_args()
    if not (args.symbol.isdigit() and len(args.symbol) == 6):
        parser.error("--symbol must be a six-digit domestic stock code")
    try:
        result = asyncio.run(_run(args.symbol))
    except Exception as exc:  # report type and sanitized message; never settings/tokens
        failure = _safe_error_details(exc)
        blocked = {
            "status": "BLOCKED", "checked_at_kst": _kst_now().isoformat(timespec="seconds"),
            "error_type": failure["error_type"],
            "sanitized_error_summary": failure["sanitized_error_summary"],
            "http_status": failure["http_status"], "kis_rt_cd": failure["kis_rt_cd"],
            "kis_msg_cd": failure["kis_msg_cd"], "result_count": None,
        }
        print(json.dumps({
            "overall_status": "BLOCKED",
            "query_time_kst": _kst_now().isoformat(timespec="seconds"),
            "failed_at": _CURRENT_STAGE,
            "items": {name: dict(blocked) for name in (
                "authentication", "balance", "positions", "daily_orders", "open_orders", "quote", "orderable"
            )},
            "order_requests_sent": 0,
            "cancel_requests_sent": 0,
        }, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("overall_status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
