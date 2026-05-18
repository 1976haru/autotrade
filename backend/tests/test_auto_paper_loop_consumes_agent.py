"""#4-Loop-09: Auto Loop consumes Agent recommendations — tests.

Covers:
* RUNNING tick → consumer invoked → ConsumerResult.consumed=True;
  status fields (last_consumed / last_decision_count / last_ledger_events /
  last_decision_log_count / last_decision_action) all reflect cycle output.
* PAUSED / STOPPED / EMERGENCY_STOP — tick() raises LoopNotRunningError,
  consumer NOT invoked.
* RUNNING tick → 1+ ledger event when decisions are non-HOLD.
* RUNNING tick + db_session → AgentDecisionLog row appended.
* RUNNING tick with risk veto risk_flag → BUY downgraded to HOLD,
  veto reason carried in AgentDecisionLog meta.
* Consumer runner exception → tick still succeeds, last_error captured,
  cycle_count still incremented.
* Static guard — no broker / OrderExecutor / route_order / AI SDK /
  external HTTP imports in agent_consumer.py.
* mode=PAPER + invariants False on every emitted row.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auto_paper.agent_consumer import (
    CONSUMER_SCHEMA_VERSION,
    ConsumerResult,
    build_deterministic_explanation,
    consume_agent_recommendations,
    null_recommendation_provider,
)
from app.auto_paper.ledger import get_ledger, reset_ledger_for_tests
from app.auto_paper.loop import (
    AutoPaperLoop,
    AutoPaperState,
    LoopNotRunningError,
)
from app.db.models import AgentDecisionLog, Base


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "auto_paper" / "agent_consumer.py"
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def _isolated_ledger():
    reset_ledger_for_tests()
    yield
    reset_ledger_for_tests()


# ─────────────────────────────────────────────────────────────────────────────
# 1. consume_agent_recommendations — pure-function level
# ─────────────────────────────────────────────────────────────────────────────


class TestConsumerPure:

    def test_non_running_state_short_circuits(self, db):
        provider_called = {"n": 0}

        def _prov(_now):
            provider_called["n"] += 1
            return build_deterministic_explanation()

        result = consume_agent_recommendations(
            loop_state="PAUSED",
            recommendation_provider=_prov,
            db_session=db,
        )
        assert result.consumed is False
        assert result.decision_count == 0
        assert result.ledger_events == 0
        assert result.decision_log_count == 0
        assert provider_called["n"] == 0   # provider 미호출.
        assert db.query(AgentDecisionLog).count() == 0

    def test_no_provider_short_circuits(self, db):
        result = consume_agent_recommendations(
            loop_state="RUNNING",
            recommendation_provider=None,
            db_session=db,
        )
        assert result.consumed is False
        assert result.decision_count == 0
        assert db.query(AgentDecisionLog).count() == 0

    def test_provider_returns_none_short_circuits(self, db):
        result = consume_agent_recommendations(
            loop_state="RUNNING",
            recommendation_provider=lambda _n: None,
            db_session=db,
        )
        assert result.consumed is False
        assert result.decision_count == 0
        assert "decisions 0건" in result.summary

    def test_provider_error_caught(self, db):
        def _boom(_now):
            raise RuntimeError("provider exploded")

        result = consume_agent_recommendations(
            loop_state="RUNNING",
            recommendation_provider=_boom,
            db_session=db,
        )
        assert result.consumed is False
        assert "provider_error" in result.metadata.get("reason", "")
        assert "RuntimeError" in result.summary

    def test_running_with_provider_produces_decision(self, db):
        prov = lambda _n: build_deterministic_explanation(  # noqa: E731
            strategy="sma_crossover", symbol="005930",
            market_regime="TREND_UP",
        )
        result = consume_agent_recommendations(
            loop_state="RUNNING",
            recommendation_provider=prov,
            virtual_trade_size=1,
            db_session=db,
        )
        assert result.consumed is True
        assert result.decision_count == 1
        assert result.ledger_events == 1
        # AgentDecisionLog row written.
        rows = db.query(AgentDecisionLog).all()
        assert len(rows) == 1
        assert rows[0].decision == "BUY"
        assert rows[0].mode == "PAPER"
        # BUY recorded — by_action carries.
        assert result.by_action.get("BUY") == 1
        assert result.decision_log_count == 1

    def test_risk_veto_flag_downgrades_to_hold(self, db):
        prov = lambda _n: build_deterministic_explanation(  # noqa: E731
            strategy="sma_crossover", symbol="005930",
            risk_flags=["stale_data"],
        )
        result = consume_agent_recommendations(
            loop_state="RUNNING",
            recommendation_provider=prov,
            db_session=db,
        )
        assert result.consumed is True
        rows = db.query(AgentDecisionLog).all()
        assert len(rows) == 1
        assert rows[0].decision == "HOLD"
        meta = rows[0].meta
        assert meta["risk_veto"] is True
        assert "STALE_DATA" in meta["risk_veto_reasons"]


# ─────────────────────────────────────────────────────────────────────────────
# 2. ConsumerResult invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestResultInvariants:

    def test_default_invariants_false(self):
        r = ConsumerResult(
            cycle_at="t", schema_version=CONSUMER_SCHEMA_VERSION,
            consumed=False, explanation_verdict=None,
        )
        assert r.is_order_signal is False
        assert r.auto_apply_allowed is False
        assert r.is_live_authorization is False

    @pytest.mark.parametrize("override", [
        {"is_order_signal": True},
        {"auto_apply_allowed": True},
        {"is_live_authorization": True},
    ])
    def test_invariant_violation_raises(self, override):
        base = dict(
            cycle_at="t", schema_version="1.0",
            consumed=False, explanation_verdict=None,
        )
        base.update(override)
        with pytest.raises(ValueError):
            ConsumerResult(**base)

    def test_to_dict_invariants(self):
        r = ConsumerResult(
            cycle_at="t", schema_version=CONSUMER_SCHEMA_VERSION,
            consumed=True, explanation_verdict="READY_TO_REVIEW",
            decision_count=1, ledger_events=1, by_action={"BUY": 1},
        )
        d = r.to_dict()
        assert d["is_order_signal"] is False
        assert d["auto_apply_allowed"] is False
        assert d["is_live_authorization"] is False
        assert d["by_action"] == {"BUY": 1}


# ─────────────────────────────────────────────────────────────────────────────
# 3. AutoPaperLoop integration
# ─────────────────────────────────────────────────────────────────────────────


class TestLoopIntegration:

    def _runner(self, db, *, risk_flags=None):
        def _run(loop_state: str, now):
            prov = lambda _n: build_deterministic_explanation(  # noqa: E731
                strategy="sma_crossover", symbol="005930",
                risk_flags=risk_flags,
            )
            return consume_agent_recommendations(
                loop_state=loop_state,
                recommendation_provider=prov,
                db_session=db,
                now=now,
            )
        return _run

    def test_running_tick_invokes_consumer_and_persists(self, db):
        loop = AutoPaperLoop(
            agent_consumer_runner=self._runner(db),
        )
        loop._state = AutoPaperState.RUNNING  # bypass market_clock for test.
        status = loop.tick()
        assert status.cycle_count == 1
        assert status.last_consumed is True
        assert status.last_decision_count == 1
        assert status.last_ledger_events == 1
        assert status.last_decision_log_count == 1
        assert status.last_decision_action == "BUY"
        # AgentDecisionLog row written.
        assert db.query(AgentDecisionLog).count() == 1
        # Paper ledger event written.
        assert len(get_ledger().recent(limit=10)) == 1

    @pytest.mark.parametrize("state", [
        AutoPaperState.PAUSED,
        AutoPaperState.STOPPED,
        AutoPaperState.EMERGENCY_STOP,
        AutoPaperState.MARKET_CLOSED,
    ])
    def test_non_running_state_blocks_tick(self, db, state):
        called = {"n": 0}

        def _run(_state, _now):
            called["n"] += 1
            return ConsumerResult(
                cycle_at="t", schema_version=CONSUMER_SCHEMA_VERSION,
                consumed=True, explanation_verdict="X",
                decision_count=5,
            )

        loop = AutoPaperLoop(agent_consumer_runner=_run)
        loop._state = state
        with pytest.raises(LoopNotRunningError):
            loop.tick()
        assert called["n"] == 0
        assert db.query(AgentDecisionLog).count() == 0
        assert len(get_ledger().recent(limit=10)) == 0

    def test_consumer_with_risk_veto_records_hold(self, db):
        loop = AutoPaperLoop(
            agent_consumer_runner=self._runner(db, risk_flags=["stale_data"]),
        )
        loop._state = AutoPaperState.RUNNING
        status = loop.tick()
        assert status.last_decision_count == 1
        # BUY downgraded to HOLD by 4-09 veto.
        assert status.last_decision_action == "HOLD"
        # Ledger still receives HOLD audit row.
        assert status.last_ledger_events >= 1
        rows = db.query(AgentDecisionLog).all()
        assert len(rows) == 1
        assert rows[0].decision == "HOLD"

    def test_consumer_exception_does_not_break_loop(self, db):
        def _boom(_state, _now):
            raise RuntimeError("consumer crash")

        loop = AutoPaperLoop(agent_consumer_runner=_boom)
        loop._state = AutoPaperState.RUNNING
        # tick() must not raise — cycle increments, last_error captured.
        status = loop.tick()
        assert status.cycle_count == 1
        assert status.last_error is not None
        assert "consumer crash" in status.last_error

    def test_set_agent_consumer_runner_replaces_runner(self, db):
        loop = AutoPaperLoop()
        loop._state = AutoPaperState.RUNNING
        # No runner yet — tick has 0 decisions.
        status = loop.tick()
        assert status.last_decision_count == 0
        # Inject runner at runtime.
        loop.set_agent_consumer_runner(self._runner(db))
        status2 = loop.tick()
        assert status2.last_decision_count == 1

    def test_multiple_ticks_accumulate_log_rows(self, db):
        loop = AutoPaperLoop(agent_consumer_runner=self._runner(db))
        loop._state = AutoPaperState.RUNNING
        loop.tick()
        loop.tick()
        loop.tick()
        # 3 ticks, each with 1 decision → 3 AgentDecisionLog rows.
        rows = db.query(AgentDecisionLog).all()
        assert len(rows) == 3
        # All in PAPER mode.
        assert {r.mode for r in rows} == {"PAPER"}


# ─────────────────────────────────────────────────────────────────────────────
# 4. Static guards
# ─────────────────────────────────────────────────────────────────────────────


_FORBIDDEN_IMPORT_SUBSTRINGS = (
    "app.brokers.kis",
    "app.brokers.mock_broker",
    "app.execution.order_router",
    "app.execution.executor",
    "app.execution.order_executor",
    "app.permission.gate",
    "app.ai.assist",
    "app.ai.client",
    "anthropic",
    "openai",
    "httpx",
    "requests",
)


_FORBIDDEN_CALL_SUBSTRINGS = (
    "broker.place_order",
    "broker.cancel_order",
    "route_order(",
    "OrderExecutor",
    "OrderRequest",
)


class TestStaticGuards:

    def _source(self) -> str:
        return _MODULE_PATH.read_text(encoding="utf-8")

    def test_no_forbidden_imports(self):
        tree = ast.parse(self._source())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name or ""
                    for bad in _FORBIDDEN_IMPORT_SUBSTRINGS:
                        assert bad not in name
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for bad in _FORBIDDEN_IMPORT_SUBSTRINGS:
                    assert bad not in module

    def test_no_forbidden_calls(self):
        tree = ast.parse(self._source())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee = ast.unparse(node.func)
                for bad in _FORBIDDEN_CALL_SUBSTRINGS:
                    assert bad not in callee

    def test_no_settings_mutation(self):
        src = self._source()
        assert not re.search(r"settings\.enable_[a-z_]+\s*=", src)

    def test_no_db_write_outside_bridge(self):
        # agent_consumer.py 자체는 db.add 호출 0건 — bridge가 4-10 module을
        # 통해 한 곳에서만 INSERT.
        src = self._source()
        assert "db.add(" not in src
        assert "session.add" not in src
        assert "session.commit" not in src

    def test_null_provider_returns_none(self):
        from datetime import datetime as _dt, timezone as _tz
        assert null_recommendation_provider(_dt.now(_tz.utc)) is None
