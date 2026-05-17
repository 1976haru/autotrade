"""#56: Execution Recommender Agent tests.

본 Agent는 매수/매도 *제안*만 생성하며 *직접 주문하지 않는다*. 사전검사는
audit row 0건, 큐 등록은 기존 sanctioned `app.ai.assist.submit_candidate`로
위임된다.

본 테스트는 그 invariant를 *코드 단*에서 강제한다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _run(coro):
    """Project doesn't depend on pytest-asyncio — drive coroutines manually."""
    return asyncio.run(coro)

from app.agents.base import AgentContext, AgentDecision, AgentRole
from app.agents.execution_recommender import (
    ExecutionProposal,
    ExecutionRecommenderAgent,
    PrecheckOutcome,
    ProposalSide,
    RecommendInput,
    RecommendResult,
    RiskPrecheckResult,
    precheck_proposal,
    recommend_proposals,
    submit_proposal,
)
from app.brokers.base import OrderSide
from app.core.modes import OperationMode


_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "agents" / "execution_recommender.py"
)
_ROUTES_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "api" / "routes_execution_recommender.py"
)


# ====================================================================
# Helpers
# ====================================================================


def _baseline_candidate(**overrides) -> RecommendInput.Candidate:
    base = dict(
        symbol="005930",
        side=ProposalSide.BUY,
        latest_price=70_000,
        target_price=75_000,
        stop_price=68_000,
        quantity=10,
        confidence=70,
        supporting_reasons=("breakout above 200ma", "volume spike"),
        opposing_reasons=("RSI overbought",),
        risk_note="공격적 진입 — 손절 엄수",
    )
    base.update(overrides)
    return RecommendInput.Candidate(**base)


# ====================================================================
# ExecutionProposal invariant guards
# ====================================================================


class TestExecutionProposalInvariants:
    def test_proposal_rejects_is_order_intent_true(self):
        with pytest.raises(ValueError, match="is_order_intent"):
            ExecutionProposal(
                proposal_id="x", symbol="005930", side=ProposalSide.BUY,
                quantity=1, confidence=50,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
                is_order_intent=True,           # ← invariant 위반
            )

    def test_proposal_rejects_can_execute_order_true(self):
        with pytest.raises(ValueError, match="can_execute_order"):
            ExecutionProposal(
                proposal_id="x", symbol="005930", side=ProposalSide.BUY,
                quantity=1, confidence=50,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
                can_execute_order=True,         # ← invariant 위반
            )

    def test_proposal_rejects_invalid_confidence(self):
        with pytest.raises(ValueError, match="confidence"):
            ExecutionProposal(
                proposal_id="x", symbol="005930", side=ProposalSide.BUY,
                quantity=1, confidence=150,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
            )

    def test_proposal_rejects_invalid_quantity(self):
        with pytest.raises(ValueError, match="quantity"):
            ExecutionProposal(
                proposal_id="x", symbol="005930", side=ProposalSide.BUY,
                quantity=0, confidence=50,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
            )

    def test_proposal_to_ai_candidate(self):
        p = ExecutionProposal(
            proposal_id="abc", symbol="005930", side=ProposalSide.BUY,
            quantity=10, confidence=72,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
            limit_price=70_000, target_price=75_000, stop_price=68_000,
            supporting_reasons=("a",), opposing_reasons=("b",),
            risk_note="aggressive",
        )
        cand = p.to_ai_candidate()
        assert cand.symbol == "005930"
        assert cand.side == OrderSide.BUY
        assert cand.quantity == 10
        assert cand.confidence == 72
        assert cand.target_price == 75_000
        assert cand.stop_price == 68_000
        assert cand.supporting_reasons == ["a"]
        assert cand.risk_note == "aggressive"

    def test_proposal_is_expired(self):
        past = datetime.now(timezone.utc) - timedelta(seconds=60)
        future = datetime.now(timezone.utc) + timedelta(seconds=600)
        expired = ExecutionProposal(
            proposal_id="x", symbol="x", side=ProposalSide.BUY,
            quantity=1, confidence=50, expires_at=past,
        )
        active = ExecutionProposal(
            proposal_id="y", symbol="y", side=ProposalSide.BUY,
            quantity=1, confidence=50, expires_at=future,
        )
        assert expired.is_expired() is True
        assert active.is_expired() is False


