from datetime import datetime, timedelta, timezone

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
    # 159: AI 주문은 reasoning을 동반해야 한다 — _ai_buy로 교체.
    result = risk.evaluate_order(
        order=_ai_buy(reasons=["live_ai_test"]),
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
        min_ai_confidence         = 0,
        enforce_ai_reasoning      = True,
        ai_rate_limit_window_seconds = 60,
        ai_rate_limit_max_count   = 0,
        max_position_size_pct     = 0.0,
        symbol_whitelist          = "",
        enforce_market_hours      = False,
        global_rate_limit_window_seconds = 60,
        global_rate_limit_max_count      = 0,
        disable_ai_orders                = False,
        max_total_exposure               = 0,
        max_total_exposure_pct           = 0.0,
        max_symbol_exposure_pct          = 0.0,
        auto_stop_consecutive_rejections = 0,
    )
    base.update(overrides)
    ns = SimpleNamespace(**base)
    # 175: symbol_whitelist_set() 메서드를 SimpleNamespace에 부착.
    ns.symbol_whitelist_set = lambda: (
        {s.strip() for s in (ns.symbol_whitelist or "").split(",") if s.strip()}
    )
    return ns


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


# ---------- 158: AI confidence threshold ----------

def _ai_buy(qty: int = 1, confidence: int | None = 80,
             reasons: list[str] | None = None) -> OrderRequest:
    """AI가 만든 주문 시뮬용 — signal_confidence + ai_decision_meta.reasons.
    159 enforcement 통과 위해 reasons 기본값 채움."""
    if reasons is None:
        reasons = ["test_reason"]
    return OrderRequest(
        symbol="005930", side=OrderSide.BUY, quantity=qty,
        signal_confidence=confidence,
        signal_strength=confidence,
        ai_decision_meta={"confidence": confidence, "reasons": list(reasons)},
    )


