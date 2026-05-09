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

from datetime import datetime, timezone
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
from app.agents.risk_auditor import (
    RiskAuditorInput,
    audit_risk,
    load_recent_agent_decisions,
    load_recent_audit_rows,
    load_recent_emergency_events,
)
from app.agents.strategy_researcher import (
    BacktestSummary,
    DataQualitySummary,
    MonteCarloSummary,
    PromotionGateSummary,
    StrategyResearcherInput,
    WalkForwardSummary,
    analyze_strategy,
    load_backtest_run,
    load_recent_backtest_runs,
)
from app.agents.daily_report_agent import (
    DailyReportInput,
    analyze_daily,
    load_agent_decisions_for_date,
    load_audit_rows_for_date,
    load_backtest_runs_for_date,
    load_emergency_events_for_date,
    load_futures_audit_for_date,
    load_pending_approvals_for_date,
    load_virtual_orders_for_date,
)
from app.backtest.metrics import summarize_metrics
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


# ====================================================================
# #54: Risk Auditor — 안전 감독 advisory (NOT order signal)
# ====================================================================


class RiskEventOut(BaseModel):
    type:               str
    severity:           str
    summary:            str
    evidence:           dict
    symbol:             str | None = None
    strategy:           str | None = None
    recommended_action: str | None = None


class RiskAuditorReportOut(BaseModel):
    audit_level:                       str
    risk_score:                        int
    summary_lines:                     list[str]
    events:                            list[RiskEventOut]
    pause_trading_recommended:         bool
    emergency_stop_recommended:        bool
    recommended_stop_reason:           str | None = None
    window_seconds:                    int | None = None
    total_audit_rows_inspected:        int
    total_emergency_events_inspected:  int
    is_order_signal:                   bool
    created_at:                        str


@router.get("/risk-auditor/report", response_model=RiskAuditorReportOut)
def get_risk_auditor_report(
    window_seconds:     int = Query(3600, ge=60, le=86400),
    daily_realized_pnl: int = Query(0),
    max_daily_loss:     int = Query(0, ge=0),
    margin_risk_pct:    float | None = Query(None, ge=0, le=200),
    futures_liquidation_pct: float | None = Query(None, ge=0, le=100),
    db:                 _Session = Depends(get_db),
) -> RiskAuditorReportOut:
    """장중 리스크 감사 리포트 (read-only).

    **broker 호출 0건, audit row 0건, DB write 0건, emergency_stop 토글 0건,
    외부 API 호출 0건.** 응답의 `pause_trading_recommended` /
    `emergency_stop_recommended`는 *advisory* — 실제 토글은 운영자/Kill Switch
    UI에서 수동 수행.
    """
    from datetime import datetime, timedelta, timezone
    since = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    inp = RiskAuditorInput(
        audit_rows=load_recent_audit_rows(db, since=since),
        emergency_events=load_recent_emergency_events(db, since=since),
        agent_decisions=load_recent_agent_decisions(db, since=since),
        daily_realized_pnl=daily_realized_pnl,
        max_daily_loss=max_daily_loss,
        window_seconds=window_seconds,
        margin_risk_pct=margin_risk_pct,
        futures_liquidation_pct=futures_liquidation_pct,
    )
    report = audit_risk(inp)
    return RiskAuditorReportOut(**report.to_dict())


class RiskAuditorMockIn(BaseModel):
    """`/risk-auditor/mock` 입력 — DB 의존 없이 deterministic 시뮬.

    각 카운트 필드는 *fake event 수* — 본 endpoint는 이 카운트만큼 fake row
    를 만들어 audit_risk()에 주입한다. broker / audit row 변경 0건.
    """
    rejected_count:          int   = 0
    duplicate_rejected_count: int  = 0
    stale_rejected_count:    int   = 0
    broker_error_count:      int   = 0
    ai_high_conf_rejected:   int   = 0
    ai_low_conf_count:       int   = 0
    emergency_toggle_count:  int   = 0
    agent_warn_count:        int   = 0
    daily_realized_pnl:      int   = 0
    max_daily_loss:          int   = 0
    margin_risk_pct:         float | None = None
    futures_liquidation_pct: float | None = None
    window_seconds:          int   = 3600


