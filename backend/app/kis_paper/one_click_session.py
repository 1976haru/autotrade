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
from dataclasses import dataclass
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


def latest_emergency_id(db: Any) -> int:
    row = db.execute(select(EmergencyStopEvent).order_by(EmergencyStopEvent.id.desc())).scalars().first()
    return int(row.id) if row is not None else 0


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
    approved_at = datetime.now(timezone.utc)
    # The click is the explicit authenticated operator action.  Record the OFF
    # transition now; never defer/auto-release it at 09:05.  Any later safety
    # stop gets a larger event id and invalidates this approval.
    from app.risk.emergency_stop import KillSwitchLevel, apply_kill_switch_to_risk
    from app.risk.kill_switch_policy import KILL_SWITCH_POLICY_VERSION
    db.add(EmergencyStopEvent(
        enabled=False, level=KillSwitchLevel.OFF.value,
        policy_version=KILL_SWITCH_POLICY_VERSION,
        decided_by=actor[:64], reason_code="operator_one_click_paper_approval",
        note=f"authenticated PAPER approval for {target.isoformat()}",
    ))
    db.flush()
    apply_kill_switch_to_risk(risk, KillSwitchLevel.OFF)
    record = {
        "schema_version": 1,
        "status": "SCHEDULED",
        "target_date": target.isoformat(),
        "expires_at": datetime.combine(target + timedelta(days=1), time(0), KST).astimezone(timezone.utc).isoformat(),
        "account_fingerprint": account_fingerprint(account, str(getattr(settings, "kis_product_code", "01"))),
        "allowed_sides": ["BUY", "SELL"],
        "limits": {
            "max_positions": 5, "per_symbol_krw": 1_000_000,
            "max_order_krw": 1_000_000, "daily_buy_count": 10,
            "daily_buy_krw": 10_000_000, "tick_buy_count": 1,
            "buy_cutoff": "14:30", "stop_loss_pct": 1.5,
            "take_profit_pct": 2.5,
        },
        "approved_by": actor[:128],
        "auth_method": "WINDOWS_DESKTOP_SESSION",
        "approved_at": approved_at.isoformat(),
        "emergency_baseline_id": latest_emergency_id(db),
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
                          side: str, now: datetime, notional_krw: int = 0,
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
    if int(notional_krw or 0) > int(limits.get("max_order_krw", 0) or 0):
        return _block(r, "APPROVED_ORDER_LIMIT_EXCEEDED")
    if side.upper() == "BUY" and int(daily_order_count) >= int(limits.get("daily_buy_count", 0) or 0):
        return _block(r, "APPROVED_DAILY_BUY_COUNT_EXCEEDED")
    if not bool(getattr(settings, "kis_is_paper", False)) or bool(getattr(settings, "enable_live_trading", False)):
        return _block(r, "PAPER_LIVE_MODE_MISMATCH")
    current_fp = account_fingerprint(str(getattr(settings, "kis_account_no", "")), str(getattr(settings, "kis_product_code", "01")))
    if not hmac.compare_digest(str(r.get("account_fingerprint", "")), current_fp):
        return _block(r, "PAPER_ACCOUNT_CHANGED")
    if bool(getattr(risk, "emergency_stop", False)):
        return _block(r, "EMERGENCY_STOP_ACTIVE")
    latest = db.execute(select(EmergencyStopEvent).order_by(EmergencyStopEvent.id.desc())).scalars().first()
    if latest is not None and int(latest.id) > int(r.get("emergency_baseline_id", 0)) and bool(latest.enabled):
        return _block(r, "NEW_EMERGENCY_STOP_AFTER_APPROVAL")
    if not bool(getattr(settings, "kis_paper_fill_polling", False)):
        return _block(r, "FILL_POLLER_DISABLED")
    try:
        await broker.get_positions()
        orders = await broker.list_today_orders(kst_date=local.date().strftime("%Y%m%d"))
    except Exception as exc:  # no order is sent on read failure
        return _block(r, f"KIS_PREFLIGHT_FAILED:{type(exc).__name__}")
    if any(int(o.get("remaining", 0) or 0) > 0 for o in orders):
        return _block(r, "OPEN_ORDER_PRESENT_CANCEL_NOT_AUTHORIZED")
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

