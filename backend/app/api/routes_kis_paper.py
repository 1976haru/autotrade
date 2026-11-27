"""KIS Paper one-click test routes (#89).

REST endpoints:
- GET  /api/kis-paper/readiness
- POST /api/kis-paper/start      (mode: quick / slow / mock)
- POST /api/kis-paper/stop
- GET  /api/kis-paper/status
- GET  /api/kis-paper/report

본 라우트는 *secret / 계좌번호 원문* 을 응답에 carry 하지 *않는다* — 존재
여부 (`*_present: bool`) 만 노출.

본 모듈은 broker.place_order 를 *직접 호출하지 않는다* — KIS paper 자동주문은
모두 sanctioned `execute_kis_paper_auto_order` → `route_order` (RiskManager →
PermissionGate → OrderExecutor) 경로로만 위임한다. quick/slow Start 는
`live_runner.build_kis_paper_tick_runner` 를 engine 에 주입해 실제 흐름을
실행하고, mock 은 외부 API 0건의 카운터 루프(engine default tick runner)를 쓴다.
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

# detached background engine task 참조 보관 (GC 방지). /start 에서 사용.
_BACKGROUND_TASKS: set = set()


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
    # KIS_PAPER_REAL_MARKET_DRYRUN — 실 KIS 시세는 흘리되 dry_run 강제 True 로
    # 주문 전송 0건(결정만 기록). quick/slow 에서만 의미. default False.
    dry_run_preview: bool = False
    # 실제 KIS 모의주문 전송 명시 동의. dry_run_preview=False AND
    # paper_order_confirm=True 일 때만 조건 충족 시 KIS 모의주문 전송.
    # default False → 미동의면 무조건 dry-run(주문 0건). 실거래와 무관.
    paper_order_confirm: bool = False


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

    # KIS paper(quick/slow) 모드는 *실제* 자동매매 흐름을 실행한다 — 시세 조회 →
    # Agent Council 판단 → execute_kis_paper_auto_order(route_order 위임). mock 은
    # 외부 API 0건의 빠른 카운터 루프(engine default tick runner) 유지.
    #
    # KIS-PAPER-REAL-TICK-RUNNER-WIRING-V1:
    # USE_REAL_TICK_RUNNER=true 면 *V2 다종목 KIS 실시간 스캔* (real_tick_runner)
    # 으로 흘려보낸다. False (default) 면 기존 단일 종목 live_runner 유지.
    tick_runner = None
    cleanup = None
    if mode in (TestMode.QUICK, TestMode.SLOW):
        from app.api.deps import get_broker, get_risk_manager
        from app.db.session import SessionLocal
        from app.kis_paper.real_tick_runner import should_use_real_tick_runner

        # request scope 밖에서 broker/risk/db 를 직접 구성 (background task).
        bg_db = SessionLocal()
        broker = get_broker()
        risk = get_risk_manager()
        force_dry = bool(body.dry_run_preview) or not bool(body.paper_order_confirm)

        if should_use_real_tick_runner(settings):
            from app.kis_paper.real_tick_runner import build_real_kis_paper_tick_runner
            tick_runner, cleanup = build_real_kis_paper_tick_runner(
                db=bg_db,
                broker=broker,
                risk=risk,
                settings=settings,
                credentials_present=bool(
                    rd.kis_key_present and rd.kis_secret_present and rd.kis_account_present
                ),
                # 실제 KIS 모의주문은 dry_run_preview=False AND paper_order_confirm=True
                # 일 때만 허용. 그 외에는 force_dry_run=True (주문 전송 0건, 결정만 기록).
                force_dry_run=force_dry,
            )
        else:
            from app.kis_paper.live_runner import build_kis_paper_tick_runner
            tick_runner, cleanup = build_kis_paper_tick_runner(
                db=bg_db,
                broker=broker,
                risk=risk,
                settings=settings,
                credentials_present=bool(
                    rd.kis_key_present and rd.kis_secret_present and rd.kis_account_present
                ),
                force_dry_run=force_dry,
            )

    # 백그라운드 실행 — engine.start() 가 async 이므로 *실행 중인* 이벤트 루프에
    # detached task 로 schedule 한다. (이전 구현은
    # background_tasks.add_task(asyncio.create_task, ...) 였는데, Starlette 가
    # 비-async callable 인 asyncio.create_task 를 threadpool 에서 호출 →
    # "no running event loop" RuntimeError 로 *루프가 시작조차 안 됐다*. 본
    # 핸들러는 async 라 이미 루프 위에 있으므로 직접 create_task 한다.)
    async def _run() -> None:
        try:
            await engine.start(mode, rd, tick_runner=tick_runner)
        except Exception as e:  # noqa: BLE001 — engine 자체가 모든 예외를 catch 하지만 방어
            logger.exception("kis-paper engine top-level failure: %s", e)
        finally:
            if cleanup is not None:
                cleanup()

    task = asyncio.create_task(_run())
    # task GC 방지 — 완료 시 set 에서 제거.
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

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
    from app.kis_paper.real_tick_runner import should_use_real_tick_runner
    from app.kis_paper.order_limits import resolve_kis_paper_order_limits
    limits = resolve_kis_paper_order_limits(settings)
    return {
        "enable_kis_paper_auto_trading": bool(settings.enable_kis_paper_auto_trading),
        "dry_run":                       bool(settings.kis_paper_auto_order_dry_run),
        "max_orders_per_day":            int(settings.kis_paper_auto_max_orders_per_day),
        "max_order_notional":            limits.max_order_krw,
        "paper_order_limits_policy":     limits.policy_version,
        "paper_total_budget_krw":        limits.total_budget_krw,
        "paper_per_symbol_krw":          limits.per_symbol_krw,
        "paper_max_positions":           limits.max_positions,
        "paper_daily_buy_count":         limits.daily_buy_count,
        "paper_daily_buy_krw":           limits.daily_buy_krw,
        "paper_buys_per_tick":           limits.buys_per_tick,
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
        # KIS-PAPER-REAL-TICK-RUNNER-WIRING-V1: 운영자 가시성.
        "use_real_tick_runner":          bool(should_use_real_tick_runner(settings)),
    }


@router.get("/auto/status")
def get_kis_paper_auto_status() -> dict:
    """KIS 모의 자동주문 설정/상태 — read-only. broker 호출 0건.

    실거래 활성화 토글을 제공하지 않는다 — ENABLE_KIS_PAPER_AUTO_TRADING 은
    .env 에서만 변경.
    """
    settings = get_settings()
    from app.kis_paper.one_click_session import (
        effective_session_settings,
        session_trading_authorized,
    )
    session_approved = session_trading_authorized(settings)
    effective_settings = effective_session_settings(settings)
    rd = evaluate_readiness(settings)
    return {
        # The configured fail-safe remains OFF/dry-run.  A current, account-scoped
        # desktop approval grants only this PAPER session its effective order mode.
        **_kis_auto_config(effective_settings),
        "configured_enable_kis_paper_auto_trading": bool(
            settings.enable_kis_paper_auto_trading),
        "configured_dry_run": bool(settings.kis_paper_auto_order_dry_run),
        "paper_session_approved": session_approved,
        # 4-01: 자격 종합 + 4종 per-credential present + 누락 키 이름 목록.
        # *값 원문 0건* — boolean / 키 이름만.
        "credentials_present":      rd.credentials_present,
        "market_data_provider":     str(settings.market_data_provider),
        "account_connection_state": (
            "CONNECTED" if rd.credentials_present and settings.kis_is_paper
            else "QA_MODE_KIS_ACCOUNT_NOT_CONNECTED"
            if str(settings.market_data_provider).lower() == "mock"
            else "KIS_CREDENTIALS_MISSING"
        ),
        "kis_app_key_present":      rd.kis_key_present,
        "kis_app_secret_present":   rd.kis_secret_present,
        "kis_account_no_present":   rd.kis_account_present,
        "kis_product_code_present": rd.kis_product_code_present,
        "product_code_present":     rd.kis_product_code_present,
        "missing_credentials":      list(rd.missing_credentials),
        "enable_ai_execution":      bool(settings.enable_ai_execution),
        "enable_futures_live_trading": bool(settings.enable_futures_live_trading),
        # 4-02: EXE 기본 .env 조합 확인 — background tick + KIS auto READY 판정.
        "enable_ai_paper_background_tick":
            bool(settings.enable_ai_paper_background_tick),
        # KIS 모의 자동주문 READY: 자격 4종 + 안전 flag + auto ON + KIS paper.
        # 자격 미설정이면 False (= BLOCKED). 실거래 권한과 무관.
        "kis_paper_auto_ready": bool(
            rd.can_run_kis_paper
            and effective_settings.enable_kis_paper_auto_trading
            and not settings.enable_live_trading
            and not settings.enable_ai_execution
        ),
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
    now = _run_once_now()
    side = body.side.strip().upper()
    # FIRST-ORDER-PREFLIGHT-01: 사용자 입력 가격을 신뢰하지 않는다. 서버가 브로커(KIS)
    #   현재가·시세 시각을 직접 조회해 (1) 출처 (2) 신선도 (3) 입력가와의 괴리를 재검증하고,
    #   주문 판단에는 *서버 조회가* 와 *실제 출처* 를 쓴다. 조회 실패/괴리 초과 → 주문 0.
    verify = await _verify_run_once_price(broker, body.symbol, int(body.price), settings, now)
    if side in ("BUY", "SELL") and not verify["ok"]:
        return {
            "reason_code": verify["reason_code"], "reason_message": verify["reason_message"],
            "submitted": False, "broker_order_sent": False, "price_check": verify,
            "broker_order_type": "KIS_PAPER", "is_live_authorization": False,
        }
    decision = KisPaperAutoDecision(
        symbol=body.symbol, side=side,
        quantity=int(body.quantity), price=int(verify.get("server_price") or body.price),
        selected_strategies=list(body.selected_strategies or []),
        confidence=float(body.confidence), quality_score=int(body.quality_score),
        entry_reason=body.entry_reason, has_exit_plan=bool(body.has_exit_plan),
        exit_plan=dict(body.exit_plan or {}),
        price_source=str(verify.get("price_source") or "unknown"),
        price_is_stale=bool(verify.get("is_stale", True)),
    )
    try:
        result = await execute_kis_paper_auto_order(
            db, decision=decision, settings=settings, broker=broker, risk=risk,
            broker_is_kis_paper=_broker_is_kis_paper(broker),
            credentials_present=bool(
                rd.kis_key_present and rd.kis_secret_present and rd.kis_account_present
            ),
            route_order_fn=route_order,
            now=now,
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
    out = result.to_dict()
    out["price_check"] = verify
    return out


def _run_once_now():
    """run-once 서버 시계 (테스트에서 monkeypatch). 프론트 시각 미사용."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


