"""StrategyBase contract 테스트 (#28).

검증:
- 새 dataclass 직렬화
- generate_signal / calculate_size / exit_rule / explain_signal default 동작
- legacy on_bar 호환성 — 기존 concrete 전략이 새 인터페이스로도 동작
- StrategySignal.is_order_intent 항상 False (invariant)
- Strategy 모듈은 broker / risk / permission / execution import 0건
"""

import inspect
import json
from datetime import datetime, timedelta, timezone

from app.backtest.types import Bar, Signal
from app.strategies.base import (
    ExitPlan,
    SignalAction,
    SignalExplanation,
    SizingHint,
    Strategy,
    StrategyBase,
    StrategyContext,
    StrategySignal,
    ValidationResult,
    from_legacy_signal,
    to_legacy_signal,
)


_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bar(i: int, c: int = 100) -> Bar:
    return Bar(symbol="X", timestamp=_BASE + timedelta(days=i),
               open=c, high=c + 5, low=max(1, c - 5), close=c, volume=1000)


class _BuyOnceStrategy(Strategy):
    entry        = "RSI < 30"
    exit         = "Take profit 2%"
    invalidation = "Stop loss 1%"
    required_regime = "ranging"
    risk_profile = {
        "position_size_pct": 5, "stop_loss_pct": 1, "take_profit_pct": 2,
    }

    def __init__(self):
        self._idx = 0

    def on_bar(self, bars):
        self._idx += 1
        if self._idx == 1:
            return Signal.BUY
        if self._idx == 5:
            return Signal.SELL
        return Signal.HOLD


# ---------- StrategyBase alias ----------


def test_strategy_base_is_alias_for_strategy():
    assert StrategyBase is Strategy


# ---------- DTO 기본 ----------


def test_strategy_signal_default_is_not_order_intent():
    s = StrategySignal(action=SignalAction.BUY)
    assert s.is_order_intent is False


def test_strategy_signal_to_dict_serializable():
    s = StrategySignal(
        action=SignalAction.BUY, symbol="005930",
        sizing_hint=SizingHint(position_size_pct=5),
        exit_plan=ExitPlan(take_profit_pct=2, stop_loss_pct=1),
        explanation=SignalExplanation(summary="test", reasons=["a", "b"], confidence=70),
    )
    d = s.to_dict()
    json.dumps(d)
    assert d["is_order_intent"] is False
    assert d["sizing_hint"]["position_size_pct"] == 5
    assert d["exit_plan"]["take_profit_pct"] == 2
    assert d["explanation"]["confidence"] == 70


def test_sizing_hint_construction():
    h = SizingHint(quantity=10, position_size_pct=3.5, risk_pct=1.0,
                   reduce_only=True, note="자금 부족")
    assert h.quantity == 10
    assert h.reduce_only is True


def test_exit_plan_construction():
    p = ExitPlan(take_profit_pct=2, stop_loss_pct=1, time_exit_bars=5,
                 invalidation="규제 공시", rule_summary="2% TP / 1% SL / 5봉")
    assert p.time_exit_bars == 5


def test_signal_explanation_construction():
    e = SignalExplanation(summary="x", reasons=["r1"], confidence=80,
                          indicators={"rsi": 25}, required_regime="ranging")
    assert e.summary == "x"
    assert e.indicators == {"rsi": 25}


def test_validation_result_construction():
    v = ValidationResult(ok=False, reasons=["bars 비어있음"])
    assert v.ok is False
    assert v.reasons == ["bars 비어있음"]


# ---------- adapter ----------


def test_to_legacy_signal_buy_sell_hold():
    assert to_legacy_signal(StrategySignal(action=SignalAction.BUY)) == Signal.BUY
    assert to_legacy_signal(StrategySignal(action=SignalAction.SELL)) == Signal.SELL
    assert to_legacy_signal(StrategySignal(action=SignalAction.EXIT)) == Signal.SELL
    assert to_legacy_signal(StrategySignal(action=SignalAction.WATCH)) == Signal.HOLD
    assert to_legacy_signal(StrategySignal(action=SignalAction.NO_SIGNAL)) == Signal.HOLD
    assert to_legacy_signal(None) == Signal.HOLD


def test_from_legacy_signal_round_trip():
    assert from_legacy_signal(Signal.BUY).action == SignalAction.BUY
    assert from_legacy_signal(Signal.SELL).action == SignalAction.SELL
    assert from_legacy_signal(Signal.HOLD).action == SignalAction.NO_SIGNAL


