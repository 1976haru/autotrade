"""B1/B2: 아침 브리핑 API — 미국 시세 + 경제 헤드라인. 읽기 전용.

GET /api/briefing/markets   (B1) 미국 시세 5종(graceful per-instrument)
GET /api/briefing/headlines (B2) 최신 경제 헤드라인(RSS 그대로)

주문 경로 / 안전 플래그 0. KIS 호출은 공유 limiter 경유(TTL 캐시).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.performance.headlines import get_headlines
from app.performance.market_briefing import get_briefing_markets

router = APIRouter(prefix="/briefing", tags=["briefing"])


@router.get("/markets")
async def briefing_markets() -> dict:
    return await get_briefing_markets()


@router.get("/headlines")
async def briefing_headlines() -> dict:
    return await get_headlines()