async def _verify_run_once_price(broker, symbol: str, client_price: int, settings,
                                 now) -> dict:
    """브로커 현재가 재조회 + 출처/신선도/괴리 검증 (주문 write 0 — read-only get_price)."""
    from datetime import datetime, timezone
    max_age = int(getattr(settings, "stale_price_max_age_seconds", 60) or 60)
    max_dev = float(getattr(settings, "kis_paper_run_once_max_price_deviation_pct", 1.0) or 1.0)
    base = {"client_price": int(client_price), "max_age_seconds": max_age,
            "max_deviation_pct": max_dev, "ok": False}
    try:
        q = await broker.get_price(symbol)
    except Exception as exc:  # noqa: BLE001
        return {**base, "reason_code": "RUN_ONCE_QUOTE_UNAVAILABLE",
                "reason_message": f"브로커 현재가 조회 실패({type(exc).__name__}) — 주문하지 않습니다."}
    price = int(getattr(q, "price", 0) or 0)
    source = str(getattr(q, "source", "") or "unknown").lower()
    try:
        ts = datetime.fromisoformat(str(q.timestamp))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (now - ts).total_seconds()
    except Exception:  # noqa: BLE001
        age = None
    stale = age is None or age > max_age or age < -5
    dev = (abs(client_price - price) / price * 100.0) if price > 0 else None
    info = {**base, "server_price": price, "price_source": source,
            "quote_timestamp": str(getattr(q, "timestamp", "")),
            "quote_age_seconds": None if age is None else round(age, 1),
            "deviation_pct": None if dev is None else round(dev, 3), "is_stale": stale}
    if price <= 0:
        return {**info, "reason_code": "RUN_ONCE_QUOTE_INVALID",
                "reason_message": "브로커 현재가가 0 이하 — 주문하지 않습니다."}
    if stale:
        return {**info, "reason_code": "RUN_ONCE_QUOTE_STALE",
                "reason_message": f"브로커 시세가 오래됨(> {max_age}초) — 주문하지 않습니다."}
    if dev is not None and dev > max_dev:
        return {**info, "reason_code": "RUN_ONCE_PRICE_MISMATCH",
                "reason_message": (f"입력가 {client_price} 가 서버 조회가 {price} 와 "
                                   f"{dev:.2f}% 차이(> {max_dev}%) — 주문하지 않습니다.")}
    # 출처 검증은 게이트(auto_permission: KIS_REALTIME_PRICE_REQUIRED)가 실제 출처로 수행 —
    #   dry-run 진단은 mock 출처도 허용하되 실제 전송은 kis 출처에서만 통과한다.
    return {**info, "ok": True, "reason_code": "RUN_ONCE_PRICE_VERIFIED",
            "reason_message": "서버가 브로커 현재가·시세 시각을 재검증했습니다."}


