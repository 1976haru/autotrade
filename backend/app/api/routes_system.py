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

import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
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
# #53 / 7-01 — EXE backend / sidecar / diagnostics status consistency
#
# 단일 read-only endpoint 가 EXE 운영 상태를 *분리된* 표준 enum 으로 emit 한다.
# 목적: 사용자가 "Backend 연결됨" 과 "연결 실패" 를 *동시에* 보지 않도록,
# backend_api_reachable / sidecar_status / diagnostics_status / db_status /
# kis_paper_readiness 를 명시 분리. 모든 값은 boolean / enum / timestamp 만 —
# Secret / API key / 계좌번호 원문 0건. broker / OrderExecutor / route_order
# 호출 0건. DB write 0건 (settings 읽기 + db_is_ready() + readiness 평가만).
#
# *주의*: 본 endpoint 가 응답한다는 사실 자체가 backend 가 reachable 함을
# 뜻한다. 따라서 body 의 backend_api_reachable 는 항상 True 이며, 연결 실패
# (false) 판정은 frontend 가 fetch 실패로부터 단일 진실로 결정한다 (frontend
# helper normalizeExeStatus 가 unreachable 시 의존 상태를 UNKNOWN 으로 강등).
# ──────────────────────────────────────────────────────────────────────────────


def _db_and_diagnostics_status() -> tuple[str, str, Optional[str]]:
    """(db_status, diagnostics_status, last_error_message) — read-only.

    db_is_ready() / migration state 만 본다. 무거운 진단 쿼리는 실행하지 않음
    (별도 `/system/diagnostics` 가 담당). 어떤 경우에도 예외를 밖으로 던지지
    않는다 — 상태 표시용이므로 실패 시 UNKNOWN 으로 안전 강등.
    """
    try:
        from app.db.migration_runner import db_is_ready, get_migration_status
        mig_state = str(get_migration_status().state.value).upper()
        if mig_state == "FAILED":
            return "FAIL", "FAIL", "DB 마이그레이션이 실패했습니다. 로그를 확인하세요."
        if db_is_ready():
            return "OK", "OK", None
        # PENDING / RUNNING 등 — DB 준비 중. 아직 실패는 아님.
        return "UNKNOWN", "DEGRADED", "DB 준비 중입니다 (마이그레이션 진행 중일 수 있음)."
    except Exception:  # noqa: BLE001
        return "UNKNOWN", "UNKNOWN", "DB 상태를 확인할 수 없습니다."


def _kis_paper_readiness_status() -> str:
    """READY / BLOCKED / UNKNOWN — readiness 평가 (broker 호출 0건)."""
    try:
        from app.kis_paper.readiness import evaluate_readiness
        rd = evaluate_readiness(get_settings())
        return "READY" if bool(rd.ready) else "BLOCKED"
    except Exception:  # noqa: BLE001
        return "UNKNOWN"


def _sidecar_status() -> str:
    """RUNNING / UNKNOWN — backend 가 Tauri sidecar 로 기동되었는지 env 마커로 판정.

    backend 는 자신이 sidecar wrapper 의 자식 프로세스인지 직접 알 수 없으므로,
    launcher 가 주입하는 `AUTOTRADE_DESKTOP_SIDECAR` 마커가 truthy 일 때만
    RUNNING 으로 보고한다. 그 외(웹 / dev / 마커 없음)는 UNKNOWN — frontend 가
    desktop 감지 + reachable 로 추가 refine.
    """
    marker = str(os.getenv("AUTOTRADE_DESKTOP_SIDECAR", "") or "").strip().lower()
    return "RUNNING" if marker in ("1", "true", "yes", "on") else "UNKNOWN"


@router.get("/system/exe-status")
def get_exe_status() -> dict:
    """EXE 운영 상태 표준 enum — read-only. broker / DB write 0건.

    응답에는 boolean / enum / timestamp 만 포함되며 Secret / API key / 계좌번호
    원문은 0건 (모든 하위 평가가 boolean 만 산출).
    """
    db_status, diagnostics_status, last_error = _db_and_diagnostics_status()
    return {
        "backend_api_reachable": True,
        "sidecar_status":        _sidecar_status(),
        "diagnostics_status":    diagnostics_status,
        "db_status":             db_status,
        "kis_paper_readiness":   _kis_paper_readiness_status(),
        "checked_at":            datetime.now(timezone.utc).isoformat(),
        "last_error_message":    last_error,
        "is_live_authorization": False,
        "contains_secret":       False,
    }


@router.get("/system/build-info")
def get_build_info_endpoint() -> dict:
    """#57 / 7-05 — backend sidecar build metadata (read-only).

    app version / channel / git commit / branch / build time / dirty 여부만
    반환 — Secret / API key / 계좌번호 0건. broker / OrderExecutor / route_order
    호출 0건, DB write 0건.
    """
    from app.system.build_info import get_build_info
    return get_build_info()


