import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_broker, get_risk_manager
from app.brokers.base import Balance, BrokerAdapter, OrderRequest, Position
from app.core.config import get_settings
from app.db.session import get_db
from app.execution.order_router import DuplicateOrderError, route_order
from app.risk.risk_manager import RiskDecision, RiskManager

router = APIRouter(prefix="/broker", tags=["broker"])
_log = logging.getLogger("autotrade.broker")

# T2: 마지막 정상 잔고 — 레이트리밋/일시 실패 시 stale 로 폴백(가짜 0 금지, 기준시각 명시).
_last_good_balance: dict | None = None
# ratelimit_fix 권고1: /price 도 동일 패턴 — 종목별 마지막 정상 시세.
# ★손절/청산 판단용 실시간 조회(app.market_data.kis_realtime.fetch_realtime_quote)는
#   이 딕셔너리·라우트를 전혀 경유하지 않는다 — 프런트 대시보드 표시 전용 캐시.
_last_good_quotes: dict[str, dict] = {}


def _balance_dict(bal) -> dict:
    if hasattr(bal, "model_dump"):
        return dict(bal.model_dump())
    if hasattr(bal, "__dict__"):
        return {k: v for k, v in vars(bal).items() if not k.startswith("_")}
    return dict(bal)


def _quote_dict(q) -> dict:
    if hasattr(q, "model_dump"):
        return dict(q.model_dump())
    if hasattr(q, "__dict__"):
        return {k: v for k, v in vars(q).items() if not k.startswith("_")}
    return dict(q)


def _now_hm_kst() -> str:
    return datetime.now(timezone(timedelta(hours=9))).strftime("%H:%M")


async def _fetch_quote_with_fallback(symbol: str, broker: BrokerAdapter) -> dict:
    """시세 1건 — 성공 시 stale=false. 레이트리밋/일시 실패 시 마지막 정상값을
    stale=true + as_of_kst 로 반환(없으면 error=true, 호출자가 503/부분실패로 매핑).
    /price/{symbol}, /prices 가 공유하는 단일 fallback 로직(ratelimit_fix 권고1).
    """
    global _last_good_quotes
    try:
        q = await broker.get_price(symbol)
        out = _quote_dict(q)
        out.update({"stale": False, "as_of_kst": _now_hm_kst()})
        _last_good_quotes[symbol] = dict(out)
        return out
    except Exception as exc:  # noqa: BLE001 — KIS 일시 실패는 stale 폴백(프런트 재시도 폭풍 방지).
        _log.warning("[broker] price(%s) 조회 실패: %s", symbol, exc)
        cached = _last_good_quotes.get(symbol)
        if cached is not None:
            stale = dict(cached)
            stale["stale"] = True
            return stale  # 옛 정상값(as_of_kst 그대로) — 가짜 값 아님
        return {
            "detail": "증권사(KIS) 응답이 없어요", "symbol": symbol,
            "kis_error": True, "stale": True, "error": True,
        }


@router.get("/price/{symbol}")
async def get_price(symbol: str, broker: BrokerAdapter = Depends(get_broker)):
    """시세 — 손절/청산 실시간 조회(app.market_data.kis_realtime.fetch_realtime_quote)는
    이 라우트를 전혀 경유하지 않는다 — 프런트 대시보드 표시 전용."""
    out = await _fetch_quote_with_fallback(symbol, broker)
    if out.pop("error", False):
        return JSONResponse(status_code=503, content=out)
    return out


@router.get("/prices")
async def get_prices(symbols: str, broker: BrokerAdapter = Depends(get_broker)):
    """다종목 일괄 시세 — ratelimit_fix v2 1단계: 프런트가 보유종목마다 개별
    호출하던 N개 요청을 1개로 묶는다(results/ratelimit_fix/design_v2.md).

    KIS quote API(inquire-price)는 종목당 1콜만 지원 — 이 엔드포인트는 프런트↔
    백엔드 왕복을 N+1콜→1콜로 줄이는 게 핵심이지, 백엔드→KIS 호출 수 자체를
    줄이지는 않는다(다만 KisBrokerAdapter의 quote 캐시를 그대로 통과하므로 이미
    봇 스캔이 최근 조회한 종목은 캐시 히트로 실제 KIS 재호출이 없다). **순차**
    조회 — 동시발사(gather)하면 리미터 앞에 N개가 한꺼번에 다시 몰려 이번 개선의
    핵심(프런트가 한 번에 N개를 동시요청하던 문제)이 백엔드 안에서 재발한다.
    손절/청산 실시간 조회(fetch_realtime_quote)는 이 라우트와 무관 — 별도 경로.
    """
    codes = [s.strip() for s in symbols.split(",") if s.strip()]
    quotes: dict[str, dict] = {}
    for sym in codes:
        out = await _fetch_quote_with_fallback(sym, broker)
        out.pop("error", None)
        quotes[sym] = out
    return {"quotes": quotes}


@router.get("/balance")
async def get_balance(broker: BrokerAdapter = Depends(get_broker)):
    """잔고 — 성공 시 broker_healthy=true. 레이트리밋/일시 실패 시 마지막 정상값을
    stale=true + as_of_kst 로 반환(없으면 503 broker_healthy=false)."""
    global _last_good_balance
    try:
        bal = await broker.get_balance()
        out = _balance_dict(bal)
        out.update({"stale": False, "as_of_kst": _now_hm_kst(), "broker_healthy": True})
        _last_good_balance = dict(out)
        return out
    except Exception as exc:  # noqa: BLE001 — KIS 일시 실패는 stale 폴백(앱 전면 실패 금지).
        _log.warning("[broker] balance 조회 실패: %s", exc)
        if _last_good_balance is not None:
            stale = dict(_last_good_balance)
            stale["stale"] = True
            stale["broker_healthy"] = False
            return stale  # 옛 정상값(as_of_kst 그대로) — 가짜 0 아님
        return JSONResponse(
            status_code=503,
            content={"detail": "증권사(KIS) 응답이 없어요", "broker_healthy": False, "kis_error": True},
        )


@router.get("/positions")
async def get_positions(broker: BrokerAdapter = Depends(get_broker)) -> list[Position]:
    return await broker.get_positions()


@router.post("/orders")
async def place_order(
    order: OrderRequest,
    broker: BrokerAdapter = Depends(get_broker),
    risk: RiskManager = Depends(get_risk_manager),
    db: Session = Depends(get_db),
):
    try:
        routing = await route_order(
            order=order,
            requested_by_ai=False,
            mode=get_settings().default_mode,
            broker=broker,
            risk=risk,
            db=db,
        )
    except DuplicateOrderError as e:
        # 140: 같은 client_order_id로 이미 처리된 주문 — 409 Conflict.
        raise HTTPException(status_code=409, detail=str(e))

    if routing.decision == RiskDecision.REJECTED:
        raise HTTPException(
            status_code=400,
            detail={"decision": routing.decision, "reasons": routing.reasons},
        )

    if routing.decision == RiskDecision.NEEDS_APPROVAL:
        return JSONResponse(
            status_code=202,
            content={
                "status":      "PENDING_APPROVAL",
                "approval_id": routing.approval.id,
                "reasons":     routing.reasons,
            },
        )

    return routing.result
