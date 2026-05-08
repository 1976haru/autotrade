"""Loss Limit Rules 단위 + 경계값 테스트 (#36).

DailyLossLimitRule / WeeklyLossLimitRule / ConsecutiveLossRule + helpers
(compute_weekly_realized_pnl_kst / count_consecutive_losing_trades) +
RiskManager 통합 검증.

본 rule들은 *주문을 만들지 않는다* — broker / OrderExecutor 호출 0건.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.brokers.base import Balance, OrderRequest, OrderSide
from app.core.modes import OperationMode
from app.db.models import OrderAuditLog
from app.risk.daily_pnl import (
    compute_weekly_realized_pnl_kst,
    count_consecutive_losing_trades,
    week_start_kst,
)
from app.risk.loss_limits import (
    ConsecutiveLossRule,
    DailyLossLimitRule,
    LossLimitDecision,
    WeeklyLossLimitRule,
    evaluate_loss_limits,
)
from app.risk.risk_manager import RiskDecision, RiskManager, RiskPolicy


def _balance(cash: int = 10_000_000) -> Balance:
    return Balance(cash=cash, equity=cash, buying_power=cash)


def _buy(qty: int = 1, symbol: str = "005930") -> OrderRequest:
    return OrderRequest(symbol=symbol, side=OrderSide.BUY, quantity=qty)


def _sell(qty: int = 1, symbol: str = "005930") -> OrderRequest:
    return OrderRequest(symbol=symbol, side=OrderSide.SELL, quantity=qty)


# ====================================================================
# DailyLossLimitRule
# ====================================================================


class TestDailyLossLimitRule:
    def test_invalid_params(self):
        with pytest.raises(ValueError):
            DailyLossLimitRule(limit=-1)
        with pytest.raises(ValueError):
            DailyLossLimitRule(limit=1, warn_pct=110)
        with pytest.raises(ValueError):
            DailyLossLimitRule(limit=1, warn_pct=80, reduce_pct=80)
        with pytest.raises(ValueError):
            DailyLossLimitRule(limit=1, warn_pct=90, reduce_pct=80)

    def test_disabled_when_limit_zero(self):
        rule = DailyLossLimitRule(limit=0)
        r = rule.evaluate(daily_pnl=-99_999, order=_buy())
        assert r.decision == LossLimitDecision.ALLOW
        assert r.block_buy is False

    def test_pnl_positive_allowed(self):
        rule = DailyLossLimitRule(limit=100_000)
        r = rule.evaluate(daily_pnl=50_000, order=_buy())
        assert r.decision == LossLimitDecision.ALLOW

    def test_below_warn_threshold_allowed(self):
        rule = DailyLossLimitRule(limit=100_000, warn_pct=50, reduce_pct=70)
        # loss 30% — 50% 미만
        r = rule.evaluate(daily_pnl=-30_000, order=_buy())
        assert r.decision == LossLimitDecision.ALLOW

    def test_warn_threshold_triggers_warn(self):
        rule = DailyLossLimitRule(limit=100_000, warn_pct=50, reduce_pct=70)
        r = rule.evaluate(daily_pnl=-50_000, order=_buy())
        assert r.decision == LossLimitDecision.WARN
        assert r.block_buy is False
        assert r.warnings  # surface warning
        assert r.indicators["usage_pct"] == 50.0

    def test_reduce_threshold_triggers_reduce_size(self):
        rule = DailyLossLimitRule(limit=100_000, warn_pct=50, reduce_pct=70)
        r = rule.evaluate(daily_pnl=-70_000, order=_buy())
        assert r.decision == LossLimitDecision.REDUCE_SIZE
        assert r.block_buy is False
        assert r.warnings

    def test_at_exact_limit_blocks_new_buy(self):
        """경계값: 정확히 한도에 도달 → BLOCK_NEW_BUY (loss >= limit)."""
        rule = DailyLossLimitRule(limit=100_000)
        r = rule.evaluate(daily_pnl=-100_000, order=_buy())
        assert r.decision == LossLimitDecision.BLOCK_NEW_BUY
        assert r.block_buy is True
        assert r.reasons

    def test_above_limit_blocks_new_buy(self):
        rule = DailyLossLimitRule(limit=100_000)
        r = rule.evaluate(daily_pnl=-150_000, order=_buy())
        assert r.decision == LossLimitDecision.BLOCK_NEW_BUY
        assert r.block_buy is True

    def test_one_below_limit_allowed_or_reduce(self):
        rule = DailyLossLimitRule(limit=100_000)
        # 99,999 loss < 100,000 → 차단 X (warn/reduce/allow 중 하나)
        r = rule.evaluate(daily_pnl=-99_999, order=_buy())
        assert r.decision != LossLimitDecision.BLOCK_NEW_BUY
        assert r.block_buy is False

    def test_sell_does_not_get_block_buy_flagged(self):
        """SELL은 한도 초과여도 block_buy=False — 리스크 축소 보호."""
        rule = DailyLossLimitRule(limit=100_000)
        r = rule.evaluate(daily_pnl=-150_000, order=_sell())
        # decision은 BLOCK_NEW_BUY로 분류되지만 block_buy 의미상 SELL은 통과
        assert r.decision == LossLimitDecision.BLOCK_NEW_BUY
        assert r.warnings  # 운영자 인지 가능
        assert r.reasons == []  # SELL은 reasons에 차단 사유 추가하지 않음


# ====================================================================
# WeeklyLossLimitRule
# ====================================================================


class TestWeeklyLossLimitRule:
    def test_disabled_when_limit_zero(self):
        rule = WeeklyLossLimitRule(limit=0)
        r = rule.evaluate(weekly_pnl=-9_999_999, order=_buy())
        assert r.decision == LossLimitDecision.ALLOW

    def test_at_exact_limit_blocks(self):
        rule = WeeklyLossLimitRule(limit=500_000)
        r = rule.evaluate(weekly_pnl=-500_000, order=_buy())
        assert r.decision == LossLimitDecision.BLOCK_NEW_BUY
        assert r.block_buy is True

    def test_below_limit_allowed(self):
        rule = WeeklyLossLimitRule(limit=500_000)
        r = rule.evaluate(weekly_pnl=-100_000, order=_buy())
        assert r.decision == LossLimitDecision.ALLOW

    def test_warn_and_reduce_thresholds(self):
        rule = WeeklyLossLimitRule(limit=500_000, warn_pct=50, reduce_pct=80)
        assert rule.evaluate(weekly_pnl=-200_000, order=_buy()).decision == LossLimitDecision.ALLOW
        assert rule.evaluate(weekly_pnl=-250_000, order=_buy()).decision == LossLimitDecision.WARN
        assert rule.evaluate(weekly_pnl=-400_000, order=_buy()).decision == LossLimitDecision.REDUCE_SIZE
        assert rule.evaluate(weekly_pnl=-500_000, order=_buy()).decision == LossLimitDecision.BLOCK_NEW_BUY

    def test_sell_passes_under_limit(self):
        rule = WeeklyLossLimitRule(limit=500_000)
        r = rule.evaluate(weekly_pnl=-1_000_000, order=_sell())
        assert r.decision == LossLimitDecision.BLOCK_NEW_BUY
        assert r.reasons == []  # SELL 통과 — reasons 미추가
        assert r.warnings  # 운영자 인지


# ====================================================================
# ConsecutiveLossRule
# ====================================================================


class TestConsecutiveLossRule:
    def test_invalid_limit(self):
        with pytest.raises(ValueError):
            ConsecutiveLossRule(limit=-1)

    def test_disabled_when_limit_zero(self):
        rule = ConsecutiveLossRule(limit=0)
        r = rule.evaluate(consecutive_loss_count=99, order=_buy())
        assert r.decision == LossLimitDecision.ALLOW

    def test_below_limit_allowed(self):
        rule = ConsecutiveLossRule(limit=3)
        r = rule.evaluate(consecutive_loss_count=2, order=_buy())
        assert r.decision == LossLimitDecision.ALLOW

    def test_at_exact_limit_blocks(self):
        rule = ConsecutiveLossRule(limit=3)
        r = rule.evaluate(consecutive_loss_count=3, order=_buy())
        assert r.decision == LossLimitDecision.BLOCK_NEW_BUY
        assert r.block_buy is True
        assert r.reasons

    def test_above_limit_blocks(self):
        rule = ConsecutiveLossRule(limit=3)
        r = rule.evaluate(consecutive_loss_count=10, order=_buy())
        assert r.decision == LossLimitDecision.BLOCK_NEW_BUY

    def test_sell_passes(self):
        rule = ConsecutiveLossRule(limit=3)
        r = rule.evaluate(consecutive_loss_count=10, order=_sell())
        assert r.decision == LossLimitDecision.BLOCK_NEW_BUY
        assert r.reasons == []
        assert r.warnings


# ====================================================================
# evaluate_loss_limits — combined
# ====================================================================


class TestEvaluateLossLimits:
    def test_all_none_returns_no_block(self):
        m = evaluate_loss_limits(
            order=_buy(), daily_rule=None, weekly_rule=None, consecutive_rule=None,
        )
        assert m.block_buy is False
        assert m.passed == []
        assert m.reasons == []

    def test_daily_block_marks_block_buy(self):
        m = evaluate_loss_limits(
            order=_buy(),
            daily_rule=DailyLossLimitRule(limit=100_000),
            weekly_rule=None,
            consecutive_rule=None,
            daily_pnl=-100_000,
        )
        assert m.block_buy is True
        assert any("daily loss" in r for r in m.reasons)

    def test_combined_block_buy_set_true_if_any(self):
        m = evaluate_loss_limits(
            order=_buy(),
            daily_rule=DailyLossLimitRule(limit=100_000),
            weekly_rule=WeeklyLossLimitRule(limit=500_000),
            consecutive_rule=ConsecutiveLossRule(limit=3),
            daily_pnl=-50_000,
            weekly_pnl=-100_000,
            consecutive_loss_count=5,  # over consecutive limit
        )
        assert m.block_buy is True
        # Only consecutive triggered the block
        assert any("consecutive losing trades" in r for r in m.reasons)

    def test_sell_never_marks_block_buy(self):
        m = evaluate_loss_limits(
            order=_sell(),
            daily_rule=DailyLossLimitRule(limit=100_000),
            weekly_rule=WeeklyLossLimitRule(limit=500_000),
            consecutive_rule=ConsecutiveLossRule(limit=3),
            daily_pnl=-1_000_000,
            weekly_pnl=-1_000_000,
            consecutive_loss_count=99,
        )
        assert m.block_buy is False  # SELL은 block_buy=False
        # warnings는 누적 — 운영자 인지
        assert len(m.warnings) >= 3


# ====================================================================
# RiskManager integration — evaluate_order이 새 rule을 호출
# ====================================================================


class TestRiskManagerIntegration:
    def test_existing_max_daily_loss_hard_reject_still_works(self):
        """기존 evaluate_order의 'daily loss limit reached' hard reject는 그대로."""
        risk = RiskManager(RiskPolicy(max_daily_loss=200_000))
        risk.daily_realized_pnl = -250_000
        result = risk.evaluate_order(
            order=_buy(1), mode=OperationMode.SIMULATION,
            balance=_balance(), positions=[], latest_price=75_000,
        )
        assert result.decision == RiskDecision.REJECTED
        assert "daily loss limit reached" in result.reasons

    def test_new_warn_threshold_appears_in_warnings(self):
        """daily_loss_warn_pct로 soft 단계 trigger."""
        risk = RiskManager(RiskPolicy(
            max_daily_loss=200_000,
            daily_loss_warn_pct=50.0,
            daily_loss_reduce_pct=80.0,
        ))
        risk.daily_realized_pnl = -120_000  # 60% 도달 → REDUCE_SIZE 미달, WARN 통과
        result = risk.evaluate_order(
            order=_buy(1), mode=OperationMode.SIMULATION,
            balance=_balance(), positions=[], latest_price=75_000,
        )
        # decision은 APPROVED(이전 hard reject 임계 미달)
        assert result.decision == RiskDecision.APPROVED
        # warnings에 daily loss surface
        assert any("daily loss" in w and "50.0%" not in w for w in result.warnings)

    def test_weekly_limit_rejects_new_buy(self):
        risk = RiskManager(RiskPolicy(weekly_loss_limit=500_000))
        result = risk.evaluate_order(
            order=_buy(1), mode=OperationMode.SIMULATION,
            balance=_balance(), positions=[], latest_price=75_000,
            weekly_realized_pnl=-500_000,
        )
        assert result.decision == RiskDecision.REJECTED
        assert any("weekly loss" in r for r in result.reasons)

    def test_weekly_limit_does_not_reject_sell(self):
        """SELL은 weekly_loss_limit 초과해도 통과."""
        risk = RiskManager(RiskPolicy(weekly_loss_limit=500_000))
        positions = [
            # SELL을 위한 보유
            __import__("app.brokers.base", fromlist=["Position"]).Position(
                symbol="005930", quantity=10, avg_price=70_000, market_price=75_000,
            ),
        ]
        result = risk.evaluate_order(
            order=_sell(1), mode=OperationMode.SIMULATION,
            balance=_balance(), positions=positions, latest_price=75_000,
            weekly_realized_pnl=-500_000,
        )
        assert result.decision == RiskDecision.APPROVED
        # warning surface는 가능
        assert any("weekly loss" in w for w in result.warnings)

    def test_consecutive_loss_rejects_new_buy(self):
        risk = RiskManager(RiskPolicy(consecutive_loss_limit=3))
        result = risk.evaluate_order(
            order=_buy(1), mode=OperationMode.SIMULATION,
            balance=_balance(), positions=[], latest_price=75_000,
            consecutive_loss_count=3,
        )
        assert result.decision == RiskDecision.REJECTED
        assert any("consecutive losing trades" in r for r in result.reasons)

    def test_consecutive_loss_does_not_reject_sell(self):
        from app.brokers.base import Position
        risk = RiskManager(RiskPolicy(consecutive_loss_limit=3))
        positions = [Position(symbol="005930", quantity=10, avg_price=70_000, market_price=75_000)]
        result = risk.evaluate_order(
            order=_sell(1), mode=OperationMode.SIMULATION,
            balance=_balance(), positions=positions, latest_price=75_000,
            consecutive_loss_count=10,
        )
        assert result.decision == RiskDecision.APPROVED
        assert any("consecutive losing trades" in w for w in result.warnings)

    def test_optional_kwargs_default_to_zero(self):
        """호출자가 weekly_pnl / consecutive_loss_count를 안 주입해도 동작."""
        risk = RiskManager(RiskPolicy(
            weekly_loss_limit=500_000, consecutive_loss_limit=3,
        ))
        result = risk.evaluate_order(
            order=_buy(1), mode=OperationMode.SIMULATION,
            balance=_balance(), positions=[], latest_price=75_000,
            # weekly/consecutive 미주입 — 0으로 처리, ALLOW
        )
        assert result.decision == RiskDecision.APPROVED


# ====================================================================
# Helpers — daily_pnl 모듈 새 함수
# ====================================================================


def _seed_filled(client, *, symbol, side, qty, price, days_ago=0):
    """test util — audit row를 ts=오늘-days_ago로 직접 시뮬."""
    with client.test_db_factory() as db:
        ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
        row = OrderAuditLog(
            mode="SIMULATION", symbol=symbol, side=side, quantity=qty,
            order_type="MARKET", latest_price=price,
            decision="APPROVED", reasons=[], executed=True,
            avg_fill_price=price, filled_quantity=qty, broker_status="FILLED",
            created_at=ts,
        )
        db.add(row)
        db.commit()


class TestWeekStartKst:
    def test_monday_start(self):
        # 2026-05-09 is a Saturday → Monday = 2026-05-04
        from datetime import date
        assert week_start_kst(date(2026, 5, 9)) == date(2026, 5, 4)

    def test_monday_returns_self(self):
        from datetime import date
        assert week_start_kst(date(2026, 5, 4)) == date(2026, 5, 4)


class TestComputeWeeklyPnl:
    def test_empty_db_returns_zero(self, client):
        with client.test_db_factory() as db:
            assert compute_weekly_realized_pnl_kst(db) == 0

    def test_aggregates_only_this_weeks_sells(self, client):
        # 이번 주 매수 + 이번 주 매도 (loss): -100 (1주)
        _seed_filled(client, symbol="X", side="BUY",  qty=1, price=100)
        _seed_filled(client, symbol="X", side="SELL", qty=1, price=90)
        with client.test_db_factory() as db:
            pnl = compute_weekly_realized_pnl_kst(db)
        assert pnl == -10  # (90 - 100) * 1


class TestCountConsecutiveLosingTrades:
    def test_empty_db_returns_zero(self, client):
        with client.test_db_factory() as db:
            assert count_consecutive_losing_trades(db) == 0

    def test_two_consecutive_losses(self, client):
        # 연속 손실 2건
        _seed_filled(client, symbol="X", side="BUY",  qty=1, price=100)
        _seed_filled(client, symbol="X", side="SELL", qty=1, price=90)   # -10
        _seed_filled(client, symbol="X", side="BUY",  qty=1, price=100)
        _seed_filled(client, symbol="X", side="SELL", qty=1, price=95)   # -5
        with client.test_db_factory() as db:
            assert count_consecutive_losing_trades(db) == 2

    def test_winning_trade_resets_count(self, client):
        # lose, lose, win, lose → trailing은 마지막 lose 1건만
        _seed_filled(client, symbol="X", side="BUY",  qty=1, price=100)
        _seed_filled(client, symbol="X", side="SELL", qty=1, price=90)   # lose
        _seed_filled(client, symbol="X", side="BUY",  qty=1, price=100)
        _seed_filled(client, symbol="X", side="SELL", qty=1, price=80)   # lose
        _seed_filled(client, symbol="X", side="BUY",  qty=1, price=100)
        _seed_filled(client, symbol="X", side="SELL", qty=1, price=120)  # win — resets
        _seed_filled(client, symbol="X", side="BUY",  qty=1, price=100)
        _seed_filled(client, symbol="X", side="SELL", qty=1, price=85)   # lose
        with client.test_db_factory() as db:
            assert count_consecutive_losing_trades(db) == 1

    def test_zero_lookback_returns_zero(self, client):
        _seed_filled(client, symbol="X", side="BUY",  qty=1, price=100)
        _seed_filled(client, symbol="X", side="SELL", qty=1, price=90)
        with client.test_db_factory() as db:
            assert count_consecutive_losing_trades(db, lookback=0) == 0


# ====================================================================
# Safety
# ====================================================================


class TestSafety:
    def test_module_does_not_import_broker_or_executor(self):
        import inspect

        from app.risk import loss_limits as mod
        src = inspect.getsource(mod)
        forbidden = (
            "from app.brokers.kis", "from app.permission",
            "from app.execution.executor", "from app.execution.order_router",
            "place_order(", "route_order(",
        )
        for f in forbidden:
            assert f not in src, f"forbidden symbol: {f}"

    def test_rule_evaluation_is_pure(self):
        """check()는 input 변경 없음 — 순수 함수."""
        rule = DailyLossLimitRule(limit=100_000)
        order = _buy()
        rule.evaluate(daily_pnl=-50_000, order=order)
        # frozen dataclass라 mutate 자체 불가능, side=BUY 그대로.
        assert order.side == OrderSide.BUY


# ====================================================================
# REDUCE_SIZE TODO surface — warnings only (정책 문서화)
# ====================================================================


class TestReduceSizeIsAdvisoryOnly:
    def test_reduce_size_does_not_block_or_resize(self):
        """현재 RiskCheckResult가 size 조정을 직접 지원하지 않으므로 REDUCE_SIZE는
        warning surface로만 동작 — 기존 RiskCheckResult 응답 호환성 유지."""
        risk = RiskManager(RiskPolicy(
            max_daily_loss=200_000,
            daily_loss_warn_pct=50.0,
            daily_loss_reduce_pct=70.0,
        ))
        risk.daily_realized_pnl = -150_000  # 75% — REDUCE_SIZE 영역
        result = risk.evaluate_order(
            order=_buy(1), mode=OperationMode.SIMULATION,
            balance=_balance(), positions=[], latest_price=75_000,
        )
        # decision은 APPROVED 그대로 — size는 호출자 책임 (TODO: PositionSizingAgent)
        assert result.decision == RiskDecision.APPROVED
        assert any("REDUCE_SIZE" in w for w in result.warnings)
