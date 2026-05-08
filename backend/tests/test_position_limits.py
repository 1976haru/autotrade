"""PositionLimitRule 단위 + 경계값 테스트 (#35).

체크리스트 #35: 1회 거래금액 / 자본 대비 1회 주문 비율 / 종목당 노출 /
자본 대비 종목당 노출 / 총 노출 / 자본 대비 총 노출 / 최대 보유 종목 수.

본 테스트는:
1. 각 한도의 boundary (이하/같음/초과) 검증
2. preview 계산(projected exposure / remaining capacity)
3. RiskManager.evaluate_order이 rule 결과를 정확히 반영
4. SELL은 BUY와 다르게 처리되는 동작 확인
5. broker / OrderExecutor / LIVE flag 호출 0건 (read-only invariant)
"""

from app.brokers.base import Balance, OrderRequest, OrderSide, Position
from app.core.modes import OperationMode
from app.risk.position_limits import (
    PositionLimitInput,
    PositionLimitPolicy,
    PositionLimitPreview,
    PositionLimitResult,
    PositionLimitRule,
    policy_from_risk_policy,
)
from app.risk.risk_manager import RiskDecision, RiskManager, RiskPolicy


# ---------- helpers ----------


def _balance(equity: int = 10_000_000, cash: int | None = None) -> Balance:
    return Balance(cash=cash if cash is not None else equity, equity=equity,
                   buying_power=equity)


def _buy(qty: int = 1, symbol: str = "005930") -> OrderRequest:
    return OrderRequest(symbol=symbol, side=OrderSide.BUY, quantity=qty)


def _sell(qty: int = 1, symbol: str = "005930") -> OrderRequest:
    return OrderRequest(symbol=symbol, side=OrderSide.SELL, quantity=qty)


def _pos(symbol: str, qty: int, market_price: int = 100_000) -> Position:
    return Position(symbol=symbol, quantity=qty, avg_price=market_price,
                    market_price=market_price)


def _input(order, *, equity=10_000_000, positions=None, latest_price=100_000) -> PositionLimitInput:
    return PositionLimitInput(
        order=order, balance=_balance(equity=equity),
        positions=positions or [], latest_price=latest_price,
    )


# ====================================================================
# Order notional (1회 주문 한도) — boundary
# ====================================================================


class TestOrderNotional:
    def test_below_limit_passes(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_order_notional=200_000))
        p, r = rule.check_order_notional(_input(_buy(1), latest_price=100_000))
        assert r == []
        assert p == ["order notional within limit"]

    def test_at_exact_limit_passes(self):
        """경계값 — 정확히 한도와 같으면 통과 (`>` 비교)."""
        rule = PositionLimitRule(PositionLimitPolicy(max_order_notional=100_000))
        p, r = rule.check_order_notional(_input(_buy(1), latest_price=100_000))
        assert r == []
        assert p == ["order notional within limit"]

    def test_one_above_limit_rejected(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_order_notional=100_000))
        p, r = rule.check_order_notional(_input(_buy(1), latest_price=100_001))
        assert r == ["order notional exceeds max_order_notional"]
        assert p == []

    def test_max_zero_passes_any_amount(self):
        """max_order_notional=0이면 검사 비활성 — 어떤 금액도 통과."""
        rule = PositionLimitRule(PositionLimitPolicy(max_order_notional=0))
        p, r = rule.check_order_notional(_input(_buy(1), latest_price=999_999_999))
        assert r == []
        assert p == ["order notional within limit"]


# ====================================================================
# Equity-relative order size (max_position_size_pct) — boundary
# ====================================================================


