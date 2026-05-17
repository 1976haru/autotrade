"""#47: Futures broker contract tests.

Coverage:
- `FuturesBrokerAdapter`는 ABC이며, 주식 `BrokerAdapter`를 상속하지 않는다 (MRO 분리).
- `FuturesOrder`는 audit 필드(strategy/signal_*/ai_decision_meta/trade_reason/client_order_id)
  를 carry하면서 `FuturesOrderRequest` 기본 필드(contract/side/quantity/order_type/limit_price)도
  유지.
- `FuturesContractSpec`이 만기/멀티/틱사이즈/틱가치/레버리지/통화/거래시간을 모두 보유.
- `FuturesMarginSnapshot.from_balance`로 legacy `FuturesBalance` → richer snapshot 변환.
- 만기 helper: `days_to_expiry`, `is_contract_expiring_soon`, `should_rollover`.
- Auto-rollover 금지: helper들은 *advisory bool/int*만 반환, broker 호출 0건.
- 정적 가드: `app/brokers/futures_base.py`는 KIS / kis_client / mock_broker / live broker /
  app.execution.executor 어떤 것도 import하지 않는다.
- `MockFuturesBroker`(legacy)도 본 ABC의 인스턴스로 인식 — re-export 호환성.
- `FuturesRiskPolicy`는 주식 `RiskPolicy`와 별개 dataclass.
- 주식 `PositionLimitRule`이 `FuturesRiskManager`에 import되지 않음 (정적 가드).
"""

from __future__ import annotations

import inspect
from datetime import datetime, time, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.brokers import futures_base as fb
from app.brokers.base import BrokerAdapter
from app.brokers.futures_base import (
    FuturesBalance,
    FuturesBrokerAdapter,
    FuturesContractSpec,
    FuturesMarginSnapshot,
    FuturesOrder,
    FuturesOrderType,
    FuturesSide,
    days_to_expiry,
    is_contract_expiring_soon,
    should_rollover,
)


_KST = timezone(timedelta(hours=9))


def _spec(expiry: datetime) -> FuturesContractSpec:
    return FuturesContractSpec(
        code="KOSPI200_2503",
        underlying="KOSPI200",
        expiry=expiry,
        multiplier=250_000,
        tick_size_pt=0.05,
        tick_value_krw=12_500,
        leverage_max=10.0,
        currency="KRW",
        market_open_kst=time(9, 0),
        market_close_kst=time(15, 45),
    )


# ====================================================================
# 1. ABC separation — futures NOT inheriting from stock
# ====================================================================


def test_futures_broker_adapter_does_not_inherit_from_stock_broker():
    """주식 `BrokerAdapter`와 선물 `FuturesBrokerAdapter`는 분리된 계층.

    상속 관계가 생기면 어댑터가 한쪽 계약을 우회 호출할 수 있어 위험. 본
    테스트는 MRO를 직접 검사해 영구 invariant로 lock한다.
    """
    assert BrokerAdapter not in FuturesBrokerAdapter.__mro__, (
        "FuturesBrokerAdapter must NOT inherit from stock BrokerAdapter — "
        "futures and equity contracts are intentionally separate ABCs."
    )
    # 역방향도 확인.
    assert FuturesBrokerAdapter not in BrokerAdapter.__mro__


def test_futures_broker_adapter_is_abstract():
    """ABC이므로 직접 인스턴스화 불가."""
    with pytest.raises(TypeError):
        FuturesBrokerAdapter()  # type: ignore[abstract]


def test_required_methods_are_declared_abstract():
    """6개 메서드(`get_quote`, `get_balance`, `get_positions`, `place_order`,
    `cancel_order`, `get_order_status`)가 abstract로 선언되어 있다."""
    expected = {"get_quote", "get_balance", "get_positions",
                "place_order", "cancel_order", "get_order_status"}
    abstracts = set(getattr(FuturesBrokerAdapter, "__abstractmethods__", set()))
    assert expected <= abstracts, (
        f"Expected abstract methods {expected}, got {abstracts}"
    )


# ====================================================================
# 2. FuturesOrder — richer model preserves base + adds audit fields
# ====================================================================


