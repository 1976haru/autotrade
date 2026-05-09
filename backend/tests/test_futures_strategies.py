"""#49: Futures strategy contract + mock strategy tests.

Coverage:
- `FuturesStrategyBase` is ABC; 주식 `Strategy` / `StrategyBase`를 *상속하지
  않는다* (MRO 분리).
- `FuturesSignal.is_order_intent = True`로 만들면 ValueError (dataclass 가드).
- `FuturesContractSizingHint.contracts > 1`이면 ValueError (mock phase 가드).
- 3개 mock 전략의 결정론적 동작:
  - `FuturesTrendFollowingStrategy`: 상승 → OPEN_LONG / 하락 → OPEN_SHORT /
                                       부족 → WATCH / 만기 임박 → WATCH + rollover
  - `FuturesVolatilityBreakoutStrategy`: 상단 돌파 → OPEN_LONG / 하단 돌파 →
                                          OPEN_SHORT / 고변동성 → REDUCE_SIZE
  - `FuturesHedgeStrategy`: |exposure| ≥ 임계 → HEDGE / 미만 → NO_SIGNAL
- 정적 가드: futures.strategies.{base,mock_strategies} 모듈은 broker /
  OrderExecutor / route_order import 0건
- 자동 롤오버 *주문* 발신 0건 — helper들은 plan dataclass만 반환
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.backtest.types import Bar
from app.futures.strategies import (
    FuturesContractSizingHint,
    FuturesExitPlan,
    FuturesRolloverPlan,
    FuturesSignal,
    FuturesSignalAction,
    FuturesSignalExplanation,
    FuturesStrategyBase,
    FuturesStrategyContext,
)
from app.futures.strategies.mock_strategies import (
    FuturesHedgeStrategy,
    FuturesTrendFollowingStrategy,
    FuturesVolatilityBreakoutStrategy,
)
from app.strategies.base import Strategy


_KST = timezone(timedelta(hours=9))


def _bar(ts: datetime, close: int, *, symbol: str = "KOSPI200_2503") -> Bar:
    return Bar(symbol=symbol, timestamp=ts, open=close, high=close,
                low=close, close=close, volume=100)


def _bars_uptrend(n: int = 30, *, start: int = 1_000_000, step: int = 1000) -> list[Bar]:
    base = datetime(2025, 1, 1, tzinfo=_KST)
    return [_bar(base + timedelta(minutes=i), start + i * step) for i in range(n)]


def _bars_downtrend(n: int = 30, *, start: int = 1_030_000, step: int = -1000) -> list[Bar]:
    base = datetime(2025, 1, 1, tzinfo=_KST)
    return [_bar(base + timedelta(minutes=i), start + i * step) for i in range(n)]


def _bars_flat(n: int = 30, *, value: int = 1_000_000) -> list[Bar]:
    base = datetime(2025, 1, 1, tzinfo=_KST)
    return [_bar(base + timedelta(minutes=i), value) for i in range(n)]


# ====================================================================
# 1. ABC separation — futures NOT inheriting from stock Strategy
# ====================================================================


def test_futures_strategy_base_does_not_inherit_stock_strategy():
    """주식 `Strategy` / `StrategyBase`와 선물 `FuturesStrategyBase`는 분리된 계층."""
    assert Strategy not in FuturesStrategyBase.__mro__, (
        "FuturesStrategyBase must NOT inherit from stock Strategy — "
        "futures and equity strategies are intentionally separate ABCs."
    )
    assert FuturesStrategyBase not in Strategy.__mro__


def test_mock_strategies_are_subclasses_of_futures_base_only():
    """3개 mock 전략은 `FuturesStrategyBase`만 상속."""
    for cls in (
        FuturesTrendFollowingStrategy,
        FuturesVolatilityBreakoutStrategy,
        FuturesHedgeStrategy,
    ):
        assert issubclass(cls, FuturesStrategyBase)
        assert not issubclass(cls, Strategy), (
            f"{cls.__name__} must not subclass stock Strategy"
        )


# ====================================================================
# 2. FuturesSignal invariants
# ====================================================================


def test_signal_rejects_is_order_intent_true():
    """`is_order_intent=True`로 만들면 즉시 ValueError — Strategy는 *추천*만."""
    with pytest.raises(ValueError, match="is_order_intent"):
        FuturesSignal(
            action=FuturesSignalAction.OPEN_LONG,
            is_order_intent=True,
        )


def test_signal_default_is_advisory():
    sig = FuturesSignal(action=FuturesSignalAction.OPEN_LONG)
    assert sig.is_order_intent is False
    d = sig.to_dict()
    assert d["is_order_intent"] is False
    assert d["action"] == "OPEN_LONG"


# ====================================================================
# 3. FuturesContractSizingHint invariants
# ====================================================================


def test_sizing_hint_rejects_more_than_one_contract():
    """본 PR mock phase: contracts > 1이면 ValueError."""
    with pytest.raises(ValueError, match="contracts must be"):
        FuturesContractSizingHint(contracts=2)


def test_sizing_hint_rejects_negative_contracts():
    with pytest.raises(ValueError, match="contracts must be"):
        FuturesContractSizingHint(contracts=-1)


def test_sizing_hint_allows_zero_or_one():
    """0 (close-only) / 1 (max mock 권장) 둘 다 허용."""
    FuturesContractSizingHint(contracts=0)
    FuturesContractSizingHint(contracts=1, risk_pct_of_equity=0.5)


# ====================================================================
# 4. FuturesTrendFollowingStrategy
# ====================================================================


def test_trend_following_uptrend_emits_open_long():
    s = FuturesTrendFollowingStrategy(fast_window=5, slow_window=20)
    ctx = FuturesStrategyContext(
        bars=_bars_uptrend(30),
        contract="KOSPI200_2503",
        account_equity=10_000_000,
    )
    sig = s.generate_signal(ctx)
    assert sig.action == FuturesSignalAction.OPEN_LONG
    assert sig.contract == "KOSPI200_2503"
    assert sig.is_order_intent is False
    assert sig.contract_sizing is not None
    assert sig.contract_sizing.contracts == 1
    assert sig.exit_plan is not None
    assert sig.explanation is not None


def test_trend_following_downtrend_emits_open_short():
    s = FuturesTrendFollowingStrategy(fast_window=5, slow_window=20)
    ctx = FuturesStrategyContext(
        bars=_bars_downtrend(30),
        contract="KOSPI200_2503",
        account_equity=10_000_000,
    )
    sig = s.generate_signal(ctx)
    assert sig.action == FuturesSignalAction.OPEN_SHORT
    assert sig.is_order_intent is False


def test_trend_following_insufficient_bars_emits_watch():
    s = FuturesTrendFollowingStrategy(fast_window=5, slow_window=20)
    ctx = FuturesStrategyContext(bars=_bars_uptrend(10), contract="KOSPI200_2503")
    sig = s.generate_signal(ctx)
    assert sig.action == FuturesSignalAction.WATCH
    assert sig.contract_sizing is None  # WATCH는 sizing 미부여
    assert "insufficient bars" in (sig.explanation.summary if sig.explanation else "")


def test_trend_following_flat_emits_watch():
    s = FuturesTrendFollowingStrategy(fast_window=5, slow_window=20)
    ctx = FuturesStrategyContext(bars=_bars_flat(30), contract="KOSPI200_2503")
    sig = s.generate_signal(ctx)
    assert sig.action == FuturesSignalAction.WATCH


def test_trend_following_suppresses_signal_near_expiry():
    """만기 임박 (≤ 5일) 시 신규 진입을 WATCH로 강등 + rollover plan carry."""
    s = FuturesTrendFollowingStrategy(fast_window=5, slow_window=20)
    bars = _bars_uptrend(30)
    expiry = bars[-1].timestamp + timedelta(days=2)  # 2일 후 만기
    ctx = FuturesStrategyContext(
        bars=bars, contract="KOSPI200_2503", expiry=expiry,
    )
    sig = s.generate_signal(ctx)
    assert sig.action == FuturesSignalAction.WATCH
    assert sig.rollover is not None
    assert sig.rollover.close_contract == "KOSPI200_2503"
    assert sig.rollover.days_to_expiry == 2


# ====================================================================
# 5. FuturesVolatilityBreakoutStrategy
# ====================================================================


def test_breakout_inside_band_emits_watch():
    """flat data → 변동성 0 + 종가 == 평균 → 채널 안 → WATCH."""
    s = FuturesVolatilityBreakoutStrategy(lookback=20, band_k=2.0)
    ctx = FuturesStrategyContext(bars=_bars_flat(30), contract="KOSPI200_2503")
    sig = s.generate_signal(ctx)
    assert sig.action == FuturesSignalAction.WATCH


def test_breakout_high_close_emits_open_long():
    """상승 추세 + 강한 마지막 봉 → 상단 돌파 → OPEN_LONG."""
    base = datetime(2025, 1, 1, tzinfo=_KST)
    bars = [_bar(base + timedelta(minutes=i), 1_000_000) for i in range(20)]
    bars.append(_bar(base + timedelta(minutes=20), 1_500_000))  # 큰 돌파
    s = FuturesVolatilityBreakoutStrategy(
        lookback=20, band_k=2.0, max_volatility_pct=200.0,  # 임계 우회
    )
    ctx = FuturesStrategyContext(bars=bars, contract="KOSPI200_2503")
    sig = s.generate_signal(ctx)
    assert sig.action == FuturesSignalAction.OPEN_LONG
    assert sig.is_order_intent is False
    assert sig.contract_sizing is not None
    assert sig.contract_sizing.contracts == 1


def test_breakout_low_close_emits_open_short():
    base = datetime(2025, 1, 1, tzinfo=_KST)
    bars = [_bar(base + timedelta(minutes=i), 1_000_000) for i in range(20)]
    bars.append(_bar(base + timedelta(minutes=20), 500_000))  # 큰 하락
    s = FuturesVolatilityBreakoutStrategy(
        lookback=20, band_k=2.0, max_volatility_pct=200.0,
    )
    ctx = FuturesStrategyContext(bars=bars, contract="KOSPI200_2503")
    sig = s.generate_signal(ctx)
    assert sig.action == FuturesSignalAction.OPEN_SHORT


def test_breakout_high_volatility_emits_reduce_size():
    """변동성이 임계 초과면 REDUCE_SIZE (contract_sizing 없음)."""
    base = datetime(2025, 1, 1, tzinfo=_KST)
    # 큰 변동성 — 종가가 1,000,000 ↔ 2,000,000 사이에서 진동.
    closes = [1_000_000, 2_000_000] * 15
    bars = [_bar(base + timedelta(minutes=i), c) for i, c in enumerate(closes)]
    s = FuturesVolatilityBreakoutStrategy(
        lookback=20, band_k=2.0, max_volatility_pct=5.0,
    )
    ctx = FuturesStrategyContext(bars=bars, contract="KOSPI200_2503")
    sig = s.generate_signal(ctx)
    assert sig.action == FuturesSignalAction.REDUCE_SIZE
    assert sig.contract_sizing is None  # 진입 권장 없음
    assert "volatility" in (sig.explanation.summary if sig.explanation else "").lower()


def test_breakout_insufficient_bars_emits_watch():
    s = FuturesVolatilityBreakoutStrategy(lookback=20, band_k=2.0)
    ctx = FuturesStrategyContext(bars=_bars_uptrend(10), contract="KOSPI200_2503")
    sig = s.generate_signal(ctx)
    assert sig.action == FuturesSignalAction.WATCH


# ====================================================================
# 6. FuturesHedgeStrategy
# ====================================================================


def test_hedge_no_exposure_emits_no_signal():
    s = FuturesHedgeStrategy(min_exposure_krw=5_000_000)
    ctx = FuturesStrategyContext(
        bars=_bars_flat(5), contract="KOSPI200_2503",
        equity_exposure_krw=1_000_000,  # 임계 미만
    )
    sig = s.generate_signal(ctx)
    assert sig.action == FuturesSignalAction.NO_SIGNAL


def test_hedge_positive_exposure_emits_hedge():
    s = FuturesHedgeStrategy(min_exposure_krw=5_000_000)
    ctx = FuturesStrategyContext(
        bars=_bars_flat(5), contract="KOSPI200_2503",
        equity_exposure_krw=20_000_000,
    )
    sig = s.generate_signal(ctx)
    assert sig.action == FuturesSignalAction.HEDGE
    assert sig.contract_sizing is not None
    assert sig.contract_sizing.contracts == 1
    assert sig.is_order_intent is False
    assert "SHORT" in (sig.explanation.summary if sig.explanation else "") \
        or any("SHORT" in r for r in (sig.explanation.reasons if sig.explanation else []))


def test_hedge_negative_exposure_also_emits_hedge():
    """이미 short 포지션 (음의 노출) → LONG 헤지 advisory."""
    s = FuturesHedgeStrategy(min_exposure_krw=5_000_000)
    ctx = FuturesStrategyContext(
        bars=_bars_flat(5), contract="KOSPI200_2503",
        equity_exposure_krw=-15_000_000,
    )
    sig = s.generate_signal(ctx)
    assert sig.action == FuturesSignalAction.HEDGE


def test_hedge_no_exposure_field_emits_no_signal():
    """`equity_exposure_krw=None`이면 0으로 처리 → NO_SIGNAL."""
    s = FuturesHedgeStrategy(min_exposure_krw=5_000_000)
    ctx = FuturesStrategyContext(bars=_bars_flat(5), contract="KOSPI200_2503")
    sig = s.generate_signal(ctx)
    assert sig.action == FuturesSignalAction.NO_SIGNAL


# ====================================================================
# 7. validate() default
# ====================================================================


def test_validate_default_rejects_empty_bars():
    s = FuturesTrendFollowingStrategy()
    ctx = FuturesStrategyContext(bars=[], contract="KOSPI200_2503")
    res = s.validate(ctx)
    assert res.ok is False
    assert any("bars" in r for r in res.reasons)


def test_validate_default_accepts_non_empty_bars():
    s = FuturesTrendFollowingStrategy()
    ctx = FuturesStrategyContext(bars=_bars_flat(5), contract="KOSPI200_2503")
    res = s.validate(ctx)
    assert res.ok is True


# ====================================================================
# 8. Static guards — no broker / executor / route_order imports
# ====================================================================


def test_strategies_base_does_not_import_broker_or_executor():
    import app.futures.strategies.base as mod
    src_path = mod.__file__
    assert src_path is not None
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    forbidden = (
        "from app.brokers",
        "import app.brokers",
        "from app.execution.executor",
        "from app.execution.order_router",
        "from app.futures.mock",
        "from app.futures.base",  # 직접 broker ABC도 import 안 함
        "broker.place_order(",
        ".place_order(",
        "broker.cancel_order(",
        ".cancel_order(",
        "force_liquidate_if_needed(",
    )
    for snippet in forbidden:
        assert snippet not in src, (
            f"app.futures.strategies.base must not contain '{snippet}' — "
            "strategies are pure decision functions."
        )


def test_mock_strategies_does_not_import_broker_or_executor():
    import app.futures.strategies.mock_strategies as mod
    src_path = mod.__file__
    assert src_path is not None
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    forbidden = (
        "from app.brokers",
        "import app.brokers",
        "from app.execution.executor",
        "from app.execution.order_router",
        "from app.futures.mock",
        "broker.place_order(",
        ".place_order(",
        ".cancel_order(",
        "force_liquidate_if_needed(",
    )
    for snippet in forbidden:
        assert snippet not in src, (
            f"app.futures.strategies.mock_strategies must not contain '{snippet}'"
        )


# ====================================================================
# 9. Rollover advisory — no broker call
# ====================================================================


def test_rollover_plan_is_advisory_only():
    """`FuturesRolloverPlan`은 dataclass — 어떤 메서드도 broker를 호출하지 않는다."""
    plan = FuturesRolloverPlan(
        close_contract="KOSPI200_2503",
        open_contract="KOSPI200_2506",
        days_to_expiry=3,
        recommended_window="expiry-7d ~ expiry-3d",
        rule_summary="manual rollover required",
    )
    d = plan.to_dict()
    assert d["close_contract"] == "KOSPI200_2503"
    assert d["open_contract"] == "KOSPI200_2506"
    assert d["days_to_expiry"] == 3


# ====================================================================
# 10. Settings invariant unchanged
# ====================================================================


def test_settings_default_keeps_futures_live_trading_disabled():
    from app.core.config import get_settings
    assert get_settings().enable_futures_live_trading is False


def test_settings_default_keeps_ai_execution_disabled():
    from app.core.config import get_settings
    assert get_settings().enable_ai_execution is False
