"""AI 에이전트 대시보드 API — *100% 읽기 전용*.

GET /api/agent/funnel       (AG3) 결정 깔때기
GET /api/agent/calibration  (AG4) 확신도 보정
GET /api/agent/shadow       (AG2) 기각 신호 그림자 추적
GET /api/agent/learning     (AG5) 다음 학습 방향(규칙 기반 관찰)

주문 경로 / agent_council 판단 / 안전 플래그 0줄. 집계만.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.performance.agent_dashboard import compute_calibration, compute_funnel
from app.performance.performance import resolve_period
from app.risk.daily_pnl import today_kst

router = APIRouter(prefix="/agent", tags=["agent-dashboard"])


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _period(period, from_, to):
    today = today_kst()
    return resolve_period(period, today=today, from_=_parse_date(from_), to=_parse_date(to))


@router.get("/funnel")
def get_funnel(period: str = Query("daily"), from_: str | None = Query(None, alias="from"),
               to: str | None = Query(None, alias="to"), db: Session = Depends(get_db)) -> dict:
    start, end = _period(period, from_, to)
    out = compute_funnel(db, start=start, end=end)
    out["period"] = period
    out["is_live_authorization"] = False
    return out


@router.get("/calibration")
def get_calibration(period: str = Query("daily"), from_: str | None = Query(None, alias="from"),
                    to: str | None = Query(None, alias="to"), db: Session = Depends(get_db)) -> dict:
    start, end = _period(period, from_, to)
    out = compute_calibration(db, start=start, end=end)
    out["period"] = period
    out["is_live_authorization"] = False
    return out


@router.get("/shadow")
async def get_shadow(period: str = Query("daily"), from_: str | None = Query(None, alias="from"),
                     to: str | None = Query(None, alias="to"), db: Session = Depends(get_db)) -> dict:
    """AG2: 기각 신호 그림자 추적. 가격 적재(방식 B)는 공유 limiter 경유·하루 1회."""
    from app.performance.shadow import compute_shadow, refresh_shadow_prices
    start, end = _period(period, from_, to)
    try:
        await refresh_shadow_prices(db)   # best-effort 가격 적재(실패해도 집계는 진행)
    except Exception:  # noqa: BLE001
        pass
    out = compute_shadow(db, start=start, end=end)
    out["period"] = period
    out["is_live_authorization"] = False
    return out


@router.get("/learning")
async def get_learning(period: str = Query("daily"), from_: str | None = Query(None, alias="from"),
                       to: str | None = Query(None, alias="to"), db: Session = Depends(get_db)) -> dict:
    from app.performance.agent_dashboard import compute_learning
    from app.performance.shadow import refresh_shadow_prices
    start, end = _period(period, from_, to)
    try:
        await refresh_shadow_prices(db)
    except Exception:  # noqa: BLE001
        pass
    out = compute_learning(db, start=start, end=end)
    out["period"] = period
    out["is_live_authorization"] = False
    return out
