"""#4-07: Paper Decision Bridge API — Agent 추천 → PaperDecision 변환 + 기록.

`POST /api/agents/paper-decision-bridge` — 4-05 explanation + 가상 포지션 +
loop_state 를 입력으로 받아 PaperDecision list + ledger 기록 결과 반환.

*broker 호출 0건*. is_order_signal=False / auto_apply_allowed=False /
is_live_authorization=False carry.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agents.market_regime_agent import MarketStateInput
from app.agents.paper_decision_bridge import (
    PositionSnapshot,
    bridge_explanation_to_paper_decisions,
)
from app.agents.paper_start_explanation import (
    PreMarketSummary,
    build_paper_start_explanation,
)
from app.auto_paper.loop import get_auto_paper_loop


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


class _PositionBody(BaseModel):
    strategy:        str
    symbol:          str
    quantity:        int  = 0
    exit_condition:  bool = False


class _BridgeBody(BaseModel):
    market_state:        Optional[_MarketStateBody] = None
    pre_market:          Optional[_PreMarketBody]   = None
    positions:           list[_PositionBody]        = []
    virtual_trade_size:  int                        = 1
    auto_fill:           bool                       = True
    demote_to_watchlist: bool                       = False


def _to_market_state(body: _BridgeBody) -> Optional[MarketStateInput]:
    if not body.market_state:
        return None
    m = body.market_state
    return MarketStateInput(
        trend_direction=m.trend_direction,
        volatility_pct=m.volatility_pct,
        liquidity_score=m.liquidity_score,
        momentum_score=m.momentum_score,
        choppiness_index=m.choppiness_index,
        high_volatility_threshold=m.high_volatility_threshold,
        low_liquidity_threshold=m.low_liquidity_threshold,
        choppiness_threshold=m.choppiness_threshold,
    )


def _to_pre_market(body: _BridgeBody) -> Optional[PreMarketSummary]:
    if not body.pre_market:
        return None
    p = body.pre_market
    return PreMarketSummary(
        start_allowed=p.start_allowed, verdict=p.verdict,
        blocking_reasons=list(p.blocking_reasons or []),
        warnings=list(p.warnings or []),
    )


@router.post("/paper-decision-bridge")
def post_paper_decision_bridge(body: _BridgeBody) -> dict:
    """Agent 추천 → PaperDecision 변환 + ledger 기록.

    1. 4-05 `PaperStartExplanation` 생성 (4-01~4-04 통합)
    2. 현재 loop_state 자동 조회 (`get_auto_paper_loop().status().state`)
    3. bridge 변환 + ledger 기록 (RUNNING + verdict 허용 시 trade event)
    4. PaperDecision list + 차단 사유 carry

    *broker 호출 0건* — endpoint 가 호출하는 모든 흐름은 read+ledger-append only.
    """
    loop = get_auto_paper_loop()
    loop_state = loop.status().state
    market_state = _to_market_state(body)
    pre_market   = _to_pre_market(body)

    explanation = build_paper_start_explanation(
        market_state=market_state, pre_market=pre_market,
        demote_to_watchlist=body.demote_to_watchlist,
    )

    positions = [
        PositionSnapshot(
            strategy=p.strategy, symbol=p.symbol,
            quantity=int(p.quantity), exit_condition=bool(p.exit_condition),
        )
        for p in body.positions
    ]

    try:
        report = bridge_explanation_to_paper_decisions(
            explanation=explanation,
            loop_state=loop_state,
            positions=positions,
            virtual_trade_size=int(body.virtual_trade_size),
            auto_fill=bool(body.auto_fill),
            record=True,
        )
    except Exception as e:   # noqa: BLE001 — ledger guards (SecretInLedgerError 등)
        raise HTTPException(
            status_code=400, detail=f"{type(e).__name__}: {e}",
        ) from e

    return report.to_dict()
