"""AI Paper Auto Loop API + Desktop health.

EXE 의 시작/정지/긴급정지 3 버튼이 호출하는 endpoints + desktop launcher 가
polling 하는 health. PAPER/SIMULATION 한정 — live broker / OrderExecutor /
route_order import 0건.

응답은 Secret / API key / 계좌번호 0건. 안전 flag 라벨만 carry.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.auto_paper.loop import (
    LoopAlreadyRunningError,
    LoopBlockedError,
    LoopNotRunningError,
    get_auto_paper_loop,
)
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


@_AP.get("/status")
def get_status() -> dict:
    return get_auto_paper_loop().status().to_dict()


@_AP.post("/start")
def post_start() -> dict:
    loop = get_auto_paper_loop()
    try:
        snap = loop.start()
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


router.include_router(_AP)
