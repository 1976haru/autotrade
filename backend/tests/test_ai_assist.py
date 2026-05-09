"""#44: AI Assisted Trading flow integration tests.

Coverage:
- AICandidate → OrderRequest 변환 (requested_by_ai=True + ai_decision_meta carry)
- submit_candidate가 AI Permission Gate를 먼저 검사 (FULL_STOP에서 raise)
- LIVE_AI_ASSIST + 정상 후보 → RiskManager NEEDS_APPROVAL → PendingApproval 등록
- LIVE_AI_ASSIST + 긴급정지 → RiskManager REJECTED → approval 등록 안 됨
- broker.place_order는 *어떤 분기에서도* 호출되지 않음 (AsyncMock spy)
- LIVE_AI_ASSIST 외 모드에서 submit → AiAssistModeError
- /api/ai/assist/submit, /api/ai/assist/pending, /api/ai/assist/summary
- 정적 가드: assist 모듈 + routes_ai_assist 모듈은 broker import 0건

Defense in depth — AI는 *추천*만, 사람 승인 후에만 broker로. 본 테스트가 그
invariant를 lock한다.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select


def _run(coro):
    """Project doesn't depend on pytest-asyncio — drive coroutines manually."""
    return asyncio.run(coro)

from app.ai.assist import (
    AI_ASSIST_TRADE_REASON,
    AICandidate,
    AiAssistModeError,
    AiAssistPermissionDeniedError,
    is_ai_assist_audit,
    submit_candidate,
)
from app.brokers.base import OrderSide, OrderType
from app.core.config import get_settings
from app.core.modes import OperationMode
from app.db.models import OrderAuditLog, PendingApproval
from app.permission.gate import STATUS_PENDING
from app.risk.risk_manager import RiskDecision


def _set_mode(monkeypatch, mode: OperationMode) -> None:
    monkeypatch.setattr(get_settings(), "default_mode", mode)


def _candidate(**overrides) -> AICandidate:
    base = dict(
        symbol="005930",
        side=OrderSide.BUY,
        quantity=1,
        order_type=OrderType.MARKET,
        confidence=72,
        quality_score=68,
        supporting_reasons=["SMA crossover bullish", "Volume spike"],
        opposing_reasons=["RSI 78 — overbought risk"],
        risk_note="단기 과열 주의 — 손절 -1.5% 권장",
        model="claude-fake",
        analysis_log_id=42,
        target_price=80_000,
        stop_price=75_000,
    )
    base.update(overrides)
    return AICandidate(**base)


# ====================================================================
# 1. AICandidate → OrderRequest conversion
# ====================================================================


def test_ai_candidate_carries_decision_meta_into_order_request():
    cand = _candidate()
    order = cand.to_order_request()
    assert order.symbol == "005930"
    assert order.side == OrderSide.BUY
    assert order.quantity == 1
    assert order.trade_reason == AI_ASSIST_TRADE_REASON
    assert order.strategy == "ai_assist"
    assert order.signal_confidence == 72
    assert order.signal_strength == 68
    assert order.ai_decision_meta is not None
    assert order.ai_decision_meta["source"] == "AI_ASSIST"
    assert order.ai_decision_meta["model"] == "claude-fake"
    assert order.ai_decision_meta["analysis_log_id"] == 42
    assert "SMA crossover bullish" in order.ai_decision_meta["supporting_reasons"]
    assert order.ai_decision_meta["target_price"] == 80_000


def test_ai_candidate_clamps_confidence_into_valid_range():
    cand = _candidate(confidence=150, quality_score=-5)
    order = cand.to_order_request()
    # OrderRequest enforces 0..100 — pre-clamp would fail validation.
    assert 0 <= order.signal_confidence <= 100
    assert order.signal_strength is None or 0 <= order.signal_strength <= 100


# ====================================================================
# 2. submit_candidate: LIVE_AI_ASSIST happy path → PendingApproval queued
# ====================================================================


def test_submit_candidate_queues_pending_approval(client, monkeypatch):
    _set_mode(monkeypatch, OperationMode.LIVE_AI_ASSIST)
    spy = AsyncMock()
    client.test_broker.place_order = spy

    risk = client.test_risk_manager
    risk.policy.enable_live_trading = True

    with client.test_db_factory() as db:
        result = _run(submit_candidate(
            candidate=_candidate(),
            mode=OperationMode.LIVE_AI_ASSIST,
            broker=client.test_broker,
            risk=risk,
            db=db,
            enable_live_trading=True,
            enable_ai_execution=False,
            enable_futures_live_trading=False,
        ))
        assert result.permission.allowed is True
        assert result.routing.decision == RiskDecision.NEEDS_APPROVAL
        assert result.routing.approval is not None
        assert result.routing.approval.status == STATUS_PENDING
        assert result.routing.audit.requested_by_ai is True
        assert result.routing.audit.trade_reason == AI_ASSIST_TRADE_REASON
        assert result.routing.audit.ai_decision_meta["source"] == "AI_ASSIST"

    # Critical: broker.place_order must NOT be invoked at submit time —
    # only at operator approve. Defends CLAUDE.md absolute principle 5.
    spy.assert_not_awaited()


