"""Operator diagnostics + event log — 단위 + 정적 + API 테스트.

검증 항목 (사용자 요청서):
- diagnostics API 안전 flag 반환 / secret 미반환
- universe_count / universe_source / market_data_provider / strategy_engine_connected
  / paper_execution_allowed / live_execution_blocked=true 반환
- 주문 0건 원인 분석: NO_UNIVERSE / NO_MARKET_DATA / BLOCKED_BY_PERMISSION_GATE /
  INSUFFICIENT_PAPER_CASH 등
- event log: SYSTEM/MARKET_DATA/STRATEGY/RISK/PERMISSION/ORDER 카테고리 기록
- event log API: 최근 이벤트 반환 + secret-like key 거부
- conclusion / next_actions 가 한국어
- 정적 import 가드 (broker / OrderExecutor / route_order / KIS 0건)
- settings.enable_*_trading mutation 0건
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.system.event_log import (
    EventCategory,
    EventLevel,
    RuntimeEvent,
    RuntimeEventLog,
    SecretLeakBlockedError,
    get_runtime_event_log,
    log_event,
    reset_runtime_event_log_for_tests,
)
from app.system.operation_diagnostics import (
    AutoBotInput,
    BackendStatusInput,
    DesktopEnvInput,
    DiagnosticsReport,
    FrontendModeInput,
    MarketDataInput,
    OverallStatus,
    PaperCashInput,
    PermissionInput,
    SafetyFlagsInput,
    TodaySummaryInput,
    UniverseInput,
    ZeroOrderReason,
    evaluate_operation_diagnostics,
    zero_order_reason_human_ko,
)


_EVENT_LOG_MODULE = (
    Path(__file__).resolve().parents[1]
    / "app" / "system" / "event_log.py"
)
_DIAG_MODULE = (
    Path(__file__).resolve().parents[1]
    / "app" / "system" / "operation_diagnostics.py"
)
_ROUTES_MODULE = (
    Path(__file__).resolve().parents[1]
    / "app" / "api" / "routes_system.py"
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_event_log():
    reset_runtime_event_log_for_tests()
    yield
    reset_runtime_event_log_for_tests()


@pytest.fixture
def api_client():
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False,
    )

    def _override_db():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Event log — single emit / filter / secret block
# ─────────────────────────────────────────────────────────────────────────────


class TestRuntimeEventLogBasic:
    def test_emit_appends_entry(self):
        log = RuntimeEventLog(capacity=10)
        ev = log.emit(
            level=EventLevel.INFO, category=EventCategory.SYSTEM,
            code="TEST_OK", message="hello",
        )
        assert ev.id == 1
        assert ev.level == EventLevel.INFO
        assert ev.category == EventCategory.SYSTEM
        assert ev.safe_for_ui is True
        assert ev.contains_secret is False
        assert len(log) == 1

    def test_ring_buffer_evicts_oldest(self):
        log = RuntimeEventLog(capacity=3)
        for i in range(5):
            log.emit(level="INFO", category="SYSTEM",
                     code=f"E{i}", message=f"m{i}")
        assert len(log) == 3
        # 최근 3 entry — E2 / E3 / E4 만 남음.
        events = log.recent(limit=10)
        assert [e.code for e in events] == ["E2", "E3", "E4"]

    def test_string_level_and_category_normalized(self):
        log = RuntimeEventLog()
        ev = log.emit(level="warn", category="market_data",
                      code="X", message="m")
        assert ev.level == EventLevel.WARN
        assert ev.category == EventCategory.MARKET_DATA

    def test_invalid_level_raises(self):
        with pytest.raises(ValueError):
            RuntimeEventLog().emit(
                level="HYPERCRITICAL", category="SYSTEM",
                code="X", message="m",
            )

    def test_clear_resets(self):
        log = RuntimeEventLog()
        log.emit(level="INFO", category="SYSTEM", code="X", message="m")
        log.clear()
        assert len(log) == 0

    def test_summary_buckets(self):
        log = RuntimeEventLog()
        log.emit(level="WARN", category="STRATEGY", code="A", message="m1")
        log.emit(level="ERROR", category="RISK", code="B", message="m2")
        s = log.summary()
        assert s["total"] == 2
        assert s["by_level"]["WARN"] == 1
        assert s["by_level"]["ERROR"] == 1
        assert s["by_category"]["STRATEGY"] == 1
        assert s["by_category"]["RISK"] == 1

    def test_invariants_locked(self):
        with pytest.raises(ValueError):
            RuntimeEvent(
                id=1, timestamp="now", level=EventLevel.INFO,
                category=EventCategory.SYSTEM, code="X", message="m",
                safe_for_ui=False,  # type: ignore[arg-type]
            )
        with pytest.raises(ValueError):
            RuntimeEvent(
                id=1, timestamp="now", level=EventLevel.INFO,
                category=EventCategory.SYSTEM, code="X", message="m",
                contains_secret=True,  # type: ignore[arg-type]
            )
        with pytest.raises(ValueError):
            RuntimeEvent(
                id=1, timestamp="now", level=EventLevel.INFO,
                category=EventCategory.SYSTEM, code="X", message="m",
                is_order_signal=True,  # type: ignore[arg-type]
            )


class TestRuntimeEventLogAllCategories:
    @pytest.mark.parametrize(
        "category",
        [
            "SYSTEM", "BACKEND", "MARKET_DATA", "UNIVERSE", "STRATEGY",
            "AGENT", "RISK", "PERMISSION", "ORDER", "PAPER", "DESKTOP",
        ],
    )
    def test_each_category_emittable(self, category):
        # 사용자 요청서: SYSTEM / MARKET_DATA / STRATEGY / RISK / PERMISSION /
        # ORDER (+ 그 외 5종) 모두 기록 가능 검증.
        ev = log_event(
            level="INFO", category=category,
            code=f"{category}_TEST", message=f"{category} ok",
        )
        assert ev is not None
        assert ev.category.value == category


class TestRuntimeEventLogSecretBlock:
    @pytest.mark.parametrize("payload", [
        "API key is sk-1234567890ABCDEFghij1234567890",
        "Anthropic key sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "Use Bearer abcdefghijklmnopqrstu1234567890",
        "GitHub token ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "JWT eyJabcdefghij.eyJpYXQiOjE1MTYyMzkwMjI.SflKxwRJSMeKKF",
        "Account 12345678-01 holds money",
    ])
    def test_emit_blocks_secret_in_message(self, payload):
        with pytest.raises(SecretLeakBlockedError):
            RuntimeEventLog().emit(
                level="INFO", category="SYSTEM",
                code="X", message=payload,
            )

    def test_emit_blocks_secret_in_details(self):
        with pytest.raises(SecretLeakBlockedError):
            RuntimeEventLog().emit(
                level="INFO", category="SYSTEM",
                code="X", message="ok",
                details={"raw_token": "sk-1234567890ABCDEFghij1234567890"},
            )

    def test_emit_blocks_suspicious_key_name(self):
        # key 이름 자체가 의심스러우면 거부.
        with pytest.raises(SecretLeakBlockedError):
            RuntimeEventLog().emit(
                level="INFO", category="SYSTEM",
                code="X", message="ok",
                details={"openai_api_key": "..."},
            )
        with pytest.raises(SecretLeakBlockedError):
            RuntimeEventLog().emit(
                level="INFO", category="SYSTEM",
                code="X", message="ok",
                details={"password": "..."},
            )

    def test_log_event_helper_silently_drops_secret(self):
        # log_event 는 *경고만* + None 반환 — 운영 흐름 보존.
        ev = log_event(
            level="INFO", category="SYSTEM",
            code="X", message="leak sk-1234567890ABCDEFghij1234567890",
        )
        assert ev is None
        # 로그에는 *기록되지 않음*.
        events = get_runtime_event_log().recent(limit=10)
        assert all(
            "sk-1234567890" not in e.message for e in events
        )

    def test_log_event_records_clean_payload(self):
        ev = log_event(
            level="INFO", category="STRATEGY",
            code="CANDIDATE_GENERATED", message="새 후보 생성",
            details={"symbol": "005930"},
        )
        assert ev is not None
        events = get_runtime_event_log().recent(limit=10)
        assert any(e.code == "CANDIDATE_GENERATED" for e in events)


class TestRuntimeEventLogFilters:
    def _seed(self, log):
        log.emit(level="DEBUG", category="SYSTEM",  code="A", message="d")
        log.emit(level="INFO",  category="SYSTEM",  code="B", message="i")
        log.emit(level="WARN",  category="STRATEGY",code="C", message="w")
        log.emit(level="ERROR", category="RISK",    code="D", message="e")
        log.emit(level="CRITICAL", category="PERMISSION", code="E", message="c")

    def test_level_exact_match(self):
        log = RuntimeEventLog()
        self._seed(log)
        out = log.recent(level="WARN", limit=10)
        assert [e.code for e in out] == ["C"]

    def test_min_level_filter(self):
        log = RuntimeEventLog()
        self._seed(log)
        out = log.recent(min_level="WARN", limit=10)
        assert [e.code for e in out] == ["C", "D", "E"]

    def test_category_filter(self):
        log = RuntimeEventLog()
        self._seed(log)
        out = log.recent(category="SYSTEM", limit=10)
        assert [e.code for e in out] == ["A", "B"]

    def test_code_filter(self):
        log = RuntimeEventLog()
        self._seed(log)
        out = log.recent(code="D", limit=10)
        assert [e.code for e in out] == ["D"]

    def test_limit_tail(self):
        log = RuntimeEventLog()
        self._seed(log)
        out = log.recent(limit=2)
        assert [e.code for e in out] == ["D", "E"]


# ─────────────────────────────────────────────────────────────────────────────
# 2. DiagnosticsReport invariants + zero-order analyzer
# ─────────────────────────────────────────────────────────────────────────────


def _safety(**over) -> SafetyFlagsInput:
    d = dict(
        default_mode="SIMULATION", enable_live_trading=False,
        enable_ai_execution=False, enable_futures_live_trading=False,
        kis_is_paper=True, market_data_provider="mock",
    )
    d.update(over)
    return SafetyFlagsInput(**d)


def _backend(**over) -> BackendStatusInput:
    d = dict(backend_ready=True, db_ready=True, migration_state="COMPLETED")
    d.update(over)
    return BackendStatusInput(**d)


def _autobot(**over) -> AutoBotInput:
    d = dict(
        state="RUNNING", is_running=True, cycle_count=1,
        last_consumed=True, last_decision_count=1,
        last_ledger_events=1, last_decision_log_count=1,
        strategy_engine_connected=True,
    )
    d.update(over)
    return AutoBotInput(**d)


def _permission(**over) -> PermissionInput:
    d = dict(
        paper_virtual_execution_allowed=True,
        live_execution_blocked=True,
        last_block_reason=None,
        risk_manager_last_block_reason=None,
    )
    d.update(over)
    return PermissionInput(**d)


def _market(**over) -> MarketDataInput:
    d = dict(provider="mock", last_fetch_ok=True, last_fetch_at=None,
             stale_symbols=0)
    d.update(over)
    return MarketDataInput(**d)


def _universe(**over) -> UniverseInput:
    d = dict(source="USER_DEFINED", count=5, fallback_used=False,
             warning_ko="")
    d.update(over)
    return UniverseInput(**d)


def _cash(**over) -> PaperCashInput:
    d = dict(available_cash_krw=1_000_000, insufficient_today=False)
    d.update(over)
    return PaperCashInput(**d)


def _today(**over) -> TodaySummaryInput:
    d = dict(
        candidate_count=0, signal_count=0, approved_candidate_count=0,
        rejected_candidate_count=0, virtual_order_count=0,
        blocked_order_count=0, error_count=0, warning_count=0,
    )
    d.update(over)
    return TodaySummaryInput(**d)


def _desktop(**over) -> DesktopEnvInput:
    d = dict(
        is_desktop=False, backend_process_alive=True,
        backend_port_reachable=True, env_file_present=True,
        logs_dir_writable=True, app_data_dir_writable=True, launcher_ok=True,
    )
    d.update(over)
    return DesktopEnvInput(**d)


def _eval(**over) -> DiagnosticsReport:
    args = dict(
        backend=_backend(), safety=_safety(market_data_provider="yfinance"),
        universe=_universe(), market=_market(provider="yfinance"),
        auto_bot=_autobot(), permission=_permission(), cash=_cash(),
        today=_today(), desktop=_desktop(),
        frontend=FrontendModeInput(displayed_mode=None),
    )
    args.update(over)
    return evaluate_operation_diagnostics(**args)


class TestDiagnosticsInvariants:
    def test_safe_for_ui_must_be_true(self):
        with pytest.raises(ValueError, match="safe_for_ui"):
            DiagnosticsReport(
                overall_status=OverallStatus.HEALTHY, conclusion_ko="x",
                next_actions_ko=(),
                default_mode="SIMULATION", enable_live_trading=False,
                enable_ai_execution=False, enable_futures_live_trading=False,
                kis_is_paper=True, market_data_provider="mock",
                frontend_mode=None, mode_mismatch=False,
                backend_ready=True, db_ready=True, migration_state="COMPLETED",
                universe_source="USER_DEFINED", universe_count=1,
                universe_fallback_used=False, universe_warning_ko="",
                market_data_last_fetch_ok=True,
                market_data_last_fetch_at=None,
                market_data_stale_symbols=0,
                auto_bot_state="RUNNING", auto_bot_running=True,
                auto_bot_cycle_count=0,
                auto_bot_last_decision_count=0,
                auto_bot_last_ledger_events=0,
                auto_bot_last_error=None,
                strategy_engine_connected=True,
                paper_virtual_execution_allowed=True,
                live_execution_blocked=True,
                permission_last_block_reason=None,
                risk_manager_last_block_reason=None,
                paper_cash_available_krw=0, paper_cash_insufficient_today=False,
                today_candidate_count=0, today_signal_count=0,
                today_approved_candidate_count=0,
                today_rejected_candidate_count=0,
                today_virtual_order_count=0, today_blocked_order_count=0,
                today_error_count=0, today_warning_count=0,
                has_orders_today=False,
                zero_order_primary_reason=ZeroOrderReason.NONE,
                zero_order_primary_message="x",
                zero_order_secondary_reasons=(),
                zero_order_pipeline_stages=(),
                is_desktop=False, desktop_checks=(), desktop_notes=(),
                event_summary={},
                safe_for_ui=False,  # type: ignore[arg-type]
            )


class TestZeroOrderAnalyzer:
    def test_no_universe_returns_no_universe(self):
        r = _eval(universe=_universe(count=0))
        assert r.zero_order_primary_reason == ZeroOrderReason.NO_UNIVERSE

    def test_no_market_data(self):
        r = _eval(
            safety=_safety(market_data_provider=""),
            market=_market(provider="", last_fetch_ok=False),
            today=_today(),
        )
        assert r.zero_order_primary_reason == ZeroOrderReason.NO_MARKET_DATA

    def test_blocked_by_permission_gate(self):
        r = _eval(
            permission=_permission(last_block_reason="duplicate_order"),
            today=_today(),
        )
        assert r.zero_order_primary_reason == ZeroOrderReason.BLOCKED_BY_PERMISSION_GATE

    def test_blocked_by_risk_manager(self):
        r = _eval(
            permission=_permission(
                risk_manager_last_block_reason="notional_limit",
            ),
            today=_today(),
        )
        # PermissionGate 차단이 없으면 RiskManager 가 primary.
        assert r.zero_order_primary_reason == ZeroOrderReason.BLOCKED_BY_RISK_MANAGER

    def test_insufficient_paper_cash(self):
        r = _eval(
            cash=_cash(available_cash_krw=0, insufficient_today=True),
            today=_today(),
        )
        assert r.zero_order_primary_reason == ZeroOrderReason.INSUFFICIENT_PAPER_CASH

    def test_paper_execution_disabled(self):
        r = _eval(
            permission=_permission(paper_virtual_execution_allowed=False),
            today=_today(),
        )
        assert r.zero_order_primary_reason == ZeroOrderReason.PAPER_EXECUTION_DISABLED

    def test_auto_bot_not_running(self):
        r = _eval(
            auto_bot=_autobot(state="PAUSED", is_running=False),
            today=_today(),
        )
        assert r.zero_order_primary_reason == ZeroOrderReason.AUTO_BOT_NOT_RUNNING

    def test_strategy_engine_not_connected(self):
        r = _eval(
            auto_bot=_autobot(strategy_engine_connected=False),
            today=_today(),
        )
        assert r.zero_order_primary_reason == ZeroOrderReason.STRATEGY_ENGINE_NOT_CONNECTED

    def test_market_closed(self):
        r = _eval(
            auto_bot=_autobot(state="MARKET_CLOSED", is_running=False),
            today=_today(),
        )
        assert r.zero_order_primary_reason == ZeroOrderReason.MARKET_CLOSED

    def test_emergency_stop(self):
        r = _eval(
            auto_bot=_autobot(state="EMERGENCY_STOP", is_running=False),
            today=_today(),
        )
        assert r.zero_order_primary_reason == ZeroOrderReason.EMERGENCY_STOP

    def test_mode_mismatch_top_priority(self):
        r = _eval(
            safety=_safety(default_mode="SIMULATION",
                           market_data_provider="yfinance"),
            frontend=FrontendModeInput(displayed_mode="LIVE_AI_EXECUTION"),
        )
        assert r.zero_order_primary_reason == ZeroOrderReason.MODE_MISMATCH

    def test_backend_offline(self):
        r = _eval(
            backend=_backend(backend_ready=False),
            today=_today(),
        )
        assert r.zero_order_primary_reason == ZeroOrderReason.BACKEND_OFFLINE
        assert r.overall_status == OverallStatus.STOPPED

    def test_desktop_env_error(self):
        r = _eval(
            desktop=_desktop(is_desktop=True, env_file_present=False),
            today=_today(),
        )
        assert r.zero_order_primary_reason == ZeroOrderReason.DESKTOP_ENVIRONMENT_ERROR

    def test_orders_today_means_no_primary(self):
        r = _eval(today=_today(virtual_order_count=3))
        assert r.has_orders_today is True
        assert r.zero_order_primary_reason == ZeroOrderReason.NONE

    def test_pipeline_stages_present(self):
        r = _eval()
        stages = [s["stage"] for s in r.zero_order_pipeline_stages]
        for required in (
            "BACKEND", "UNIVERSE", "MARKET_DATA", "AUTO_BOT",
            "STRATEGY_ENGINE", "PERMISSION_GATE", "RISK_MANAGER", "PAPER_ORDER",
        ):
            assert required in stages


class TestDiagnosticsSafetyAndKorean:
    def test_safety_flags_carried(self):
        r = _eval()
        assert r.enable_live_trading is False
        assert r.enable_ai_execution is False
        assert r.enable_futures_live_trading is False
        assert r.kis_is_paper is True
        assert r.live_execution_blocked is True

    def test_conclusion_is_korean(self):
        r = _eval(
            universe=_universe(count=0), today=_today(),
        )
        # 한글 문자열 포함 검증.
        assert any(
            "가" <= ch <= "힣" for ch in r.conclusion_ko
        ), f"conclusion must contain Korean: {r.conclusion_ko}"

    def test_next_actions_are_korean(self):
        r = _eval(
            universe=_universe(count=0), today=_today(),
        )
        assert len(r.next_actions_ko) > 0
        for a in r.next_actions_ko:
            assert any("가" <= ch <= "힣" for ch in a), (
                f"next action must be Korean: {a}"
            )

    @pytest.mark.parametrize("reason", list(ZeroOrderReason))
    def test_every_reason_has_korean_message(self, reason):
        msg = zero_order_reason_human_ko(reason)
        assert msg
        # NONE 도 "정상" 같은 한글 라벨 보장.
        assert any("가" <= ch <= "힣" for ch in msg)


# ─────────────────────────────────────────────────────────────────────────────
# 3. API endpoints
# ─────────────────────────────────────────────────────────────────────────────


class TestDiagnosticsEndpoint:
    def test_get_diagnostics_returns_safety_flags(self, api_client):
        r = api_client.get("/api/system/diagnostics")
        assert r.status_code == 200
        body = r.json()
        # 안전 flag 반환.
        assert "enable_live_trading" in body
        assert "enable_ai_execution" in body
        assert "kis_is_paper" in body
        assert "live_execution_blocked" in body
        # invariant.
        assert body["safe_for_ui"] is True
        assert body["contains_secret"] is False
        assert body["is_order_signal"] is False
        assert body["is_live_authorization"] is False

    def test_get_diagnostics_returns_universe_state(self, api_client):
        r = api_client.get("/api/system/diagnostics")
        assert r.status_code == 200
        body = r.json()
        assert "universe_source" in body
        assert "universe_count" in body
        assert "universe_fallback_used" in body

    def test_get_diagnostics_returns_market_data_provider(self, api_client):
        r = api_client.get("/api/system/diagnostics")
        body = r.json()
        assert "market_data_provider" in body

    def test_get_diagnostics_returns_strategy_engine_status(self, api_client):
        r = api_client.get("/api/system/diagnostics")
        body = r.json()
        assert "strategy_engine_connected" in body
        assert isinstance(body["strategy_engine_connected"], bool)

    def test_get_diagnostics_returns_paper_execution_allowed(self, api_client):
        r = api_client.get("/api/system/diagnostics")
        body = r.json()
        assert "paper_virtual_execution_allowed" in body
        assert isinstance(body["paper_virtual_execution_allowed"], bool)

    def test_live_execution_blocked_is_true_by_default(self, api_client):
        r = api_client.get("/api/system/diagnostics")
        body = r.json()
        assert body["live_execution_blocked"] is True

    def test_post_diagnostics_with_body(self, api_client):
        r = api_client.post(
            "/api/system/diagnostics",
            json={
                "frontend_mode": "NOT_A_REAL_MODE",
                "today_virtual_order_count": 0,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["mode_mismatch"] is True
        assert body["zero_order_primary_reason"] == "MODE_MISMATCH"

    def test_post_with_universe_override(self, api_client):
        r = api_client.post(
            "/api/system/diagnostics",
            json={
                "universe_source": "FALLBACK_MARKET_CAP_TOP50",
                "universe_count": 50,
                "universe_fallback_used": True,
            },
        )
        body = r.json()
        assert body["universe_source"] == "FALLBACK_MARKET_CAP_TOP50"
        assert body["universe_count"] == 50
        assert body["universe_fallback_used"] is True

    def test_conclusion_and_actions_korean(self, api_client):
        r = api_client.get("/api/system/diagnostics")
        body = r.json()
        assert isinstance(body["conclusion_ko"], str)
        assert isinstance(body["next_actions_ko"], list)
        # 사용자 요청서: 사람이 읽을 수 있는 한국어.
        assert any(
            "가" <= ch <= "힣" for ch in body["conclusion_ko"]
        )

    def test_no_secret_in_diagnostics_payload(self, api_client):
        r = api_client.get("/api/system/diagnostics")
        text = r.text
        for needle in [
            "sk-ant-", "ghp_", "xoxb-",
            "anthropic_api_key", "openai_api_key", "kis_app_secret",
        ]:
            assert needle not in text, f"secret-like token in payload: {needle}"

    def test_summary_endpoint_carries_core_fields(self, api_client):
        r = api_client.get("/api/auto-paper/diagnostics/summary")
        assert r.status_code == 200
        body = r.json()
        for k in (
            "overall_status", "conclusion_ko",
            "zero_order_primary_reason", "zero_order_primary_message",
            "default_mode", "enable_live_trading", "enable_ai_execution",
        ):
            assert k in body


class TestEventsEndpoint:
    def test_recent_endpoint_returns_events(self, api_client):
        # 운영자가 직접 발사한 이벤트가 reflected.
        api_client.post("/api/system/events/test", json={
            "level": "INFO", "category": "STRATEGY",
            "code": "OPERATOR_TEST", "message": "hello",
        })
        r = api_client.get("/api/system/events/recent?limit=10")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] >= 1
        codes = [e["code"] for e in body["events"]]
        assert "OPERATOR_TEST" in codes
        # invariant.
        assert body["safe_for_ui"] is True
        assert body["contains_secret"] is False

    def test_recent_endpoint_filters_by_category(self, api_client):
        api_client.post("/api/system/events/test", json={
            "level": "INFO", "category": "STRATEGY",
            "code": "S1", "message": "m",
        })
        api_client.post("/api/system/events/test", json={
            "level": "INFO", "category": "RISK",
            "code": "R1", "message": "m",
        })
        r = api_client.get("/api/system/events/recent?category=RISK&limit=10")
        body = r.json()
        codes = [e["code"] for e in body["events"]]
        assert "R1" in codes
        assert "S1" not in codes

    def test_recent_endpoint_min_level_filter(self, api_client):
        api_client.post("/api/system/events/test", json={
            "level": "INFO", "category": "SYSTEM",
            "code": "I1", "message": "m",
        })
        api_client.post("/api/system/events/test", json={
            "level": "ERROR", "category": "SYSTEM",
            "code": "E1", "message": "m",
        })
        r = api_client.get("/api/system/events/recent?min_level=WARN&limit=10")
        body = r.json()
        codes = [e["code"] for e in body["events"]]
        assert "E1" in codes
        assert "I1" not in codes

    def test_test_event_blocks_secret(self, api_client):
        r = api_client.post("/api/system/events/test", json={
            "level": "INFO", "category": "SYSTEM",
            "code": "LEAK", "message": "sk-1234567890ABCDEFghij1234567890",
        })
        # secret 차단 — 200 + ok=false.
        body = r.json()
        assert body["ok"] is False
        assert body["error"] == "secret_leak_blocked"

    def test_test_event_blocks_suspicious_details(self, api_client):
        r = api_client.post("/api/system/events/test", json={
            "level": "INFO", "category": "SYSTEM",
            "code": "X", "message": "ok",
            "details": {"openai_api_key": "secret"},
        })
        body = r.json()
        assert body["ok"] is False
        assert body["error"] == "secret_leak_blocked"

    def test_invalid_filter_returns_safe_response(self, api_client):
        r = api_client.get(
            "/api/system/events/recent?category=NOT_REAL&limit=5",
        )
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 0
        assert "error_filter" in body


# ─────────────────────────────────────────────────────────────────────────────
# 4. 정적 import / 안전 invariant 가드
# ─────────────────────────────────────────────────────────────────────────────


class TestStaticImportGuards:
    @pytest.mark.parametrize("path", [_EVENT_LOG_MODULE, _DIAG_MODULE])
    def test_no_broker_imports(self, path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                for banned in (
                    "app.brokers", "app.execution",
                    "app.kis_paper.engine",
                    "anthropic", "openai", "httpx", "requests", "urllib3",
                    "app.core.config",
                ):
                    assert not m.startswith(banned), (
                        f"{path.name} 가 금지 모듈 '{m}' import"
                    )

    @pytest.mark.parametrize(
        "path", [_EVENT_LOG_MODULE, _DIAG_MODULE, _ROUTES_MODULE],
    )
    def test_no_broker_call_patterns(self, path):
        text = path.read_text(encoding="utf-8")
        for pat in (
            r"\bbroker\.place_order\s*\(",
            r"\bbroker\.cancel_order\s*\(",
            r"\broute_order\s*\(",
            r"OrderExecutor\s*\(",
            r"OrderRequest\s*\(",
            r"\.enable_live_trading\s*=",
            r"\.enable_ai_execution\s*=",
            r"\.enable_futures_live_trading\s*=",
        ):
            assert not re.search(pat, text), (
                f"{path.name} 에 금지 패턴: /{pat}/"
            )

    def test_modules_parse_ok(self):
        for p in (_EVENT_LOG_MODULE, _DIAG_MODULE, _ROUTES_MODULE):
            ast.parse(p.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# 5. backend_ready event auto-emitted on lifespan
# ─────────────────────────────────────────────────────────────────────────────


class TestLifespanEventEmitted:
    def test_backend_ready_event_visible_after_startup(self, api_client):
        # TestClient 는 lifespan 을 실행 — BACKEND_READY 이벤트가 emit 됨.
        r = api_client.get("/api/system/events/recent?limit=50")
        body = r.json()
        codes = [e["code"] for e in body["events"]]
        assert "BACKEND_READY" in codes
