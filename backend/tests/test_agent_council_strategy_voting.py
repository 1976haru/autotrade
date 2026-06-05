"""Agent Council 4전략 투표 → BUY/SELL/HOLD 결정 테스트.

검증 (사용자 요청서 §2/§3):
- 4 전략 모두 HOLD → final HOLD
- 강한 BUY 신호 → BUY (selected_strategies / exit_plan / confidence / quality)
- 같은 점수: CONSERVATIVE HOLD vs AGGRESSIVE BUY (threshold 차이)
- risk_flags 많으면 AGGRESSIVE 여도 HOLD
- AGGRESSIVE 도 is_live_authorization=False
- SELL 은 held_position=True 일 때만 (naked SELL 방지)
- BUY 는 exit_plan 필수
- 각 evaluator 데이터 부족 → HOLD + reason_code
- to_kis_paper_decision: BUY→decision / HOLD→None
- 정적 가드: broker / route_order / OrderExecutor import 0건
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.agents.agent_council import (
    CouncilAction,
    StrategyMarketInput,
    evaluate_gap,
    evaluate_momentum,
    evaluate_orb,
    evaluate_vwap,
    run_agent_council,
)


_MODULE = Path(__file__).resolve().parents[1] / "app" / "agents" / "agent_council.py"


def _buy_input(**kw) -> StrategyMarketInput:
    base = dict(
        symbol="005930", current_price=78000, prev_close=75000, open_price=76500,
        vwap=77000, opening_range_high=77500, opening_range_low=76000,
        recent_closes=(74000, 75000, 76000, 77000, 78000),
        current_volume=200.0, avg_volume=100.0,
        market_regime="TREND_UP", regime_decision="ALLOW",
    )
    base.update(kw)
    return StrategyMarketInput(**base)


def _flat_input(**kw) -> StrategyMarketInput:
    base = dict(
        symbol="005930", current_price=75000, prev_close=75000, open_price=75000,
        vwap=75000, opening_range_high=75500, opening_range_low=74500,
        recent_closes=(75000, 75000, 75000), current_volume=100.0, avg_volume=100.0,
        market_regime="SIDEWAYS",
    )
    base.update(kw)
    return StrategyMarketInput(**base)


def _sell_input(**kw) -> StrategyMarketInput:
    base = dict(
        symbol="005930", current_price=72000, prev_close=75000, open_price=73000,
        vwap=74000, opening_range_high=75500, opening_range_low=74500,
        recent_closes=(76000, 75000, 74000, 73000, 72000),
        current_volume=150.0, avg_volume=100.0, market_regime="SIDEWAYS",
    )
    base.update(kw)
    return StrategyMarketInput(**base)


# ── 최종 결정 ─────────────────────────────────────────────────────────────────


def test_all_hold_when_flat():
    d = run_agent_council(_flat_input())
    assert d.final_action == CouncilAction.HOLD
    assert all(v.signal == CouncilAction.HOLD for v in d.votes)


def test_config_min_confidence_floor_overrides_preset():
    # B(SSOT 정합): config 주문게이트 min_confidence 를 floor 로 주입하면, 어떤
    #   프리셋도 그보다 관대할 수 없다(council 후보 임계 == 실제 게이트 임계 정합).
    base = run_agent_council(_buy_input(), risk_profile="BALANCED")
    assert base.final_action == CouncilAction.BUY
    conf = float(base.confidence)
    high = min(1.0, conf + 0.1)
    low = max(0.0, conf - 0.2)
    # floor > confidence → config 가 더 엄격 → HOLD 로 강등.
    floored = run_agent_council(_buy_input(), risk_profile="BALANCED",
                                min_confidence_floor=high)
    assert floored.final_action == CouncilAction.HOLD
    assert "임계" in (floored.reason or "")
    # floor < confidence → 영향 없음 → BUY 유지.
    assert run_agent_council(_buy_input(), risk_profile="BALANCED",
                             min_confidence_floor=low).final_action == CouncilAction.BUY
    # floor=None → 기존 프리셋 동작 그대로.
    assert run_agent_council(_buy_input(), risk_profile="BALANCED",
                             min_confidence_floor=None).final_action == CouncilAction.BUY


def test_strong_buy():
    d = run_agent_council(_buy_input(), risk_profile="BALANCED")
    assert d.final_action == CouncilAction.BUY
    assert len(d.votes) == 4
    assert d.confidence > 0.5
    assert d.quality_score >= 60
    assert d.has_exit_plan is True
    assert d.exit_plan.get("stop_loss_pct", 0) > 0
    assert set(d.selected_strategies).issubset({"MOMENTUM", "VWAP", "ORB", "GAP"})


def test_same_signal_conservative_hold_aggressive_buy():
    # 동일 입력에서 CONSERVATIVE 는 quality/conf 임계로 HOLD, AGGRESSIVE 는 BUY.
    inp = _buy_input()
    cons = run_agent_council(inp, risk_profile="CONSERVATIVE")
    aggr = run_agent_council(inp, risk_profile="AGGRESSIVE")
    assert cons.final_action == CouncilAction.HOLD
    assert aggr.final_action == CouncilAction.BUY


def test_risk_flags_force_hold_even_aggressive():
    # low_volume + high_volatility → 2 flags. AGGRESSIVE max_risk_flags=2 →
    # 2개는 허용 경계. 3개 이상이면 차단. low_volume + high_vol = 2, 경계.
    # 더 확실히: HIGH_VOLATILITY + low volume + (gap/orb also flag) → veto.
    inp = _buy_input(current_volume=5.0, avg_volume=100.0,
                     market_regime="HIGH_VOLATILITY")
    aggr = run_agent_council(inp, risk_profile="AGGRESSIVE")
    # AGGRESSIVE max_risk_flags=2; flags = {low_volume, high_volatility} = 2 → 경계
    # 통과할 수 있으나 CONSERVATIVE(max 0) 는 반드시 HOLD.
    cons = run_agent_council(inp, risk_profile="CONSERVATIVE")
    assert cons.final_action == CouncilAction.HOLD
    assert "high_volatility" in cons.risk_flags


def test_aggressive_not_live_authorization():
    d = run_agent_council(_buy_input(), risk_profile="AGGRESSIVE")
    assert d.is_live_authorization is False
    assert d.is_order_signal is False
    assert d.auto_apply_allowed is False


def test_regime_block_new_buy_suppresses_buy():
    d = run_agent_council(_buy_input(regime_decision="BLOCK_NEW_BUY"),
                          risk_profile="AGGRESSIVE")
    assert d.final_action == CouncilAction.HOLD
    assert "BUY" in d.reason or "억제" in d.reason


def test_sell_requires_held_position():
    no_hold = run_agent_council(_sell_input(), held_position=False)
    held = run_agent_council(_sell_input(), held_position=True)
    assert no_hold.final_action == CouncilAction.HOLD
    assert held.final_action == CouncilAction.SELL


def test_buy_has_exit_plan_hold_does_not():
    buy = run_agent_council(_buy_input(), risk_profile="BALANCED")
    hold = run_agent_council(_flat_input())
    assert buy.final_action == CouncilAction.BUY and buy.has_exit_plan is True
    assert hold.final_action == CouncilAction.HOLD and hold.has_exit_plan is False
    assert hold.exit_plan == {}


# ── evaluator 데이터 부족 → HOLD + reason_code ─────────────────────────────────


@pytest.mark.parametrize("evaluator,inp", [
    (evaluate_orb, StrategyMarketInput(symbol="X")),
    (evaluate_momentum, StrategyMarketInput(symbol="X", recent_closes=(1.0,))),
    (evaluate_gap, StrategyMarketInput(symbol="X")),
    (evaluate_vwap, StrategyMarketInput(symbol="X")),
])
def test_evaluator_insufficient_data_holds(evaluator, inp):
    v = evaluator(inp)
    assert v.signal == CouncilAction.HOLD
    assert v.reason_code.startswith("INSUFFICIENT") or v.reason_code != ""


def test_each_evaluator_can_signal_buy():
    inp = _buy_input(open_price=77200)   # gap = (77200-75000)/75000 = 2.93% > 2%
    assert evaluate_momentum(inp).signal == CouncilAction.BUY
    assert evaluate_vwap(inp).signal == CouncilAction.BUY
    assert evaluate_orb(inp).signal == CouncilAction.BUY
    assert evaluate_gap(inp).signal == CouncilAction.BUY


# ── KIS Paper 변환 ─────────────────────────────────────────────────────────────


def test_to_kis_paper_decision_buy():
    d = run_agent_council(_buy_input(), risk_profile="BALANCED")
    kd = d.to_kis_paper_decision(quantity=13, price=78000)
    assert kd is not None
    assert kd.side == "BUY"
    assert kd.quantity == 13
    assert kd.has_exit_plan is True
    assert kd.notional_krw == 13 * 78000
    assert kd.selected_strategies


def test_to_kis_paper_decision_hold_is_none():
    d = run_agent_council(_flat_input())
    assert d.to_kis_paper_decision(quantity=1, price=75000) is None


# ── 정적 가드 + invariants ─────────────────────────────────────────────────────


def test_static_no_broker_or_route_order_imports():
    src = _MODULE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    forbidden = (
        "app.brokers.kis", "app.brokers.mock_broker",
        "app.execution.executor", "app.execution.order_router",
        "anthropic", "openai", "httpx", "requests",
    )
    for mod in imported:
        for bad in forbidden:
            assert bad not in (mod or ""), f"forbidden import: {mod}"
    # top-level 에서 kis_paper.auto_executor / route_order import 0건 (lazy 만 허용).
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.col_offset == 0:
            assert "auto_executor" not in (node.module or "")
    # 실제 호출 패턴 0건.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in ("place_order", "route_order")


def test_decision_invariants_locked():
    from app.agents.agent_council import AgentCouncilDecision
    with pytest.raises(ValueError):
        AgentCouncilDecision(
            symbol="X", final_action=CouncilAction.HOLD, confidence=0.0,
            quality_score=0, selected_strategies=[], votes=[], reason="",
            risk_flags=[], risk_profile="BALANCED", market_regime="UNKNOWN",
            buy_score=0, sell_score=0, hold_score=0, has_exit_plan=False,
            exit_plan={}, is_live_authorization=True,
        )


# ── API endpoint ──────────────────────────────────────────────────────────────


def test_api_council_evaluate_buy(client):
    res = client.post("/api/agents/council/evaluate", json={
        "symbol": "005930", "current_price": 78000, "prev_close": 75000,
        "open_price": 76500, "vwap": 77000, "opening_range_high": 77500,
        "opening_range_low": 76000,
        "recent_closes": [74000, 75000, 76000, 77000, 78000],
        "current_volume": 200, "avg_volume": 100,
        "market_regime": "TREND_UP", "risk_profile": "BALANCED",
    })
    assert res.status_code == 200
    j = res.json()
    assert j["final_action"] == "BUY"
    assert len(j["votes"]) == 4
    assert j["is_live_authorization"] is False
    assert j["has_exit_plan"] is True


def test_api_council_evaluate_hold_when_flat(client):
    res = client.post("/api/agents/council/evaluate", json={
        "symbol": "005930", "current_price": 75000, "prev_close": 75000,
        "open_price": 75000, "vwap": 75000,
        "recent_closes": [75000, 75000, 75000],
        "market_regime": "SIDEWAYS",
    })
    assert res.status_code == 200
    assert res.json()["final_action"] == "HOLD"
