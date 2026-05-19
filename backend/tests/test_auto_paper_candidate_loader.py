"""#PaperCandidateWire: candidate registry + provider + tick integration.

Covers:
* `readiness_state()`: NO_CANDIDATE / WAITING_APPROVAL / CANDIDATE_READY.
* `approve()` rejects HIGH_RISK / BLOCK / OVERFIT_RISK / STRESS_FAILED.
* `reject()` blocks subsequent approval.
* `active_candidate()` returns the lowest-rank APPROVED candidate.
* `build_candidate_provider()` returns None when no APPROVED.
* `consume_agent_recommendations(provider=...)` produces a PaperDecision
  + ledger row + AgentDecisionLog row when an APPROVED candidate exists.
* `AutoPaperLoop.tick()` produces zero decisions when no candidate is
  approved (even if loop is RUNNING).
* No broker / OrderExecutor / route_order calls.
"""

from __future__ import annotations

import ast
import re
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.analytics.final_paper_candidates import (
    CandidateInput,
    select_paper_candidates,
)
from app.auto_paper.agent_consumer import consume_agent_recommendations
from app.auto_paper.candidate_provider import build_candidate_provider
from app.auto_paper.candidate_registry import (
    ApprovalBlockedError,
    ApprovalStatus,
    CandidateNotFoundError,
    CandidateRegistry,
    ReadinessState,
    get_candidate_registry,
    reset_candidate_registry_for_tests,
)
from app.auto_paper.ledger import reset_ledger_for_tests
from app.auto_paper.loop import AutoPaperLoop, AutoPaperState
from app.brokers.kis import KisBrokerAdapter
from app.db.models import AgentDecisionLog, Base


_REGISTRY_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "auto_paper" / "candidate_registry.py"
)
_PROVIDER_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "auto_paper" / "candidate_provider.py"
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
def _isolated_state():
    reset_candidate_registry_for_tests()
    reset_ledger_for_tests()
    yield
    reset_candidate_registry_for_tests()
    reset_ledger_for_tests()


@pytest.fixture
def kis_spy(monkeypatch):
    spy = MagicMock(side_effect=AssertionError(
        "KisBrokerAdapter.place_order must NOT be called by candidate wiring"
    ))
    monkeypatch.setattr(KisBrokerAdapter, "place_order", spy)
    cspy = MagicMock(side_effect=AssertionError(
        "KisBrokerAdapter.cancel_order must NOT be called by candidate wiring"
    ))
    monkeypatch.setattr(KisBrokerAdapter, "cancel_order", cspy)
    return spy, cspy


def _passing_input(name="MOMENTUM", symbol="005930", **over):
    base = dict(
        name=name,
        included_tactics=("MOMENTUM",),
        included_strategies=("sma_crossover",),
        symbol=symbol,
        primary_regime="TREND_UP",
        trade_count=20,
        expectancy=200.0,
        profit_factor=1.5,
        max_drawdown=0.12,
        win_rate=0.55,
        loss_streak=2,
        total_return=4000.0,
        paper_candidate_status="READY_FOR_PAPER",
        walk_forward_verdict="HEALTHY",
        stress_verdict="PASS",
        combo_verdict="PASS",
        regime_combo_verdict="PASS",
        combo_risk_verdict="PASS",
        confirmation_score=3,
        correlation_score=0.3,
        concentration_score=0.4,
    )
    base.update(over)
    return CandidateInput(**base)


def _load_two_pending() -> CandidateRegistry:
    reg = get_candidate_registry()
    report = select_paper_candidates(inputs=[
        _passing_input(name="cand_a", symbol="005930"),
        _passing_input(name="cand_b", symbol="035720"),
    ])
    reg.load_candidates(report)
    return reg


# ─────────────────────────────────────────────────────────────────────────────
# 1. Readiness state matrix
# ─────────────────────────────────────────────────────────────────────────────


