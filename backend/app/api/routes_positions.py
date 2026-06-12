"""라이브 포지션 상황판 + 수동 전량 매도 API.

원칙:
  - 포지션·매도가능 수량의 진실 = KIS get_positions (DB 미참조 — 2bcf89a 원칙).
  - ★수동 매도는 *반드시* route_order 진입점을 통과한다(RiskManager→PermissionGate→
    OrderExecutor). broker(kis_client) 직접 호출 0건. 주문 경로 4개 파일 미수정 —
    origin=manual 은 OrderRequest.trade_reason + derive_order_source(strategy=None,
    requested_by_ai=False → MANUAL) 로 *라우트 계층에서* 표시한다.
  - 신규 KIS 호출 추가 금지 — get_positions(=잔고조회, 어댑터 캐싱)에 편승.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_broker, get_risk_manager
from app.brokers.base import BrokerAdapter, OrderRequest, OrderSide, OrderType
from app.core.config import get_settings
from app.db.models import OrderAuditLog
from app.db.session import get_db
from app.execution.order_router import DuplicateOrderError, route_order
from app.risk.risk_manager import RiskDecision, RiskManager
from app.universe.default_universe import FALLBACK_MARKET_CAP_TOP50_NAMES

router = APIRouter(tags=["positions"])

_KST = timezone(timedelta(hours=9))


def _now_hm_kst() -> str:
    return datetime.now(timezone.utc).astimezone(_KST).strftime("%H:%M")


def _resolve_name(symbol: str) -> str | None:
    return FALLBACK_MARKET_CAP_TOP50_NAMES.get(symbol)


def _kst_today_start_utc(now: datetime) -> datetime:
    today_kst = now.astimezone(_KST).date()
    return datetime(today_kst.year, today_kst.month, today_kst.day, tzinfo=_KST).astimezone(timezone.utc)


def _symbol_has_open_sell_today(db: Session, symbol: str, now: datetime) -> bool:
    """오늘(KST) 제출된 *미체결* SELL 이 있는가 — D2 원칙(오늘 기준).

    미체결 = 실행됨(broker 전송) + 아직 FILLED/REJECTED 아님.
    """
    start = _kst_today_start_utc(now)
    rows = (
        db.query(OrderAuditLog)
        .filter(
            OrderAuditLog.symbol == symbol,
            OrderAuditLog.side == "SELL",
            OrderAuditLog.executed.is_(True),
            OrderAuditLog.created_at >= start.replace(tzinfo=None),
        )
        .all()
    )
    for r in rows:
        bs = str(getattr(r, "broker_status", "") or "").upper()
        if bs in ("FILLED", "REJECTED"):
            continue
        if int(getattr(r, "filled_quantity", 0) or 0) > 0:
            continue
        return True
    return False


@router.get("/positions/live")
async def get_live_positions(
    broker: BrokerAdapter = Depends(get_broker),
    db: Session = Depends(get_db),
) -> dict:
    """보유 종목 상황판 — KIS 잔고 기반(현재가 포함). 실패와 보유 0 은 다르게 표시."""
    try:
        positions = await broker.get_positions()
    except Exception:  # noqa: BLE001 — 조회 실패는 빈 목록이 아니라 *실패 플래그*.
        return {"available": False, "reason": "FETCH_FAILED",
                "fetched_at_kst": _now_hm_kst(), "positions": []}

    now = datetime.now(timezone.utc)
    out = []
    for p in positions:
        qty = int(getattr(p, "quantity", 0) or 0)
        if qty <= 0:
            continue
        avg = int(getattr(p, "avg_price", 0) or 0)
        mkt = int(getattr(p, "market_price", 0) or 0)
        eval_pnl = (mkt - avg) * qty if (avg and mkt) else None
        ret_pct = round((mkt - avg) / avg * 100, 2) if (avg and mkt) else None
        in_progress = _symbol_has_open_sell_today(db, p.symbol, now)
        out.append({
            "symbol":       p.symbol,
            # V4: KIS 잔고 응답의 종목명(prdt_name) 우선 → 정적 맵 → None(프론트 해석).
            #   지어내기 금지: KIS 가 주면 그 이름, 없으면 코드 유지.
            "name":         (getattr(p, "name", None) or _resolve_name(p.symbol)),
            "quantity":     qty,
            "avg_price":    avg or None,
            "market_price": mkt or None,
            "eval_pnl_krw": eval_pnl,
            "return_pct":   ret_pct,
            "sell_in_progress": in_progress,
            "status":       "sell_in_progress" if in_progress else "sellable",
        })
    # 설계 B 조각 1: 보유 출처(BOT/MANUAL/UNTAGGED) 분류 부착 — *표시 전용*.
    #   ★조각 2(봇 격리=_kis_held_map 차감)는 미구현 → bot_isolated=False 안내.
    from app.positions.holding_source import classify_positions
    out = classify_positions(db, out)
    return {
        "available": True, "positions": out, "fetched_at_kst": _now_hm_kst(),
        # ★조각 1 경고: 봇이 아직 MANUAL 을 격리 못 함(조각 2 전). UI 가 표시.
        "bot_isolation_active": False,
        "manual_isolation_notice": (
            "봇이 아직 직접 보유(MANUAL)를 격리하지 못합니다 — 수동 보유 기능은 봇 격리 "
            "검증(조각 2) 후 사용하세요. 지금은 봇 PAUSED 상태에서 표시·태깅만 동작합니다."
        ),
    }


def _reject_reason_ko(reasons: list[str] | None) -> str:
    joined = " ".join(str(r) for r in (reasons or [])).lower()
    if "emergency stop" in joined or "긴급" in joined:
        return "긴급정지 중이라 주문이 차단됐어요"
    if "insufficient" in joined or "잔고" in joined:
        return "잔고가 부족해 매도가 차단됐어요"
    if reasons:
        return f"매도 주문이 거절됐어요 — {reasons[0]}"
    return "매도 주문이 거절됐어요 — 사유 확인 중"


@router.post("/positions/{symbol}/sell-all")
async def sell_all(
    symbol: str,
    broker: BrokerAdapter = Depends(get_broker),
    risk: RiskManager = Depends(get_risk_manager),
    db: Session = Depends(get_db),
):
    """보유 전량 시장가 매도 — KIS 잔고 재확인 → route_order 경유 제출. 체결 단정 0."""
    # 1. KIS 잔고에서 보유 수량 *재확인* (화면값 신뢰 금지).
    try:
        positions = await broker.get_positions()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="잔고를 불러오지 못했어요. 잠시 후 다시 시도해주세요.")
    held = next((p for p in positions if p.symbol == symbol and int(getattr(p, "quantity", 0) or 0) > 0), None)
    if held is None:
        raise HTTPException(status_code=400, detail="보유 수량이 없어 매도할 수 없어요.")

    # 2. 오늘 미체결 SELL 존재 → 중복 방지.
    if _symbol_has_open_sell_today(db, symbol, datetime.now(timezone.utc)):
        raise HTTPException(status_code=409, detail="이미 매도 주문이 진행 중이에요.")

    name = _resolve_name(symbol) or symbol
    qty = int(held.quantity)

    # 3. route_order 경유(전량 시장가 SELL). origin=manual 은 trade_reason + MANUAL source.
    order = OrderRequest(
        symbol=symbol, side=OrderSide.SELL, quantity=qty,
        order_type=OrderType.MARKET, trade_reason="manual_sell_all",
    )
    try:
        routing = await route_order(
            order=order, requested_by_ai=False,
            mode=get_settings().default_mode, broker=broker, risk=risk, db=db,
        )
    except DuplicateOrderError as e:
        raise HTTPException(status_code=409, detail=str(e))

    # 4. 결과 분기 — 기존 경로 결과를 그대로 한국어로 전달(우회 0).
    if routing.decision == RiskDecision.REJECTED:
        reason_ko = _reject_reason_ko(routing.reasons)
        _record_feed_best_effort(db, submitted=False, name=name, qty=qty, reason_ko=reason_ko)
        raise HTTPException(status_code=400, detail=reason_ko)

    if routing.decision == RiskDecision.NEEDS_APPROVAL:
        return JSONResponse(status_code=202, content={
            "status": "PENDING_APPROVAL",
            "approval_id": getattr(routing.approval, "id", None),
            "message": "매도 주문이 승인 대기로 들어갔어요.",
        })

    # APPROVED — 제출됨(≠ 체결).
    _record_feed_best_effort(db, submitted=True, name=name, qty=qty, reason_ko=None)
    broker_order_no = getattr(getattr(routing, "audit", None), "broker_order_id", None)
    return {
        "status": "SUBMITTED",
        "broker_order_no": broker_order_no,
        "submitted_at_kst": _now_hm_kst(),
        "message": f"{name} {qty}주 전량 매도 주문을 보냈어요. 체결은 잠시 후 확인돼요.",
    }


class _ManualBuyBody(BaseModel):
    symbol:   str
    quantity: int = Field(..., ge=1, le=100_000)


@router.post("/positions/manual-buy")
async def manual_buy(
    body: _ManualBuyBody,
    broker: BrokerAdapter = Depends(get_broker),
    risk: RiskManager = Depends(get_risk_manager),
    db: Session = Depends(get_db),
):
    """수동 매수(설계 B 조각 1) — 운영자 직접 매수. trade_reason=manual_buy 태깅.

    ★route_order 경유(수동 매도와 대칭) — 주문경로 4파일 *미수정*. 봇의 일일한도/동시진입/
    Agent Council 은 이 경로에 없으므로 *자연 우회*. 단 RiskManager 의 긴급정지·PAPER 플래그·
    notional/잔고 게이트는 route_order 안에서 *반드시* 통과한다(우회 0).

    ★조각 1: 봇 격리(MANUAL 차감)는 아직 — 봇 PAUSED 상태에서만 안전. 봇 가동 중엔
    이 수동 보유를 봇이 청산 대상으로 오인할 수 있음(조각 2 전까지 미사용 권장).
    """
    symbol = str(body.symbol or "").strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="종목 코드를 입력해주세요.")
    qty = int(body.quantity)
    name = _resolve_name(symbol) or symbol

    # route_order 경유 — 시장가 BUY, origin=manual(trade_reason). RiskManager 가
    #   긴급정지/PAPER/notional 을 평가(우회 없음). 봇 council/한도는 이 경로에 없음.
    order = OrderRequest(
        symbol=symbol, side=OrderSide.BUY, quantity=qty,
        order_type=OrderType.MARKET, trade_reason="manual_buy",
    )
    try:
        routing = await route_order(
            order=order, requested_by_ai=False,
            mode=get_settings().default_mode, broker=broker, risk=risk, db=db,
        )
    except DuplicateOrderError as e:
        raise HTTPException(status_code=409, detail=str(e))

    if routing.decision == RiskDecision.REJECTED:
        reason_ko = _reject_reason_ko(routing.reasons)
        _record_buy_feed_best_effort(db, submitted=False, name=name, qty=qty, reason_ko=reason_ko)
        raise HTTPException(status_code=400, detail=reason_ko)

    if routing.decision == RiskDecision.NEEDS_APPROVAL:
        return JSONResponse(status_code=202, content={
            "status": "PENDING_APPROVAL",
            "approval_id": getattr(routing.approval, "id", None),
            "message": "매수 주문이 승인 대기로 들어갔어요.",
        })

    _record_buy_feed_best_effort(db, submitted=True, name=name, qty=qty, reason_ko=None)
    broker_order_no = getattr(getattr(routing, "audit", None), "broker_order_id", None)
    return {
        "status": "SUBMITTED",
        "broker_order_no": broker_order_no,
        "submitted_at_kst": _now_hm_kst(),
        "source": "MANUAL",
        "message": f"{name} {qty}주 직접 매수 주문을 보냈어요. 체결은 잠시 후 확인돼요.",
    }


def _record_buy_feed_best_effort(db, *, submitted: bool, name: str, qty: int, reason_ko: str | None) -> None:
    try:
        from app.core.runtime_config_activity import (
            record_manual_buy_rejected,
            record_manual_buy_submitted,
        )
        if submitted:
            record_manual_buy_submitted(db, symbol_name=name, quantity=qty)
        else:
            record_manual_buy_rejected(db, symbol_name=name, reason_ko=reason_ko or "사유 확인 중")
    except Exception:  # noqa: BLE001
        pass


def _record_feed_best_effort(db, *, submitted: bool, name: str, qty: int, reason_ko: str | None) -> None:
    try:
        from app.core.runtime_config_activity import (
            record_manual_sell_rejected,
            record_manual_sell_submitted,
        )
        if submitted:
            record_manual_sell_submitted(db, symbol_name=name, quantity=qty)
        else:
            record_manual_sell_rejected(db, symbol_name=name, reason_ko=reason_ko or "사유 확인 중")
    except Exception:  # noqa: BLE001 — 기록 실패가 주문 결과를 무효화하지 않음.
        pass