@router.get("/system/preflight")
def get_preflight() -> dict:
    """#63 / 8-01 — EXE Preflight Smoke 결과 (read-only).

    health / 안전 flag / KIS readiness / DB / auto loop / Agent Council /
    Decision Episode / build·version / update status 를 점검해 PASS/WARN/FAIL.
    broker / OrderExecutor / route_order 호출 0건, DB read-only SELECT 만,
    Secret / API key / 계좌번호 원문 0건.
    """
    from app.db.session import SessionLocal
    from app.system.preflight import evaluate_preflight
    db = SessionLocal()
    try:
        return evaluate_preflight(db=db)
    finally:
        db.close()


@router.get("/system/program-integrity")
def get_program_integrity(request: Request) -> dict:
    """BUILD-01 — 최종 빌드 전 전체 프로그램 정합성 점검 (read-only, offline/fake).

    Universe → KIS readiness → 4전략 vote → Agent Council → RiskOfficer →
    exit_plan → quality → BUY/SELL/HOLD → KIS Paper decision → fake 주문 결과 →
    order_quality → portfolio → outcome/review → feedback/quality → UI/API →
    Live safety 를 한 번에 점검. broker / OrderExecutor / route_order / KIS 실제
    API 호출 0건, DB write 0건, secret 0건. is_live_authorization=False.
    """
    from app.kis_paper.readiness import evaluate_readiness
    from app.system.program_integrity_gate import (
        GateInputs,
        run_program_integrity_gate,
    )
    s = get_settings()
    try:
        rd = evaluate_readiness(s)
        creds = bool(getattr(rd, "credentials_present", False))
    except Exception:  # noqa: BLE001 — readiness 실패해도 점검은 계속.
        creds = None
    # 등록된 read-only route path 수집 (UI/API 섹션 검증용).
    routes: set[str] = set()
    for r in getattr(request.app, "routes", []):
        p = getattr(r, "path", None)
        if isinstance(p, str):
            routes.add(p)
    report = run_program_integrity_gate(GateInputs(
        enable_live_trading=bool(s.enable_live_trading),
        enable_ai_execution=bool(s.enable_ai_execution),
        enable_futures_live_trading=bool(s.enable_futures_live_trading),
        kis_is_paper=bool(s.kis_is_paper),
        default_mode=str(s.default_mode.value),
        kis_credentials_present=creds,
        available_api_routes=frozenset(routes),
    ))
    return report.to_dict()


@router.get("/system/kis-paper-autotrade-audit")
def get_kis_paper_autotrade_audit(request: Request) -> dict:
    """BUILD-02B-0 — KIS 모의 AI 자동매매 전체 코드 감사 (read-only, offline/fake).

    AI 판단 → KIS Paper 주문 결정 → fake 주문 결과 → order_quality → portfolio →
    outcome/review/feedback 전 흐름 + 권한 게이트/Live safety 를 fake 로 감사한다.
    **실제 KIS API 호출 0건, 주문 0건, 실전 승인 아님.** KIS 자격은 present 여부만.
    """
    from app.kis_paper.readiness import evaluate_readiness
    from app.system.kis_paper_ai_autotrade_audit import (
        KisPaperAuditInputs,
        run_kis_paper_ai_autotrade_audit,
    )
    s = get_settings()
    try:
        rd = evaluate_readiness(s)
        creds = bool(getattr(rd, "credentials_present", False))
    except Exception:  # noqa: BLE001
        creds = None
    routes: set[str] = set()
    for r in getattr(request.app, "routes", []):
        p = getattr(r, "path", None)
        if isinstance(p, str):
            routes.add(p)
    report = run_kis_paper_ai_autotrade_audit(
        KisPaperAuditInputs(
            enable_kis_paper_auto_trading=bool(getattr(s, "enable_kis_paper_auto_trading", True)),
            kis_paper_auto_order_dry_run=bool(getattr(s, "kis_paper_auto_order_dry_run", True)),
            kis_is_paper=bool(s.kis_is_paper),
            enable_live_trading=bool(s.enable_live_trading),
            enable_ai_execution=bool(s.enable_ai_execution),
            kis_credentials_present=creds,
        ),
        available_api_routes=frozenset(routes),
    )
    return report.to_dict()