class TestReadinessState:

    def test_no_candidate_when_registry_empty(self):
        reg = get_candidate_registry()
        assert reg.readiness_state() == ReadinessState.NO_CANDIDATE
        assert reg.active_candidate() is None

    def test_no_candidate_when_report_empty(self):
        reg = get_candidate_registry()
        report = select_paper_candidates(inputs=[])
        reg.load_candidates(report)
        assert reg.readiness_state() == ReadinessState.NO_CANDIDATE

    def test_waiting_approval_when_only_pending(self):
        reg = _load_two_pending()
        assert reg.readiness_state() == ReadinessState.WAITING_APPROVAL
        assert reg.active_candidate() is None

    def test_candidate_ready_after_approve(self):
        reg = _load_two_pending()
        first = reg.list_candidates()[0]
        reg.approve(first.candidate_id, "op")
        assert reg.readiness_state() == ReadinessState.CANDIDATE_READY
        assert reg.active_candidate() is not None
        assert reg.active_candidate().candidate_id == first.candidate_id

    def test_no_candidate_after_all_rejected(self):
        reg = _load_two_pending()
        for m in reg.list_candidates():
            reg.reject(m.candidate_id, "op")
        # 모두 REJECTED — 사용 가능한 후보 0.
        assert reg.readiness_state() == ReadinessState.NO_CANDIDATE
        assert reg.active_candidate() is None


# ─────────────────────────────────────────────────────────────────────────────
# 2. Approve / reject policy
# ─────────────────────────────────────────────────────────────────────────────


class TestApproveReject:

    def test_approve_pending_succeeds(self):
        reg = _load_two_pending()
        cid = reg.list_candidates()[0].candidate_id
        m = reg.approve(cid, "op-1", note="reviewed")
        assert m.status == ApprovalStatus.APPROVED
        assert m.approved_by == "op-1"
        assert m.approved_at
        assert any("approve" in n for n in m.decision_notes)

    def test_approve_unknown_id_raises(self):
        reg = _load_two_pending()
        with pytest.raises(CandidateNotFoundError):
            reg.approve("nope", "op")

    def test_approve_blocked_when_high_risk(self):
        # final_paper_candidates 의 selector 는 HIGH_RISK / BLOCK 후보를 *통과시키지*
        # 않으므로 시뮬레이션 위해 직접 ManagedCandidate 가공.
        from dataclasses import replace
        reg = _load_two_pending()
        cid = reg.list_candidates()[0].candidate_id
        m = reg.get(cid)
        # candidate 를 HIGH_RISK risk_flag 가 carry 된 사본으로 교체.
        new_c = replace(
            m.candidate,
            risk_flags=list(m.candidate.risk_flags) + ["HIGH_RISK"],
        )
        new_m = replace(m, candidate=new_c)
        reg._candidates[cid] = new_m   # type: ignore[attr-defined]

        with pytest.raises(ApprovalBlockedError):
            reg.approve(cid, "op")

    def test_approve_blocked_when_stress_failed_verdict(self):
        from dataclasses import replace
        reg = _load_two_pending()
        cid = reg.list_candidates()[0].candidate_id
        m = reg.get(cid)
        new_c = replace(m.candidate, stress_verdict="FAIL")
        reg._candidates[cid] = replace(m, candidate=new_c)   # type: ignore[attr-defined]
        with pytest.raises(ApprovalBlockedError):
            reg.approve(cid, "op")

    def test_reject_then_approve_raises(self):
        reg = _load_two_pending()
        cid = reg.list_candidates()[0].candidate_id
        reg.reject(cid, "op")
        with pytest.raises(RuntimeError):
            reg.approve(cid, "op")

    def test_approve_then_reject_raises(self):
        reg = _load_two_pending()
        cid = reg.list_candidates()[0].candidate_id
        reg.approve(cid, "op")
        with pytest.raises(RuntimeError):
            reg.reject(cid, "op")

    def test_approve_missing_approved_by_raises(self):
        reg = _load_two_pending()
        cid = reg.list_candidates()[0].candidate_id
        with pytest.raises(ValueError):
            reg.approve(cid, "")

    def test_load_preserves_approved_candidates(self):
        reg = _load_two_pending()
        cid = reg.list_candidates()[0].candidate_id
        reg.approve(cid, "op")
        # 같은 report 다시 load — APPROVED 후보 보존.
        report = select_paper_candidates(inputs=[
            _passing_input(name="cand_a", symbol="005930"),
            _passing_input(name="cand_b", symbol="035720"),
        ])
        reg.load_candidates(report)
        m = reg.get(cid)
        assert m.status == ApprovalStatus.APPROVED