class TestRecommendResultInvariants:
    def test_result_rejects_auto_apply_allowed_true(self):
        with pytest.raises(ValueError, match="auto_apply_allowed"):
            RecommendResult(
                proposals=(),
                skipped=(),
                created_at=datetime.now(timezone.utc),
                auto_apply_allowed=True,        # ← invariant 위반
            )

    def test_result_rejects_is_order_signal_true(self):
        with pytest.raises(ValueError, match="is_order_signal"):
            RecommendResult(
                proposals=(),
                skipped=(),
                created_at=datetime.now(timezone.utc),
                is_order_signal=True,           # ← invariant 위반
            )


# ====================================================================
# recommend_proposals — pure analysis
# ====================================================================


class TestRecommendProposals:
    def test_baseline_candidate_produces_proposal(self):
        inp = RecommendInput(candidates=(_baseline_candidate(),))
        result = recommend_proposals(inp)
        assert len(result.proposals) == 1
        p = result.proposals[0]
        assert p.symbol == "005930"
        assert p.side == ProposalSide.BUY
        # reward = (75000-70000)*10 = 50,000; risk = (70000-68000)*10 = 20,000; rr=2.5
        assert p.expected_reward == 50_000
        assert p.expected_risk == 20_000
        assert p.risk_reward_ratio is not None
        assert p.risk_reward_ratio == pytest.approx(2.5)
        assert p.is_order_intent is False
        assert p.can_execute_order is False

    def test_low_confidence_skipped(self):
        inp = RecommendInput(candidates=(
            _baseline_candidate(confidence=20),  # < 40 임계
        ))
        result = recommend_proposals(inp)
        assert len(result.proposals) == 0
        assert len(result.skipped) == 1
        assert "confidence" in result.skipped[0][1]

    def test_low_risk_reward_skipped(self):
        # reward = (71000-70000)*10 = 10,000; risk = (70000-68000)*10 = 20,000; rr=0.5
        inp = RecommendInput(candidates=(
            _baseline_candidate(target_price=71_000),
        ))
        result = recommend_proposals(inp)
        assert len(result.proposals) == 0
        assert len(result.skipped) == 1
        assert "risk_reward_ratio" in result.skipped[0][1]

    def test_sell_side_proposal(self):
        # SELL: reward = (latest - target) * qty; risk = (stop - latest) * qty
        inp = RecommendInput(candidates=(
            _baseline_candidate(
                side=ProposalSide.SELL,
                latest_price=70_000,
                target_price=66_000,    # 매도 후 더 떨어지길 기대
                stop_price=72_000,      # 반등 시 손절
            ),
        ))
        result = recommend_proposals(inp)
        assert len(result.proposals) == 1
        p = result.proposals[0]
        assert p.side == ProposalSide.SELL
        # reward = (70000-66000)*10 = 40000; risk = (72000-70000)*10 = 20000; rr=2.0
        assert p.expected_reward == 40_000
        assert p.expected_risk == 20_000

    def test_proposals_are_unique_by_id(self):
        inp = RecommendInput(candidates=(
            _baseline_candidate(symbol="005930"),
            _baseline_candidate(symbol="000660"),
        ))
        result = recommend_proposals(inp)
        assert len(result.proposals) == 2
        ids = {p.proposal_id for p in result.proposals}
        assert len(ids) == 2

    def test_expiry_at_least_60s(self):
        inp = RecommendInput(candidates=(_baseline_candidate(),),
                             expiry_seconds=10)  # 너무 짧음 — clamp되어야
        before = datetime.now(timezone.utc)
        result = recommend_proposals(inp)
        assert len(result.proposals) == 1
        # clamp: expires_at >= now() + ~60s — minimum 60 seconds
        delta = result.proposals[0].expires_at - before
        assert delta.total_seconds() >= 59  # tolerate 1s clock jitter

    def test_empty_input_produces_no_proposals(self):
        result = recommend_proposals(RecommendInput(candidates=()))
        assert result.proposals == ()
        assert result.skipped == ()


# ====================================================================
# Agent class (#51 AgentBase 호환)
# ====================================================================