@router.get("/system/strategy-potential")
def get_strategy_potential() -> dict:
    """STRATEGY-VALIDATION-01 — 전략 가능성 종합 평가 (advisory, read-only).

    sample fixture 로 backtest + walk-forward + stress 를 in-process 실행 후 종합 평가
    한다. **자동 적용 / 실전 전환 / 주문 0건, 수익 보장 아님.** sample fixture 결과는
    기능 확인용이라 `sample_fixture_only=True` → STRONG_CANDIDATE 판정 불가.
    broker / OrderExecutor / route_order / KIS 실제 API 호출 0건, DB write 0건.
    """
    from app.backtest.strategy_council_backtest import (
        BacktestInput,
        load_ohlcv_from_csv,
        run_strategy_council_backtest,
        summarize_backtest_report,
    )
    from app.stress_test.agent_stress_test import (
        run_agent_stress_test,
        summarize_stress_report,
    )
    from pathlib import Path
    from app.system.strategy_potential import (
        StrategyPotentialInputs,
        evaluate_strategy_potential,
        to_dict,
    )

    csv_path = (
        Path(__file__).resolve().parents[2]
        / "tests" / "fixtures" / "backtest" / "sample_ohlcv.csv"
    )
    bt = None
    st = None
    try:
        bars = load_ohlcv_from_csv(str(csv_path))
        bt = summarize_backtest_report(
            run_strategy_council_backtest(BacktestInput(bars=tuple(bars))))
    except Exception:  # noqa: BLE001 — fixture 부재/형식 문제여도 평가는 계속.
        bt = None
    try:
        st = summarize_stress_report(run_agent_stress_test())
    except Exception:  # noqa: BLE001
        st = None
    report = evaluate_strategy_potential(StrategyPotentialInputs(
        backtest=bt, stress=st, has_real_data=False))
    return to_dict(report)


@router.get("/system/intraday-strategy-validation/latest")
def get_intraday_strategy_validation_latest() -> dict:
    """INTRADAY-DATA-01 — 분봉 단타 전략 검증 (latest, read-only, 무거운 실행 금지).

    `reports/strategy_validation/intraday_strategy_latest.json` 가 있으면 그 요약을
    반환하고, **없으면 empty fallback** 을 반환한다(분봉 backtest 는 무거우므로 API 에서
    실행하지 않음 — CLI `run_intraday_strategy_validation.py --write-latest` 전용).
    broker / OrderExecutor / route_order / KIS 주문 API 호출 0건, secret/계좌 원문 0건.
    """
    import json
    from pathlib import Path

    latest = Path("reports/strategy_validation/intraday_strategy_latest.json")
    if latest.exists():
        try:
            data = json.loads(latest.read_text(encoding="utf-8"))
            data["_source"] = "report_file"
            return data
        except Exception:  # noqa: BLE001
            pass
    return {
        "_source": "empty",
        "available": False,
        "intraday_data_used": False,
        "overall_verdict": "RESEARCH_ONLY",
        "total_trades": 0,
        "symbols_count": 0,
        "pass_symbols": [],
        "blocked_symbols": [],
        "agent_value_summary": "AGENT_VALUE_INSUFFICIENT_SAMPLE",
        "do_not_auto_apply": True,
        "auto_apply_allowed": False,
        "is_live_authorization": False,
        "is_order_signal": False,
        "contains_secret": False,
        "disclaimer": (
            "분봉 검증 리포트가 아직 없습니다. CLI "
            "`run_intraday_strategy_validation.py --write-latest` 실행 후 갱신됩니다. "
            "자동 적용 / 실전 전환 / 주문 0건, 수익 보장 아님."
        ),
    }


@router.get("/system/real-data-strategy-validation/latest")
def get_real_data_strategy_validation_latest() -> dict:
    """REAL-DATA-STRATEGY-01 — 실제/준실제 데이터 전략 가능성 검증 (latest, read-only).

    `reports/strategy_validation/real_data_strategy_latest.json` 가 있으면 그 요약을
    반환하고, 없으면 *기능 시연용* 으로 quasi-real 데모 CSV 에 대해 경량 평가를 즉석
    계산해 반환한다(무거운 다종목 실행은 CLI 전용). **자동 적용/실전 전환/주문 0건.**
    broker / OrderExecutor / route_order / KIS 주문 API 호출 0건, secret/계좌 원문 0건.
    """
    import json
    from pathlib import Path

    latest = Path("reports/strategy_validation/real_data_strategy_latest.json")
    if latest.exists():
        try:
            data = json.loads(latest.read_text(encoding="utf-8"))
            data["_source"] = "report_file"
            return data
        except Exception:  # noqa: BLE001 — 손상 시 즉석 계산으로 fallback.
            pass

    from app.market_data.real_ohlcv_loader import load_from_csv
    from app.system.real_data_strategy import evaluate_real_data_strategy, to_dict

    demo = (
        Path(__file__).resolve().parents[2]
        / "tests" / "fixtures" / "real_data" / "demo_quasi_real.csv"
    )
    loaded = load_from_csv(str(demo))
    report = evaluate_real_data_strategy(loaded)
    out = to_dict(report)
    out["_source"] = "on_demand_demo_fixture"
    return out