# ── KIS-PAPER-INTRADAY-FLAT-BY-CLOSE-V1: 장마감 당일 청산 상태 / 운영자 확인 ──────


@router.get("/eod/status")
def get_kis_paper_eod_status(db: Session = Depends(get_db)) -> dict:
    """장중 단타 세션(14:30 매수마감·15:15 강제청산·15:30 마감) + EOD 청산 상태.

    read-only — broker 호출 0건 (driver 의 마지막 대조 결과만). 서버 시계 기준
    판단이므로 프론트 시각과 무관하다. 실거래 권한 아님.
    """
    from datetime import datetime, timezone

    from app.auto_paper.background_driver import get_background_tick_driver
    from app.kis_paper.eod_liquidation import eod_status
    out = eod_status(db)
    drv = get_background_tick_driver()
    out["background_driver_running"] = bool(drv.is_running)
    out["background_driver_enabled"] = bool(drv.status().get("enabled"))
    out["service_kind"] = "CONTINUOUS_BACKGROUND_DRIVER"
    # P0-SAFETY-FIX-V1: EOD 를 실제로 관리하는 실행기 — driver(종일 운용) / guardian(청산 전용) / 없음.
    from app.auto_paper.background_driver import get_eod_guardian
    guardian = get_eod_guardian()
    out["eod_guardian_running"] = bool(guardian.is_running)
    out["eod_guardian_last_started_at"] = guardian.last_started_at
    out["eod_guardian_last_finished_at"] = guardian.last_finished_at
    last_activity = guardian.last_finished_at or guardian.last_started_at
    interval = int(get_settings().ai_paper_tick_interval_seconds or 30)
    out["eod_guardian_stalled"] = bool(
        guardian.is_running and last_activity is not None
        and (datetime.now(timezone.utc) - last_activity).total_seconds() > max(90, 3 * interval)
    )
    out["eod_manager"] = ("BACKGROUND_DRIVER" if drv.is_running
                          else "EOD_GUARDIAN" if guardian.is_running else "NONE")
    out["notice"] = (
        "정상 종일 운용은 백엔드 상시 서비스(background driver)가 수행합니다. "
        "설정 화면의 quick(3틱)/slow(20틱) 시험은 단발 점검이며 종일 운용이 아닙니다. "
        "PC 절전·앱 종료·네트워크 단절 시 15:15 청산이 실행되지 않을 수 있습니다."
    )
    return out


