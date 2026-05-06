"""Agent Council tests (185, MUST)."""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai.agents import (
    ChiefTradingAgent,
    CouncilContext,
    EntryTimingAgent,
    ExitTimingAgent,
    MarketRegimeAgent,
    NewsTrendAgent,
    PositionSizingAgent,
    PostTradeReviewAgent,
    RiskOfficerAgent,
    StockSelectionAgent,
    StrategySelectionAgent,
    new_chain_id,
    persist_decision,
)
from app.db.base import Base
from app.db.models import AgentDecisionLog


def _session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


def _ctx(**overrides) -> CouncilContext:
    base = dict(
        symbol="005930", last_close=110, prev_close=100,
        equity=10_000_000, notional=110_000,
        regime="trending_up", sample_size=60,
        candidates=["005930"], risk_pct=5.0,
        emergency_stop=False, max_order_notional=1_000_000,
        sentiment=50, unrealized_pct=0.0,
    )
    base.update(overrides)
    return CouncilContext(**base)


# ---------- individual agents ----------

def test_market_regime_agent_surfaces_regime():
    a = MarketRegimeAgent()
    d = a.decide(regime="trending_up", sample_size=60)
    assert d.decision == "INFO"
    assert d.meta["regime"] == "trending_up"
    assert d.confidence >= 80


def test_strategy_selection_agent_picks_for_regime():
    a = StrategySelectionAgent()
    assert a.decide(regime="ranging").meta["strategy"] == "rsi_reversion"
    assert a.decide(regime="trending_up").meta["strategy"] == "orb_vwap"
    assert a.decide(regime="any").meta["strategy"] == "sma_crossover"


def test_stock_selection_no_candidates_holds():
    a = StockSelectionAgent()
    d = a.decide(candidates=[])
    assert d.decision == "HOLD"


def test_stock_selection_first_candidate():
    a = StockSelectionAgent()
    d = a.decide(candidates=["A", "B", "C"])
    assert d.symbol == "A"


def test_position_sizing_basic():
    a = PositionSizingAgent()
    # equity 10M, price 100K, risk 5% = target 500K → qty = 5.
    d = a.decide(equity=10_000_000, price=100_000, risk_pct=5.0)
    assert d.meta["quantity"] == 5


def test_position_sizing_invalid_inputs():
    a = PositionSizingAgent()
    assert a.decide(equity=0, price=100, risk_pct=5).decision == "HOLD"
    assert a.decide(equity=100, price=0, risk_pct=5).decision == "HOLD"


def test_risk_officer_rejects_emergency_stop():
    a = RiskOfficerAgent()
    d = a.decide(notional=100_000, max_order_notional=1_000_000, emergency_stop=True)
    assert d.decision == "REJECT"


def test_risk_officer_rejects_oversized_notional():
    a = RiskOfficerAgent()
    d = a.decide(notional=2_000_000, max_order_notional=1_000_000, emergency_stop=False)
    assert d.decision == "REJECT"


def test_risk_officer_approves_normal():
    a = RiskOfficerAgent()
    d = a.decide(notional=500_000, max_order_notional=1_000_000, emergency_stop=False)
    assert d.decision == "APPROVE"


def test_entry_timing_buy_on_close_up():
    a = EntryTimingAgent()
    assert a.decide(last_close=110, prev_close=100).decision == "BUY"
    assert a.decide(last_close=100, prev_close=110).decision == "HOLD"


def test_exit_timing_stop_loss():
    a = ExitTimingAgent()
    d = a.decide(unrealized_pct=-0.03, stop_loss_pct=2.0, take_profit_pct=5.0)
    assert d.decision == "SELL"
    assert d.meta["reason_code"] == "stop_loss"


def test_exit_timing_take_profit():
    a = ExitTimingAgent()
    d = a.decide(unrealized_pct=+0.06, stop_loss_pct=2.0, take_profit_pct=5.0)
    assert d.decision == "SELL"
    assert d.meta["reason_code"] == "take_profit"


def test_exit_timing_hold_within_band():
    a = ExitTimingAgent()
    assert a.decide(unrealized_pct=+0.01).decision == "HOLD"


def test_news_trend_sentiment_branches():
    a = NewsTrendAgent()
    assert a.decide(sentiment=80).decision == "INFO"
    assert a.decide(sentiment=50).decision == "INFO"
    assert a.decide(sentiment=20).decision == "WARN"


