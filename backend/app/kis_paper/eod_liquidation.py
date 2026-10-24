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
EOD_STAGED                 = "EOD_STAGED_FLATTEN_IN_PROGRESS"
EOD_CANCEL_FAILED          = "EOD_BUY_CANCEL_FAILED"
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
    EOD_EMERGENCY_STOP:       ("긴급정지(LEVEL 2/3 또는 이전 정책) ON — 자동 청산·취소를 하지 않습니다. "
                               "남은 보유·미체결은 KIS 모의투자 MTS/HTS 에서 직접 정리하세요."),
    EOD_STAGED:               "15:00 사전점검 후 단계적 청산 진행 중 (손실 큰 종목부터).",
    EOD_CANCEL_FAILED:        "14:30 이후 미체결 매수 취소가 확인되지 않았습니다 — 운영자 확인 필요.",
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


def _kst_day_start_utc_naive(trading_date: str) -> datetime:
    y, m, d = (int(x) for x in trading_date.split("-"))
    return datetime(y, m, d) - timedelta(hours=9)


def _yyyymmdd(trading_date: str) -> str:
    return trading_date.replace("-", "")


def eod_client_order_prefix(trading_date: str, symbol: str) -> str:
    # 글자(d/s/n) 로 숫자 연속을 끊는다 — 비밀정보 스캐너 credit_card 패턴(13~19자리) 오탐 방지.
    return f"eod-d{_yyyymmdd(trading_date)}-s{symbol}-n"


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


def release_owner_leases(db: Any, owner: str | None = None) -> int:
    """이 프로세스가 쥔 EOD lease 전부 해제 — 정상 종료 시 (P0-SAFETY-FIX-V1).

    해제하지 않으면 재시작한 새 프로세스가 lease 만료(≥45초)까지 EOD 를 못 잡는다.
    다른 owner 의 lease 는 건드리지 않는다. 반환 = 해제한 행 수.
    """
    from sqlalchemy import update

    from app.db.models import EodLiquidationState
    res = db.execute(
        update(EodLiquidationState)
        .where(EodLiquidationState.lease_owner == (owner or process_owner_id()))
        .values(lease_owner=None, lease_expires_at=None)
    )
    db.commit()
    return int(res.rowcount or 0)


def step_in_progress() -> bool:
    """EOD coordinator 단계가 지금 실행 중인지 (프로세스 내 _STEP_LOCK)."""
    return _STEP_LOCK.locked()


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
        .filter(OrderAuditLog.client_order_id.like(f"eod-d{_yyyymmdd(trading_date)}-%"))
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
    org_by_odno = {str(r.broker_order_id): str(getattr(r, "broker_org_no", "") or "")
                   for r in rows if getattr(r, "broker_order_id", None)
                   and getattr(r, "broker_org_no", None)}

    held: dict[str, dict[str, int]] = {}
    for p in positions or []:
        sym = str(getattr(p, "symbol", "") or "")
        q = int(getattr(p, "quantity", 0) or 0)
        if not sym or q <= 0:
            continue
        sell = getattr(p, "sellable_quantity", None)
        avg = int(getattr(p, "avg_price", 0) or 0)
        mkt = int(getattr(p, "market_price", 0) or 0)
        held[sym] = {"hldg": q, "ord_psbl": int(sell) if sell not in (None, "") else q,
                     "pnl_bp": int((mkt - avg) * 10000 / avg) if avg > 0 and mkt > 0 else 0}

    open_sell: dict[str, int] = {}
    program_open_buys: list[dict[str, Any]] = []
    for o in orders:
        rem = int(o.get("remaining", 0) or 0)
        if rem <= 0:
            continue
        if o.get("side") == "SELL":
            open_sell[o["symbol"]] = open_sell.get(o["symbol"], 0) + rem
        elif o.get("odno") in program_odnos:
            # 주문조직번호: 1순위 주문 시점 응답(audit.broker_org_no), 2순위 일별체결 ord_gno_brno.
            program_open_buys.append({"symbol": o["symbol"], "odno": o["odno"], "remaining": rem,
                                      "ord_gno_brno": (org_by_odno.get(o["odno"])
                                                       or o.get("ord_gno_brno", "")),
                                      "org_no_source": ("ORDER_RESPONSE"
                                                        if org_by_odno.get(o["odno"])
                                                        else "DAILY_CCLD"),
                                      "visible_at_kis": True})
    # 오늘 접수된 프로그램 BUY 인데 KIS 일별체결에 아직 안 보이는 것 → 미확인 BUY.
    #   (주문 접수 직후 조회 지연) — 확인 전까지 FLAT_CONFIRMED 금지.
    day_start = _kst_day_start_utc_naive(trading_date)
    for r in rows:
        odno = str(getattr(r, "broker_order_id", "") or "")
        created = getattr(r, "created_at", None)
        if (str(getattr(r, "side", "")).upper() == "BUY" and odno and odno not in
                {o.get("odno") for o in orders} and bool(getattr(r, "executed", False))
                and created is not None and _naive_utc(created) >= day_start
                and int(getattr(r, "filled_quantity", 0) or 0) < int(getattr(r, "quantity", 0) or 0)):
            program_open_buys.append({
                "symbol": str(r.symbol), "odno": odno,
                "remaining": int(r.quantity or 0) - int(r.filled_quantity or 0),
                "ord_gno_brno": "", "visible_at_kis": False})

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
            "pnl_bp": int(h.get("pnl_bp", 0)),
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
    route_cancel_fn: Optional[Callable[..., Any]] = None,
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
            risk=risk, route_order_fn=route_order_fn, route_cancel_fn=route_cancel_fn,
            loop_state=loop_state,
            owner_id=owner_id, event_sink=event_sink,
            broker_is_kis_paper=broker_is_kis_paper, credentials_present=credentials_present,
        )


