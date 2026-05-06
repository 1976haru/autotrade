"""All-guards integration tests (184, MUST).

158~183 동안 추가된 모든 RiskPolicy 가드를 한 번에 활성화한 상태에서:
- 정상 주문이 통과하는지 (모든 가드 통과)
- 단일 가드만 위반 시 정확한 reason
- 다중 가드 위반 시 reasons 누적
- hard short-circuit 가드의 우선순위

기존 단위 테스트가 가드를 개별 검증한다면, 본 모듈은 *상호작용*과 통합
규칙을 검증.
"""

from datetime import datetime, timezone


from app.brokers.base import Balance, OrderRequest, OrderSide, Position
from app.core.modes import OperationMode
from app.risk.risk_manager import RiskDecision, RiskManager, RiskPolicy


def _balance(cash: int = 100_000_000) -> Balance:
    return Balance(cash=cash, equity=cash, buying_power=cash)


def _ai_buy(symbol: str = "005930", qty: int = 1, confidence: int = 80) -> OrderRequest:
    return OrderRequest(
        symbol=symbol, side=OrderSide.BUY, quantity=qty,
        signal_confidence=confidence, signal_strength=confidence,
        ai_decision_meta={"confidence": confidence, "reasons": ["test"]},
    )


def _strict_policy() -> RiskPolicy:
    """모든 가드를 운영 권장값으로 켠 정책. 진정한 'fortress mode'."""
    return RiskPolicy(
        # 절대값 한도 (큰 자본 가정).
        max_order_notional      = 5_000_000,
        max_daily_loss          = 1_000_000,
        max_positions           = 10,
        max_symbol_exposure     = 10_000_000,
        max_total_exposure      = 50_000_000,
        # 비율 한도.
        max_position_size_pct   = 5.0,    # 자본 5% 단일 주문
        max_total_exposure_pct  = 50.0,   # 자본 50% 총 노출
        max_symbol_exposure_pct = 15.0,   # 자본 15% 종목별
        # AI 가드.
        min_ai_confidence       = 60,
        enforce_ai_reasoning    = True,
        disable_ai_orders       = False,  # AI 자체는 허용
        # 운영 가드.
        symbol_whitelist        = frozenset({"005930", "000660"}),
        # 시장 시간은 테스트 안정성 위해 비활성 (시간 의존 테스트는 별도).
        enforce_market_hours    = False,
        # 시간 한도.
        stale_price_max_age_seconds = 60,
        ai_rate_limit_window_seconds = 60,
        ai_rate_limit_max_count   = 10,
        global_rate_limit_window_seconds = 60,
        global_rate_limit_max_count      = 50,
        # 자동화.
        auto_stop_consecutive_rejections = 0,  # 자동 stop은 별도 테스트
        max_orders_per_day      = 100,
        # 안전 가드.
        enable_live_trading     = False,
        enable_ai_execution     = False,
    )


# ---------- 정상 주문 통과 ----------

def test_strict_policy_approves_normal_ai_order():
    """모든 가드가 켜진 상태에서 정상 주문 통과 — 운영 가능 invariant."""
    risk = RiskManager(_strict_policy())
    result = risk.evaluate_order(
        order=_ai_buy(symbol="005930", qty=1, confidence=80),
        mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        requested_by_ai=True,
        latest_price_timestamp=datetime.now(timezone.utc),
    )
    assert result.decision == RiskDecision.APPROVED, result.reasons


def test_strict_policy_approves_strategy_order():
    """비-AI strategy 주문도 통과."""
    risk = RiskManager(_strict_policy())
    result = risk.evaluate_order(
        order=OrderRequest(
            symbol="005930", side=OrderSide.BUY, quantity=1,
            strategy="sma_crossover",
        ),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        requested_by_ai=False,
        latest_price_timestamp=datetime.now(timezone.utc),
    )
    assert result.decision == RiskDecision.APPROVED, result.reasons


# ---------- hard short-circuit 우선순위 ----------

def test_emergency_stop_short_circuits_all():
    """emergency_stop이 켜져 있으면 다른 모든 reason 무시 — 단독 reason."""
    risk = RiskManager(_strict_policy())
    risk.set_emergency_stop(True)
    result = risk.evaluate_order(
        order=_ai_buy(symbol="UNKNOWN", qty=999),  # 의도적으로 다중 위반
        mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        requested_by_ai=True,
    )
    assert result.decision == RiskDecision.REJECTED
    assert len(result.reasons) == 1
    assert "emergency stop" in result.reasons[0]


def test_ai_kill_switch_short_circuits_only_ai():
    """disable_ai_orders는 AI만 short-circuit. 비-AI는 정상 평가."""
    policy = _strict_policy()
    policy.disable_ai_orders = True
    risk = RiskManager(policy)
    # AI 주문은 AI kill-switch single reason.
    result_ai = risk.evaluate_order(
        order=_ai_buy(symbol="UNKNOWN", qty=999),
        mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        requested_by_ai=True,
    )
    assert result_ai.decision == RiskDecision.REJECTED
    assert len(result_ai.reasons) == 1
    assert "AI orders are disabled" in result_ai.reasons[0]
    # 비-AI는 일반 가드 평가 — symbol whitelist 위반 등.
    result_nonai = risk.evaluate_order(
        order=OrderRequest(symbol="UNKNOWN", side=OrderSide.BUY, quantity=1),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        requested_by_ai=False,
    )
    assert result_nonai.decision == RiskDecision.REJECTED
    assert any("whitelist" in r for r in result_nonai.reasons)


# ---------- 단일 가드 위반 격리 ----------