class TestEquityRelative:
    def test_below_pct_passes(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_position_size_pct=10.0))
        # equity=1,000,000 × 10% = 100,000. 50,000 통과.
        p, r = rule.check_equity_relative_order_size(
            _input(_buy(1), equity=1_000_000, latest_price=50_000),
        )
        assert r == []
        assert p == ["order notional within equity-relative cap"]

    def test_at_exact_pct_passes(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_position_size_pct=10.0))
        p, r = rule.check_equity_relative_order_size(
            _input(_buy(1), equity=1_000_000, latest_price=100_000),
        )
        assert r == []
        assert p == ["order notional within equity-relative cap"]

    def test_one_above_pct_rejected(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_position_size_pct=10.0))
        p, r = rule.check_equity_relative_order_size(
            _input(_buy(1), equity=1_000_000, latest_price=100_001),
        )
        assert len(r) == 1
        assert "exceeds 10.0% of equity" in r[0]
        assert p == []

    def test_pct_zero_skips_check(self):
        """pct=0이면 검사 비활성 — passed/reasons 모두 빈 list (기존 evaluate_order
        동작 보존)."""
        rule = PositionLimitRule(PositionLimitPolicy(max_position_size_pct=0.0))
        p, r = rule.check_equity_relative_order_size(_input(_buy(99)))
        assert p == []
        assert r == []


# ====================================================================
# Max positions — boundary + side-aware
# ====================================================================


class TestMaxPositions:
    def test_below_count_passes_for_new_buy(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_positions=5))
        positions = [_pos("X1", 10), _pos("X2", 10)]
        p, r = rule.check_max_positions(_input(_buy(1, "X3"), positions=positions))
        assert r == []
        assert p == ["position count within limit"]

    def test_at_limit_blocks_new_symbol_buy(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_positions=2))
        positions = [_pos("X1", 10), _pos("X2", 10)]
        p, r = rule.check_max_positions(_input(_buy(1, "X3"), positions=positions))
        assert r == ["max positions reached"]
        assert p == []

    def test_at_limit_allows_adding_to_existing_symbol(self):
        """이미 보유 중인 종목에 추가 BUY는 max_positions 위반 아님."""
        rule = PositionLimitRule(PositionLimitPolicy(max_positions=2))
        positions = [_pos("X1", 10), _pos("X2", 10)]
        p, r = rule.check_max_positions(_input(_buy(1, "X1"), positions=positions))
        assert r == []
        assert p == ["position count within limit"]

    def test_sell_does_not_violate_max_positions(self):
        """SELL은 노출 축소 — max_positions 위반 아님."""
        rule = PositionLimitRule(PositionLimitPolicy(max_positions=1))
        positions = [_pos("X1", 10), _pos("X2", 10)]
        p, r = rule.check_max_positions(_input(_sell(1, "X3"), positions=positions))
        assert r == []
        assert p == ["position count within limit"]

    def test_zero_quantity_position_does_not_count(self):
        """수량 0인 stale position은 보유 종목으로 세지 않음."""
        rule = PositionLimitRule(PositionLimitPolicy(max_positions=1))
        positions = [_pos("X1", 0)]  # 잔량 0
        p, r = rule.check_max_positions(_input(_buy(1, "X2"), positions=positions))
        assert r == []


# ====================================================================
# Symbol exposure (절대값) — boundary
# ====================================================================


class TestSymbolExposure:
    def test_below_limit_passes(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_symbol_exposure=1_000_000))
        positions = [_pos("X", 5, market_price=100_000)]  # 500,000 보유
        p, r = rule.check_symbol_exposure(
            _input(_buy(4, "X"), positions=positions, latest_price=100_000),
        )
        # 500,000 + 400,000 = 900,000 < 1,000,000 → 통과
        assert r == []

    def test_at_exact_limit_passes(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_symbol_exposure=1_000_000))
        positions = [_pos("X", 5, market_price=100_000)]
        p, r = rule.check_symbol_exposure(
            _input(_buy(5, "X"), positions=positions, latest_price=100_000),
        )
        # 500,000 + 500,000 = 1,000,000 == limit → 통과 (`>` 비교)
        assert r == []
        assert p == ["symbol exposure within limit"]

    def test_one_above_limit_rejected(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_symbol_exposure=1_000_000))
        positions = [_pos("X", 5, market_price=100_000)]
        p, r = rule.check_symbol_exposure(
            _input(_buy(6, "X"), positions=positions, latest_price=100_000),
        )
        # 500,000 + 600,000 = 1,100,000 > limit → 거부
        assert r == ["symbol exposure limit exceeded"]

    def test_sell_passes_symbol_exposure(self):
        """SELL은 absolute symbol_exposure 검사를 통과 (passed로 분류)."""
        rule = PositionLimitRule(PositionLimitPolicy(max_symbol_exposure=1_000))
        positions = [_pos("X", 100, market_price=100_000)]  # 매우 큰 보유
        p, r = rule.check_symbol_exposure(
            _input(_sell(50, "X"), positions=positions, latest_price=100_000),
        )
        assert r == []
        assert p == ["symbol exposure within limit"]


