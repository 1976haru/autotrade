"""Operator-facing system diagnostics + event log endpoints.

- `GET  /api/system/diagnostics`        — single 진단 리포트
- `POST /api/system/diagnostics`        — frontend_mode 등 body 주입형
- `GET  /api/system/events/recent`      — 최근 in-memory 이벤트 필터 조회
- `POST /api/system/events/test`        — *advisory* 테스트 이벤트 emit
- `GET  /api/auto-paper/diagnostics/summary` — Paper 운영자 compact 요약
- `GET  /api/system/copy-report`        — UI 가 "진단 리포트 복사" 로 쓸 safe JSON

본 라우터는 broker / OrderExecutor / route_order import 0건. settings 는
*읽기만*. DB write 0건. 응답에 Secret / API key / 계좌번호 0건 (event_log 가
fail-closed 로 차단).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.system.event_log import (
    EventCategory,
    EventLevel,
    SecretLeakBlockedError,
    get_runtime_event_log,
    log_event,
)
from app.system.operation_diagnostics import (
    AutoBotInput,
    BackendStatusInput,
    DesktopEnvInput,
    FrontendModeInput,
    MarketDataInput,
    PaperCashInput,
    PermissionInput,
    SafetyFlagsInput,
    TodaySummaryInput,
    UniverseInput,
    ZeroOrderReason,
    analyze_zero_order_reason,
    evaluate_operation_diagnostics,
)


router = APIRouter(tags=["system-diagnostics"])


# ──────────────────────────────────────────────────────────────────────────────
# Helpers — settings + current state → input DTO
# ──────────────────────────────────────────────────────────────────────────────


def _safety_input() -> SafetyFlagsInput:
    s = get_settings()
    return SafetyFlagsInput(
        default_mode=str(
            s.default_mode.value if hasattr(s.default_mode, "value")
            else s.default_mode
        ),
        enable_live_trading=bool(s.enable_live_trading),
        enable_ai_execution=bool(s.enable_ai_execution),
        enable_futures_live_trading=bool(s.enable_futures_live_trading),
        kis_is_paper=bool(s.kis_is_paper),
        market_data_provider=str(s.market_data_provider),
    )


def _backend_input() -> BackendStatusInput:
    try:
        from app.db.migration_runner import db_is_ready, get_migration_status
        mig = get_migration_status()
        return BackendStatusInput(
            backend_ready=True,
            db_ready=bool(db_is_ready()),
            migration_state=str(mig.state.value),
        )
    except Exception:  # noqa: BLE001
        return BackendStatusInput(
            backend_ready=True, db_ready=False, migration_state="UNKNOWN",
        )


def _auto_bot_input() -> AutoBotInput:
    try:
        from app.auto_paper.loop import get_auto_paper_loop
        loop = get_auto_paper_loop()
        snap = loop.status()
        handler = getattr(loop, "_paper_tick_handler", None)
        consumer = getattr(loop, "_agent_consumer_runner", None)
        return AutoBotInput(
            state=snap.state,
            is_running=(snap.state == "RUNNING"),
            cycle_count=int(snap.cycle_count),
            last_consumed=bool(snap.last_consumed),
            last_decision_count=int(snap.last_decision_count),
            last_ledger_events=int(snap.last_ledger_events),
            last_decision_log_count=int(snap.last_decision_log_count),
            last_error=snap.last_error,
            strategy_engine_connected=bool(
                handler is not None or consumer is not None
            ),
        )
    except Exception:  # noqa: BLE001
        return AutoBotInput()


def _paper_cash_input() -> PaperCashInput:
    try:
        from app.auto_paper.capital_state import get_capital_state
        snap = get_capital_state().snapshot()
        return PaperCashInput(
            available_cash_krw=int(snap.available_cash_krw),
            insufficient_today=False,
        )
    except Exception:  # noqa: BLE001
        return PaperCashInput()


def _common_notice() -> dict[str, Any]:
    return {
        "safe_for_ui":           True,
        "contains_secret":       False,
        "is_order_signal":       False,
        "is_live_authorization": False,
        "advisory_disclaimer": (
            "본 응답은 PAPER 검증 advisory — broker / route_order / "
            "OrderExecutor 호출 0건. 민감정보는 진단 리포트에 포함되지 않습니다."
        ),
    }


# ──────────────────────────────────────────────────────────────────────────────
# DTO bodies
# ──────────────────────────────────────────────────────────────────────────────


class _DiagnosticsBody(BaseModel):
    """진단 입력 — 모두 optional. frontend 가 *현재 표시 모드* 만 carry 권장.

    `desktop` block 으로 EXE 환경 점검 라벨을 명시 전달 가능. 미주입 시
    `is_desktop=False` 로 처리.
    """
    frontend_mode:           Optional[str] = None
    is_desktop:              bool          = False
    backend_process_alive:   bool          = True
    backend_port_reachable:  bool          = True
    env_file_present:        bool          = True
    logs_dir_writable:       bool          = True
    app_data_dir_writable:   bool          = True
    launcher_ok:             bool          = True
    # 오늘 운영 카운터 — caller (frontend / 운영자) 가 ledger 에서 집계 후 전달.
    today_candidate_count:           int = 0
    today_signal_count:              int = 0
    today_approved_candidate_count:  int = 0
    today_rejected_candidate_count:  int = 0
    today_virtual_order_count:       int = 0
    today_blocked_order_count:       int = 0
    today_error_count:               int = 0
    today_warning_count:             int = 0
    # universe 정보 — 미주입 시 active watchlist / fallback 로 backend 가 추정.
    universe_source:                 Optional[str]  = None
    universe_count:                  Optional[int]  = None
    universe_fallback_used:          Optional[bool] = None
    universe_warning_ko:             Optional[str]  = None


def _build_diagnostics_dict(body: _DiagnosticsBody | None) -> dict[str, Any]:
    body = body or _DiagnosticsBody()
    safety = _safety_input()
    backend = _backend_input()
    auto_bot = _auto_bot_input()
    cash = _paper_cash_input()
    # universe — body override 우선, 없으면 active watchlist 기반 default.
    if body.universe_count is not None:
        uni = UniverseInput(
            source=body.universe_source or "UNKNOWN",
            count=int(body.universe_count),
            fallback_used=bool(body.universe_fallback_used or False),
            warning_ko=body.universe_warning_ko or "",
        )
    else:
        try:
            from app.universe.default_universe import get_default_universe
            r = get_default_universe(user_symbols=None)
            uni = UniverseInput(
                source=r.source.value, count=r.count,
                fallback_used=r.fallback_used, warning_ko=r.warning_ko,
            )
        except Exception:  # noqa: BLE001
            uni = UniverseInput()
    market = MarketDataInput(
        provider=safety.market_data_provider,
        last_fetch_ok=True, last_fetch_at=None, stale_symbols=0,
    )
    permission = PermissionInput(
        paper_virtual_execution_allowed=True,
        live_execution_blocked=True,
        last_block_reason=None,
        risk_manager_last_block_reason=None,
    )
    today = TodaySummaryInput(
        candidate_count=int(body.today_candidate_count),
        signal_count=int(body.today_signal_count),
        approved_candidate_count=int(body.today_approved_candidate_count),
        rejected_candidate_count=int(body.today_rejected_candidate_count),
        virtual_order_count=int(body.today_virtual_order_count),
        blocked_order_count=int(body.today_blocked_order_count),
        error_count=int(body.today_error_count),
        warning_count=int(body.today_warning_count),
    )
    desktop = DesktopEnvInput(
        is_desktop=bool(body.is_desktop),
        backend_process_alive=bool(body.backend_process_alive),
        backend_port_reachable=bool(body.backend_port_reachable),
        env_file_present=bool(body.env_file_present),
        logs_dir_writable=bool(body.logs_dir_writable),
        app_data_dir_writable=bool(body.app_data_dir_writable),
        launcher_ok=bool(body.launcher_ok),
    )
    frontend = FrontendModeInput(displayed_mode=body.frontend_mode)
    ev_summary = get_runtime_event_log().summary()
    report = evaluate_operation_diagnostics(
        backend=backend, safety=safety, universe=uni, market=market,
        auto_bot=auto_bot, permission=permission, cash=cash,
        today=today, desktop=desktop, frontend=frontend,
        event_summary=ev_summary,
    )
    return {
        **report.to_dict(),
        **_common_notice(),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Diagnostics endpoints
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/system/diagnostics")
def get_system_diagnostics(
    frontend_mode: Optional[str] = None,
    is_desktop:    bool = False,
) -> dict:
    """단일 진단 리포트 — read-only. DB write 0건. broker 호출 0건."""
    body = _DiagnosticsBody(
        frontend_mode=frontend_mode, is_desktop=is_desktop,
    )
    return _build_diagnostics_dict(body)


@router.post("/system/diagnostics")
def post_system_diagnostics(body: _DiagnosticsBody) -> dict:
    """body 입력형 진단 — 오늘 카운터 / desktop 라벨 / universe override 가능."""
    return _build_diagnostics_dict(body)


@router.get("/auto-paper/diagnostics/summary")
def get_paper_diagnostics_summary(
    frontend_mode: Optional[str] = None,
) -> dict:
    """Paper 운영자 compact 요약 — 전체 진단의 *핵심 필드* 만 carry.

    UI 카드가 한 줄로 요약하기 좋도록 작은 payload 만 emit.
    """
    full = _build_diagnostics_dict(
        _DiagnosticsBody(frontend_mode=frontend_mode),
    )
    return {
        "overall_status":             full["overall_status"],
        "conclusion_ko":              full["conclusion_ko"],
        "default_mode":               full["default_mode"],
        "enable_live_trading":        full["enable_live_trading"],
        "enable_ai_execution":        full["enable_ai_execution"],
        "kis_is_paper":               full["kis_is_paper"],
        "universe_source":            full["universe_source"],
        "universe_count":             full["universe_count"],
        "universe_fallback_used":     full["universe_fallback_used"],
        "auto_bot_state":             full["auto_bot_state"],
        "strategy_engine_connected":  full["strategy_engine_connected"],
        "paper_virtual_execution_allowed": full["paper_virtual_execution_allowed"],
        "has_orders_today":           full["has_orders_today"],
        "zero_order_primary_reason":  full["zero_order_primary_reason"],
        "zero_order_primary_message": full["zero_order_primary_message"],
        "next_actions_ko":            full["next_actions_ko"],
        **_common_notice(),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Event log endpoints
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/system/events/recent")
def get_system_events_recent(
    limit:     int = 100,
    level:     Optional[str] = None,
    min_level: Optional[str] = None,
    category:  Optional[str] = None,
    code:      Optional[str] = None,
    since:     Optional[str] = None,
) -> dict:
    """최근 in-memory 이벤트 read-only. broker / DB write 0건.

    filter:
    - `level=WARN` 정확 매치
    - `min_level=WARN` 이상 (WARN/ERROR/CRITICAL)
    - `category=STRATEGY` 정확 매치
    - `code=AUTO_BOT_STARTED` 정확 매치
    - `since=2026-05-21T00:00:00+00:00` 시각 이후
    """
    elog = get_runtime_event_log()
    try:
        events = elog.recent(
            limit=int(limit),
            level=level if level else None,
            min_level=min_level if min_level else None,
            category=category if category else None,
            code=code,
            since=since,
        )
    except ValueError as exc:
        # level / category 가 enum 으로 변환 안 되면 빈 결과 + 안내.
        return {
            "events": [],
            "count":  0,
            "summary": elog.summary(),
            "error_filter": str(exc),
            **_common_notice(),
        }
    return {
        "events":  [e.to_dict() for e in events],
        "count":   len(events),
        "summary": elog.summary(),
        "capacity": elog.capacity,
        **_common_notice(),
    }


class _EventTestBody(BaseModel):
    """*advisory* 테스트 이벤트 emit — UI / 운영자가 sandbox 테스트할 때 사용."""
    level:    str  = Field("INFO", description="DEBUG/INFO/WARN/ERROR/CRITICAL")
    category: str  = Field("SYSTEM",
                            description="SYSTEM/BACKEND/MARKET_DATA/UNIVERSE/STRATEGY/AGENT/RISK/PERMISSION/ORDER/PAPER/DESKTOP")
    code:     str  = Field("OPERATOR_TEST_EVENT")
    message:  str  = Field("operator test event")
    details:  dict[str, Any] = Field(default_factory=dict)


@router.post("/system/events/test")
def post_system_events_test(body: _EventTestBody) -> dict:
    """운영자가 UI 에서 발사하는 *advisory* 테스트 이벤트.

    Secret 의심 패턴 발견 시 *400* + safe message. 정상 이벤트는 200 + 기록.
    """
    try:
        ev = get_runtime_event_log().emit(
            level=body.level, category=body.category,
            code=body.code, message=body.message, details=body.details,
        )
    except SecretLeakBlockedError as exc:
        return {
            "ok":      False,
            "error":   "secret_leak_blocked",
            "message": str(exc),
            **_common_notice(),
        }
    except ValueError as exc:
        return {
            "ok":      False,
            "error":   "invalid_input",
            "message": str(exc),
            **_common_notice(),
        }
    return {
        "ok":    True,
        "event": ev.to_dict(),
        **_common_notice(),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Internal helper for other modules to push events via API path (not import).
# (실제 운영 코드는 `from app.system.event_log import log_event` 권장.)
# ──────────────────────────────────────────────────────────────────────────────


def emit_runtime_event(
    *,
    level:    EventLevel | str,
    category: EventCategory | str,
    code:     str,
    message:  str,
    details:  dict[str, Any] | None = None,
) -> None:
    """다른 모듈이 import 해 사용할 수 있는 shorthand — failure-safe."""
    log_event(
        level=level, category=category, code=code,
        message=message, details=details,
    )


__all__ = ["router", "emit_runtime_event"]


# ──────────────────────────────────────────────────────────────────────────────
# Backwards compat: convenience to also expose ZeroOrderReason for tests.
# ──────────────────────────────────────────────────────────────────────────────


_RE_EXPORTED_ZERO_ORDER_REASON = ZeroOrderReason
_RE_EXPORTED_ANALYZE = analyze_zero_order_reason