@router.get("/eod/shutdown-check")
def get_kis_paper_eod_shutdown_check(db: Session = Depends(get_db)) -> dict:
    """데스크톱 앱 종료 전 확인 — 보유/미체결이 있으면 종료 시 자동 청산 불가 경고.
    read-only (DB/마지막 대조 요약), broker 호출 0건."""
    from app.kis_paper.eod_liquidation import shutdown_check
    return shutdown_check(db)


class _EodAckBody(BaseModel):
    trading_date: str
    decided_by:   str
    note:         Optional[str] = None
    account:      Optional[str] = None


@router.post("/eod/acknowledge")
def post_kis_paper_eod_acknowledge(body: _EodAckBody, db: Session = Depends(get_db)) -> dict:
    """Record the operator's acknowledgment of an incomplete EOD close.

    사고 기록(state)은 그대로 보존. 주문 0건. 운영자는 KIS 모의계좌에서 잔여 보유를
    직접 확인/정리한 뒤 호출해야 한다.
    """
    from app.kis_paper.eod_liquidation import acknowledge_incident
    if not (body.decided_by or "").strip():
        raise HTTPException(status_code=400, detail="decided_by is required")
    account = (body.account or "").strip()
    if account and account not in ("LEGACY_ACCOUNT", "CURRENT"):
        raise HTTPException(status_code=400, detail="account must be LEGACY_ACCOUNT, CURRENT or omitted")
    try:
        row = acknowledge_incident(db, trading_date=body.trading_date,
                                   decided_by=body.decided_by.strip(), note=body.note,
                                   account_key="" if account == "LEGACY_ACCOUNT" else None)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "trading_date": row.trading_date, "state": row.state,
        "incident_acknowledged": True, "acknowledged_by": row.acknowledged_by,
        "account": row.account_key or "LEGACY_ACCOUNT",
        "is_live_authorization": False, "broker_order_sent": False,
    }