@router.post("/risk-auditor/mock", response_model=RiskAuditorReportOut)
def post_risk_auditor_mock(req: RiskAuditorMockIn) -> RiskAuditorReportOut:
    """DB 의존 없는 deterministic mock — 운영자가 시나리오를 직접 흘려
    Risk Auditor 동작을 검증할 수 있다. broker 호출 0건, DB 변경 0건."""

    # In-memory fake row builder — DB row 객체와 attribute 호환만 맞추면 됨.
    class _Row:
        def __init__(self, **kw): self.__dict__.update(kw)

    audit_rows = []
    # rejected baseline
    for i in range(req.rejected_count):
        audit_rows.append(_Row(
            id=i + 1, decision="REJECTED",
            reasons=["mock rejection"], message="",
            requested_by_ai=False, signal_confidence=None,
        ))
    for i in range(req.duplicate_rejected_count):
        audit_rows.append(_Row(
            id=10_000 + i, decision="REJECTED",
            reasons=["duplicate fingerprint"], message="",
            requested_by_ai=False, signal_confidence=None,
        ))
    for i in range(req.stale_rejected_count):
        audit_rows.append(_Row(
            id=20_000 + i, decision="REJECTED",
            reasons=["stale price (60s+)"], message="",
            requested_by_ai=False, signal_confidence=None,
        ))
    for i in range(req.broker_error_count):
        audit_rows.append(_Row(
            id=30_000 + i, decision="REJECTED",
            reasons=[], message="broker error: timeout",
            requested_by_ai=False, signal_confidence=None,
        ))
    for i in range(req.ai_high_conf_rejected):
        audit_rows.append(_Row(
            id=40_000 + i, decision="REJECTED",
            reasons=["ai overconf"], message="",
            requested_by_ai=True, signal_confidence=90,
        ))
    for i in range(req.ai_low_conf_count):
        audit_rows.append(_Row(
            id=50_000 + i, decision="APPROVED",
            reasons=[], message="",
            requested_by_ai=True, signal_confidence=20,
        ))
    emergency_events = [
        _Row(id=i, enabled=(i % 2 == 0), reason_code=None, level=None)
        for i in range(req.emergency_toggle_count)
    ]
    agent_decisions = [
        _Row(id=i, decision="WARN", agent_name="mock", reasons=[], meta=None)
        for i in range(req.agent_warn_count)
    ]
    inp = RiskAuditorInput(
        audit_rows=audit_rows,
        emergency_events=emergency_events,
        agent_decisions=agent_decisions,
        daily_realized_pnl=req.daily_realized_pnl,
        max_daily_loss=req.max_daily_loss,
        window_seconds=req.window_seconds,
        margin_risk_pct=req.margin_risk_pct,
        futures_liquidation_pct=req.futures_liquidation_pct,
    )
    report = audit_risk(inp)
    return RiskAuditorReportOut(**report.to_dict())


# ====================================================================
# 55: Strategy Researcher Agent — read-only advisory.
#
# 본 라우트는 BacktestRun + 외부 검증 결과를 분석해 markdown 리포트와 구조화된
# 제안을 *반환*만 한다. **자동 적용 / 자동 코드 수정 / 자동 파라미터 저장 / 자동
# 주문 0건** — 모든 출력은 운영자가 별도 PR / 별도 백테스트 / paper / shadow를
# 거쳐야만 실제 변경에 반영된다.
# ====================================================================


class StrategyFindingOut(BaseModel):
    code:        str
    severity:    str
    summary:     str
    metric_name: str | None = None
    metric_value: float | None = None
    threshold:   float | None = None
    detail:      dict[str, Any] = {}


class StrategySuggestionOut(BaseModel):
    category:            str
    severity:            str
    title:               str
    rationale:           str
    proposed_change:     str
    required_validation: list[str] = []
    references:          list[str] = []