async def _run_eod_step_locked(*, now, settings, session_factory, broker, risk,
                               route_order_fn, route_cancel_fn, loop_state, owner_id, event_sink,
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
            # 판정 근거 — dry-run 은 KIS 주문 0건이라 FLAT/INCOMPLETE 가 *실제 청산 결과가 아님*.
            "verdict_basis": ("DRY_RUN_NO_KIS_ORDERS" if mode.dry_run
                              else "KIS_PAPER_BALANCE_RECONCILED"),
            "last_broker_order_no": prev_summary.get("last_broker_order_no"),
            "last_order_reason_code": prev_summary.get("last_order_reason_code"),
            "last_fill_detected_at": prev_summary.get("last_fill_detected_at"),
            "last_failure_code": prev_summary.get("last_failure_code"),
            "dry_run_planned": prev_summary.get("dry_run_planned", {}),
            "buy_cancels": prev_summary.get("buy_cancels", {}),
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

        # ── 14:30 ~ 15:30 : 강한 긴급정지 → 주문/취소 0 + 수동 청산 경고 ────────
        #   P0-SAFETY-FIX-V1: 새 정책 LEVEL_1(신규 매수만 차단)은 청산·BUY 취소를 계속 한다.
        #   LEVEL_2/3·이전 정책(legacy strict) 정지·루프 EMERGENCY_STOP 만 차단.
        from app.risk.kill_switch_policy import emergency_order_policy
        ks = emergency_order_policy(risk)
        summary["kill_switch"] = ks.to_dict()
        emergency = (not ks.allows_eod_forced_exit) or loop_state == "EMERGENCY_STOP"
        summary["manual_liquidation_required"] = bool(
            emergency and int((summary.get("totals") or {}).get("managed_qty", 0) or 0) > 0)
        if emergency:
            first = row.state != EodState.BLOCKED.value or row.reason_code != EOD_EMERGENCY_STOP
            _persist(db, row, state=EodState.BLOCKED, reason=EOD_EMERGENCY_STOP, now=now,
                     summary=summary, reconcile_ok=rec.ok)
            if first:
                _emit(event_sink, level="CRITICAL", code=f"KIS_PAPER_{EOD_EMERGENCY_STOP}",
                      message=eod_message_ko(EOD_EMERGENCY_STOP),
                      details={"trading_date": trading_date, "totals": summary.get("totals"),
                               "manual_liquidation_required":
                                   summary["manual_liquidation_required"]})
            return _out(EodState.BLOCKED, EOD_EMERGENCY_STOP, summary=summary)

        if not rec.ok:
            # 조회 실패 — 주문/취소 0 (중복/초과 매도 방지). 소유권 불명은 BLOCKED+CRITICAL.
            if rec.error_code == EOD_OWNERSHIP_UNKNOWN:
                first = row.state != EodState.BLOCKED.value or row.reason_code != EOD_OWNERSHIP_UNKNOWN
                _persist(db, row, state=EodState.BLOCKED, reason=EOD_OWNERSHIP_UNKNOWN,
                         now=now, summary=summary, reconcile_ok=False)
                if first:
                    _emit(event_sink, level="CRITICAL", code=f"KIS_PAPER_{EOD_OWNERSHIP_UNKNOWN}",
                          message=eod_message_ko(EOD_OWNERSHIP_UNKNOWN),
                          details={"trading_date": trading_date})
                return _out(EodState.BLOCKED, EOD_OWNERSHIP_UNKNOWN, summary=summary)
            st = (EodState.BUY_CUTOFF_ACTIVE if phase == IntradayPhase.BUY_CUTOFF
                  else EodState.FLATTENING)
            _persist(db, row, state=st, reason=rec.error_code, now=now, summary=summary,
                     reconcile_ok=False)
            if phase == IntradayPhase.EOD_PRECHECK and "precheck" not in prev_summary:
                summary["precheck"] = {"at": now.isoformat(), "ok": False,
                                       "error": rec.error_code}
                _persist(db, row, state=st, reason=rec.error_code, now=now, summary=summary)
                _emit(event_sink, level="CRITICAL", code="KIS_PAPER_EOD_PRECHECK_FAILED",
                      message="15:00 장마감 청산 사전점검 실패 — KIS 잔고/주문 조회 불가.",
                      details={"trading_date": trading_date, "error": rec.error_code})
            return _out(st, rec.error_code, summary=summary)

        allow_auction = bool(getattr(settings, "kis_paper_eod_closing_auction_orders_enabled", False))
        max_attempts = max(1, int(getattr(settings, "kis_paper_eod_max_attempts_per_symbol", 3) or 3))
        in_auction = phase == IntradayPhase.CLOSING_AUCTION

        # ── 14:30~ : 프로그램 미체결 BUY 취소 (취소도 OrderExecutor 단일 경로) ──
        cancels = 0
        if not in_auction or allow_auction:
            cancels = await _cancel_open_buys(
                db, rec=rec, summary=summary, settings=settings, broker=broker, risk=risk,
                route_cancel_fn=route_cancel_fn, now=now, trading_date=trading_date,
                max_attempts=max_attempts, dry_run=bool(mode.dry_run), event_sink=event_sink,
            )

        if phase == IntradayPhase.BUY_CUTOFF:
            _persist(db, row, state=EodState.BUY_CUTOFF_ACTIVE, reason="BUY_CUTOFF_ACTIVE",
                     now=now, summary=summary, reconcile_ok=True)
            out = _out(EodState.BUY_CUTOFF_ACTIVE, "BUY_CUTOFF_ACTIVE", summary=summary)
            out["reason_message"] = buy_reason_message_ko("BUY_CUTOFF_ACTIVE")
            out["cancels_sent_this_step"] = cancels
            return out

        # ── 15:00 사전점검 (1회 기록) ─────────────────────────────────────────
        if phase == IntradayPhase.EOD_PRECHECK and "precheck" not in prev_summary:
            summary["precheck"] = {
                "at": now.isoformat(), "ok": True,
                "managed_symbols": rec.totals["managed_symbols"],
                "managed_qty": rec.totals["managed_qty"],
                "open_sell_qty": rec.totals["open_sell_qty"],
                "program_open_buy_qty": rec.totals["program_open_buy_qty"],
                "dry_run": bool(mode.dry_run), "mode": mode.mode,
                "capacity": estimate_eod_capacity(settings, rec.totals["managed_symbols"]),
            }
            _emit(event_sink, level="INFO", code="KIS_PAPER_EOD_PRECHECK_OK",
                  message="15:00 장마감 청산 사전점검 — KIS 잔고/주문 조회 정상, 단계적 청산 시작.",
                  details={"trading_date": trading_date, "totals": rec.totals})
            cap = summary["precheck"]["capacity"]
            if cap["risk"] != "OK":
                # *경고만* — 동작 변경 없음. 운영자가 보수 정책(보유 축소 등)을 판단.
                _emit(event_sink, level="WARN", code="KIS_PAPER_EOD_CAPACITY_RISK",
                      message=(f"장마감 청산 처리량 위험({cap['risk']}): 추정 "
                               f"{cap['est_seconds_worst']}초 / 15:15~15:20 창 "
                               f"{cap['window_seconds']}초 (추정치 — KIS 실측 아님)."),
                      details={"trading_date": trading_date, **cap})
        elif "precheck" in prev_summary:
            summary["precheck"] = prev_summary["precheck"]

        # ── 15:00~15:15 단계적 / 15:15~ 전량 강제청산 ─────────────────────────
        may_submit = phase in (IntradayPhase.EOD_PRECHECK, IntradayPhase.EOD_FLATTEN) or (
            in_auction and allow_auction)
        staged = phase == IntradayPhase.EOD_PRECHECK
        submitted = 0
        if may_submit:
            submitted = await _submit_sells(
                db, rec=rec, summary=summary, settings=settings, broker=broker, risk=risk,
                route_order_fn=route_order_fn, now=now, trading_date=trading_date,
                max_attempts=max_attempts, dry_run=bool(mode.dry_run),
                broker_is_kis_paper=bool(broker_is_kis_paper),
                credentials_present=credentials_present, emergency=False,
                max_symbols=(max(1, int(getattr(
                    settings, "kis_paper_eod_staged_symbols_per_step", 2) or 2))
                    if staged else None),
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
            state, reason = EodState.FLATTENING, (EOD_STAGED if staged else EOD_IN_PROGRESS)
        elif pending > 0:
            state, reason = EodState.WAITING_FOR_FILLS, EOD_IN_PROGRESS
        else:
            # 보유는 있으나 더 보낼 수 없음(시도 상한/주문가능 0) — 대기, 최종 대조에서 판정.
            state, reason = EodState.WAITING_FOR_FILLS, EOD_IN_PROGRESS
        _persist(db, row, state=state, reason=reason, now=now, summary=summary,
                 reconcile_ok=True)
        out = _out(state, reason, summary=summary)
        out["orders_submitted_this_step"] = submitted
        out["cancels_sent_this_step"] = cancels
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
                        emergency: bool, max_symbols: int | None = None) -> int:
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
    tried = 0
    # 단계적 청산(15:00~)은 손실 큰 종목(pnl 낮은 순)부터 — 15:15 이후는 전량.
    ordered = sorted(rec.symbols.items(), key=lambda kv: (int(kv[1].get("pnl_bp", 0)), kv[0]))
    for sym, info in ordered:
        qty = sell_quantity_for(info)
        info["planned_sell_qty"] = qty
        if qty <= 0:
            continue
        if max_symbols is not None and tried >= max_symbols:
            info["status"] = "STAGED_NEXT"
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
        tried += 1
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


_CANCEL_CONFIRM_WAIT_SECONDS = 90

# 처리 용량 추정 (V3) — *추정치*. KIS 모의서버 실측값이 아니다.
#   종목당 KIS 호출: 시세 1 + route_order 시세/잔고/포지션 2(캐시 미스 최악) + 주문 1 = 4회 근사
#   (fake 브로커 실측 5종목 27회·15종목 77회·40종목 202회 ≈ 5회/종목 + 대조 2회 → 최악 5 사용).
_CALLS_PER_SELL_WORST = 5
_RECONCILE_CALLS = 2


def estimate_eod_capacity(settings: Any, n_symbols: int) -> dict[str, Any]:
    """15:15~15:20(강제청산→단일가) 창 안에 전량 청산 주문을 *제출*할 수 있는지 추정.

    제출 시간만 계산한다 — 체결 확인·부분체결 재시도·네트워크 지연·EGW00201 백오프는
    포함하지 않으므로 여유율(50%)로 판정한다. risk: OK / TIGHT(>50%) / OVER(>100%).
    """
    calls = max(1, int(getattr(settings, "kis_rate_limit_calls", 1) or 1))
    window = float(getattr(settings, "kis_rate_limit_window_seconds", 1.1) or 1.1)
    per_call = window / calls
    flatten_min = min(30, max(12, int(getattr(
        settings, "kis_paper_eod_flatten_minutes_before_close", 15) or 15)))
    window_seconds = (flatten_min - 10) * 60          # 강제청산 시작 → 단일가(마감−10분)
    est = round((n_symbols * _CALLS_PER_SELL_WORST + _RECONCILE_CALLS) * per_call)
    risk = "OK" if est <= window_seconds * 0.5 else ("TIGHT" if est <= window_seconds else "OVER")
    return {"symbols": int(n_symbols), "est_seconds_worst": int(est),
            "window_seconds": int(window_seconds), "seconds_per_call": round(per_call, 3),
            "risk": risk, "basis": "ESTIMATE_NOT_MEASURED_ON_KIS"}


def _cancel_audit_rows(db: Any, trading_date: str, odno: str) -> list[Any]:
    from app.db.models import OrderAuditLog
    return (
        db.query(OrderAuditLog)
        .filter(OrderAuditLog.client_order_id.like(f"cxl-d{_yyyymmdd(trading_date)}-o{odno}-n%"))
        .order_by(OrderAuditLog.id.asc())
        .all()
    )


async def _cancel_open_buys(db: Any, *, rec: Reconciliation, summary: dict[str, Any],
                            settings: Any, broker: Any, risk: Any, route_cancel_fn: Any,
                            now: datetime, trading_date: str, max_attempts: int,
                            dry_run: bool, event_sink: Any) -> int:
    """14:30 이후 프로그램 미체결 BUY 잔량 취소 요청. 반환 = 취소 *접수* 건수.

    - KIS 에 보이는(일별체결) 주문만 취소 — 미확인 BUY 는 대기(FLAT_CONFIRMED 금지).
    - 접수된 취소가 있으면 90초간 재요청 0 (확인 대기) — 이후에도 잔량이면 재시도.
    - 시도 상한 도달/취소 불가 → CRITICAL 1회 + last_failure_code.
    - 취소 직전 체결분은 KIS 잔고/체결수량으로 원장에 반영돼 EOD 청산 대상이 된다.
    """
    from app.brokers.base import OrderSide
    from app.core.modes import OperationMode
    if route_cancel_fn is None:
        from app.execution.order_router import route_cancel
        route_cancel_fn = route_cancel
    from app.execution.order_router import DuplicateOrderError

    book = dict(summary.get("buy_cancels") or {})
    sent = 0
    for b in rec.program_open_buys:
        odno = str(b["odno"])
        entry = dict(book.get(odno) or {"symbol": b["symbol"], "critical_emitted": False})
        entry["remaining"] = int(b["remaining"])
        if not b.get("visible_at_kis"):
            entry["status"] = "AWAITING_KIS_VISIBILITY"
            book[odno] = entry
            continue
        rows = _cancel_audit_rows(db, trading_date, odno)
        accepted = [r for r in rows if bool(getattr(r, "executed", False))]
        if accepted:
            last = _naive_utc(accepted[-1].created_at) if accepted[-1].created_at else None
            if last is not None and (_naive_utc(now) - last).total_seconds() < _CANCEL_CONFIRM_WAIT_SECONDS:
                entry["status"] = "CANCEL_REQUESTED_AWAITING_CONFIRM"
                book[odno] = entry
                continue
        if len(rows) >= max_attempts:
            entry["status"] = "CANCEL_FAILED"
            summary["last_failure_code"] = EOD_CANCEL_FAILED
            if not entry.get("critical_emitted"):
                entry["critical_emitted"] = True
                _emit(event_sink, level="CRITICAL", code=f"KIS_PAPER_{EOD_CANCEL_FAILED}",
                      message=eod_message_ko(EOD_CANCEL_FAILED),
                      details={"trading_date": trading_date, "odno": odno,
                               "symbol": b["symbol"], "remaining": b["remaining"]})
            book[odno] = entry
            continue
        if dry_run:
            entry["status"] = "DRY_RUN_CANCEL_PLANNED"
            book[odno] = entry
            continue
        coid = f"cxl-d{_yyyymmdd(trading_date)}-o{odno}-n{len(rows) + 1}"
        try:
            res = await route_cancel_fn(
                original_order_id=odno, org_no=b.get("ord_gno_brno") or None,
                symbol=b["symbol"], side=OrderSide.BUY, reason="BUY_CUTOFF_OPEN_BUY",
                mode=OperationMode.PAPER, broker=broker, risk=risk, db=db,
                client_order_id=coid,
                live_trading_enabled=bool(getattr(settings, "enable_live_trading", False)),
            )
        except DuplicateOrderError:
            entry["status"] = "CANCEL_REQUESTED_AWAITING_CONFIRM"
            book[odno] = entry
            continue
        audit = res.audit
        entry["last_cancel_client_order_id"] = coid
        entry["last_cancel_message"] = str(getattr(audit, "message", "") or "")[:120]
        if bool(getattr(audit, "executed", False)):
            sent += 1
            entry["status"] = "CANCEL_REQUESTED_AWAITING_CONFIRM"
        else:
            entry["status"] = "CANCEL_ATTEMPT_FAILED"
            summary["last_failure_code"] = EOD_CANCEL_FAILED
        book[odno] = entry
    # 이번 대조에서 더 이상 잔량이 없는 주문 = 취소(또는 체결) 확인.
    open_odnos = {str(b["odno"]) for b in rec.program_open_buys}
    for odno, entry in book.items():
        if odno not in open_odnos and entry.get("status") not in ("CONFIRMED_NO_REMAINING",):
            entry["status"] = "CONFIRMED_NO_REMAINING"
            entry["remaining"] = 0
    summary["buy_cancels"] = book
    return sent


# ── status / 운영자 확인 ─────────────────────────────────────────────────────


def eod_status(db: Any, *, settings: Any = None, now: datetime | None = None,
               risk: Any = None) -> dict[str, Any]:
    """UI/API 용 read-only 상태 — 브로커 호출 0건 (마지막 대조 결과 + DB 원장)."""
    if risk is None:
        try:
            from app.api.deps import get_risk_manager
            risk = get_risk_manager()
        except Exception:  # noqa: BLE001
            risk = None
    emergency_on = bool(getattr(risk, "emergency_stop", False))
    from app.risk.kill_switch_policy import emergency_order_policy
    ks_policy = emergency_order_policy(risk)
    # P0-SAFETY-FIX-V1: 수동 청산 필요 = *자동 청산이 막힌* 정지(LEVEL_2/3·legacy)일 때만.
    strict_stop = emergency_on and not ks_policy.allows_eod_forced_exit
    try:
        ledger_holdings = [
            {"symbol": s, "quantity": q}
            for s, q in sorted(bot_net_quantities(_program_audit_rows(db), {}).items())
            if q > 0
        ]
    except Exception:  # noqa: BLE001
        ledger_holdings = []
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
        EodState.BUY_CUTOFF_ACTIVE.value if phase in (IntradayPhase.BUY_CUTOFF, IntradayPhase.EOD_PRECHECK)
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
            # EOD 는 끌 수 없다(V2) — KIS 안전 게이트 통과 시 항상 관리.
            "flatten_always_on": True,
            "closing_auction_orders_enabled": bool(getattr(
                settings, "kis_paper_eod_closing_auction_orders_enabled", False)),
            "max_attempts_per_symbol": int(getattr(
                settings, "kis_paper_eod_max_attempts_per_symbol", 3) or 3),
            "staged_symbols_per_step": int(getattr(
                settings, "kis_paper_eod_staged_symbols_per_step", 2) or 2),
            # 취소: 코드 경로 구현(모의 TR VTTC0013U) — 실제 KIS 모의 접수는 리허설로 확인 필요.
            "cancel_order_supported": True,
            "cancel_order_verified_on_kis_paper": False,
        },
        "emergency_stop": emergency_on,
        "kill_switch": ks_policy.to_dict(),
        "program_ledger_holdings": ledger_holdings,
        "manual_liquidation_required": bool(strict_stop and (
            ledger_holdings or int(((row.summary or {}) if row else {}).get(
                "totals", {}).get("managed_qty", 0) or 0) > 0)),
        "auto_liquidation_guaranteed": False,
        # WINDOWS-ONECLICK-EXE-V1: 시작 화면 표시용 (secret 0건 — bool/심볼/수량만).
        "rehearsal_mode": bool(getattr(settings, "kis_paper_rehearsal_mode", False)),
        "rehearsal_symbol": str(getattr(settings, "kis_paper_rehearsal_symbol", "") or ""),
        "rehearsal_max_buy_qty": int(getattr(settings, "kis_paper_rehearsal_max_buy_qty", 0) or 0),
        "kis_credentials_present": _credentials_present(settings),
        "auto_loop_state": _auto_loop_state(now),
        "paper_auto_mode": mode.to_dict(),
        "broker_label": ("KIS_PAPER" if (mode.mode != "VIRTUAL_ONLY" and mode.kis_realtime)
                         else "VIRTUAL/MOCK"),
        "kis_is_paper": bool(getattr(settings, "kis_is_paper", True)),
        "enable_live_trading": bool(getattr(settings, "enable_live_trading", False)),
        "is_live_authorization": False,
        "broker_order_type": "KIS_PAPER",
    }


