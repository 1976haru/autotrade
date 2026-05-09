from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_agents import router as agents_router
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
from app.core.config import get_settings
from app.db.session import apply_migrations
from app.execution.fill_poller import FillPoller

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    apply_migrations()

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

    try:
        yield
    finally:
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


@app.get("/")
def root() -> dict:
    return {"name": settings.app_name, "status": "ok", "docs": "/docs"}
