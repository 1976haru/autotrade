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

from app.agents.base import AgentContext, AgentRole
from app.agents.market_observer import (
    IndexQuote,
    MarketObserverInput,
    observe_market,
)
from app.agents.market_regime import classify_market_regime
from app.agents.news_trend_agent import (
    load_recent_theme_signals,
    summarize_themes,
)
from app.agents.roles import build_default_registry
from app.agents.signal_quality import evaluate_signal_quality
from app.db.session import get_db
from fastapi import Depends, Query
from sqlalchemy.orm import Session as _Session
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


# ====================================================================
# #51: Agent architecture introspection + mock-run
# ====================================================================


class AgentArchitectureRoleOut(BaseModel):
    role:               str
    decision_label:     str   # 카테고리 라벨 (정의에 가까움)
    description:        str
    can_execute_order:  bool


class AgentArchitectureOut(BaseModel):
    """6개 표준 역할 + 절대 invariant 안내. read-only."""
    roles:              list[AgentArchitectureRoleOut]
    forbidden_for_all:  list[str]
    notice:             str


_ARCHITECTURE_ROLE_DOC = {
    "OBSERVER": (
        "OBSERVE",
        "시장 / 데이터 / 운영 상태 관찰. 결정 X.",
    ),
    "ANALYST": (
        "ANALYZE",
        "후보 종목 / 상황 분석 의견 산출.",
    ),
    "RISK_AUDITOR": (
        "WARN | REJECT",
        "일일 손실 / 중복 / stale data / risk events 점검.",
    ),
    "STRATEGY_RESEARCHER": (
        "REPORT | RECOMMEND",
        "전략 / 백테스트 개선안 제안.",
    ),
    "REPORT_WRITER": (
        "REPORT",
        "일일 / 주간 운영 리포트 작성.",
    ),
    "EXECUTION_RECOMMENDER": (
        "APPROVAL_CANDIDATE",
        "매수/매도 후보 제안. approval queue 후보 payload만 — 직접 주문 0건.",
    ),
}


@router.get("/architecture", response_model=AgentArchitectureOut)
def get_agent_architecture() -> AgentArchitectureOut:
    """6개 표준 Agent 역할 + 절대 invariant 안내 (read-only)."""
    roles = [
        AgentArchitectureRoleOut(
            role=role,
            decision_label=label,
            description=desc,
            can_execute_order=False,
        )
        for role, (label, desc) in _ARCHITECTURE_ROLE_DOC.items()
    ]
    return AgentArchitectureOut(
        roles=roles,
        forbidden_for_all=[
            "broker / OrderExecutor / route_order 호출 금지",
            "실제 주문 / approval queue 등록 금지 (caller 책임)",
            "AI API key / Secret 인자 수용 금지 — deterministic mock만",
            "CLAUDE.md 절대 원칙 1, 2 준수",
        ],
        notice=(
            "모든 Agent는 분석 / 추천 / 리포트만 한다. ExecutionRecommender도 "
            "approval queue 후보 payload만 생성하며, 실제 주문은 RiskManager + "
            "PermissionGate + OrderExecutor 흐름에서만 만들어진다."
        ),
    )


class AgentCatalogEntryOut(BaseModel):
    name:               str
    role:               str
    description:        str
    inputs:             list[str]
    outputs:            list[str]
    forbidden:          list[str]
    can_execute_order:  bool


@router.get("/catalog", response_model=list[AgentCatalogEntryOut])
def get_agent_catalog() -> list[AgentCatalogEntryOut]:
    """등록된 Agent 인스턴스의 metadata 카탈로그 (read-only).

    `build_default_registry()`가 만든 단일 인스턴스의 self-describing
    metadata를 직렬화 — 어떤 Agent도 broker 호출이 없는 advisory 인터페이스
    임을 운영자가 한눈에 확인할 수 있다.
    """
    registry = build_default_registry()
    return [
        AgentCatalogEntryOut(**agent.metadata.to_dict())
        for agent in registry.values()
    ]


class AgentMockRunIn(BaseModel):
    """`/agents/mock-run` 입력. broker / API key 필드 0개 — caller가 mock
    context만 전달."""

    role:           str = "OBSERVER"
    operator_intent: str | None = None
    market_state:    dict | None = None
    watchlist:       list[str] | None = None
    recent_signals:  list[dict] | None = None
    audit_summary:   dict | None = None
    risk_state:      dict | None = None
    extra:           dict | None = None


class AgentMockRunOut(BaseModel):
    role:               str
    decision:           str
    summary:            str
    reasons:            list[str]
    confidence:         int | None = None
    risk_flags:         list[str]
    approval_candidate: dict | None = None
    metadata:           dict
    is_order_intent:    bool
    can_execute_order:  bool
    created_at:         str


@router.post("/mock-run", response_model=AgentMockRunOut)
def post_agent_mock_run(req: AgentMockRunIn) -> AgentMockRunOut:
    """단일 Agent를 mock 모드로 호출.

    **broker 호출 0건, audit row 0건, 실 LLM 호출 0건** — deterministic mock.
    caller가 `role`을 지정하지 않으면 OBSERVER로 fallback. 알 수 없는 role은
    400 (Pydantic은 enum 검증을 strict하게 하지 않으므로 아래에서 분기).
    """
    try:
        role = AgentRole(req.role)
    except ValueError:
        # 알 수 없는 role 명시 안내.
        return AgentMockRunOut(
            role=req.role,
            decision="NO_OP",
            summary=f"unknown agent role: {req.role}",
            reasons=[f"role {req.role!r} is not a registered AgentRole"],
            confidence=None,
            risk_flags=[],
            approval_candidate=None,
            metadata={"valid_roles": [r.value for r in AgentRole]},
            is_order_intent=False,
            can_execute_order=False,
            created_at="1970-01-01T00:00:00+00:00",
        )
    registry = build_default_registry()
    agent = registry[role]
    output = agent.run(AgentContext(
        operator_intent=req.operator_intent,
        market_state=req.market_state,
        watchlist=req.watchlist,
        recent_signals=req.recent_signals,
        audit_summary=req.audit_summary,
        risk_state=req.risk_state,
        extra=req.extra,
    ))
    return AgentMockRunOut(**output.to_dict())


