"""KIS-PAPER-INTRADAY-FLAT-BY-CLOSE-V1 — 장마감 당일 청산(EOD) coordinator.

정책: 프로그램(kis_paper_auto) 관리 보유는 overnight 0 을 *목표* 로 한다.
- 14:30 (buy_cutoff) ~      : BUY_CUTOFF_ACTIVE — 신규 BUY 0 (route_order 가 강제),
                              잔고/미체결 read-only 대조만.
- 15:15 (flatten_start) ~   : FLATTENING — 미청산 수량을 *기존 sanctioned 경로* 로 매도
                              (execute_kis_paper_auto_order → route_order → RiskManager →
                              PermissionGate → OrderExecutor → KisBrokerAdapter[is_paper]).
- 15:20 (auction_start) ~   : 종가 단일가. KIS *모의* 서버의 단일가 접수가 공식 문서로
                              확인되지 않아 기본은 신규 주문 0 (플래그로만 허용) →
                              WAITING_FOR_FILLS.
- 15:30 (close) ~           : 최종 대조. KIS 잔고의 *프로그램 관리 수량 0* + 관리 종목
                              미체결 SELL 0 + 프로그램 미체결 BUY 0 일 때만 FLAT_CONFIRMED.
                              그 외(잔여 보유·미체결·조회 실패)는 FLATTEN_INCOMPLETE +
                              CRITICAL 이벤트 + 운영자 확인 전까지 다음 거래일 신규 BUY 차단.

안전 원칙:
- 주문은 오직 execute_kis_paper_auto_order(→ route_order) 로만 — broker.place_order 직접
  호출 0건. 긴급정지·실거래 OFF·KIS paper·자격·시세출처(KIS)·RiskManager·OrderGuard
  어느 것도 우회하지 않는다. EMERGENCY_STOP 이면 주문 0 + BLOCKED(수동 대응 필요).
- 청산 대상 = 소유권 원장(kis_paper_auto 순체결) 과 KIS 실보유의 min. 수동/타 모듈 보유는
  절대 매도하지 않는다. 원장 조회 실패(소유권 불명) → BLOCKED + 알림 (fail-closed).
- OrderResult.RECEIVED ≠ 청산 완료. 완료는 KIS 잔고/일별체결 대조로만 판정.
- 중복 매도 방지: (1) DB lease (프로세스/중복 start 간 단일 실행), (2) 종목별 미체결 SELL
  잔량 또는 KIS 미확인 EOD 주문이 있으면 추가 매도 0, (3) 결정적 client_order_id
  `eod-YYYYMMDD-SYMBOL-N` → route_order idempotency(DuplicateOrderError), (4) 순보유는
  KIS 체결수량으로 갱신해 잔고 반영 지연 중에도 재매도 0, (5) 종목당 시도 상한.
- 재시작 복구: 상태·시도·주문번호는 DB(eod_liquidation_state + order_audit_log) 에서 복원.
- 취소 주문(order-rvsecncl) 은 미구현 — 대기 주문은 *취소하지 않고* 보고만 한다.

본 모듈은 broker 를 *주입받거나* deps 에서 lazy 로 얻어 read-only 메서드
(get_positions / list_today_orders / get_price)만 직접 호출한다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from enum import StrEnum
from typing import Any, Callable, Optional

from app.kis_paper.auto_executor import (
    KisPaperAutoDecision,
    execute_kis_paper_auto_order,
)
from app.scheduler.intraday_session import (
    CalendarStatus,
    IntradayPhase,
    IntradayPolicy,
    KrxCalendar,
    buy_reason_message_ko,
)
from app.scheduler.market_clock import to_kst

_log = logging.getLogger("autotrade.kis_paper.eod")


class EodState(StrEnum):
    IDLE               = "IDLE"
    BUY_CUTOFF_ACTIVE  = "BUY_CUTOFF_ACTIVE"
    FLATTENING         = "FLATTENING"
    WAITING_FOR_FILLS  = "WAITING_FOR_FILLS"
    FLAT_CONFIRMED     = "FLAT_CONFIRMED"
    FLATTEN_INCOMPLETE = "FLATTEN_INCOMPLETE"
    BLOCKED            = "BLOCKED"


# reason codes (API / UI / log 정합).
EOD_NOT_TRADING_DAY        = "EOD_NOT_TRADING_DAY"
EOD_BEFORE_CUTOFF          = "EOD_BEFORE_CUTOFF"
EOD_NOT_KIS_PAPER_MODE     = "EOD_NOT_KIS_PAPER_MODE"
EOD_BROKER_NOT_KIS_PAPER   = "EOD_BROKER_NOT_KIS_PAPER"
EOD_LIVE_TRADING_ENABLED   = "EOD_LIVE_TRADING_ENABLED"
EOD_EMERGENCY_STOP         = "EOD_EMERGENCY_STOP_MANUAL_ACTION_REQUIRED"
EOD_DISABLED               = "EOD_FLATTEN_DISABLED"
EOD_LOOP_STOPPED           = "EOD_LOOP_STOPPED_FLATTEN_DISABLED"
EOD_OWNERSHIP_UNKNOWN      = "EOD_OWNERSHIP_UNKNOWN"
EOD_POSITIONS_FAILED       = "EOD_POSITIONS_QUERY_FAILED"
EOD_ORDERS_FAILED          = "EOD_ORDERS_QUERY_FAILED"
EOD_LEASE_HELD             = "EOD_LEASE_HELD_BY_OTHER"
EOD_DRY_RUN                = "EOD_DRY_RUN_NO_ORDERS"
EOD_AUCTION_ORDERS_OFF     = "EOD_CLOSING_AUCTION_ORDERS_DISABLED"
EOD_IN_PROGRESS            = "EOD_FLATTEN_IN_PROGRESS"
EOD_ALL_FLAT_PENDING_FINAL = "EOD_FLAT_PENDING_FINAL_RECONCILE"
EOD_FLAT                   = "EOD_FLAT_CONFIRMED"
EOD_POSITIONS_REMAIN       = "EOD_POSITIONS_REMAIN"
EOD_OPEN_ORDERS_REMAIN     = "EOD_OPEN_ORDERS_REMAIN"
EOD_RECONCILE_FAILED       = "EOD_FINAL_RECONCILE_FAILED"

_MESSAGES_KO = {
    EOD_NOT_TRADING_DAY:      "휴장일/주말 — 장마감 청산 대상이 아닙니다.",
    EOD_BEFORE_CUTOFF:        "신규 매수 마감 전입니다.",
    EOD_NOT_KIS_PAPER_MODE:   "KIS 실시간 모의 자동매매 모드가 아니라 장마감 청산을 실행하지 않습니다.",
    EOD_BROKER_NOT_KIS_PAPER: "브로커가 KIS 모의투자 어댑터가 아니라 청산 주문을 보내지 않습니다.",
    EOD_LIVE_TRADING_ENABLED: "실거래 플래그가 켜져 있어 차단합니다 (KIS 모의 전용 기능).",
    EOD_EMERGENCY_STOP:       "긴급정지 ON — 자동 청산을 하지 않습니다. 남은 보유는 운영자가 직접 정리해야 합니다.",
    EOD_DISABLED:             "장마감 자동 청산이 꺼져 있습니다 (kis_paper_eod_flatten_enabled=false).",
    EOD_LOOP_STOPPED:         "자동매매 루프가 정지 상태이고 정지 중 청산이 꺼져 있습니다.",
    EOD_OWNERSHIP_UNKNOWN:    "프로그램 보유 원장 조회 실패 — 소유권이 불명확해 청산하지 않습니다(수동 확인 필요).",
    EOD_POSITIONS_FAILED:     "KIS 잔고 조회 실패 — 이번 단계 주문 0, 다음 단계 재시도.",
    EOD_ORDERS_FAILED:        "KIS 주문/체결 조회 실패 — 미체결을 몰라 중복 매도 방지를 위해 주문 0, 재시도.",
    EOD_LEASE_HELD:           "다른 프로세스가 장마감 청산을 진행 중입니다 (중복 실행 방지).",
    EOD_DRY_RUN:              "dry-run 모드 — 청산 계획만 기록, KIS 주문 전송 0건.",
    EOD_AUCTION_ORDERS_OFF:   "종가 단일가(15:20~15:30) 신규 청산 주문은 꺼져 있습니다 — 체결 대기/대조만.",
    EOD_IN_PROGRESS:          "장마감 청산 진행 중.",
    EOD_ALL_FLAT_PENDING_FINAL: "프로그램 보유 0 — 15:30 최종 대조 대기.",
    EOD_FLAT:                 "최종 대조 완료 — 프로그램 보유 0, 미체결 0 (FLAT_CONFIRMED).",
    EOD_POSITIONS_REMAIN:     "장 마감 후에도 프로그램 보유가 남았습니다 (FLATTEN_INCOMPLETE) — 운영자 확인 필요.",
    EOD_OPEN_ORDERS_REMAIN:   "장 마감 후에도 미체결 주문이 남았습니다 (FLATTEN_INCOMPLETE) — 운영자 확인 필요.",
    EOD_RECONCILE_FAILED:     "최종 잔고/체결 대조 실패 — 청산 완료를 확인할 수 없습니다 (FLATTEN_INCOMPLETE).",
}


def eod_message_ko(code: str | None) -> str:
    return _MESSAGES_KO.get(code or "", code or "")


_FINAL_STATES = frozenset({EodState.FLAT_CONFIRMED, EodState.FLATTEN_INCOMPLETE})
# 최종 대조 실패 시 재시도 마감 (KST) — 이후엔 마지막 판정 유지(밤새 API 호출 방지).
_FINAL_RETRY_UNTIL = time(18, 0)
_STEP_LOCK = asyncio.Lock()


def process_owner_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"[:96]


def _naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _yyyymmdd(trading_date: str) -> str:
    return trading_date.replace("-", "")


def eod_client_order_prefix(trading_date: str, symbol: str) -> str:
    return f"eod-{_yyyymmdd(trading_date)}-{symbol}-"


# ── 정책 / 단계 ────────────────────────────────────────────────────────────


def effective_policy(settings: Any, now: datetime) -> tuple[IntradayPolicy, bool]:
    """(policy, calendar_fallback). 달력 미등록 연도의 *평일* 은 기본 세션 시각으로
    청산만 수행하도록 그 해를 커버한 정책으로 대체 (신규 BUY 차단은 별도 유지)."""
    policy = IntradayPolicy.from_settings(settings)
    d = to_kst(now).date()
    if policy.calendar.status(d) == CalendarStatus.UNKNOWN:
        cal = policy.calendar
        fb = KrxCalendar(cal.holidays, cal.special_sessions,
                         frozenset(set(cal.covered_years) | {d.year}),
                         source=cal.source + "+fallback")
        return IntradayPolicy(
            window_start=policy.window_start, window_end=policy.window_end,
            buy_cutoff_minutes_before_close=policy.buy_cutoff_minutes_before_close,
            flatten_minutes_before_close=policy.flatten_minutes_before_close,
            calendar=fb,
        ), True
    return policy, False


# ── DB helpers (state row / lease) ─────────────────────────────────────────


def get_state_row(db: Any, trading_date: str):
    from app.db.models import EodLiquidationState
    return (db.query(EodLiquidationState)
            .filter(EodLiquidationState.trading_date == trading_date).first())


def _get_or_create_row(db: Any, trading_date: str, now: datetime):
    from sqlalchemy.exc import IntegrityError

    from app.db.models import EodLiquidationState
    row = get_state_row(db, trading_date)
    if row is not None:
        return row
    row = EodLiquidationState(
        trading_date=trading_date, state=EodState.IDLE.value,
        created_at=_naive_utc(now), updated_at=_naive_utc(now), summary={},
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # 다른 프로세스가 동시에 생성 — 그 행을 사용.
        db.rollback()
        row = get_state_row(db, trading_date)
    return row


def acquire_lease(db: Any, trading_date: str, owner: str, now: datetime,
                  ttl_seconds: int) -> bool:
    """원자적 UPDATE 로 lease 획득/갱신. True = 이 프로세스가 단독 실행권 보유."""
    from sqlalchemy import or_, update

    from app.db.models import EodLiquidationState
    _get_or_create_row(db, trading_date, now)
    n = _naive_utc(now)
    res = db.execute(
        update(EodLiquidationState)
        .where(EodLiquidationState.trading_date == trading_date)
        .where(or_(
            EodLiquidationState.lease_owner.is_(None),
            EodLiquidationState.lease_owner == owner,
            EodLiquidationState.lease_expires_at.is_(None),
            EodLiquidationState.lease_expires_at < n,
        ))
        .values(lease_owner=owner,
                lease_expires_at=n + timedelta(seconds=max(10, int(ttl_seconds))))
    )
    db.commit()
    return int(res.rowcount or 0) == 1


def release_lease(db: Any, trading_date: str, owner: str) -> None:
    from sqlalchemy import update

    from app.db.models import EodLiquidationState
    db.execute(
        update(EodLiquidationState)
        .where(EodLiquidationState.trading_date == trading_date)
        .where(EodLiquidationState.lease_owner == owner)
        .values(lease_owner=None, lease_expires_at=None)
    )
    db.commit()


# ── 대조 (reconcile) ────────────────────────────────────────────────────────


@dataclass
class Reconciliation:
    ok: bool
    error_code: str | None
    symbols: dict[str, dict[str, Any]]
    unmanaged: list[dict[str, Any]]
    program_open_buys: list[dict[str, Any]]
    totals: dict[str, int]


def _program_audit_rows(db: Any) -> list[Any]:
    from app.db.models import OrderAuditLog
    return (
        db.query(OrderAuditLog)
        .filter(OrderAuditLog.trade_reason == "kis_paper_auto")
        .filter(OrderAuditLog.decision == "APPROVED")
        .all()
    )


def _eod_audit_rows(db: Any, trading_date: str) -> list[Any]:
    from app.db.models import OrderAuditLog
    return (
        db.query(OrderAuditLog)
        .filter(OrderAuditLog.client_order_id.like(f"eod-{_yyyymmdd(trading_date)}-%"))
        .order_by(OrderAuditLog.id.asc())
        .all()
    )


def bot_net_quantities(rows: list[Any], ccld_filled: dict[str, int]) -> dict[str, int]:
    """프로그램 순보유 수량 (BUY 체결 − SELL 체결). 오늘 주문은 KIS 체결수량으로 보정
    (fill poller 미동작/지연이어도 정확) — 잔고 반영 지연 중 재매도 방지에도 사용."""
    net: dict[str, int] = {}
    for r in rows:
        sym = str(getattr(r, "symbol", "") or "")
        if not sym:
            continue
        filled = int(getattr(r, "filled_quantity", 0) or 0)
        odno = str(getattr(r, "broker_order_id", "") or "")
        if odno and odno in ccld_filled:
            filled = max(filled, int(ccld_filled[odno]))
        if filled <= 0:
            continue
        side = str(getattr(r, "side", "") or "").upper()
        if side == "BUY":
            net[sym] = net.get(sym, 0) + filled
        elif side == "SELL":
            net[sym] = net.get(sym, 0) - filled
    return net


async def reconcile(db: Any, broker: Any, *, trading_date: str) -> Reconciliation:
    """KIS 실잔고 + 오늘 주문(전 페이지) + 소유권 원장 대조 — read-only."""
    def _fail(code: str) -> Reconciliation:
        return Reconciliation(False, code, {}, [], [], {})

    try:
        positions = await broker.get_positions()
    except Exception as exc:  # noqa: BLE001
        _log.warning("[eod] get_positions 실패: %s", type(exc).__name__)
        return _fail(EOD_POSITIONS_FAILED)
    try:
        if not hasattr(broker, "list_today_orders"):
            return _fail(EOD_ORDERS_FAILED)
        orders = await broker.list_today_orders(kst_date=_yyyymmdd(trading_date))
    except Exception as exc:  # noqa: BLE001
        _log.warning("[eod] list_today_orders 실패: %s", type(exc).__name__)
        return _fail(EOD_ORDERS_FAILED)
    try:
        rows = _program_audit_rows(db)
    except Exception:  # noqa: BLE001
        _log.error("[eod] 소유권 원장 조회 실패 — 청산 보류(fail-closed)")
        return _fail(EOD_OWNERSHIP_UNKNOWN)

    ccld_by_odno = {o["odno"]: o for o in orders if o.get("odno")}
    ccld_filled = {k: int(v.get("filled", 0) or 0) for k, v in ccld_by_odno.items()}
    net = bot_net_quantities(rows, ccld_filled)
    program_odnos = {str(getattr(r, "broker_order_id", "") or "") for r in rows} - {""}

    held: dict[str, dict[str, int]] = {}
    for p in positions or []:
        sym = str(getattr(p, "symbol", "") or "")
        q = int(getattr(p, "quantity", 0) or 0)
        if not sym or q <= 0:
            continue
        sell = getattr(p, "sellable_quantity", None)
        held[sym] = {"hldg": q, "ord_psbl": int(sell) if sell not in (None, "") else q}

    open_sell: dict[str, int] = {}
    program_open_buys: list[dict[str, Any]] = []
    for o in orders:
        rem = int(o.get("remaining", 0) or 0)
        if rem <= 0:
            continue
        if o.get("side") == "SELL":
            open_sell[o["symbol"]] = open_sell.get(o["symbol"], 0) + rem
        elif o.get("odno") in program_odnos:
            program_open_buys.append({"symbol": o["symbol"], "odno": o["odno"], "remaining": rem})

    # 오늘 EOD 주문 *전부*(RiskManager 거부 포함) — 시도 횟수/결정적 client_order_id 의
    #   단일 진실. 승인·전송됐는데 KIS 일별체결에 아직 안 보이는 것 → 미확인(재매도 금지).
    try:
        all_eod = _eod_audit_rows(db, trading_date)
    except Exception:  # noqa: BLE001
        return _fail(EOD_OWNERSHIP_UNKNOWN)
    eod_rows: dict[str, list[Any]] = {}
    for r in all_eod:
        eod_rows.setdefault(str(r.symbol), []).append(r)

    symbols: dict[str, dict[str, Any]] = {}
    unmanaged: list[dict[str, Any]] = []
    managed_syms = {s for s, q in net.items() if q > 0} | set(eod_rows)
    for sym, h in held.items():
        bot_q = max(0, int(net.get(sym, 0)))
        managed = min(bot_q, h["hldg"])
        if h["hldg"] - managed > 0:
            unmanaged.append({"symbol": sym, "quantity": h["hldg"] - managed})
        if managed > 0:
            managed_syms.add(sym)
    for sym in sorted(managed_syms):
        h = held.get(sym, {"hldg": 0, "ord_psbl": 0})
        bot_q = max(0, int(net.get(sym, 0)))
        managed = min(bot_q, h["hldg"])
        unconfirmed = 0
        eod_orders = []
        for r in eod_rows.get(sym, []):
            odno = str(getattr(r, "broker_order_id", "") or "")
            seen = ccld_by_odno.get(odno) if odno else None
            if bool(getattr(r, "executed", False)) and odno and seen is None:
                unconfirmed += int(getattr(r, "quantity", 0) or 0)
            eod_orders.append({
                "client_order_id": r.client_order_id, "broker_order_no": odno or None,
                "decision": str(getattr(r, "decision", "") or ""),
                "quantity": int(getattr(r, "quantity", 0) or 0),
                "filled": int((seen or {}).get("filled", getattr(r, "filled_quantity", 0)) or 0),
                "remaining": int((seen or {}).get("remaining", 0) or 0),
                "executed": bool(getattr(r, "executed", False)),
                "visible_at_kis": seen is not None,
            })
        symbols[sym] = {
            "symbol": sym, "kis_hldg": h["hldg"], "ord_psbl": h["ord_psbl"],
            "bot_net": bot_q, "managed_qty": managed,
            "ledger_exceeds_broker": bot_q > h["hldg"],
            "open_sell_qty": int(open_sell.get(sym, 0)),
            "unconfirmed_eod_qty": unconfirmed,
            "eod_attempts": len(eod_rows.get(sym, [])),
            "eod_orders": eod_orders,
        }
    totals = {
        "managed_symbols": sum(1 for v in symbols.values() if v["managed_qty"] > 0),
        "managed_qty": sum(v["managed_qty"] for v in symbols.values()),
        "open_sell_qty": sum(v["open_sell_qty"] for v in symbols.values()),
        "unconfirmed_eod_qty": sum(v["unconfirmed_eod_qty"] for v in symbols.values()),
        "program_open_buy_qty": sum(b["remaining"] for b in program_open_buys),
        "eod_orders_submitted": sum(
            1 for v in symbols.values() for o in v["eod_orders"] if o["executed"]),
        "eod_filled_qty": sum(o["filled"] for v in symbols.values() for o in v["eod_orders"]),
        "unmanaged_symbols": len(unmanaged),
    }
    return Reconciliation(True, None, symbols, unmanaged, program_open_buys, totals)


def sell_quantity_for(info: dict[str, Any]) -> int:
    """이번 단계 매도 수량. 미체결/미확인 EOD 주문이 있으면 0 (중복 매도 방지)."""
    if info["open_sell_qty"] > 0 or info["unconfirmed_eod_qty"] > 0:
        return 0
    return max(0, min(int(info["managed_qty"]), int(info["ord_psbl"])))


# ── 메인 단계 ───────────────────────────────────────────────────────────────


def _persist(db: Any, row: Any, *, state: EodState, reason: str | None, now: datetime,
             summary: dict[str, Any] | None = None, reconcile_ok: bool | None = None,
             final: bool = False) -> None:
    row.state = state.value
    row.reason_code = reason
    row.updated_at = _naive_utc(now)
    if summary is not None:
        row.summary = summary
    if reconcile_ok is not None:
        row.last_reconcile_at = _naive_utc(now)
        row.last_reconcile_ok = bool(reconcile_ok)
    if final:
        row.final_verdict_at = _naive_utc(now)
    db.commit()


def _emit(event_sink: Callable[..., Any] | None, *, level: str, code: str, message: str,
          details: dict[str, Any]) -> None:
    try:
        if event_sink is not None:
            event_sink(level=level, category="PAPER", code=code, message=message, details=details)
        else:
            from app.system.event_log import log_event
            log_event(level=level, category="PAPER", code=code, message=message, details=details)
    except Exception:  # noqa: BLE001
        pass


def _broker_is_kis_paper(broker: Any) -> bool:
    try:
        from app.brokers.kis import KisBrokerAdapter
        return isinstance(broker, KisBrokerAdapter) and bool(getattr(broker, "is_paper", False))
    except Exception:  # noqa: BLE001
        return False


async def run_eod_step(
    *,
    now: datetime | None = None,
    settings: Any = None,
    session_factory: Optional[Callable[[], Any]] = None,
    broker: Any = None,
    risk: Any = None,
    route_order_fn: Optional[Callable[..., Any]] = None,
    loop_state: str | None = None,
    owner_id: str | None = None,
    event_sink: Optional[Callable[..., Any]] = None,
    broker_is_kis_paper: Optional[bool] = None,
    credentials_present: Optional[bool] = None,
) -> dict[str, Any]:
    """EOD coordinator 1 단계 — driver 가 wall-clock 으로 매 interval 호출.

    예외를 던지지 않는다(driver 보호). 반환 dict 는 status API 와 같은 shape.
    """
    async with _STEP_LOCK:   # 같은 프로세스 내 병렬 호출 직렬화.
        return await _run_eod_step_locked(
            now=now, settings=settings, session_factory=session_factory, broker=broker,
            risk=risk, route_order_fn=route_order_fn, loop_state=loop_state,
            owner_id=owner_id, event_sink=event_sink,
            broker_is_kis_paper=broker_is_kis_paper, credentials_present=credentials_present,
        )


async def _run_eod_step_locked(*, now, settings, session_factory, broker, risk,
                               route_order_fn, loop_state, owner_id, event_sink,
                               broker_is_kis_paper, credentials_present) -> dict[str, Any]:
    if now is None:
        now = datetime.now(timezone.utc)
    if settings is None:
        from app.core.config import get_settings
        settings = get_settings()
    policy, cal_fallback = effective_policy(settings, now)
    sch = policy.schedule(now)
    phase = policy.phase(now)
    trading_date = sch.trading_date
    base = {"trading_date": trading_date, "phase": phase.value,
            "schedule": sch.to_dict(), "calendar_fallback": cal_fallback,
            "orders_submitted_this_step": 0, "is_live_authorization": False}

    def _out(state: EodState, reason: str | None, **extra) -> dict[str, Any]:
        return {**base, "state": state.value, "reason_code": reason,
                "reason_message": eod_message_ko(reason), **extra}

    if phase == IntradayPhase.NON_TRADING_DAY:
        return _out(EodState.IDLE, EOD_NOT_TRADING_DAY)
    if phase in (IntradayPhase.PRE_OPEN, IntradayPhase.OPENING_BUFFER,
                 IntradayPhase.ENTRY_OPEN):
        return _out(EodState.IDLE, EOD_BEFORE_CUTOFF)

    from app.auto_paper.paper_auto_mode import resolve_paper_auto_mode
    mode = resolve_paper_auto_mode(settings)
    if bool(getattr(settings, "enable_live_trading", False)):
        return _out(EodState.BLOCKED, EOD_LIVE_TRADING_ENABLED)
    # VIRTUAL_ONLY(provider≠kis 또는 KIS 자동매매 OFF)면 KIS 조회/주문 0.
    #   (kis_realtime 은 VIRTUAL_ONLY 에서도 provider=kis 면 True 라 mode 로 판정.)
    if mode.mode == "VIRTUAL_ONLY" or not mode.kis_realtime:
        return _out(EodState.IDLE, EOD_NOT_KIS_PAPER_MODE)

    if session_factory is None:
        from app.db.session import SessionLocal
        session_factory = SessionLocal
    owner = owner_id or process_owner_id()
    db = session_factory()
    try:
        row = get_state_row(db, trading_date)
        if row is not None and row.state in {s.value for s in _FINAL_STATES} \
                and row.final_verdict_at is not None:
            # 최종 판정 확정 — 더 이상 API 호출/주문 0 (재시작해도 동일).
            return _out(EodState(row.state), row.reason_code, summary=row.summary or {})
        if phase == IntradayPhase.POST_CLOSE and to_kst(now).time() >= _FINAL_RETRY_UNTIL \
                and row is not None and row.state == EodState.FLATTEN_INCOMPLETE.value:
            return _out(EodState.FLATTEN_INCOMPLETE, row.reason_code, summary=row.summary or {})

        interval = int(getattr(settings, "ai_paper_tick_interval_seconds", 30) or 30)
        if not acquire_lease(db, trading_date, owner, now, ttl_seconds=max(45, int(interval * 1.5))):
            row = get_state_row(db, trading_date)
            return _out(EodState(row.state) if row else EodState.IDLE, EOD_LEASE_HELD)
        row = get_state_row(db, trading_date)

        if broker is None:
            from app.api.deps import get_broker
            broker = get_broker()
        if risk is None:
            from app.api.deps import get_risk_manager
            risk = get_risk_manager()
        if broker_is_kis_paper is None:
            broker_is_kis_paper = _broker_is_kis_paper(broker)
        if not broker_is_kis_paper:
            _persist(db, row, state=EodState.BLOCKED, reason=EOD_BROKER_NOT_KIS_PAPER, now=now)
            return _out(EodState.BLOCKED, EOD_BROKER_NOT_KIS_PAPER)

        rec = await reconcile(db, broker, trading_date=trading_date)
        prev_summary = dict(row.summary or {})
        summary = {
            "symbols": rec.symbols, "unmanaged_holdings": rec.unmanaged,
            "program_open_buys": rec.program_open_buys, "totals": rec.totals,
            "reconcile_ok": rec.ok, "reconcile_error": rec.error_code,
            "mode": mode.mode, "dry_run": bool(mode.dry_run), "smoke_mode": bool(mode.smoke_mode),
            "last_broker_order_no": prev_summary.get("last_broker_order_no"),
            "last_order_reason_code": prev_summary.get("last_order_reason_code"),
            "last_fill_detected_at": prev_summary.get("last_fill_detected_at"),
            "last_failure_code": prev_summary.get("last_failure_code"),
            "dry_run_planned": prev_summary.get("dry_run_planned", {}),
            "updated_by": owner,
        }
        prev_filled = int((prev_summary.get("totals") or {}).get("eod_filled_qty", 0) or 0)
        if rec.ok and int(rec.totals.get("eod_filled_qty", 0)) > prev_filled:
            summary["last_fill_detected_at"] = now.isoformat()
        if not rec.ok:
            summary["last_failure_code"] = rec.error_code
            summary["symbols"] = prev_summary.get("symbols", {})
            summary["totals"] = prev_summary.get("totals", {})

        # ── 최종 대조 (15:30 ~) ─────────────────────────────────────────────
        if phase == IntradayPhase.POST_CLOSE:
            if not rec.ok:
                verdict, reason = EodState.FLATTEN_INCOMPLETE, (
                    EOD_OWNERSHIP_UNKNOWN if rec.error_code == EOD_OWNERSHIP_UNKNOWN
                    else EOD_RECONCILE_FAILED)
            elif rec.totals["managed_qty"] > 0:
                verdict, reason = EodState.FLATTEN_INCOMPLETE, EOD_POSITIONS_REMAIN
            elif (rec.totals["open_sell_qty"] + rec.totals["program_open_buy_qty"]
                  + rec.totals["unconfirmed_eod_qty"]) > 0:
                verdict, reason = EodState.FLATTEN_INCOMPLETE, EOD_OPEN_ORDERS_REMAIN
            else:
                verdict, reason = EodState.FLAT_CONFIRMED, EOD_FLAT
            first_incomplete = (verdict == EodState.FLATTEN_INCOMPLETE
                                and row.state != EodState.FLATTEN_INCOMPLETE.value)
            _persist(db, row, state=verdict, reason=reason, now=now, summary=summary,
                     reconcile_ok=rec.ok, final=rec.ok)
            if first_incomplete:
                _emit(event_sink, level="CRITICAL", code=f"KIS_PAPER_EOD_{reason}",
                      message=eod_message_ko(reason),
                      details={"trading_date": trading_date, "totals": rec.totals,
                               "next_day_buy_blocked_until_ack": True,
                               "is_live_authorization": False})
            elif verdict == EodState.FLAT_CONFIRMED:
                _emit(event_sink, level="INFO", code="KIS_PAPER_EOD_FLAT_CONFIRMED",
                      message=eod_message_ko(reason),
                      details={"trading_date": trading_date, "totals": rec.totals})
            return _out(verdict, reason, summary=summary)

        # ── 14:30 ~ 15:15 : 컷오프 대조만 ─────────────────────────────────────
        if phase == IntradayPhase.BUY_CUTOFF:
            _persist(db, row, state=EodState.BUY_CUTOFF_ACTIVE,
                     reason=rec.error_code or "BUY_CUTOFF_ACTIVE", now=now,
                     summary=summary, reconcile_ok=rec.ok)
            out = _out(EodState.BUY_CUTOFF_ACTIVE, rec.error_code, summary=summary)
            out["reason_message"] = (eod_message_ko(rec.error_code) if rec.error_code
                                     else buy_reason_message_ko("BUY_CUTOFF_ACTIVE"))
            return out

        # ── 15:15 ~ 15:30 : 강제청산 ──────────────────────────────────────────
        blocked_reason = None
        emergency = bool(getattr(risk, "emergency_stop", False)) or loop_state == "EMERGENCY_STOP"
        if emergency:
            blocked_reason = EOD_EMERGENCY_STOP
        elif not bool(getattr(settings, "kis_paper_eod_flatten_enabled", True)):
            blocked_reason = EOD_DISABLED
        elif loop_state in ("STOPPED", "PAUSED") and not bool(
                getattr(settings, "kis_paper_eod_flatten_when_loop_stopped", True)):
            blocked_reason = EOD_LOOP_STOPPED
        elif not rec.ok and rec.error_code == EOD_OWNERSHIP_UNKNOWN:
            blocked_reason = EOD_OWNERSHIP_UNKNOWN
        if blocked_reason is not None:
            first = row.state != EodState.BLOCKED.value or row.reason_code != blocked_reason
            _persist(db, row, state=EodState.BLOCKED, reason=blocked_reason, now=now,
                     summary=summary, reconcile_ok=rec.ok)
            if first:
                _emit(event_sink, level="CRITICAL", code=f"KIS_PAPER_{blocked_reason}",
                      message=eod_message_ko(blocked_reason),
                      details={"trading_date": trading_date, "totals": rec.totals})
            return _out(EodState.BLOCKED, blocked_reason, summary=summary)

        if not rec.ok:
            # 조회 실패 — 주문 0 (중복/초과 매도 방지), 다음 단계 재시도.
            _persist(db, row, state=EodState.FLATTENING, reason=rec.error_code, now=now,
                     summary=summary, reconcile_ok=False)
            return _out(EodState.FLATTENING, rec.error_code, summary=summary)

        allow_auction = bool(getattr(settings, "kis_paper_eod_closing_auction_orders_enabled", False))
        may_submit = phase == IntradayPhase.EOD_FLATTEN or (
            phase == IntradayPhase.CLOSING_AUCTION and allow_auction)
        max_attempts = max(1, int(getattr(settings, "kis_paper_eod_max_attempts_per_symbol", 3) or 3))

        submitted = 0
        if may_submit:
            submitted = await _submit_sells(
                db, rec=rec, summary=summary, settings=settings, broker=broker, risk=risk,
                route_order_fn=route_order_fn, now=now, trading_date=trading_date,
                max_attempts=max_attempts, dry_run=bool(mode.dry_run),
                broker_is_kis_paper=bool(broker_is_kis_paper),
                credentials_present=credentials_present, emergency=emergency,
            )

        remaining = sum(sell_quantity_for(v) for v in rec.symbols.values())
        pending = rec.totals["open_sell_qty"] + rec.totals["unconfirmed_eod_qty"] + submitted
        if mode.dry_run:
            state, reason = EodState.FLATTENING, EOD_DRY_RUN
        elif rec.totals["managed_qty"] == 0 and rec.totals["program_open_buy_qty"] == 0:
            state, reason = EodState.WAITING_FOR_FILLS, EOD_ALL_FLAT_PENDING_FINAL
        elif not may_submit:
            state, reason = EodState.WAITING_FOR_FILLS, EOD_AUCTION_ORDERS_OFF
        elif remaining > 0 or submitted > 0:
            state, reason = EodState.FLATTENING, EOD_IN_PROGRESS
        elif pending > 0:
            state, reason = EodState.WAITING_FOR_FILLS, EOD_IN_PROGRESS
        else:
            # 보유는 있으나 더 보낼 수 없음(시도 상한/주문가능 0) — 대기, 최종 대조에서 판정.
            state, reason = EodState.WAITING_FOR_FILLS, EOD_IN_PROGRESS
        _persist(db, row, state=state, reason=reason, now=now, summary=summary,
                 reconcile_ok=True)
        out = _out(state, reason, summary=summary)
        out["orders_submitted_this_step"] = submitted
        return out
    except Exception as exc:  # noqa: BLE001 — driver 보호.
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        _log.warning("[eod] step error: %s: %s", type(exc).__name__, exc)
        return _out(EodState.BLOCKED, "EOD_STEP_ERROR", error=type(exc).__name__)
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass


async def _submit_sells(db: Any, *, rec: Reconciliation, summary: dict[str, Any],
                        settings: Any, broker: Any, risk: Any, route_order_fn: Any,
                        now: datetime, trading_date: str, max_attempts: int, dry_run: bool,
                        broker_is_kis_paper: bool, credentials_present: Optional[bool],
                        emergency: bool) -> int:
    """미청산 수량을 sanctioned 경로로 매도. 반환 = 실제 KIS 모의 전송 건수."""
    if route_order_fn is None:
        from app.execution.order_router import route_order
        route_order_fn = route_order
    if credentials_present is None:
        from app.kis_paper.readiness import evaluate_readiness
        rd = evaluate_readiness(settings)
        credentials_present = bool(rd.kis_key_present and rd.kis_secret_present
                                   and rd.kis_account_present)
    planned = dict(summary.get("dry_run_planned") or {})
    sent = 0
    for sym, info in rec.symbols.items():
        qty = sell_quantity_for(info)
        info["planned_sell_qty"] = qty
        if qty <= 0:
            continue
        if info["eod_attempts"] >= max_attempts:
            info["status"] = "MAX_ATTEMPTS_REACHED"
            summary["last_failure_code"] = "EOD_MAX_ATTEMPTS_REACHED"
            continue
        if dry_run and planned.get(sym) == qty:
            info["status"] = "DRY_RUN_PLANNED"
            continue    # 같은 계획은 하루 1회만 기록 (decision log 노이즈 방지).
        try:
            quote = await broker.get_price(sym)
            price = int(getattr(quote, "price", 0) or 0)
            source = str(getattr(quote, "source", "") or "")
        except Exception as exc:  # noqa: BLE001
            info["status"] = "QUOTE_FAILED"
            summary["last_failure_code"] = f"EOD_QUOTE_FAILED:{type(exc).__name__}"
            continue
        if price <= 0:
            info["status"] = "QUOTE_FAILED"
            summary["last_failure_code"] = "EOD_QUOTE_INVALID"
            continue
        attempt = int(info["eod_attempts"]) + 1
        coid = f"{eod_client_order_prefix(trading_date, sym)}{attempt}"
        decision = KisPaperAutoDecision(
            symbol=sym, side="SELL", quantity=int(qty), price=price,
            selected_strategies=["EOD_FLATTEN"], confidence=0.0, quality_score=0,
            entry_reason="EOD_FORCED_EXIT", has_exit_plan=True,
            sell_reason_code="MARKET_CLOSE_EXIT", sell_reason_category="MARKET_EXIT",
            held_position=True, position_quantity=int(info["managed_qty"]),
            price_source=source or "unknown", price_is_stale=False,
            order_purpose="EOD_FORCED_EXIT",
        )
        result = await execute_kis_paper_auto_order(
            db, decision=decision, settings=settings, broker=broker, risk=risk,
            broker_is_kis_paper=broker_is_kis_paper,
            credentials_present=bool(credentials_present), emergency_stop=emergency,
            route_order_fn=route_order_fn, now=now, chain_id=coid,
        )
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        info["last_result"] = {"reason_code": result.reason_code,
                               "broker_order_no": result.broker_order_no,
                               "submitted": bool(result.submitted),
                               "client_order_id": coid}
        summary["last_order_reason_code"] = result.reason_code
        if result.submitted:
            sent += 1
            info["status"] = "SELL_SUBMITTED"     # RECEIVED ≠ 체결 — 대조로 확인.
            summary["last_broker_order_no"] = result.broker_order_no
        elif dry_run:
            info["status"] = "DRY_RUN_PLANNED"
            planned[sym] = qty
        else:
            info["status"] = "SELL_NOT_SENT"
            summary["last_failure_code"] = result.reason_code
            _log.warning("[eod] %s SELL 미전송: %s %s", sym, result.reason_code,
                         result.reason_message)
    summary["dry_run_planned"] = planned
    return sent


# ── status / 운영자 확인 ─────────────────────────────────────────────────────


def eod_status(db: Any, *, settings: Any = None, now: datetime | None = None) -> dict[str, Any]:
    """UI/API 용 read-only 상태 — 브로커 호출 0건 (마지막 대조 결과만)."""
    if now is None:
        now = datetime.now(timezone.utc)
    if settings is None:
        from app.core.config import get_settings
        settings = get_settings()
    from app.auto_paper.paper_auto_mode import resolve_paper_auto_mode
    from app.kis_paper.intraday_buy_gate import eod_incident_block, program_buy_block_reason
    policy, cal_fallback = effective_policy(settings, now)
    sch = policy.schedule(now)
    phase = policy.phase(now)
    buy_code, buy_msg = program_buy_block_reason(db, settings=settings, now=now)
    row = get_state_row(db, sch.trading_date)
    prev_incident = eod_incident_block(db, now)
    mode = resolve_paper_auto_mode(settings)
    state = row.state if row is not None else (
        EodState.BUY_CUTOFF_ACTIVE.value if phase == IntradayPhase.BUY_CUTOFF
        else EodState.IDLE.value)

    def _iso(dt):
        return dt.replace(tzinfo=timezone.utc).isoformat() if dt is not None else None

    return {
        "now_kst": to_kst(now).isoformat(),
        "trading_date": sch.trading_date,
        "phase": phase.value,
        "schedule": sch.to_dict(),
        "calendar_fallback": cal_fallback,
        "buy_allowed": buy_code is None,
        "buy_block_reason_code": buy_code,
        "buy_block_reason_message": buy_msg,
        "eod_state": state,
        "eod_reason_code": row.reason_code if row else None,
        "eod_reason_message": eod_message_ko(row.reason_code) if row else None,
        "last_reconcile_at": _iso(row.last_reconcile_at) if row else None,
        "last_reconcile_ok": bool(row.last_reconcile_ok) if row else False,
        "final_verdict_at": _iso(row.final_verdict_at) if row else None,
        "summary": (row.summary or {}) if row else {},
        "lease_owner": row.lease_owner if row else None,
        "previous_incident_trading_date": prev_incident,
        "incident_acknowledged": bool(row.incident_acknowledged) if row else False,
        "eod_settings": {
            "flatten_enabled": bool(getattr(settings, "kis_paper_eod_flatten_enabled", True)),
            "closing_auction_orders_enabled": bool(getattr(
                settings, "kis_paper_eod_closing_auction_orders_enabled", False)),
            "flatten_when_loop_stopped": bool(getattr(
                settings, "kis_paper_eod_flatten_when_loop_stopped", True)),
            "max_attempts_per_symbol": int(getattr(
                settings, "kis_paper_eod_max_attempts_per_symbol", 3) or 3),
            "cancel_order_supported": False,
        },
        "paper_auto_mode": mode.to_dict(),
        "broker_label": ("KIS_PAPER" if (mode.mode != "VIRTUAL_ONLY" and mode.kis_realtime)
                         else "VIRTUAL/MOCK"),
        "kis_is_paper": bool(getattr(settings, "kis_is_paper", True)),
        "enable_live_trading": bool(getattr(settings, "enable_live_trading", False)),
        "is_live_authorization": False,
        "broker_order_type": "KIS_PAPER",
    }


def acknowledge_incident(db: Any, *, trading_date: str, decided_by: str,
                         note: str | None = None, now: datetime | None = None) -> Any:
    """운영자 확인 — FLATTEN_INCOMPLETE/미판정 행을 확인 처리(다음 BUY 차단 해제).
    상태 자체는 바꾸지 않는다(사고 기록 보존). 주문 0건."""
    if now is None:
        now = datetime.now(timezone.utc)
    row = get_state_row(db, trading_date)
    if row is None:
        raise LookupError(f"no EOD state for {trading_date}")
    row.incident_acknowledged = True
    row.acknowledged_by = (decided_by or "operator")[:64]
    row.acknowledged_at = _naive_utc(now)
    row.ack_note = (note or "")[:255] or None
    db.commit()
    return row


__all__ = [
    "EodState", "run_eod_step", "reconcile", "eod_status", "acknowledge_incident",
    "acquire_lease", "release_lease", "bot_net_quantities", "sell_quantity_for",
    "eod_client_order_prefix", "effective_policy", "process_owner_id", "eod_message_ko",
]
