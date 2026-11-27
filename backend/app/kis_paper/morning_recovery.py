"""Fail-closed recovery of the 2026-11-27 KIS PAPER EOD incident.

Only the recorded, account-scoped program holdings can be submitted through
the existing EOD -> auto executor -> route_order -> risk/permission/executor
path. A separate persisted verification releases the incident BUY gate; the
prior FLATTEN_INCOMPLETE row is never rewritten.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError

from app.kis_paper.eod_liquidation import (
    EodState, _broker_is_kis_paper, _naive_utc, _submit_sells, eod_account_key,
    get_state_row, reconcile,
)
from app.scheduler.intraday_session import CalendarStatus, IntradayPhase, IntradayPolicy
from app.scheduler.market_clock import to_kst

log = logging.getLogger("autotrade.kis_paper.morning_recovery")

INCIDENT_DATE = "2026-11-27"
RECOVERY_DATE = "2026-11-30"
INCIDENT_HOLDINGS = {"001740": 155, "103140": 16, "267260": 1}
VERIFIED = "VERIFIED"
_STEP_LOCK = asyncio.Lock()


def get_verification(db: Any, *, incident_date: str = INCIDENT_DATE,
                     recovery_date: str = RECOVERY_DATE,
                     account_key: str | None = None) -> Any | None:
    from app.db.models import MorningRecoveryVerification
    key = eod_account_key(account_key)
    return (db.query(MorningRecoveryVerification)
            .filter(MorningRecoveryVerification.incident_date == incident_date)
            .filter(MorningRecoveryVerification.recovery_date == recovery_date)
            .filter(MorningRecoveryVerification.account_key == key).first())


def recovery_buy_block_reason(db: Any, *, now: datetime,
                              account_key: str | None = None) -> str | None:
    """Return the unresolved incident date unless same-account proof exists today."""
    from app.db.models import EodLiquidationState, MorningRecoveryVerification
    from app.kis_paper.account_scope import current_account_scope, kis_scope_required

    scope = account_key if account_key is not None else current_account_scope()
    if scope is None and kis_scope_required():
        return "ACCOUNT_SCOPE_UNKNOWN"
    today = to_kst(now).date().isoformat()
    key = scope or ""
    rows = (db.query(EodLiquidationState)
            .filter(EodLiquidationState.trading_date < today)
            .filter(EodLiquidationState.account_key == key)
            .order_by(EodLiquidationState.trading_date.desc()).all())
    for incident in rows:
        if incident.state not in {EodState.FLATTEN_INCOMPLETE.value, EodState.BLOCKED.value}:
            continue
        proof = (db.query(MorningRecoveryVerification)
                 .filter_by(incident_date=incident.trading_date,
                            recovery_date=today, account_key=key, status=VERIFIED).first())
        if proof is None or proof.verified_at is None:
            return incident.trading_date
    return None


def _persist(db: Any, row: Any, *, status: str, now: datetime,
             reason: str | None = None, summary: dict[str, Any] | None = None,
             verified: bool = False) -> None:
    row.status = status
    row.updated_at = _naive_utc(now)
    row.reason_code = reason
    if summary is not None:
        row.summary = summary
    row.verified_at = _naive_utc(now) if verified else None
    db.commit()


def _get_or_create(db: Any, *, now: datetime, key: str) -> Any:
    from app.db.models import MorningRecoveryVerification
    row = get_verification(db, account_key=key)
    if row is not None:
        return row
    row = MorningRecoveryVerification(
        incident_date=INCIDENT_DATE, recovery_date=RECOVERY_DATE,
        account_key=key, status="PENDING", created_at=_naive_utc(now),
        updated_at=_naive_utc(now), summary={},
    )
    db.add(row)
    try:
        db.commit()
        return row
    except IntegrityError:
        db.rollback()
        return get_verification(db, account_key=key)


def _acquire(db: Any, row: Any, *, now: datetime, owner: str, ttl: int) -> bool:
    from app.db.models import MorningRecoveryVerification
    n = _naive_utc(now)
    result = db.execute(
        update(MorningRecoveryVerification)
        .where(MorningRecoveryVerification.id == row.id)
        .where(or_(MorningRecoveryVerification.lease_owner.is_(None),
                   MorningRecoveryVerification.lease_owner == owner,
                   MorningRecoveryVerification.lease_expires_at.is_(None),
                   MorningRecoveryVerification.lease_expires_at < n))
        .values(lease_owner=owner, lease_expires_at=n + timedelta(seconds=max(10, ttl)))
    )
    db.commit()
    return int(result.rowcount or 0) == 1


def _incident_baseline(db: Any, key: str) -> tuple[Any | None, str | None]:
    incident = get_state_row(db, INCIDENT_DATE, key)
    if incident is None or incident.state != EodState.FLATTEN_INCOMPLETE.value:
        return None, "RECOVERY_INCIDENT_NOT_FOUND"
    if incident.is_live_authorization:
        return None, "RECOVERY_INCIDENT_NOT_PAPER"
    summary = incident.summary or {}
    symbols = summary.get("symbols") or {}
    baseline = {s: int((symbols.get(s) or {}).get("managed_qty", 0) or 0)
                for s in INCIDENT_HOLDINGS}
    if baseline != INCIDENT_HOLDINGS:
        return None, "RECOVERY_INCIDENT_EVIDENCE_MISMATCH"
    return incident, None


async def run_morning_recovery_step(
    *, now: datetime | None = None, settings: Any = None,
    session_factory: Callable[[], Any] | None = None, broker: Any = None,
    risk: Any = None, route_order_fn: Any = None,
    broker_is_kis_paper: bool | None = None,
) -> dict[str, Any]:
    """Run one guarded recovery cycle; at most one new symbol SELL per cycle."""
    async with _STEP_LOCK:
        return await _run_locked(now=now, settings=settings, session_factory=session_factory,
                                 broker=broker, risk=risk, route_order_fn=route_order_fn,
                                 broker_is_kis_paper=broker_is_kis_paper)


async def _run_locked(*, now, settings, session_factory, broker, risk,
                      route_order_fn, broker_is_kis_paper) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if settings is None:
        from app.core.config import get_settings
        settings = get_settings()
    policy = IntradayPolicy.from_settings(settings)
    phase = policy.phase(now)
    base = {"incident_date": INCIDENT_DATE, "recovery_date": RECOVERY_DATE,
            "phase": phase.value, "orders_submitted_this_step": 0,
            "is_live_authorization": False, "buy_blocked": True}
    if to_kst(now).date().isoformat() != RECOVERY_DATE:
        return {**base, "status": "IDLE", "reason_code": "RECOVERY_DATE_MISMATCH"}
    if policy.calendar.status(to_kst(now).date()) != CalendarStatus.TRADING_DAY:
        return {**base, "status": "BLOCKED", "reason_code": "RECOVERY_CALENDAR_UNCONFIRMED"}
    local_time = to_kst(now).time()
    if local_time < datetime.strptime("09:05", "%H:%M").time() or phase != IntradayPhase.ENTRY_OPEN:
        return {**base, "status": "WAITING", "reason_code": "RECOVERY_OUTSIDE_NORMAL_ORDER_WINDOW"}
    if not bool(getattr(settings, "kis_is_paper", False)):
        return {**base, "status": "BLOCKED", "reason_code": "RECOVERY_NOT_KIS_PAPER"}
    if bool(getattr(settings, "enable_live_trading", False)):
        return {**base, "status": "BLOCKED", "reason_code": "RECOVERY_LIVE_ENABLED"}

    if session_factory is None:
        from app.db.session import SessionLocal
        session_factory = SessionLocal
    db = session_factory()
    row = None
    owner = f"morning-recovery:{__import__('os').getpid()}"
    try:
        from app.kis_paper.account_scope import current_account_scope
        key = current_account_scope()
        if not key:
            return {**base, "status": "BLOCKED", "reason_code": "ACCOUNT_SCOPE_UNKNOWN"}
        row = _get_or_create(db, now=now, key=key)
        if row.status == VERIFIED and row.verified_at is not None:
            return {**base, "status": VERIFIED, "reason_code": None,
                    "verified_at": row.verified_at.isoformat(), "summary": row.summary or {},
                    "buy_blocked": False}
        incident, incident_error = _incident_baseline(db, key)
        if incident_error:
            _persist(db, row, status="BLOCKED", now=now, reason=incident_error)
            return {**base, "status": "BLOCKED", "reason_code": incident_error}

        from app.kis_paper.one_click_session import (
            effective_exit_settings, load_approval, public_status, session_exit_authorized,
        )
        approval = load_approval(db)
        approval_status = public_status(approval)
        if (not session_exit_authorized(settings, db=db)
                or approval_status.get("target_date") != RECOVERY_DATE
                or "SELL" not in set(approval_status.get("allowed_sides") or [])):
            code = "RECOVERY_SELL_APPROVAL_INVALID"
            _persist(db, row, status="BLOCKED", now=now, reason=code)
            return {**base, "status": "BLOCKED", "reason_code": code}
        settings = effective_exit_settings(settings, db=db)
        from app.auto_paper.paper_auto_mode import resolve_paper_auto_mode
        mode = resolve_paper_auto_mode(settings)
        if mode.mode == "VIRTUAL_ONLY" or not mode.kis_realtime or mode.dry_run:
            code = "RECOVERY_PAPER_ORDER_PATH_NOT_ARMED"
            _persist(db, row, status="BLOCKED", now=now, reason=code)
            return {**base, "status": "BLOCKED", "reason_code": code}
        if broker is None:
            from app.api.deps import get_kis_paper_broker
            broker = get_kis_paper_broker(settings)
        if risk is None:
            from app.api.deps import get_risk_manager
            risk = get_risk_manager()
        if broker_is_kis_paper is None:
            broker_is_kis_paper = _broker_is_kis_paper(broker)
        if not broker_is_kis_paper:
            code = "RECOVERY_BROKER_NOT_KIS_PAPER"
            _persist(db, row, status="BLOCKED", now=now, reason=code)
            return {**base, "status": "BLOCKED", "reason_code": code}
        if not _acquire(db, row, now=now, owner=owner,
                        ttl=int(getattr(settings, "ai_paper_tick_interval_seconds", 30) or 30) * 2):
            return {**base, "status": row.status, "reason_code": "RECOVERY_LEASE_HELD"}

        rec = await reconcile(db, broker, trading_date=RECOVERY_DATE)
        if not rec.ok:
            _persist(db, row, status="BLOCKED", now=now, reason=rec.error_code,
                     summary={"last_reconcile_ok": False})
            return {**base, "status": "BLOCKED", "reason_code": rec.error_code}
        symbols = rec.symbols
        unexpected = {s: int(v.get("managed_qty", 0) or 0) for s, v in symbols.items()
                      if int(v.get("managed_qty", 0) or 0) > 0 and s not in INCIDENT_HOLDINGS}
        oversized = {s: int((symbols.get(s) or {}).get("bot_net", 0) or 0)
                     for s, cap in INCIDENT_HOLDINGS.items()
                     if int((symbols.get(s) or {}).get("bot_net", 0) or 0) > cap}
        if unexpected or oversized:
            code = "RECOVERY_OWNERSHIP_SCOPE_MISMATCH"
            summary = {"unexpected_program_holdings": unexpected,
                       "incident_symbol_ledger": oversized}
            _persist(db, row, status="BLOCKED", now=now, reason=code, summary=summary)
            return {**base, "status": "BLOCKED", "reason_code": code, "summary": summary}
        target_by_symbol = row.summary.get("target_ledger_qty_by_symbol") if row.summary else None
        if not isinstance(target_by_symbol, dict):
            target_by_symbol = {
                sym: min(INCIDENT_HOLDINGS[sym],
                         int((symbols.get(sym) or {}).get("bot_net", 0) or 0))
                for sym in INCIDENT_HOLDINGS
            }
            row.summary = {**(row.summary or {}),
                           "target_ledger_qty_by_symbol": target_by_symbol}
            db.commit()
        if rec.program_open_buys or int(rec.totals.get("unconfirmed_eod_qty", 0) or 0):
            code = "RECOVERY_OPEN_OR_UNKNOWN_PROGRAM_ORDER"
            _persist(db, row, status="WAITING", now=now, reason=code,
                     summary={"program_open_buys": rec.program_open_buys,
                              "totals": rec.totals})
            return {**base, "status": "WAITING", "reason_code": code,
                    "summary": {"totals": rec.totals}}
        if int(rec.totals.get("account_open_order_qty", 0) or 0) > 0:
            code = "RECOVERY_ACCOUNT_OPEN_ORDERS_PRESENT"
            _persist(db, row, status="WAITING", now=now, reason=code,
                     summary={"totals": rec.totals})
            return {**base, "status": "WAITING", "reason_code": code,
                    "summary": {"totals": rec.totals}}

        active = {s: dict(info) for s, info in symbols.items() if s in INCIDENT_HOLDINGS}
        for sym, info in active.items():
            info["managed_qty"] = min(int(info.get("managed_qty", 0) or 0), INCIDENT_HOLDINGS[sym])
        pending = sum(int(v.get("managed_qty", 0) or 0) for v in active.values())
        submitted = 0
        if pending:
            rec.symbols = active
            max_attempts = max(1, int(getattr(settings, "kis_paper_eod_max_attempts_per_symbol", 3) or 3))
            submitted = await _submit_sells(
                db, rec=rec, summary={}, settings=settings, broker=broker, risk=risk,
                route_order_fn=route_order_fn, now=now, trading_date=RECOVERY_DATE,
                max_attempts=max_attempts, dry_run=False,
                broker_is_kis_paper=True, credentials_present=None,
                emergency=bool(getattr(risk, "emergency_stop", False)), max_symbols=1,
            )
            # Always reconcile after the attempt. An accepted order is not proof
            # of a fill; an unknown result is persisted and blocks retries.
            rec = await reconcile(db, broker, trading_date=RECOVERY_DATE)
            if not rec.ok:
                _persist(db, row, status="WAITING", now=now,
                         reason=rec.error_code, summary={"last_reconcile_ok": False,
                                                        "orders_submitted": submitted})
                return {**base, "status": "WAITING", "reason_code": rec.error_code,
                        "orders_submitted_this_step": submitted}

        evidence = {
            "last_reconcile_ok": bool(rec.ok),
            "broker_managed_qty": int(rec.totals.get("managed_qty", 0) or 0),
            "program_ledger_qty": int(rec.totals.get("ledger_qty", 0) or 0),
            "account_open_order_qty": int(rec.totals.get("account_open_order_qty", 0) or 0),
            "program_open_buy_qty": int(rec.totals.get("program_open_buy_qty", 0) or 0),
            "open_sell_qty": int(rec.totals.get("open_sell_qty", 0) or 0),
            "unconfirmed_eod_qty": int(rec.totals.get("unconfirmed_eod_qty", 0) or 0),
            "eod_filled_qty": int(rec.totals.get("eod_filled_qty", 0) or 0),
            "target_ledger_qty_by_symbol": target_by_symbol,
            "recovery_filled_qty_by_symbol": {
                s: sum(int(o.get("filled", 0) or 0)
                       for o in ((rec.symbols.get(s) or {}).get("eod_orders") or []))
                for s in INCIDENT_HOLDINGS
            },
            "symbols": {s: {"managed_qty": int((rec.symbols.get(s) or {}).get("managed_qty", 0) or 0),
                             "kis_hldg": int((rec.symbols.get(s) or {}).get("kis_hldg", 0) or 0),
                             "bot_net": int((rec.symbols.get(s) or {}).get("bot_net", 0) or 0)}
                        for s in INCIDENT_HOLDINGS},
        }
        success = (
            rec.ok and not mode.dry_run
            and evidence["broker_managed_qty"] == 0
            and evidence["program_ledger_qty"] == 0
            and evidence["account_open_order_qty"] == 0
            and evidence["program_open_buy_qty"] == 0
            and evidence["open_sell_qty"] == 0
            and evidence["unconfirmed_eod_qty"] == 0
            and all(evidence["recovery_filled_qty_by_symbol"][s]
                    >= int(target_by_symbol.get(s, 0) or 0)
                    for s in INCIDENT_HOLDINGS)
            and all(v["managed_qty"] == 0 and v["bot_net"] == 0
                    for v in evidence["symbols"].values())
        )
        if success:
            _persist(db, row, status=VERIFIED, now=now, summary=evidence, verified=True)
            return {**base, "status": VERIFIED, "reason_code": None,
                    "verified_at": now.isoformat(), "summary": evidence,
                    "buy_blocked": False,
                    "orders_submitted_this_step": submitted}
        code = "RECOVERY_AWAITING_FILLS_OR_RECONCILIATION"
        _persist(db, row, status="WAITING", now=now, reason=code, summary=evidence)
        return {**base, "status": "WAITING", "reason_code": code, "summary": evidence,
                "orders_submitted_this_step": submitted,
                "buy_blocked": True}
    except Exception as exc:  # Guardian must continue; BUY remains gated.
        try:
            db.rollback()
            if row is not None:
                _persist(db, row, status="BLOCKED", now=now,
                         reason=f"RECOVERY_STEP_ERROR:{type(exc).__name__}")
        except Exception:  # noqa: BLE001
            pass
        log.warning("[morning-recovery] step failed: %s", type(exc).__name__)
        return {**base, "status": "BLOCKED",
                "reason_code": f"RECOVERY_STEP_ERROR:{type(exc).__name__}"}
    finally:
        try:
            if row is not None and row.lease_owner == owner:
                row.lease_owner = None
                row.lease_expires_at = None
                db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        db.close()


__all__ = ["INCIDENT_DATE", "RECOVERY_DATE", "INCIDENT_HOLDINGS", "VERIFIED",
           "get_verification", "recovery_buy_block_reason", "run_morning_recovery_step"]