def test_ai_confidence_below_threshold_rejected():
    risk = RiskManager(RiskPolicy(min_ai_confidence=70))
    result = risk.evaluate_order(
        order=_ai_buy(confidence=50),
        mode=OperationMode.SIMULATION,
        balance=_balance(), positions=[],
        latest_price=75_000,
        requested_by_ai=True,
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("AI signal confidence 50" in r and "min_ai_confidence 70" in r
                for r in result.reasons), result.reasons


def test_ai_confidence_at_threshold_passes():
    """경계값 — 정확히 임계와 같으면 통과 (>= 의미)."""
    risk = RiskManager(RiskPolicy(min_ai_confidence=70))
    result = risk.evaluate_order(
        order=_ai_buy(confidence=70),
        mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(), positions=[],
        latest_price=75_000,
        requested_by_ai=True,
    )
    assert result.decision == RiskDecision.APPROVED


def test_ai_confidence_missing_rejected_when_threshold_set():
    """signal_confidence=None인 AI 주문 — 임계 설정 시 거부 (안전 측)."""
    risk = RiskManager(RiskPolicy(min_ai_confidence=70))
    result = risk.evaluate_order(
        order=_ai_buy(confidence=None),
        mode=OperationMode.SIMULATION,
        balance=_balance(), positions=[],
        latest_price=75_000,
        requested_by_ai=True,
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("missing" in r for r in result.reasons), result.reasons


def test_ai_confidence_check_skipped_when_threshold_zero():
    """기본값 0이면 검사 비활성 — 기존 호출 호환."""
    risk = RiskManager(RiskPolicy(min_ai_confidence=0))
    result = risk.evaluate_order(
        order=_ai_buy(confidence=10),  # 매우 낮아도
        mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(), positions=[],
        latest_price=75_000,
        requested_by_ai=True,
    )
    assert result.decision == RiskDecision.APPROVED


def test_non_ai_orders_unaffected_by_threshold():
    """requested_by_ai=False인 주문은 confidence 임계 검사 자체가 적용 안 됨."""
    risk = RiskManager(RiskPolicy(min_ai_confidence=70))
    result = risk.evaluate_order(
        order=_buy(1),  # confidence 미명시
        mode=OperationMode.SIMULATION,
        balance=_balance(), positions=[],
        latest_price=75_000,
        requested_by_ai=False,
    )
    assert result.decision == RiskDecision.APPROVED


def test_ai_confidence_threshold_combines_with_other_violations():
    """confidence + notional 같이 위반 — 두 reason 모두 누적."""
    risk = RiskManager(RiskPolicy(
        min_ai_confidence=70,
        max_order_notional=100_000,
    ))
    result = risk.evaluate_order(
        order=_ai_buy(qty=10, confidence=50),
        mode=OperationMode.SIMULATION,
        balance=_balance(), positions=[],
        latest_price=75_000,
        requested_by_ai=True,
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("AI signal confidence" in r for r in result.reasons)
    assert any("order notional exceeds" in r for r in result.reasons)


def test_policy_from_settings_propagates_min_ai_confidence():
    p = RiskPolicy.from_settings(_settings(min_ai_confidence=80))
    assert p.min_ai_confidence == 80


def test_policy_from_settings_at_defaults_includes_min_ai_confidence():
    """from_settings default가 dataclass default (0)과 일치 — 회귀 가드."""
    fs   = RiskPolicy.from_settings(_settings())
    bare = RiskPolicy()
    assert fs.min_ai_confidence == bare.min_ai_confidence
    assert fs.min_ai_confidence == 0


# ---------- 159: AI proposal reasoning required ----------

def test_ai_order_with_reasons_passes_reasoning_check():
    """ai_decision_meta.reasons가 채워진 AI 주문은 통과 (다른 가드 통과 가정)."""
    risk = RiskManager(RiskPolicy(enforce_ai_reasoning=True))
    result = risk.evaluate_order(
        order=_ai_buy(reasons=["earnings_beat", "regime_match"]),
        mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(), positions=[],
        latest_price=75_000,
        requested_by_ai=True,
    )
    assert result.decision == RiskDecision.APPROVED


def test_ai_order_without_meta_rejected():
    """ai_decision_meta가 None인 AI 주문 — 거부."""
    risk = RiskManager(RiskPolicy(enforce_ai_reasoning=True))
    order = OrderRequest(
        symbol="005930", side=OrderSide.BUY, quantity=1,
        signal_confidence=80,
        # ai_decision_meta 미명시
    )
    result = risk.evaluate_order(
        order=order, mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(), positions=[], latest_price=75_000,
        requested_by_ai=True,
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("missing reasoning" in r for r in result.reasons)


def test_ai_order_with_empty_reasons_rejected():
    """ai_decision_meta가 있어도 reasons가 빈 list면 거부."""
    risk = RiskManager(RiskPolicy(enforce_ai_reasoning=True))
    order = OrderRequest(
        symbol="005930", side=OrderSide.BUY, quantity=1,
        signal_confidence=80,
        ai_decision_meta={"confidence": 80, "reasons": []},
    )
    result = risk.evaluate_order(
        order=order, mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(), positions=[], latest_price=75_000,
        requested_by_ai=True,
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("missing reasoning" in r for r in result.reasons)


def test_ai_order_with_meta_but_no_reasons_key_rejected():
    """meta가 있지만 reasons key 자체가 없으면 거부 — defensive."""
    risk = RiskManager(RiskPolicy(enforce_ai_reasoning=True))
    order = OrderRequest(
        symbol="005930", side=OrderSide.BUY, quantity=1,
        signal_confidence=80,
        ai_decision_meta={"confidence": 80},  # reasons key 없음
    )
    result = risk.evaluate_order(
        order=order, mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(), positions=[], latest_price=75_000,
        requested_by_ai=True,
    )
    assert result.decision == RiskDecision.REJECTED


def test_non_ai_order_unaffected_by_reasoning_check():
    """requested_by_ai=False면 reasoning 검사 미적용 — 회귀 가드."""
    risk = RiskManager(RiskPolicy(enforce_ai_reasoning=True))
    result = risk.evaluate_order(
        order=_buy(1),  # 일반 운영자 주문
        mode=OperationMode.SIMULATION,
        balance=_balance(), positions=[], latest_price=75_000,
        requested_by_ai=False,
    )
    assert result.decision == RiskDecision.APPROVED


def test_reasoning_check_disabled_by_flag():
    """enforce_ai_reasoning=False면 검사 우회 — backwards-compat 옵션."""
    risk = RiskManager(RiskPolicy(enforce_ai_reasoning=False))
    order = OrderRequest(
        symbol="005930", side=OrderSide.BUY, quantity=1,
        signal_confidence=80,
        # 의도적으로 ai_decision_meta 미명시
    )
    result = risk.evaluate_order(
        order=order, mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(), positions=[], latest_price=75_000,
        requested_by_ai=True,
    )
    assert result.decision == RiskDecision.APPROVED


def test_policy_from_settings_propagates_enforce_ai_reasoning():
    p = RiskPolicy.from_settings(_settings(enforce_ai_reasoning=False))
    assert p.enforce_ai_reasoning is False
    p2 = RiskPolicy.from_settings(_settings(enforce_ai_reasoning=True))
    assert p2.enforce_ai_reasoning is True


def test_policy_default_enforces_ai_reasoning():
    """기본값이 True인지 — 회귀 가드. 운영자가 명시적으로 끄지 않는 한 활성."""
    bare = RiskPolicy()
    assert bare.enforce_ai_reasoning is True

# ---------- 143: stale price detection ----------


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


# ---------- 174: equity-relative position size guard ----------

def test_max_position_size_pct_disabled_by_default():
    """기본값 0이면 검사 비활성 — 회귀 가드."""
    risk = RiskManager(RiskPolicy())  # default
    result = risk.evaluate_order(
        order=_buy(100), mode=OperationMode.SIMULATION,
        balance=_balance(cash=1_000_000),  # 작은 자본
        positions=[], latest_price=75_000,
    )
    # max_order_notional은 100 * 75000 = 7.5M > 1M 거부지만 그건 별개.
    # max_position_size_pct=0이라 본 검사 reasons에 없어야.
    assert not any("equity" in r for r in result.reasons)


def test_position_size_within_pct_of_equity_passes():
    """equity 10M, pct=10%이면 1M까지 허용. 1M 정확히는 통과."""
    risk = RiskManager(RiskPolicy(
        max_order_notional=999_999_999,  # 절대값 가드 우회
        max_position_size_pct=10.0,
    ))
    result = risk.evaluate_order(
        order=_buy(10), mode=OperationMode.SIMULATION,
        balance=_balance(cash=10_000_000),  # equity = 10M
        positions=[], latest_price=100_000,  # notional = 1M = 10%
    )
    assert result.decision == RiskDecision.APPROVED


def test_position_size_exceeds_pct_of_equity_rejected():
    """equity 10M, pct=10% → 1M cap. 1.5M 시도 → 거부."""
    risk = RiskManager(RiskPolicy(
        max_order_notional=999_999_999,
        max_position_size_pct=10.0,
    ))
    result = risk.evaluate_order(
        order=_buy(15), mode=OperationMode.SIMULATION,
        balance=_balance(cash=10_000_000),
        positions=[], latest_price=100_000,  # notional = 1.5M > 1M cap
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("equity" in r and "1500000" in r for r in result.reasons), result.reasons


def test_position_size_pct_scales_with_equity():
    """equity 증가 시 cap 자동 증가 — 절대값 한도와의 차이점."""
    risk = RiskManager(RiskPolicy(
        max_order_notional=999_999_999,
        max_position_size_pct=10.0,
    ))
    # equity 1M → cap 100K. 200K 주문 거부.
    small_eq = risk.evaluate_order(
        order=_buy(2), mode=OperationMode.SIMULATION,
        balance=_balance(cash=1_000_000), positions=[], latest_price=100_000,
    )
    assert small_eq.decision == RiskDecision.REJECTED

    # equity 100M → cap 10M. 같은 200K 주문 통과.
    large_eq = risk.evaluate_order(
        order=_buy(2), mode=OperationMode.SIMULATION,
        balance=_balance(cash=100_000_000), positions=[], latest_price=100_000,
    )
    assert large_eq.decision == RiskDecision.APPROVED


def test_position_size_pct_combines_with_other_violations():
    """pct + notional 같이 위반 — 두 reason 모두 누적."""
    risk = RiskManager(RiskPolicy(
        max_order_notional=100_000,
        max_position_size_pct=5.0,
    ))
    result = risk.evaluate_order(
        order=_buy(10), mode=OperationMode.SIMULATION,
        balance=_balance(cash=1_000_000),  # equity 1M, 5% = 50K cap
        positions=[], latest_price=75_000,  # notional 750K
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("max_order_notional" in r for r in result.reasons)
    assert any("equity" in r for r in result.reasons)


def test_policy_from_settings_propagates_max_position_size_pct():
    p = RiskPolicy.from_settings(_settings(max_position_size_pct=15.0))
    assert p.max_position_size_pct == 15.0


def test_policy_from_settings_default_max_position_size_pct_zero():
    """default 0 — 회귀 가드."""
    p = RiskPolicy.from_settings(_settings())
    assert p.max_position_size_pct == 0.0


# ---------- 175: symbol whitelist ----------

def test_symbol_whitelist_disabled_when_empty():
    """기본 빈 set이면 검사 비활성 — 회귀 가드."""
    risk = RiskManager(RiskPolicy())
    result = risk.evaluate_order(
        order=_buy(1, symbol="random"), mode=OperationMode.SIMULATION,
        balance=_balance(), positions=[], latest_price=100,
    )
    assert not any("whitelist" in r for r in result.reasons)


def test_symbol_in_whitelist_passes():
    risk = RiskManager(RiskPolicy(
        symbol_whitelist=frozenset({"005930", "000660"}),
    ))
    result = risk.evaluate_order(
        order=_buy(1, symbol="005930"), mode=OperationMode.SIMULATION,
        balance=_balance(), positions=[], latest_price=100,
    )
    assert result.decision == RiskDecision.APPROVED


def test_symbol_not_in_whitelist_rejected():
    risk = RiskManager(RiskPolicy(
        symbol_whitelist=frozenset({"005930"}),
    ))
    result = risk.evaluate_order(
        order=_buy(1, symbol="UNKNOWN"), mode=OperationMode.SIMULATION,
        balance=_balance(), positions=[], latest_price=100,
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("'UNKNOWN'" in r and "whitelist" in r for r in result.reasons)


def test_whitelist_combines_with_other_violations():
    """whitelist + notional 같이 위반 — 두 reason 누적."""
    risk = RiskManager(RiskPolicy(
        symbol_whitelist=frozenset({"005930"}),
        max_order_notional=100_000,
    ))
    result = risk.evaluate_order(
        order=_buy(10, symbol="UNKNOWN"), mode=OperationMode.SIMULATION,
        balance=_balance(), positions=[], latest_price=75_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("whitelist" in r for r in result.reasons)
    assert any("max_order_notional" in r for r in result.reasons)


def test_settings_parses_whitelist_from_csv():
    """env 콤마 문자열 → set 파싱."""
    p = RiskPolicy.from_settings(_settings(
        symbol_whitelist="005930, 000660 ,035420",
    ))
    assert p.symbol_whitelist == frozenset({"005930", "000660", "035420"})


def test_settings_default_whitelist_empty():
    p = RiskPolicy.from_settings(_settings())
    assert p.symbol_whitelist == frozenset()


# ---------- 176: trading hours guard ----------

def test_market_hours_guard_disabled_by_default():
    """default False — 회귀 가드. 24/7 모든 주문 통과."""
    risk = RiskManager(RiskPolicy())
    result = risk.evaluate_order(
        order=_buy(1), mode=OperationMode.SIMULATION,
        balance=_balance(), positions=[], latest_price=100,
    )
    assert not any("market is closed" in r for r in result.reasons)


def test_is_market_open_helper():
    """_is_market_open helper 직접 테스트 — 결정적 시각 인자."""
    from app.risk.risk_manager import _is_market_open

    # KST 평일 10:00 — 장중.
    weekday_morning_kst = datetime(2026, 5, 6, 10, 0,
                                    tzinfo=timezone(timedelta(hours=9)))
    assert _is_market_open(weekday_morning_kst) is True

    # KST 평일 16:00 — 장 종료 후.
    weekday_evening_kst = datetime(2026, 5, 6, 16, 0,
                                    tzinfo=timezone(timedelta(hours=9)))
    assert _is_market_open(weekday_evening_kst) is False

    # KST 평일 08:00 — 장 시작 전.
    weekday_dawn_kst = datetime(2026, 5, 6, 8, 0,
                                 tzinfo=timezone(timedelta(hours=9)))
    assert _is_market_open(weekday_dawn_kst) is False

    # 일요일 — 무조건 closed.
    sunday_noon = datetime(2026, 5, 10, 12, 0,
                            tzinfo=timezone(timedelta(hours=9)))
    assert sunday_noon.weekday() == 6
    assert _is_market_open(sunday_noon) is False

    # 토요일도 closed.
    saturday_noon = datetime(2026, 5, 9, 12, 0,
                              tzinfo=timezone(timedelta(hours=9)))
    assert saturday_noon.weekday() == 5
    assert _is_market_open(saturday_noon) is False

    # boundary: 09:00 정확히 — open.
    open_boundary = datetime(2026, 5, 6, 9, 0,
                              tzinfo=timezone(timedelta(hours=9)))
    assert _is_market_open(open_boundary) is True

    # boundary: 15:30 정확히 — closed (< 의미라 같으면 closed).
    close_boundary = datetime(2026, 5, 6, 15, 30,
                               tzinfo=timezone(timedelta(hours=9)))
    assert _is_market_open(close_boundary) is False


def test_naive_datetime_treated_as_utc_for_market_hours():
    """naive datetime은 UTC로 가정 — KST 변환 후 판정."""
    from app.risk.risk_manager import _is_market_open

    # UTC 02:00 = KST 11:00 — 장중.
    utc_2am = datetime(2026, 5, 6, 2, 0)  # naive → UTC 가정
    assert _is_market_open(utc_2am) is True

    # UTC 10:00 = KST 19:00 — 장 종료 후.
    utc_10am = datetime(2026, 5, 6, 10, 0)  # naive
    assert _is_market_open(utc_10am) is False


def test_policy_from_settings_propagates_market_hours():
    p = RiskPolicy.from_settings(_settings(enforce_market_hours=True))
    assert p.enforce_market_hours is True
    p2 = RiskPolicy.from_settings(_settings(enforce_market_hours=False))
    assert p2.enforce_market_hours is False


# ---------- 178: AI kill-switch ----------

def test_ai_kill_switch_default_disabled():
    """default False — 회귀 가드. AI 주문 정상 통과."""
    risk = RiskManager(RiskPolicy())
    result = risk.evaluate_order(
        order=_ai_buy(confidence=80),
        mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(), positions=[],
        latest_price=75_000, requested_by_ai=True,
    )
    assert result.decision == RiskDecision.APPROVED


def test_ai_kill_switch_blocks_ai_orders():
    risk = RiskManager(RiskPolicy(disable_ai_orders=True))
    result = risk.evaluate_order(
        order=_ai_buy(confidence=80),
        mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(), positions=[],
        latest_price=75_000, requested_by_ai=True,
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("AI orders are disabled" in r for r in result.reasons)


def test_ai_kill_switch_does_not_block_strategy_orders():
    """non-AI 주문은 disable_ai_orders 영향 X — 회귀 가드."""
    risk = RiskManager(RiskPolicy(disable_ai_orders=True))
    result = risk.evaluate_order(
        order=_buy(1), mode=OperationMode.SIMULATION,
        balance=_balance(), positions=[],
        latest_price=75_000, requested_by_ai=False,
    )
    assert result.decision == RiskDecision.APPROVED


def test_ai_kill_switch_short_circuits_other_checks():
    """emergency_stop과 같은 hard reject — 다른 위반 사유 누적 안 함."""
    risk = RiskManager(RiskPolicy(
        disable_ai_orders=True,
        max_order_notional=100,  # 위반할 만한 한도
    ))
    result = risk.evaluate_order(
        order=_ai_buy(qty=10, confidence=80),
        mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(), positions=[],
        latest_price=75_000, requested_by_ai=True,
    )
    assert result.decision == RiskDecision.REJECTED
    assert len(result.reasons) == 1
    assert "AI orders are disabled" in result.reasons[0]


def test_set_ai_disabled_runtime_toggle():
    """set_ai_disabled로 in-memory 토글 — emergency_stop 패턴."""
    risk = RiskManager(RiskPolicy())
    assert risk.policy.disable_ai_orders is False

    risk.set_ai_disabled(True)
    assert risk.policy.disable_ai_orders is True
    result = risk.evaluate_order(
        order=_ai_buy(confidence=80),
        mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(), positions=[],
        latest_price=75_000, requested_by_ai=True,
    )
    assert result.decision == RiskDecision.REJECTED

    risk.set_ai_disabled(False)
    result2 = risk.evaluate_order(
        order=_ai_buy(confidence=80),
        mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(), positions=[],
        latest_price=75_000, requested_by_ai=True,
    )
    assert result2.decision == RiskDecision.APPROVED


def test_emergency_stop_takes_priority_over_ai_kill_switch():
    """emergency_stop이 더 상위 — AI든 non-AI든 모두 차단."""
    risk = RiskManager(RiskPolicy(disable_ai_orders=True))
    risk.set_emergency_stop(True)
    # AI 주문.
    result = risk.evaluate_order(
        order=_ai_buy(confidence=80),
        mode=OperationMode.VIRTUAL_AI_EXECUTION,
        balance=_balance(), positions=[],
        latest_price=75_000, requested_by_ai=True,
    )
    assert result.decision == RiskDecision.REJECTED
    # emergency_stop reason이 surface (AI kill-switch가 아님 — 더 상위 가드).
    assert any("emergency stop" in r for r in result.reasons)


def test_policy_from_settings_propagates_disable_ai_orders():
    p = RiskPolicy.from_settings(_settings(disable_ai_orders=True))
    assert p.disable_ai_orders is True


# ---------- 179: total exposure cap ----------

def _pos(symbol: str, qty: int, price: int) -> Position:
    return Position(symbol=symbol, quantity=qty, avg_price=price, market_price=price)


def test_total_exposure_cap_disabled_by_default():
    risk = RiskManager(RiskPolicy())
    result = risk.evaluate_order(
        order=_buy(1), mode=OperationMode.SIMULATION,
        balance=_balance(), positions=[
            _pos("A", 100, 1000),  # 100K 노출
        ],
        latest_price=100,
    )
    # max_total_exposure=0이라 검사 안 함.
    assert not any("total exposure" in r for r in result.reasons)


def test_total_exposure_absolute_cap_enforced():
    """max_total_exposure 절대값 — 누적 한도 초과 거부."""
    risk = RiskManager(RiskPolicy(
        max_total_exposure=300_000,
        max_symbol_exposure=999_999_999,  # 종목별 가드 우회
        max_order_notional=999_999_999,
        max_positions=999,
    ))
    # 기존 200K 노출 + 신규 200K → 400K > 300K cap.
    result = risk.evaluate_order(
        order=_buy(2), mode=OperationMode.SIMULATION,
        balance=_balance(), positions=[
            _pos("A", 2, 100_000),  # 200K
        ],
        latest_price=100_000,  # 신규 BUY 200K
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("total exposure" in r and "max_total_exposure" in r
                for r in result.reasons)


def test_total_exposure_pct_cap_enforced():
    """equity 대비 % 한도 — 자본 1M, 30% = 300K cap. 기존 200K + 신규 200K = 400K 거부."""
    risk = RiskManager(RiskPolicy(
        max_total_exposure_pct=30.0,
        max_symbol_exposure=999_999_999,
        max_order_notional=999_999_999,
        max_positions=999,
    ))
    result = risk.evaluate_order(
        order=_buy(2), mode=OperationMode.SIMULATION,
        balance=_balance(cash=1_000_000),
        positions=[_pos("A", 2, 100_000)],  # 200K
        latest_price=100_000,
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("total exposure" in r and "of equity" in r for r in result.reasons)


def test_total_exposure_within_caps_passes():
    risk = RiskManager(RiskPolicy(
        max_total_exposure=1_000_000,
        max_total_exposure_pct=80.0,
        max_symbol_exposure=999_999_999,
        max_order_notional=999_999_999,
        max_positions=999,
    ))
    result = risk.evaluate_order(
        order=_buy(2), mode=OperationMode.SIMULATION,
        balance=_balance(cash=1_000_000),  # equity 1M, 80% = 800K cap
        positions=[_pos("A", 2, 100_000)],  # 200K
        latest_price=100_000,  # 신규 200K → total 400K < 800K + < 1M abs cap
    )
    assert result.decision == RiskDecision.APPROVED


def test_total_exposure_only_buy_side_checked():
    """SELL은 노출 감소 — 검사 우회 (회귀 가드)."""
    risk = RiskManager(RiskPolicy(
        max_total_exposure=100_000,  # 작은 한도
        max_symbol_exposure=999_999_999,
        max_order_notional=999_999_999,
    ))
    # SELL — total exposure 검사 무관.
    result = risk.evaluate_order(
        order=OrderRequest(symbol="A", side=OrderSide.SELL, quantity=2),
        mode=OperationMode.SIMULATION,
        balance=_balance(), positions=[_pos("A", 5, 100_000)],  # 500K (한도 초과)
        latest_price=100_000,
    )
    # SELL이라 노출 감소 — total exposure reason 없음.
    assert not any("total exposure" in r for r in result.reasons)


def test_total_exposure_combines_with_other_violations():
    risk = RiskManager(RiskPolicy(
        max_total_exposure=100_000,
        max_order_notional=50_000,
    ))
    result = risk.evaluate_order(
        order=_buy(2), mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=[_pos("A", 1, 100_000)],
        latest_price=100_000,  # notional 200K + total 300K
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("max_order_notional" in r for r in result.reasons)
    assert any("total exposure" in r for r in result.reasons)


def test_policy_from_settings_propagates_total_exposure_caps():
    p = RiskPolicy.from_settings(_settings(
        max_total_exposure=5_000_000,
        max_total_exposure_pct=50.0,
    ))
    assert p.max_total_exposure == 5_000_000
    assert p.max_total_exposure_pct == 50.0


# ---------- 181: symbol exposure pct ----------

def test_symbol_exposure_pct_disabled_by_default():
    risk = RiskManager(RiskPolicy())
    result = risk.evaluate_order(
        order=_buy(1), mode=OperationMode.SIMULATION,
        balance=_balance(), positions=[],
        latest_price=100,
    )
    assert not any("equity" in r and "symbol" in r for r in result.reasons)


def test_symbol_exposure_pct_enforced():
    """equity 1M, pct=20% → 단일 종목 200K cap. 250K 시도 거부."""
    risk = RiskManager(RiskPolicy(
        max_symbol_exposure_pct=20.0,
        max_symbol_exposure=999_999_999,
        max_order_notional=999_999_999,
        max_positions=999,
    ))
    result = risk.evaluate_order(
        order=_buy(2), mode=OperationMode.SIMULATION,
        balance=_balance(cash=1_000_000),
        positions=[_pos("005930", 1, 100_000)],  # 100K 기존
        latest_price=150_000,  # 신규 150K → 종목 합 250K > 200K cap
    )
    assert result.decision == RiskDecision.REJECTED
    assert any("symbol exposure" in r and "of equity" in r for r in result.reasons)


def test_symbol_exposure_pct_within_cap_passes():
    risk = RiskManager(RiskPolicy(
        max_symbol_exposure_pct=20.0,
        max_symbol_exposure=999_999_999,
        max_order_notional=999_999_999,
    ))
    # equity 10M, 20% = 2M cap. 합 1M 통과.
    result = risk.evaluate_order(
        order=_buy(1), mode=OperationMode.SIMULATION,
        balance=_balance(cash=10_000_000),
        positions=[_pos("005930", 1, 500_000)],
        latest_price=500_000,
    )
    assert result.decision == RiskDecision.APPROVED


def test_symbol_exposure_pct_only_buy_side():
    """SELL은 검사 우회 — 회귀 가드."""
    risk = RiskManager(RiskPolicy(
        max_symbol_exposure_pct=1.0,  # 매우 작은 한도
        max_symbol_exposure=999_999_999,
    ))
    result = risk.evaluate_order(
        order=OrderRequest(symbol="005930", side=OrderSide.SELL, quantity=10),
        mode=OperationMode.SIMULATION,
        balance=_balance(),
        positions=[_pos("005930", 100, 100_000)],
        latest_price=100_000,
    )
    assert not any("symbol exposure" in r and "of equity" in r for r in result.reasons)


def test_policy_from_settings_propagates_symbol_exposure_pct():
    p = RiskPolicy.from_settings(_settings(max_symbol_exposure_pct=15.0))
    assert p.max_symbol_exposure_pct == 15.0


def test_default_max_symbol_exposure_pct_zero():
    p = RiskPolicy.from_settings(_settings())
    assert p.max_symbol_exposure_pct == 0.0
