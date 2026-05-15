"""AI Paper Auto Loop API — EXE 원클릭 시작/정지/긴급정지.

EXE 의 시작/정지/긴급정지 3 버튼이 호출하는 4 endpoints + desktop health.
PAPER/SIMULATION 한정 — live broker / OrderExecutor / route_order import 0건.

Endpoints:
    GET  /api/desktop/health                — 데스크톱 launcher 친화 health
    GET  /api/auto-paper/status             — 현재 상태 스냅샷
    POST /api/auto-paper/start              — RUNNING 전환
    POST /api/auto-paper/stop               — STOPPED 전환
    POST /api/auto-paper/emergency-stop     — EMERGENCY 전환
    POST /api/auto-paper/reset              — IDLE 로 리셋 (운영자 명시)

응답은 Secret / API key / 계좌번호 0건. 안전 flag 라벨만 carry.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.auto_paper.loop import (
    AutoPaperState,
    LoopAlreadyRunningError,
    LoopNotRunningError,
    get_auto_paper_loop,
)
from app.core.config import get_settings


router = APIRouter(tags=["auto-paper"])


# ----------------------------------------------------------------------
# 데스크톱 health — launcher 가 polling
# ----------------------------------------------------------------------


@router.get("/desktop/health")
def desktop_health() -> dict:
    """EXE launcher 가 connectivity 확인용으로 호출. Secret 0건.

    응답에는 *안전 flag 라벨* 만 carry — KIS app key / secret / 계좌번호 /
    Anthropic key 등은 *참조도 하지 않는다*.
    """
    settings = get_settings()
    loop = get_auto_paper_loop()
    return {
        "ok": True,
        "app": settings.app_name,
        "env": settings.app_env,
        "default_mode": settings.default_mode.value,
        # 안전 flag 라벨 - 값만 carry.
        "safety_flags": {
            "enable_live_trading":         settings.enable_live_trading,
            "enable_ai_execution":         settings.enable_ai_execution,
            "enable_futures_live_trading": settings.enable_futures_live_trading,
            "kis_is_paper":                settings.kis_is_paper,
        },
        "auto_paper": loop.status().to_dict(),
        # 본 응답에는 Secret 포함 0건 (테스트로 lock).
        "advisory_only": True,
    }


# ----------------------------------------------------------------------
# /api/auto-paper/*
# ----------------------------------------------------------------------


_AP = APIRouter(prefix="/auto-paper", tags=["auto-paper"])


@_AP.get("/status")
def get_status() -> dict:
    return get_auto_paper_loop().status().to_dict()


@_AP.post("/start")
def post_start() -> dict:
    """RUNNING 으로 전환. 이미 RUNNING 이면 409.

    *forced_paper* — settings.enable_live_trading 가 True 여도 본 loop 은
    live broker 진입 0건. tick 은 placeholder.
    """
    loop = get_auto_paper_loop()
    try:
        snap = loop.start()
    except LoopAlreadyRunningError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return snap.to_dict()


@_AP.post("/stop")
def post_stop() -> dict:
    """RUNNING → STOPPED. RUNNING 이 아니면 409 (멱등 아님 — 명시 오류)."""
    loop = get_auto_paper_loop()
    try:
        snap = loop.stop()
    except LoopNotRunningError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return snap.to_dict()


@_AP.post("/emergency-stop")
def post_emergency_stop() -> dict:
    """모든 상태에서 EMERGENCY 로. 멱등 — 이미 EMERGENCY 여도 200.

    본 endpoint 는 broker 호출 0건 — 상태 라벨만 변경. 실제 미체결 주문
    취소 / 청산은 별도 #37 Kill Switch (`/risk/emergency-stop`) 흐름.
    """
    loop = get_auto_paper_loop()
    return loop.emergency_stop().to_dict()


@_AP.post("/reset")
def post_reset() -> dict:
    """EMERGENCY / STOPPED → IDLE. 운영자가 명시 호출하지 않으면 호출 0."""
    return get_auto_paper_loop().reset().to_dict()


# ----------------------------------------------------------------------
# combine sub-router into module router
# ----------------------------------------------------------------------

router.include_router(_AP)
