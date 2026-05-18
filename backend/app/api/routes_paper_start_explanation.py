"""#4-05: Paper Start Explanation API — read-only 시작 전 설명 카드.

`POST /api/agents/paper-start-explanation` — 운영자가 시작 버튼 누르기 전
4-01~4-04 결과를 통합한 advisory 설명 생성.

*broker 호출 0건*. is_order_signal=False / auto_apply_allowed=False /
is_live_authorization=False carry.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.agents.market_regime_agent import MarketStateInput
from app.agents.paper_start_explanation import (
    PreMarketSummary,
    build_paper_start_explanation,
)


router = APIRouter(prefix="/agents", tags=["agents"])


class _MarketStateBody(BaseModel):
    trend_direction:    Optional[str]   = None
    volatility_pct:     Optional[float] = None
    liquidity_score:    Optional[float] = None
    momentum_score:     Optional[float] = None
    choppiness_index:   Optional[float] = None
    high_volatility_threshold: float = 0.04
    low_liquidity_threshold:   float = 0.30
    choppiness_threshold:      float = 0.60


class _PreMarketBody(BaseModel):
    start_allowed:    bool
    verdict:          str
    blocking_reasons: list[str] = []
    warnings:         list[str] = []


class _ExplanationBody(BaseModel):
    """본 endpoint 입력 — 운영자가 *현재 상태* 를 명시 carry.

    Secret / API key / 계좌번호 필드 0건 — 검증 결과 라벨만 carry.
    """
    market_state:        Optional[_MarketStateBody] = None
    pre_market:          Optional[_PreMarketBody]   = None
    demote_to_watchlist: bool                       = False


@router.post("/paper-start-explanation")
def post_explanation(body: _ExplanationBody) -> dict:
    """4-05 advisory 설명 카드 — read-only.

    *broker / OrderExecutor / route_order 호출 0건* — 입력 받아 통합 dataclass
    를 반환만.
    """
    state = MarketStateInput(
        trend_direction=body.market_state.trend_direction if body.market_state else None,
        volatility_pct=body.market_state.volatility_pct if body.market_state else None,
        liquidity_score=body.market_state.liquidity_score if body.market_state else None,
        momentum_score=body.market_state.momentum_score if body.market_state else None,
        choppiness_index=body.market_state.choppiness_index if body.market_state else None,
        high_volatility_threshold=(
            body.market_state.high_volatility_threshold if body.market_state else 0.04
        ),
        low_liquidity_threshold=(
            body.market_state.low_liquidity_threshold if body.market_state else 0.30
        ),
        choppiness_threshold=(
            body.market_state.choppiness_threshold if body.market_state else 0.60
        ),
    ) if body.market_state else None

    pre_market = PreMarketSummary(
        start_allowed=body.pre_market.start_allowed,
        verdict=body.pre_market.verdict,
        blocking_reasons=list(body.pre_market.blocking_reasons or []),
        warnings=list(body.pre_market.warnings or []),
    ) if body.pre_market else None

    explanation = build_paper_start_explanation(
        market_state=state,
        pre_market=pre_market,
        demote_to_watchlist=body.demote_to_watchlist,
    )
    return explanation.to_dict()
