"""Default Universe + Paper Diagnostics — 단위 + 정적 + API 테스트.

검증 항목 (사용자 요청서):
- 사용자 관심종목 비어있으면 fallback 50개 universe
- USER_DEFINED / FALLBACK_MARKET_CAP_TOP50 source 표시
- universe_count == 50
- 사용자 관심종목 우선
- universe 0건이면 NO_UNIVERSE 차단 사유 반환
- PAPER 모드에서 live execution 차단
- PAPER virtual execution 허용 또는 차단 사유 명확히 반환
- market data 없으면 NO_MARKET_DATA / NO_CANDIDATE
- strategy signal 없으면 NO_STRATEGY_SIGNAL
- 주문 0건일 때 마지막 차단 사유 carry
- broker / OrderExecutor / route_order 호출 0건 (정적 + AST 가드)
- settings.enable_*_trading mutate 0건
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.universe.default_universe import (
    DEFAULT_UNIVERSE_LIMIT,
    DEFAULT_UNIVERSE_NAME,
    FALLBACK_MARKET_CAP_TOP50_SYMBOLS,
    FALLBACK_WARNING_KO,
    ResolvedUniverse,
    UniverseSource,
    get_default_universe,
    get_fallback_universe_symbols,
)
from app.universe.paper_diagnostics import (
    AutoBotLoopInput,
    FrontendModeInput,
    PaperBlockReason,
    PaperDiagnosticsReport,
    PermissionGateInput,
    SafetyFlagsInput,
    evaluate_paper_diagnostics,
    human_message_ko,
)


_UNIVERSE_MODULE = (
    Path(__file__).resolve().parents[1]
    / "app" / "universe" / "default_universe.py"
)
_DIAG_MODULE = (
    Path(__file__).resolve().parents[1]
    / "app" / "universe" / "paper_diagnostics.py"
)
_ROUTES_MODULE = (
    Path(__file__).resolve().parents[1]
    / "app" / "api" / "routes_paper_universe.py"
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. ResolvedUniverse dataclass invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestResolvedUniverseInvariants:
    def test_is_paper_safe_only_must_be_true(self):
        with pytest.raises(ValueError, match="is_paper_safe_only"):
            ResolvedUniverse(
                source=UniverseSource.USER_DEFINED,
                name="x", symbols=("005930",),
                is_paper_safe_only=False,  # type: ignore[arg-type]
            )

    def test_is_order_signal_must_be_false(self):
        with pytest.raises(ValueError, match="is_order_signal"):
            ResolvedUniverse(
                source=UniverseSource.USER_DEFINED,
                name="x", symbols=("005930",),
                is_order_signal=True,  # type: ignore[arg-type]
            )

    def test_is_live_authorization_must_be_false(self):
        with pytest.raises(ValueError, match="is_live_authorization"):
            ResolvedUniverse(
                source=UniverseSource.USER_DEFINED,
                name="x", symbols=("005930",),
                is_live_authorization=True,  # type: ignore[arg-type]
            )

    def test_empty_source_must_have_empty_symbols(self):
        with pytest.raises(ValueError, match="EMPTY"):
            ResolvedUniverse(
                source=UniverseSource.EMPTY,
                name="x", symbols=("005930",),
            )

    def test_user_defined_cannot_have_fallback_used_true(self):
        with pytest.raises(ValueError, match="USER_DEFINED"):
            ResolvedUniverse(
                source=UniverseSource.USER_DEFINED,
                name="x", symbols=("005930",),
                fallback_used=True,
            )

    def test_to_dict_carries_all_fields(self):
        r = ResolvedUniverse(
            source=UniverseSource.FALLBACK_MARKET_CAP_TOP50,
            name=DEFAULT_UNIVERSE_NAME,
            symbols=("005930", "000660"),
            requested_limit=10,
            fallback_used=True,
            warning_ko="warn",
        )
        d = r.to_dict()
        assert d["universe_source"] == "FALLBACK_MARKET_CAP_TOP50"
        assert d["universe_count"] == 2
        assert d["symbols"] == ["005930", "000660"]
        assert d["fallback_used"] is True
        assert d["warning"] == "warn"
        assert d["is_paper_safe_only"] is True
        assert d["is_order_signal"] is False
        assert d["is_live_authorization"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 2. get_fallback_universe_symbols
# ─────────────────────────────────────────────────────────────────────────────


class TestFallbackHelper:
    def test_default_limit_50(self):
        assert len(get_fallback_universe_symbols()) == 50

    def test_explicit_limit_10(self):
        assert len(get_fallback_universe_symbols(10)) == 10

    def test_limit_zero_returns_empty(self):
        assert get_fallback_universe_symbols(0) == ()

    def test_limit_over_50_caps_at_snapshot_size(self):
        assert len(get_fallback_universe_symbols(100)) == 50

    def test_negative_limit_raises(self):
        with pytest.raises(ValueError, match="limit"):
            get_fallback_universe_symbols(-1)

    def test_samsung_first(self):
        symbols = get_fallback_universe_symbols(5)
        assert symbols[0] == "005930"  # 삼성전자
        assert symbols[1] == "000660"  # SK하이닉스

    def test_fallback_list_has_50_entries(self):
        # snapshot lock — 사용자 요청서 정확히 50개.
        assert len(FALLBACK_MARKET_CAP_TOP50_SYMBOLS) == 50


# ─────────────────────────────────────────────────────────────────────────────
# 3. get_default_universe — user vs fallback
# ─────────────────────────────────────────────────────────────────────────────


class TestGetDefaultUniverse:
    def test_no_user_symbols_returns_fallback_50(self):
        # 사용자 관심종목이 비어있으면 기본 Universe 50개 반환.
        r = get_default_universe(user_symbols=None)
        assert r.source == UniverseSource.FALLBACK_MARKET_CAP_TOP50
        assert r.count == 50
        assert r.fallback_used is True
        assert "관심종목" in r.warning_ko
        assert "fallback" in r.warning_ko.lower()
        assert r.symbols[0] == "005930"

    def test_empty_user_symbols_returns_fallback_50(self):
        r = get_default_universe(user_symbols=[])
        assert r.source == UniverseSource.FALLBACK_MARKET_CAP_TOP50
        assert r.count == 50
        assert r.fallback_used is True

    def test_default_name_is_kospi_kosdaq_top50(self):
        r = get_default_universe(user_symbols=None)
        assert r.name == "KOSPI_KOSDAQ_TOP50_MARKET_CAP"
        assert r.name == DEFAULT_UNIVERSE_NAME

    def test_user_symbols_take_precedence(self):
        # 관심종목이 있으면 사용자 관심종목이 우선됨.
        r = get_default_universe(user_symbols=["005930", "000660"])
        assert r.source == UniverseSource.USER_DEFINED
        assert r.count == 2
        assert r.fallback_used is False
        assert r.symbols == ("005930", "000660")
        assert r.warning_ko == ""

    def test_user_symbols_normalized_and_deduped(self):
        r = get_default_universe(user_symbols=["  005930 ", "005930", "000660"])
        assert r.source == UniverseSource.USER_DEFINED
        assert r.symbols == ("005930", "000660")

    def test_user_symbols_lowercase_normalized_to_upper(self):
        r = get_default_universe(user_symbols=["aapl", "tsla"])
        assert r.symbols == ("AAPL", "TSLA")

    def test_limit_zero_with_no_user_returns_empty(self):
        r = get_default_universe(user_symbols=None, limit=0)
        assert r.source == UniverseSource.EMPTY
        assert r.count == 0
        assert r.is_empty is True

    def test_limit_truncates_user_symbols(self):
        r = get_default_universe(
            user_symbols=["005930", "000660", "035720"], limit=2,
        )
        assert r.count == 2
        assert r.symbols == ("005930", "000660")

    def test_default_limit_is_50(self):
        assert DEFAULT_UNIVERSE_LIMIT == 50

    def test_metadata_carries_snapshot_disclaimer_in_fallback(self):
        r = get_default_universe(user_symbols=None)
        disclaimer = r.metadata.get("snapshot_disclaimer", "").lower()
        assert "fallback" in disclaimer or "paper test" in disclaimer
        assert r.metadata.get("snapshot_count") == 50

    def test_negative_limit_raises(self):
        with pytest.raises(ValueError, match="limit"):
            get_default_universe(user_symbols=None, limit=-1)


# ─────────────────────────────────────────────────────────────────────────────
# 4. PaperBlockReason + diagnostics result invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestPaperDiagnosticsInvariants:
    def test_is_paper_safe_only_lock(self):
        with pytest.raises(ValueError, match="is_paper_safe_only"):
            PaperDiagnosticsReport(
                primary_block_reason=PaperBlockReason.NONE,
                blocking_reasons=(), warnings=(),
                summary_ko="x",
                universe_source="x", universe_count=0,
                universe_fallback_used=False, universe_warning_ko="",
                market_data_provider="mock",
                default_mode="SIMULATION",
                enable_live_trading=False, enable_ai_execution=False,
                enable_futures_live_trading=False, kis_is_paper=True,
                auto_bot_state="PAUSED", auto_bot_running=False,
                strategy_engine_connected=False,
                last_decision_count=0, last_ledger_events=0,
                last_decision_log_count=0,
                paper_virtual_execution_allowed=True,
                live_execution_blocked=True,
                frontend_mode=None, mode_mismatch=False,
                blocking_messages_ko=(), warning_messages_ko=(),
                next_actions_ko=(),
                is_paper_safe_only=False,  # type: ignore[arg-type]
            )


# ─────────────────────────────────────────────────────────────────────────────
# 5. evaluate_paper_diagnostics — 매트릭스
# ─────────────────────────────────────────────────────────────────────────────


def _base_safety(**over) -> SafetyFlagsInput:
    d = dict(
        default_mode="SIMULATION",
        enable_live_trading=False,
        enable_ai_execution=False,
        enable_futures_live_trading=False,
        kis_is_paper=True,
        market_data_provider="mock",
    )
    d.update(over)
    return SafetyFlagsInput(**d)


def _base_autobot(**over) -> AutoBotLoopInput:
    d = dict(
        state="RUNNING", is_running=True, cycle_count=1,
        last_consumed=True, last_decision_count=1,
        last_decision_action="BUY", last_ledger_events=1,
        last_decision_log_count=1, strategy_engine_connected=True,
    )
    d.update(over)
    return AutoBotLoopInput(**d)


def _base_permission(**over) -> PermissionGateInput:
    d = dict(
        paper_virtual_execution_allowed=True,
        live_execution_blocked=True, last_block_reason=None,
    )
    d.update(over)
    return PermissionGateInput(**d)


class TestDiagnosticsHappyPath:
    def test_all_green_returns_none(self):
        r = evaluate_paper_diagnostics(
            universe_source="USER_DEFINED",
            universe_count=5,
            safety=_base_safety(market_data_provider="yfinance"),
            auto_bot=_base_autobot(),
            permission=_base_permission(),
        )
        assert r.primary_block_reason == PaperBlockReason.NONE
        assert r.has_blocking is False


class TestDiagnosticsBlockingReasons:
    def test_no_universe_when_count_zero(self):
        # universe가 0건이면 NO_UNIVERSE 반환.
        r = evaluate_paper_diagnostics(
            universe_source="EMPTY", universe_count=0,
            safety=_base_safety(),
            auto_bot=_base_autobot(),
            permission=_base_permission(),
        )
        assert r.primary_block_reason == PaperBlockReason.NO_UNIVERSE
        assert PaperBlockReason.NO_UNIVERSE in r.blocking_reasons
        assert any("관심종목" in m for m in r.blocking_messages_ko)

    def test_fallback_universe_is_warning_not_blocking(self):
        r = evaluate_paper_diagnostics(
            universe_source="FALLBACK_MARKET_CAP_TOP50", universe_count=50,
            universe_fallback_used=True,
            universe_warning_ko="fallback warning",
            safety=_base_safety(market_data_provider="yfinance"),
            auto_bot=_base_autobot(),
            permission=_base_permission(),
        )
        assert r.primary_block_reason == PaperBlockReason.NONE
        assert PaperBlockReason.USING_FALLBACK_UNIVERSE in r.warnings
        # 한국어 fallback 경고 메시지 carry.
        assert any(
            "시가총액 상위 50" in m or "fallback" in m.lower()
            for m in r.warning_messages_ko
        )

    def test_auto_bot_not_running_blocks(self):
        r = evaluate_paper_diagnostics(
            universe_source="USER_DEFINED", universe_count=5,
            safety=_base_safety(),
            auto_bot=_base_autobot(state="PAUSED", is_running=False),
            permission=_base_permission(),
        )
        assert r.primary_block_reason == PaperBlockReason.AUTO_BOT_NOT_RUNNING

    def test_market_closed_blocks(self):
        r = evaluate_paper_diagnostics(
            universe_source="USER_DEFINED", universe_count=5,
            safety=_base_safety(),
            auto_bot=_base_autobot(state="MARKET_CLOSED", is_running=False),
            permission=_base_permission(),
        )
        assert r.primary_block_reason == PaperBlockReason.MARKET_CLOSED

    def test_strategy_engine_not_connected_blocks(self):
        # 자동봇 미연동 — 사용자 요청서 "전략 엔진 미연동" 케이스.
        r = evaluate_paper_diagnostics(
            universe_source="USER_DEFINED", universe_count=5,
            safety=_base_safety(),
            auto_bot=_base_autobot(strategy_engine_connected=False),
            permission=_base_permission(),
        )
        assert r.primary_block_reason == PaperBlockReason.STRATEGY_ENGINE_NOT_CONNECTED
        assert any(
            "전략 엔진" in m for m in r.blocking_messages_ko
        )

    def test_paper_execution_disabled_blocks(self):
        # PAPER 모드인데 가상 실행 차단 — 사용자 요청서 케이스.
        r = evaluate_paper_diagnostics(
            universe_source="USER_DEFINED", universe_count=5,
            safety=_base_safety(default_mode="PAPER"),
            auto_bot=_base_autobot(),
            permission=_base_permission(paper_virtual_execution_allowed=False),
        )
        assert r.primary_block_reason == PaperBlockReason.PAPER_EXECUTION_DISABLED
        assert any(
            "가상 실행" in m and "차단" in m
            for m in r.blocking_messages_ko
        )

    def test_no_market_data_blocks(self):
        r = evaluate_paper_diagnostics(
            universe_source="USER_DEFINED", universe_count=5,
            safety=_base_safety(market_data_provider=""),
            auto_bot=_base_autobot(),
            permission=_base_permission(),
        )
        assert r.primary_block_reason == PaperBlockReason.NO_MARKET_DATA

    def test_mock_market_data_is_warning(self):
        r = evaluate_paper_diagnostics(
            universe_source="USER_DEFINED", universe_count=5,
            safety=_base_safety(market_data_provider="mock"),
            auto_bot=_base_autobot(),
            permission=_base_permission(),
        )
        assert PaperBlockReason.MOCK_MARKET_DATA_ONLY in r.warnings

    def test_no_candidate_when_running_no_decisions(self):
        # strategy signal이 없으면 NO_CANDIDATE.
        r = evaluate_paper_diagnostics(
            universe_source="USER_DEFINED", universe_count=5,
            safety=_base_safety(market_data_provider="yfinance"),
            auto_bot=_base_autobot(
                cycle_count=5, last_consumed=False,
                last_decision_count=0, last_ledger_events=0,
            ),
            permission=_base_permission(),
        )
        assert r.primary_block_reason == PaperBlockReason.NO_CANDIDATE

    def test_no_strategy_signal_when_decisions_no_ledger(self):
        r = evaluate_paper_diagnostics(
            universe_source="USER_DEFINED", universe_count=5,
            safety=_base_safety(market_data_provider="yfinance"),
            auto_bot=_base_autobot(
                cycle_count=5, last_consumed=True,
                last_decision_count=3, last_ledger_events=0,
            ),
            permission=_base_permission(),
        )
        assert r.primary_block_reason == PaperBlockReason.NO_STRATEGY_SIGNAL

    def test_blocked_by_permission_gate_when_last_block_reason_set(self):
        r = evaluate_paper_diagnostics(
            universe_source="USER_DEFINED", universe_count=5,
            safety=_base_safety(),
            auto_bot=_base_autobot(),
            permission=_base_permission(
                last_block_reason="duplicate_order_blocked",
            ),
        )
        assert r.primary_block_reason == PaperBlockReason.BLOCKED_BY_PERMISSION_GATE

    def test_mode_mismatch_takes_top_priority(self):
        r = evaluate_paper_diagnostics(
            universe_source="USER_DEFINED", universe_count=5,
            safety=_base_safety(default_mode="SIMULATION"),
            auto_bot=_base_autobot(),
            permission=_base_permission(),
            frontend=FrontendModeInput(displayed_mode="PAPER"),
        )
        assert r.primary_block_reason == PaperBlockReason.MODE_MISMATCH
        assert r.mode_mismatch is True


class TestDiagnosticsSafetyWarnings:
    def test_live_disabled_safe_is_warning(self):
        # PAPER 모드에서 live execution은 차단 → LIVE_DISABLED_SAFE 경고 carry.
        r = evaluate_paper_diagnostics(
            universe_source="USER_DEFINED", universe_count=5,
            safety=_base_safety(enable_live_trading=False),
            auto_bot=_base_autobot(),
            permission=_base_permission(),
        )
        assert PaperBlockReason.LIVE_DISABLED_SAFE in r.warnings

    def test_ai_execution_disabled_safe_is_warning(self):
        r = evaluate_paper_diagnostics(
            universe_source="USER_DEFINED", universe_count=5,
            safety=_base_safety(enable_ai_execution=False),
            auto_bot=_base_autobot(),
            permission=_base_permission(),
        )
        assert PaperBlockReason.AI_EXECUTION_DISABLED_SAFE in r.warnings

    def test_live_disabled_not_block_reason(self):
        # 안전 비활성은 *blocking* 으로 분류되면 안 됨.
        r = evaluate_paper_diagnostics(
            universe_source="USER_DEFINED", universe_count=5,
            safety=_base_safety(),
            auto_bot=_base_autobot(),
            permission=_base_permission(),
        )
        assert PaperBlockReason.LIVE_DISABLED_SAFE not in r.blocking_reasons
        assert PaperBlockReason.AI_EXECUTION_DISABLED_SAFE not in r.blocking_reasons


class TestHumanMessages:
    @pytest.mark.parametrize("reason", list(PaperBlockReason))
    def test_every_reason_has_korean_message(self, reason):
        msg = human_message_ko(reason)
        assert msg and isinstance(msg, str)


# ─────────────────────────────────────────────────────────────────────────────
# 6. API endpoints
# ─────────────────────────────────────────────────────────────────────────────


class TestUniverseEndpoints:
    def test_default_universe_returns_fallback_when_no_watchlist(self, client):
        res = client.get("/api/paper/universe/default")
        assert res.status_code == 200
        body = res.json()
        assert body["universe_source"] == "FALLBACK_MARKET_CAP_TOP50"
        assert body["universe_count"] == 50
        assert body["fallback_used"] is True
        assert body["is_paper_safe_only"] is True
        assert body["is_live_authorization"] is False
        assert "관심종목" in body["warning"]

    def test_default_universe_prefers_user_watchlist(self, client):
        # active watchlist 등록 + 종목 추가.
        cr = client.post("/api/watchlists", json={
            "name": "테스트", "is_active": True,
        })
        assert cr.status_code == 201
        wid = cr.json()["id"]
        client.post(f"/api/watchlists/{wid}/items", json={
            "symbol": "005930",
        })
        client.post(f"/api/watchlists/{wid}/items", json={
            "symbol": "000660",
        })

        res = client.get("/api/paper/universe/default")
        assert res.status_code == 200
        body = res.json()
        assert body["universe_source"] == "USER_DEFINED"
        assert body["universe_count"] == 2
        assert body["symbols"] == ["005930", "000660"]
        assert body["fallback_used"] is False

    def test_universe_preview_with_explicit_symbols(self, client):
        res = client.post("/api/paper/universe/preview", json={
            "user_symbols": ["005930", "000660"],
            "limit": 10,
        })
        assert res.status_code == 200
        body = res.json()
        assert body["universe_source"] == "USER_DEFINED"
        assert body["universe_count"] == 2

    def test_universe_preview_empty_uses_fallback(self, client):
        res = client.post("/api/paper/universe/preview", json={
            "user_symbols": [], "limit": 50,
        })
        assert res.status_code == 200
        body = res.json()
        assert body["universe_source"] == "FALLBACK_MARKET_CAP_TOP50"
        assert body["universe_count"] == 50


class TestDiagnosticsEndpoint:
    def test_get_preflight_no_watchlist_carries_fallback_warning(self, client):
        res = client.get("/api/paper/diagnostics/preflight")
        assert res.status_code == 200
        body = res.json()
        # universe carry.
        assert body["universe_source"] == "FALLBACK_MARKET_CAP_TOP50"
        assert body["universe_count"] == 50
        assert body["universe_fallback_used"] is True
        # warnings 에 fallback carry.
        assert "USING_FALLBACK_UNIVERSE" in body["warnings"]
        # 안전 flag false 일 때만 safe-off 경고 — test runtime의 실제 값 의존성 회피.
        if body["enable_live_trading"] is False:
            assert "LIVE_DISABLED_SAFE" in body["warnings"]
        if body["enable_ai_execution"] is False:
            assert "AI_EXECUTION_DISABLED_SAFE" in body["warnings"]
        # invariant.
        assert body["is_paper_safe_only"] is True
        assert body["is_live_authorization"] is False
        # advisory carry.
        assert "advisory" in body["advisory_disclaimer"].lower()

    def test_get_preflight_mode_mismatch_detected(self, client):
        # backend 가 어떤 모드이든 "NOT_A_REAL_MODE" 와는 항상 mismatch.
        res = client.get(
            "/api/paper/diagnostics/preflight?frontend_mode=NOT_A_REAL_MODE",
        )
        assert res.status_code == 200
        body = res.json()
        assert body["mode_mismatch"] is True
        assert body["primary_block_reason"] == "MODE_MISMATCH"

    def test_post_preflight_with_body(self, client):
        # frontend 가 backend 와 *다른* 모드를 carry → MODE_MISMATCH.
        # backend 가 어떤 모드든, "LIVE" 는 default 안전 flag 와 모순되므로 항상 mismatch.
        res = client.post(
            "/api/paper/diagnostics/preflight",
            json={"frontend_mode": "LIVE_AI_EXECUTION", "limit": 50},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["mode_mismatch"] is True
        # body 가 매칭되는 케이스도 검증 — 같은 모드는 mismatch=False.
        backend_mode = body["default_mode"]
        res2 = client.post(
            "/api/paper/diagnostics/preflight",
            json={"frontend_mode": backend_mode, "limit": 50},
        )
        assert res2.status_code == 200
        assert res2.json()["mode_mismatch"] is False

    def test_preflight_returns_blocking_messages_ko(self, client):
        res = client.get("/api/paper/diagnostics/preflight")
        assert res.status_code == 200
        body = res.json()
        # 한국어 메시지가 비어있지 않음.
        # primary_block_reason 이 NONE 이 아니라면 blocking_messages_ko 비어있지 않음.
        if body["primary_block_reason"] != "NONE":
            assert len(body["blocking_messages_ko"]) > 0


# ─────────────────────────────────────────────────────────────────────────────
# 7. 정적 import / 안전 invariant
# ─────────────────────────────────────────────────────────────────────────────


class TestStaticImportGuards:
    def test_default_universe_module_exists(self):
        assert _UNIVERSE_MODULE.exists()
        assert _DIAG_MODULE.exists()
        assert _ROUTES_MODULE.exists()

    @pytest.mark.parametrize("path", [_UNIVERSE_MODULE, _DIAG_MODULE])
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
                    "app.brokers", "app.brokers.kis", "app.brokers.kis_client",
                    "app.brokers.live_broker",
                    "app.execution", "app.execution.executor",
                    "app.execution.order_router",
                    "app.kis_paper.engine",
                    "anthropic", "openai", "httpx", "requests", "urllib3",
                ):
                    assert not m.startswith(banned), (
                        f"{path.name} 가 금지 모듈 '{m}' import"
                    )

    @pytest.mark.parametrize("path", [_UNIVERSE_MODULE, _DIAG_MODULE])
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

    def test_default_universe_does_not_import_settings(self):
        # caller 가 DTO 로 주입 — 모듈 내부에서 settings 를 직접 읽지 않는다.
        tree = ast.parse(_UNIVERSE_MODULE.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                assert not m.startswith("app.core.config"), (
                    f"default_universe.py 가 settings 직접 import: {m}"
                )

    def test_diagnostics_does_not_import_settings(self):
        tree = ast.parse(_DIAG_MODULE.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                assert not m.startswith("app.core.config"), (
                    f"paper_diagnostics.py 가 settings 직접 import: {m}"
                )

    def test_modules_parse_ok(self):
        ast.parse(_UNIVERSE_MODULE.read_text(encoding="utf-8"))
        ast.parse(_DIAG_MODULE.read_text(encoding="utf-8"))
        ast.parse(_ROUTES_MODULE.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# 8. CLAUDE.md 절대 원칙 — safety flag mutation 0건
# ─────────────────────────────────────────────────────────────────────────────


class TestSafetyFlagInvariants:
    @pytest.mark.parametrize(
        "path", [_UNIVERSE_MODULE, _DIAG_MODULE, _ROUTES_MODULE],
    )
    def test_no_safety_flag_mutations(self, path):
        src = path.read_text(encoding="utf-8")
        for pat in [
            r"settings\.enable_live_trading\s*=",
            r"settings\.enable_ai_execution\s*=",
            r"settings\.enable_futures_live_trading\s*=",
            r"enable_live_trading\s*=\s*True",
            r"enable_ai_execution\s*=\s*True",
            r"enable_futures_live_trading\s*=\s*True",
            r"KIS_IS_PAPER\s*=\s*False",
        ]:
            assert not re.search(pat, src), (
                f"{path.name} 안전 flag mutation 의심 패턴: {pat!r}"
            )

    def test_fallback_warning_mentions_paper_not_recommendation(self):
        # 사용자가 fallback 을 *투자 추천* 으로 오인하지 않도록 메시지 검증.
        assert "투자 추천이 아닙니다" in FALLBACK_WARNING_KO
        assert "PAPER" in FALLBACK_WARNING_KO
