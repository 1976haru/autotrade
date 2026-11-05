"""Date/account scoped authorization for unattended KIS PAPER sessions.

The file is deliberately separate from normal settings: it is an authorization
record, not a configuration flag.  It never contains the account number and it
cannot authorize cancellation.  Every real order revalidates it immediately
before routing.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db.models import EmergencyStopEvent

KST = timezone(timedelta(hours=9))
SESSION_FILE = "paper-session-approval.json"


def account_fingerprint(account_no: str, product_code: str = "01") -> str:
    raw = f"KIS-PAPER|{account_no.strip()}|{product_code.strip()}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _base_dir() -> Path:
    root = os.getenv("APPDATA") or tempfile.gettempdir()
    return Path(root) / "Autotrade" / "authorization"


def approval_path() -> Path:
    override = os.getenv("AUTOTRADE_PAPER_APPROVAL_FILE", "").strip()
    return Path(override) if override else _base_dir() / SESSION_FILE


def _write_atomic(data: dict[str, Any]) -> None:
    path = approval_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_approval() -> dict[str, Any] | None:
    try:
        value = json.loads(approval_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def desktop_token_valid(candidate: str | None) -> bool:
    expected = os.getenv("AUTOTRADE_DESKTOP_AUTH_TOKEN", "")
    return bool(expected and candidate and hmac.compare_digest(expected, candidate))


def create_approval(*, db: Any, settings: Any, risk: Any, target_date: str, actor: str,
                    explicit_ack: bool) -> dict[str, Any]:
    if not explicit_ack:
        raise ValueError("EXPLICIT_APPROVAL_REQUIRED")
    target = datetime.strptime(target_date, "%Y-%m-%d").date()
    today = datetime.now(KST).date()
    if target < today or target > today + timedelta(days=1):
        raise ValueError("TARGET_DATE_NOT_TODAY_OR_NEXT_DAY")
    if not bool(getattr(settings, "kis_is_paper", False)):
        raise ValueError("PAPER_ACCOUNT_REQUIRED")
    if bool(getattr(settings, "enable_live_trading", False)):
        raise ValueError("LIVE_TRADING_MUST_BE_OFF")
    account = str(getattr(settings, "kis_account_no", "") or "")
    if len(account) < 8:
        raise ValueError("PAPER_ACCOUNT_MISSING")
    latest = db.execute(
        select(EmergencyStopEvent).order_by(EmergencyStopEvent.id.desc())
    ).scalars().first()
    if bool(getattr(risk, "emergency_stop", False)) or (latest is not None and bool(latest.enabled)):
        raise ValueError("EMERGENCY_STOP_ACTIVE")
    approved_at = datetime.now(timezone.utc)
    from app.kis_paper.order_limits import resolve_kis_paper_order_limits
    limits = resolve_kis_paper_order_limits(settings)
    record = {
        "schema_version": 1,
        "status": "SCHEDULED",
        "target_date": target.isoformat(),
        "expires_at": datetime.combine(target + timedelta(days=1), time(0), KST).astimezone(timezone.utc).isoformat(),
        "account_fingerprint": account_fingerprint(account, str(getattr(settings, "kis_product_code", "01"))),
        "allowed_sides": ["BUY", "SELL"],
        "limits": {
            "policy_version": limits.policy_version,
            "total_budget_krw": limits.total_budget_krw,
            "max_positions": limits.max_positions,
            "per_symbol_krw": limits.per_symbol_krw,
            "max_order_krw": limits.max_order_krw,
            "daily_buy_count": limits.daily_buy_count,
            "daily_buy_krw": limits.daily_buy_krw,
            "tick_buy_count": limits.buys_per_tick,
            "cost_reserve_bps": limits.cost_reserve_bps,
            "buy_cutoff": "14:30", "stop_loss_pct": 1.5,
            "take_profit_pct": 2.5,
        },
        "approved_by": actor[:128],
        "auth_method": "WINDOWS_DESKTOP_SESSION",
        "approved_at": approved_at.isoformat(),
        "emergency_baseline_id": int(latest.id) if latest is not None else 0,
        "cancel_authorized": False,
        "cancel_scope": "BOT_OWNED_KIS_PAPER_ONLY_REQUIRES_SEPARATE_APPROVAL",
        "last_preflight_at": None,
        "block_reason": None,
    }
    db.commit()
    _write_atomic(record)
    return record


def public_status(record: dict[str, Any] | None = None) -> dict[str, Any]:
    r = record or load_approval()
    if not r:
        return {"status": "NONE", "authorized": False, "cancel_authorized": False}
    return {k: r.get(k) for k in (
        "status", "target_date", "expires_at", "account_fingerprint", "allowed_sides",
        "limits", "approved_by", "auth_method", "approved_at", "cancel_authorized",
        "cancel_scope", "last_preflight_at", "block_reason")}


def _block(record: dict[str, Any], reason: str) -> tuple[bool, str]:
    record["status"] = "BLOCKED"
    record["block_reason"] = reason
    _write_atomic(record)
    return False, reason


async def authorize_order(*, db: Any, settings: Any, broker: Any, risk: Any,
                          side: str, now: datetime, symbol: str = "",
                          quantity: int = 0, notional_krw: int = 0,
                          daily_order_count: int = 0) -> tuple[bool, str]:
    """Final fail-closed gate.  Read-only KIS checks run before first order."""
    r = load_approval()
    if not r:
        return False, "ONE_CLICK_APPROVAL_MISSING"
    local = now.astimezone(KST)
    if r.get("target_date") != local.date().isoformat():
        return _block(r, "APPROVAL_DATE_MISMATCH")
    if local.time() < time(9, 5):
        return False, "WAITING_FOR_0905_KST"
    if side.upper() == "BUY" and local.time() >= time(14, 30):
        return False, "BUY_CUTOFF_1430_KST"
    if side.upper() not in set(r.get("allowed_sides") or []):
        return _block(r, "ORDER_SIDE_NOT_APPROVED")
    limits = r.get("limits") or {}
    from app.kis_paper.order_limits import resolve_kis_paper_order_limits
    current_limits = resolve_kis_paper_order_limits(settings)
    approved_order_limit = min(
        int(limits.get("max_order_krw", 0) or 0), current_limits.max_order_krw,
    )
    if int(notional_krw or 0) > approved_order_limit:
        return _block(r, "APPROVED_ORDER_LIMIT_EXCEEDED")
    if side.upper() == "BUY" and int(daily_order_count) >= int(limits.get("daily_buy_count", 0) or 0):
        return _block(r, "APPROVED_DAILY_BUY_COUNT_EXCEEDED")
    if not bool(getattr(settings, "kis_is_paper", False)) or bool(getattr(settings, "enable_live_trading", False)):
        return _block(r, "PAPER_LIVE_MODE_MISMATCH")
    current_fp = account_fingerprint(
        str(getattr(settings, "kis_account_no", "")),
        str(getattr(settings, "kis_product_code", "01")),
    )
    if not hmac.compare_digest(str(r.get("account_fingerprint", "")), current_fp):
        return _block(r, "PAPER_ACCOUNT_CHANGED")
    if bool(getattr(risk, "emergency_stop", False)):
        return _block(r, "EMERGENCY_STOP_ACTIVE")
    newer_stop = db.execute(
        select(EmergencyStopEvent.id).where(
            EmergencyStopEvent.id > int(r.get("emergency_baseline_id", 0)),
            EmergencyStopEvent.enabled.is_(True),
        ).limit(1)
    ).first()
    if newer_stop is not None:
        return _block(r, "NEW_EMERGENCY_STOP_AFTER_APPROVAL")
    if not bool(getattr(settings, "kis_paper_fill_polling", False)):
        return _block(r, "FILL_POLLER_DISABLED")
    try:
        positions = await broker.get_positions()
        orders = await broker.list_today_orders(kst_date=local.date().strftime("%Y%m%d"))
        balance = await broker.get_balance() if side.upper() == "BUY" else None
        buy_capacity = None
        order_price = max(1, int(notional_krw) // max(1, int(quantity)))
        capacity_reader = getattr(broker, "get_orderable_buy_capacity", None)
        if side.upper() == "BUY" and callable(capacity_reader):
            buy_capacity = await capacity_reader(symbol=symbol, price=order_price)
            orderable_cash = min(
                int(buy_capacity["orderable_cash_krw"]),
                int(buy_capacity["no_credit_buy_amount_krw"]),
            )
        else:
            orderable_cash = (
                await broker.get_orderable_cash(symbol=symbol, price=order_price)
                if side.upper() == "BUY" and callable(getattr(broker, "get_orderable_cash", None))
                else None
            )
    except Exception as exc:  # no order is sent on read failure
        return _block(r, f"KIS_PREFLIGHT_FAILED:{type(exc).__name__}")
    if side.upper() == "BUY" and any(
        str(o.get("side", "")).upper() == "BUY"
        and int(o.get("remaining", 0) or 0) > 0 for o in orders
    ):
        return _block(r, "OPEN_ORDER_PRESENT_CANCEL_NOT_AUTHORIZED")
    if side.upper() == "SELL":
        position = next((p for p in (positions or [])
                         if str(getattr(p, "symbol", "")) == symbol), None)
        held = int(getattr(position, "quantity", 0) or 0) if position else 0
        orderable = getattr(position, "sellable_quantity", None) if position else 0
        orderable = held if orderable is None else int(orderable or 0)
        if not symbol or int(quantity or 0) <= 0 or int(quantity) > min(held, orderable):
            return _block(r, "KIS_SELL_QUANTITY_UNVERIFIED")
    if side.upper() == "BUY":
        notional = int(notional_krw or 0)
        effective_unit_price = notional // max(1, int(quantity))
        if buy_capacity is not None:
            kis_qty = int(buy_capacity["no_credit_buy_quantity"])
            calc_price = int(buy_capacity["calculation_price_krw"])
            if int(quantity) <= 0 or calc_price <= 0:
                return _block(r, "KIS_ORDERABLE_CAPACITY_UNVERIFIED")
            if int(quantity) > kis_qty:
                return _block(r, "KIS_NO_CREDIT_BUY_QUANTITY_EXCEEDED")
            effective_unit_price = max(effective_unit_price, calc_price)
        risk_notional = int(quantity) * effective_unit_price
        if risk_notional > approved_order_limit:
            return _block(r, "APPROVED_ORDER_LIMIT_EXCEEDED_AT_KIS_CALC_PRICE")
        positions_by_symbol = {
            str(getattr(p, "symbol", "")): p for p in (positions or [])
            if int(getattr(p, "quantity", 0) or 0) > 0
        }
        current_total = 0
        for p in positions_by_symbol.values():
            qty = int(getattr(p, "quantity", 0) or 0)
            price = int(getattr(p, "market_price", 0) or getattr(p, "avg_price", 0) or 0)
            if price <= 0:
                return _block(r, "KIS_POSITION_VALUE_UNAVAILABLE")
            current_total += qty * price
        if not symbol:
            return _block(r, "PAPER_SYMBOL_REQUIRED")
        symbol_position = positions_by_symbol.get(symbol)
        symbol_price = int(
            getattr(symbol_position, "market_price", 0)
            or getattr(symbol_position, "avg_price", 0)
            or 0
        ) if symbol_position else 0
        symbol_exposure = (
            int(getattr(symbol_position, "quantity", 0) or 0) * symbol_price
            if symbol_position else 0
        )
        symbol_cap = min(
            int(limits.get("per_symbol_krw", 0) or 0),
            current_limits.per_symbol_krw,
        )
        reserve_bps = int(limits.get("cost_reserve_bps", current_limits.cost_reserve_bps))
        cash_required = risk_notional + (risk_notional * reserve_bps + 9_999) // 10_000
        if symbol_exposure + cash_required > symbol_cap:
            return _block(r, "PAPER_SYMBOL_EXPOSURE_EXCEEDED")
        if orderable_cash is None:
            return _block(r, "KIS_ORDERABLE_CASH_UNAVAILABLE")
        available_cash = min(
            int(orderable_cash), int(getattr(balance, "buying_power", 0) or 0),
        )
        if available_cash < cash_required:
            return _block(r, "KIS_ORDERABLE_CASH_INSUFFICIENT")
        if current_total + cash_required > min(
            int(limits.get("total_budget_krw", 0) or 0), current_limits.total_budget_krw,
        ):
            return _block(r, "PAPER_TOTAL_BUDGET_OR_COST_RESERVE_EXCEEDED")
    r["status"] = "ACTIVE"
    r["last_preflight_at"] = datetime.now(timezone.utc).isoformat()
    r["block_reason"] = None
    _write_atomic(r)
    return True, "ONE_CLICK_PAPER_AUTHORIZED"


def cancel_permission(*, bot_owned: bool, is_paper: bool, account_fingerprint_value: str) -> tuple[bool, str]:
    r = load_approval()
    if not r or not r.get("cancel_authorized"):
        return False, "CANCEL_SEPARATE_APPROVAL_REQUIRED"
    if not bot_owned or not is_paper or account_fingerprint_value != r.get("account_fingerprint"):
        return False, "CANCEL_SCOPE_MISMATCH"
    return True, "CANCEL_AUTHORIZED"