def test_futures_order_inherits_base_fields():
    """`FuturesOrder`는 `FuturesOrderRequest`의 contract/side/quantity 등을 그대로 carry."""
    order = FuturesOrder(
        contract="KOSPI200_2503",
        side=FuturesSide.BUY,
        quantity=1,
        order_type=FuturesOrderType.MARKET,
    )
    assert order.contract == "KOSPI200_2503"
    assert order.side == FuturesSide.BUY
    assert order.quantity == 1
    assert order.order_type == FuturesOrderType.MARKET
    # audit 필드는 default None.
    assert order.client_order_id is None
    assert order.trade_reason is None
    assert order.strategy is None
    assert order.signal_strength is None
    assert order.signal_confidence is None
    assert order.ai_decision_meta is None


def test_futures_order_carries_audit_fields():
    order = FuturesOrder(
        contract="KOSPI200_2503",
        side=FuturesSide.SELL,
        quantity=2,
        order_type=FuturesOrderType.LIMIT,
        limit_price=350_000,
        client_order_id="abc-123",
        trade_reason="strategy_signal",
        strategy="ai_assist",
        signal_strength=70,
        signal_confidence=85,
        ai_decision_meta={"source": "AI_ASSIST"},
    )
    assert order.client_order_id == "abc-123"
    assert order.trade_reason == "strategy_signal"
    assert order.strategy == "ai_assist"
    assert order.signal_strength == 70
    assert order.signal_confidence == 85
    assert order.ai_decision_meta == {"source": "AI_ASSIST"}


def test_futures_order_validates_quantity_positive():
    with pytest.raises(ValidationError):
        FuturesOrder(contract="KOSPI200_2503", side=FuturesSide.BUY, quantity=0)


def test_futures_order_validates_signal_quality_range():
    with pytest.raises(ValidationError):
        FuturesOrder(contract="KOSPI200_2503", side=FuturesSide.BUY,
                     quantity=1, signal_confidence=150)


# ====================================================================
# 3. FuturesContractSpec — full contract metadata
# ====================================================================


def test_contract_spec_has_all_expected_fields():
    expiry = datetime(2025, 3, 13, 15, 45, tzinfo=_KST)
    spec = _spec(expiry)
    assert spec.code == "KOSPI200_2503"
    assert spec.underlying == "KOSPI200"
    assert spec.expiry == expiry
    assert spec.multiplier == 250_000
    assert spec.tick_size_pt == 0.05
    assert spec.tick_value_krw == 12_500
    assert spec.leverage_max == 10.0
    assert spec.currency == "KRW"
    assert spec.market_open_kst == time(9, 0)
    assert spec.market_close_kst == time(15, 45)


def test_contract_spec_validates_positive_multiplier():
    with pytest.raises(ValidationError):
        FuturesContractSpec(
            code="X", underlying="Y",
            expiry=datetime(2025, 1, 1, tzinfo=_KST),
            multiplier=0,
        )


def test_contract_spec_validates_positive_leverage():
    with pytest.raises(ValidationError):
        FuturesContractSpec(
            code="X", underlying="Y",
            expiry=datetime(2025, 1, 1, tzinfo=_KST),
            multiplier=1, leverage_max=0,
        )


# ====================================================================
# 4. FuturesMarginSnapshot — adds maintenance margin
# ====================================================================


def test_margin_snapshot_from_balance_default_maintenance_zero():
    bal = FuturesBalance(cash=10_000_000, margin_used=2_000_000,
                          margin_available=8_000_000, equity=10_000_000)
    snap = FuturesMarginSnapshot.from_balance(bal)
    assert snap.cash == 10_000_000
    assert snap.margin_used == 2_000_000
    assert snap.margin_available == 8_000_000
    assert snap.equity == 10_000_000
    assert snap.maintenance_margin_required == 0
    assert snap.margin_call is False
    assert snap.currency == "KRW"


def test_margin_snapshot_carries_maintenance_and_margin_call():
    bal = FuturesBalance(cash=1_000_000, margin_used=900_000,
                          margin_available=100_000, equity=1_000_000)
    snap = FuturesMarginSnapshot.from_balance(
        bal, maintenance_margin_required=200_000, margin_call=True,
    )
    assert snap.maintenance_margin_required == 200_000
    assert snap.margin_call is True


# ====================================================================
# 5. Expiry / rollover helpers — advisory only, no broker calls
# ====================================================================


