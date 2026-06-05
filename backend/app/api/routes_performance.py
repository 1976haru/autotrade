"""성과 대시보드 API — *100% 읽기 전용*.

GET /api/performance?period=daily|weekly|monthly|custom&from=&to=
  → 승률/손익비/순손익/기간수익률 + 청산 건수 + 기간 경계(KST) + 표본 정직성 플래그
  → market: 코스피/코스닥 비교(P2, best-effort — 실패해도 봇 성과는 정상)

주문 경로 / 봇 루프 / 리스크 / config 쓰기 0건. 집계 소스 order_audit_log 실체결만.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.performance.performance import compute_performance, resolve_period
from app.risk.daily_pnl import today_kst

router = APIRouter(tags=["performance"])


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


@router.get("/performance")
async def get_performance(
    period: str = Query("daily"),
    from_:  str | None = Query(None, alias="from"),
    to:     str | None = Query(None, alias="to"),
    db: Session = Depends(get_db),
) -> dict:
    today = today_kst()
    start, end = resolve_period(period, today=today, from_=_parse_date(from_), to=_parse_date(to))

    # 봇 성과(승률/손익비/순손익) — order_audit_log 만. *항상* 산출(시장 실패와 무관).
    perf = compute_performance(db, start=start, end=end, base_equity_krw=None)

    # P2: 지수 비교 + 기간수익률 기준 평가자산(KIS, 공유 limiter 경유) — best-effort.
    #   async 로 *현재 uvicorn 루프* 에서 호출 → 공유 limiter(asyncio.Lock) 정상 동작.
    market = {"available": False, "reason": "MARKET_NOT_FETCHED"}
    current_equity = None
    try:
        from app.performance.market_index import get_market_comparison_context
        ctx = await get_market_comparison_context(start=start, end=end)
        market = ctx.get("market", market)
        current_equity = ctx.get("current_equity_krw")
    except Exception:  # noqa: BLE001 — 시장 데이터 실패는 봇 성과 표시를 막지 않음.
        market = {"available": False, "reason": "MARKET_FETCH_FAILED"}

    # 기간 수익률 = 순손익 ÷ *기간 시작 평가자산*(현재 평가자산 − 기간 순손익).
    #   평가자산은 KIS 잔고(브로커 진실, 공유 limiter 경유) — paper_capital_state.json 미참조.
    net = perf.get("net_pnl_krw") or 0
    if current_equity and not perf.get("no_data"):
        base = int(current_equity) - int(net)
        if base > 0:
            perf["period_return_pct"] = round(net / base * 100, 2)

    # P2: 봇 vs 지수 비교(같은 기간 수익률, %p 차이) — 봇 수익률이 있을 때만.
    comparison = None
    if market.get("available") and perf.get("period_return_pct") is not None:
        bot = perf["period_return_pct"]
        kospi = market.get("kospi_return_pct")
        kosdaq = market.get("kosdaq_return_pct")
        comparison = {
            "bot_return_pct": bot,
            "kospi_return_pct": kospi,
            "kosdaq_return_pct": kosdaq,
            "vs_kospi_pp":  (round(bot - kospi, 2) if kospi is not None else None),
            "vs_kosdaq_pp": (round(bot - kosdaq, 2) if kosdaq is not None else None),
        }

    return {
        **perf,
        "period":     period,
        "market":     market,
        "comparison": comparison,
        "is_live_authorization": False,
    }