class TestSymbolExposurePct:
    def test_below_pct_passes(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_symbol_exposure_pct=20.0))
        # equity=1,000,000 × 20% = 200,000 cap. 노출 100,000 통과.
        p, r = rule.check_symbol_exposure_pct(
            _input(_buy(1, "X"), equity=1_000_000, latest_price=100_000),
        )
        assert r == []

    def test_above_pct_rejected(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_symbol_exposure_pct=10.0))
        positions = [_pos("X", 1, market_price=100_000)]  # 100,000 보유
        p, r = rule.check_symbol_exposure_pct(
            _input(_buy(1, "X"), equity=1_000_000, positions=positions,
                   latest_price=100_000),
        )
        # cap = 100,000. new = 100,000 + 100,000 = 200,000 > 100,000 → 거부
        assert len(r) == 1
        assert "symbol exposure" in r[0]
        assert "10.0%" in r[0]

    def test_pct_zero_skips_check(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_symbol_exposure_pct=0.0))
        p, r = rule.check_symbol_exposure_pct(_input(_buy(99, "X")))
        assert p == []
        assert r == []

    def test_sell_skips_pct_check(self):
        """SELL은 symbol_exposure_pct 검사 우회."""
        rule = PositionLimitRule(PositionLimitPolicy(max_symbol_exposure_pct=1.0))
        positions = [_pos("X", 100, market_price=100_000)]
        p, r = rule.check_symbol_exposure_pct(
            _input(_sell(50, "X"), positions=positions, latest_price=100_000),
        )
        assert p == []
        assert r == []


# ====================================================================
# Total exposure — boundary
# ====================================================================


class TestTotalExposure:
    def test_below_limit_passes(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_total_exposure=2_000_000))
        positions = [_pos("X", 5, market_price=100_000),
                     _pos("Y", 5, market_price=100_000)]
        p, r = rule.check_total_exposure(
            _input(_buy(5, "Z"), positions=positions, latest_price=100_000),
        )
        # 500k + 500k + 500k = 1,500k < 2,000k
        assert r == []

    def test_at_exact_limit_passes(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_total_exposure=1_500_000))
        positions = [_pos("X", 5, market_price=100_000),
                     _pos("Y", 5, market_price=100_000)]
        p, r = rule.check_total_exposure(
            _input(_buy(5, "Z"), positions=positions, latest_price=100_000),
        )
        # 1,500k == limit → 통과
        assert r == []

    def test_above_limit_rejected(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_total_exposure=1_000_000))
        positions = [_pos("X", 5, market_price=100_000),
                     _pos("Y", 5, market_price=100_000)]
        p, r = rule.check_total_exposure(
            _input(_buy(1, "Z"), positions=positions, latest_price=100_000),
        )
        # 1,000k + 100k = 1,100k > 1,000k → 거부
        assert len(r) == 1
        assert "total exposure" in r[0]
        assert "exceeds max_total_exposure" in r[0]

    def test_zero_skips_check(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_total_exposure=0))
        positions = [_pos("X", 999, market_price=999_999)]
        p, r = rule.check_total_exposure(
            _input(_buy(1, "Z"), positions=positions, latest_price=999_999),
        )
        assert p == []
        assert r == []

    def test_sell_skips_check(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_total_exposure=1))
        positions = [_pos("X", 100, market_price=100_000)]
        p, r = rule.check_total_exposure(
            _input(_sell(1, "X"), positions=positions, latest_price=100_000),
        )
        assert p == []
        assert r == []