def test_days_to_expiry_future():
    now = datetime(2025, 3, 1, 12, 0, tzinfo=_KST)
    expiry = datetime(2025, 3, 11, 15, 45, tzinfo=_KST)
    # 약 10일 남음 (소수점 floor).
    assert days_to_expiry(expiry, now=now) == 10


def test_days_to_expiry_today():
    now = datetime(2025, 3, 11, 12, 0, tzinfo=_KST)
    expiry = now
    assert days_to_expiry(expiry, now=now) == 0


def test_days_to_expiry_past_returns_negative():
    now = datetime(2025, 3, 15, 12, 0, tzinfo=_KST)
    expiry = datetime(2025, 3, 11, 15, 45, tzinfo=_KST)
    # 만료된 contract — 음수.
    assert days_to_expiry(expiry, now=now) < 0


def test_days_to_expiry_naive_datetime_treated_as_kst():
    """naive datetime은 KST로 가정 (거래소 시간대 매칭)."""
    now = datetime(2025, 3, 1, 12, 0)  # naive
    expiry = datetime(2025, 3, 11, 15, 45)  # naive
    assert days_to_expiry(expiry, now=now) == 10


def test_is_contract_expiring_soon_default_threshold():
    now = datetime(2025, 3, 1, tzinfo=_KST)
    # 임계 default 5일 → 7일 남음 → False
    spec = _spec(datetime(2025, 3, 8, 15, 45, tzinfo=_KST))
    assert is_contract_expiring_soon(spec, now=now) is False
    # 4일 남음 → True
    spec_close = _spec(datetime(2025, 3, 5, 15, 45, tzinfo=_KST))
    assert is_contract_expiring_soon(spec_close, now=now) is True


def test_is_contract_expiring_soon_custom_threshold():
    now = datetime(2025, 3, 1, tzinfo=_KST)
    spec = _spec(datetime(2025, 3, 11, 15, 45, tzinfo=_KST))  # 10일
    assert is_contract_expiring_soon(spec, now=now, threshold_days=10) is True
    assert is_contract_expiring_soon(spec, now=now, threshold_days=5)  is False


def test_is_contract_expiring_soon_negative_threshold_raises():
    now = datetime(2025, 3, 1, tzinfo=_KST)
    spec = _spec(datetime(2025, 3, 8, tzinfo=_KST))
    with pytest.raises(ValueError):
        is_contract_expiring_soon(spec, now=now, threshold_days=-1)


def test_should_rollover_matches_expiring_soon():
    """`should_rollover`는 `is_contract_expiring_soon`과 같은 임계 — 의도적
    동일 helper. 둘 다 advisory bool만 반환한다."""
    now = datetime(2025, 3, 1, tzinfo=_KST)
    spec = _spec(datetime(2025, 3, 4, tzinfo=_KST))  # 3일
    assert should_rollover(spec, now=now) is True
    assert should_rollover(spec, now=now) == is_contract_expiring_soon(spec, now=now)


def test_helpers_return_only_advisory_no_broker_calls():
    """helper 함수들의 반환값은 plain bool / int — 어떤 broker 객체나
    Coroutine도 반환하지 않는다 (자동 롤오버 금지 invariant)."""
    spec = _spec(datetime(2025, 3, 8, tzinfo=_KST))
    now = datetime(2025, 3, 1, tzinfo=_KST)
    assert isinstance(days_to_expiry(spec.expiry, now=now), int)
    assert isinstance(is_contract_expiring_soon(spec, now=now), bool)
    assert isinstance(should_rollover(spec, now=now), bool)


# ====================================================================
# 6. Static guard — no live broker / executor / kis client imports
# ====================================================================


def test_module_does_not_import_live_broker_or_executor():
    """`app.brokers.futures_base`는 KIS / kis_client / mock_broker /
    OrderExecutor / route_order를 import하지 않는다."""
    src_path = fb.__file__
    assert src_path is not None
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    forbidden = (
        "from app.brokers.kis import",
        "import app.brokers.kis",
        "from app.brokers.kis_client",
        "from app.brokers.mock_broker",
        "from app.execution.executor",
        "from app.execution.order_router",
        "broker.place_order(",
        ".place_order(",
        "broker.cancel_order(",
        ".cancel_order(",
    )
    for snippet in forbidden:
        assert snippet not in src, (
            f"app.brokers.futures_base must not contain '{snippet}' — "
            "it is a contract-only module (no order execution)."
        )


