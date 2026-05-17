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
    _startup_logger.info(
        "[startup] lifespan begin — alembic migration starting "
        "(첫 실행 시 DB 초기화로 1~2분 걸릴 수 있습니다)"
    )
    try:
        apply_migrations()
    except Exception:
        # migration 실패 시 stack trace 전체를 로그에 남긴다 — launcher 의
        # FileHandler 와 stderr 양쪽으로 propagate. Secret 노출 0건 (alembic
        # 메시지에는 connection string redact 가 SQLAlchemy 측에서 처리).
        _startup_logger.exception("[startup] alembic migration FAILED")
        raise
    _startup_logger.info("[startup] alembic migration complete")

    poller: FillPoller | None = None
    cfg = get_settings()
    if cfg.enable_fill_polling:
        from app.api.deps import get_broker
        from app.db.session import SessionLocal
        poller = FillPoller(
            broker_factory=get_broker,
            session_factory=SessionLocal,
            interval=cfg.fill_polling_interval_seconds,
        )
        poller.start()

    _startup_logger.info(
        "[startup] backend ready — uvicorn accepting requests on /health, "
        "/api/status, /api/kis-paper/readiness"
    )

    try:
        yield
    finally:
        _startup_logger.info("[shutdown] lifespan exit")
        if poller is not None:
            await poller.stop()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
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
@app.get("/health")
def health() -> dict:
    return {"ok": True, "status": "ok", "app": settings.app_name}
