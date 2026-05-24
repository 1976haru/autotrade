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
    # 4-01: KIS 모의 자격 종합 검증 (값 원문 0건 — boolean / 키 이름만).
    kis_product_code_present: bool = False
    product_code_present:     bool = False
    credentials_present:      bool = False
    missing_credentials:      list[str] = []
    default_mode:             str | None = None
    paper_broker_kind:        str | None = None
    enable_kis_paper_auto_trading: bool | None = None
    dry_run:                  bool | None = None
    fill_polling:             bool | None = None
    enable_live_trading:      bool | None = None
    enable_ai_execution:      bool | None = None
    is_order_intent:      bool
    is_order_signal:      bool
    is_live_authorization: bool = False
    contains_secret:      bool = False


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


# ============================================================================
# KIS Paper Auto Trading — AI/전략 결정 → KIS 모의투자 API 주문 (실거래 아님)
# ============================================================================


from fastapi import Depends   # noqa: E402
from sqlalchemy.orm import Session   # noqa: E402

from app.api.deps import get_broker, get_risk_manager   # noqa: E402
from app.db.session import get_db   # noqa: E402
from app.execution.order_router import route_order   # noqa: E402
from app.kis_paper.auto_executor import (   # noqa: E402
    KisPaperAutoDecision,
    execute_kis_paper_auto_order,
)


def _broker_is_kis_paper(broker) -> bool:
    """broker 가 KIS *모의투자* 어댑터인지 — live KIS / Mock 은 False."""
    return (
        type(broker).__name__ == "KisBrokerAdapter"
        and bool(getattr(broker, "is_paper", False))
    )


def _resolve_paper_broker_kind(settings) -> str:
    """paper_broker_kind 효력값 — 미설정 시 default_mode + kis_is_paper 로 추론."""
    explicit = getattr(settings, "paper_broker_kind", "") or ""
    if explicit:
        return str(explicit)
    from app.execution.paper_trader import _default_paper_broker_kind
    return _default_paper_broker_kind(settings).value


def _kis_auto_config(settings) -> dict:
    return {
        "enable_kis_paper_auto_trading": bool(settings.enable_kis_paper_auto_trading),
        "dry_run":                       bool(settings.kis_paper_auto_order_dry_run),
        "max_orders_per_day":            int(settings.kis_paper_auto_max_orders_per_day),
        "max_order_notional":            int(settings.kis_paper_auto_max_order_notional),
        "window_start":                  settings.kis_paper_auto_order_window_start,
        "window_end":                    settings.kis_paper_auto_order_window_end,
        "min_confidence":                float(settings.kis_paper_auto_min_confidence),
        "min_quality_score":             int(settings.kis_paper_auto_min_quality_score),
        "kis_is_paper":                  bool(settings.kis_is_paper),
        "enable_live_trading":           bool(settings.enable_live_trading),
        "fill_polling":                  bool(settings.kis_paper_fill_polling),
        # 0-04: EXE env 표시용 — 운용모드 + paper broker 종류 carry.
        "default_mode":                  getattr(
            settings.default_mode, "value", settings.default_mode),
        "paper_broker_kind":             _resolve_paper_broker_kind(settings),
    }


@router.get("/auto/status")
def get_kis_paper_auto_status() -> dict:
    """KIS 모의 자동주문 설정/상태 — read-only. broker 호출 0건.

    실거래 활성화 토글을 제공하지 않는다 — ENABLE_KIS_PAPER_AUTO_TRADING 은
    .env 에서만 변경.
    """
    settings = get_settings()
    rd = evaluate_readiness(settings)
    return {
        **_kis_auto_config(settings),
        # 4-01: 자격 종합 + 4종 per-credential present + 누락 키 이름 목록.
        # *값 원문 0건* — boolean / 키 이름만.
        "credentials_present":      rd.credentials_present,
        "kis_app_key_present":      rd.kis_key_present,
        "kis_app_secret_present":   rd.kis_secret_present,
        "kis_account_no_present":   rd.kis_account_present,
        "kis_product_code_present": rd.kis_product_code_present,
        "product_code_present":     rd.kis_product_code_present,
        "missing_credentials":      list(rd.missing_credentials),
        "enable_ai_execution":      bool(settings.enable_ai_execution),
        "is_live_authorization": False,
        "contains_secret":       False,
        "broker_order_type":     "KIS_PAPER",
        "notice": (
            "KIS Paper Auto Trading은 한투 모의투자 API 전용이며 실거래 권한이 "
            "아닙니다. 실제 돈이 나가지 않습니다. ENABLE_KIS_PAPER_AUTO_TRADING은 "
            ".env에서만 켤 수 있습니다."
        ),
    }


class _KisAutoRunOnceBody(BaseModel):
    """KIS 모의 자동주문 1건 실행 입력 — AI/전략 결정 carry."""

    symbol:              str
    side:                str                       # BUY / SELL / HOLD
    quantity:            int
    price:               int
    selected_strategies: Optional[list[str]]       = None
    confidence:          float                      = 0.0
    quality_score:       int                        = 0
    entry_reason:        str                        = ""
    has_exit_plan:       bool                       = False
    exit_plan:           Optional[dict]             = None


@router.post("/auto/run-once")
async def post_kis_paper_auto_run_once(
    body: _KisAutoRunOnceBody,
    db: Session = Depends(get_db),
    broker = Depends(get_broker),
    risk = Depends(get_risk_manager),
) -> dict:
    """AI/전략 결정을 받아 KIS 모의 자동주문 1건 실행 (게이트 통과 시).

    기본 flag OFF → KIS_PAPER_AUTO_DISABLED. dry_run=true → KIS_PAPER_DRY_RUN_OK
    (KIS API 호출 0건). dry_run=false + 게이트 통과 → route_order 로 KIS 모의
    주문 전송. broker.place_order 직접 호출 0건, 실거래 0건.
    """
    settings = get_settings()
    rd = evaluate_readiness(settings)
    decision = KisPaperAutoDecision(
        symbol=body.symbol, side=body.side.strip().upper(),
        quantity=int(body.quantity), price=int(body.price),
        selected_strategies=list(body.selected_strategies or []),
        confidence=float(body.confidence), quality_score=int(body.quality_score),
        entry_reason=body.entry_reason, has_exit_plan=bool(body.has_exit_plan),
        exit_plan=dict(body.exit_plan or {}),
    )
    try:
        result = await execute_kis_paper_auto_order(
            db, decision=decision, settings=settings, broker=broker, risk=risk,
            broker_is_kis_paper=_broker_is_kis_paper(broker),
            credentials_present=bool(
                rd.kis_key_present and rd.kis_secret_present and rd.kis_account_present
            ),
            route_order_fn=route_order,
        )
        db.commit()
    except Exception as exc:   # noqa: BLE001 — 흐름이 500 으로 죽지 않게.
        db.rollback()
        return {
            "reason_code":     "KIS_PAPER_ERROR",
            "submitted":       False,
            "reason_message":  f"{type(exc).__name__}: {exc}",
            "broker_order_type":     "KIS_PAPER",
            "broker_order_sent":     False,
            "is_live_authorization": False,
        }
    return result.to_dict()