def test_only_low_confidence_violation():
    risk = RiskManager(_strict_policy())
    result = risk.evaluate_order(
        order=_ai_buy(confidence=30),  # min=60 미달
        mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        requested_by_ai=True,
        latest_price_timestamp=datetime.now(timezone.utc),
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("AI signal confidence" in r for r in result.reasons)
    # 다른 reason이 누적되지 않은지 확인.
    others = [r for r in result.reasons if "AI signal confidence" not in r]
    assert others == [], f"unexpected other reasons: {others}"


def test_only_whitelist_violation():
    risk = RiskManager(_strict_policy())
    result = risk.evaluate_order(
        order=_ai_buy(symbol="UNKNOWN_TICKER"),
        mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        requested_by_ai=True,
        latest_price_timestamp=datetime.now(timezone.utc),
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("whitelist" in r for r in result.reasons)


# ---------- 다중 위반 누적 ----------

def test_multiple_violations_accumulate():
    """소량 자본 + 큰 주문 + 미등록 symbol → notional + whitelist + position_size_pct +
    total_exposure_pct 동시 위반."""
    risk = RiskManager(_strict_policy())
    result = risk.evaluate_order(
        order=_ai_buy(symbol="UNKNOWN", qty=100),  # whitelist 위반 + 큰 qty
        mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(cash=1_000_000),  # 작은 자본 → pct 한도 초과
        positions=[],
        latest_price=100_000,  # 100 * 100_000 = 10M (≫ 5M cap)
        requested_by_ai=True,
        latest_price_timestamp=datetime.now(timezone.utc),
    )
    assert result.decision == RiskDecision.REJECTED
    # 최소 4건 위반: whitelist / max_order_notional / max_position_size_pct /
    # cash insufficient (10M > 1M).
    assert any("whitelist" in r for r in result.reasons)
    assert any("max_order_notional" in r for r in result.reasons)
    assert any("equity" in r for r in result.reasons)


# ---------- LIVE 모드 가드 일관성 ----------

def test_live_manual_strict_policy_routes_to_approval_queue():
    """LIVE_MANUAL_APPROVAL 모드에서 strict policy + enable_live_trading=True →
    NEEDS_APPROVAL (approve 시점 가드는 PermissionGate가 다시 적용)."""
    policy = _strict_policy()
    policy.enable_live_trading = True
    risk = RiskManager(policy)
    result = risk.evaluate_order(
        order=_ai_buy(symbol="005930", qty=1, confidence=80),
        mode=OperationMode.LIVE_MANUAL_APPROVAL,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        requested_by_ai=True,
        latest_price_timestamp=datetime.now(timezone.utc),
    )
    assert result.decision == RiskDecision.NEEDS_APPROVAL


def test_live_manual_blocked_when_flag_off():
    """enable_live_trading=False면 LIVE 큐 자체가 닫힘 (061)."""
    policy = _strict_policy()
    # enable_live_trading=False (default).
    risk = RiskManager(policy)
    result = risk.evaluate_order(
        order=_ai_buy(symbol="005930"),
        mode=OperationMode.LIVE_MANUAL_APPROVAL,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        requested_by_ai=True,
        latest_price_timestamp=datetime.now(timezone.utc),
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("live trading is disabled" in r for r in result.reasons)


# ---------- 누적 노출 가드 상호작용 ----------

def test_position_size_and_total_exposure_cumulative():
    """단일 주문은 OK지만 누적 시 total_exposure 위반."""
    policy = _strict_policy()
    # equity 1M에서 max_total_exposure_pct=50% → 500K cap.
    risk = RiskManager(policy)
    # 기존 400K 노출.
    existing = Position(symbol="005930", quantity=4, avg_price=100_000,
                         market_price=100_000)
    # 신규 200K → total 600K > 500K cap.
    result = risk.evaluate_order(
        order=_ai_buy(symbol="005930", qty=2, confidence=80),
        mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(cash=1_000_000),
        positions=[existing],
        latest_price=100_000,
        requested_by_ai=True,
        latest_price_timestamp=datetime.now(timezone.utc),
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("total exposure" in r for r in result.reasons)


# ---------- runtime toggle 우선순위 ----------

def test_runtime_emergency_stop_overrides_strict_policy_approval():
    """정상 주문이라도 emergency_stop runtime 토글 시 거부."""
    risk = RiskManager(_strict_policy())
    # 첫 호출 — 정상.
    result_normal = risk.evaluate_order(
        order=_ai_buy(),
        mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        requested_by_ai=True,
        latest_price_timestamp=datetime.now(timezone.utc),
    )
    assert result_normal.decision == RiskDecision.APPROVED

    # emergency_stop 토글.
    risk.set_emergency_stop(True)
    result_stopped = risk.evaluate_order(
        order=_ai_buy(),
        mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        requested_by_ai=True,
        latest_price_timestamp=datetime.now(timezone.utc),
    )
    assert result_stopped.decision == RiskDecision.REJECTED


def test_runtime_ai_kill_switch_does_not_block_strategy():
    """set_ai_disabled는 AI만 차단 — strategy는 그대로."""
    risk = RiskManager(_strict_policy())
    risk.set_ai_disabled(True)
    # AI 차단.
    result_ai = risk.evaluate_order(
        order=_ai_buy(),
        mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(), positions=[],
        latest_price=75_000,
        requested_by_ai=True,
    )
    assert result_ai.decision == RiskDecision.REJECTED
    # strategy 정상 (whitelist 통과 symbol).
    result_strategy = risk.evaluate_order(
        order=OrderRequest(
            symbol="005930", side=OrderSide.BUY, quantity=1,
            strategy="sma_crossover",
        ),
        mode=OperationMode.SIMULATION,
        balance=_balance(), positions=[],
        latest_price=75_000,
        requested_by_ai=False,
        latest_price_timestamp=datetime.now(timezone.utc),
    )
    assert result_strategy.decision == RiskDecision.APPROVED, result_strategy.reasons