def _credentials_present(settings: Any) -> bool:
    try:
        from app.kis_paper.readiness import evaluate_readiness
        rd = evaluate_readiness(settings)
        return bool(rd.kis_key_present and rd.kis_secret_present and rd.kis_account_present)
    except Exception:  # noqa: BLE001
        return False


def _auto_loop_state(now: datetime) -> str | None:
    try:
        from app.auto_paper.loop import get_auto_paper_loop
        return str(get_auto_paper_loop().status(now=now).state)
    except Exception:  # noqa: BLE001
        return None


def shutdown_check(db: Any, *, settings: Any = None, now: datetime | None = None,
                   risk: Any = None) -> dict[str, Any]:
    """데스크톱 앱 종료(X/트레이 '안전 종료') 전 판단 — DB/요약만, 브로커 호출 0건.

    프로그램 보유(원장) 또는 미체결(마지막 대조)이 있으면 종료 시 15:00~15:30 자동 청산이
    실행되지 않으므로 safe_to_quit=False + 이유. 자동 청산은 앱(백엔드)이 켜져 있어야만
    동작하며 PC 절전/강제 종료/전원 차단 시에는 보장되지 않는다.
    """
    st = eod_status(db, settings=settings, now=now, risk=risk)
    holdings = list(st.get("program_ledger_holdings") or [])
    totals = (st.get("summary") or {}).get("totals") or {}
    open_qty = int(totals.get("open_sell_qty", 0) or 0) + int(
        totals.get("program_open_buy_qty", 0) or 0) + int(totals.get("unconfirmed_eod_qty", 0) or 0)
    phase = st.get("phase")
    after_close = phase in ("POST_CLOSE", "NON_TRADING_DAY")
    needs = bool(holdings) or open_qty > 0
    if not needs:
        msg = "프로그램 보유·미체결이 없습니다. 안전하게 종료할 수 있습니다."
    elif after_close:
        msg = (f"장 마감 이후인데 프로그램 보유 {len(holdings)}종목이 남아 있습니다(원장 기준). "
               "오늘 자동 청산은 더 진행되지 않습니다 — KIS 앱에서 잔고를 확인하세요.")
    else:
        msg = (f"프로그램 보유 {len(holdings)}종목 / 미체결 {open_qty}주가 있습니다. "
               "지금 종료하면 15:00~15:30 자동 청산이 실행되지 않습니다. "
               "'트레이로 계속 실행'을 권장합니다.")
    return {
        "safe_to_quit": not needs or after_close,
        "needs_running_for_eod": needs and not after_close,
        "program_holdings": holdings,
        "open_order_qty": open_qty,
        "phase": phase,
        "eod_state": st.get("eod_state"),
        "emergency_stop": st.get("emergency_stop"),
        "message_ko": msg,
        "auto_liquidation_guaranteed": False,
        "guarantee_notice_ko": ("PC 전원 종료·강제 종료·절전·네트워크 단절 상태에서는 "
                                "자동 청산을 보장할 수 없습니다."),
        "is_live_authorization": False,
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
    "acquire_lease", "release_lease", "release_owner_leases", "step_in_progress", "bot_net_quantities", "sell_quantity_for",
    "eod_client_order_prefix", "effective_policy", "process_owner_id", "eod_message_ko",
    "shutdown_check",
]
