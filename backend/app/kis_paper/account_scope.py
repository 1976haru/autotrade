"""계좌 스코프 — 주문 원장(order_audit_log)을 *계좌별로* 분리하는 단일 진실.

배경(2026-10-27): order_audit_log 에 계좌 구분이 없어, 모의계좌를 바꾸면 구계좌의 잔여
순보유(원장상 7종목)가 새 계좌에서 '프로그램 보유'로 오인돼 EOD 청산·소유권 판단에
섞일 수 있었다. 이 모듈이:

- 계좌 스코프 값 = ``kis-paper-<sha256(계좌10자리)[:12]>`` — *계좌번호 원문은 저장하지 않는다*.
- ``account_id IS NULL`` 행 = LEGACY_ACCOUNT (계좌 구분 도입 전 기록). 실제 계좌 근거가
  없으므로 어떤 계좌로도 귀속시키지 않고(백필 0), 현재 계좌의 보유·미체결·손익·EOD 에서 제외.
- 스코프가 None(KIS 브로커 아님 — SIMULATION/Mock) 이면 필터 없음(기존 동작 유지).
- 프로그램 순보유 = 같은 계좌의 *실체결*만, 시간순, 자동(kis_paper_auto) BUY/SELL + 수동 SELL
  (manual_sell / manual_sell_all 등 *모든* SELL 체결) 반영, 0 미만 금지, 주문번호 중복 1회 반영.

broker / route_order / OrderExecutor import 0 — 순수 계산 + read-only SELECT.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, Iterable

LEGACY_ACCOUNT = "LEGACY_ACCOUNT"
PROGRAM_TRADE_REASON = "kis_paper_auto"
ACCOUNT_SCOPE_UNKNOWN = "ACCOUNT_SCOPE_UNKNOWN"

_PAPER_MODES = ("PAPER", "LIVE_SHADOW")


def account_id_for(account_no: str | None, *, is_paper: bool = True) -> str | None:
    """계좌번호(8+2 자리, '-' 허용) → 스코프 값. 10자리 미만/비어 있으면 None(불명)."""
    digits = "".join(ch for ch in str(account_no or "") if ch.isdigit())
    if len(digits) < 10:
        return None
    env = "paper" if is_paper else "live"
    return f"kis-{env}-{hashlib.sha256(digits.encode()).hexdigest()[:12]}"


def account_scope_for_broker(broker: Any) -> str | None:
    """주문을 실제로 보내는 브로커 기준 스코프. KIS 어댑터가 아니면 None(스코프 없음)."""
    try:
        from app.brokers.kis import KisBrokerAdapter
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(broker, KisBrokerAdapter):
        return None
    return account_id_for(getattr(broker, "account_no", ""), is_paper=bool(getattr(broker, "is_paper", True)))


def kis_scope_required(settings: Any = None) -> bool:
    """이 설정에서 브로커가 KIS 인가(= 계좌 스코프가 반드시 있어야 하는가)."""
    if settings is None:
        from app.core.config import get_settings
        settings = get_settings()
    mode = getattr(settings, "default_mode", "")
    mode = str(getattr(mode, "value", mode) or "").upper()
    return mode in _PAPER_MODES


def current_account_scope(settings: Any = None) -> str | None:
    """설정 기준 현재 운영 계좌 스코프. KIS 모드가 아니면 None(필터 없음).

    KIS 모드인데 계좌번호가 불명확하면 None — 호출자는 ``kis_scope_required`` 와 함께 보고
    자동매매 시작/신규 BUY 를 차단해야 한다(``account_scope_block_reason``).
    """
    if settings is None:
        from app.core.config import get_settings
        settings = get_settings()
    if not kis_scope_required(settings):
        return None
    return account_id_for(getattr(settings, "kis_account_no", ""),
                          is_paper=bool(getattr(settings, "kis_is_paper", True)))


def account_scope_block_reason(settings: Any = None) -> str | None:
    """KIS 모드인데 계좌 스코프를 정할 수 없으면 ACCOUNT_SCOPE_UNKNOWN, 아니면 None."""
    if settings is None:
        from app.core.config import get_settings
        settings = get_settings()
    if kis_scope_required(settings) and current_account_scope(settings) is None:
        return ACCOUNT_SCOPE_UNKNOWN
    return None


def scope_query(query: Any, model: Any, scope: str | None) -> Any:
    """SQLAlchemy query 에 계좌 필터. PAPER 범위 불명은 전체 원장으로 fail-open하지 않는다."""
    if scope is None:
        if kis_scope_required():
            from app.core.config import get_settings

            settings = get_settings()
            mode = str(getattr(getattr(settings, "default_mode", ""), "value",
                               getattr(settings, "default_mode", "")) or "").upper()
            # LIVE_SHADOW에는 실주문 경로가 없지만 데이터 유출도 허용하지 않는다:
            # 스코프를 확인할 수 없으면 조회 결과를 비운다. PAPER는 실제 KIS
            # 실행 모드이므로 잘못된 계좌 설정을 예외로 드러내 fail-closed 한다.
            if mode == "LIVE_SHADOW":
                return query.filter(False)
            raise RuntimeError(ACCOUNT_SCOPE_UNKNOWN)
        return query
    return query.filter(model.account_id == scope)


def label(account_id: str | None) -> str:
    return account_id or LEGACY_ACCOUNT


# ── 프로그램 순보유 ───────────────────────────────────────────────────────────


def program_ledger_rows(db: Any, scope: str | None, *, legacy: bool = False) -> list[Any]:
    """소유권 계산 대상 행: 같은 계좌의 APPROVED 행 중 프로그램 주문 전부 + 모든 SELL.

    legacy=True 면 계좌 구분 전 기록(account_id IS NULL)만 — 구계좌 격리 보고용.
    수동 BUY 는 프로그램 소유가 아니므로 제외. 정렬 = 시간순(created_at, id).
    """
    from sqlalchemy import or_

    from app.db.models import OrderAuditLog
    q = (db.query(OrderAuditLog)
         .filter(OrderAuditLog.decision == "APPROVED")
         .filter(or_(OrderAuditLog.trade_reason == PROGRAM_TRADE_REASON,
                     OrderAuditLog.side == "SELL")))
    if legacy:
        q = q.filter(OrderAuditLog.account_id.is_(None))
    else:
        q = scope_query(q, OrderAuditLog, scope)
    return q.order_by(OrderAuditLog.created_at.asc(), OrderAuditLog.id.asc()).all()


def _sort_key(r: Any) -> tuple:
    created = getattr(r, "created_at", None)
    if not isinstance(created, datetime):
        created = datetime.min
    return (created.replace(tzinfo=None), int(getattr(r, "id", 0) or 0))


def _fill_key(r: Any, i: int) -> str:
    """중복 체결 식별 키. KIS 주문번호(ODNO)는 *영업일마다 재사용*·계좌별 독립이므로 계좌·날짜·방향·종목과 함께 본다
    (주문번호만으로 묶으면 다른 날의 다른 주문이 합쳐져 실제 체결이 누락된다 — 실DB 검증에서 발견)."""
    odno = str(getattr(r, "broker_order_id", "") or "")
    if not odno:
        return f"row:{getattr(r, 'id', i)}"
    created = getattr(r, "created_at", None)
    day = (created + timedelta(hours=9)).date().isoformat() if isinstance(created, datetime) else "?"
    side = str(getattr(r, "side", "") or "").upper()
    acct = getattr(r, "account_id", None) or LEGACY_ACCOUNT
    return f"{acct}|{day}|{odno}|{side}|{getattr(r, 'symbol', '')}"


def program_net_quantities(rows: Iterable[Any], ccld_filled: dict[str, int] | None = None) -> dict[str, int]:
    """프로그램 순보유(종목별, 0 포함).

    - 실체결만: filled_quantity (오늘 주문은 KIS 일별체결 수량으로 보정 가능) — 접수만으로 차감 X.
    - BUY 는 프로그램 주문(kis_paper_auto)만 가산, SELL 은 사유 무관 모두 차감(수동매도 포함).
    - 시간순 누적, 0 미만으로 내려가지 않음(수동 보유 매도가 프로그램 보유를 음수로 만들지 않음).
    - 같은 (영업일, 주문번호, 방향, 종목) 은 1회만 반영(중복 행/재조회 방지, 더 큰 체결수량 채택).
    """
    ccld_filled = ccld_filled or {}
    ordered = sorted(rows, key=_sort_key)
    best: dict[str, int] = {}
    first_idx: dict[str, int] = {}
    for i, r in enumerate(ordered):
        odno = str(getattr(r, "broker_order_id", "") or "")
        key = _fill_key(r, i)
        filled = int(getattr(r, "filled_quantity", 0) or 0)
        if odno and odno in ccld_filled:
            filled = max(filled, int(ccld_filled[odno] or 0))
        first_idx.setdefault(key, i)
        best[key] = max(best.get(key, 0), filled)

    net: dict[str, int] = {}
    for i, r in enumerate(ordered):
        key = _fill_key(r, i)
        if first_idx.get(key) != i:
            continue
        filled = best.get(key, 0)
        sym = str(getattr(r, "symbol", "") or "")
        if not sym or filled <= 0:
            continue
        side = str(getattr(r, "side", "") or "").upper()
        reason = str(getattr(r, "trade_reason", "") or "")
        cur = net.get(sym, 0)
        if side == "BUY" and reason == PROGRAM_TRADE_REASON:
            net[sym] = cur + filled
        elif side == "SELL":
            net[sym] = max(0, cur - filled)
    return net


def program_holdings(db: Any, scope: str | None) -> dict[str, int]:
    return {s: q for s, q in program_net_quantities(program_ledger_rows(db, scope)).items() if q > 0}


def legacy_unverified_holdings(db: Any) -> list[dict[str, Any]]:
    """계좌 구분 전(LEGACY_ACCOUNT) 기록의 잔여 순보유 — *격리 보고용*.

    실제 KIS 보유가 확인될 때까지 0 으로 간주하거나 청산 대상으로 삼지 않는다(보고만).
    """
    net = program_net_quantities(program_ledger_rows(db, None, legacy=True))
    return [{"symbol": s, "quantity": q, "account": LEGACY_ACCOUNT, "status": "UNVERIFIED"}
            for s, q in sorted(net.items()) if q > 0]


__all__ = [
    "LEGACY_ACCOUNT", "ACCOUNT_SCOPE_UNKNOWN", "account_id_for", "account_scope_for_broker",
    "kis_scope_required", "current_account_scope", "account_scope_block_reason", "scope_query",
    "label", "program_ledger_rows", "program_net_quantities", "program_holdings",
    "legacy_unverified_holdings",
]
