from app.brokers.base import Balance, OrderRequest, OrderSide, Position
from app.core.modes import OperationMode
from app.risk.risk_manager import RiskDecision, RiskManager, RiskPolicy


def _balance(cash: int = 10_000_000) -> Balance:
    return Balance(cash=cash, equity=cash, buying_power=cash)


def _buy(qty: int = 1, symbol: str = "005930") -> OrderRequest:
    return OrderRequest(symbol=symbol, side=OrderSide.BUY, quantity=qty)


def test_simulation_small_order_is_approved():
    risk = RiskManager(RiskPolicy())
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.APPROVED


def test_rejects_order_over_notional_limit():
    risk = RiskManager(RiskPolicy(max_order_notional=100_000))
    result = risk.evaluate_order(
        order=_buy(10),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert "order notional exceeds max_order_notional" in result.reasons


def test_emergency_stop_rejects_otherwise_valid_order():
    risk = RiskManager(RiskPolicy())
    risk.set_emergency_stop(True)
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert "emergency stop is enabled" in result.reasons


def test_daily_loss_limit_rejects_new_orders():
    risk = RiskManager(RiskPolicy(max_daily_loss=200_000))
    risk.daily_realized_pnl = -250_000
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert "daily loss limit reached" in result.reasons


def test_insufficient_cash_is_rejected():
    risk = RiskManager(RiskPolicy())
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.SIMULATION,
        balance=_balance(cash=10_000),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert "insufficient cash" in result.reasons


def test_max_positions_blocks_new_symbol():
    risk = RiskManager(RiskPolicy(max_positions=2))
    held = [
        Position(symbol="000660", quantity=1, avg_price=185_000, market_price=185_000),
        Position(symbol="035420", quantity=1, avg_price=205_000, market_price=205_000),
    ]
    result = risk.evaluate_order(
        order=_buy(1, symbol="005930"),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=held,
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert "max positions reached" in result.reasons


def test_symbol_exposure_limit():
    risk = RiskManager(RiskPolicy(max_symbol_exposure=200_000, max_order_notional=10_000_000))
    held = [Position(symbol="005930", quantity=2, avg_price=75_000, market_price=75_000)]
    result = risk.evaluate_order(
        order=_buy(2, symbol="005930"),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=held,
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert "symbol exposure limit exceeded" in result.reasons


def test_shadow_mode_rejects_live_order_attempt():
    risk = RiskManager(RiskPolicy(enable_live_trading=True))
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.LIVE_SHADOW,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("LIVE_SHADOW" in reason for reason in result.reasons)


def test_manual_mode_requires_approval():
    risk = RiskManager(RiskPolicy(enable_live_trading=True))
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.LIVE_MANUAL_APPROVAL,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.NEEDS_APPROVAL


def test_ai_assist_mode_requires_approval():
    risk = RiskManager(RiskPolicy(enable_live_trading=True))
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.LIVE_AI_ASSIST,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.NEEDS_APPROVAL


def test_live_ai_execution_requires_global_flag():
    risk = RiskManager(RiskPolicy(enable_live_trading=False, enable_ai_execution=True))
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.LIVE_AI_EXECUTION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        requested_by_ai=True,
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("live trading" in reason for reason in result.reasons)


def test_live_ai_execution_blocked_when_ai_flag_off():
    risk = RiskManager(RiskPolicy(enable_live_trading=True, enable_ai_execution=False))
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.LIVE_AI_EXECUTION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        requested_by_ai=True,
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("AI execution" in reason for reason in result.reasons)


def test_live_ai_execution_approved_when_both_flags_on():
    risk = RiskManager(RiskPolicy(enable_live_trading=True, enable_ai_execution=True))
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.LIVE_AI_EXECUTION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        requested_by_ai=True,
    )
    assert result.decision == RiskDecision.APPROVED
    assert result.allowed is True


# ---------- LIVE-readiness invariants (059 audit) ----------
#
# evaluate_order has an early-return for LIVE_MANUAL_APPROVAL / LIVE_AI_ASSIST
# that converts the order to NEEDS_APPROVAL before checking the global
# enable_live_trading flag and (importantly) before any reason populated
# earlier could downgrade the decision to REJECTED. The tests below lock
# that behavior in so a future RiskManager refactor that wants to gate the
# queue itself becomes a deliberate change. The multi-layer defense today
# still holds because get_broker returns MockBroker for these modes
# (test_live_*_returns_mock) — the LIVE PR has to wire the real broker AND
# decide whether enable_live_trading should additionally short-circuit the
# queue.

def test_manual_approval_rejects_when_live_trading_disabled():
    """061 hardening: the global ENABLE_LIVE_TRADING flag now blocks the
    NEEDS_APPROVAL queue itself, not just downstream execution. The queue
    no longer fills with orders the operator hasn't authorized at the
    global flag level. Reasons list contains only the flag string —
    incidental approval-mode wording is dropped because it no longer
    applies."""
    risk = RiskManager(RiskPolicy(enable_live_trading=False))
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.LIVE_MANUAL_APPROVAL,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert result.reasons == ["live trading is disabled by global safety flag"]


def test_ai_assist_rejects_when_live_trading_disabled():
    """Mirror of the LIVE_MANUAL_APPROVAL invariant but for LIVE_AI_ASSIST.
    The two share the early-return path; both must remain symmetric so any
    LIVE-routing change applies to both at once."""
    risk = RiskManager(RiskPolicy(enable_live_trading=False))
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.LIVE_AI_ASSIST,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert result.reasons == ["live trading is disabled by global safety flag"]


def test_emergency_stop_short_circuits_to_rejected_in_manual_approval_mode():
    """060 hardening: emergency_stop now forces REJECTED before the
    NEEDS_APPROVAL early-return fires. Previously the order queued with
    "emergency stop" alongside "manual approval required" and relied on
    the operator to spot the alarm in the modal — now the queue itself is
    closed while the alarm is on. Reasons list is focused on the alarm
    only, not the incidental "manual approval required" string that no
    longer applies."""
    risk = RiskManager(RiskPolicy())
    risk.set_emergency_stop(True)
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.LIVE_MANUAL_APPROVAL,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert result.reasons == ["emergency stop is enabled"]


def test_emergency_stop_short_circuits_to_rejected_in_ai_assist_mode():
    """Mirror of the LIVE_MANUAL_APPROVAL invariant. The two modes share the
    early-return path; both must remain symmetrically blocked when emergency
    stop is engaged."""
    risk = RiskManager(RiskPolicy())
    risk.set_emergency_stop(True)
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.LIVE_AI_ASSIST,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert result.reasons == ["emergency stop is enabled"]


def test_emergency_stop_short_circuits_in_live_ai_execution_with_flags_on():
    """Even with both global flags enabled, emergency_stop wins — operator's
    explicit stop signal beats configured trading permissions."""
    risk = RiskManager(RiskPolicy(enable_live_trading=True, enable_ai_execution=True))
    risk.set_emergency_stop(True)
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.LIVE_AI_EXECUTION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        requested_by_ai=True,
    )
    assert result.decision == RiskDecision.REJECTED
    assert result.reasons == ["emergency stop is enabled"]


def test_emergency_stop_short_circuits_in_live_shadow_mode():
    """LIVE_SHADOW already rejects every order; with emergency_stop the
    rejection reason is the alarm rather than the shadow-mode notice. This
    keeps the audit row pointed at the operator action, not the mode."""
    risk = RiskManager(RiskPolicy())
    risk.set_emergency_stop(True)
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.LIVE_SHADOW,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert result.reasons == ["emergency stop is enabled"]


def test_manual_approval_surfaces_oversized_reason_to_operator():
    """A notional violation in SIMULATION mode produces REJECTED. The same
    violation in LIVE_MANUAL_APPROVAL — with live trading globally enabled
    — produces NEEDS_APPROVAL with the notional reason attached. The
    operator gets the violation reason in the modal and decides whether to
    override. This is the operator-override design; the test locks it in.

    (When enable_live_trading is off, the 061 queue gate REJECTs before
    this scenario can run — see test_manual_approval_rejects_when_live_trading_disabled.)
    """
    risk = RiskManager(RiskPolicy(max_order_notional=100_000, enable_live_trading=True))
    result = risk.evaluate_order(
        order=_buy(10),  # 10 * 75_000 = 750_000 > 100_000 cap
        mode=OperationMode.LIVE_MANUAL_APPROVAL,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.NEEDS_APPROVAL
    assert any("max_order_notional" in r for r in result.reasons)
    assert any("manual approval" in r for r in result.reasons)


# ---------- RiskPolicy.from_settings ----------

def _settings(**overrides):
    """Stand-in for app.core.config.Settings — only the fields RiskPolicy reads."""
    from types import SimpleNamespace
    base = dict(
        risk_max_order_notional   = 1_000_000,
        risk_max_daily_loss       = 200_000,
        risk_max_positions        = 5,
        risk_max_symbol_exposure  = 1_500_000,
        enable_live_trading       = False,
        enable_ai_execution       = False,
        stale_price_max_age_seconds = 60,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_policy_from_settings_at_defaults_matches_dataclass_defaults():
    """Unset env vars must preserve current behavior — no silent threshold change."""
    fs   = RiskPolicy.from_settings(_settings())
    bare = RiskPolicy()
    assert fs.max_order_notional   == bare.max_order_notional
    assert fs.max_daily_loss       == bare.max_daily_loss
    assert fs.max_positions        == bare.max_positions
    assert fs.max_symbol_exposure  == bare.max_symbol_exposure
    assert fs.enable_live_trading  == bare.enable_live_trading
    assert fs.enable_ai_execution  == bare.enable_ai_execution
    assert fs.stale_price_max_age_seconds == bare.stale_price_max_age_seconds


def test_policy_from_settings_propagates_threshold_overrides():
    p = RiskPolicy.from_settings(_settings(
        risk_max_order_notional   = 50_000,
        risk_max_daily_loss       = 75_000,
        risk_max_positions        = 2,
        risk_max_symbol_exposure  = 100_000,
    ))
    assert p.max_order_notional   == 50_000
    assert p.max_daily_loss       == 75_000
    assert p.max_positions        == 2
    assert p.max_symbol_exposure  == 100_000


def test_policy_from_settings_propagates_safety_flags():
    """Previously, ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION env flags were
    not wired into the runtime RiskPolicy (the dependency built RiskPolicy()
    with no args, falling back to dataclass defaults False/False). Wiring is
    asserted here so that regression doesn't recur."""
    p = RiskPolicy.from_settings(_settings(
        enable_live_trading = True,
        enable_ai_execution = True,
    ))
    assert p.enable_live_trading is True
    assert p.enable_ai_execution is True


def test_lowered_notional_threshold_rejects_orders_that_default_would_approve():
    """End-to-end sanity: tunable threshold actually changes evaluation."""
    risk = RiskManager(RiskPolicy.from_settings(_settings(
        risk_max_order_notional = 100_000,
    )))
    # 75_000 * 10 = 750_000 > 100_000 (configured limit)
    result = risk.evaluate_order(
        order=_buy(10),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert "order notional exceeds max_order_notional" in result.reasons


# ---------- 143: stale price detection ----------

from datetime import datetime, timedelta, timezone


def test_stale_price_rejects_with_explicit_reason():
    """timestamp가 threshold보다 오래된 경우 RiskManager가 즉시 REJECTED.
    invariant: '시세가 stale이면 사이즈/포지션 평가의 근거가 없으므로 차단'."""
    risk = RiskManager(RiskPolicy(stale_price_max_age_seconds=30))
    stale_ts = datetime.now(timezone.utc) - timedelta(seconds=120)
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        latest_price_timestamp=stale_ts,
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("stale" in r for r in result.reasons), result.reasons


def test_fresh_price_passes_stale_check():
    """timestamp가 threshold 이내면 stale 검사를 통과 — 다른 검증으로 결정."""
    risk = RiskManager(RiskPolicy(stale_price_max_age_seconds=60))
    fresh_ts = datetime.now(timezone.utc)
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        latest_price_timestamp=fresh_ts,
    )
    assert result.decision == RiskDecision.APPROVED


def test_stale_check_skipped_when_timestamp_is_none():
    """timestamp 미제공 — 기존 호출 경로 호환. 검사 자체를 건너뛴다."""
    risk = RiskManager(RiskPolicy(stale_price_max_age_seconds=60))
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        # latest_price_timestamp 미제공
    )
    assert result.decision == RiskDecision.APPROVED


def test_stale_check_disabled_when_threshold_is_zero():
    """policy.stale_price_max_age_seconds=0 → 검사 비활성. 운영자가 의도적으로
    꺼둘 수 있다 (예: backtest fixture 환경)."""
    risk = RiskManager(RiskPolicy(stale_price_max_age_seconds=0))
    very_stale = datetime.now(timezone.utc) - timedelta(days=365)
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        latest_price_timestamp=very_stale,
    )
    assert result.decision == RiskDecision.APPROVED


def test_stale_check_normalizes_naive_timestamp_as_utc():
    """naive datetime은 UTC로 가정 — broker의 timestamp 약속과 일치."""
    risk = RiskManager(RiskPolicy(stale_price_max_age_seconds=30))
    naive_old = (datetime.now(timezone.utc) - timedelta(seconds=120)).replace(tzinfo=None)
    result = risk.evaluate_order(
        order=_buy(1),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        latest_price_timestamp=naive_old,
    )
    assert result.decision == RiskDecision.REJECTED


def test_stale_check_runs_before_other_checks():
    """stale은 emergency_stop과 같은 hard-reject — notional / cash 같은 다른
    위반과 같이 누적되지 않고 단독 reason으로 surface."""
    # notional 위반과 stale을 동시에 발생시키면 — stale만 reason에 들어가야.
    risk = RiskManager(RiskPolicy(
        max_order_notional=100_000,
        stale_price_max_age_seconds=30,
    ))
    stale_ts = datetime.now(timezone.utc) - timedelta(seconds=120)
    result = risk.evaluate_order(
        order=_buy(10),  # 10 * 75_000 = 750_000 > 100_000 cap
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=[],
        latest_price=75_000,
        latest_price_timestamp=stale_ts,
    )
    assert result.decision == RiskDecision.REJECTED
    assert len(result.reasons) == 1
    assert "stale" in result.reasons[0]


def test_policy_from_settings_propagates_stale_threshold():
    p = RiskPolicy.from_settings(_settings(stale_price_max_age_seconds=15))
    assert p.stale_price_max_age_seconds == 15
