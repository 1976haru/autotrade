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
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db.models import EmergencyStopEvent, PaperSessionApproval

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


# 세션 상태:
#   SCHEDULED / ACTIVE  — 신규 BUY + 청산 SELL 가능 (기존)
#   EXIT_ONLY           — 신규 BUY 차단, 청산 SELL(EOD 포함)만 기존 SELL 검증을 거쳐 허용.
#                         긴급정지·비정상 재시작 후 상태. 새 *명시적* 승인으로만 해제.
#   BLOCKED / 기타      — 기존 의미 (세션 비활성)
# 파일·DB 충돌 시 제한도: SCHEDULED/ACTIVE(0) < EXIT_ONLY(1) < BLOCKED·기타(2),
# 그리고 buy_blocked 는 어느 한쪽이라도 True 면 True.
_USABLE_STATUSES = frozenset({"SCHEDULED", "ACTIVE"})
EXIT_ONLY = "EXIT_ONLY"
_EXIT_STATUSES = _USABLE_STATUSES | {EXIT_ONLY}
EMERGENCY_SESSION_BLOCK_REASON = "AUTO_PAPER_EMERGENCY_STOP"
BUY_BLOCKED_EMERGENCY = "EMERGENCY_STOP_SESSION_BLOCKED"
BUY_BLOCKED_RESTART = "RESTART_REAPPROVAL_REQUIRED"
APPROVAL_DB_UNVERIFIED = "APPROVAL_DB_UNVERIFIED"

# 프로세스 내 신규 BUY 차단 표식 — 영속 저장 실패/재시작 복구 거부 시에도 이 프로세스는
# fail-closed. 새 *명시적* 승인(create_approval)으로만 해제된다. 청산 SELL 은 막지 않는다.
_PROCESS_EMERGENCY_BLOCK = False
_PROCESS_BUY_BLOCK_REASON: str | None = None


class EmergencyBlockPersistError(RuntimeError):
    """세션 BUY 차단을 승인 파일·DB 어디에도 영속 저장하지 못함."""


def _set_process_buy_block(reason: str) -> None:
    global _PROCESS_EMERGENCY_BLOCK, _PROCESS_BUY_BLOCK_REASON
    _PROCESS_EMERGENCY_BLOCK = True
    _PROCESS_BUY_BLOCK_REASON = reason


def _clear_process_buy_block() -> None:
    global _PROCESS_EMERGENCY_BLOCK, _PROCESS_BUY_BLOCK_REASON
    _PROCESS_EMERGENCY_BLOCK = False
    _PROCESS_BUY_BLOCK_REASON = None


def _status_rank(record: dict[str, Any] | None) -> int:
    if not record:
        return -1
    status = record.get("status")
    if status in _USABLE_STATUSES:
        return 0
    return 1 if status == EXIT_ONLY else 2


def _buy_blocked(record: dict[str, Any] | None) -> bool:
    return bool(record) and bool(
        record.get("buy_blocked") or record.get("emergency_blocked")
        or record.get("status") == EXIT_ONLY
    )


def _merge_restrictive(primary: dict[str, Any] | None,
                       other: dict[str, Any] | None) -> dict[str, Any] | None:
    """두 기록 중 더 제한적인 상태를 택하고 BUY 차단 표식은 합친다 (동률이면 primary)."""
    if not other:
        return primary
    if not primary:
        return other
    base = other if _status_rank(other) > _status_rank(primary) else primary
    merged = dict(base)
    for src in (primary, other):
        if _buy_blocked(src) and not merged.get("buy_blocked"):
            merged["buy_blocked"] = True
            merged["buy_block_reason"] = src.get("buy_block_reason") or BUY_BLOCKED_EMERGENCY
        if src.get("emergency_blocked"):
            merged["emergency_blocked"] = True
    return merged