class TestTotalExposurePct:
    def test_below_pct_passes(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_total_exposure_pct=50.0))
        # equity=1,000k × 50% = 500k cap.
        p, r = rule.check_total_exposure_pct(
            _input(_buy(1, "X"), equity=1_000_000, latest_price=100_000),
        )
        assert r == []

    def test_above_pct_rejected(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_total_exposure_pct=10.0))
        positions = [_pos("X", 1, market_price=100_000)]
        p, r = rule.check_total_exposure_pct(
            _input(_buy(1, "Y"), equity=1_000_000, positions=positions,
                   latest_price=100_000),
        )
        # cap = 100k. new total = 100k + 100k = 200k > 100k → 거부
        assert len(r) == 1
        assert "total exposure" in r[0]
        assert "10.0%" in r[0]

    def test_pct_zero_skips(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_total_exposure_pct=0.0))
        p, r = rule.check_total_exposure_pct(_input(_buy(99, "X")))
        assert p == []
        assert r == []


# ====================================================================
# Preview — projected exposure + remaining capacity
# ====================================================================


class TestPreview:
    def test_buy_increases_exposures(self):
        rule = PositionLimitRule(PositionLimitPolicy(
            max_symbol_exposure=1_000_000,
            max_total_exposure=2_000_000,
            max_positions=5,
        ))
        positions = [_pos("X", 3, market_price=100_000),  # 300k
                     _pos("Y", 5, market_price=100_000)]  # 500k
        prev = rule.build_preview(_input(_buy(2, "X"), positions=positions,
                                          latest_price=100_000))
        assert prev.order_notional == 200_000
        assert prev.current_symbol_exposure == 300_000
        assert prev.projected_symbol_exposure == 500_000
        assert prev.current_total_exposure == 800_000
        assert prev.projected_total_exposure == 1_000_000
        assert prev.current_position_count == 2
        assert prev.projected_position_count == 2  # X 이미 보유
        assert prev.will_open_new_position is False

    def test_buy_new_symbol_opens_new_slot(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_positions=5))
        positions = [_pos("X", 3, market_price=100_000)]
        prev = rule.build_preview(_input(_buy(1, "Y"), positions=positions,
                                          latest_price=100_000))
        assert prev.will_open_new_position is True
        assert prev.current_position_count == 1
        assert prev.projected_position_count == 2

    def test_remaining_symbol_capacity(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_symbol_exposure=1_000_000))
        positions = [_pos("X", 3, market_price=100_000)]  # 300k
        prev = rule.build_preview(_input(_buy(2, "X"), positions=positions,
                                          latest_price=100_000))
        # projected 500k → remaining 500k
        assert prev.remaining_symbol_capacity == 500_000

    def test_remaining_total_capacity(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_total_exposure=2_000_000))
        positions = [_pos("X", 3, market_price=100_000)]
        prev = rule.build_preview(_input(_buy(2, "Y"), positions=positions,
                                          latest_price=100_000))
        # projected total 500k → remaining 1_500k
        assert prev.remaining_total_capacity == 1_500_000

    def test_remaining_position_slots(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_positions=5))
        positions = [_pos("X", 1)]
        prev = rule.build_preview(_input(_buy(1, "Y"), positions=positions))
        # projected 2 → 5 - 2 = 3
        assert prev.remaining_position_slots == 3

    def test_no_limit_yields_none_capacity(self):
        rule = PositionLimitRule(PositionLimitPolicy())  # 모두 0
        prev = rule.build_preview(_input(_buy(1, "X")))
        assert prev.remaining_symbol_capacity is None
        assert prev.remaining_total_capacity is None
        assert prev.remaining_position_slots is None

    def test_remaining_clamped_to_zero_when_already_over(self):
        """이미 한도 초과 상태면 remaining=0 (음수 floor)."""
        rule = PositionLimitRule(PositionLimitPolicy(max_symbol_exposure=100_000))
        positions = [_pos("X", 5, market_price=100_000)]  # 500k 이미 초과
        prev = rule.build_preview(_input(_buy(1, "X"), positions=positions,
                                          latest_price=100_000))
        assert prev.remaining_symbol_capacity == 0

    def test_sell_decreases_exposures(self):
        rule = PositionLimitRule(PositionLimitPolicy())
        positions = [_pos("X", 5, market_price=100_000),  # 500k
                     _pos("Y", 5, market_price=100_000)]
        prev = rule.build_preview(_input(_sell(2, "X"), positions=positions,
                                          latest_price=100_000))
        # SELL 200k → symbol projected 300k, total 800k
        assert prev.projected_symbol_exposure == 300_000
        assert prev.projected_total_exposure == 800_000
        assert prev.will_open_new_position is False

    def test_full_sell_reduces_position_count(self):
        rule = PositionLimitRule(PositionLimitPolicy())
        positions = [_pos("X", 5, market_price=100_000),
                     _pos("Y", 5, market_price=100_000)]
        prev = rule.build_preview(_input(_sell(5, "X"), positions=positions,
                                          latest_price=100_000))
        # X 전량 매도 → 종목 수 2 → 1
        assert prev.current_position_count == 2
        assert prev.projected_position_count == 1

    def test_to_dict_serializable(self):
        rule = PositionLimitRule(PositionLimitPolicy(max_symbol_exposure=1_000_000))
        prev = rule.build_preview(_input(_buy(1, "X")))
        d = prev.to_dict()
        assert "order_notional" in d
        assert "remaining_symbol_capacity" in d


