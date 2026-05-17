"""AI Paper Auto Loop API + Desktop health.

EXE 의 시작/정지/긴급정지 3 버튼이 호출하는 endpoints + desktop launcher 가
polling 하는 health. PAPER/SIMULATION 한정 — live broker / OrderExecutor /
route_order import 0건.

응답은 Secret / API key / 계좌번호 0건. 안전 flag 라벨만 carry.

feat/step2-05-pre-market-gate: `POST /api/auto-paper/start` 는 optional
body `{ pre_market: { start_allowed, verdict, blocking_reasons, warnings } }`
를 받아 `start_allowed=False` 면 409 + blocking_reasons 로 차단. body 가
없으면 (legacy compat) 게이트 건너뜀 — frontend 는 항상 동봉.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.auto_paper.loop import (
    LoopAlreadyRunningError,
    LoopBlockedError,
    LoopNotRunningError,
    LoopPreMarketBlockedError,
    PreMarketSummary,
    get_auto_paper_loop,
)
from app.auto_paper.ledger import get_ledger
from app.auto_paper.events import DecisionAction
from app.core.config import get_settings


router = APIRouter(tags=["auto-paper"])


@router.get("/desktop/health")
def desktop_health() -> dict:
    """EXE launcher 가 connectivity 확인용으로 호출. Secret 0건."""
    settings = get_settings()
    loop = get_auto_paper_loop()
    return {
        "ok": True,
        "app": settings.app_name,
        "env": settings.app_env,
        "default_mode": settings.default_mode.value,
        "safety_flags": {
            "enable_live_trading":         settings.enable_live_trading,
            "enable_ai_execution":         settings.enable_ai_execution,
            "enable_futures_live_trading": settings.enable_futures_live_trading,
            "kis_is_paper":                settings.kis_is_paper,
        },
        "auto_paper": loop.status().to_dict(),
        "advisory_only": True,
    }


_AP = APIRouter(prefix="/auto-paper", tags=["auto-paper"])


# ─────────────────────────────────────────────────────────────────────
# Pre-market gate payload schema
# ─────────────────────────────────────────────────────────────────────


class _PreMarketBody(BaseModel):
    """Pre-market checklist 결과의 compact carry — frontend → start 호출.

    full `PreMarketCheckResult` 의 부분집합. `app.governance.pre_market_check`
    와 결합도 분리.
    """
    start_allowed:    bool       = Field(..., description="False 면 start() 차단")
    verdict:          str        = Field(
        default="",
        description="READY_TO_START / WARN_BUT_START_ALLOWED / DO_NOT_START",
    )
    blocking_reasons: list[str]  = Field(default_factory=list)
    warnings:         list[str]  = Field(default_factory=list)


class _StartBody(BaseModel):
    """`POST /auto-paper/start` body. 모두 optional — body 없이도 호출 가능."""
    pre_market: Optional[_PreMarketBody] = None


@_AP.get("/status")
def get_status() -> dict:
    return get_auto_paper_loop().status().to_dict()


@_AP.post("/start")
def post_start(body: _StartBody | None = None) -> dict:
    """자동 시작.

    feat/step2-05-pre-market-gate: `body.pre_market.start_allowed=False` 면
    `409 Conflict` + detail.blocking_reasons 로 차단. blocking_reasons 는
    Secret 0건 (pre_market_check 모듈이 라벨만 emit). frontend 가 표시.
    """
    loop = get_auto_paper_loop()
    pm: PreMarketSummary | None = None
    if body is not None and body.pre_market is not None:
        pm = PreMarketSummary(
            start_allowed=body.pre_market.start_allowed,
            verdict=body.pre_market.verdict,
            blocking_reasons=list(body.pre_market.blocking_reasons),
            warnings=list(body.pre_market.warnings),
        )
    try:
        snap = loop.start(pre_market=pm)
    except LoopPreMarketBlockedError as e:
        # Pre-market BLOCK — 차단 사유 구조화 응답.
        raise HTTPException(
            status_code=409,
            detail={
                "error":            "pre_market_blocked",
                "message":          str(e),
                "verdict":          e.verdict,
                "blocking_reasons": e.blocking_reasons,
            },
        )
    except LoopAlreadyRunningError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except LoopBlockedError as e:
        # EMERGENCY_STOP 상태에서 start() 차단 — 운영자가 reset() 호출 후
        # 재시도해야 함. 409 Conflict 로 표현 (이미 다른 상태에 잠겨 있음).
        raise HTTPException(status_code=409, detail=str(e))
    return snap.to_dict()


@_AP.post("/stop")
def post_stop() -> dict:
    loop = get_auto_paper_loop()
    try:
        snap = loop.stop()
    except LoopNotRunningError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return snap.to_dict()


@_AP.post("/emergency-stop")
def post_emergency_stop() -> dict:
    return get_auto_paper_loop().emergency_stop().to_dict()


@_AP.post("/reset")
def post_reset() -> dict:
    return get_auto_paper_loop().reset().to_dict()


# ─────────────────────────────────────────────────────────────────────────────
# #2-09: Paper Auto Loop ledger (read-only)
# ─────────────────────────────────────────────────────────────────────────────


def _serialize_ledger_response(
    *,
    limit:    int            = 50,
    state:    str | None     = None,
    strategy: str | None     = None,
    symbol:   str | None     = None,
    action:   str | None     = None,
) -> dict:
    """단일 직렬화 — `/ledger` 와 `/events` alias 가 공유."""
    ledger = get_ledger()
    # decision_action filter — enum 값 검증.
    action_enum: DecisionAction | None = None
    if action is not None:
        try:
            action_enum = DecisionAction(action.upper())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"invalid decision_action: {action!r}",
            )
    if any(v is not None for v in (state, strategy, symbol, action_enum)):
        events = ledger.filter_by(
            loop_state=state, strategy=strategy, symbol=symbol,
            decision_action=action_enum,
        )
        events = events[-max(1, int(limit)):]
    else:
        events = ledger.recent(limit=max(1, int(limit)))
    return {
        "is_order_signal":        False,
        "auto_apply_allowed":     False,
        "is_live_authorization":  False,
        "advisory_disclaimer": (
            "Paper Auto Loop 의 advisory ledger — Paper 가상 체결 / AI 판단만 "
            "기록. 실 broker 호출 0건."
        ),
        "events":      [e.to_dict() for e in events],
        "event_count": len(events),
        "stats":       ledger.stats(),
        "filters": {
            "limit":    int(limit),
            "state":    state,
            "strategy": strategy,
            "symbol":   symbol,
            "action":   action,
        },
    }


@_AP.get("/ledger")
def get_ledger_endpoint(
    limit:    int            = 50,
    state:    str | None     = None,
    strategy: str | None     = None,
    symbol:   str | None     = None,
    action:   str | None     = None,
) -> dict:
    """Paper Auto Loop ledger — 최근 event read-only.

    응답 invariant: `is_order_signal=False` / `auto_apply_allowed=False` /
    `is_live_authorization=False` carry. Secret / API key / 계좌번호 필드 0건.
    """
    return _serialize_ledger_response(
        limit=limit, state=state, strategy=strategy, symbol=symbol, action=action,
    )


@_AP.get("/events")
def get_events_endpoint(
    limit:    int            = 50,
    state:    str | None     = None,
    strategy: str | None     = None,
    symbol:   str | None     = None,
    action:   str | None     = None,
) -> dict:
    """ledger alias — 운영자 친화 두 번째 경로 (`/events`)."""
    return _serialize_ledger_response(
        limit=limit, state=state, strategy=strategy, symbol=symbol, action=action,
    )


router.include_router(_AP)
