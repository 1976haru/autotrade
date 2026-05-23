"""AI Paper background tick driver — 단위 + 정적 가드 + async 수명주기 테스트.

검증 (사용자 요청서 §7):
- flag 기본 false / flag false 면 미실행
- flag true + PAPER + market OPEN + loop RUNNING 이면 tick 실행 + cycle 증가
- market CLOSED / loop STOPPED / emergency_stop / live enabled 시 미실행 + 정확한 reason
- tick 실행 시 last_tick_at + RuntimeEvent 기록
- no signal → NO_STRATEGY_SIGNAL, mock 정상 → PAPER_DRY_RUN_OK
- dry_run=true 면 체결 미반영, dry_run=false 라도 가상 후보까지만
- broker / live 호출 0회 (invariant + 정적 grep)
- shutdown 시 task cancel, interval 적용
- run-readiness background_tick 필드
- 안전 flag 변경 0건
"""

from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.auto_paper.background_driver import (
    BackgroundTickDriver,
    BackgroundTickReason,
    DriverTickResult,
    get_background_tick_driver,
    reset_background_tick_driver_for_tests,
)
from app.auto_paper.run_once import RunOnceResultCode, run_paper_pipeline_once
from app.core.config import Settings
from app.core.modes import OperationMode


_DRIVER_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "auto_paper" / "background_driver.py"
)

_KST = timezone(timedelta(hours=9))


def _utc(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=_KST).astimezone(timezone.utc)


# 2026-05-22 금요일.
OPEN_TIME = _utc(2026, 5, 22, 10, 0)
CLOSED_TIME = _utc(2026, 5, 22, 16, 0)
PRE_OPEN_TIME = _utc(2026, 5, 22, 8, 30)