# ====================================================================
# 3. submit_candidate: emergency_stop / disable_ai_orders → permission denied
# ====================================================================


def test_submit_candidate_blocked_by_emergency_stop(client, monkeypatch):
    _set_mode(monkeypatch, OperationMode.LIVE_AI_ASSIST)
    spy = AsyncMock()
    client.test_broker.place_order = spy

    risk = client.test_risk_manager
    risk.emergency_stop = True
    risk.policy.enable_live_trading = True

    with client.test_db_factory() as db:
        with pytest.raises(AiAssistPermissionDeniedError) as exc:
            _run(submit_candidate(
                candidate=_candidate(),
                mode=OperationMode.LIVE_AI_ASSIST,
                broker=client.test_broker,
                risk=risk,
                db=db,
                enable_live_trading=True,
                enable_ai_execution=False,
                enable_futures_live_trading=False,
            ))
        # Permission gate decision carries the reason.
        assert exc.value.decision.allowed is False
        assert any("emergency_stop" in r for r in exc.value.decision.reasons)

        # No audit row, no approval row written.
        assert db.execute(select(OrderAuditLog)).scalars().all() == []
        assert db.execute(select(PendingApproval)).scalars().all() == []

    spy.assert_not_awaited()


def test_submit_candidate_blocked_by_disable_ai_orders(client, monkeypatch):
    _set_mode(monkeypatch, OperationMode.LIVE_AI_ASSIST)
    risk = client.test_risk_manager
    risk.policy.disable_ai_orders = True
    risk.policy.enable_live_trading = True
    spy = AsyncMock()
    client.test_broker.place_order = spy

    with client.test_db_factory() as db:
        with pytest.raises(AiAssistPermissionDeniedError):
            _run(submit_candidate(
                candidate=_candidate(),
                mode=OperationMode.LIVE_AI_ASSIST,
                broker=client.test_broker,
                risk=risk,
                db=db,
                enable_live_trading=True,
                enable_ai_execution=False,
                enable_futures_live_trading=False,
            ))
    spy.assert_not_awaited()


# ====================================================================
# 4. submit_candidate: non-LIVE_AI_ASSIST mode raises
# ====================================================================


def test_submit_candidate_rejects_other_modes(client, monkeypatch):
    _set_mode(monkeypatch, OperationMode.SIMULATION)
    risk = client.test_risk_manager
    spy = AsyncMock()
    client.test_broker.place_order = spy

    with client.test_db_factory() as db:
        with pytest.raises(AiAssistModeError):
            _run(submit_candidate(
                candidate=_candidate(),
                mode=OperationMode.SIMULATION,
                broker=client.test_broker,
                risk=risk,
                db=db,
                enable_live_trading=False,
                enable_ai_execution=False,
                enable_futures_live_trading=False,
            ))
    spy.assert_not_awaited()


# ====================================================================
# 5. /api/ai/assist/submit endpoint
# ====================================================================


