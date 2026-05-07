"""Agent Operating Loop API (223, MUST).

deterministic stub 위주 — 실 LLM·실 시장 데이터 없이도 mock output을 안정적
으로 반환. 입력 파라미터는 모두 Query string으로 받아 운영자가 손수 mock
시나리오를 흘릴 수 있게 했다 (스마트폰에서도 fetch 한 번이면 됨).

라우트:
  GET  /api/agents/operating-loop/status   — 현재 단계 + 단계 목록
  POST /api/agents/pre-market-brief        — 장전 brief 생성
  POST /api/agents/intraday-summary        — 장중 누적 요약
  POST /api/agents/post-market-review      — 장후 복기

위 라우트는 모두 advisory — broker 주문을 만들지 않는다. 실 주문 흐름은
RiskManager + PermissionGate + OrderExecutor (CLAUDE.md 절대 원칙).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.agents.operating_loop import (
    OPERATING_STAGES,
    build_intraday_summary,
    build_post_market_review,
    build_pre_market_brief,
    current_stage,
    review_positions,
    watch_market_open,
)
from app.core.config import get_settings


router = APIRouter(prefix="/agents", tags=["agents"])


# ---------- request / response models ----------

class OperatingLoopStatusOut(BaseModel):
    stage:  str
    stages: list[str]


class PreMarketBriefIn(BaseModel):
    daily_loss_cap:    int        = 1_000_000
    market_risk_level: str        = "MEDIUM"
    themes:            list[str] | None = None
    strategies:        list[str] | None = None


class PreMarketBriefOut(BaseModel):
    market_risk_level:    str
    interesting_themes:   list[str]
    available_strategies: list[str]
    daily_loss_cap:       int
    trading_allowed:      bool
    readiness_score:      int
    readiness_label:      str
    operator_summary:     list[str]


class MarketOpenWatchIn(BaseModel):
    gap_up_symbols:   list[str] | None = None
    gap_down_symbols: list[str] | None = None
    volume_spikes:    list[str] | None = None
    volatility_pct:   float = 0.0


class MarketOpenWatchOut(BaseModel):
    volatile_symbols: list[str]
    gap_up_symbols:   list[str]
    gap_down_symbols: list[str]
    volume_spikes:    list[str]
    market_action:    str
    reasons:          list[str]


class IntradaySummaryIn(BaseModel):
    candidates:     int               = 0
    virtual_orders: int               = 0
    rejected:       int               = 0
    last_decision:  str | None        = None
    last_reasons:   list[str] | None  = None


class IntradaySummaryOut(BaseModel):
    candidates_evaluated: int
    virtual_orders_made:  int
    rejected_signals:     int
    last_chief_decision:  str | None
    notable_reasons:      list[str]
    operator_summary:     list[str]


class PositionMonitorIn(BaseModel):
    positions: list[dict[str, Any]] = []


class PositionMonitorEntryOut(BaseModel):
    symbol:         str
    unrealized_pct: float
    advice:         str
    reasons:        list[str]


class PostMarketReviewIn(BaseModel):
    total_decisions:  int               = 0
    successes:        int               = 0
    failures:         int               = 0
    misclassified:    int               = 0
    pnl_estimate:     int               = 0
    next_adjustments: list[str] | None  = None


class PostMarketReviewOut(BaseModel):
    total_decisions:        int
    successes:              int
    failures:               int
    misclassified_signals:  int
    pnl_estimate:           int
    next_day_adjustments:   list[str]
    agent_score_delta:      int
    operator_summary:       list[str]


# ---------- routes ----------

@router.get("/operating-loop/status", response_model=OperatingLoopStatusOut)
def get_operating_loop_status() -> OperatingLoopStatusOut:
    return OperatingLoopStatusOut(stage=current_stage(), stages=list(OPERATING_STAGES))


@router.post("/pre-market-brief", response_model=PreMarketBriefOut)
def post_pre_market_brief(req: PreMarketBriefIn) -> PreMarketBriefOut:
    s = get_settings()
    brief = build_pre_market_brief(
        daily_loss_cap=req.daily_loss_cap,
        emergency_stop=False,  # advisory — runtime ES는 RiskManager 소관
        enable_live_trading=s.enable_live_trading,
        market_risk_level=req.market_risk_level,
        themes=req.themes,
        strategies=req.strategies,
    )
    return PreMarketBriefOut(**brief.__dict__)


@router.post("/market-open-watch", response_model=MarketOpenWatchOut)
def post_market_open_watch(req: MarketOpenWatchIn) -> MarketOpenWatchOut:
    obs = watch_market_open(
        gap_up_symbols=req.gap_up_symbols,
        gap_down_symbols=req.gap_down_symbols,
        volume_spikes=req.volume_spikes,
        volatility_pct=req.volatility_pct,
    )
    return MarketOpenWatchOut(**obs.__dict__)


@router.post("/intraday-summary", response_model=IntradaySummaryOut)
def post_intraday_summary(req: IntradaySummaryIn) -> IntradaySummaryOut:
    summary = build_intraday_summary(
        candidates=req.candidates,
        virtual_orders=req.virtual_orders,
        rejected=req.rejected,
        last_decision=req.last_decision,
        last_reasons=req.last_reasons,
    )
    return IntradaySummaryOut(**summary.__dict__)


@router.post("/position-monitor", response_model=list[PositionMonitorEntryOut])
def post_position_monitor(req: PositionMonitorIn) -> list[PositionMonitorEntryOut]:
    rows = review_positions(req.positions)
    return [PositionMonitorEntryOut(**r.__dict__) for r in rows]


@router.post("/post-market-review", response_model=PostMarketReviewOut)
def post_post_market_review(req: PostMarketReviewIn) -> PostMarketReviewOut:
    review = build_post_market_review(
        total_decisions=req.total_decisions,
        successes=req.successes,
        failures=req.failures,
        misclassified=req.misclassified,
        pnl_estimate=req.pnl_estimate,
        next_adjustments=req.next_adjustments,
    )
    return PostMarketReviewOut(**review.__dict__)
