"""수동 포트폴리오 요약 API — 직접 보유 현황·기간 손익 조회 (read-only).

안전 원칙:
- route_order / OrderExecutor / broker.place_order import 0건.
- DB read-only. INSERT 0건.
- 보호 4계층 0줄 수정.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_broker
from app.brokers.base import BrokerAdapter
from app.db.session import get_db
from app.portfolio.manual_portfolio import (
    compute_manual_holdings,
    compute_period_realized_pnl,
    resolve_period_dates,
    today_kst,
)

router = APIRouter(prefix="/manual-portfolio", tags=["manual-portfolio"])


@router.get("/summary")
async def manual_portfolio_summary(
    period: str = Query("today", pattern="^(today|1w|1m|custom)$"),
    from_: str | None = Query(None, alias="from"),
    to_: str | None = Query(None, alias="to"),
    broker: BrokerAdapter = Depends(get_broker),
    db: Session = Depends(get_db),
):
    """직접 보유 현황 + 기간 손익.

    기준: order_audit_log FIFO (체결 기준). 현재가는 broker.get_positions 재활용.
    미조회 종목(broker 오류)은 current_price=0, unrealized_pnl=cost 차감.

    ★스냅샷 없이 체결 기준 손익 제공. 기간 중 미체결 보유분의 일별 등락 추이
    차트가 필요해지면 ManualPortfolioSnapshot + 장마감 잡을 추가한다 (hook 준비됨).
    """
    today = today_kst()
    since, until = resolve_period_dates(period, today, from_, to_)

    try:
        broker_positions = await broker.get_positions()
    except Exception:
        broker_positions = []

    holdings = compute_manual_holdings(db, broker_positions)
    period_realized = compute_period_realized_pnl(db, since=since, until=until)

    total_value    = sum(h.market_value  for h in holdings)
    total_cost     = sum(h.cost_basis    for h in holdings)
    total_unreal   = sum(h.unrealized_pnl for h in holdings)
    unreal_pct     = round(total_unreal / total_cost * 100, 2) if total_cost > 0 else 0.0
    no_price_count = sum(1 for h in holdings if h.current_price == 0)

    return {
        "period":      period,
        "period_from": since.isoformat(),
        "period_to":   until.isoformat(),
        "holdings":    [h.to_dict() for h in holdings],
        "summary": {
            "position_count":         len(holdings),
            "total_market_value":     total_value,
            "total_cost":             total_cost,
            "total_unrealized_pnl":   total_unreal,
            "total_unrealized_pnl_pct": unreal_pct,
            "period_realized_pnl":    period_realized,
        },
        "no_price_count": no_price_count,
        "data_note":   "체결 기준 손익. 현재가 미조회 종목은 평가액 0 표시.",
    }