class StrategyResearchReportOut(BaseModel):
    audit_level:         str
    findings:            list[StrategyFindingOut]
    suggestions:         list[StrategySuggestionOut]
    required_next_tests: list[str]
    markdown_report:     str
    summary_lines:       list[str]
    strategy:            str
    run_id:              int
    auto_apply_allowed:  bool
    is_order_signal:     bool
    created_at:          str


class StrategyResearcherRecentItemOut(BaseModel):
    run_id:        int
    strategy:      str
    created_at:    str
    audit_level:   str
    findings_count:    int
    suggestions_count: int
    summary_line:  str


class StrategyResearcherRecentOut(BaseModel):
    items: list[StrategyResearcherRecentItemOut]


class StrategyResearcherWalkForwardIn(BaseModel):
    recommendation:           str | None = None
    fold_count:               int = 0
    positive_fold_ratio:      float | None = None
    single_best_fold_share:   float | None = None
    overfit_risk_score:       float | None = None
    holdout_pnl:              int | None = None
    warnings:                 list[str] = []


class StrategyResearcherMonteCarloIn(BaseModel):
    method:                   str | None = None
    iterations:               int = 0
    risk_of_ruin:             float | None = None
    p05_total_pnl:            int | None = None
    p50_total_pnl:            int | None = None
    p95_total_pnl:            int | None = None
    worst_5pct_avg_mdd:       int | None = None
    promotion_risk_flag:      str | None = None
    stability_grade:          str | None = None
    warnings:                 list[str] = []


class StrategyResearcherDataQualityIn(BaseModel):
    symbol:        str
    interval:      str = "1d"
    score:         float | None = None
    grade:         str | None = None
    missing_rate:  float | None = None
    coverage_score: float | None = None
    notes:         list[str] = []


class StrategyResearcherPromotionIn(BaseModel):
    current_stage:    str | None = None
    target_stage:     str | None = None
    decision:         str | None = None
    failed_criteria:  list[str] = []
    cautions:         list[str] = []
    required_actions: list[str] = []


class StrategyResearcherMockIn(BaseModel):
    """`/strategy-researcher/mock` 입력 — 외부 검증 결과를 운영자가 직접 주입."""
    backtest_run_id:  int | None = None
    walk_forward:     StrategyResearcherWalkForwardIn | None = None
    monte_carlo:      StrategyResearcherMonteCarloIn | None = None
    data_quality:     list[StrategyResearcherDataQualityIn] = []
    promotion_gate:   StrategyResearcherPromotionIn | None = None
    operator_note:    str | None = None


def _serialize_strategy_report(report) -> StrategyResearchReportOut:
    return StrategyResearchReportOut(
        audit_level=str(report.audit_level),
        findings=[
            StrategyFindingOut(
                code=str(f.code),
                severity=str(f.severity),
                summary=f.summary,
                metric_name=f.metric_name,
                metric_value=f.metric_value,
                threshold=f.threshold,
                detail=f.detail or {},
            ) for f in report.findings
        ],
        suggestions=[
            StrategySuggestionOut(
                category=str(s.category),
                severity=str(s.severity),
                title=s.title,
                rationale=s.rationale,
                proposed_change=s.proposed_change,
                required_validation=list(s.required_validation),
                references=list(s.references),
            ) for s in report.suggestions
        ],
        required_next_tests=list(report.required_next_tests),
        markdown_report=report.markdown_report,
        summary_lines=list(report.summary_lines),
        strategy=report.strategy,
        run_id=report.run_id,
        auto_apply_allowed=report.auto_apply_allowed,
        is_order_signal=report.is_order_signal,
        created_at=report.created_at.isoformat(),
    )