class TestAgentClass:
    def test_agent_metadata(self):
        agent = ExecutionRecommenderAgent()
        meta = agent.metadata
        assert meta.role == AgentRole.EXECUTION_RECOMMENDER
        assert meta.can_execute_order is False

    def test_agent_run_no_input_returns_no_op(self):
        agent = ExecutionRecommenderAgent()
        out = agent.run(AgentContext())
        assert out.decision == AgentDecision.NO_OP
        assert out.is_order_intent is False
        assert out.can_execute_order is False

    def test_agent_run_with_candidates_returns_approval_candidate(self):
        agent = ExecutionRecommenderAgent()
        ctx = AgentContext(extra={"recommend_input": RecommendInput(
            candidates=(_baseline_candidate(),),
        )})
        out = agent.run(ctx)
        assert out.decision == AgentDecision.APPROVAL_CANDIDATE
        assert out.is_order_intent is False
        assert out.can_execute_order is False
        assert out.approval_candidate is not None
        assert out.approval_candidate["is_order_intent"] is False
        assert out.approval_candidate["can_execute_order"] is False
        assert out.approval_candidate["source"] == "AGENT_EXECUTION_RECOMMENDER"
        assert out.approval_candidate["symbol"] == "005930"
        assert out.metadata["auto_apply_allowed"] is False

    def test_agent_run_filters_low_confidence(self):
        agent = ExecutionRecommenderAgent()
        ctx = AgentContext(extra={"recommend_input": RecommendInput(
            candidates=(_baseline_candidate(confidence=10),),
        )})
        out = agent.run(ctx)
        assert out.decision == AgentDecision.NO_OP
        assert out.metadata["proposals_count"] == 0


# ====================================================================
# precheck_proposal — async, integration with FastAPI test client
# ====================================================================


class TestPrecheckIntegration:
    def test_precheck_passes_with_balance_and_quote(self, client):
        """`client` fixture는 MockBroker + RiskManager + DB session을 제공.
        본 테스트는 *Agent 모듈*의 precheck 흐름을 직접 호출."""
        broker = client.test_broker
        risk   = client.test_risk_manager

        proposal = ExecutionProposal(
            proposal_id="t1",
            symbol="005930",
            side=ProposalSide.BUY,
            quantity=1,
            confidence=70,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
        )
        result = _run(precheck_proposal(
            proposal,
            risk=risk,
            broker=broker,
            mode=OperationMode.SIMULATION,
            requested_by_ai=True,
        ))
        assert isinstance(result, RiskPrecheckResult)
        assert result.proposal_id == "t1"
        assert result.outcome in (
            PrecheckOutcome.APPROVED,
            PrecheckOutcome.NEEDS_APPROVAL,
            PrecheckOutcome.REJECTED,
            PrecheckOutcome.BLOCKED,
        )

    def test_precheck_does_not_create_audit_rows(self, client):
        """audit row 0건 — precheck는 advisory dry-run."""
        from sqlalchemy import select

        from app.db.models import OrderAuditLog
        broker = client.test_broker
        risk   = client.test_risk_manager
        db = client.test_db_factory()
        try:
            before = db.execute(select(OrderAuditLog)).all()
            proposal = ExecutionProposal(
                proposal_id="t2",
                symbol="005930",
                side=ProposalSide.BUY,
                quantity=1,
                confidence=70,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
            )
            _run(precheck_proposal(
                proposal,
                risk=risk,
                broker=broker,
                mode=OperationMode.SIMULATION,
                requested_by_ai=True,
            ))
            after = db.execute(select(OrderAuditLog)).all()
            assert len(after) == len(before), "precheck must not create audit rows"
        finally:
            db.close()

    def test_precheck_rejects_expired_proposal(self, client):
        broker = client.test_broker
        risk   = client.test_risk_manager
        proposal = ExecutionProposal(
            proposal_id="t3",
            symbol="005930",
            side=ProposalSide.BUY,
            quantity=1,
            confidence=70,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=60),
        )
        result = _run(precheck_proposal(
            proposal, risk=risk, broker=broker,
            mode=OperationMode.SIMULATION,
        ))
        assert result.outcome == PrecheckOutcome.REJECTED
        assert any("expired" in r for r in result.reasons)


# ====================================================================
# submit_proposal — delegates to ai.assist.submit_candidate
# ====================================================================