# ====================================================================
# 7. Backwards compat — MockFuturesBroker still implements the ABC
# ====================================================================


def test_mock_futures_broker_implements_canonical_abc():
    """re-export 호환성: legacy `MockFuturesBroker`가 본 모듈의
    `FuturesBrokerAdapter`도 만족 (같은 클래스이므로)."""
    from app.futures.mock import MockFuturesBroker
    broker = MockFuturesBroker(initial_cash=10_000_000)
    assert isinstance(broker, FuturesBrokerAdapter)


def test_legacy_and_new_abc_are_same_class():
    """`app.futures.base.FuturesBrokerAdapter` ≡ `app.brokers.futures_base.FuturesBrokerAdapter`.

    별도 ABC를 새로 만들면 MRO가 분기되어 backwards compat 깨짐 — 본 PR은
    *re-export*로 동일 클래스 유지."""
    from app.futures.base import FuturesBrokerAdapter as LegacyABC
    assert LegacyABC is FuturesBrokerAdapter


# ====================================================================
# 8. Risk separation — futures uses its own policy / rules
# ====================================================================


def test_futures_risk_policy_is_separate_from_stock_policy():
    """`FuturesRiskPolicy`는 주식 `RiskPolicy`와 별개 dataclass.

    필드명 / 의미가 다르며 (max_contracts vs max_positions, max_margin_used
    vs max_order_notional 등) 동일 instance로 취급할 수 없다."""
    from app.futures.risk import FuturesRiskPolicy
    from app.risk.risk_manager import RiskPolicy

    fp = FuturesRiskPolicy()
    sp = RiskPolicy()
    assert type(fp) is not type(sp)
    # 주요 필드 — 선물 정책에만 존재.
    assert hasattr(fp, "max_contracts")
    assert hasattr(fp, "max_margin_used")
    assert hasattr(fp, "max_leverage")
    assert hasattr(fp, "enable_futures_live_trading")
    # 주식 정책에만 존재 (선물 정책에는 없음).
    assert hasattr(sp, "max_order_notional")
    assert hasattr(sp, "max_positions")
    assert not hasattr(fp, "max_order_notional")
    assert not hasattr(fp, "max_positions")


def test_futures_risk_module_does_not_import_stock_position_limits():
    """주식 `PositionLimitRule`이 `FuturesRiskManager`에 import되지 않는다 —
    선물은 자체 margin/contracts 가드를 사용."""
    import app.futures.risk as fr_mod
    src_path = fr_mod.__file__
    assert src_path is not None
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    forbidden = (
        "from app.risk.position_limits",
        "import app.risk.position_limits",
        "PositionLimitRule(",
    )
    for snippet in forbidden:
        assert snippet not in src, (
            f"app.futures.risk must not import stock '{snippet}' — "
            "futures uses its own contracts/margin/leverage guards."
        )


def test_futures_risk_manager_evaluate_order_signature_differs_from_stock():
    """`FuturesRiskManager.evaluate_order` 시그니처는 주식과 별개 — futures
    margin / mark_price / leverage 인자를 갖는다 (주식의 balance/positions/
    latest_price 시그니처와 다름)."""
    from app.futures.risk import FuturesRiskManager
    sig = inspect.signature(FuturesRiskManager.evaluate_order)
    params = set(sig.parameters.keys())
    assert "order" in params
    assert "positions" in params
    assert "margin_used" in params
    assert "margin_available" in params
    # 주식 RiskManager.evaluate_order의 파라미터 (balance / latest_price /
    # mode / requested_by_ai)는 선물에 등장하지 않거나 의미가 다름 — futures는
    # leverage / mark_price 중심 가드.


# ====================================================================
# 9. ENABLE_FUTURES_LIVE_TRADING invariant
# ====================================================================


def test_futures_risk_policy_default_keeps_live_trading_disabled():
    from app.futures.risk import FuturesRiskPolicy
    p = FuturesRiskPolicy()
    assert p.enable_futures_live_trading is False, (
        "ENABLE_FUTURES_LIVE_TRADING must remain False by default — "
        "#46 / #47 invariant."
    )


def test_settings_default_keeps_futures_live_trading_disabled():
    from app.core.config import get_settings
    s = get_settings()
    assert s.enable_futures_live_trading is False, (
        "Settings.enable_futures_live_trading must remain False by default."
    )
