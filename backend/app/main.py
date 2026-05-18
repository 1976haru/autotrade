import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_agents import router as agents_router
from app.api.routes_notifications import router as notifications_router
from app.api.routes_auto_trader import router as auto_trader_router
from app.api.routes_auto_paper import router as auto_paper_router
from app.api.routes_ai import router as ai_router
from app.api.routes_ai_assist import router as ai_assist_router
from app.api.routes_ai_execution import router as ai_execution_router
from app.api.routes_execution_recommender import router as execution_recommender_router
from app.api.routes_paper_start_explanation import router as paper_start_explanation_router
from app.api.routes_paper_decision_bridge import router as paper_decision_bridge_router
from app.api.routes_paper_decision_log import router as paper_decision_log_router
from app.api.routes_agent_memory import router as agent_memory_router
from app.api.routes_approvals import router as approvals_router
from app.api.routes_audit import router as audit_router
from app.api.routes_backtest import router as backtest_router
from app.api.routes_broker import router as broker_router
from app.api.routes_explainability import router as explainability_router
from app.api.routes_live_engine import router as live_engine_router
from app.api.routes_market import router as market_router
from app.api.routes_analytics import router as analytics_router
from app.api.routes_monitoring import router as monitoring_router
from app.api.routes_paper import router as paper_router
from app.api.routes_reconciliation import router as reconciliation_router
from app.api.routes_risk import router as risk_router
from app.api.routes_futures import router as futures_router
from app.api.routes_shadow import router as shadow_router
from app.api.routes_status import router as status_router
from app.api.routes_governance import router as governance_router
from app.api.routes_themes import router as themes_router
from app.api.routes_virtual import router as virtual_router
from app.api.routes_watchlists import router as watchlists_router
from app.api.routes_kis_paper import router as kis_paper_router  # #89
from app.core.config import get_settings
from app.db.migration_runner import (
    MigrationState,
    db_is_ready,
    get_migration_status,
    run_migration_blocking,
    start_migration_in_background,
)
from app.db.session import apply_migrations
from app.execution.fill_poller import FillPoller
from app.monitoring.middleware import ApiMetricsMiddleware

settings = get_settings()


# fix/desktop-backend-startup-readiness: 데스크톱 launcher 가 backend startup
# 진행 단계를 desktop-backend.log 에서 명확히 확인할 수 있도록 명시 로그 marker
# 를 alembic migration 시작 / 종료 / FastAPI 준비완료 시점에 emit 한다. uvicorn
# 의 자체 로그 ("Started server process", "Waiting for application startup",
# "Application startup complete") 는 launcher 가 `log_config=None` 으로 root
# logger 에 propagate 시켜 동일 파일에 기록되므로 사용자 / 운영자는 첫 실행시
# migration 지연 여부 / 실패 원인을 한눈에 본다.
_startup_logger = logging.getLogger("autotrade.startup")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """FastAPI lifespan.

    Two startup modes (config flag `migration_nonblocking`):

    *Blocking (default — preserves existing tests / CI / scripts)*
      lifespan 진입 시 alembic migration 을 *동기적으로* 실행하고 yield. 모든
      기존 TestClient 흐름이 변경 없이 동작 (route handler 가 DB 를 만들어진
      상태로 신뢰).

    *Non-blocking (opt-in — 데스크톱 EXE 운영자 흐름)*
      `MIGRATION_NONBLOCKING=true` 일 때 migration 을 *background daemon thread*
      에서 실행하고 즉시 yield. `/health` 와 `/api/status` 가 첫 응답부터 200
      → frontend launcher 가 "초기 DB 준비 중" UI 를 그릴 수 있음. fill_poller
      는 별도 asyncio task 가 migration 완료 후 start.
    """
    cfg = get_settings()
    _startup_logger.info(
        "[startup] lifespan begin — migration_nonblocking=%s",
        cfg.migration_nonblocking,
    )

    poller: FillPoller | None = None
    poller_starter_task: asyncio.Task | None = None

    def _build_and_start_poller() -> FillPoller:
        from app.api.deps import get_broker
        from app.db.session import SessionLocal
        p = FillPoller(
            broker_factory=get_broker,
            session_factory=SessionLocal,
            interval=cfg.fill_polling_interval_seconds,
        )
        p.start()
        return p

    if cfg.migration_nonblocking:
        # ──────────────────────────────────────────────────────────────
        # 데스크톱 EXE 흐름: migration 을 background thread 로 → 즉시 yield.
        # /health 와 /api/status 는 migration 진행 중에도 200 응답.
        # ──────────────────────────────────────────────────────────────
        start_migration_in_background(apply_migrations)
        if cfg.enable_fill_polling:
            async def _start_poller_when_db_ready() -> None:
                nonlocal poller
                # migration 종료 (COMPLETED or FAILED) 까지 polling.
                while True:
                    mig = get_migration_status()
                    if mig.state == MigrationState.COMPLETED:
                        poller = _build_and_start_poller()
                        _startup_logger.info(
                            "[startup] fill_poller started after migration complete"
                        )
                        return
                    if mig.state in (MigrationState.FAILED, MigrationState.SKIPPED):
                        _startup_logger.warning(
                            "[startup] fill_poller NOT started (migration state=%s)",
                            mig.state.value,
                        )
                        return
                    await asyncio.sleep(1.0)
            poller_starter_task = asyncio.create_task(_start_poller_when_db_ready())

        _startup_logger.info(
            "[startup] backend ready (non-blocking) — /health and /api/status "
            "respond immediately; DB migration running in background"
        )
    else:
        # ──────────────────────────────────────────────────────────────
        # 기존 동기 흐름: migration 완료까지 blocking, 그 후 yield.
        # 모든 기존 TestClient / CI / script 가 변경 없이 동작.
        # ──────────────────────────────────────────────────────────────
        _startup_logger.info(
            "[startup] alembic migration starting "
            "(첫 실행 시 DB 초기화로 1~2분 걸릴 수 있습니다)"
        )
        run_migration_blocking(apply_migrations)
        mig = get_migration_status()
        if mig.state == MigrationState.FAILED:
            # run_migration_blocking 은 traceback 을 log 에 남기고 *return*.
            # 기존 동기 흐름은 lifespan startup 실패를 raise 로 표현해야 하므로
            # 여기서 RuntimeError 로 escalate — 단, 메시지에 secret 0건.
            raise RuntimeError(
                f"alembic migration failed: {mig.error_type or 'Unknown'}"
            )
        _startup_logger.info("[startup] alembic migration complete")
        if cfg.enable_fill_polling:
            poller = _build_and_start_poller()
        _startup_logger.info(
            "[startup] backend ready — uvicorn accepting requests on /health, "
            "/api/status, /api/kis-paper/readiness"
        )

    try:
        yield
    finally:
        _startup_logger.info("[shutdown] lifespan exit")
        if poller_starter_task is not None and not poller_starter_task.done():
            poller_starter_task.cancel()
            try:
                await poller_starter_task
            except (asyncio.CancelledError, Exception):
                pass
        if poller is not None:
            await poller.stop()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