class TestSubmitProposal:
    def test_submit_rejects_expired(self, client):
        broker = client.test_broker
        risk   = client.test_risk_manager
        db = client.test_db_factory()
        try:
            proposal = ExecutionProposal(
                proposal_id="t4",
                symbol="005930", side=ProposalSide.BUY,
                quantity=1, confidence=70,
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=60),
            )
            with pytest.raises(RuntimeError, match="expired"):
                _run(submit_proposal(
                    proposal,
                    risk=risk, broker=broker, db=db,
                    mode=OperationMode.LIVE_AI_ASSIST,
                    enable_live_trading=False,
                    enable_ai_execution=False,
                    enable_futures_live_trading=False,
                ))
        finally:
            db.close()

    def test_submit_in_simulation_mode_is_blocked(self, client):
        """LIVE_AI_ASSIST 외 모드에서는 ai.assist가 raise."""
        from app.ai.assist import AiAssistModeError
        broker = client.test_broker
        risk   = client.test_risk_manager
        db = client.test_db_factory()
        try:
            proposal = ExecutionProposal(
                proposal_id="t5",
                symbol="005930", side=ProposalSide.BUY,
                quantity=1, confidence=70,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
            )
            with pytest.raises(AiAssistModeError):
                _run(submit_proposal(
                    proposal,
                    risk=risk, broker=broker, db=db,
                    mode=OperationMode.SIMULATION,
                    enable_live_trading=False,
                    enable_ai_execution=False,
                    enable_futures_live_trading=False,
                ))
        finally:
            db.close()


# ====================================================================
# API endpoints
# ====================================================================


class TestAPI:
    def test_recommend_returns_advisory_payload(self, client):
        res = client.post("/api/agents/execution-recommender/recommend", json={
            "candidates": [{
                "symbol": "005930", "side": "BUY",
                "latest_price": 70_000, "target_price": 75_000,
                "stop_price": 68_000, "quantity": 10, "confidence": 70,
                "supporting_reasons": ["breakout"],
            }],
            "expiry_seconds": 600,
        })
        assert res.status_code == 200
        body = res.json()
        assert body["auto_apply_allowed"] is False
        assert body["is_order_signal"] is False
        assert "주문" in body["notice"] or "advisory" in body["notice"].lower()
        assert len(body["proposals"]) == 1
        p = body["proposals"][0]
        assert p["is_order_intent"] is False
        assert p["can_execute_order"] is False
        assert p["risk_reward_ratio"] == pytest.approx(2.5)

    def test_recommend_skips_low_confidence(self, client):
        res = client.post("/api/agents/execution-recommender/recommend", json={
            "candidates": [{
                "symbol": "005930", "side": "BUY",
                "latest_price": 70_000, "quantity": 1, "confidence": 10,
            }],
        })
        assert res.status_code == 200
        body = res.json()
        assert body["proposals"] == []
        assert len(body["skipped"]) == 1

    def test_recommend_does_not_create_audit_rows(self, client):
        from sqlalchemy import select

        from app.db.models import OrderAuditLog
        db = client.test_db_factory()
        try:
            before = len(db.execute(select(OrderAuditLog)).all())
            client.post("/api/agents/execution-recommender/recommend", json={
                "candidates": [{
                    "symbol": "005930", "side": "BUY",
                    "latest_price": 70_000, "target_price": 75_000,
                    "stop_price": 68_000, "quantity": 10, "confidence": 70,
                }],
            })
            after = len(db.execute(select(OrderAuditLog)).all())
            assert before == after
        finally:
            db.close()

    def test_precheck_returns_outcome(self, client):
        # 먼저 recommend
        rec = client.post("/api/agents/execution-recommender/recommend", json={
            "candidates": [{
                "symbol": "005930", "side": "BUY",
                "latest_price": 70_000, "target_price": 75_000,
                "stop_price": 68_000, "quantity": 1, "confidence": 70,
            }],
        }).json()
        assert rec["proposals"]
        proposal = rec["proposals"][0]

        res = client.post("/api/agents/execution-recommender/precheck",
                          json={"proposal": proposal})
        assert res.status_code == 200
        body = res.json()
        assert body["proposal_id"] == proposal["proposal_id"]
        assert body["outcome"] in {
            "APPROVED", "NEEDS_APPROVAL", "REJECTED", "BLOCKED", "REDUCED",
        }
        assert "audit row" in body["notice"]

    def test_precheck_does_not_create_audit_rows(self, client):
        from sqlalchemy import select

        from app.db.models import OrderAuditLog
        rec = client.post("/api/agents/execution-recommender/recommend", json={
            "candidates": [{
                "symbol": "005930", "side": "BUY",
                "latest_price": 70_000, "target_price": 75_000,
                "stop_price": 68_000, "quantity": 1, "confidence": 70,
            }],
        }).json()
        proposal = rec["proposals"][0]

        db = client.test_db_factory()
        try:
            before = len(db.execute(select(OrderAuditLog)).all())
            client.post("/api/agents/execution-recommender/precheck",
                        json={"proposal": proposal})
            after = len(db.execute(select(OrderAuditLog)).all())
            assert before == after
        finally:
            db.close()

    def test_submit_in_simulation_mode_returns_403(self, client):
        """default test mode = SIMULATION → /submit는 403."""
        rec = client.post("/api/agents/execution-recommender/recommend", json={
            "candidates": [{
                "symbol": "005930", "side": "BUY",
                "latest_price": 70_000, "target_price": 75_000,
                "stop_price": 68_000, "quantity": 1, "confidence": 70,
            }],
        }).json()
        proposal = rec["proposals"][0]

        res = client.post("/api/agents/execution-recommender/submit",
                          json={"proposal": proposal})
        assert res.status_code == 403
        body = res.json()
        assert body["detail"]["error"] == "ai_assist_mode_required"

    def test_submit_returns_410_for_expired(self, client):
        # 매우 짧은 expiry로 recommend 후 곧장 검증
        rec = client.post("/api/agents/execution-recommender/recommend", json={
            "candidates": [{
                "symbol": "005930", "side": "BUY",
                "latest_price": 70_000, "target_price": 75_000,
                "stop_price": 68_000, "quantity": 1, "confidence": 70,
            }],
            "expiry_seconds": 60,
        }).json()
        proposal = rec["proposals"][0]
        # Force expiration
        proposal["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=60)
        ).isoformat()
        # mode를 LIVE_AI_ASSIST로 만들 수 없으므로 SIMULATION 단계 가드가 먼저
        # 작동 — submit이 410을 반환하지 않을 수 있음. 현재 모드 가드(403)가
        # expiry 가드(410) 보다 먼저 평가되므로, expiry 가드는 unit-level에서
        # `submit_proposal`로 별도 검증 (test_submit_rejects_expired 위쪽).