def test_post_trade_review_insufficient_sample():
    a = PostTradeReviewAgent()
    d = a.decide(realized_pnl=100, win_rate=0.5, trades=3)
    assert d.decision == "INFO"
    assert "insufficient_sample" in d.reasons[0]


def test_post_trade_review_underperform():
    a = PostTradeReviewAgent()
    d = a.decide(realized_pnl=-1000, win_rate=0.3, trades=20)
    assert d.decision == "WARN"
    assert d.meta["verdict"] == "underperform"


# ---------- ChiefTradingAgent orchestrator ----------

def test_chief_returns_buy_when_entry_up_and_news_neutral():
    chief = ChiefTradingAgent()
    decision, members = chief.coordinate(_ctx(last_close=110, prev_close=100))
    assert decision.decision == "BUY"
    assert decision.symbol == "005930"
    # 9 member agents.
    assert len(members) == 9


def test_chief_holds_when_close_not_up():
    chief = ChiefTradingAgent()
    decision, _ = chief.coordinate(_ctx(last_close=100, prev_close=110))
    assert decision.decision == "HOLD"


def test_chief_rejects_when_emergency_stop():
    chief = ChiefTradingAgent()
    decision, _ = chief.coordinate(_ctx(emergency_stop=True))
    assert decision.decision == "REJECT"


def test_chief_rejects_when_oversized_notional():
    chief = ChiefTradingAgent()
    decision, _ = chief.coordinate(_ctx(notional=99_999_999, max_order_notional=1_000_000))
    assert decision.decision == "REJECT"


def test_chief_sells_on_stop_loss():
    chief = ChiefTradingAgent()
    decision, _ = chief.coordinate(_ctx(unrealized_pct=-0.05))
    assert decision.decision == "SELL"
    assert decision.meta["exit_reason_code"] == "stop_loss"


def test_chief_holds_when_news_warns():
    chief = ChiefTradingAgent()
    # entry would be BUY but news WARN → HOLD.
    decision, _ = chief.coordinate(_ctx(last_close=110, prev_close=100, sentiment=20))
    assert decision.decision == "HOLD"


def test_chief_chain_id_links_all_members():
    chief = ChiefTradingAgent()
    decision, members = chief.coordinate(_ctx())
    assert decision.chain_id is not None
    for m in members:
        assert m.chain_id == decision.chain_id


def test_chief_explicit_chain_id_used():
    chief = ChiefTradingAgent()
    custom = "test-chain-001"
    decision, members = chief.coordinate(_ctx(), chain_id=custom)
    assert decision.chain_id == custom
    assert all(m.chain_id == custom for m in members)


# ---------- persist_decision ----------

def test_persist_decision_creates_row():
    Session = _session()
    chief = ChiefTradingAgent()
    chain = new_chain_id()
    decision, members = chief.coordinate(_ctx(), chain_id=chain)
    with Session() as db:
        persist_decision(db, decision, mode="VIRTUAL_AI_EXECUTION")
        for m in members:
            persist_decision(db, m, mode="VIRTUAL_AI_EXECUTION")
        db.commit()
        rows = db.execute(
            select(AgentDecisionLog).where(AgentDecisionLog.chain_id == chain)
        ).scalars().all()
    # 1 chief + 9 members = 10.
    assert len(rows) == 10
    chief_rows = [r for r in rows if r.agent_name == "ChiefTradingAgent"]
    assert len(chief_rows) == 1


def test_persist_decision_meta_optional():
    """meta=None인 agent도 정상 영구화."""
    Session = _session()
    from app.ai.agents.base import AgentDecision
    d = AgentDecision(
        agent_name="TestAgent", decision="INFO", confidence=50,
        reasons=["test"], meta={},
    )
    with Session() as db:
        persist_decision(db, d, mode="SIMULATION")
        db.commit()
        row = db.execute(select(AgentDecisionLog)).scalar_one()
    assert row.agent_name == "TestAgent"
    assert row.decision == "INFO"


def test_decide_required_to_be_implemented():
    """ABC interface — Chief.decide 호출 가능."""
    chief = ChiefTradingAgent()
    d = chief.decide(ctx=_ctx())
    assert d.decision in ("BUY", "SELL", "HOLD", "REJECT")
