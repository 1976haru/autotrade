"""KIS-PAPER-INTRADAY-FLAT-BY-CLOSE-V1 — 프로그램 신규 BUY 서버측 게이트 (공유 helper).

신규 BUY 차단 판단을 *모든* 진입점이 같은 함수로 재검증하도록 한 곳에 둔다:
- `route_order`            (스캔 tick / run-once / HTTP 경로의 단일 진입점)
- `PermissionGate.approve` (승인 큐 — route_order 를 거치지 않고 재평가 후 실행)
- `execute_kis_paper_auto_order` (KIS 모의 자동주문 게이트 입력)
- `kis_paper_realtime_scan_tick` (BUY 후보 사전 skip — audit 노이즈 방지)

대상은 *프로그램* 주문(trade_reason ∈ PROGRAM_TRADE_REASONS)의 BUY 뿐이다. 운영자가
수동매매 패널로 직접 내는 주문(manual_buy 등)은 사람이 결정한 주문이라 본 게이트
대상이 아니다 (docs/kis_paper_intraday_flat_by_close.md §수동주문).

차단 사유:
1. 시간/달력 — intraday_session.IntradayPolicy.buy_entry_decision (14:30 컷오프,
   휴장일, 달력 미등록 연도, 개장 직후 버퍼, 장 종료 후).
2. EOD 사고 후 계좌별 최종 청산 대조 미완료.

CLAUDE.md: broker / OrderExecutor / route_order import 0건 (순환/우회 방지). 순수 판단
+ DB read 1회. 실패 시 *차단* 쪽으로 기운다(fail-closed) — 신규 BUY 는 위험 증가.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.scheduler.intraday_session import (
    BUY_ALLOWED,
    IntradayPolicy,
    buy_reason_message_ko,
)
from app.scheduler.market_clock import to_kst

_log = logging.getLogger("autotrade.kis_paper.intraday_buy_gate")

PROGRAM_TRADE_REASONS: frozenset[str] = frozenset({"kis_paper_auto"})

BUY_BLOCKED_BY_EOD_INCIDENT = "BUY_BLOCKED_BY_EOD_INCIDENT"
BUY_BLOCKED_GATE_ERROR = "BUY_BLOCKED_GATE_ERROR"

# 판정이 "끝나지 않은" 상태 — 직전 거래일 행이 이 상태로 남아 있으면 overnight
# 보유 여부를 확인 못 한 것 → 운영자 확인 전까지 신규 BUY 차단.
_UNRESOLVED_STATES = frozenset({
    "BUY_CUTOFF_ACTIVE", "FLATTENING", "WAITING_FOR_FILLS",
    "FLATTEN_INCOMPLETE", "BLOCKED",
})


def is_program_order(trade_reason: str | None) -> bool:
    return str(trade_reason or "").strip() in PROGRAM_TRADE_REASONS


# V2: 수동매매 패널 BUY 도 14:30 이후 차단 (KRX 세션 판단만 — 직전일 EOD 사고 차단은
#   프로그램 주문 전용). KIS 브로커로 가는 경우에만 적용 — mock/시뮬레이션 브로커는
#   거래소 세션과 무관하므로 대상 아님.
MANUAL_BUY_TRADE_REASONS: frozenset[str] = frozenset({"manual_buy"})


def is_manual_buy(trade_reason: str | None) -> bool:
    return str(trade_reason or "").strip() in MANUAL_BUY_TRADE_REASONS


def manual_buy_block_reason(*, settings: Any = None,
                            now: datetime | None = None) -> tuple[str | None, str]:
    """(reason_code | None, message) — 수동 BUY 의 시간/달력 판단 (fail-closed)."""
    if now is None:
        now = datetime.now(timezone.utc)
    if settings is None:
        from app.core.config import get_settings
        settings = get_settings()
    try:
        ok, code = IntradayPolicy.from_settings(settings).buy_entry_decision(now)
        return (None, buy_reason_message_ko(code)) if ok else (code, buy_reason_message_ko(code))
    except Exception:  # noqa: BLE001
        return BUY_BLOCKED_GATE_ERROR, "신규 매수 게이트 판단 실패 — 안전을 위해 차단합니다."


def eod_incident_block(db: Any, now: datetime, *, account_scope: str | None = None) -> str | None:
    """An unresolved incident requires a later, verified close on the same account."""
    from app.db.models import EodLiquidationState
    from app.kis_paper.account_scope import current_account_scope, kis_scope_required
    from app.kis_paper.eod_liquidation import LEGACY_EOD_KEY
    today = to_kst(now).date().isoformat()
    scope = account_scope if account_scope is not None else current_account_scope()
    if scope is None and kis_scope_required():
        return "ACCOUNT_SCOPE_UNKNOWN"

    def unresolved(key: str) -> str | None:
        rows = (db.query(EodLiquidationState)
                .filter(EodLiquidationState.trading_date < today)
                .filter(EodLiquidationState.account_key == key)
                .order_by(EodLiquidationState.trading_date.desc()))
        for row in rows:
            if row.state in _UNRESOLVED_STATES:
                return str(row.trading_date)
            if (row.state == "FLAT_CONFIRMED" and row.last_reconcile_ok
                    and row.final_verdict_at is not None
                    and (row.summary or {}).get("recovery_verified") is True):
                return None
        return None

    incident = unresolved(scope or LEGACY_EOD_KEY)
    if incident:
        return incident
    if scope is not None:
        legacy = unresolved(LEGACY_EOD_KEY)
        if legacy:
            return f"LEGACY_ACCOUNT {legacy}"
    return None


def paper_recovery_buy_block_reason(db: Any, *, account_scope: str | None = None,
                                   now: datetime | None = None) -> tuple[str | None, str]:
    try:
        incident = eod_incident_block(db, now or datetime.now(timezone.utc),
                                      account_scope=account_scope)
    except Exception:  # noqa: BLE001 — BUY must fail closed when recovery cannot be checked.
        return BUY_BLOCKED_GATE_ERROR, "장마감 사고 복구 상태를 확인할 수 없어 신규 매수를 차단합니다."
    if incident:
        return BUY_BLOCKED_BY_EOD_INCIDENT, (
            f"{incident} 장마감 사고가 미해결 상태입니다. "
            "KIS 잔고·프로그램 원장·미체결 대조에서 청산이 검증될 때까지 신규 매수를 차단합니다."
        )
    return None, ""


REHEARSAL_SYMBOL_NOT_ALLOWED = "REHEARSAL_SYMBOL_NOT_ALLOWED"
REHEARSAL_BUY_LIMIT_REACHED = "REHEARSAL_BUY_LIMIT_REACHED"
REHEARSAL_SCAN_BUY_DISABLED = "REHEARSAL_SCAN_BUY_DISABLED"


def rehearsal_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "kis_paper_rehearsal_mode", False))


def _today_program_buy_qty(db: Any, now: datetime) -> int:
    """오늘(KST) 프로그램 BUY 로 *전송된* 수량 합 (접수 기준 — 체결 전이라도 카운트)."""
    from app.db.models import OrderAuditLog
    from app.kis_paper.account_scope import current_account_scope, scope_query
    day = to_kst(now).date()
    start_utc = datetime(day.year, day.month, day.day) - timedelta(hours=9)
    rows = scope_query(
        db.query(OrderAuditLog.quantity)
        .filter(OrderAuditLog.trade_reason.in_(tuple(PROGRAM_TRADE_REASONS)))
        .filter(OrderAuditLog.side == "BUY")
        .filter(OrderAuditLog.executed.is_(True))
        .filter(OrderAuditLog.created_at >= start_utc),
        OrderAuditLog, current_account_scope(),
    ).all()
    return sum(int(q or 0) for (q,) in rows)


def rehearsal_buy_block_reason(db: Any, *, settings: Any, now: datetime,
                               symbol: str | None, quantity: int | None) -> tuple[str | None, str]:
    """리허설 모드 신규 BUY 한도 — 지정 종목·하루 누적 수량. SELL 은 대상 아님."""
    if not rehearsal_enabled(settings):
        return None, ""
    want = str(getattr(settings, "kis_paper_rehearsal_symbol", "005930") or "")
    cap = max(0, int(getattr(settings, "kis_paper_rehearsal_max_buy_qty", 1) or 0))
    if symbol is not None and str(symbol) != want:
        return REHEARSAL_SYMBOL_NOT_ALLOWED, f"리허설 모드 — 신규 매수는 {want} 만 허용됩니다."
    used = _today_program_buy_qty(db, now) if db is not None else 0
    if used + int(quantity or 0) > cap:
        return REHEARSAL_BUY_LIMIT_REACHED, (
            f"리허설 모드 — 오늘 신규 매수 상한 {cap}주(이미 {used}주) 초과. 청산 매도는 계속 허용.")
    return None, ""


def program_buy_block_reason(
    db: Any, *, settings: Any = None, now: datetime | None = None,
    symbol: str | None = None, quantity: int | None = None,
) -> tuple[str | None, str]:
    """(reason_code | None, message). None = 신규 BUY 허용 (시간/달력/EOD 사고/리허설 기준)."""
    if now is None:
        now = datetime.now(timezone.utc)
    if settings is None:
        from app.core.config import get_settings
        settings = get_settings()
    try:
        ok, code = IntradayPolicy.from_settings(settings).buy_entry_decision(now)
        if not ok:
            return code, buy_reason_message_ko(code)
        assert code == BUY_ALLOWED
        if db is not None:
            from app.kis_paper.account_scope import (
                ACCOUNT_SCOPE_UNKNOWN, account_scope_block_reason,
                kis_scope_required, legacy_unverified_holdings,
            )
            if account_scope_block_reason(settings) is not None:
                return ACCOUNT_SCOPE_UNKNOWN, "KIS 계좌 범위를 확인할 수 없어 신규 매수를 차단합니다."
            if kis_scope_required(settings) and legacy_unverified_holdings(db):
                return "LEGACY_ACCOUNT_HOLDINGS_UNVERIFIED", (
                    "계좌 귀속을 확인할 수 없는 과거 원장 보유가 있어 신규 매수를 차단합니다."
                )
            incident_code, incident_message = paper_recovery_buy_block_reason(db, now=now)
            if incident_code:
                return incident_code, incident_message
        r_code, r_msg = rehearsal_buy_block_reason(
            db, settings=settings, now=now, symbol=symbol, quantity=quantity)
        if r_code is not None:
            return r_code, r_msg
        return None, buy_reason_message_ko(BUY_ALLOWED)
    except Exception as exc:  # noqa: BLE001 — fail-closed (신규 BUY 차단).
        _log.warning("[intraday-buy-gate] 판단 실패 → BUY 차단: %s: %s",
                     type(exc).__name__, exc)
        return BUY_BLOCKED_GATE_ERROR, "신규 매수 게이트 판단 실패 — 안전을 위해 차단합니다."


__all__ = [
    "PROGRAM_TRADE_REASONS",
    "BUY_BLOCKED_BY_EOD_INCIDENT",
    "BUY_BLOCKED_GATE_ERROR",
    "is_program_order",
    "eod_incident_block",
    "program_buy_block_reason",
]