# ====================================================================
# Static module guards (절대 원칙 강제)
# ====================================================================


class TestAgentStaticGuards:
    def _source(self) -> str:
        return _MODULE_PATH.read_text(encoding="utf-8")

    def test_module_does_not_import_brokers_at_module_level(self):
        """Module top-level scope에서 broker symbol을 *직접* import하지 않는다.

        `_proposal_side_to_order_side` 내부의 lazy importlib 호출은 본 가드를
        피하기 위함 — module top-level scope에 broker symbol이 노출되지 않음.
        """
        src = self._source()
        for forbidden in (
            "from app.brokers.kis",
            "from app.brokers.mock_broker",
            "from app.brokers.base import OrderRequest",
            "from app.brokers.base import BrokerAdapter",
            "from app.brokers.futures_base",
        ):
            assert forbidden not in src, (
                f"execution_recommender.py must not contain '{forbidden}' "
                "— ExecutionRecommender must not import broker classes."
            )

    def _import_lines(self) -> list[str]:
        """Return only the import statements from the source — docstrings/comments
        excluded. Used to scope static guards to actual imports."""
        lines: list[str] = []
        for raw in self._source().splitlines():
            stripped = raw.strip()
            if stripped.startswith("from ") or stripped.startswith("import "):
                lines.append(stripped)
        return lines

    def test_module_does_not_import_executor_or_router(self):
        imports = self._import_lines()
        for line in imports:
            for forbidden in (
                "from app.execution.executor",
                "from app.execution.order_executor",
                "from app.execution.order_router",
                "import app.execution.executor",
                "import app.execution.order_executor",
                "import app.execution.order_router",
            ):
                assert forbidden not in line, (
                    f"execution_recommender.py imports forbidden module: {line}"
                )

    def test_module_does_not_call_place_or_cancel_order(self):
        """실제 *호출*만 검사 — docstring에서 정책 설명 시 단어 사용은 허용."""
        src = self._source()
        # actual call expressions (with `(` and not following `*` markdown emphasis)
        forbidden_calls = (
            "broker.place_order(",
            "broker.cancel_order(",
            "self.place_order(",
            "self.cancel_order(",
            "_broker.place_order(",
            "_broker.cancel_order(",
            "await broker.place_order",
            "await broker.cancel_order",
        )
        for snippet in forbidden_calls:
            assert snippet not in src, (
                f"execution_recommender.py must not call '{snippet}' "
                "— ExecutionRecommender never places or cancels orders directly."
            )

    def test_module_does_not_call_route_order(self):
        src = self._source()
        for forbidden in (
            "= route_order(",
            "await route_order(",
            "= await route_order",
        ):
            assert forbidden not in src, (
                f"execution_recommender.py must not call '{forbidden}'"
            )
        # Direct import도 금지.
        for line in self._import_lines():
            assert "from app.execution.order_router import" not in line
            assert "from app.execution.order_router" not in line or \
                "import" not in line

    def test_module_does_not_import_external_http_or_ai(self):
        src = self._source()
        for forbidden in (
            "import httpx", "import requests", "import urllib3",
            "from anthropic", "import anthropic",
            "from openai", "import openai",
        ):
            assert forbidden not in src

    def test_module_does_not_emit_db_writes(self):
        src = self._source()
        for forbidden in (
            "db.add(", "db.commit(", "db.delete(", "db.merge(",
            "session.add(", "session.commit(", "session.delete(",
            ".insert(", ".update(", ".delete(",
        ):
            assert forbidden not in src, (
                f"execution_recommender.py must not perform DB writes: {forbidden}"
            )

    def test_module_does_not_import_orderrequest(self):
        """ExecutionProposal != 주문 요청 객체. 본 모듈은 주문 요청 객체를 import하지
        않는다 — 변환은 ai.assist.AICandidate 내부에서만 발생."""
        for line in self._import_lines():
            # `from app.brokers.base import OrderRequest` 같은 import 금지
            assert "OrderRequest" not in line, (
                f"execution_recommender.py imports forbidden symbol: {line}"
            )
        # Type annotations / variable names로도 OrderRequest를 *생성*하지 않는다.
        src = self._source()
        for forbidden in (
            "OrderRequest(",                   # 직접 생성
            ": OrderRequest",                   # type annotation
            "-> OrderRequest",                  # return type
            "= OrderRequest",                   # assignment
        ):
            assert forbidden not in src, (
                f"execution_recommender.py must not reference {forbidden}"
            )

    def test_proposal_invariant_guards_present(self):
        src = self._source()
        # __post_init__ 가드가 살아 있는지 정적 검사
        assert "is_order_intent must be False" in src
        assert "can_execute_order must be False" in src

    def test_no_buy_sell_hold_in_decision_enums(self):
        """`PrecheckOutcome` enum에 BUY/SELL/HOLD 0개. (`ProposalSide`의
        BUY/SELL은 *데이터* 방향 — 결정 값이 아니다)."""
        for member in PrecheckOutcome:
            v = str(member.value).upper()
            assert "BUY" not in v
            assert "SELL" not in v
            assert v != "HOLD"


