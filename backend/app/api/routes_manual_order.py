"""수동 매매 통합 엔드포인트 — route_order 경유, trade_reason 고정.

안전 원칙 (CLAUDE.md 절대 원칙 준수):
  - route_order → RiskManager → OrderExecutor 정식 경로. broker 직접 호출 0건.
  - 보호 4계층(risk_manager/permission/order_executor/order_router) 미수정.
  - 봇 진입 게이트(confidence/quality) 미적용 — 이 경로에 없으므로 자연 우회.
    단 긴급정지·PAPER 플래그·notional 상한은 RiskManager 안에서 반드시 통과.
  - naked SELL 차단: KIS 잔고 재확인 후 미보유/초과 수량 거부.

trade_reason 매핑:
  BUY  → 'manual_buy'  (holding_source.MANUAL_BUY_REASONS)
  SELL → 'manual_sell' (holding_source.MANUAL_SELL_REASONS)
  → classify_positions: source=MANUAL → 봇 원장 밖 → driver_bridge 필터 A/B 적용:
    봇이 해당 종목 PositionContext 미생성, SELL 신호 차단.
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
from app.db.session import get_db
from app.execution.order_router import DuplicateOrderError, route_order
from app.risk.risk_manager import RiskDecision, RiskManager
from app.universe.default_universe import (
    FALLBACK_MARKET_CAP_TOP50_NAMES,
    _FALLBACK_TOP100,
)

router = APIRouter(prefix="/manual-order", tags=["manual-order"])

_KST = timezone(timedelta(hours=9))


def _now_hm_kst() -> str:
    return datetime.now(timezone.utc).astimezone(_KST).strftime("%H:%M")


def _resolve_name(symbol: str) -> str | None:
    return FALLBACK_MARKET_CAP_TOP50_NAMES.get(symbol)


class _OrderBody(BaseModel):
    symbol:   str
    side:     str = Field(..., pattern="^(BUY|SELL)$")
    quantity: int = Field(..., ge=1, le=100_000)


@router.post("")
async def manual_order(
    body: _OrderBody,
    broker: BrokerAdapter = Depends(get_broker),
    risk: RiskManager = Depends(get_risk_manager),
    db: Session = Depends(get_db),
):
    """수동 매수/매도 — route_order 경유.

    BUY:  trade_reason='manual_buy'  → holding_source MANUAL → 봇 청산 대상 제외.
    SELL: trade_reason='manual_sell' → holding_source MANUAL → KIS 잔고 재확인 후 실행.
    """
    symbol = str(body.symbol or "").strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="종목 코드를 입력해주세요.")
    side = body.side.upper()
    qty  = int(body.quantity)
    name = _resolve_name(symbol) or symbol

    # ── naked SELL 차단 — KIS 잔고 재확인 ──────────────────────────────────────
    if side == "SELL":
        try:
            positions = await broker.get_positions()
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="잔고를 불러오지 못했어요. 잠시 후 다시 시도해주세요.",
            )
        held = next(
            (p for p in positions
             if p.symbol == symbol and int(getattr(p, "quantity", 0) or 0) > 0),
            None,
        )
        if held is None:
            raise HTTPException(
                status_code=400,
                detail=f"{name} 보유 수량이 없어 매도할 수 없어요.",
            )
        held_qty = int(held.quantity)
        if qty > held_qty:
            raise HTTPException(
                status_code=400,
                detail=f"{name} 보유 {held_qty}주 초과 요청({qty}주) — 매도 불가.",
            )

    # ── route_order 경유 ────────────────────────────────────────────────────────
    trade_reason = "manual_buy" if side == "BUY" else "manual_sell"
    order = OrderRequest(
        symbol=symbol,
        side=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
        quantity=qty,
        order_type=OrderType.MARKET,
        trade_reason=trade_reason,
    )
    try:
        routing = await route_order(
            order=order, requested_by_ai=False,
            mode=get_settings().default_mode, broker=broker, risk=risk, db=db,
        )
    except DuplicateOrderError as e:
        raise HTTPException(status_code=409, detail=str(e))

    if routing.decision == RiskDecision.REJECTED:
        raise HTTPException(status_code=400, detail=_reject_reason_ko(routing.reasons))

    if routing.decision == RiskDecision.NEEDS_APPROVAL:
        return JSONResponse(status_code=202, content={
            "status": "PENDING_APPROVAL",
            "approval_id": getattr(routing.approval, "id", None),
            "message": "주문이 승인 대기로 들어갔어요.",
        })

    broker_order_no = getattr(getattr(routing, "audit", None), "broker_order_id", None)
    side_ko = "매수" if side == "BUY" else "매도"
    return {
        "status": "SUBMITTED",
        "side": side,
        "symbol": symbol,
        "name": name,
        "quantity": qty,
        "trade_reason": trade_reason,
        "broker_order_no": broker_order_no,
        "submitted_at_kst": _now_hm_kst(),
        "message": f"{name} {qty}주 {side_ko} 주문을 보냈어요. 체결은 잠시 후 확인돼요.",
    }


@router.get("/quote/{symbol}")
async def manual_order_quote(symbol: str, broker: BrokerAdapter = Depends(get_broker)):
    """현재가 조회 — 수동 매매 패널 매수 전 가격 확인용. broker.get_price 재활용."""
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="종목 코드를 입력해주세요.")
    try:
        quote = await broker.get_price(symbol)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"시세 조회 실패 — {e}")
    name = _resolve_name(symbol) or getattr(quote, "name", None)
    return {
        "symbol": symbol,
        "name": name,
        "price": int(getattr(quote, "price", 0) or 0),
        "fetched_at_kst": _now_hm_kst(),
    }


@router.get("/universe")
def manual_order_universe():
    """자동완성용 종목 목록 — (code, name) 100개. DB/KIS 호출 0건(정적 목록)."""
    return [{"code": c, "name": n} for c, n in _FALLBACK_TOP100]


def _reject_reason_ko(reasons: list[str] | None) -> str:
    joined = " ".join(str(r) for r in (reasons or [])).lower()
    if "emergency stop" in joined or "긴급" in joined:
        return "긴급정지 중이라 주문이 차단됐어요."
    if "insufficient" in joined or "잔고" in joined:
        return "잔고가 부족해 주문이 차단됐어요."
    if reasons:
        return f"주문이 거절됐어요 — {reasons[0]}"
    return "주문이 거절됐어요 — 사유 확인 중"
