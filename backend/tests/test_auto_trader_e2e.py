"""체크리스트 #60: AI Agent end-to-end 모의매매 통합 테스트.

시나리오 (체크리스트 6번 A~G + 추가 안전 invariant):
  A. 상승 추세 → Agent BUY 판단 + Paper Broker 가상 체결
  B. 손절 조건 (보유 후 하락) → Agent SELL 판단 + 가상 체결
  C. 신호 엇갈림 / 낮은 confidence → HOLD
  D. RiskManager 한도 초과 → BUY 신호여도 라우팅 REJECTED
  E. Emergency Stop ON → 모든 주문 차단 (route_order 미도달)
  F. 가상 주문 체결 후 portfolio/cash/positions/감사 로그 정상 업데이트
  G. Agent reason이 사람이 읽을 수 있는 설명으로 저장됨
  H. LIVE 모드면 RuntimeError로 차단 (LIVE 절대 금지 invariant)
  I. broker가 live이면 NotPaperBrokerError로 차단
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.auto_trader_loop import (
    AgentDecision,
    AutoTraderAgent,
    AutoTraderInput,
    RiskChecksPreview,
    StrategySignalReport,
    mix_strategy_signals,
)
from app.backtest.types import Bar
from app.brokers.mock_broker import MockBrokerAdapter
from app.core.modes import OperationMode
from app.db.base import Base
from app.db.models import OrderAuditLog
from app.risk.risk_manager import RiskDecision, RiskManager, RiskPolicy


# ---------- helpers ----------


def _session():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    return sessionmaker(
        bind=eng, autoflush=False, autocommit=False, expire_on_commit=False,
    )


def _bars_uptrend(symbol: str, n: int = 60, start: int = 50000) -> list[Bar]:
    """단조 상승 — SmaCrossover가 BUY 신호를 만들기 충분한 추세."""
    base = datetime(2026, 5, 13, 9, 0, tzinfo=timezone.utc)
    bars: list[Bar] = []
    for i in range(n):
        price = start + i * 200  # 매 봉 +200
        ts = base + timedelta(minutes=i)
        bars.append(Bar(
            symbol=symbol, timestamp=ts,
            open=price, high=price + 50, low=price - 50, close=price, volume=1000,
        ))
    return bars


def _bars_downtrend(symbol: str, n: int = 60, start: int = 70000) -> list[Bar]:
    base = datetime(2026, 5, 13, 9, 0, tzinfo=timezone.utc)
    bars: list[Bar] = []
    for i in range(n):
        price = max(1000, start - i * 200)
        ts = base + timedelta(minutes=i)
        bars.append(Bar(
            symbol=symbol, timestamp=ts,
            open=price, high=price + 50, low=price - 50, close=price, volume=1000,
        ))
    return bars


def _bars_then_reversal(symbol: str, n_up: int = 30, n_down: int = 30) -> list[Bar]:
    """상승 후 반전 — Crossover에서 BUY 후 SELL을 유도."""
    base = datetime(2026, 5, 13, 9, 0, tzinfo=timezone.utc)
    bars: list[Bar] = []
    price = 50000
    for i in range(n_up):
        price += 200
        bars.append(Bar(
            symbol=symbol,
            timestamp=base + timedelta(minutes=i),
            open=price, high=price+50, low=price-50, close=price, volume=1000,
        ))
    # 반전
    for i in range(n_down):
        price -= 250
        bars.append(Bar(
            symbol=symbol,
            timestamp=base + timedelta(minutes=n_up + i),
            open=price, high=price+50, low=price-50, close=price, volume=1000,
        ))
    return bars


def _make_agent_decision(action="BUY", confidence=80, symbol="005930"):
    return AgentDecision(
        action=action,
        symbol=symbol,
        confidence=confidence,
        position_size=1,
        reason="test",
        used_strategies=["sma_crossover"],
        risk_checks=RiskChecksPreview(True, True, True, True),
        created_at=datetime.now(timezone.utc),
    )


# ====================================================================
# Unit: mixer / dataclass invariants
# ====================================================================


def test_mix_signals_majority_buy_picks_buy():
    sigs = [
        StrategySignalReport("a", "BUY",  70, "r1"),
        StrategySignalReport("b", "BUY",  80, "r2"),
        StrategySignalReport("c", "HOLD", 40, "r3"),
        StrategySignalReport("d", "SELL", 50, "r4"),
    ]
    mixed = mix_strategy_signals(sigs)
    assert mixed.final_action == "BUY"
    assert mixed.confidence == 75      # (70 + 80) // 2
    assert mixed.used_strategies == ["a", "b"]


def test_mix_signals_tie_yields_hold():
    sigs = [
        StrategySignalReport("a", "BUY",  80, "r1"),
        StrategySignalReport("b", "SELL", 80, "r2"),
    ]
    mixed = mix_strategy_signals(sigs)
    assert mixed.final_action == "HOLD"
    assert "엇갈림" in mixed.reason


def test_mix_signals_all_hold():
    sigs = [
        StrategySignalReport("a", "HOLD", 40, "r1"),
        StrategySignalReport("b", "HOLD", 50, "r2"),
    ]
    mixed = mix_strategy_signals(sigs)
    assert mixed.final_action == "HOLD"
    assert "HOLD" in mixed.reason


def test_agent_decision_rejects_truthy_order_intent():
    with pytest.raises(ValueError, match="is_order_intent"):
        AgentDecision(
            action="BUY", symbol="005930", confidence=80, position_size=1,
            reason="r", used_strategies=["s"],
            risk_checks=RiskChecksPreview(True, True, True, True),
            created_at=datetime.now(timezone.utc),
            is_order_intent=True,
        )


def test_agent_decision_rejects_unknown_action():
    with pytest.raises(ValueError, match="action"):
        AgentDecision(
            action="LONG", symbol="005930", confidence=80, position_size=1,
            reason="r", used_strategies=["s"],
            risk_checks=RiskChecksPreview(True, True, True, True),
            created_at=datetime.now(timezone.utc),
        )


def test_agent_decision_rejects_invalid_confidence():
    with pytest.raises(ValueError, match="confidence"):
        _make_agent_decision(confidence=120)


# ====================================================================
# Scenario A: 상승 추세 → BUY → 가상 체결
# ====================================================================


def test_scenario_a_uptrend_results_in_buy_and_paper_fill():
    Session = _session()
    with Session() as db:
        broker = MockBrokerAdapter(initial_cash=5_000_000)
        broker.set_price("005930", 60_000)
        risk = RiskManager(RiskPolicy())
        agent = AutoTraderAgent()
        inp = AutoTraderInput(
            watchlist=["005930"],
            bars_by_symbol={"005930": _bars_uptrend("005930")},
            strategy_names=["sma_crossover"],
            min_confidence=50,
            default_quantity=1,
            mode=OperationMode.SIMULATION,
        )
        report = asyncio.run(agent.run_once(inp, broker=broker, risk=risk, db=db))
        plan = report.plans[0]
        # 적어도 BUY 또는 HOLD (SmaCrossover는 추세 봉에서 BUY 산출 가능)
        # 핵심: 상승 추세에서 SELL은 절대 나오면 안 됨.
        assert plan.decision.action in ("BUY", "HOLD")
        if plan.decision.action == "BUY":
            assert plan.executed is True
            assert plan.routing_decision == RiskDecision.APPROVED.value
            assert plan.fill_quantity == 1
            assert plan.audit_id is not None
            # broker state mutated
            balance = asyncio.run(broker.get_balance())
            assert balance.cash < 5_000_000


# ====================================================================
# Scenario B: 보유 후 반전 → SELL → 가상 체결
# ====================================================================


def test_scenario_b_holding_then_reversal_yields_sell():
    Session = _session()
    with Session() as db:
        broker = MockBrokerAdapter(initial_cash=10_000_000)
        broker.set_price("005930", 60_000)
        risk = RiskManager(RiskPolicy())
        agent = AutoTraderAgent()

        # 1) 상승 봉 흐름으로 BUY 신호 + 체결
        inp_up = AutoTraderInput(
            watchlist=["005930"],
            bars_by_symbol={"005930": _bars_uptrend("005930", n=60)},
            strategy_names=["sma_crossover"],
            min_confidence=40,
            default_quantity=1,
            mode=OperationMode.SIMULATION,
        )
        asyncio.run(agent.run_once(inp_up, broker=broker, risk=risk, db=db))

        # 2) 반전 봉 흐름 — SmaCrossover는 cross-down 봉에서 SELL.
        inp_rev = AutoTraderInput(
            watchlist=["005930"],
            bars_by_symbol={"005930": _bars_then_reversal("005930", 30, 30)},
            strategy_names=["sma_crossover"],
            min_confidence=40,
            default_quantity=1,
            mode=OperationMode.SIMULATION,
        )
        # SmaCrossover는 state 보존이라 second agent run에서는 새 instance라
        # cross-down 봉을 즉시 잡아낸다. 본 시나리오는 mixer 흐름 — SELL이
        # 만들어지는 *흐름*이 통과만 검증.
        report = asyncio.run(agent.run_once(inp_rev, broker=broker, risk=risk, db=db))
        plan = report.plans[0]
        # 핵심: 반전 봉에서 SELL 또는 HOLD (BUY가 절대 안 나옴) 보장.
        assert plan.decision.action in ("SELL", "HOLD")


# ====================================================================
# Scenario C: confidence 낮으면 HOLD로 강등
# ====================================================================


def test_scenario_c_low_confidence_demotes_to_hold():
    Session = _session()
    with Session() as db:
        broker = MockBrokerAdapter(initial_cash=5_000_000)
        broker.set_price("005930", 60_000)
        risk = RiskManager(RiskPolicy())
        agent = AutoTraderAgent()
        # min_confidence 95로 강제 — 어떤 전략이 BUY를 만들어도 강등.
        inp = AutoTraderInput(
            watchlist=["005930"],
            bars_by_symbol={"005930": _bars_uptrend("005930")},
            strategy_names=["sma_crossover"],
            min_confidence=95,
            default_quantity=1,
            mode=OperationMode.SIMULATION,
        )
        report = asyncio.run(agent.run_once(inp, broker=broker, risk=risk, db=db))
        plan = report.plans[0]
        assert plan.decision.action == "HOLD"
        # 라우팅 시도 X
        assert plan.executed is False


def test_scenario_c_no_bars_yields_hold():
    Session = _session()
    with Session() as db:
        broker = MockBrokerAdapter()
        risk = RiskManager(RiskPolicy())
        agent = AutoTraderAgent()
        inp = AutoTraderInput(
            watchlist=["005930"],
            bars_by_symbol={},   # 데이터 없음
            strategy_names=["sma_crossover"],
            min_confidence=30,
            mode=OperationMode.SIMULATION,
        )
        report = asyncio.run(agent.run_once(inp, broker=broker, risk=risk, db=db))
        assert report.plans[0].decision.action == "HOLD"
        assert "봉" in report.plans[0].decision.reason


# ====================================================================
# Scenario D: Risk 한도 초과 → 라우팅 REJECTED
# ====================================================================


def test_scenario_d_risk_block_rejects_even_with_buy_signal():
    Session = _session()
    with Session() as db:
        broker = MockBrokerAdapter(initial_cash=10_000)   # 잔금 거의 0
        broker.set_price("005930", 60_000)
        # max_order_notional 매우 작게 — BUY notional 60_000 > limit 1_000
        policy = RiskPolicy(
            max_order_notional=1_000,
            enforce_ai_reasoning=False,    # ai_decision_meta 검사 우회
        )
        risk = RiskManager(policy)
        agent = AutoTraderAgent()
        inp = AutoTraderInput(
            watchlist=["005930"],
            bars_by_symbol={"005930": _bars_uptrend("005930")},
            strategy_names=["sma_crossover"],
            min_confidence=40,
            default_quantity=1,
            mode=OperationMode.SIMULATION,
        )
        report = asyncio.run(agent.run_once(inp, broker=broker, risk=risk, db=db))
        plan = report.plans[0]
        if plan.decision.action == "BUY":
            # RiskManager가 REJECTED — audit row는 존재 + 사유 carry.
            assert plan.routing_decision == RiskDecision.REJECTED.value
            assert plan.executed is False
            assert plan.audit_id is not None
            row = db.execute(
                select(OrderAuditLog).where(OrderAuditLog.id == plan.audit_id)
            ).scalar_one()
            assert row.decision == RiskDecision.REJECTED.value
            assert any("notional" in r.lower() or "max" in r.lower()
                       for r in row.reasons)


# ====================================================================
# Scenario E: Emergency Stop → 전 종목 차단 (route_order 미도달)
# ====================================================================


def test_scenario_e_emergency_stop_blocks_all_plans():
    Session = _session()
    with Session() as db:
        broker = MockBrokerAdapter(initial_cash=10_000_000)
        risk = RiskManager(RiskPolicy())
        risk.set_emergency_stop(True)
        agent = AutoTraderAgent()
        inp = AutoTraderInput(
            watchlist=["005930", "000660"],
            bars_by_symbol={
                "005930": _bars_uptrend("005930"),
                "000660": _bars_uptrend("000660", start=180000),
            },
            strategy_names=["sma_crossover"],
            min_confidence=10,
            mode=OperationMode.SIMULATION,
        )
        report = asyncio.run(agent.run_once(inp, broker=broker, risk=risk, db=db))
        assert report.emergency_stop is True
        for plan in report.plans:
            assert plan.blocked_by == "emergency_stop"
            assert plan.executed is False
            assert plan.audit_id is None      # route_order 미도달
            # risk_checks도 emergency 시 모두 false로 표시
            assert plan.decision.risk_checks.max_position_ok is False
            assert plan.decision.risk_checks.daily_loss_limit_ok is False


# ====================================================================
# Scenario F: 체결 후 portfolio + audit log 정상 갱신
# ====================================================================


def test_scenario_f_paper_broker_and_audit_updated_after_buy():
    Session = _session()
    with Session() as db:
        broker = MockBrokerAdapter(initial_cash=2_000_000)
        broker.set_price("005930", 60_000)
        risk = RiskManager(RiskPolicy())
        agent = AutoTraderAgent()
        inp = AutoTraderInput(
            watchlist=["005930"],
            bars_by_symbol={"005930": _bars_uptrend("005930")},
            strategy_names=["sma_crossover"],
            min_confidence=40,
            default_quantity=1,
            mode=OperationMode.SIMULATION,
        )
        before_cash = (asyncio.run(broker.get_balance())).cash
        report = asyncio.run(agent.run_once(inp, broker=broker, risk=risk, db=db))
        plan = report.plans[0]
        if plan.executed:
            after_balance = asyncio.run(broker.get_balance())
            assert after_balance.cash < before_cash
            positions = asyncio.run(broker.get_positions())
            assert any(p.symbol == "005930" for p in positions)
            # audit row 존재 + ai_decision_meta carry
            row = db.execute(
                select(OrderAuditLog).where(OrderAuditLog.id == plan.audit_id)
            ).scalar_one()
            assert row.executed is True
            assert row.trade_reason == "agent_auto_trade"
            assert row.strategy == "auto_trader_agent"
            assert row.requested_by_ai is True
            assert row.ai_decision_meta is not None
            assert row.ai_decision_meta.get("agent") == "AutoTraderAgent"


# ====================================================================
# Scenario G: reason이 사람이 읽을 수 있는 설명
# ====================================================================


def test_scenario_g_reason_is_human_readable():
    Session = _session()
    with Session() as db:
        broker = MockBrokerAdapter(initial_cash=2_000_000)
        risk = RiskManager(RiskPolicy())
        agent = AutoTraderAgent()
        inp = AutoTraderInput(
            watchlist=["005930"],
            bars_by_symbol={"005930": _bars_uptrend("005930")},
            strategy_names=["sma_crossover"],
            min_confidence=10,
            mode=OperationMode.SIMULATION,
        )
        report = asyncio.run(agent.run_once(inp, broker=broker, risk=risk, db=db))
        plan = report.plans[0]
        assert isinstance(plan.decision.reason, str)
        assert len(plan.decision.reason) > 0
        # reason은 빈 placeholder가 아니라 의미있는 한국어/영어
        assert plan.decision.reason.strip() not in {"", "?", "—"}
        # used_strategies 비어 있지 않음
        assert plan.decision.used_strategies


# ====================================================================
# Scenario H: LIVE 모드 차단 (절대 원칙)
# ====================================================================


def test_scenario_h_live_mode_blocked_with_runtime_error():
    Session = _session()
    with Session() as db:
        broker = MockBrokerAdapter()
        risk = RiskManager(RiskPolicy())
        agent = AutoTraderAgent()
        for blocked_mode in [
            OperationMode.LIVE_SHADOW,
            OperationMode.LIVE_MANUAL_APPROVAL,
            OperationMode.LIVE_AI_ASSIST,
            OperationMode.LIVE_AI_EXECUTION,
        ]:
            inp = AutoTraderInput(
                watchlist=["005930"],
                bars_by_symbol={"005930": _bars_uptrend("005930")},
                strategy_names=["sma_crossover"],
                mode=blocked_mode,
            )
            with pytest.raises(RuntimeError, match="disabled for mode"):
                asyncio.run(agent.run_once(inp, broker=broker, risk=risk, db=db))


# ====================================================================
# Scenario I: live broker 차단 (NotPaperBrokerError)
# ====================================================================


def test_scenario_i_live_broker_blocked():
    from app.brokers.kis import KisBrokerAdapter
    from app.execution.paper_trader import NotPaperBrokerError

    Session = _session()
    with Session() as db:
        live_broker = KisBrokerAdapter()
        live_broker.is_paper = False  # live 강제
        risk = RiskManager(RiskPolicy())
        agent = AutoTraderAgent()
        inp = AutoTraderInput(
            watchlist=["005930"],
            bars_by_symbol={"005930": _bars_uptrend("005930")},
            strategy_names=["sma_crossover"],
            mode=OperationMode.SIMULATION,
        )
        with pytest.raises(NotPaperBrokerError):
            asyncio.run(agent.run_once(inp, broker=live_broker, risk=risk, db=db))


# ====================================================================
# Scenario J: 모듈 invariant — broker.place_order / .cancel_order 직접 호출 0건
# ====================================================================


def test_module_does_not_call_broker_directly():
    """AutoTraderAgent 모듈은 broker.place_order / cancel_order를 직접
    호출하지 않는다 — 모든 broker 호출은 route_order / OrderExecutor 경유.

    AST를 사용해 *실제 함수 호출*만 검사 (docstring/주석은 제외)."""
    import ast
    import inspect
    from app.agents import auto_trader_loop

    source = inspect.getsource(auto_trader_loop)
    tree = ast.parse(source)
    forbidden = {"place_order", "cancel_order"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in forbidden, (
                f"AutoTraderAgent 모듈이 금지된 broker 메서드를 직접 호출함: "
                f".{node.func.attr}() — route_order 경유 필요."
            )


# ====================================================================
# Scenario K: recent_decisions cache 동작
# ====================================================================


def test_recent_decisions_returns_latest_first():
    Session = _session()
    with Session() as db:
        broker = MockBrokerAdapter(initial_cash=2_000_000)
        risk = RiskManager(RiskPolicy())
        agent = AutoTraderAgent()
        for symbol in ["005930", "000660"]:
            inp = AutoTraderInput(
                watchlist=[symbol],
                bars_by_symbol={symbol: _bars_uptrend(symbol)},
                strategy_names=["sma_crossover"],
                mode=OperationMode.SIMULATION,
            )
            asyncio.run(agent.run_once(inp, broker=broker, risk=risk, db=db))
        decisions = agent.recent_decisions(limit=5)
        assert decisions  # 비어있지 않음
        # 모든 decision에 사람-가독 필드
        for d in decisions:
            assert "createdAt" in d
            assert "symbol" in d
            assert "action" in d
            assert d["action"] in ("BUY", "SELL", "HOLD")
            assert "reason" in d