# ─────────────────────────────────────────────────────────────────────────────
# 3. Provider — None when no approved candidate
# ─────────────────────────────────────────────────────────────────────────────


class TestProvider:

    def test_returns_none_when_registry_empty(self):
        provider = build_candidate_provider()
        assert provider(datetime.now()) is None

    def test_returns_none_when_only_pending(self):
        _load_two_pending()
        provider = build_candidate_provider()
        assert provider(datetime.now()) is None

    def test_returns_explanation_after_approve(self):
        reg = _load_two_pending()
        cid = reg.list_candidates()[0].candidate_id
        reg.approve(cid, "op")
        provider = build_candidate_provider()
        exp = provider(datetime.now())
        assert exp is not None
        assert len(exp.recommended_explanations) == 1
        assert exp.recommended_explanations[0].symbol == "005930"


# ─────────────────────────────────────────────────────────────────────────────
# 4. End-to-end — RUNNING tick with approved candidate
# ─────────────────────────────────────────────────────────────────────────────


class TestEndToEnd:

    def test_no_decisions_when_no_candidate(self, db, kis_spy):
        # registry empty → provider returns None → consumer NOT consumed.
        result = consume_agent_recommendations(
            loop_state="RUNNING",
            recommendation_provider=build_candidate_provider(),
            db_session=db,
        )
        assert result.consumed is False
        assert result.decision_count == 0
        assert db.query(AgentDecisionLog).count() == 0

    def test_no_decisions_when_only_pending(self, db, kis_spy):
        _load_two_pending()
        result = consume_agent_recommendations(
            loop_state="RUNNING",
            recommendation_provider=build_candidate_provider(),
            db_session=db,
        )
        assert result.consumed is False
        assert result.decision_count == 0
        assert db.query(AgentDecisionLog).count() == 0

    def test_decisions_after_approve(self, db, kis_spy):
        reg = _load_two_pending()
        cid = reg.list_candidates()[0].candidate_id
        reg.approve(cid, "op")
        result = consume_agent_recommendations(
            loop_state="RUNNING",
            recommendation_provider=build_candidate_provider(),
            db_session=db,
        )
        assert result.consumed is True
        assert result.decision_count == 1
        assert result.by_action.get("BUY", 0) == 1
        assert result.ledger_events == 1
        rows = db.query(AgentDecisionLog).all()
        assert len(rows) == 1
        assert rows[0].mode == "PAPER"
        assert rows[0].decision == "BUY"
        # broker 호출 0건.
        spy, cspy = kis_spy
        assert spy.call_count == 0
        assert cspy.call_count == 0

    def test_loop_tick_consumes_active_candidate(self, db, kis_spy, monkeypatch):
        from app.scheduler.market_clock import MarketPhase
        monkeypatch.setattr(
            "app.auto_paper.loop.current_market_phase",
            lambda now=None: MarketPhase.OPEN,
        )
        reg = _load_two_pending()
        cid = reg.list_candidates()[0].candidate_id
        reg.approve(cid, "op")

        def _runner(loop_state, now):
            return consume_agent_recommendations(
                loop_state=loop_state,
                recommendation_provider=build_candidate_provider(),
                db_session=db, now=now,
            )

        loop = AutoPaperLoop(agent_consumer_runner=_runner)
        loop._state = AutoPaperState.RUNNING
        status = loop.tick()
        assert status.last_consumed is True
        assert status.last_decision_count == 1
        assert status.last_decision_action == "BUY"
        assert status.candidate_readiness == "CANDIDATE_READY"
        assert status.has_active_candidate is True
        # broker 호출 0건.
        assert kis_spy[0].call_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# 5. Status snapshot reflects readiness
# ─────────────────────────────────────────────────────────────────────────────