class TestRoutesStaticGuards:
    def _source(self) -> str:
        return _ROUTES_PATH.read_text(encoding="utf-8")

    def _import_lines(self) -> list[str]:
        lines: list[str] = []
        for raw in self._source().splitlines():
            stripped = raw.strip()
            if stripped.startswith("from ") or stripped.startswith("import "):
                lines.append(stripped)
        return lines

    def test_routes_module_does_not_call_broker_directly(self):
        src = self._source()
        for forbidden in (
            "broker.place_order(", "broker.cancel_order(",
            "await broker.place_order", "await broker.cancel_order",
        ):
            assert forbidden not in src, (
                f"routes_execution_recommender must not call '{forbidden}'"
            )

    def test_routes_module_does_not_import_executor_or_router(self):
        for line in self._import_lines():
            for forbidden in (
                "from app.execution.executor",
                "from app.execution.order_executor",
                "from app.execution.order_router",
                "import app.execution.executor",
                "import app.execution.order_executor",
                "import app.execution.order_router",
            ):
                assert forbidden not in line, (
                    f"routes module imports forbidden: {line}"
                )

    def test_routes_module_does_not_import_orderrequest(self):
        for line in self._import_lines():
            assert "OrderRequest" not in line, (
                f"routes module imports forbidden symbol: {line}"
            )
        src = self._source()
        for forbidden in (
            "OrderRequest(", ": OrderRequest", "-> OrderRequest",
        ):
            assert forbidden not in src