# ====================================================================
# #52: Market Observer — context-only snapshot (NOT an order signal)
# ====================================================================


class _IndexQuoteIn(BaseModel):
    name:                 str
    last_price:           float | None = None
    change_pct:           float | None = None
    last_updated_seconds: int   | None = None


class MarketObserverIn(BaseModel):
    """`/api/agents/market-observer` 입력. 모든 필드 optional — 데이터 없으면
    UNKNOWN / WATCH_ONLY로 friendly fallback (예외 X)."""

    indices:                list[_IndexQuoteIn] | None = None
    turnover_vs_avg:        float | None = None
    volatility_pct:         float | None = None
    leading_sectors:        list[str] | None = None
    lagging_sectors:        list[str] | None = None
    leading_themes:         list[str] | None = None
    surge_count:            int | None = None
    plunge_count:           int | None = None
    data_freshness_seconds: int | None = None
    # 선택 — caller가 market_regime classifier 입력을 함께 보내면 본 endpoint
    # 가 자동으로 classify해서 carry. 미지정이면 regime carry 안 함.
    market_regime_input:    dict | None = None


class MarketObserverOut(BaseModel):
    risk_level:         str
    recommended_stance: str
    summary_lines:      list[str]
    turnover_state:     str
    volatility_state:   str
    freshness_status:   str
    leading_sectors:    list[str]
    lagging_sectors:    list[str]
    leading_themes:     list[str]
    surge_count:        int
    plunge_count:       int
    indices:            list[dict]
    market_regime:      dict | None = None
    reasons:            list[str]
    is_order_signal:    bool
    created_at:         str


@router.post("/market-observer", response_model=MarketObserverOut)
def post_market_observer(req: MarketObserverIn) -> MarketObserverOut:
    """장중 시장 환경 snapshot 생성 (read-only).

    **broker 호출 0건, audit row 0건, DB 변경 0건, 외부 네트워크 호출 0건.**
    응답의 `is_order_signal`은 항상 False — 본 snapshot은 *주문 신호가 아님*을
    명시. caller는 BUY/SELL/HOLD를 추론하지 *말고* 다른 Agent / 운영자가 참고할
    context로만 사용.
    """
    # market_regime_input이 주어지면 classify해서 carry. 알 수 없는 키는 무시.
    regime = None
    if req.market_regime_input:
        ri = req.market_regime_input
        regime = classify_market_regime(
            trend_strength_pct=float(ri.get("trend_strength_pct") or 0.0),
            volatility_pct=float(ri.get("volatility_pct") or 0.0),
            volume_ratio=float(ri.get("volume_ratio") or 1.0),
            gap_pct=float(ri.get("gap_pct") or 0.0),
            news_sentiment=int(ri.get("news_sentiment") or 50),
            is_opening_30min=bool(ri.get("is_opening_30min") or False),
            is_late_day_30min=bool(ri.get("is_late_day_30min") or False),
            risk_off_signal=bool(ri.get("risk_off_signal") or False),
        )

    inp = MarketObserverInput(
        indices=[IndexQuote(**q.model_dump()) for q in (req.indices or [])],
        turnover_vs_avg=req.turnover_vs_avg,
        volatility_pct=req.volatility_pct,
        leading_sectors=req.leading_sectors,
        lagging_sectors=req.lagging_sectors,
        leading_themes=req.leading_themes,
        surge_count=req.surge_count,
        plunge_count=req.plunge_count,
        data_freshness_seconds=req.data_freshness_seconds,
        market_regime=regime,
    )
    snap = observe_market(inp)
    return MarketObserverOut(**snap.to_dict())


# ====================================================================
# #53: News / Trend Agent — context-only (NOT an order signal)
# ====================================================================


class NewsTrendOut(BaseModel):
    recommended_action:       str
    summary_lines:            list[str]
    top_themes:               list[dict]
    rising_keywords:          list[dict]
    related_candidates:       list[dict]
    caution_themes:           list[dict]
    overheating_warnings:     list[str]
    used_for_order_warnings:  list[str]
    total_signal_count:       int
    window_seconds:           int | None = None
    is_order_signal:          bool
    created_at:               str


@router.get("/news-trend", response_model=NewsTrendOut)
def get_news_trend(
    limit:     int = Query(100, ge=1, le=500),
    min_score: int | None = Query(None, ge=0, le=100),
    db:        _Session = Depends(get_db),
) -> NewsTrendOut:
    """ThemeSignal 테이블을 read-only로 요약. **broker 호출 0건, audit row
    0건, INSERT/UPDATE/DELETE 0건, 외부 API 호출 0건.**

    응답의 `is_order_signal`은 항상 False — 본 요약은 *주문 신호가 아님*을
    명시. caller는 BUY/SELL/HOLD를 추론하지 *말고* 후보 필터 / Agent context
    로만 사용.
    """
    signals = load_recent_theme_signals(db, limit=limit, min_score=min_score)
    out = summarize_themes(signals)
    return NewsTrendOut(**out.to_dict())