@router.get("/system/premarket-readiness")
def get_premarket_readiness() -> dict:
    """BUILD-02A — 장 열리기 전 사전 검증 (fast mode, read-only, offline).

    환경변수 / KIS 자격 present / Paper·Live 분리 / Universe / Portfolio / Agent
    카드 / BUILD-01 정합성 / preflight / 문서·Runbook / 리포트 스크립트 가용성을
    한 번에 점검. **실제 KIS API 호출 0건, 주문 0건, 실전 승인 아님.** KIS 자격은
    present 여부만 — 원문 0건. full mode(테스트/빌드 명령)는 CLI 전용.
    """
    from app.db.session import SessionLocal
    from app.kis_paper.readiness import evaluate_readiness
    from app.system.premarket_readiness_gate import (
        PremarketInputs,
        run_premarket_readiness_gate,
    )
    from app.system.preflight import evaluate_preflight
    s = get_settings()
    try:
        rd = evaluate_readiness(s)
        creds = bool(getattr(rd, "credentials_present", False))
    except Exception:  # noqa: BLE001
        creds = None
    preflight_result = None
    db = SessionLocal()
    try:
        preflight_result = evaluate_preflight(db=db)
    except Exception:  # noqa: BLE001 — preflight 실패해도 사전 검증은 계속.
        preflight_result = None
    finally:
        db.close()
    report = run_premarket_readiness_gate(PremarketInputs(
        enable_live_trading=bool(s.enable_live_trading),
        enable_ai_execution=bool(s.enable_ai_execution),
        enable_futures_live_trading=bool(s.enable_futures_live_trading),
        kis_is_paper=bool(s.kis_is_paper),
        default_mode=str(s.default_mode.value),
        kis_credentials_present=creds,
        market_data_provider=str(s.market_data_provider),
        preflight_result=preflight_result,
    ), mode="fast")
    return report.to_dict()


@router.get("/system/logs")
def get_system_logs(
    source:   str = "ALL",
    severity: str = "ALL",
    q:        Optional[str] = None,
    limit:    int = 100,
    db:       Session = Depends(get_db),
) -> dict:
    """#56 / 7-04 — 통합 오류/이벤트 로그 뷰어 (read-only, 최근 100건).

    RuntimeEvent / AgentDecision(AI 판단) / KIS 주문(OrderAuditLog)을 단일
    모양으로 병합. source / severity / keyword 필터. free-text 는 마스킹되어
    Secret / API key / 계좌번호 원문 0건. broker / OrderExecutor / route_order
    호출 0건, DB read-only SELECT 만.
    """
    from app.system.log_viewer import collect_recent_logs
    return collect_recent_logs(
        db=db, source=source, severity=severity, q=q, limit=limit,
    )


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


# ──────────────────────────────────────────────────────────────────────────────
# P-32: 이벤트 로그 품질 점검 (read-only diagnostics) — DB write 0건
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/diagnostics/event-integrity")
def get_event_integrity_diagnostics(
    lookback_days: int = 7,
    include_info:  bool = False,
) -> dict:
    """decision episode 이벤트 정합성 진단 (read-only).

    판단→주문→체결→성과→복기→포트폴리오 연결 누락/불일치를 점검한다. DB write
    0건(SELECT only), broker / OrderExecutor / route_order / 실 계좌 조회 0건,
    secret 0건, is_live_authorization=False. **진단만 — 자동 주문 중단 없음**.
    """
    from app.db.session import SessionLocal
    from app.diagnostics.event_integrity import (
        SEV_INFO,
        run_event_integrity_diagnostics,
    )
    db = SessionLocal()
    try:
        report = run_event_integrity_diagnostics(
            db, lookback_days=max(1, min(365, int(lookback_days))))
    finally:
        db.close()
    d = report.to_dict()
    if not include_info:
        d["issues"] = [i for i in d["issues"] if i.get("severity") != SEV_INFO]
    return {
        "report": d,
        "summary": {
            "integrity_score":   d["integrity_score"],
            "safe_for_analysis": d["safe_for_analysis"],
            "safe_for_paper_gate": d["safe_for_paper_gate"],
            "issue_counts":      d["issue_counts"],
            "contains_secret":       False,
            "is_live_authorization": False,
        },
    }


__all__ = ["router", "emit_runtime_event"]


# ──────────────────────────────────────────────────────────────────────────────
# Backwards compat: convenience to also expose ZeroOrderReason for tests.
# ──────────────────────────────────────────────────────────────────────────────


_RE_EXPORTED_ZERO_ORDER_REASON = ZeroOrderReason
_RE_EXPORTED_ANALYZE = analyze_zero_order_reason
