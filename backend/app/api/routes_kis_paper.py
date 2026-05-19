"""KIS Paper one-click test routes (#89).

REST endpoints:
- GET  /api/kis-paper/readiness
- POST /api/kis-paper/start      (mode: quick / slow / mock)
- POST /api/kis-paper/stop
- GET  /api/kis-paper/status
- GET  /api/kis-paper/report

본 라우트는 *secret / 계좌번호 원문* 을 응답에 carry 하지 *않는다* — 존재
여부 (`*_present: bool`) 만 노출.

본 모듈은 broker / OrderExecutor / route_order 를 *직접 호출하지 않는다*.
실제 KIS paper 흐름은 engine 의 default tick runner 가 안전 단위로 처리.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.core.config import get_settings
from app.kis_paper.engine import (
    KisPaperRunState,
    TestMode,
    get_engine,
)
from app.kis_paper.readiness import evaluate_readiness


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/kis-paper", tags=["kis-paper"])


# ====================================================================
# Pydantic schemas
# ====================================================================


class KisPaperReadinessOut(BaseModel):
    ready:                bool
    can_run_kis_paper:    bool
    can_run_mock:         bool
    blocked_reasons:      list[str]
    detail_messages:      list[str]
    safety_flags:         dict
    # Secret 자체 0건 — 존재 여부만.
    kis_key_present:      bool
    kis_secret_present:   bool
    kis_account_present:  bool
    # fix/desktop-kis-env-readiness-load: 신규 alias + 진단 필드.
    # *secret 원문 0건* — boolean / 경로만.
    kis_app_key_present:    bool = False
    kis_app_secret_present: bool = False
    kis_account_no_present: bool = False
    kis_is_paper:           bool = True
    can_use_kis_paper:      bool = False
    env_file_found:         bool = False
    env_file_loaded:        bool = False
    env_loaded_path:        str  = ""
    is_order_intent:      bool
    is_order_signal:      bool


class KisPaperStartIn(BaseModel):
    mode: str   # quick / slow / mock
    # 운영자 명시 확인 — UI 가 "모의투자 주문 테스트 시작" 모달에서 true 보내야.
    confirm:    bool = False


class KisPaperStatusOut(BaseModel):
    state:         str
    mode:          str | None
    started_at:    str | None
    finished_at:   str | None
    counters:      dict
    failures:      list[str]


class KisPaperReportOut(BaseModel):
    mode:              str
    state:             str
    started_at:        str
    finished_at:       str | None
    duration_seconds:  float
    counters:          dict
    failures:          list[str]
    score:             dict
    safety_note:       str
    is_order_signal:   bool


# ====================================================================
# Routes
# ====================================================================


@router.get("/readiness", response_model=KisPaperReadinessOut)
def get_kis_paper_readiness() -> KisPaperReadinessOut:
    """preflight 검사. broker / KIS API 호출 0건 — 환경변수만 평가.

    fix/desktop-kis-env-readiness-load:
    매 호출마다 `get_settings.cache_clear()` 를 호출해 *직전에 로드된* .env
    값을 즉시 반영. 운영자가 .env 파일을 수정하고 UI 의 "준비상태 확인" 을
    누르면 새 값으로 재평가된다 (재시작 불필요).

    *주의*: cache_clear() 는 Settings *인스턴스* 만 무효화 — process env 의
    실제 값은 launcher / dotenv 가 갱신해야 보임. 본 endpoint 자체는 .env
    파일을 읽지 *않는다*.
    """
    get_settings.cache_clear()
    settings = get_settings()
    rd = evaluate_readiness(settings)
    return KisPaperReadinessOut(**rd.to_dict())


@router.post("/start", response_model=KisPaperStatusOut)
async def post_kis_paper_start(
    body: KisPaperStartIn,
    background_tasks: BackgroundTasks,
) -> KisPaperStatusOut:
    """one-click test 시작.

    body.mode ∈ {quick, slow, mock}.
    body.confirm 이 True 여야 진행 (UI 의 확인 모달 통과 강제).
    """
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="모의투자 테스트 시작 확인이 필요합니다 (confirm=true).",
        )

    try:
        mode = TestMode(body.mode.lower())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"알 수 없는 mode: {body.mode}. quick / slow / mock 중 하나.",
        )

    engine = get_engine()
    if engine.state == KisPaperRunState.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="이미 실행 중인 테스트가 있습니다 — stop 호출 후 재시작.",
        )

    settings = get_settings()
    rd = evaluate_readiness(settings)

    # 즉시 차단 — readiness BLOCKED 면 시작도 안 함.
    if mode in (TestMode.QUICK, TestMode.SLOW) and not rd.can_run_kis_paper:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "KIS paper 모드 진입 불가",
                "blocked_reasons": [r.value for r in rd.blocked_reasons],
                "details": list(rd.detail_messages),
            },
        )
    if mode == TestMode.MOCK and not rd.can_run_mock:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Mock 모드 진입 불가",
                "blocked_reasons": [r.value for r in rd.blocked_reasons],
                "details": list(rd.detail_messages),
            },
        )

    # 백그라운드 실행 — engine.start() 가 async 이므로 task 로 schedule.
    async def _run() -> None:
        try:
            await engine.start(mode, rd)
        except Exception as e:  # noqa: BLE001 — engine 자체가 모든 예외를 catch 하지만 방어
            logger.exception("kis-paper engine top-level failure: %s", e)

    background_tasks.add_task(asyncio.create_task, _run())

    return KisPaperStatusOut(**engine.status_dict())


@router.post("/stop", response_model=KisPaperStatusOut)
def post_kis_paper_stop() -> KisPaperStatusOut:
    """실행 중 테스트 중단 신호. 다음 tick 에서 종료."""
    engine = get_engine()
    engine.stop()
    return KisPaperStatusOut(**engine.status_dict())


@router.get("/status", response_model=KisPaperStatusOut)
def get_kis_paper_status() -> KisPaperStatusOut:
    """현재 engine 상태 + counters + failures."""
    engine = get_engine()
    return KisPaperStatusOut(**engine.status_dict())


@router.get("/report", response_model=Optional[KisPaperReportOut])
def get_kis_paper_report() -> Optional[KisPaperReportOut]:
    """가장 최근 완료 보고서. 한 번도 실행 안 했으면 None."""
    engine = get_engine()
    if engine.last_report is None:
        return None
    return KisPaperReportOut(**engine.last_report.to_dict())