def test_api_submit_returns_needs_approval_with_approval_id(client, monkeypatch):
    _set_mode(monkeypatch, OperationMode.LIVE_AI_ASSIST)
    client.test_risk_manager.policy.enable_live_trading = True
    spy = AsyncMock()
    client.test_broker.place_order = spy

    res = client.post("/api/ai/assist/submit", json={
        "symbol": "005930", "side": "BUY", "quantity": 1,
        "confidence": 72, "quality_score": 68,
        "supporting_reasons": ["bull cross", "vol spike"],
        "opposing_reasons":   ["rsi overbought"],
        "risk_note": "단기 과열 주의",
        "model": "claude-fake",
        "target_price": 80000, "stop_price": 75000,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["decision"] == "NEEDS_APPROVAL"
    assert body["approval_id"] is not None
    assert body["audit_id"] is not None
    assert body["permission_note"].startswith("AI permission OK")
    assert body["candidate_meta"]["source"] == "AI_ASSIST"
    assert "단기 과열" in body["candidate_meta"]["risk_note"]
    spy.assert_not_awaited()


def test_api_submit_blocked_by_emergency_stop_returns_403(client, monkeypatch):
    _set_mode(monkeypatch, OperationMode.LIVE_AI_ASSIST)
    client.test_risk_manager.policy.enable_live_trading = True
    client.test_risk_manager.emergency_stop = True
    spy = AsyncMock()
    client.test_broker.place_order = spy

    res = client.post("/api/ai/assist/submit", json={
        "symbol": "005930", "side": "BUY", "quantity": 1, "confidence": 50,
    })
    assert res.status_code == 403
    detail = res.json()["detail"]
    assert detail["error"] == "ai_permission_denied"
    assert any("emergency_stop" in r for r in detail["reasons"])
    spy.assert_not_awaited()


def test_api_submit_in_simulation_mode_returns_403(client, monkeypatch):
    _set_mode(monkeypatch, OperationMode.SIMULATION)
    spy = AsyncMock()
    client.test_broker.place_order = spy

    res = client.post("/api/ai/assist/submit", json={
        "symbol": "005930", "side": "BUY", "quantity": 1, "confidence": 50,
    })
    assert res.status_code == 403
    detail = res.json()["detail"]
    assert detail["error"] == "ai_assist_mode_required"
    assert detail["current_mode"] == "SIMULATION"
    spy.assert_not_awaited()


# ====================================================================
# 6. /api/ai/assist/pending — only AI Assist rows
# ====================================================================


def test_api_pending_lists_only_ai_assist_rows(client, monkeypatch):
    _set_mode(monkeypatch, OperationMode.LIVE_AI_ASSIST)
    client.test_risk_manager.policy.enable_live_trading = True

    # Submit 1 AI Assist candidate.
    submit_res = client.post("/api/ai/assist/submit", json={
        "symbol": "005930", "side": "BUY", "quantity": 1,
        "confidence": 75,
        "supporting_reasons": ["AI bull"], "opposing_reasons": [],
    })
    assert submit_res.status_code == 200, submit_res.text

    # Manually queue a non-AI approval directly via /api/broker/orders
    # (LIVE_AI_ASSIST mode also queues plain orders as NEEDS_APPROVAL).
    plain_res = client.post("/api/broker/orders", json={
        "symbol": "000660", "side": "BUY", "quantity": 1,
    })
    # /api/broker/orders returns 202 when the order is queued for approval —
    # we don't care about the exact code, only that the row landed in the
    # generic PendingApproval queue. The /api/ai/assist/pending filter must
    # still exclude it.
    assert plain_res.status_code in (200, 202), plain_res.text

    res = client.get("/api/ai/assist/pending")
    assert res.status_code == 200
    rows = res.json()
    # Only the AI Assist row — the plain manual order is filtered out by
    # requested_by_ai + trade_reason=ai_assist.
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "005930"
    assert row["confidence"] == 75
    assert row["request_source"] == "AI"
    assert "AI bull" in row["supporting_reasons"]


# ====================================================================
# 7. /api/ai/assist/summary
# ====================================================================


def test_api_summary_counts_ai_assist_pending(client, monkeypatch):
    _set_mode(monkeypatch, OperationMode.LIVE_AI_ASSIST)
    client.test_risk_manager.policy.enable_live_trading = True

    # Empty state baseline.
    res0 = client.get("/api/ai/assist/summary")
    assert res0.status_code == 200
    assert res0.json()["pending_count"] == 0
    assert res0.json()["total_24h"] == 0

    # Submit two candidates → both in NEEDS_APPROVAL queue.
    for sym in ("005930", "000660"):
        client.post("/api/ai/assist/submit", json={
            "symbol": sym, "side": "BUY", "quantity": 1, "confidence": 60,
        })

    res1 = client.get("/api/ai/assist/summary")
    body = res1.json()
    assert body["pending_count"] == 2
    assert body["total_24h"] == 2
    # 모든 candidate가 NEEDS_APPROVAL이라 approved_count_24h는 0.
    assert body["approved_count_24h"] == 0
    assert body["rejected_count_24h"] == 0
    assert "사람 승인" in body["notice"]
    assert body["last_submitted_at"] is not None


# ====================================================================
# 8. is_ai_assist_audit helper
# ====================================================================


def test_is_ai_assist_audit_true_only_when_both_flags_set():
    class FakeAudit:
        def __init__(self, requested_by_ai, trade_reason):
            self.requested_by_ai = requested_by_ai
            self.trade_reason = trade_reason

    assert is_ai_assist_audit(FakeAudit(True, "ai_assist")) is True
    assert is_ai_assist_audit(FakeAudit(True, "stop_loss")) is False
    assert is_ai_assist_audit(FakeAudit(False, "ai_assist")) is False
    assert is_ai_assist_audit(None) is False


# ====================================================================
# 9. Static guards: assist module + routes_ai_assist do not import broker
# ====================================================================


def test_assist_module_does_not_import_broker_or_executor():
    """절대 원칙 5/6 — AI Assist 흐름은 broker / OrderExecutor를 직접 import하지
    않고 route_order 단일 진입점만 사용한다."""
    import app.ai.assist as mod
    src_path = mod.__file__
    assert src_path is not None
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    forbidden = (
        "from app.brokers.kis",
        "from app.brokers.mock_broker",
        "from app.execution.executor import OrderExecutor",
        "broker.place_order(",
        "broker.cancel_order(",
    )
    for snippet in forbidden:
        assert snippet not in src, (
            f"app.ai.assist must not contain '{snippet}' — "
            "AI Assist flow must go through route_order only."
        )


def test_routes_ai_assist_does_not_call_broker_directly():
    """절대 원칙 — 라우터는 broker.place_order / cancel_order를 직접 호출하지
    않는다. submit_candidate (→ route_order) 위임만 가능."""
    import app.api.routes_ai_assist as mod
    src_path = mod.__file__
    assert src_path is not None
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    forbidden = (
        "broker.place_order(",
        "broker.cancel_order(",
        ".place_order(",
        ".cancel_order(",
    )
    for snippet in forbidden:
        assert snippet not in src, (
            f"routes_ai_assist must not contain '{snippet}' — "
            "all order flow goes through submit_candidate → route_order."
        )