class TestStatusSnapshot:

    def test_status_reports_no_candidate_initially(self):
        loop = AutoPaperLoop()
        status = loop.status()
        assert status.candidate_readiness == "NO_CANDIDATE"
        assert status.has_active_candidate is False

    def test_status_reports_waiting_when_pending(self):
        _load_two_pending()
        loop = AutoPaperLoop()
        status = loop.status()
        assert status.candidate_readiness == "WAITING_APPROVAL"
        assert status.has_active_candidate is False

    def test_status_reports_ready_after_approve(self):
        reg = _load_two_pending()
        cid = reg.list_candidates()[0].candidate_id
        reg.approve(cid, "op")
        loop = AutoPaperLoop()
        status = loop.status()
        assert status.candidate_readiness == "CANDIDATE_READY"
        assert status.has_active_candidate is True


# ─────────────────────────────────────────────────────────────────────────────
# 6. to_dict + invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestSerialization:

    def test_managed_candidate_invariants(self):
        reg = _load_two_pending()
        m = reg.list_candidates()[0]
        d = m.to_dict()
        assert d["is_order_signal"] is False
        assert d["auto_apply_allowed"] is False
        assert d["is_live_authorization"] is False
        assert d["status"] == "PENDING_APPROVAL"
        # 내부 candidate 도 requires_operator_approval=True 영구.
        assert d["candidate"]["requires_operator_approval"] is True

    def test_registry_to_dict(self):
        reg = _load_two_pending()
        d = reg.to_dict()
        assert d["total"] == 2
        assert d["pending"] == 2
        assert d["approved"] == 0
        assert d["readiness_state"] == "WAITING_APPROVAL"
        assert d["is_order_signal"] is False

    def test_managed_candidate_invariant_violation_raises(self):
        reg = _load_two_pending()
        m = reg.list_candidates()[0]
        from dataclasses import replace
        # is_order_signal=True 시도 → ValueError.
        with pytest.raises(ValueError):
            replace(m, is_order_signal=True)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Static guards
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

    @pytest.mark.parametrize("path", [_REGISTRY_MODULE_PATH, _PROVIDER_MODULE_PATH])
    def test_no_forbidden_imports(self, path):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for bad in _FORBIDDEN_IMPORT_SUBSTRINGS:
                        assert bad not in (alias.name or "")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for bad in _FORBIDDEN_IMPORT_SUBSTRINGS:
                    assert bad not in module

    @pytest.mark.parametrize("path", [_REGISTRY_MODULE_PATH, _PROVIDER_MODULE_PATH])
    def test_no_forbidden_calls(self, path):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee = ast.unparse(node.func)
                for bad in _FORBIDDEN_CALL_SUBSTRINGS:
                    assert bad not in callee

    @pytest.mark.parametrize("path", [_REGISTRY_MODULE_PATH, _PROVIDER_MODULE_PATH])
    def test_no_db_write(self, path):
        src = path.read_text(encoding="utf-8")
        for bad in ("session.commit", "session.add", "session.delete",
                    "db.commit(", "db.add(", "db.delete("):
            assert bad not in src

    @pytest.mark.parametrize("path", [_REGISTRY_MODULE_PATH, _PROVIDER_MODULE_PATH])
    def test_no_settings_mutation(self, path):
        src = path.read_text(encoding="utf-8")
        assert not re.search(r"settings\.enable_[a-z_]+\s*=", src)


# ─────────────────────────────────────────────────────────────────────────────
# 8. API endpoints — route registration check
# ─────────────────────────────────────────────────────────────────────────────


class TestApiRoutesRegistered:
    """Verifies the 4 new endpoints are registered on the FastAPI app.

    The full request/response cycle is exercised at the registry level above —
    each endpoint is a thin wrapper over CandidateRegistry methods, so the
    same surface is already covered by TestApproveReject / TestReadinessState
    / TestProvider / TestEndToEnd.
    """

    def test_candidate_endpoints_in_app_routes(self):
        from app.main import app
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/api/auto-paper/candidates" in paths
        assert "/api/auto-paper/candidates/{candidate_id}/approve-paper" in paths
        assert "/api/auto-paper/candidates/{candidate_id}/reject" in paths
        assert "/api/auto-paper/active-candidate" in paths
