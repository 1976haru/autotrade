"""#45: AIExecutionGate tests.

Coverage:
- 기본 정책(`AIExecutionPolicy()`)에서 어떤 입력에서도 BLOCKED
- 모든 가드 통과 + canary mode → CANARY_ONLY (절대 ALLOW 아님)
- 모든 가드 통과 + canary mode 해제 → ALLOW
- 각 단일 가드 위반이 reason에 누적되는지 (12개 가드 전부)
- to_audit_meta()가 audit row carry용 dict 반환
- /api/ai-execution/evaluate, /policy
- 정적 가드: ai_execution_gate 모듈 + routes_ai_execution은 broker import 0건
- ENABLE_AI_EXECUTION이 default False 유지

본 게이트는 *최종 안전 레이어*. 한 가드라도 깨지면 invariant 위반 — 본 테스트
가 그 invariant를 lock한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.core.modes import OperationMode
from app.risk.ai_execution_gate import (
    AIExecutionDecision,
    AIExecutionInput,
    AIExecutionPolicy,
    build_default_blocked_policy,
    build_policy_status,
    evaluate_ai_execution,
)


_KST = timezone(timedelta(hours=9))


def _within_window_now() -> datetime:
    """KST 기준 11:30 — default window [10:00, 14:00) 안의 결정론적 시각."""
    today = datetime.now(_KST).date()
    return datetime.combine(today, datetime.min.time(),
                            tzinfo=_KST).replace(hour=11, minute=30)


def _passing_input(**overrides) -> AIExecutionInput:
    """모든 default 가드를 통과하는 입력 — 단일 가드를 깨려면 한 필드만 override."""
    base = dict(
        mode=OperationMode.LIVE_AI_EXECUTION,
        symbol="005930",
        quantity=1,
        latest_price=80_000,        # notional 80,000 < 100,000 default
        confidence=85,              # >= 80 default
        quality_score=75,            # >= 70 default
        explanation="SMA crossover bullish + volume spike — high conviction",
        target_price=85_000,
        stop_price=78_000,
        agent_name="ChiefTradingAgent",
        agent_chain_id="chain-001",
        strategy="ai_assist",
        today_ai_order_count=0,
        risk_passed=True,
        permission_passed=True,
        order_guard_passed=True,
        now_kst=_within_window_now(),
    )
    base.update(overrides)
    return AIExecutionInput(**base)


def _opted_in_policy(**overrides) -> AIExecutionPolicy:
    """모든 운영 게이트가 통과 가능한 가상 정책 — 테스트 전용. 본 PR의 default
    code path는 이 정책을 *생성하지 않는다* — 운영자 명시 opt-in 후에만 도달."""
    base = dict(
        enable_ai_execution=True,
        enable_live_trading=True,
        is_canary_mode=True,
        symbol_whitelist=frozenset({"005930", "000660"}),
    )
    base.update(overrides)
    return AIExecutionPolicy(**base)


# ====================================================================
# 1. Default policy → always BLOCKED (invariant)
# ====================================================================


def test_default_policy_blocks_any_input():
    """`AIExecutionPolicy()` default는 어떤 입력에서도 BLOCKED.

    이는 절대 invariant — 본 PR이 ENABLE_AI_EXECUTION을 false로 고정하면서
    'AIExecutionGate를 통과해 ALLOW가 되는 코드 경로 0건'을 보장한다.
    """
    policy = build_default_blocked_policy()
    inp = _passing_input()
    res = evaluate_ai_execution(inp=inp, policy=policy)
    assert res.decision == AIExecutionDecision.BLOCKED
    assert res.allowed_to_execute is False
    assert res.actual_broker_order_sent is False
    # 최소 enable_ai_execution=False + enable_live_trading=False + symbol_whitelist 비어 있음
    assert any("ENABLE_AI_EXECUTION" in r for r in res.reasons)
    assert any("ENABLE_LIVE_TRADING" in r for r in res.reasons)


def test_settings_default_keeps_ai_execution_disabled():
    """env / Settings의 default는 enable_ai_execution=False 유지."""
    s = get_settings()
    assert s.enable_ai_execution is False, (
        "ENABLE_AI_EXECUTION must remain False by default — #45 invariant"
    )


# ====================================================================
# 2. Canary mode: all passes → CANARY_ONLY (never ALLOW)
# ====================================================================


def test_all_pass_in_canary_returns_canary_only():
    policy = _opted_in_policy(is_canary_mode=True)
    res = evaluate_ai_execution(inp=_passing_input(), policy=policy)
    assert res.decision == AIExecutionDecision.CANARY_ONLY
    assert res.is_canary is True
    assert res.allowed_to_execute is False     # canary는 broker 주문 X
    assert res.actual_broker_order_sent is False
    assert "canary only" in res.audit_note.lower()
    assert res.reasons == []


def test_all_pass_canary_off_returns_allow():
    """canary mode를 명시적으로 끄면 ALLOW. 본 PR의 default code path는 여기에
    *도달하지 않는다* — 테스트 전용 시나리오."""
    policy = _opted_in_policy(is_canary_mode=False)
    res = evaluate_ai_execution(inp=_passing_input(), policy=policy)
    assert res.decision == AIExecutionDecision.ALLOW
    assert res.is_canary is False
    assert res.allowed_to_execute is True
    assert res.actual_broker_order_sent is True
    assert res.reasons == []


# ====================================================================
# 3. Each guard violation accumulates a reason
# ====================================================================


def test_block_on_wrong_mode():
    res = evaluate_ai_execution(
        inp=_passing_input(mode=OperationMode.LIVE_AI_ASSIST),
        policy=_opted_in_policy(),
    )
    assert res.decision == AIExecutionDecision.BLOCKED
    assert any("LIVE_AI_EXECUTION" in r for r in res.reasons)


def test_block_on_disable_ai_execution():
    res = evaluate_ai_execution(
        inp=_passing_input(),
        policy=_opted_in_policy(enable_ai_execution=False),
    )
    assert res.decision == AIExecutionDecision.BLOCKED
    assert any("ENABLE_AI_EXECUTION" in r for r in res.reasons)


def test_block_on_disable_live_trading():
    res = evaluate_ai_execution(
        inp=_passing_input(),
        policy=_opted_in_policy(enable_live_trading=False),
    )
    assert res.decision == AIExecutionDecision.BLOCKED
    assert any("ENABLE_LIVE_TRADING" in r for r in res.reasons)


def test_block_on_low_confidence():
    res = evaluate_ai_execution(
        inp=_passing_input(confidence=70),  # < 80 default
        policy=_opted_in_policy(),
    )
    assert res.decision == AIExecutionDecision.BLOCKED
    assert any("confidence" in r for r in res.reasons)


def test_block_on_low_quality_score():
    res = evaluate_ai_execution(
        inp=_passing_input(quality_score=50),  # < 70 default
        policy=_opted_in_policy(),
    )
    assert res.decision == AIExecutionDecision.BLOCKED
    assert any("quality_score" in r for r in res.reasons)


def test_block_on_missing_explanation():
    res = evaluate_ai_execution(
        inp=_passing_input(explanation=""),
        policy=_opted_in_policy(),
    )
    assert res.decision == AIExecutionDecision.BLOCKED
    assert any("explanation" in r for r in res.reasons)


def test_block_on_missing_exit_plan():
    res = evaluate_ai_execution(
        inp=_passing_input(target_price=None, stop_price=None),
        policy=_opted_in_policy(),
    )
    assert res.decision == AIExecutionDecision.BLOCKED
    assert any("exit plan" in r for r in res.reasons)


def test_block_on_oversize_notional():
    res = evaluate_ai_execution(
        inp=_passing_input(quantity=100, latest_price=10_000),  # 1,000,000 > 100,000 default
        policy=_opted_in_policy(),
    )
    assert res.decision == AIExecutionDecision.BLOCKED
    assert any("notional" in r for r in res.reasons)


def test_block_on_symbol_not_in_whitelist():
    res = evaluate_ai_execution(
        inp=_passing_input(symbol="999999"),
        policy=_opted_in_policy(),
    )
    assert res.decision == AIExecutionDecision.BLOCKED
    assert any("whitelist" in r for r in res.reasons)


def test_block_on_empty_whitelist_blocks_all():
    """symbol_whitelist=frozenset()는 *모든 종목 차단* — None은 의도적으로 X."""
    res = evaluate_ai_execution(
        inp=_passing_input(),
        policy=_opted_in_policy(symbol_whitelist=frozenset()),
    )
    assert res.decision == AIExecutionDecision.BLOCKED
    assert any("whitelist is empty" in r for r in res.reasons)


def test_block_on_outside_window():
    today = datetime.now(_KST).date()
    early_morning = datetime.combine(today, datetime.min.time(),
                                      tzinfo=_KST).replace(hour=8, minute=0)
    res = evaluate_ai_execution(
        inp=_passing_input(now_kst=early_morning),
        policy=_opted_in_policy(),
    )
    assert res.decision == AIExecutionDecision.BLOCKED
    assert any("execution window" in r for r in res.reasons)


def test_block_on_daily_count_exceeded():
    res = evaluate_ai_execution(
        inp=_passing_input(today_ai_order_count=3),  # >= 3 default
        policy=_opted_in_policy(),
    )
    assert res.decision == AIExecutionDecision.BLOCKED
    assert any("max_orders_per_day" in r for r in res.reasons)


def test_block_when_upstream_guard_failed():
    """RiskManager / AiPermissionGate / OrderGuard 어느 하나라도 통과하지 않으면
    AIExecutionGate가 BLOCKED — 책임 분리 invariant."""
    for field_name in ("risk_passed", "permission_passed", "order_guard_passed"):
        res = evaluate_ai_execution(
            inp=_passing_input(**{field_name: False}),
            policy=_opted_in_policy(),
        )
        assert res.decision == AIExecutionDecision.BLOCKED, (
            f"upstream {field_name}=False must BLOCK"
        )


# ====================================================================
# 4. to_audit_meta — audit carry
# ====================================================================


def test_to_audit_meta_carries_decision_and_reasons():
    res = evaluate_ai_execution(
        inp=_passing_input(),
        policy=build_default_blocked_policy(),
    )
    meta = res.to_audit_meta()
    assert meta["decision"] == "BLOCKED"
    assert isinstance(meta["reasons"], list)
    assert isinstance(meta["passed"], list)
    assert "audit_note" in meta
    assert "evaluated_at" in meta
    assert meta["is_canary"] is False


def test_canary_audit_note_says_no_broker_order():
    res = evaluate_ai_execution(
        inp=_passing_input(),
        policy=_opted_in_policy(is_canary_mode=True),
    )
    assert res.is_canary is True
    assert res.audit_note == "AI execution canary only; no broker order sent"


# ====================================================================
# 5. /api/ai-execution endpoints
# ====================================================================


def test_api_policy_endpoint_shows_disabled_default(client):
    res = client.get("/api/ai-execution/policy")
    assert res.status_code == 200
    body = res.json()
    assert body["enable_ai_execution"] is False
    assert body["enable_live_trading"] is False
    assert body["is_canary_mode"] is True
    assert body["live_ai_execution_disabled"] is True
    assert body["max_notional_per_order"] == 100_000
    assert body["max_orders_per_day"] == 3
    assert body["symbol_whitelist"] == []
    assert "AI API Key는 주문 권한이 아닙니다" in body["notice"]


def test_api_evaluate_endpoint_returns_blocked_by_default(client):
    res = client.post("/api/ai-execution/evaluate", json={
        "mode": "LIVE_AI_EXECUTION",
        "symbol": "005930",
        "quantity": 1,
        "latest_price": 80000,
        "confidence": 95,
        "quality_score": 90,
        "explanation": "high-conviction setup",
        "target_price": 85000,
        "stop_price": 78000,
        "risk_passed": True,
        "permission_passed": True,
        "order_guard_passed": True,
    })
    assert res.status_code == 200
    body = res.json()
    # default ENABLE_AI_EXECUTION=False → BLOCKED.
    assert body["decision"] == "BLOCKED"
    assert body["actual_broker_order_sent"] is False
    assert body["is_canary"] is False
    assert any("ENABLE_AI_EXECUTION" in r for r in body["reasons"])


def test_api_evaluate_unknown_mode_blocks_with_explicit_reason(client):
    res = client.post("/api/ai-execution/evaluate", json={
        "mode": "BOGUS_MODE", "symbol": "005930", "quantity": 1,
        "latest_price": 80000,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["decision"] == "BLOCKED"
    assert any("unknown mode" in r for r in body["reasons"])


def test_api_evaluate_does_not_create_audit_or_approval(client):
    """evaluate는 read-only — 호출 후 audit / approval row가 생기지 않는다."""
    from sqlalchemy import select
    from app.db.models import OrderAuditLog, PendingApproval

    client.post("/api/ai-execution/evaluate", json={
        "mode": "LIVE_AI_EXECUTION", "symbol": "005930",
        "quantity": 1, "latest_price": 80000,
    })
    with client.test_db_factory() as db:
        assert db.execute(select(OrderAuditLog)).scalars().all() == []
        assert db.execute(select(PendingApproval)).scalars().all() == []


# ====================================================================
# 6. Static guards: no broker / executor / route_order import
# ====================================================================


def test_ai_execution_gate_module_does_not_import_broker_or_executor():
    """AIExecutionGate는 순수 의사결정 함수 — broker / OrderExecutor 미참조."""
    import app.risk.ai_execution_gate as mod
    src_path = mod.__file__
    assert src_path is not None
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    forbidden = (
        "from app.brokers",
        "import app.brokers",
        "from app.execution.executor",
        "from app.execution.order_router",
        "broker.place_order(",
        ".place_order(",
    )
    for snippet in forbidden:
        assert snippet not in src, (
            f"app.risk.ai_execution_gate must not contain '{snippet}'"
        )


def test_routes_ai_execution_does_not_import_broker_or_executor():
    import app.api.routes_ai_execution as mod
    src_path = mod.__file__
    assert src_path is not None
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    forbidden = (
        "from app.brokers",
        "import app.brokers",
        "from app.execution.executor",
        "broker.place_order(",
        ".place_order(",
    )
    for snippet in forbidden:
        assert snippet not in src, (
            f"app.api.routes_ai_execution must not contain '{snippet}'"
        )


# ====================================================================
# 7. Status surface helper
# ====================================================================


def test_build_policy_status_marks_disabled_when_flags_off():
    status = build_policy_status(build_default_blocked_policy())
    assert status["live_ai_execution_disabled"] is True
    assert status["is_canary_mode"] is True
    assert "AI API Key" in status["notice"]


def test_build_policy_status_when_opted_in():
    status = build_policy_status(_opted_in_policy())
    # 두 flag가 모두 True면 disabled=False (그래도 canary).
    assert status["live_ai_execution_disabled"] is False
    assert status["is_canary_mode"] is True