def _build_backtest_summary(run) -> BacktestSummary:
    """`BacktestRun` row → `BacktestSummary` (metrics #24 호출)."""
    trades = run.trades_json or []
    metrics = summarize_metrics(trades, initial_cash=run.initial_cash)
    return BacktestSummary(
        run_id=run.id,
        strategy=run.strategy,
        created_at=run.created_at,
        params=run.params or {},
        initial_cash=run.initial_cash,
        bars_processed=run.bars_processed,
        trade_count=metrics.get("trade_count", 0),
        win_count=metrics.get("win_count", 0),
        loss_count=metrics.get("loss_count", 0),
        total_pnl=metrics.get("total_pnl", 0),
        final_cash=run.final_cash,
        win_rate=metrics.get("win_rate"),
        profit_factor=metrics.get("profit_factor"),
        expectancy=metrics.get("expectancy"),
        max_drawdown=metrics.get("max_drawdown", 0),
        max_consecutive_losses=metrics.get("max_consecutive_losses", 0),
        max_consecutive_wins=metrics.get("max_consecutive_wins", 0),
        sharpe_ratio=metrics.get("sharpe_ratio"),
        avg_win=metrics.get("avg_win"),
        avg_loss=metrics.get("avg_loss"),
        hourly_pnl=metrics.get("hourly_pnl") or {},
        data_symbol=run.data_symbol,
        data_interval=run.data_interval,
        data_start=run.data_start,
        data_end=run.data_end,
    )


@router.get(
    "/strategy-researcher/recent",
    response_model=StrategyResearcherRecentOut,
)
def strategy_researcher_recent(
    limit:    int = Query(20, ge=1, le=100),
    strategy: str | None = Query(None),
    db:       _Session = Depends(get_db),
) -> StrategyResearcherRecentOut:
    """최근 BacktestRun 목록 — 각 run에 대해 audit_level 미리보기.

    *advisory* 응답이며, broker 호출 0건 / DB write 0건.
    """
    runs = load_recent_backtest_runs(db, strategy=strategy, limit=limit)
    items: list[StrategyResearcherRecentItemOut] = []
    for run in runs:
        bt = _build_backtest_summary(run)
        report = analyze_strategy(StrategyResearcherInput(backtest=bt))
        items.append(StrategyResearcherRecentItemOut(
            run_id=run.id,
            strategy=run.strategy,
            created_at=run.created_at.isoformat(),
            audit_level=str(report.audit_level),
            findings_count=len(report.findings),
            suggestions_count=len(report.suggestions),
            summary_line=report.summary_lines[0] if report.summary_lines else "",
        ))
    return StrategyResearcherRecentOut(items=items)


@router.get(
    "/strategy-researcher/report/{run_id}",
    response_model=StrategyResearchReportOut,
)
def strategy_researcher_report(
    run_id: int,
    db:     _Session = Depends(get_db),
) -> StrategyResearchReportOut:
    """단일 BacktestRun을 분석해 markdown advisory report 반환.

    *advisory*만 — broker 호출 0건, DB write 0건, 코드/파라미터 자동 변경 0건.
    """
    run = load_backtest_run(db, run_id)
    if run is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"BacktestRun {run_id} not found")
    bt = _build_backtest_summary(run)
    report = analyze_strategy(StrategyResearcherInput(backtest=bt))
    return _serialize_strategy_report(report)