def _read_file_record() -> dict[str, Any] | None:
    try:
        value = json.loads(approval_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _open_default_db() -> Any | None:
    try:
        from app.db.session import SessionLocal
        return SessionLocal()
    except Exception:  # noqa: BLE001 — DB 미구성: 파일 기록만 사용 (기존 동작).
        return None


def _db_record(db: Any, *, target_date: str | None = None,
               fingerprint: str | None = None) -> dict[str, Any] | None:
    query = select(PaperSessionApproval)
    if target_date is not None and fingerprint is not None:
        query = query.where(
            PaperSessionApproval.trading_date == target_date,
            PaperSessionApproval.account_fingerprint == fingerprint,
        )
    row = db.execute(query.order_by(PaperSessionApproval.id.desc()).limit(1)).scalars().first()
    if row is None:
        return None
    value = dict(row.record or {})
    if not value:
        return None
    # 컬럼 status 가 기록 JSON 보다 제한적이면 컬럼을 따른다.
    if str(row.status or "") not in _USABLE_STATUSES and value.get("status") in _USABLE_STATUSES:
        value["status"] = str(row.status)
    return value


def _load_checked(db: Any | None = None) -> tuple[dict[str, Any] | None, bool]:
    """(기록, DB 확인 성공 여부). 파일 + 운영 DB 를 함께 읽어 더 제한적인 기록을 만든다."""
    file_record = _read_file_record()
    own = None
    session = db
    if session is None:
        session = own = _open_default_db()
    try:
        if session is None:
            return file_record, False
        try:
            if file_record is not None:
                db_rec = _db_record(
                    session, target_date=str(file_record.get("target_date", "")),
                    fingerprint=str(file_record.get("account_fingerprint", "")),
                )
                merged = _merge_restrictive(file_record, db_rec)
                if merged != file_record:
                    try:
                        _write_atomic(merged)
                    except OSError:
                        pass
                return merged, True
            value = _db_record(session)
            if value:
                try:
                    _write_atomic(value)
                except OSError:
                    pass
            return value, True
        except Exception:  # noqa: BLE001
            return file_record, False
    finally:
        if own is not None:
            own.close()


def load_approval(db: Any | None = None) -> dict[str, Any] | None:
    """승인 파일 + 운영 DB 를 함께 읽고 *더 제한적인* 기록을 반환한다.

    - 파일이 있으면 같은 (target_date, 계좌) DB 기록과 합쳐 제한적인 상태를 쓴다
      (BLOCKED > EXIT_ONLY > SCHEDULED/ACTIVE, BUY 차단 표식은 OR). DB 가 더
      제한적이면 파일도 그 상태로 맞춘다.
    - 파일이 없거나 손상되면 DB 최신 기록으로 복구한다 (차단 상태도 그대로).
    - ``db`` 가 없으면 기본 DB 세션을 읽기 전용으로 연다. DB 읽기 실패 시 파일
      기록을 반환하되, 신규 BUY 경로(``session_trading_authorized`` /
      ``authorize_order``)는 이 경우 차단한다 (``_load_checked`` 의 검증 플래그).
    """
    return _load_checked(db)[0]


def _db_verified(db: Any | None, record: dict[str, Any] | None) -> bool:
    """이 기록의 DB 대조가 *지금* 가능한가 — 불가하면 신규 BUY 는 fail-closed."""
    if not record:
        return False
    own = None
    session = db
    if session is None:
        session = own = _open_default_db()
    if session is None:
        return False
    try:
        db_rec = _db_record(
            session, target_date=str(record.get("target_date", "")),
            fingerprint=str(record.get("account_fingerprint", "")),
        )
        # DB 쪽이 더 제한적이면(파일이 아직 못 따라온 경우) 검증 실패로 본다.
        if db_rec is not None and (_status_rank(db_rec) > _status_rank(record)
                                   or (_buy_blocked(db_rec) and not _buy_blocked(record))):
            return False
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        if own is not None:
            own.close()


def entry_budget_caps(db: Any, *, fingerprint: str, now: datetime | None = None) -> tuple[int, int]:
    """Return the daily and exposure ceilings authorized for this account and day."""
    from app.core.runtime_config import DEFAULT_DAILY_BUY_LIMIT_KRW
    from app.kis_paper.order_limits import PAPER_TOTAL_BUDGET_KRW

    baseline = (DEFAULT_DAILY_BUY_LIMIT_KRW, PAPER_TOTAL_BUDGET_KRW)
    day = (now or datetime.now(timezone.utc)).astimezone(KST).date().isoformat()
    try:
        record = _db_record(db, target_date=day, fingerprint=fingerprint)
    except Exception:  # noqa: BLE001
        return baseline
    if record is None:
        return baseline
    limits = record.get("limits") or {}
    caps = (int(limits.get("daily_buy_krw", baseline[0])),
            int(limits.get("total_budget_krw", baseline[1])))
    file_record = _read_file_record()
    if (file_record and file_record.get("target_date") == day
            and file_record.get("account_fingerprint") == fingerprint):
        record = _merge_restrictive(record, file_record)
    if record.get("status") not in _USABLE_STATUSES or _buy_blocked(record) or _PROCESS_EMERGENCY_BLOCK:
        return min(caps[0], baseline[0]), min(caps[1], baseline[1])
    return caps


def _persist_approval(db: Any, record: dict[str, Any]) -> None:
    fp = str(record["account_fingerprint"])
    day = str(record["target_date"])
    row = db.execute(
        select(PaperSessionApproval).where(
            PaperSessionApproval.trading_date == day,
            PaperSessionApproval.account_fingerprint == fp,
        )
    ).scalars().first()
    expires = datetime.fromisoformat(str(record["expires_at"]))
    approved = datetime.fromisoformat(str(record["approved_at"]))
    if row is None:
        row = PaperSessionApproval(
            trading_date=day, account_fingerprint=fp,
            status=str(record["status"]), approved_by=str(record["approved_by"]),
            approved_at=approved, expires_at=expires, record=dict(record),
        )
        db.add(row)
    else:
        row.status = str(record["status"])
        row.approved_by = str(record["approved_by"])
        row.approved_at = approved
        row.record = dict(record)
        row.expires_at = expires
    db.commit()


def desktop_token_valid(candidate: str | None) -> bool:
    expected = os.getenv("AUTOTRADE_DESKTOP_AUTH_TOKEN", "")
    return bool(expected and candidate and hmac.compare_digest(expected, candidate))


# 승인을 반영해도 KIS 모의주문이 실제로 나갈 수 없는 설정 — 사유 코드 (UI 정합).
ORDER_PATH_NOT_READY = "ORDER_PATH_NOT_READY"
BLOCKER_PROVIDER_NOT_KIS = "MARKET_DATA_PROVIDER_NOT_KIS"
BLOCKER_BACKGROUND_TICK_DISABLED = "BACKGROUND_TICK_DISABLED"
BLOCKER_KIS_CREDENTIALS_MISSING = "KIS_CREDENTIALS_MISSING"
BLOCKER_FILL_POLLER_DISABLED = "FILL_POLLER_DISABLED"
BLOCKER_SMOKE_MODE_ON = "KIS_PAPER_SMOKE_MODE_ON"


def order_path_blockers(settings: Any) -> list[str]:
    """Pure check: would an approved session actually route KIS PAPER orders?

    Evaluated on the approval-applied view (``ApprovedPaperSettings``) so the
    answer matches what the background driver will see after approval.  It
    only *reports* reasons; every order still passes ``authorize_order`` and
    ``route_order``.  Returns ``[]`` when the order path is ready.
    """
    s = ApprovedPaperSettings(settings)
    codes: list[str] = []
    provider = str(getattr(s, "market_data_provider", "mock") or "mock").strip().lower()
    if provider != "kis":
        codes.append(BLOCKER_PROVIDER_NOT_KIS)
    if not bool(getattr(s, "enable_ai_paper_background_tick", False)):
        codes.append(BLOCKER_BACKGROUND_TICK_DISABLED)
    if not all(str(getattr(s, k, "") or "").strip()
               for k in ("kis_app_key", "kis_app_secret", "kis_account_no")):
        codes.append(BLOCKER_KIS_CREDENTIALS_MISSING)
    if not bool(getattr(s, "kis_paper_fill_polling", False)):
        codes.append(BLOCKER_FILL_POLLER_DISABLED)
    if bool(getattr(s, "kis_paper_smoke_mode", False)):
        codes.append(BLOCKER_SMOKE_MODE_ON)
    return codes


def armed_session_date(settings: Any, *, now: datetime | None = None,
                       db: Any | None = None) -> date | None:
    """Future trading day with a valid approval that the loop may wait for.

    Returns the approval's ``target_date`` only when it is *after* today and
    equals ``next_trading_day(now)``.  Today's approval returns None (the
    normal phase logic applies).  This never authorizes an order:
    ``authorize_order`` still requires ``target_date == today``.
    """
    from app.scheduler.intraday_session import next_trading_day
    now = now or datetime.now(timezone.utc)
    if not session_trading_authorized(settings, db=db):
        return None
    try:
        record = load_approval(db)
    except TypeError:
        record = load_approval()
    try:
        target = datetime.strptime(str((record or {}).get("target_date", "")),
                                   "%Y-%m-%d").date()
    except ValueError:
        return None
    today = now.astimezone(KST).date()
    if target <= today:
        return None
    calendar = _calendar_for(settings)
    return target if target == next_trading_day(now, calendar=calendar) else None


def _persist_buy_block(session: Any | None, record: dict[str, Any], *,
                       buy_reason: str, block_reason: str,
                       extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """기록을 BUY 차단(EXIT_ONLY)으로 바꿔 파일·DB 둘 다에 저장 — {file, db, errors}."""
    record = dict(record)
    if record.get("status") in _EXIT_STATUSES:
        record["status"] = EXIT_ONLY          # 청산 SELL 은 계속 가능
    record.update(buy_blocked=True, buy_block_reason=buy_reason, block_reason=block_reason,
                  buy_blocked_at=datetime.now(timezone.utc).isoformat(), **(extra or {}))
    errors: list[str] = []
    file_ok = db_ok = False
    try:
        _write_atomic(record)
        file_ok = True
    except Exception as exc:  # noqa: BLE001
        errors.append(f"file:{type(exc).__name__}")
    if session is None:
        errors.append("db:UNAVAILABLE")
    else:
        try:
            _persist_approval(session, record)
            db_ok = True
        except Exception as exc:  # noqa: BLE001
            errors.append(f"db:{type(exc).__name__}")
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
    return {"blocked": True, "reason": block_reason, "status": record.get("status"),
            "file": file_ok, "db": db_ok, "errors": errors}


def block_session_for_emergency(db: Any | None = None,
                                reason: str = EMERGENCY_SESSION_BLOCK_REASON) -> dict[str, Any]:
    """긴급정지: 세션을 EXIT_ONLY(신규 BUY 차단, 청산 SELL 허용)로 영속 저장.

    승인 파일과 운영 DB 기록 *둘 다* 에 기록한다 (어느 한쪽만 남아도
    ``load_approval`` 의 제한 우선 규칙으로 재시작 후 차단 유지). 이 프로세스는
    저장 결과와 무관하게 즉시 신규 BUY fail-closed. 둘 다 실패하면
    ``EmergencyBlockPersistError`` — 이 경우 재시작 허가(restore permit)도 기록되지
    않으므로 재시작 후 자동 복구는 거부된다 (``evaluate_restart_restore``).
    EOD/청산 SELL 은 막지 않는다 (당일 청산 원칙 보존). 해제는 새 명시적 승인만.
    """
    _set_process_buy_block(BUY_BLOCKED_EMERGENCY)
    discard_restore_permit()
    own = None
    session = db
    if session is None:
        session = own = _open_default_db()
    try:
        record = load_approval(session)
        if not record:
            return {"blocked": False, "reason": "NO_APPROVAL", "file": False, "db": False,
                    "errors": []}
        result = _persist_buy_block(
            session, record, buy_reason=BUY_BLOCKED_EMERGENCY, block_reason=reason,
            extra={"emergency_blocked": True,
                   "emergency_blocked_at": datetime.now(timezone.utc).isoformat()},
        )
        if not (result["file"] or result["db"]):
            raise EmergencyBlockPersistError(",".join(result["errors"]) or "PERSIST_FAILED")
        return result
    finally:
        if own is not None:
            own.close()


# ── 재시작 복구 허가 (정상 종료 시에만 기록되는 일회용 표식) ──────────────────
RESTORE_PERMIT_FILE = "paper-session-restore-permit.json"


def restore_permit_path() -> Path:
    return approval_path().parent / RESTORE_PERMIT_FILE


def discard_restore_permit() -> bool:
    """허가 삭제 — 성공(또는 원래 없음)이면 True."""
    try:
        restore_permit_path().unlink(missing_ok=True)
        return True
    except OSError:
        return False


def write_restore_permit(settings: Any, *, loop_state: str | None,
                         db: Any | None = None) -> dict[str, Any]:
    """정상 종료(lifespan 종료) 시 호출. 아래가 *모두* 참일 때만 허가를 기록한다.

    - 루프가 RUNNING / WAITING_MARKET (사용자가 멈춘 세션은 자동 재개하지 않음)
    - 이 프로세스에 BUY 차단 표식 없음 (긴급정지 저장 실패 포함)
    - 세션이 신규 BUY 가능 상태이고 DB 대조 성공 (``session_trading_authorized``)
    그 외에는 기존 허가를 지운다. 쓰기 실패 = 허가 없음 = 재시작 시 재승인 (fail-closed).
    """
    state = str(loop_state or "").upper()
    reason = None
    if state not in {"RUNNING", "WAITING_MARKET"}:
        reason = "LOOP_NOT_ACTIVE"
    elif _PROCESS_EMERGENCY_BLOCK:
        reason = "PROCESS_BUY_BLOCKED"
    elif not session_trading_authorized(settings, db=db):
        reason = "SESSION_NOT_AUTHORIZED"
    if reason is not None:
        discard_restore_permit()
        return {"written": False, "reason": reason}
    record = load_approval(db) or {}
    permit = {
        "schema_version": 1,
        "target_date": record.get("target_date"),
        "account_fingerprint": record.get("account_fingerprint"),
        "approved_at": record.get("approved_at"),
        "loop_state": state,
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    path = restore_permit_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(permit, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        discard_restore_permit()
        return {"written": False, "reason": f"WRITE_FAILED:{type(exc).__name__}"}
    return {"written": True, "reason": None}


def _consume_restore_permit() -> tuple[dict[str, Any] | None, str | None]:
    """허가를 읽고 *즉시 삭제* (일회용). 삭제 실패 시 재사용 위험 → 무효 처리."""
    path = restore_permit_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "NO_CLEAN_SHUTDOWN_PERMIT"
    except OSError:
        return None, "PERMIT_UNREADABLE"
    if not discard_restore_permit():
        return None, "PERMIT_NOT_CONSUMABLE"
    try:
        value = json.loads(raw)
    except ValueError:
        return None, "PERMIT_CORRUPT"
    return (value, None) if isinstance(value, dict) else (None, "PERMIT_CORRUPT")


def evaluate_restart_restore(settings: Any, *, risk_emergency: bool,
                             now: datetime | None = None,
                             db: Any | None = None) -> dict[str, Any]:
    """프로세스 시작 시 1회 — 승인 세션을 자동 복구해도 되는지 fail-closed 판단.

    복구 허용 조건: 직전 프로세스가 *정상 종료* 하며 남긴 허가가 있고, 그 허가가 현재
    승인(날짜·계좌·승인시각)과 일치하며, 세션이 신규 BUY 가능 상태로 DB 대조까지 성공.
    하나라도 아니면 자동 복구 금지 + 세션을 EXIT_ONLY(재승인 필요)로 저장 시도 +
    이 프로세스 BUY 차단. (긴급정지 저장 실패·크래시·강제 종료·DB 조회 실패 포함.)
    청산 SELL/EOD 는 EXIT_ONLY 에서도 계속된다.
    """
    permit, permit_reason = _consume_restore_permit()
    record, verified = _load_checked(db)
    status = public_status(record) if record else {"status": "NONE"}
    if status.get("status") not in _EXIT_STATUSES:
        return {"restore": False, "reason": "NO_ACTIVE_SESSION", "armed_for": None}
    if risk_emergency:
        return {"restore": False, "reason": "RISK_EMERGENCY_STOP", "armed_for": None}
    reason = permit_reason
    if reason is None:
        same = all(str(permit.get(k)) == str(record.get(k))
                   for k in ("target_date", "account_fingerprint", "approved_at"))
        if not same:
            reason = "PERMIT_MISMATCH"
        elif not verified:
            reason = APPROVAL_DB_UNVERIFIED
        elif not session_trading_authorized(settings, db=db):
            reason = "SESSION_NOT_AUTHORIZED"
    if reason is None:
        return {"restore": True, "reason": None,
                "armed_for": armed_session_date(settings, now=now, db=db)}
    _set_process_buy_block(BUY_BLOCKED_RESTART)
    persisted = None
    if not _buy_blocked(record):
        own = None
        session = db
        if session is None:
            session = own = _open_default_db()
        try:
            persisted = _persist_buy_block(
                session, record, buy_reason=BUY_BLOCKED_RESTART,
                block_reason=f"RESTART_WITHOUT_RESTORE_PERMIT:{reason}",
            )
        finally:
            if own is not None:
                own.close()
    return {"restore": False, "reason": reason, "armed_for": None, "persisted": persisted}


def _calendar_for(settings: Any):
    from app.scheduler.intraday_session import load_krx_calendar
    path = str(getattr(settings, "krx_calendar_override_path", "") or "") or None
    return load_krx_calendar(path)


def create_approval(*, db: Any, settings: Any, risk: Any, target_date: str, actor: str,
                    explicit_ack: bool, confirmed_daily_buy_krw: int | None = None,
                    confirmed_total_budget_krw: int | None = None) -> dict[str, Any]:
    if not explicit_ack:
        raise ValueError("EXPLICIT_APPROVAL_REQUIRED")
    blockers = order_path_blockers(settings)
    if blockers:
        raise ValueError(f"{ORDER_PATH_NOT_READY}:" + ",".join(blockers))
    target = datetime.strptime(target_date, "%Y-%m-%d").date()
    now_utc = datetime.now(timezone.utc)
    today = now_utc.astimezone(KST).date()
    from app.scheduler.intraday_session import next_trading_day
    next_session = next_trading_day(now_utc, calendar=_calendar_for(settings))
    # 오늘 또는 다음 거래일(KRX 달력)만 — 그 이후 미래는 거부.
    if target not in {today, next_session}:
        raise ValueError("TARGET_DATE_NOT_TODAY_OR_NEXT_DAY")
    if not bool(getattr(settings, "kis_is_paper", False)):
        raise ValueError("PAPER_ACCOUNT_REQUIRED")
    if bool(getattr(settings, "enable_live_trading", False)):
        raise ValueError("LIVE_TRADING_MUST_BE_OFF")
    account = str(getattr(settings, "kis_account_no", "") or "")
    if len(account) < 8:
        raise ValueError("PAPER_ACCOUNT_MISSING")
    fp = account_fingerprint(account, str(getattr(settings, "kis_product_code", "01")))
    from app.core.runtime_config import DEFAULT_DAILY_BUY_LIMIT_KRW
    from app.kis_paper.order_limits import PAPER_TOTAL_BUDGET_KRW, resolve_kis_paper_order_limits

    limits = resolve_kis_paper_order_limits(settings)
    existing = db.execute(
        select(PaperSessionApproval).where(
            PaperSessionApproval.trading_date == target.isoformat(),
            PaperSessionApproval.account_fingerprint == fp,
        )
    ).scalars().first()
    prior = dict(existing.record or {}) if existing is not None else {}
    prior_limits = prior.get("limits") or {}
    prior_daily = int(prior_limits.get("daily_buy_krw", DEFAULT_DAILY_BUY_LIMIT_KRW))
    prior_total = int(prior_limits.get("total_budget_krw", PAPER_TOTAL_BUDGET_KRW))
    for configured, confirmed, threshold in (
        (limits.daily_buy_krw, confirmed_daily_buy_krw, min(prior_daily, DEFAULT_DAILY_BUY_LIMIT_KRW)),
        (limits.total_budget_krw, confirmed_total_budget_krw, min(prior_total, PAPER_TOTAL_BUDGET_KRW)),
    ):
        if confirmed != configured and (confirmed is not None or configured > threshold):
            raise ValueError("PAPER_LIMITS_CONFIRMATION_REQUIRED")
    if existing is not None:
        if (public_status(prior).get("status") in _USABLE_STATUSES
                and str(existing.status or "") in _USABLE_STATUSES
                and not _buy_blocked(prior)
                and not _PROCESS_EMERGENCY_BLOCK
                and limits.daily_buy_krw <= prior_daily
                and limits.total_budget_krw <= prior_total):
            _write_atomic(prior)
            return prior
    latest = db.execute(
        select(EmergencyStopEvent).order_by(EmergencyStopEvent.id.desc())
    ).scalars().first()
    if bool(getattr(risk, "emergency_stop", False)) or (latest is not None and bool(latest.enabled)):
        raise ValueError("EMERGENCY_STOP_ACTIVE")
    approved_at = datetime.now(timezone.utc)
    record = {
        "schema_version": 1,
        "status": "SCHEDULED",
        "target_date": target.isoformat(),
        "expires_at": datetime.combine(target + timedelta(days=1), time(0), KST).astimezone(timezone.utc).isoformat(),
        "account_fingerprint": fp,
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
    _persist_approval(db, record)
    _write_atomic(record)
    _clear_process_buy_block()   # 새 *명시적* 승인만이 BUY 차단을 해제한다.
    return record


def public_status(record: dict[str, Any] | None = None) -> dict[str, Any]:
    r = record or load_approval()
    if not r:
        return {"status": "NONE", "authorized": False, "cancel_authorized": False}
    out = {k: r.get(k) for k in (
        "status", "target_date", "expires_at", "account_fingerprint", "allowed_sides",
        "limits", "approved_by", "auth_method", "approved_at", "cancel_authorized",
        "cancel_scope", "last_preflight_at", "block_reason")}
    out["emergency_blocked"] = bool(r.get("emergency_blocked"))
    out["buy_blocked"] = _buy_blocked(r)
    out["buy_block_reason"] = r.get("buy_block_reason") if _buy_blocked(r) else None
    out["exit_only"] = r.get("status") == EXIT_ONLY
    try:
        today = datetime.now(KST).date().isoformat()
        expires = datetime.fromisoformat(str(r.get("expires_at", "")))
        target_date = str(r.get("target_date", ""))
        if target_date < today or expires <= datetime.now(timezone.utc):
            out.update(status="EXPIRED", authorized=False, block_reason="APPROVAL_DATE_EXPIRED")
            return out
        if target_date > today:
            out.update(authorized=False, block_reason="WAITING_FOR_APPROVED_DATE")
            return out
    except (TypeError, ValueError):
        out.update(status="INVALID", authorized=False, block_reason="APPROVAL_EXPIRY_INVALID")
        return out
    out["authorized"] = r.get("status") == "ACTIVE"
    return out


def session_trading_authorized(settings: Any, *, db: Any | None = None) -> bool:
    """True only for today's explicit approval and the same PAPER account.

    This is the narrow replacement for global AUTO/dry-run switches.  It does
    not authorize an order by itself; ``authorize_order`` still performs all
    fresh broker/risk checks immediately before routing.
    """
    if not bool(getattr(settings, "kis_is_paper", False)):
        return False
    if bool(getattr(settings, "enable_live_trading", False)):
        return False
    if _PROCESS_EMERGENCY_BLOCK:
        return False
    account = str(getattr(settings, "kis_account_no", "") or "")
    if not account:
        return False
    try:
        record = load_approval(db)
    except TypeError:  # compatibility with injected zero-argument test readers
        record = load_approval()
    status = public_status(record)
    if status.get("status") not in _USABLE_STATUSES or _buy_blocked(record):
        return False
    expected = account_fingerprint(
        account, str(getattr(settings, "kis_product_code", "01")),
    )
    if not hmac.compare_digest(str(status.get("account_fingerprint", "")), expected):
        return False
    # DB 대조 불가(조회 실패 등)면 파일의 오래된 SCHEDULED 를 믿지 않는다 — fail-closed.
    return _db_verified(db, record)


def session_exit_authorized(settings: Any, *, db: Any | None = None) -> bool:
    """청산 SELL(EOD 포함) 용 세션 — SCHEDULED/ACTIVE 또는 EXIT_ONLY.

    신규 BUY 권한이 아니다 (``authorize_order`` 가 EXIT_ONLY/BUY 차단 시 BUY 거부).
    위험 축소 경로라 프로세스 BUY 차단 표식·DB 대조 실패로 막지 않는다. 실제 SELL 은
    ``authorize_order`` 의 KIS 보유·주문가능수량 확인 등 기존 검사를 그대로 거친다.
    """
    if not bool(getattr(settings, "kis_is_paper", False)):
        return False
    if bool(getattr(settings, "enable_live_trading", False)):
        return False
    account = str(getattr(settings, "kis_account_no", "") or "")
    if not account:
        return False
    try:
        record = load_approval(db)
    except TypeError:  # compatibility with injected zero-argument test readers
        record = load_approval()
    status = public_status(record)
    if status.get("status") not in _EXIT_STATUSES:
        return False
    expected = account_fingerprint(
        account, str(getattr(settings, "kis_product_code", "01")),
    )
    return hmac.compare_digest(str(status.get("account_fingerprint", "")), expected)


class ApprovedPaperSettings:
    """Read-only settings view enabling PAPER orders for an approved session."""

    def __init__(self, base: Any):
        self._base = base

    def __getattr__(self, name: str) -> Any:
        if name == "enable_kis_paper_auto_trading":
            return True
        if name == "kis_paper_auto_order_dry_run":
            return False
        return getattr(self._base, name)


def effective_session_settings(settings: Any, *, db: Any | None = None) -> Any:
    return ApprovedPaperSettings(settings) if session_trading_authorized(
        settings, db=db,
    ) else settings


def effective_exit_settings(settings: Any, *, db: Any | None = None) -> Any:
    """EOD/청산 전용 — EXIT_ONLY 세션도 PAPER 청산 주문 경로를 연다 (BUY 는 authorize_order 가 거부)."""
    return ApprovedPaperSettings(settings) if session_exit_authorized(
        settings, db=db,
    ) else settings


def _block(record: dict[str, Any], reason: str) -> tuple[bool, str]:
    if record.get("status") != EXIT_ONLY:
        record["status"] = "BLOCKED"
    record["block_reason"] = reason
    _write_atomic(record)
    return False, reason


async def authorize_order(*, db: Any, settings: Any, broker: Any, risk: Any,
                          side: str, now: datetime, symbol: str = "",
                          quantity: int = 0, notional_krw: int = 0,
                          daily_order_count: int = 0) -> tuple[bool, str]:
    """Final fail-closed gate.  Read-only KIS checks run before first order."""
    try:
        r = load_approval(db)
    except TypeError:  # compatibility with injected zero-argument test readers
        r = load_approval()
    if not r:
        return False, "ONE_CLICK_APPROVAL_MISSING"
    if side.upper() == "BUY":
        # 신규 BUY 차단 세션(EXIT_ONLY) / 프로세스 차단 — 새 명시적 승인 전까지 BUY 불가.
        #   청산 SELL 은 아래 기존 검사를 그대로 거쳐 계속 허용 (당일 청산 원칙).
        if _buy_blocked(r):
            return False, str(r.get("buy_block_reason") or BUY_BLOCKED_EMERGENCY)
        if _PROCESS_EMERGENCY_BLOCK:
            return False, _PROCESS_BUY_BLOCK_REASON or BUY_BLOCKED_EMERGENCY
        if not _db_verified(db, r):
            return False, APPROVAL_DB_UNVERIFIED
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
    if r.get("status") in _USABLE_STATUSES or r.get("status") == "BLOCKED":
        if not _buy_blocked(r):
            r["status"] = "ACTIVE"
            r["block_reason"] = None
    r["last_preflight_at"] = datetime.now(timezone.utc).isoformat()
    _write_atomic(r)
    return True, "ONE_CLICK_PAPER_AUTHORIZED"


def cancel_permission(*, bot_owned: bool, is_paper: bool, account_fingerprint_value: str) -> tuple[bool, str]:
    r = load_approval()
    if not r or not r.get("cancel_authorized"):
        return False, "CANCEL_SEPARATE_APPROVAL_REQUIRED"
    if not bot_owned or not is_paper or account_fingerprint_value != r.get("account_fingerprint"):
        return False, "CANCEL_SCOPE_MISMATCH"
    return True, "CANCEL_AUTHORIZED"