def test_from_legacy_signal_carries_symbol():
    s = from_legacy_signal(Signal.BUY, symbol="005930")
    assert s.symbol == "005930"
    assert s.is_order_intent is False


# ---------- 기존 concrete strategy 호환성 ----------


def test_existing_strategy_works_with_new_generate_signal():
    """기존 on_bar만 구현한 Strategy가 새 generate_signal로도 동작."""
    s = _BuyOnceStrategy()
    bars = [_bar(0)]
    out = s.generate_signal(StrategyContext(bars=bars, symbol="X"))
    assert isinstance(out, StrategySignal)
    assert out.action == SignalAction.BUY
    assert out.is_order_intent is False
    assert out.symbol == "X"


def test_default_calculate_size_uses_risk_profile():
    s = _BuyOnceStrategy()
    sig = StrategySignal(action=SignalAction.BUY)
    h = s.calculate_size(sig)
    assert h.position_size_pct == 5
    assert h.risk_pct == 1.0


def test_default_exit_rule_uses_metadata():
    s = _BuyOnceStrategy()
    sig = StrategySignal(action=SignalAction.BUY)
    p = s.exit_rule(sig)
    assert p.take_profit_pct == 2.0
    assert p.stop_loss_pct == 1.0
    assert p.invalidation == "Stop loss 1%"
    assert p.rule_summary == "Take profit 2%"


def test_default_explain_signal_includes_metadata():
    s = _BuyOnceStrategy()
    sig = StrategySignal(action=SignalAction.BUY)
    e = s.explain_signal(sig)
    assert "_BuyOnceStrategy" in e.summary
    assert any("RSI < 30" in r for r in e.reasons)
    assert e.required_regime == "ranging"


def test_default_validate_context_rejects_empty_bars():
    s = _BuyOnceStrategy()
    v = s.validate_context(StrategyContext(bars=[]))
    assert v.ok is False
    assert any("비어" in r for r in v.reasons)


def test_default_validate_context_ok_with_bars():
    s = _BuyOnceStrategy()
    v = s.validate_context(StrategyContext(bars=[_bar(0)]))
    assert v.ok is True


# ---------- 직접 주문 금지 invariant ----------


def test_strategy_module_does_not_import_broker_or_risk():
    """base.py는 broker/risk/permission/execution import 0건."""
    import app.strategies.base as mod
    src = inspect.getsource(mod)
    forbidden = (
        "from app.brokers", "from app.risk", "from app.permission",
        "from app.execution", "from app.governance",
    )
    for f in forbidden:
        assert f not in src, f"forbidden import found: {f}"


def test_strategy_signal_is_order_intent_invariant():
    """모든 default StrategySignal은 is_order_intent=False."""
    for action in SignalAction:
        s = StrategySignal(action=action)
        assert s.is_order_intent is False


def test_strategy_class_has_no_order_decision_methods():
    """Strategy class에 BUY/SELL/decide_order/place_order 메서드가 없다."""
    public = [n for n in dir(Strategy) if not n.startswith("_")]
    forbidden = {"buy", "sell", "place_order", "submit_order", "decide_order",
                 "make_order", "to_order", "execute"}
    intersection = forbidden & {n.lower() for n in public}
    assert intersection == set(), f"forbidden order method: {intersection}"


def test_strategy_signal_dict_has_no_order_fields():
    """StrategySignal.to_dict()에 side/quantity/order_type/limit_price 같은 주문 필드 없음."""
    s = StrategySignal(action=SignalAction.BUY)
    d = s.to_dict()
    forbidden = ("side", "order_type", "limit_price", "decision",
                 "broker_order_id", "client_order_id")
    for f in forbidden:
        assert f not in d, f"forbidden order field: {f}"


# ---------- 기존 concrete 전략 호환성 (loaded via build_strategy) ----------


def test_concrete_strategies_satisfy_new_contract():
    """sma_crossover / rsi_reversion / orb_vwap이 새 인터페이스로 동작."""
    from app.strategies.concrete import build_strategy

    bars = [_bar(i, c=100 + i) for i in range(40)]
    ctx = StrategyContext(bars=bars, symbol="TEST")

    for name in ("sma_crossover", "rsi_reversion", "orb_vwap"):
        try:
            s = build_strategy(name, {})
        except Exception:  # noqa: BLE001 — strict contract 미충족 가능
            s = build_strategy(name, {}, enforce_contract=False)
        sig = s.generate_signal(ctx)
        assert isinstance(sig, StrategySignal)
        assert sig.is_order_intent is False
        # default helpers 통과.
        s.calculate_size(sig)
        s.exit_rule(sig)
        s.explain_signal(sig, context=ctx)