@router.post(
    "/strategy-researcher/mock",
    response_model=StrategyResearchReportOut,
)
def strategy_researcher_mock(
    body: StrategyResearcherMockIn,
    db:   _Session = Depends(get_db),
) -> StrategyResearchReportOut:
    """deterministic mock — backtest_run_id가 있으면 DB에서 BacktestRun을 읽어
    summary로 변환하고, 운영자가 외부 검증 결과(walk_forward / monte_carlo /
    data_quality / promotion_gate)를 직접 주입해 advisory 리포트를 받는다.

    *advisory*만 — broker 호출 0건, DB write 0건.
    """
    if body.backtest_run_id is None:
        bt = BacktestSummary(
            run_id=0, strategy="mock",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            initial_cash=0, bars_processed=0,
            trade_count=0, win_count=0, loss_count=0,
            total_pnl=0, final_cash=0,
            max_drawdown=0, max_consecutive_losses=0, max_consecutive_wins=0,
        )
    else:
        run = load_backtest_run(db, body.backtest_run_id)
        if run is None:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=404,
                detail=f"BacktestRun {body.backtest_run_id} not found",
            )
        bt = _build_backtest_summary(run)

    wf = None
    if body.walk_forward is not None:
        wf = WalkForwardSummary(
            recommendation=body.walk_forward.recommendation,
            fold_count=body.walk_forward.fold_count,
            positive_fold_ratio=body.walk_forward.positive_fold_ratio,
            single_best_fold_share=body.walk_forward.single_best_fold_share,
            overfit_risk_score=body.walk_forward.overfit_risk_score,
            holdout_pnl=body.walk_forward.holdout_pnl,
            warnings=tuple(body.walk_forward.warnings),
        )
    mc = None
    if body.monte_carlo is not None:
        mc = MonteCarloSummary(
            method=body.monte_carlo.method,
            iterations=body.monte_carlo.iterations,
            risk_of_ruin=body.monte_carlo.risk_of_ruin,
            p05_total_pnl=body.monte_carlo.p05_total_pnl,
            p50_total_pnl=body.monte_carlo.p50_total_pnl,
            p95_total_pnl=body.monte_carlo.p95_total_pnl,
            worst_5pct_avg_mdd=body.monte_carlo.worst_5pct_avg_mdd,
            promotion_risk_flag=body.monte_carlo.promotion_risk_flag,
            stability_grade=body.monte_carlo.stability_grade,
            warnings=tuple(body.monte_carlo.warnings),
        )
    quality = tuple(
        DataQualitySummary(
            symbol=q.symbol,
            interval=q.interval,
            score=q.score,
            grade=q.grade,
            missing_rate=q.missing_rate,
            coverage_score=q.coverage_score,
            notes=tuple(q.notes),
        )
        for q in body.data_quality
    )
    pg = None
    if body.promotion_gate is not None:
        pg = PromotionGateSummary(
            current_stage=body.promotion_gate.current_stage,
            target_stage=body.promotion_gate.target_stage,
            decision=body.promotion_gate.decision,
            failed_criteria=tuple(body.promotion_gate.failed_criteria),
            cautions=tuple(body.promotion_gate.cautions),
            required_actions=tuple(body.promotion_gate.required_actions),
        )

    inp = StrategyResearcherInput(
        backtest=bt,
        walk_forward=wf,
        monte_carlo=mc,
        data_quality=quality,
        promotion_gate=pg,
        operator_note=body.operator_note,
    )
    report = analyze_strategy(inp)
    return _serialize_strategy_report(report)


# ====================================================================
# 57: Daily Report Agent — read-only advisory + optional file write.
#
# 본 라우트는 OrderAuditLog / VirtualOrder / FuturesOrderAuditLog /
# AgentDecisionLog / EmergencyStopEvent / PendingApproval / BacktestRun을
# read-only로 분석해 markdown 리포트를 생성한다. 본 리포트는 *투자 조언이
# 아니며*, 종목 추천 / 매수 매도 신호를 포함하지 않는다.
# ====================================================================


from datetime import date as _Date  # noqa: E402
from pathlib import Path as _Path   # noqa: E402


class DailyReportPreviewOut(BaseModel):
    report_date:        str
    markdown_report:    str
    summary_lines:      list[str]
    findings_count:     int
    warnings_count:     int
    action_items_count: int
    auto_apply_allowed: bool
    is_order_signal:    bool
    notice:             str = (
        "본 리포트는 *투자 조언이 아니라* 시스템 운영 / 검증 / 개선 자료입니다. "
        "종목 추천 / 매수 매도 신호 없음."
    )


class DailyReportGenerateIn(BaseModel):
    date:             str | None = None    # YYYY-MM-DD; None이면 오늘 (UTC)
    output_dir:       str = "reports"
    include_virtual:  bool = True
    include_futures:  bool = True


