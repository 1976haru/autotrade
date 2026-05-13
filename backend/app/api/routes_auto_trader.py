"""체크리스트 #60: AI Agent 기반 모의매매 라우트.

본 라우트는 *모의매매(SIMULATION/PAPER/VIRTUAL_AI_EXECUTION)* 한정 —
LIVE 모드는 backend에서 `AutoTraderAgent.run_once`가 RuntimeError로 차단한다.

라우트:
  GET  /api/auto-trader/status         — Agent 현재 상태 + 마지막 결정 + paper flag
  GET  /api/auto-trader/decisions      — 최근 결정 list (in-memory cache)
  GET  /api/auto-trader/portfolio      — 현재 가상 broker 잔고/포지션
  POST /api/auto-trader/run-once       — 한 번 실행 (시장 데이터 입력 + 결과)
  POST /api/auto-trader/emergency-stop — 비상 정지 토글 (편의 — /risk/emergency-stop alias)

모든 라우트는 `assert_paper_broker(broker)`를 통과해야만 동작 — 실 KIS live
broker가 주입되면 즉시 400.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.auto_trader_loop import (
    AutoTraderAgent,
    AutoTraderInput,
)
from app.api.deps import get_broker, get_risk_manager
from app.backtest.types import Bar
from app.brokers.base import BrokerAdapter
from app.core.config import get_settings
from app.core.modes import OperationMode
from app.db.session import get_db
from app.execution.paper_trader import (
    NotPaperBrokerError,
    assert_paper_broker,
    build_paper_status,
)
from app.risk.risk_manager import RiskManager


router = APIRouter(prefix="/auto-trader", tags=["auto-trader"])


@lru_cache
def _get_auto_trader_agent() -> AutoTraderAgent:
    """단일 인스턴스 — 메모리 캐시(`recent_reports`)가 GET endpoint로 surface."""
    return AutoTraderAgent()


# ====================================================================
# Schemas
# ====================================================================


class _BarIn(BaseModel):
    timestamp: str          # ISO datetime
    open:      int
    high:      int
    low:       int
    close:     int
    volume:    int = 0


class RunOnceIn(BaseModel):
    """`/run-once` 입력. 운영자/UI가 운영 흐름에 맞게 채워서 전달.

    `bars_by_symbol`은 종목별 시계열. ISO timestamp + OHLCV. 시뮬레이션
    경로에서는 호출자가 mock 데이터를 직접 보낸다. PAPER 경로에서는 backend
    가 별도 데이터 수집기로부터 받은 봉을 caller가 옮겨준다.
    """
    watchlist:        list[str]                 = Field(..., min_length=1, max_length=20)
    bars_by_symbol:   dict[str, list[_BarIn]]   = Field(default_factory=dict)
    strategy_names:   list[str]                 = Field(default_factory=list)
    min_confidence:   int                       = Field(65, ge=0, le=100)
    default_quantity: int                       = Field(1, ge=1, le=1000)
    mode:             str | None                = None
    note:             str | None                = None


class _PortfolioOut(BaseModel):
    cash:         int
    equity:       int
    buyingPower:  int
    positions:    list[dict[str, Any]]


class _RiskChecksOut(BaseModel):
    maxPositionOk:    bool
    dailyLossLimitOk: bool
    cooldownOk:       bool
    cashAvailableOk:  bool


class _StrategySignalOut(BaseModel):
    strategyId: str
    signal:     str
    confidence: int
    reason:     str
    indicators: dict[str, Any] = {}


class _AgentDecisionOut(BaseModel):
    action:         str
    symbol:         str
    confidence:     int
    positionSize:   int
    reason:         str
    usedStrategies: list[str]
    riskChecks:     _RiskChecksOut
    createdAt:      str
    isOrderIntent:  bool


class _PlanOut(BaseModel):
    symbol:           str
    strategySignals:  list[_StrategySignalOut]
    decision:         _AgentDecisionOut
    routingDecision:  str | None = None
    routingReasons:   list[str] = []
    auditId:          int | None = None
    executed:         bool = False
    fillQuantity:     int = 0
    fillPrice:        int | None = None
    blockedBy:        str | None = None
    error:            str | None = None


class RunOnceOut(BaseModel):
    mode:           str
    emergencyStop:  bool
    startedAt:      str
    finishedAt:     str
    plans:          list[_PlanOut]
    portfolio:      _PortfolioOut
    summary:        dict[str, int]
    notice:         str


class StatusOut(BaseModel):
    paperStatus:        dict[str, Any]
    lastReport:         RunOnceOut | None = None
    recentReportCount:  int
    emergencyStop:      bool
    enableLiveTrading:  bool
    enableAiExecution:  bool


class DecisionsOut(BaseModel):
    decisions:  list[dict[str, Any]]
    total:      int


class EmergencyStopToggleIn(BaseModel):
    enabled: bool
    note:    str | None = None


# ====================================================================
# Helpers
# ====================================================================


def _parse_mode(raw: str | None) -> OperationMode:
    if raw is None:
        return get_settings().default_mode
    try:
        return OperationMode(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"unknown mode: {raw!r} (valid: {[m.value for m in OperationMode]})",
        ) from exc


def _to_bars(symbol: str, items: list[_BarIn]) -> list[Bar]:
    bars: list[Bar] = []
    for it in items:
        try:
            ts = datetime.fromisoformat(it.timestamp)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"invalid timestamp in bars[{symbol}]: {it.timestamp!r}",
            ) from exc
        bars.append(Bar(
            symbol=symbol, timestamp=ts,
            open=int(it.open), high=int(it.high),
            low=int(it.low), close=int(it.close),
            volume=int(it.volume),
        ))
    return bars


def _ensure_paper(broker: BrokerAdapter) -> None:
    try:
        assert_paper_broker(broker)
    except NotPaperBrokerError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"AutoTrader refuses non-paper broker: {exc}",
        ) from exc


# ====================================================================
# Routes
# ====================================================================


@router.get("/status", response_model=StatusOut)
def get_status(broker: BrokerAdapter = Depends(get_broker),
               risk:   RiskManager   = Depends(get_risk_manager)) -> StatusOut:
    """Agent 현재 상태 + 마지막 결정 + 안전 flag 스냅샷.

    *read-only* — broker 호출 0건, route_order 호출 0건.
    """
    settings = get_settings()
    agent = _get_auto_trader_agent()
    last_report = agent.last_report()
    last_out: RunOnceOut | None = None
    if last_report is not None:
        last_out = RunOnceOut(**last_report.to_dict())
    return StatusOut(
        paperStatus=build_paper_status().to_dict(),
        lastReport=last_out,
        recentReportCount=len(agent.recent_reports),
        emergencyStop=bool(getattr(risk, "emergency_stop", False)),
        enableLiveTrading=bool(settings.enable_live_trading),
        enableAiExecution=bool(settings.enable_ai_execution),
    )


@router.get("/decisions", response_model=DecisionsOut)
def get_decisions(limit: int = Query(20, ge=1, le=200)) -> DecisionsOut:
    """최근 Agent 결정 list (in-memory cache). UI 표시용 read-only.

    `OrderAuditLog` (DB)와는 별개 — DB 기록은 `/api/audit/orders`에서 조회.
    """
    agent = _get_auto_trader_agent()
    decisions = agent.recent_decisions(limit=limit)
    return DecisionsOut(decisions=decisions, total=len(decisions))


@router.get("/portfolio", response_model=_PortfolioOut)
async def get_portfolio(broker: BrokerAdapter = Depends(get_broker)) -> _PortfolioOut:
    """현재 가상 broker의 cash / equity / 보유 포지션. read-only."""
    _ensure_paper(broker)
    balance = await broker.get_balance()
    positions = await broker.get_positions()
    return _PortfolioOut(
        cash=balance.cash,
        equity=balance.equity,
        buyingPower=balance.buying_power,
        positions=[p.model_dump() for p in positions],
    )


@router.post("/run-once", response_model=RunOnceOut)
async def post_run_once(
    body:   RunOnceIn,
    broker: BrokerAdapter = Depends(get_broker),
    risk:   RiskManager   = Depends(get_risk_manager),
    db:     Session       = Depends(get_db),
) -> RunOnceOut:
    """AI Agent 한 사이클 실행 — 가상 주문까지 완결.

    모드 가드: SIMULATION / PAPER / VIRTUAL_AI_EXECUTION 외에는 400.
    Broker 가드: live broker가 주입되면 400.
    """
    _ensure_paper(broker)
    mode = _parse_mode(body.mode)

    # bars 파싱
    bars_by_symbol = {
        sym: _to_bars(sym, items)
        for sym, items in body.bars_by_symbol.items()
    }

    inp = AutoTraderInput(
        watchlist=list(body.watchlist),
        bars_by_symbol=bars_by_symbol,
        strategy_names=list(body.strategy_names),
        min_confidence=int(body.min_confidence),
        default_quantity=int(body.default_quantity),
        mode=mode,
        note=body.note,
    )

    agent = _get_auto_trader_agent()
    try:
        report = await agent.run_once(inp, broker=broker, risk=risk, db=db)
    except RuntimeError as exc:
        # mode 차단 (LIVE) 또는 paper-safe 가드 실패
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotPaperBrokerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RunOnceOut(**report.to_dict())


@router.post("/emergency-stop")
def post_emergency_stop(
    body: EmergencyStopToggleIn,
    risk: RiskManager = Depends(get_risk_manager),
) -> dict[str, Any]:
    """비상 정지 토글 — *현재 in-memory만*. 영구화는 `/api/risk/emergency-stop`.

    AutoTraderAgent.run_once는 `risk.emergency_stop`을 매 호출마다 확인 —
    True면 모든 종목 plan을 `blocked_by=emergency_stop`으로 차단한다.
    """
    risk.set_emergency_stop(bool(body.enabled))
    return {
        "enabled":   bool(risk.emergency_stop),
        "note":      body.note,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "notice":    (
            "본 토글은 in-memory 상태만 변경합니다. 영구 기록 + 운영자 사유 "
            "기록은 POST /api/risk/emergency-stop을 사용하세요."
        ),
    }


__all__ = ["router"]
