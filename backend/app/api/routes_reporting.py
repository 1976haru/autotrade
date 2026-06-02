"""Reporting routes (STEP 3, 2026-06-03) — 거래 일생 테이블 (read-only).

CLAUDE.md 절대 원칙:
- read-only DB SELECT 만 — broker / OrderExecutor / route_order 호출 0건,
  새 주문 생성 0건, DB write 0건.
- 민감정보(계좌/secret) 미포함 — 화이트리스트 필드만.
- LIVE flag / mode 변경 0건.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.reporting.trade_lifecycle import (
    aggregate_by_strategy,
    build_trade_lifecycle,
    to_csv,
)

router = APIRouter(prefix="/reporting", tags=["reporting"])

_KST = timezone(timedelta(hours=9))


def _kst_day_bounds_utc(date_str: str | None, days: int) -> tuple[datetime, datetime]:
    """KST 날짜(YYYY-MM-DD) 또는 최근 N일 → created_at 비교용 UTC [from, to) 범위.

    created_at 은 UTC 로 저장되므로 KST 일 경계를 UTC 로 환산해 반환.
    """
    if date_str:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        start_kst = datetime(d.year, d.month, d.day, tzinfo=_KST)
        end_kst = start_kst + timedelta(days=1)
    else:
        now_kst = datetime.now(_KST)
        end_kst = datetime(now_kst.year, now_kst.month, now_kst.day, tzinfo=_KST) + timedelta(days=1)
        start_kst = end_kst - timedelta(days=max(1, days))
    # UTC naive (DB created_at 비교) — astimezone(utc) 후 tzinfo 제거.
    to_utc = lambda x: x.astimezone(timezone.utc).replace(tzinfo=None)
    return to_utc(start_kst), to_utc(end_kst)


@router.get("/trade-lifecycle")
def get_trade_lifecycle(
    date:   str | None = Query(None, description="KST 날짜 YYYY-MM-DD (없으면 최근 N일)"),
    days:   int = Query(7, ge=1, le=90, description="date 미지정 시 조회할 최근 일수"),
    mode:   str | None = Query(None, description="운용모드 필터 (예: PAPER)"),
    fmt:    str = Query("json", pattern="^(json|csv)$", alias="format"),
    include_open: bool = Query(True),
    db: Session = Depends(get_db),
):
    """거래 일생 테이블 — 한 거래 = 한 줄. JSON 또는 CSV.

    JSON: {trades, by_strategy, summary, notice, is_order_signal, contains_secret}.
    CSV : 민감정보 제외, 운영자가 엑셀에서 보도록.
    """
    date_from, date_to = _kst_day_bounds_utc(date, days)
    rows = build_trade_lifecycle(
        db, date_from=date_from, date_to=date_to, mode=mode, include_open=include_open,
    )

    if fmt == "csv":
        fname = f"trades_{date or f'last{days}d'}.csv"
        return PlainTextResponse(
            content=to_csv(rows),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    by_strategy = aggregate_by_strategy(rows)
    closed = [r for r in rows if r.status == "CLOSED"]
    realized = sum(int(r.gross_pnl_krw or 0) for r in closed)
    return {
        "trades":      [r.to_dict() for r in rows],
        "by_strategy": by_strategy,
        "summary": {
            "total_trades":       len(rows),
            "closed_trades":      len(closed),
            "open_trades":        sum(1 for r in rows if r.status == "OPEN"),
            "gross_realized_pnl_krw": realized,
            "date_from_utc":      date_from.isoformat(),
            "date_to_utc":        date_to.isoformat(),
        },
        "notice": (
            "체결가가 없으면 주문가 기준 추정(price_basis=ESTIMATE)입니다. "
            "손익은 비용(거래세/수수료/슬리피지) 미반영 총손익이며, 비용 반영은 "
            "비용 추적기를 참고하세요. 본 표는 표시용이며 주문 신호가 아닙니다."
        ),
        "is_order_signal": False,
        "contains_secret": False,
    }
