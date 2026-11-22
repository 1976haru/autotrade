"""Deterministic BackgroundTickDriver -> Council -> real order router path."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.agent_council import CouncilAction, StrategyMarketInput
from app.auto_paper.background_driver import BackgroundTickDriver
from app.brokers.base import Balance, OrderResult, OrderSide, OrderStatus, Position, Quote
from app.brokers.kis import KisBrokerAdapter
from app.core.modes import OperationMode
from app.db.base import Base
from app.execution.order_router import route_order
from app.kis_paper.auto_executor import KisPaperAutoDecision
from app.kis_paper.driver_bridge import kis_paper_realtime_scan_tick
from app.market_data.kis_realtime import KIS_PRICE_OK, KisRealtimeQuote
from app.risk.risk_manager import RiskManager, RiskPolicy


NOW = datetime(2026, 11, 4, 1, 0, tzinfo=timezone.utc)  # 10:00 KST
SYMBOLS = ["000001", "000002", "000003", "000004", "000005", "000006"]


class FakeKisPaperBroker(KisBrokerAdapter):
    """KIS adapter type with deterministic, local-only fills and account state."""

    def __init__(self, *, starting_cash=10_000_000):
        super().__init__(app_key="fake", app_secret="fake", account_no="1234567801",
                         is_paper=True, client=object())
        self.cash = starting_cash
        self.holdings: dict[str, int] = {}
        self.prices = {symbol: 10_000 for symbol in SYMBOLS}
        self.orders: list[dict] = []
        self.executed_orders: list[tuple[str, str, int]] = []
        self.order_no = 0

    async def get_price(self, symbol):
        return Quote(symbol=symbol, price=self.prices[symbol],
                     timestamp=NOW.isoformat(), source="kis")

    async def get_balance(self):
        invested = sum(self.holdings[s] * self.prices[s] for s in self.holdings)
        return Balance(cash=self.cash, equity=self.cash + invested,
                       buying_power=self.cash)

    async def get_positions(self):
        return [Position(symbol=s, quantity=q, avg_price=self.prices[s],
                         market_price=self.prices[s], sellable_quantity=q)
                for s, q in self.holdings.items() if q > 0]

    async def get_orderable_cash(self, *, symbol, price):
        return self.cash

    async def get_orderable_buy_capacity(self, *, symbol, price):
        return {
            "orderable_cash_krw": self.cash,
            "no_credit_buy_amount_krw": self.cash,
            "no_credit_buy_quantity": self.cash // max(1, int(price)),
            "calculation_price_krw": int(price),
        }

    async def list_today_orders(self, *, kst_date):
        return [o for o in self.orders if o["remaining"] > 0]

    async def place_order(self, order):
        price = int(order.limit_price or self.prices[order.symbol])
        notional = price * int(order.quantity)
        self.order_no += 1
        if order.side == OrderSide.BUY:
            if notional > self.cash:
                return OrderResult(order_id=f"F{self.order_no}", status=OrderStatus.REJECTED,
                                   symbol=order.symbol, side=order.side,
                                   quantity=order.quantity, message="fake cash insufficient")
            self.cash -= notional
            self.holdings[order.symbol] = self.holdings.get(order.symbol, 0) + order.quantity
        else:
            held = self.holdings.get(order.symbol, 0)
            if order.quantity > held:
                return OrderResult(order_id=f"F{self.order_no}", status=OrderStatus.REJECTED,
                                   symbol=order.symbol, side=order.side,
                                   quantity=order.quantity, message="fake sell exceeds holding")
            self.holdings[order.symbol] = held - order.quantity
            self.cash += notional
        self.orders.append({"side": order.side.value, "remaining": 0})
        self.executed_orders.append((order.symbol, order.side.value, int(order.quantity)))
        return OrderResult(order_id=f"F{self.order_no}", status=OrderStatus.FILLED,
                           symbol=order.symbol, side=order.side,
                           quantity=order.quantity, filled_quantity=order.quantity,
                           avg_fill_price=price, message="deterministic fake fill")


class _Loop:
    def __init__(self):
        self.cycle_count = 0

    def status(self, *, now):
        return SimpleNamespace(state="RUNNING", cycle_count=self.cycle_count)

    def tick(self):
        self.cycle_count += 1
        return SimpleNamespace(cycle_count=self.cycle_count)


class _Council:
    def __init__(self, symbol, side):
        self.final_action = CouncilAction(side)
        self.selected_strategies = ["DETERMINISTIC_TEST_SIGNAL"]
        self.votes = []
        self.risk_flags = []
        self.reason_code = "FIXTURE_SIGNAL"
        self.confidence = 0.95
        self.quality_score = 95
        self.market_regime = "TREND_UP"
        self.symbol = symbol

    def to_dict(self):
        return {"symbol": self.symbol, "action": self.final_action.value}

    def to_kis_paper_decision(self, *, quantity, price):
        return KisPaperAutoDecision(
            symbol=self.symbol, side=self.final_action.value, quantity=quantity,
            price=price, selected_strategies=self.selected_strategies,
            confidence=0.95, quality_score=95, entry_reason="deterministic integration fixture",
            has_exit_plan=True,
            exit_plan={"stop_loss_pct": 1.5, "take_profit_pct": 2.5},
        )


async def _market_input(symbol, *, client, now, market_is_open, **kwargs):
    price = 10_000
    market = StrategyMarketInput(
        symbol=symbol, current_price=price, prev_close=9_800, open_price=9_900,
        vwap=9_950, opening_range_high=9_980, opening_range_low=9_850,
        recent_closes=(9_800, 9_850, 9_900, 9_950, 10_000),
        current_volume=200, avg_volume=100,
        market_regime="TREND_UP", regime_decision="ALLOW",
    )
    quote = KisRealtimeQuote(symbol=symbol, status=KIS_PRICE_OK, price=price,
                             is_stale=False)
    return market, quote


@pytest.fixture
def harness(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = SimpleNamespace(
        enable_ai_paper_background_tick=True,
        enable_kis_paper_auto_trading=True,
        kis_paper_auto_order_dry_run=False,
        kis_paper_fill_polling=True,
        kis_is_paper=True,
        enable_live_trading=False,
        default_mode=OperationMode.PAPER,
        market_data_provider="kis",
        kis_app_key="FAKE", kis_app_secret="FAKE", kis_account_no="1234567801",
        kis_product_code="01",
        kis_paper_auto_order_window_start="09:05",
        kis_paper_auto_order_window_end="14:50",
        kis_paper_auto_min_confidence=0.6,
        kis_paper_auto_min_quality_score=60,
        kis_paper_auto_max_order_notional=2_000_000,
        kis_paper_auto_max_orders_per_day=10,
        kis_paper_max_concurrent_positions=5,
        kis_paper_per_symbol_notional_krw=1_000_000,
        kis_paper_daily_buy_limit_krw=10_000_000,
        kis_paper_max_new_positions_per_tick=1,
        kis_paper_smoke_mode=False,
        ai_paper_tick_max_per_day=0,
        krx_calendar_override_path="",
        kis_paper_buy_cutoff_minutes_before_close=60,
        kis_paper_eod_flatten_minutes_before_close=15,
        kis_paper_eod_closing_auction_orders_enabled=False,
    )
    broker = FakeKisPaperBroker()
    risk = RiskManager(RiskPolicy(
        max_order_notional=1_000_000,
        max_positions=5,
        max_symbol_exposure=1_500_000,
        max_total_exposure=10_000_000,
        max_daily_loss=10_000_000,
        enable_live_trading=False,
        enable_ai_execution=False,
        enforce_ai_reasoning=False,
        stale_price_max_age_seconds=0,
    ))
    loop = _Loop()
    monkeypatch.setenv("AUTOTRADE_PAPER_APPROVAL_FILE", str(tmp_path / "approval.json"))
    from app.kis_paper import one_click_session

    real_datetime = one_click_session.datetime

    class _FrozenDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz is not None else NOW.replace(tzinfo=None)

    monkeypatch.setattr(one_click_session, "datetime", _FrozenDateTime)
    create_approval = one_click_session.create_approval
    approval_db = session_factory()
    create_approval(
        db=approval_db, settings=settings, risk=risk,
        target_date="2026-11-04", actor="deterministic-test-operator",
        explicit_ack=True,
    )
    approval_db.close()
    from app.core import runtime_config
    monkeypatch.setattr(runtime_config, "effective_per_stock_budget",
                        lambda: int(settings.kis_paper_per_symbol_notional_krw))
    monkeypatch.setattr(runtime_config, "effective_max_concurrent_positions",
                        lambda: int(settings.kis_paper_max_concurrent_positions))
    monkeypatch.setattr(runtime_config, "effective_daily_buy_limit",
                        lambda: int(settings.kis_paper_daily_buy_limit_krw))
    selected = {"symbols": [], "actions": {}}
    from app.agents import agent_council
    from app.kis_paper import driver_bridge
    monkeypatch.setattr(agent_council, "run_agent_council",
                        lambda market, **kwargs: _Council(
                            market.symbol, selected["actions"].get(market.symbol, "BUY"),
                        ))
    monkeypatch.setattr(driver_bridge, "_broker_is_kis_paper", lambda _broker: True)

    async def auto_tick(*, now):
        result = await kis_paper_realtime_scan_tick(
            now=now, session_factory=session_factory, broker=broker, risk=risk,
            route_order_fn=route_order, settings=settings,
            market_input_fn=_market_input, universe_symbols=list(selected["symbols"]),
            client=object(),
        )
        selected["last_scan"] = result
        return result

    driver = BackgroundTickDriver(
        settings_provider=lambda: settings,
        loop_provider=lambda: loop,
        kis_auto_tick_fn=auto_tick,
        event_sink=lambda **kwargs: True,
        now_provider=lambda: NOW,
    )
    try:
        yield SimpleNamespace(driver=driver, broker=broker, settings=settings,
                              selected=selected, session_factory=session_factory,
                              risk=risk)
    finally:
        engine.dispose()


def _tick(harness, symbols, side=None):
    harness.selected["symbols"] = list(symbols)
    harness.selected["actions"] = {symbols[0]: side} if side else {}
    return asyncio.run(harness.driver.kis_paper_tick(NOW))


def test_scan_cycle_persists_order_neutral_council_audit(harness):
    _tick(harness, [SYMBOLS[0]], side="HOLD")

    from app.db.models import AgentDecisionLog

    db = harness.session_factory()
    try:
        row = db.query(AgentDecisionLog).filter_by(
            agent_name="kis_paper_scan_audit",
        ).one()
        assert row.mode == "PAPER"
        assert row.decision == "INFO"
        assert row.meta["audit_schema"] == "KIS_PAPER_SCAN_V1"
        assert row.meta["symbols_scanned"] == 1
        assert row.meta["council_evaluated"] == 1
        assert row.meta["council_actions"] == {"BUY": 0, "HOLD": 1, "SELL": 0}
        assert row.meta["council_results"] == [{
            "symbol": SYMBOLS[0], "action": "HOLD", "confidence": 0.95,
        }]
        assert row.meta["orders_attempted"] == 0
        assert row.meta["orders_submitted"] == 0
        assert row.meta["broker_order_sent"] is False
        assert row.meta["is_order_signal"] is False
        assert row.meta["is_live_authorization"] is False
    finally:
        db.close()


def test_background_driver_router_fills_five_positions_and_exits(harness):
    initial_cash = harness.broker.cash
    results = [_tick(harness, [symbol]) for symbol in SYMBOLS[:5]]
    assert all(result.executed for result in results)
    scan = harness.selected["last_scan"]
    assert scan["orders_submitted"] == 1, [
        (o.get("reason_code"), o.get("reason_message"), o.get("dry_run"), o.get("broker_order_sent"))
        for o in scan.get("orders", [])
    ]
    assert all(result.reason_code == "KIS_REALTIME_SCAN_DONE" for result in results)
    assert len(harness.broker.holdings) == 5
    assert sum(harness.broker.holdings.values()) == 495
    assert harness.broker.cash == initial_cash - 4_950_000
    from app.db.models import OrderAuditLog
    from app.kis_paper.account_scope import account_id_for, program_holdings
    audit_db = harness.session_factory()
    try:
        scoped = account_id_for("1234567801", is_paper=True)
        audit_rows = audit_db.query(OrderAuditLog).all()
        assert len(audit_rows) == 5
        assert all(row.account_id == scoped and row.filled_quantity > 0 for row in audit_rows)
        assert program_holdings(audit_db, scoped) == {symbol: 99 for symbol in SYMBOLS[:5]}
    finally:
        audit_db.close()

    sixth = _tick(harness, [SYMBOLS[5]])
    assert sixth.reason_code == "KIS_REALTIME_SCAN_DONE"
    assert any(item.get("reason_code") == "MAX_CONCURRENT_POSITIONS_REACHED"
               for item in harness.selected["last_scan"]["skipped"])
    assert harness.broker.holdings.get(SYMBOLS[5], 0) == 0

    duplicate = _tick(harness, [SYMBOLS[0]])
    assert harness.broker.holdings[SYMBOLS[0]] == 99

    before_sell_cash = harness.broker.cash
    sold = _tick(harness, [SYMBOLS[0]], side="SELL")
    assert harness.broker.holdings[SYMBOLS[0]] == 0
    assert len([p for p in harness.broker.holdings.values() if p > 0]) == 4, (
        harness.broker.holdings, harness.broker.executed_orders,
    )
    assert harness.broker.cash == before_sell_cash + 990_000
    assert duplicate.executed and sold.executed

    for symbol in SYMBOLS[1:5]:
        exit_result = _tick(harness, [symbol], side="SELL")
        assert exit_result.executed
        assert harness.broker.holdings[symbol] == 0
    assert not any(harness.broker.holdings.values())
    assert harness.broker.cash == initial_cash


def test_two_million_allocation_resizes_to_five_positions_with_cost_reserve(harness):
    import os
    from pathlib import Path
    from app.kis_paper.one_click_session import create_approval

    harness.settings.kis_paper_per_symbol_notional_krw = 2_000_000
    # Keep the per-order ceiling aligned with this isolated scenario. The
    # normal operating profile remains 1M; this fixture alone validates the
    # existing 2M hard PAPER ceiling.
    harness.settings.kis_paper_auto_max_order_notional = 2_000_000
    harness.risk.policy.max_order_notional = 2_000_000
    # The shared fixture already created a 1M approval. Replace only its
    # isolated temp approval file so the 2M scenario exercises a fresh,
    # matching approval snapshot (never the operator's APPDATA approval).
    approval_file = Path(os.environ["AUTOTRADE_PAPER_APPROVAL_FILE"])
    approval_file.unlink(missing_ok=True)
    approval_db = harness.session_factory()
    # create_approval is intentionally idempotent for an already approved
    # date/account in its DB ledger too. Remove only the fixture's in-memory
    # test row before writing the matching snapshot.
    from app.db.models import PaperSessionApproval
    approval_db.query(PaperSessionApproval).filter_by(
        trading_date="2026-11-04",
    ).delete(synchronize_session=False)
    approval_db.commit()
    create_approval(
        db=approval_db, settings=harness.settings, risk=harness.risk,
        target_date="2026-11-04", actor="deterministic-test-operator",
        explicit_ack=True,
    )
    approval_db.close()

    results = [_tick(harness, [symbol]) for symbol in SYMBOLS[:5]]
    assert len([qty for qty in harness.broker.holdings.values() if qty > 0]) == 5, [
        (harness.selected["last_scan"].get("candidates_found"),
         harness.selected["last_scan"].get("orders_attempted"),
         harness.selected["last_scan"].get("orders_submitted"),
         [{k: order.get(k) for k in ("reason_code", "reason_message", "submitted",
                                      "dry_run", "extra_meta", "routing_reasons")}
          for order in harness.selected["last_scan"].get("orders", [])],
         harness.selected["last_scan"].get("skipped"))
    ]
    assert sum(harness.broker.holdings.values()) == 990
    invested = sum(harness.broker.holdings.values()) * 10_000
    assert invested == 9_900_000
    assert invested + (invested * 100 + 9_999) // 10_000 <= 10_000_000
    assert harness.broker.cash == 100_000
    assert all(result.executed for result in results)