def _settings(**kw):
    base = dict(
        enable_ai_paper_background_tick=True,
        enable_live_trading=False,
        default_mode=OperationMode.PAPER,
        kis_is_paper=True,
        ai_paper_tick_dry_run=True,
        ai_paper_tick_max_per_day=0,
        ai_paper_tick_interval_seconds=30,
        market_data_provider="mock",
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _FakeLoop:
    def __init__(self, state="RUNNING"):
        self._state = state
        self._cycle = 0

    def status(self, now=None):
        return SimpleNamespace(state=self._state, cycle_count=self._cycle)

    def tick(self):
        if self._state != "RUNNING":
            raise RuntimeError("not running")
        self._cycle += 1
        return SimpleNamespace(cycle_count=self._cycle)


def _driver(settings, loop, events=None, pipeline=None, now=OPEN_TIME):
    sink = (lambda **kw: events.append(kw["code"])) if events is not None else (lambda **kw: None)
    return BackgroundTickDriver(
        settings_provider=lambda: settings,
        loop_provider=lambda: loop,
        event_sink=sink,
        now_provider=lambda: now,
        pipeline_runner=pipeline or run_paper_pipeline_once,
    )


# ── config 기본값 ────────────────────────────────────────────────────────────


def test_flag_default_false(safe_default_flags):
    s = Settings()
    assert s.enable_ai_paper_background_tick is False
    assert s.ai_paper_tick_interval_seconds == 30
    assert s.ai_paper_tick_dry_run is True
    assert s.ai_paper_tick_max_per_day == 0


# ── gate / tick 조건 ──────────────────────────────────────────────────────────


def test_flag_false_does_not_run():
    d = _driver(_settings(enable_ai_paper_background_tick=False), _FakeLoop("RUNNING"))
    r = d.tick_once(OPEN_TIME)
    assert r.executed is False
    assert r.reason_code == BackgroundTickReason.BACKGROUND_TICK_DISABLED.value


def test_live_enabled_blocks_driver():
    d = _driver(_settings(enable_live_trading=True), _FakeLoop("RUNNING"))
    r = d.tick_once(OPEN_TIME)
    assert r.executed is False
    assert r.reason_code == BackgroundTickReason.LIVE_DISABLED_SAFE.value


def test_non_paper_mode_blocks():
    d = _driver(_settings(default_mode=OperationMode.LIVE_AI_EXECUTION), _FakeLoop("RUNNING"))
    r = d.tick_once(OPEN_TIME)
    assert r.reason_code == BackgroundTickReason.PAPER_ONLY_DRIVER.value


def test_kis_paper_false_with_paper_mode_blocks():
    # PAPER 모드인데 kis_is_paper=false → paper-safe 아님 → 차단.
    d = _driver(_settings(default_mode=OperationMode.PAPER, kis_is_paper=False), _FakeLoop("RUNNING"))
    r = d.tick_once(OPEN_TIME)
    assert r.executed is False
    assert r.reason_code == BackgroundTickReason.PAPER_ONLY_DRIVER.value


def test_market_closed_blocks():
    d = _driver(_settings(), _FakeLoop("RUNNING"), now=CLOSED_TIME)
    r = d.tick_once(CLOSED_TIME)
    assert r.executed is False
    assert r.reason_code == BackgroundTickReason.MARKET_CLOSED.value


def test_loop_stopped_blocks():
    d = _driver(_settings(), _FakeLoop("STOPPED"))
    r = d.tick_once(OPEN_TIME)
    assert r.executed is False
    assert r.reason_code == BackgroundTickReason.AUTO_LOOP_NOT_RUNNING.value


def test_emergency_stop_blocks():
    d = _driver(_settings(), _FakeLoop("EMERGENCY_STOP"))
    r = d.tick_once(OPEN_TIME)
    assert r.executed is False
    assert r.reason_code == BackgroundTickReason.EMERGENCY_STOP_ENABLED.value


# ── 실행 tick ─────────────────────────────────────────────────────────────────


def test_happy_path_executes_and_increments_cycle():
    loop = _FakeLoop("RUNNING")
    events: list[str] = []
    d = _driver(_settings(), loop, events=events)
    r = d.tick_once(OPEN_TIME)
    assert r.executed is True
    assert r.cycle_count == 1
    assert r.pipeline_result_code == RunOnceResultCode.PAPER_DRY_RUN_OK.value
    # 두 번째 tick → cycle 2.
    r2 = d.tick_once(OPEN_TIME)
    assert r2.cycle_count == 2
    # RuntimeEvent 기록됨.
    assert any("AI_PAPER_TICK" in c for c in events)


def test_tick_records_last_tick_at():
    d = _driver(_settings(), _FakeLoop("RUNNING"))
    d.tick_once(OPEN_TIME)
    assert d.status()["last_tick_at"] == OPEN_TIME.isoformat()


def test_blocked_tick_also_records_event():
    events: list[str] = []
    d = _driver(_settings(), _FakeLoop("STOPPED"), events=events)
    r = d.tick_once(OPEN_TIME)
    assert r.recorded_event is True
    assert any("AUTO_LOOP_NOT_RUNNING" in c for c in events)


def test_no_signal_records_no_strategy_signal():
    # pipeline_runner override 로 signal_present=False 강제.
    def runner(**kw):
        return run_paper_pipeline_once(symbol="005930", price=75000, signal_present=False,
                                       record=False)
    d = _driver(_settings(), _FakeLoop("RUNNING"), pipeline=runner)
    r = d.tick_once(OPEN_TIME)
    assert r.executed is True   # 게이트는 통과, 파이프라인 내부에서 no-signal.
    assert r.pipeline_result_code == RunOnceResultCode.NO_STRATEGY_SIGNAL.value


def test_mock_price_yields_dry_run_ok():
    d = _driver(_settings(), _FakeLoop("RUNNING"))
    r = d.tick_once(OPEN_TIME)
    assert r.pipeline_result_code == RunOnceResultCode.PAPER_DRY_RUN_OK.value


def test_dry_run_true_does_not_commit_cash_or_position():
    # dry_run=True 면 run-once 가 PAPER_DRY_RUN_OK 로 끝나고 capital state mutate 0건.
    from app.auto_paper.capital_state import get_capital_state, reset_capital_state_for_tests
    reset_capital_state_for_tests(10_000_000)
    before = get_capital_state().snapshot().available_cash_krw
    d = _driver(_settings(ai_paper_tick_dry_run=True), _FakeLoop("RUNNING"))
    d.tick_once(OPEN_TIME)
    after = get_capital_state().snapshot().available_cash_krw
    assert before == after   # 체결 반영 0건.


def test_dry_run_false_creates_virtual_candidate_only():
    captured = {}

    def runner(**kw):
        captured.update(kw)
        return run_paper_pipeline_once(**kw)

    d = _driver(_settings(ai_paper_tick_dry_run=False), _FakeLoop("RUNNING"), pipeline=runner)
    r = d.tick_once(OPEN_TIME)
    assert captured["dry_run"] is False
    # 가상 후보까지만 — broker_order_sent invariant.
    assert r.broker_order_sent is False
    assert r.pipeline_result_code in (
        RunOnceResultCode.VIRTUAL_ORDER_CANDIDATE_CREATED.value,
        RunOnceResultCode.PAPER_DRY_RUN_OK.value,
    )


def test_max_per_day_caps_ticks():
    d = _driver(_settings(ai_paper_tick_max_per_day=1), _FakeLoop("RUNNING"))
    a = d.tick_once(OPEN_TIME)
    b = d.tick_once(OPEN_TIME)
    assert a.executed is True
    assert b.executed is False
    assert b.reason_code == BackgroundTickReason.BACKGROUND_TICK_MAX_PER_DAY.value


# ── invariants / broker 0 호출 ────────────────────────────────────────────────


def test_result_invariants():
    d = _driver(_settings(), _FakeLoop("RUNNING"))
    r = d.tick_once(OPEN_TIME)
    assert r.is_order_signal is False
    assert r.is_live_authorization is False
    assert r.broker_order_sent is False   # diagnostic/simulated 모드는 broker 무관
    # is_live_authorization=True 는 절대 금지 (실거래 아님).
    with pytest.raises(ValueError):
        DriverTickResult(executed=True, reason_code="X", reason_message="x",
                         cycle_count=1, is_live_authorization=True)
    # KIS_PAPER_AUTO 모드에서는 *모의* broker 주문 전송 시 broker_order_sent=True
    # 허용 (한투 모의투자 — 실거래 아님). is_live_authorization 은 여전히 False.
    ok = DriverTickResult(executed=True, reason_code="KIS_PAPER_SUBMITTED",
                          reason_message="x", cycle_count=1,
                          tick_mode="KIS_PAPER_AUTO", broker_order_sent=True)
    assert ok.broker_order_sent is True
    assert ok.is_live_authorization is False


def test_static_no_broker_or_route_order_imports():
    src = _DRIVER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    forbidden = (
        "app.brokers", "app.execution.executor", "app.execution.order_router",
        "kis_client", "anthropic", "openai", "httpx", "requests",
    )
    for mod in imported:
        for bad in forbidden:
            assert bad not in (mod or ""), f"forbidden import: {mod}"
    assert "broker.place_order(" not in src
    assert "route_order(" not in src
    assert ".place_order(" not in src


# ── async 수명주기 ───────────────────────────────────────────────────────────


def test_start_returns_false_when_flag_off():
    async def _run():
        d = BackgroundTickDriver(settings_provider=lambda: _settings(
            enable_ai_paper_background_tick=False))
        started = d.start()
        assert started is False
        assert d.is_running is False
        await d.stop()
    asyncio.run(_run())


def test_start_returns_false_when_live_enabled():
    async def _run():
        d = BackgroundTickDriver(settings_provider=lambda: _settings(
            enable_live_trading=True))
        assert d.start() is False
        await d.stop()
    asyncio.run(_run())


def test_start_then_stop_cancels_task():
    async def _run():
        loop = _FakeLoop("RUNNING")
        d = BackgroundTickDriver(
            settings_provider=lambda: _settings(ai_paper_tick_interval_seconds=1),
            loop_provider=lambda: loop,
            event_sink=lambda **kw: None,
            now_provider=lambda: OPEN_TIME,
        )
        assert d.start() is True
        await asyncio.sleep(0.2)   # 최소 1 tick.
        assert d.is_running is True
        await d.stop()
        assert d.is_running is False
        # tick 이 최소 1회 실행되어 cycle 증가.
        assert loop._cycle >= 1
    asyncio.run(_run())


# ── singleton + status ───────────────────────────────────────────────────────


def test_singleton_status_keys():
    reset_background_tick_driver_for_tests()
    d = get_background_tick_driver()
    st = d.status()
    for k in ("enabled", "running", "interval_seconds", "dry_run",
              "last_tick_at", "last_reason_code", "is_live_authorization",
              "broker_order_sent"):
        assert k in st
    assert st["is_live_authorization"] is False
    assert st["broker_order_sent"] is False
    reset_background_tick_driver_for_tests()


def test_run_readiness_includes_background_tick(safe_default_flags, client):
    res = client.get("/api/auto-paper/run-readiness")
    assert res.status_code == 200
    bt = res.json()["background_tick"]
    assert bt["enabled"] is False         # 기본 OFF.
    assert bt["running"] is False
    assert bt["interval_seconds"] == 30
    assert bt["dry_run"] is True
    assert bt["is_live_authorization"] is False
    assert bt["broker_order_sent"] is False


def test_safety_flags_unchanged():
    s = Settings()
    assert s.enable_live_trading is False
    assert s.enable_ai_execution is False
    assert s.enable_futures_live_trading is False
    assert s.kis_is_paper is True


def test_simulated_fills_flag_default_false():
    s = Settings()
    assert s.ai_paper_allow_simulated_fills is False
    assert s.ai_paper_fill_slippage_bps == 0.0


# ── tick mode 분리 (diagnostic vs simulated trade) ────────────────────────────


def _sim_settings(**kw):
    base = dict(
        enable_ai_paper_background_tick=True, enable_live_trading=False,
        default_mode=OperationMode.PAPER, kis_is_paper=True,
        ai_paper_tick_dry_run=False, ai_paper_allow_simulated_fills=True,
        ai_paper_tick_max_per_day=0, ai_paper_tick_interval_seconds=30,
        market_data_provider="mock", ai_paper_fill_slippage_bps=0.0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _db_engine():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db.base import Base
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)


def _sim_driver(settings, Sess, **extra):
    from app.risk.risk_manager import RiskManager, RiskPolicy
    return BackgroundTickDriver(
        settings_provider=lambda: settings, loop_provider=lambda: _FakeLoop("RUNNING"),
        event_sink=lambda **kw: None, now_provider=lambda: OPEN_TIME,
        session_factory=lambda: Sess(),
        risk_manager_provider=lambda: RiskManager(RiskPolicy()),
        **extra,
    )


def test_diagnostic_mode_when_dry_run_true():
    # dry_run=True → 항상 DIAGNOSTIC, allow_fills 무관.
    d = _driver(_settings(ai_paper_tick_dry_run=True), _FakeLoop("RUNNING"))
    r = d.tick_once(OPEN_TIME)
    assert r.tick_mode == "DIAGNOSTIC_DRY_RUN"
    assert r.order_created is False


def test_diagnostic_mode_when_allow_fills_false():
    d = _driver(_sim_settings(ai_paper_tick_dry_run=False,
                              ai_paper_allow_simulated_fills=False),
                _FakeLoop("RUNNING"))
    r = d.tick_once(OPEN_TIME)
    assert r.tick_mode == "DIAGNOSTIC_DRY_RUN"
    assert r.order_created is False


def test_simulated_trade_mode_creates_filled_order():
    from app.auto_paper.capital_state import (
        reset_capital_state_for_tests,
    )
    from app.auto_paper.ledger import reset_ledger_for_tests
    from app.db.models import AgentDecisionLog, VirtualOrder
    reset_capital_state_for_tests(10_000_000)
    reset_ledger_for_tests()
    Sess = _db_engine()
    d = _sim_driver(_sim_settings(), Sess)
    r = d.tick_once(OPEN_TIME)
    assert r.tick_mode == "SIMULATED_TRADE"
    assert r.reason_code == "VIRTUAL_ORDER_CANDIDATE_CREATED"
    assert r.order_created is True
    assert r.fill_status == "FILLED"
    assert r.quantity >= 1
    assert r.cash_before == 10_000_000
    assert r.cash_after is not None and r.cash_after < 10_000_000
    assert r.position_quantity >= 1
    assert r.broker_order_sent is False
    assert r.is_live_authorization is False
    db = Sess()
    assert db.query(VirtualOrder).filter_by(status="FILLED").count() >= 1
    assert db.query(AgentDecisionLog).count() >= 1
    db.close()
    reset_capital_state_for_tests(10_000_000)


def test_simulated_trade_error_records_background_tick_error_and_survives():
    # trade_flow_runner 가 예외 → BACKGROUND_TICK_ERROR, task 죽지 않음.
    Sess = _db_engine()
    events: list[str] = []

    def boom(*a, **k):
        raise RuntimeError("boom secret sk-abcdefghij1234567890")

    d = _sim_driver(_sim_settings(), Sess, trade_flow_runner=boom)
    d._event_sink = lambda **kw: events.append((kw["code"], kw["message"]))
    r = d.tick_once(OPEN_TIME)
    assert r.reason_code == "BACKGROUND_TICK_ERROR"
    # reason_message 는 일반 안내(secret 없음).
    assert "sk-" not in r.reason_message
    # emit 된 상세 메시지는 redaction 적용.
    err_events = [m for c, m in events if "BACKGROUND_TICK_ERROR" in c]
    assert err_events and "sk-abcdefghij" not in err_events[0]
    assert "[REDACTED]" in err_events[0]


def test_status_carries_tick_mode_and_order_fields():
    from app.auto_paper.capital_state import reset_capital_state_for_tests
    from app.auto_paper.ledger import reset_ledger_for_tests
    reset_capital_state_for_tests(10_000_000)
    reset_ledger_for_tests()
    Sess = _db_engine()
    d = _sim_driver(_sim_settings(), Sess)
    d.tick_once(OPEN_TIME)
    st = d.status()
    assert st["tick_mode"] == "SIMULATED_TRADE"
    assert st["simulated_fills_enabled"] is True
    assert st["last_order_id"] is not None
    assert st["last_fill_status"] == "FILLED"
    assert st["last_quantity"] >= 1
    assert st["last_cash_after"] is not None
    assert st["is_live_authorization"] is False
    assert st["broker_order_sent"] is False
    reset_capital_state_for_tests(10_000_000)


def test_run_readiness_background_tick_has_tick_mode_field(client):
    bt = client.get("/api/auto-paper/run-readiness").json()["background_tick"]
    assert "tick_mode" in bt
    assert "last_order_id" in bt
    assert "last_fill_status" in bt
    assert "simulated_fills_enabled" in bt
