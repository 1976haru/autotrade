"""Paper Universe + Diagnostics API.

PAPER / SIMULATION 검증 흐름을 *관심종목 미등록* / *strategy engine 미연동*
등으로 막히지 않게 만드는 *advisory* endpoints:

- `GET /api/paper/universe/default`        — 현재 default universe 해결 결과
- `POST /api/paper/universe/preview`       — 임의 user_symbols 시뮬
- `GET /api/paper/diagnostics/preflight`   — "왜 주문이 0건인가" 단일 진단

본 라우터는 broker / OrderExecutor / route_order import 0건. settings 는
`get_settings()` 로 *읽기만* (안전 flag 라벨 carry 용도). DB write 0건.
응답 invariant: `is_order_signal=False` / `is_live_authorization=False` /
`is_paper_safe_only=True` carry.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Watchlist, WatchlistItem
from app.db.session import get_db
from app.universe.default_universe import (
    DEFAULT_UNIVERSE_LIMIT,
    DEFAULT_UNIVERSE_NAME,
    UniverseSource,
    get_default_universe,
)
from app.universe.paper_diagnostics import (
    AutoBotLoopInput,
    FrontendModeInput,
    PermissionGateInput,
    SafetyFlagsInput,
    evaluate_paper_diagnostics,
)


router = APIRouter(prefix="/paper", tags=["paper-universe"])


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _active_watchlist_symbols(db: Session) -> list[str]:
    """현재 *active* watchlist 의 종목 코드 (없으면 빈 리스트).

    어떤 watchlist 도 active 가 아니면 *전체* watchlist 의 첫 번째를 사용
    (운영자가 active flag 를 명시 안 한 경우의 fallback).
    """
    from sqlalchemy import select
    active = db.execute(
        select(Watchlist).where(Watchlist.is_active.is_(True))
    ).scalar_one_or_none()
    if active is None:
        # active 가 없으면 *어떤* watchlist 도 사용하지 않는다 — 운영자가
        # 명시 active 로 설정해야 함. (USER_DEFINED 라벨이 적용되려면 명시
        # active 가 필요 — 무작위 watchlist 자동 채택은 의외 동작.)
        return []
    items = db.execute(
        select(WatchlistItem)
        .where(WatchlistItem.watchlist_id == active.id)
        .order_by(WatchlistItem.created_at.asc())
    ).scalars().all()
    return [it.symbol for it in items if it.symbol]


def _auto_bot_input() -> AutoBotLoopInput:
    """현재 AutoPaperLoop 의 상태 → DTO. strategy_engine_connected 는 handler
    등록 여부로 추정."""
    try:
        from app.auto_paper.loop import get_auto_paper_loop
        loop = get_auto_paper_loop()
        snap = loop.status()
        # handler 등록 여부 — private attribute 접근이지만 본 endpoint 는
        # advisory 라벨 carry 만 하므로 안전. 미등록이면 strategy engine 이
        # 자동봇 loop 와 연결되지 않은 상태.
        handler = getattr(loop, "_paper_tick_handler", None)
        consumer = getattr(loop, "_agent_consumer_runner", None)
        connected = bool(handler is not None or consumer is not None)
        return AutoBotLoopInput(
            state=snap.state,
            is_running=(snap.state == "RUNNING"),
            cycle_count=int(snap.cycle_count),
            last_consumed=bool(snap.last_consumed),
            last_decision_count=int(snap.last_decision_count),
            last_decision_action=snap.last_decision_action,
            last_ledger_events=int(snap.last_ledger_events),
            last_decision_log_count=int(snap.last_decision_log_count),
            last_error=snap.last_error,
            strategy_engine_connected=connected,
        )
    except Exception:  # noqa: BLE001
        return AutoBotLoopInput()


def _permission_input() -> PermissionGateInput:
    """default PermissionGate 라벨 — *PAPER 가상 실행 허용* default True.

    실제 PermissionGate 모듈은 LIVE 모드의 큐 처리에만 관여하며 PAPER 가상
    실행은 OrderExecutor + RiskManager 흐름으로 처리. 본 carry 는 *advisory
    표시* 전용 — 운영자가 "PAPER 가상 실행이 차단되었는가" 를 한 곳에서 볼
    수 있게 한다.
    """
    return PermissionGateInput(
        paper_virtual_execution_allowed=True,
        live_execution_blocked=True,
        last_block_reason=None,
    )


def _safety_input() -> SafetyFlagsInput:
    s = get_settings()
    return SafetyFlagsInput(
        default_mode=str(s.default_mode.value if hasattr(s.default_mode, "value") else s.default_mode),
        enable_live_trading=bool(s.enable_live_trading),
        enable_ai_execution=bool(s.enable_ai_execution),
        enable_futures_live_trading=bool(s.enable_futures_live_trading),
        kis_is_paper=bool(s.kis_is_paper),
        market_data_provider=str(s.market_data_provider),
    )


def _common_notice() -> dict[str, Any]:
    return {
        "is_order_signal":       False,
        "is_live_authorization": False,
        "is_paper_safe_only":    True,
        "advisory_disclaimer": (
            "본 응답은 PAPER 검증 advisory — broker / route_order / "
            "OrderExecutor 호출 0건."
        ),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Universe endpoints
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/universe/default")
def get_default_universe_endpoint(
    limit: int = DEFAULT_UNIVERSE_LIMIT,
    db:    Session = Depends(get_db),
) -> dict:
    """현재 default universe 해결 결과 — active watchlist 우선, 없으면 fallback.

    *읽기 전용* — DB write 0건, broker / 외부 API 호출 0건.
    """
    user_syms = _active_watchlist_symbols(db)
    resolved = get_default_universe(user_symbols=user_syms, limit=limit)
    return {
        **resolved.to_dict(),
        "default_name":   DEFAULT_UNIVERSE_NAME,
        "default_limit":  DEFAULT_UNIVERSE_LIMIT,
        "allowed_sources": [s.value for s in UniverseSource],
        **_common_notice(),
    }


class _UniversePreviewBody(BaseModel):
    user_symbols: list[str] | None = Field(
        None, description="None / 빈 리스트 → fallback 적용",
    )
    limit:        int             = Field(DEFAULT_UNIVERSE_LIMIT, ge=0, le=500)


@router.post("/universe/preview")
def preview_universe_endpoint(body: _UniversePreviewBody) -> dict:
    """what-if 시뮬 — 임의 user_symbols 로 ResolvedUniverse 미리보기."""
    resolved = get_default_universe(
        user_symbols=body.user_symbols, limit=body.limit,
    )
    return {
        **resolved.to_dict(),
        **_common_notice(),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Diagnostics endpoint
# ──────────────────────────────────────────────────────────────────────────────


class _DiagnosticsBody(BaseModel):
    """진단 입력 — 모두 optional. frontend 가 *현재 표시 모드* 만 carry 권장."""
    frontend_mode:   Optional[str]   = Field(
        None, description="frontend 화면이 표시 중인 운용 모드 (대소문자 무시)",
    )
    limit:           int             = Field(
        DEFAULT_UNIVERSE_LIMIT, ge=1, le=500,
    )


def _build_diagnostics(
    *,
    db:            Session,
    frontend_mode: str | None,
    limit:         int,
) -> dict:
    user_syms = _active_watchlist_symbols(db)
    resolved = get_default_universe(user_symbols=user_syms, limit=limit)
    safety = _safety_input()
    auto_bot = _auto_bot_input()
    permission = _permission_input()
    frontend = FrontendModeInput(displayed_mode=frontend_mode)
    report = evaluate_paper_diagnostics(
        universe_source=resolved.source.value,
        universe_count=resolved.count,
        universe_fallback_used=resolved.fallback_used,
        universe_warning_ko=resolved.warning_ko,
        safety=safety,
        auto_bot=auto_bot,
        permission=permission,
        frontend=frontend,
    )
    return {
        **report.to_dict(),
        "universe":          resolved.to_dict(),
        **_common_notice(),
    }


@router.get("/diagnostics/preflight")
def get_diagnostics_preflight(
    frontend_mode: Optional[str] = None,
    limit:         int = DEFAULT_UNIVERSE_LIMIT,
    db:            Session = Depends(get_db),
) -> dict:
    """PAPER preflight 진단 — *"왜 주문이 0건인가"* 단일 응답.

    DB write 0건. 안전 flag 라벨 read-only carry. broker / route_order 호출
    0건. frontend 가 본 응답의 `primary_block_reason` + `blocking_messages_ko`
    + `next_actions_ko` 를 그대로 표시.
    """
    return _build_diagnostics(
        db=db, frontend_mode=frontend_mode, limit=limit,
    )


@router.post("/diagnostics/preflight")
def post_diagnostics_preflight(
    body: _DiagnosticsBody,
    db:   Session = Depends(get_db),
) -> dict:
    """diagnostics body 입력 form — frontend 가 displayed_mode 를 carry 가능."""
    return _build_diagnostics(
        db=db, frontend_mode=body.frontend_mode, limit=body.limit,
    )