# ====================================================================
# 통합 check() — 모든 한도 동시 검증
# ====================================================================


class TestCheckCombined:
    def test_all_limits_passing(self):
        rule = PositionLimitRule(PositionLimitPolicy(
            max_order_notional=1_000_000,
            max_position_size_pct=20.0,
            max_positions=5,
            max_symbol_exposure=1_000_000,
            max_symbol_exposure_pct=20.0,
            max_total_exposure=2_000_000,
            max_total_exposure_pct=50.0,
        ))
        result = rule.check(_input(_buy(1, "X"), equity=1_000_000, latest_price=100_000))
        assert isinstance(result, PositionLimitResult)
        assert result.allowed is True
        assert result.reasons == []
        assert isinstance(result.preview, PositionLimitPreview)

    def test_multiple_violations_all_collected(self):
        rule = PositionLimitRule(PositionLimitPolicy(
            max_order_notional=10,
            max_total_exposure=10,
        ))
        result = rule.check(_input(_buy(1, "X"), latest_price=100_000))
        assert result.allowed is False
        assert len(result.reasons) >= 2  # notional + total exposure

    def test_to_dict(self):
        rule = PositionLimitRule(PositionLimitPolicy())
        d = rule.check(_input(_buy(1))).to_dict()
        assert "allowed" in d
        assert "passed" in d
        assert "reasons" in d
        assert "preview" in d


# ====================================================================
# RiskManager integration — 기존 테스트 wording이 유지되어야 함
# ====================================================================


