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

from app.agents.market_regime import classify_market_regime
from app.agents.signal_quality import evaluate_signal_quality
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


class MarketRegimeIn(BaseModel):
    trend_strength_pct: float = 0.0
    volatility_pct:     float = 0.0
    volume_ratio:       float = 1.0
    gap_pct:            float = 0.0
    news_sentiment:     int   = 50
    is_opening_30min:   bool  = False
    is_late_day_30min:  bool  = False
    risk_off_signal:    bool  = False


class MarketRegimeOut(BaseModel):
    regime:                       str
    confidence:                   int
    reasons:                      list[str]
    allowed_strategies:           list[str]
    blocked_strategies:           list[str]
    risk_multiplier:              float
    max_position_size_multiplier: float
    trade_permission:             str
    operator_summary:             list[str]


@router.post("/market-regime", response_model=MarketRegimeOut)
def post_market_regime(req: MarketRegimeIn) -> MarketRegimeOut:
    out = classify_market_regime(
        trend_strength_pct=req.trend_strength_pct,
        volatility_pct=req.volatility_pct,
        volume_ratio=req.volume_ratio,
        gap_pct=req.gap_pct,
        news_sentiment=req.news_sentiment,
        is_opening_30min=req.is_opening_30min,
        is_late_day_30min=req.is_late_day_30min,
        risk_off_signal=req.risk_off_signal,
    )
    return MarketRegimeOut(**out.__dict__)


class SignalQualityIn(BaseModel):
    signal_strength:    int = 0
    regime_fit:         int = 0
    agent_agreement:    int = 0
    scenario_stress:    int = 0
    exit_plan_quality:  int = 0
    sizing_safety:      int = 0
    data_freshness:     int = 100
    duplicate_penalty:  int = 100
    min_required_score: int = 60


class SignalQualityOut(BaseModel):
    quality_score:           int
    quality_grade:           str
    approval_recommendation: str
    rejection_reasons:       list[str]
    min_required_score:      int
    breakdown:               dict[str, int]
    operator_summary:        list[str]


@router.post("/signal-quality", response_model=SignalQualityOut)
def post_signal_quality(req: SignalQualityIn) -> SignalQualityOut:
    out = evaluate_signal_quality(
        signal_strength=req.signal_strength,
        regime_fit=req.regime_fit,
        agent_agreement=req.agent_agreement,
        scenario_stress=req.scenario_stress,
        exit_plan_quality=req.exit_plan_quality,
        sizing_safety=req.sizing_safety,
        data_freshness=req.data_freshness,
        duplicate_penalty=req.duplicate_penalty,
        min_required_score=req.min_required_score,
    )
    return SignalQualityOut(**out.__dict__)


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