class DailyReportGenerateOut(BaseModel):
    report_date:        str
    output_path:        str
    bytes_written:      int
    findings_count:     int
    warnings_count:     int
    action_items_count: int
    notice:             str = (
        "본 호출은 reports/ 디렉토리에 markdown 파일을 작성합니다. "
        "broker / OrderExecutor / route_order 호출 0건. 투자 조언 아님."
    )


def _build_daily_input(db: _Session, report_date: _Date,
                       include_virtual: bool = True,
                       include_futures: bool = True) -> DailyReportInput:
    settings = get_settings()
    return DailyReportInput(
        report_date=report_date,
        operation_mode=settings.default_mode.value,
        audit_rows=tuple(load_audit_rows_for_date(db, report_date)),
        virtual_orders=(
            tuple(load_virtual_orders_for_date(db, report_date))
            if include_virtual else ()
        ),
        futures_audit_rows=(
            tuple(load_futures_audit_for_date(db, report_date))
            if include_futures else ()
        ),
        agent_decisions=tuple(load_agent_decisions_for_date(db, report_date)),
        emergency_events=tuple(load_emergency_events_for_date(db, report_date)),
        pending_approvals=tuple(load_pending_approvals_for_date(db, report_date)),
        backtest_runs=tuple(load_backtest_runs_for_date(db, report_date)),
    )


def _parse_report_date(s: str | None) -> _Date:
    if s is None:
        return datetime.now(timezone.utc).date()
    return datetime.strptime(s, "%Y-%m-%d").date()


@router.get("/daily-report/preview", response_model=DailyReportPreviewOut)
def daily_report_preview(
    date:    str | None = Query(None, description="YYYY-MM-DD (KST). 미지정 시 오늘."),
    include_virtual:  bool = Query(True),
    include_futures:  bool = Query(True),
    db:      _Session = Depends(get_db),
) -> DailyReportPreviewOut:
    """파일을 *작성하지 않고* markdown 미리보기 반환. broker 호출 0건, DB write 0건."""
    try:
        report_date = _parse_report_date(date)
    except ValueError:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date format: {date} (expected YYYY-MM-DD)",
        )
    inp = _build_daily_input(db, report_date,
                              include_virtual=include_virtual,
                              include_futures=include_futures)
    report = analyze_daily(inp)
    return DailyReportPreviewOut(
        report_date=report.report_date.isoformat(),
        markdown_report=report.markdown_report,
        summary_lines=list(report.summary_lines),
        findings_count=len(report.findings),
        warnings_count=len(report.tomorrow_warnings),
        action_items_count=len(report.action_items),
        auto_apply_allowed=report.auto_apply_allowed,
        is_order_signal=report.is_order_signal,
    )


@router.post("/daily-report/generate", response_model=DailyReportGenerateOut)
def daily_report_generate(
    body: DailyReportGenerateIn,
    db:   _Session = Depends(get_db),
) -> DailyReportGenerateOut:
    """`reports/daily_YYYY-MM-DD.md` 파일을 작성. broker 호출 0건, DB write 0건.

    output_dir은 backend/ 기준 상대 경로. 운영자가 명시 path를 줘서 임의의
    경로에 쓰는 것을 방지하고 싶으면 운영 환경에서 본 endpoint를 비활성화하고
    CLI(scripts/generate_daily_report.py)를 사용하세요.
    """
    try:
        report_date = _parse_report_date(body.date)
    except ValueError:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date format: {body.date} (expected YYYY-MM-DD)",
        )
    inp = _build_daily_input(db, report_date,
                              include_virtual=body.include_virtual,
                              include_futures=body.include_futures)
    report = analyze_daily(inp)

    out_dir = _Path(body.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"daily_{report.report_date.isoformat()}.md"
    out_path.write_text(report.markdown_report, encoding="utf-8")

    return DailyReportGenerateOut(
        report_date=report.report_date.isoformat(),
        output_path=str(out_path),
        bytes_written=len(report.markdown_report.encode("utf-8")),
        findings_count=len(report.findings),
        warnings_count=len(report.tomorrow_warnings),
        action_items_count=len(report.action_items),
    )
