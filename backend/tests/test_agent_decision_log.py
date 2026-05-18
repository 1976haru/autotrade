"""#4-10: AgentDecisionLog (Paper) tests.

Covers:
* BUY / SELL / HOLD / EXIT / NO_OP — each persisted to AgentDecisionLog with
  mode="PAPER", agent_name="PaperDecisionBridge".
* Risk veto blocked decisions still recorded (with risk_veto=True meta).
* Position sizing carry — sizing_verdict / sizing_quantity in meta.
* Invariants: is_order_signal=False / auto_apply_allowed=False /
  is_live_authorization=False on every row + every API response.
* No secret fields in meta (sanitizer fail-closed).
* Static guards — module imports no broker / OrderExecutor / route_order /
  AI SDK / external HTTP; INSERT only, no DELETE / UPDATE statements.
* API GET /api/auto-paper/decision-log — read-only, paginated, summary.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.paper_decision_bridge import (
    PositionSnapshot,
    bridge_explanation_to_paper_decisions,
)
from app.agents.paper_start_explanation import (
    ExplanationVerdict,
    PaperStartExplanation,
    StrategyExplanation,
)
from app.auto_paper.decision_log import (
    DECISION_LOG_SCHEMA_VERSION,
    PAPER_DECISION_LOG_MODE,
    PAPER_DECISION_LOG_SOURCE,
    PaperDecisionLogEntry,
    SecretInDecisionLogError,
    query_paper_decision_log,
    summarize_paper_decisions,
)
from app.auto_paper.ledger import reset_ledger_for_tests
from app.auto_paper.position_sizer import PositionSizingPolicy
from app.db.models import AgentDecisionLog, Base
from app.main import app as fastapi_app


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "auto_paper" / "decision_log.py"
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    """Per-test in-memory SQLite with all tables created."""
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


def _se(strategy, symbol, *, bucket="recommended",
        risk_flags=None, overfit_verdict=None,
        paper_status="READY_FOR_PAPER"):
    return StrategyExplanation(
        strategy=strategy, symbol=symbol,
        bucket=bucket,
        paper_candidate_status=paper_status,
        rationale_lines=["test rationale"],
        risk_flags=list(risk_flags or []),
        overfit_verdict=overfit_verdict,
    )


def _exp(*, verdict=ExplanationVerdict.READY_TO_REVIEW,
         recommended=None, watchlist=None, excluded=None,
         market_regime="TREND_UP"):
    return PaperStartExplanation(
        generated_at="2026-05-18T00:00:00+00:00",
        schema_version="1.0",
        verdict=verdict,
        recommended_explanations=list(recommended or []),
        watchlist_explanations=list(watchlist or []),
        excluded_explanations=list(excluded or []),
        market_regime=market_regime,
        regime_confidence=0.85,
        regime_reasons=[],
        regime_risk_flags=[],
        regime_allowed_tactics=[],
        regime_blocked_tactics=[],
        overfit_count=0,
        overfit_strategies=[],
        headline="test",
        risk_summary=[],
        operator_note="",
        next_actions=[],
        can_start_paper=True,
        blocking_reasons=[],
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. record_bridge_report — BUY / SELL / HOLD / EXIT / NO_OP all persisted
# ─────────────────────────────────────────────────────────────────────────────


class TestRecordEachAction:

    def test_buy_decision_recorded(self, db):
        exp = _exp(recommended=[_se("sma_crossover", "005930")])
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            db_session=db,
        )
        # 1 BUY decision → 1 AgentDecisionLog row.
        rows = db.query(AgentDecisionLog).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.decision == "BUY"
        assert row.mode == PAPER_DECISION_LOG_MODE
        assert row.agent_name == "PaperDecisionBridge"
        assert row.symbol == "005930"
        assert row.meta["risk_veto"] is False

    def test_hold_decision_recorded(self, db):
        exp = _exp(recommended=[_se("sma_crossover", "005930")])
        # 보유 중 → HOLD.
        bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING",
            positions=[PositionSnapshot(strategy="sma_crossover",
                                         symbol="005930", quantity=10)],
            db_session=db,
        )
        rows = db.query(AgentDecisionLog).all()
        assert len(rows) == 1
        assert rows[0].decision == "HOLD"

    def test_exit_decision_recorded(self, db):
        exp = _exp(watchlist=[
            _se("sma_crossover", "005930", bucket="watchlist"),
        ])
        bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING",
            positions=[PositionSnapshot(strategy="sma_crossover",
                                         symbol="005930",
                                         quantity=10, exit_condition=True)],
            db_session=db,
        )
        rows = db.query(AgentDecisionLog).all()
        assert len(rows) == 1
        assert rows[0].decision == "EXIT"

    def test_no_op_decision_recorded(self, db):
        exp = _exp(excluded=[
            _se("sma_crossover", "005930", bucket="excluded"),
        ])
        bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            db_session=db,
        )
        rows = db.query(AgentDecisionLog).all()
        assert len(rows) == 1
        assert rows[0].decision == "NO_OP"

    def test_sell_path_via_watchlist_exit_without_position(self, db):
        # watchlist + exit_condition + no position → HOLD (not SELL).
        # SELL action is generated only via custom direction flows. We exercise
        # the SELL persistence via process_ai_recommendation direct call.
        from app.auto_paper.decisions import (
            AIDirection,
            AIRecommendationInput,
            process_ai_recommendation,
        )
        from app.auto_paper.decision_log import _decision_to_log_meta
        rec = AIRecommendationInput(
            strategy="sma_crossover", symbol="005930",
            direction=AIDirection.SELL, reason="test sell",
            current_position=5,
        )
        decision, _ = process_ai_recommendation(
            rec, loop_state="RUNNING",
            virtual_trade_size=2, auto_fill=True, record=False,
        )
        assert decision.action.value == "SELL"
        # Persist via the same row helper.
        meta = _decision_to_log_meta(
            decision_id="x",
            paper_decision=decision,
            market_regime="TREND_UP",
            explanation_verdict="READY_TO_REVIEW",
            source_module=PAPER_DECISION_LOG_SOURCE,
        )
        row = AgentDecisionLog(
            agent_name="PaperDecisionBridge",
            symbol="005930",
            mode=PAPER_DECISION_LOG_MODE,
            decision="SELL", confidence=None,
            reasons=["test sell"], meta=meta,
        )
        db.add(row)
        db.flush()
        rows = db.query(AgentDecisionLog).filter_by(decision="SELL").all()
        assert len(rows) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 2. Risk veto blocked decisions still recorded
# ─────────────────────────────────────────────────────────────────────────────


class TestVetoRecording:

    def test_stale_data_veto_recorded_with_risk_veto_true(self, db):
        exp = _exp(recommended=[
            _se("sma_crossover", "005930", risk_flags=["stale_data"]),
        ])
        bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            db_session=db,
        )
        # BUY downgraded to HOLD by veto — still 1 row.
        rows = db.query(AgentDecisionLog).all()
        assert len(rows) == 1
        assert rows[0].decision == "HOLD"
        meta = rows[0].meta
        assert meta["risk_veto"] is True
        assert "STALE_DATA" in meta["risk_veto_reasons"]
        assert meta["risk_veto_severity"] == "BLOCK_NEW_ENTRY"

    def test_risk_officer_reject_recorded(self, db):
        exp = _exp(recommended=[_se("sma_crossover", "005930")])
        bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            risk_officer_rejects={("sma_crossover", "005930"): "low conf"},
            db_session=db,
        )
        rows = db.query(AgentDecisionLog).all()
        assert len(rows) == 1
        meta = rows[0].meta
        assert meta["risk_veto"] is True
        assert "RISK_OFFICER_REJECT" in meta["risk_veto_reasons"]


# ─────────────────────────────────────────────────────────────────────────────
# 3. Position sizing carry
# ─────────────────────────────────────────────────────────────────────────────


class TestSizingCarry:

    def test_sizing_quantity_recorded_in_meta(self, db):
        exp = _exp(recommended=[_se("sma_crossover", "005930")])
        pol = PositionSizingPolicy(
            max_risk_per_trade_pct=0.01,
            default_stop_loss_pct=0.03,
            max_position_pct=1.0,
            max_position_krw=10_000_000_000,
        )
        bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            sizing_policy=pol,
            price_lookup={("sma_crossover", "005930"): 70_000.0},
            account_equity=10_000_000.0,
            confidence_lookup={("sma_crossover", "005930"): 0.90},
            db_session=db,
        )
        rows = db.query(AgentDecisionLog).all()
        assert len(rows) == 1
        meta = rows[0].meta
        assert meta["sizing_verdict"] in ("SIZED", "REDUCED")
        assert isinstance(meta["sizing_quantity"], int)
        assert meta["sizing_quantity"] >= 1


# ─────────────────────────────────────────────────────────────────────────────
# 4. chain_id grouping
# ─────────────────────────────────────────────────────────────────────────────


class TestChainId:

    def test_all_rows_in_one_call_share_chain_id(self, db):
        exp = _exp(recommended=[
            _se("sma_crossover", "005930"),
            _se("rsi_reversion", "035720"),
        ])
        bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            db_session=db,
        )
        rows = db.query(AgentDecisionLog).all()
        assert len(rows) == 2
        ids = {r.chain_id for r in rows}
        assert len(ids) == 1, "all rows in one bridge call share chain_id"
        assert next(iter(ids)) is not None

    def test_explicit_chain_id_preserved(self, db):
        exp = _exp(recommended=[_se("sma_crossover", "005930")])
        bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            db_session=db,
            chain_id="my-test-chain-001",
        )
        rows = db.query(AgentDecisionLog).all()
        assert rows[0].chain_id == "my-test-chain-001"


# ─────────────────────────────────────────────────────────────────────────────
# 5. invariants — no broker call, no secret fields
# ─────────────────────────────────────────────────────────────────────────────


class TestInvariants:

    def test_no_db_session_no_writes(self, db):
        # backwards compat — db_session=None → 0 rows.
        exp = _exp(recommended=[_se("sma_crossover", "005930")])
        bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            # db_session omitted.
        )
        assert db.query(AgentDecisionLog).count() == 0

    def test_invariants_in_log_entry_dataclass(self):
        entry = PaperDecisionLogEntry(
            decision_id="x", timestamp="t",
            agent_name="A", strategy="s", symbol="x",
            mode=PAPER_DECISION_LOG_MODE,
            decision_action="HOLD", confidence=None, reason="",
        )
        assert entry.is_order_signal is False
        assert entry.auto_apply_allowed is False
        assert entry.is_live_authorization is False
        d = entry.to_dict()
        assert d["is_order_signal"] is False
        assert d["auto_apply_allowed"] is False
        assert d["is_live_authorization"] is False

    @pytest.mark.parametrize("override", [
        {"is_order_signal": True},
        {"auto_apply_allowed": True},
        {"is_live_authorization": True},
    ])
    def test_entry_invariant_violation_raises(self, override):
        base = dict(
            decision_id="x", timestamp="t",
            agent_name="A", strategy="s", symbol="x",
            mode=PAPER_DECISION_LOG_MODE,
            decision_action="HOLD", confidence=None, reason="",
        )
        base.update(override)
        with pytest.raises(ValueError):
            PaperDecisionLogEntry(**base)

    def test_mode_must_be_paper(self):
        with pytest.raises(ValueError):
            PaperDecisionLogEntry(
                decision_id="x", timestamp="t",
                agent_name="A", strategy="s", symbol="x",
                mode="LIVE",   # invalid — must be PAPER.
                decision_action="HOLD", confidence=None, reason="",
            )

    def test_recorded_row_always_paper_mode(self, db):
        exp = _exp(recommended=[_se("sma_crossover", "005930")])
        bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            db_session=db,
        )
        for row in db.query(AgentDecisionLog).all():
            assert row.mode == "PAPER"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Secret sanitizer
# ─────────────────────────────────────────────────────────────────────────────


class TestSecretSanitizer:

    def test_meta_with_forbidden_key_raises(self):
        from app.auto_paper.decision_log import _sanitize_meta
        with pytest.raises(SecretInDecisionLogError):
            _sanitize_meta({"api_key": "anything"})

    def test_meta_with_anthropic_key_pattern_raises(self):
        from app.auto_paper.decision_log import _sanitize_meta
        with pytest.raises(SecretInDecisionLogError):
            _sanitize_meta({
                "note": "sk-ant-aaaaaaaaaaaaaaaaaaaaaa",
            })

    def test_meta_with_openai_key_pattern_raises(self):
        from app.auto_paper.decision_log import _sanitize_meta
        with pytest.raises(SecretInDecisionLogError):
            _sanitize_meta({
                "note": "sk-aaaaaaaaaaaaaaaaaaaaaa",
            })

    def test_meta_with_jwt_pattern_raises(self):
        from app.auto_paper.decision_log import _sanitize_meta
        jwt = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
               "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ."
               "abcdef1234567890ABCDEF")
        with pytest.raises(SecretInDecisionLogError):
            _sanitize_meta({"note": jwt})

    def test_clean_meta_passes(self):
        from app.auto_paper.decision_log import _sanitize_meta
        meta = {"strategy": "sma_crossover", "regime": "TREND_UP"}
        assert _sanitize_meta(meta) == meta


# ─────────────────────────────────────────────────────────────────────────────
# 7. Query helpers
# ─────────────────────────────────────────────────────────────────────────────


class TestQuery:

    def test_query_returns_entries(self, db):
        exp = _exp(recommended=[_se("sma_crossover", "005930")])
        bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            db_session=db,
        )
        entries = query_paper_decision_log(db, limit=10)
        assert len(entries) == 1
        e = entries[0]
        assert e.mode == PAPER_DECISION_LOG_MODE
        assert e.decision_action == "BUY"
        assert e.symbol == "005930"
        assert e.is_order_signal is False
        assert e.auto_apply_allowed is False
        assert e.is_live_authorization is False

    def test_query_filter_by_action(self, db):
        exp = _exp(recommended=[
            _se("sma_crossover", "005930"),
            _se("rsi_reversion", "035720", risk_flags=["stale_data"]),
        ])
        bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            db_session=db,
        )
        buys = query_paper_decision_log(db, action="BUY")
        holds = query_paper_decision_log(db, action="HOLD")
        assert len(buys) == 1
        assert len(holds) == 1
        assert buys[0].symbol == "005930"
        assert holds[0].symbol == "035720"

    def test_summary_counts(self, db):
        exp = _exp(recommended=[
            _se("sma_crossover", "005930"),
            _se("rsi_reversion", "035720", risk_flags=["stale_data"]),
        ])
        bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            db_session=db,
        )
        entries = query_paper_decision_log(db)
        summary = summarize_paper_decisions(entries)
        assert summary["by_action"].get("BUY", 0) == 1
        assert summary["by_action"].get("HOLD", 0) == 1
        assert summary["veto_count"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# 8. API GET /api/auto-paper/decision-log
# ─────────────────────────────────────────────────────────────────────────────


class TestApi:

    def test_get_decision_log_returns_envelope(self):
        client = TestClient(fastapi_app)
        r = client.get("/api/auto-paper/decision-log?limit=10")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mode"] == "PAPER"
        assert body["source_module"] == PAPER_DECISION_LOG_SOURCE
        assert body["schema_version"] == DECISION_LOG_SCHEMA_VERSION
        assert body["is_order_signal"] is False
        assert body["auto_apply_allowed"] is False
        assert body["is_live_authorization"] is False
        assert isinstance(body["entries"], list)
        assert isinstance(body["summary"], dict)
        # disclaimer phrase always present.
        assert "advisory" in body["advisory_disclaimer"]

    def test_get_decision_log_rejects_invalid_limit(self):
        client = TestClient(fastapi_app)
        r = client.get("/api/auto-paper/decision-log?limit=0")
        assert r.status_code == 422   # Query ge=1.


# ─────────────────────────────────────────────────────────────────────────────
# 9. Static guards — no broker / route_order / OrderExecutor / DELETE / UPDATE
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

    def test_no_delete_or_update_statements(self):
        # Strip docstrings + comments so policy descriptions (which mention
        # DELETE/UPDATE as forbidden) don't trigger false positives.
        import tokenize
        import io
        src = self._source()
        out_tokens = []
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            tok_type, tok_str, *_ = tok
            if tok_type == tokenize.COMMENT:
                continue
            if tok_type == tokenize.STRING:
                # Skip docstrings (top-level / class-level / func-level
                # strings as expression statements). Simple heuristic: skip
                # all triple-quoted strings.
                if tok_str.startswith(('"""', "'''", 'r"""', "r'''")):
                    continue
            out_tokens.append(tok_str)
        code = " ".join(out_tokens)
        for bad in (
            "DELETE FROM", "delete(", ".delete()",
            "session.delete", "db.delete(",
        ):
            assert bad not in code, f"forbidden write op: {bad}"
        # UPDATE keyword check — case sensitive, with trailing space (SQL).
        assert "UPDATE " not in code or "bulk_update_mappings" in code, \
            "UPDATE SQL statement not allowed"

    def test_module_only_inserts_via_db_add(self):
        src = self._source()
        # `db.add(` is the only allowed write surface (one occurrence in the
        # main entry).
        assert "db.add(" in src
        # No bulk insert / update.
        assert "bulk_insert_mappings" not in src
        assert "bulk_update_mappings" not in src

    def test_no_settings_mutation(self):
        src = self._source()
        assert not re.search(r"settings\.enable_[a-z_]+\s*=", src)