class TestRiskManagerIntegration:
    def test_evaluate_order_uses_rule_text_for_notional(self):
        """기존 reason 문구가 그대로 — 'order notional exceeds max_order_notional'."""
        risk = RiskManager(RiskPolicy(max_order_notional=100_000))
        result = risk.evaluate_order(
            order=_buy(10), mode=OperationMode.SIMULATION,
            balance=_balance(), positions=[], latest_price=75_000,
        )
        assert result.decision == RiskDecision.REJECTED
        assert "order notional exceeds max_order_notional" in result.reasons

    def test_evaluate_order_text_for_max_positions(self):
        risk = RiskManager(RiskPolicy(max_positions=2))
        positions = [_pos("X1", 10), _pos("X2", 10)]
        result = risk.evaluate_order(
            order=_buy(1, "X3"), mode=OperationMode.SIMULATION,
            balance=_balance(), positions=positions, latest_price=10_000,
        )
        assert "max positions reached" in result.reasons

    def test_evaluate_order_text_for_symbol_exposure(self):
        risk = RiskManager(RiskPolicy(max_symbol_exposure=500_000))
        positions = [_pos("X", 5, market_price=100_000)]
        result = risk.evaluate_order(
            order=_buy(2, "X"), mode=OperationMode.SIMULATION,
            balance=_balance(), positions=positions, latest_price=100_000,
        )
        assert "symbol exposure limit exceeded" in result.reasons

    def test_evaluate_order_text_for_total_exposure(self):
        risk = RiskManager(RiskPolicy(max_total_exposure=1_000_000))
        positions = [_pos("X", 5, market_price=100_000),
                     _pos("Y", 5, market_price=100_000)]
        result = risk.evaluate_order(
            order=_buy(1, "Z"), mode=OperationMode.SIMULATION,
            balance=_balance(), positions=positions, latest_price=100_000,
        )
        assert any("total exposure" in r and "max_total_exposure" in r
                   for r in result.reasons)

    def test_evaluate_order_text_for_total_exposure_pct(self):
        risk = RiskManager(RiskPolicy(max_total_exposure_pct=10.0))
        positions = [_pos("X", 1, market_price=100_000)]
        result = risk.evaluate_order(
            order=_buy(1, "Y"), mode=OperationMode.SIMULATION,
            balance=_balance(equity=1_000_000), positions=positions,
            latest_price=100_000,
        )
        assert any("total exposure" in r and "10.0%" in r for r in result.reasons)

    def test_evaluate_order_text_for_position_size_pct(self):
        risk = RiskManager(RiskPolicy(max_position_size_pct=10.0))
        result = risk.evaluate_order(
            order=_buy(1), mode=OperationMode.SIMULATION,
            balance=_balance(equity=1_000_000), positions=[],
            latest_price=200_000,  # 200k > 10% of 1M = 100k
        )
        assert any("exceeds 10.0% of equity" in r for r in result.reasons)


# ====================================================================
# Adapter — policy_from_risk_policy
# ====================================================================


class TestPolicyAdapter:
    def test_round_trip_fields(self):
        rp = RiskPolicy(
            max_order_notional=123_000,
            max_position_size_pct=12.5,
            max_positions=7,
            max_symbol_exposure=999_999,
            max_symbol_exposure_pct=4.5,
            max_total_exposure=8_888_888,
            max_total_exposure_pct=33.0,
        )
        pl = policy_from_risk_policy(rp)
        assert pl.max_order_notional == 123_000
        assert pl.max_position_size_pct == 12.5
        assert pl.max_positions == 7
        assert pl.max_symbol_exposure == 999_999
        assert pl.max_symbol_exposure_pct == 4.5
        assert pl.max_total_exposure == 8_888_888
        assert pl.max_total_exposure_pct == 33.0


# ====================================================================
# Safety invariants
# ====================================================================


class TestSafety:
    def test_module_does_not_import_broker_or_executor(self):
        import inspect

        from app.risk import position_limits as mod
        src = inspect.getsource(mod)
        # Position limits 모듈은 broker/permission/execution 호출 0건.
        forbidden = (
            "from app.brokers.kis", "from app.permission",
            "from app.execution.executor", "from app.execution.order_router",
            "place_order(", "route_order(",
        )
        for f in forbidden:
            assert f not in src, f"forbidden symbol in position_limits: {f}"

    def test_rule_check_does_not_mutate_input(self):
        """check()는 순수 함수 — input 변경 없음."""
        positions = [_pos("X", 10), _pos("Y", 5)]
        original_lengths = [len(positions), positions[0].quantity, positions[1].quantity]
        rule = PositionLimitRule(PositionLimitPolicy(max_total_exposure=1))
        rule.check(_input(_buy(1, "Z"), positions=positions))
        # input mutate 0건
        assert [len(positions), positions[0].quantity, positions[1].quantity] == original_lengths


# ====================================================================
# Futures — 본 rule이 선물 정책을 *건드리지 않는다*는 invariant
# ====================================================================


class TestFuturesSeparation:
    def test_position_limits_module_does_not_import_futures(self):
        import inspect

        from app.risk import position_limits as mod
        src = inspect.getsource(mod)
        # 선물은 별도 정책 — 본 모듈은 futures 모듈을 import하지 않는다.
        assert "from app.futures" not in src

    def test_futures_risk_module_intact(self):
        """FuturesRiskPolicy 기존 import / 로딩이 본 PR로 깨지지 않음."""
        from app.futures import risk as futures_risk
        assert hasattr(futures_risk, "FuturesRiskPolicy")