# fix/step1-backend-autoconnect-final: Tauri 데스크톱 webview 는 *cross-origin*
# 으로 backend 에 fetch 한다 (webview origin = `tauri://localhost` /
# `https://tauri.localhost` / `http://tauri.localhost`). 기존 cors_origins 는
# dev 서버 (localhost:5173, 127.0.0.1:5173) 만 포함해 EXE 모드에서 *모든
# fetch 가 CORS 차단* 으로 실패 → frontend 가 backend 를 "offline" 으로 오인.
#
# 본 정규식은 *backend 가 127.0.0.1 loopback 에만 listen 하는 EXE 운영* 가정
# 하에 안전: Tauri webview 의 모든 변형 origin 을 허용 (cross-PC 접근은
# 127.0.0.1 listen 으로 차단). cors_origin_list (env 명시) 는 그대로 유지 —
# dev 서버 / staging 추가 origin 도 동작.
_TAURI_ORIGIN_REGEX = (
    r"^(tauri://localhost"
    r"|https?://tauri\.localhost"
    r"|tauri://[^/]+"
    r"|https?://localhost(:\d+)?"
    r"|https?://127\.0\.0\.1(:\d+)?)$"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=_TAURI_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 체크리스트 #70: 모든 요청을 ApiMetricsRegistry에 ring-buffer 기록.
# 핸들러 응답을 변형하지 않으며 실패는 fail-open (모니터링이 운영을 막지 않음).
app.add_middleware(ApiMetricsMiddleware)

app.include_router(status_router, prefix="/api")
app.include_router(risk_router, prefix="/api")
app.include_router(broker_router, prefix="/api")
app.include_router(ai_router, prefix="/api")
app.include_router(backtest_router, prefix="/api")
app.include_router(market_router, prefix="/api")
app.include_router(approvals_router, prefix="/api")
app.include_router(audit_router, prefix="/api")
app.include_router(live_engine_router, prefix="/api")
app.include_router(virtual_router, prefix="/api")
app.include_router(futures_router, prefix="/api")
app.include_router(reconciliation_router, prefix="/api")
app.include_router(agents_router, prefix="/api")
app.include_router(watchlists_router, prefix="/api")
app.include_router(themes_router, prefix="/api")
app.include_router(governance_router, prefix="/api")
app.include_router(explainability_router, prefix="/api")
app.include_router(paper_router, prefix="/api")
app.include_router(shadow_router, prefix="/api")
app.include_router(ai_assist_router, prefix="/api")
app.include_router(ai_execution_router, prefix="/api")
app.include_router(execution_recommender_router, prefix="/api")
app.include_router(paper_start_explanation_router, prefix="/api")
app.include_router(paper_decision_bridge_router, prefix="/api")
app.include_router(paper_decision_log_router, prefix="/api")
app.include_router(agent_memory_router, prefix="/api")
app.include_router(auto_trader_router, prefix="/api")
app.include_router(auto_paper_router, prefix="/api")
app.include_router(notifications_router, prefix="/api")
app.include_router(monitoring_router, prefix="/api")
app.include_router(analytics_router, prefix="/api")
app.include_router(kis_paper_router, prefix="/api")  # #89


@app.get("/")
def root() -> dict:
    return {"name": settings.app_name, "status": "ok", "docs": "/docs"}


# fix/desktop-sidecar-runtime-diagnostics: 데스크톱 launcher 가
# /api/status 실패 시 fallback 으로 호출하는 *최소* liveness probe.
# 어떤 DB / monitoring 서비스 의존성 없이 즉시 200 — sidecar 가 살아있다는
# 사실만 확인. Secret / API key / 계좌번호 포함 0건.
#
# fix/desktop-nonblocking-migration-health: `db_ready` / `migration_status`
# 를 추가 — DB 호출 없이 module-level singleton 만 read 하므로 응답 속도는
# 영향 없음. migration 진행 중에도 본 endpoint 는 200 응답.
@app.get("/health")
def health() -> dict:
    mig = get_migration_status()
    return {
        "ok":               True,
        "status":           "ok",
        "app":              settings.app_name,
        "db_ready":         db_is_ready(),
        "migration_status": mig.state.value,
    }
