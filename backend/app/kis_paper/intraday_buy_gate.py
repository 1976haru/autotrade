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
2. 직전 거래일 EOD 청산 미완료(FLATTEN_INCOMPLETE 또는 판정 없음) + 운영자 미확인.

CLAUDE.md: broker / OrderExecutor / route_order import 0건 (순환/우회 방지). 순수 판단
+ DB read 1회. 실패 시 *차단* 쪽으로 기운다(fail-closed) — 신규 BUY 는 위험 증가.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
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


def eod_incident_block(db: Any, now: datetime) -> str | None:
    """직전 거래일 EOD 미해결 행의 trading_date (없으면 None).

    오늘보다 *이전* 날짜 중 가장 최근 행만 본다. 운영자가 acknowledge 하면 해제.
    """
    from app.db.models import EodLiquidationState
    today = to_kst(now).date().isoformat()
    row = (
        db.query(EodLiquidationState)
        .filter(EodLiquidationState.trading_date < today)
        .order_by(EodLiquidationState.trading_date.desc())
        .first()
    )
    if row is None:
        return None
    if row.state in _UNRESOLVED_STATES and not bool(row.incident_acknowledged):
        return str(row.trading_date)
    return None


def program_buy_block_reason(
    db: Any, *, settings: Any = None, now: datetime | None = None,
) -> tuple[str | None, str]:
    """(reason_code | None, message). None = 신규 BUY 허용 (시간/달력/EOD 사고 기준)."""
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
            prev = eod_incident_block(db, now)
            if prev is not None:
                return BUY_BLOCKED_BY_EOD_INCIDENT, (
                    f"{prev} 장마감 청산이 확인되지 않았습니다(FLATTEN_INCOMPLETE/미판정). "
                    "운영자 확인(acknowledge) 전까지 신규 매수를 차단합니다.")
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
