"""3-02: 보유 종목 기준 SELL 판단 강화 테스트.

held_position(PositionContext) 기준 SELL — stop_loss/take_profit/VWAP/Momentum/
장마감 청산, 보유 없으면 SELL 금지, SELL 수량 ≤ 보유, 숏 진입 아님,
KisPaperAutoDecision/decision_log_meta 연결.
"""

from __future__ import annotations

import pytest

from app.agents.agent_council import (
    CouncilAction,
    StrategyMarketInput,
    run_agent_council,
)
from app.agents.position_context import (
    PositionContext,
    cap_sell_quantity,
    infer_position_sell_reason,
)


def _inp(**kw):
    base = dict(
        symbol="005930", current_price=73500, prev_close=75000, open_price=75000,
        vwap=74000, opening_range_high=75500, opening_range_low=74500,
        recent_closes=(75000, 74500, 74000, 73800, 73500),
        current_volume=120.0, avg_volume=100.0,
        market_regime="SIDEWAYS", regime_decision="ALLOW",
    )
    base.update(kw)
    return StrategyMarketInput(**base)


def _held(**kw):
    base = dict(held_position=True, symbol="005930", quantity=13, available_quantity=13,
                average_entry_price=75000, current_price=73500,
                stop_loss=73500, take_profit=77250)
    base.update(kw)
    return PositionContext(**base)


# ── PositionContext / trigger ──

def test_position_context_invariants():
    p = _held()
    assert p.is_short_entry is False
    assert p.short_position is False
    assert p.is_sellable is True
    assert p.sellable_quantity == 13
    with pytest.raises(ValueError):
        PositionContext(held_position=True, is_short_entry=True)


def test_infer_stop_loss():
    assert infer_position_sell_reason(_held(current_price=73500, stop_loss=73500)) == "STOP_LOSS"
    assert infer_position_sell_reason(_held(current_price=73400, stop_loss=73500)) == "STOP_LOSS"


def test_infer_take_profit():
    assert infer_position_sell_reason(
        _held(current_price=77300, stop_loss=73500, take_profit=77250)) == "TAKE_PROFIT"


def test_infer_market_close():
    p = _held(current_price=74500, stop_loss=73500, take_profit=77250,
              market_time_phase="CLOSING", market_close_exit_enabled=True)
    assert infer_position_sell_reason(p) == "MARKET_CLOSE_EXIT"


def test_infer_none_when_not_held():
    assert infer_position_sell_reason(PositionContext(held_position=False)) is None


def test_infer_none_when_no_trigger():
    p = _held(current_price=74500, stop_loss=73500, take_profit=77250)  # 사이.
    assert infer_position_sell_reason(p) is None


# ── council SELL (held) ──

def test_held_stop_loss_triggers_sell():
    d = run_agent_council(_inp(current_price=73500), risk_profile="BALANCED",
                          position=_held(current_price=73500, stop_loss=73500))
    assert d.final_action == CouncilAction.SELL
    assert d.sell_reason["reason_code"] == "STOP_LOSS"
    assert d.held_position is True
    assert d.position_quantity == 13


def test_held_take_profit_triggers_sell():
    d = run_agent_council(_inp(current_price=77300), risk_profile="BALANCED",
                          position=_held(current_price=77300))
    assert d.final_action == CouncilAction.SELL
    assert d.sell_reason["reason_code"] == "TAKE_PROFIT"


def test_held_market_close_triggers_sell():
    pos = _held(current_price=74500, market_time_phase="CLOSING",
                market_close_exit_enabled=True)
    d = run_agent_council(_inp(current_price=74500), risk_profile="BALANCED", position=pos)
    assert d.final_action == CouncilAction.SELL
    assert d.sell_reason["reason_code"] == "MARKET_CLOSE_EXIT"


def test_held_vwap_breakdown_vote_sell():
    # VWAP 하향 이탈 vote (현재가 < vwap) + 보유 → SELL.
    inp = _inp(current_price=72000, vwap=74000, recent_closes=(75000, 74000, 73000, 72500, 72000),
               market_regime="TREND_DOWN")
    pos = _held(current_price=72000, stop_loss=70000, take_profit=80000)  # 가격트리거 X.
    d = run_agent_council(inp, risk_profile="AGGRESSIVE", position=pos)
    assert d.final_action == CouncilAction.SELL
    assert d.sell_reason["reason_code"] in ("VWAP_BREAKDOWN", "MOMENTUM_WEAKENING",
                                            "STRATEGY_REVERSAL", "AI_AGENT_EXIT")


# ── 보유 없으면 SELL 금지 ──

def test_not_held_blocks_sell():
    inp = _inp(current_price=71000, recent_closes=(75000, 74000, 73000, 72000, 71000),
               vwap=74000, market_regime="TREND_DOWN")
    d = run_agent_council(inp, risk_profile="AGGRESSIVE",
                          position=PositionContext(held_position=False))
    assert d.final_action == CouncilAction.HOLD
    assert "NO_HELD_POSITION" in d.reason


def test_zero_quantity_blocks_sell():
    inp = _inp(current_price=71000, recent_closes=(75000, 74000, 73000, 72000, 71000),
               vwap=74000, market_regime="TREND_DOWN")
    pos = PositionContext(held_position=True, quantity=0, available_quantity=0)
    d = run_agent_council(inp, risk_profile="AGGRESSIVE", position=pos)
    assert d.final_action == CouncilAction.HOLD


# ── SELL 수량 / 숏 아님 ──

def test_sell_quantity_capped_to_held():
    d = run_agent_council(_inp(current_price=73500), risk_profile="BALANCED",
                          position=_held(quantity=13, available_quantity=13))
    kd = d.to_kis_paper_decision(quantity=100, price=73500)   # 요청 100 > 보유 13.
    assert kd.side == "SELL"
    assert kd.quantity == 13                                  # 보유 이하로 제한.
    assert kd.held_position is True
    assert kd.is_short_entry is False
    assert kd.short_position is False
    assert kd.sell_reason_code == "STOP_LOSS"


def test_cap_sell_quantity_helper():
    assert cap_sell_quantity(100, _held(quantity=13, available_quantity=13)) == 13
    assert cap_sell_quantity(5, _held(quantity=13, available_quantity=13)) == 5
    assert cap_sell_quantity(100, None) == 100


def test_available_quantity_limits_sell():
    pos = _held(quantity=20, available_quantity=8)
    d = run_agent_council(_inp(current_price=73500), risk_profile="BALANCED", position=pos)
    kd = d.to_kis_paper_decision(quantity=20, price=73500)
    assert kd.quantity == 8                                   # available 8 이하.


def test_kis_decision_is_not_short_entry():
    d = run_agent_council(_inp(current_price=73500), risk_profile="BALANCED", position=_held())
    kd = d.to_kis_paper_decision(quantity=13, price=73500)
    assert kd.is_short_entry is False
    assert kd.short_position is False
    with pytest.raises(ValueError):
        from app.kis_paper.auto_executor import KisPaperAutoDecision
        KisPaperAutoDecision(symbol="X", side="SELL", quantity=1, price=1, is_short_entry=True)


# ── HOLD 시 exit_plan 필수 정책 SELL 미적용 ──

def test_sell_does_not_require_buy_exit_plan():
    # SELL 은 BUY 용 exit_plan 필수 정책을 적용받지 않는다 — sell_reason_code 만 필수.
    d = run_agent_council(_inp(current_price=73500), risk_profile="CONSERVATIVE",
                          position=_held(current_price=73500, stop_loss=73500))
    assert d.final_action == CouncilAction.SELL
    assert d.sell_reason["reason_code"]
    # SELL 은 has_exit_plan(BUY 진입계획) False.
    assert d.has_exit_plan is False


# ── episode / AgentDecisionLog 연결 ──

def test_council_to_dict_carries_position_context():
    d = run_agent_council(_inp(current_price=73500), risk_profile="BALANCED", position=_held())
    cd = d.to_dict()
    assert cd["held_position"] is True
    assert cd["position_quantity"] == 13
    assert cd["is_short_entry"] is False
    assert cd["short_position"] is False
    assert cd["sell_reason"]["reason_code"] == "STOP_LOSS"


def test_decision_log_meta_carries_held_position():
    from app.agents.decision_log_meta import build_agent_decision_log_meta
    d = run_agent_council(_inp(current_price=73500), risk_profile="BALANCED", position=_held())
    kd = d.to_kis_paper_decision(quantity=13, price=73500)
    m = build_agent_decision_log_meta(decision=kd, reason_code="KIS_PAPER_SUBMITTED")
    assert m["held_position"] is True
    assert m["position_quantity"] == 13
    assert m["is_short_entry"] is False
    assert m["sell_reason_code"] == "STOP_LOSS"
    assert m["final_action"] == "SELL"


# ── 안전 invariant ──

def test_invariants():
    d = run_agent_council(_inp(current_price=73500), risk_profile="BALANCED", position=_held())
    assert d.is_live_authorization is False
    assert d.is_order_signal is False
    assert d.is_short_entry is False


# ── 하위호환: position 없이 held_position bool ──

def test_backwards_compat_bool_held_position():
    # position 미지정 시 기존 bool held_position 동작 유지.
    inp = _inp(current_price=72000, recent_closes=(75000, 74000, 73000, 72500, 72000),
               vwap=74000, market_regime="TREND_DOWN")
    no_hold = run_agent_council(inp, held_position=False, risk_profile="AGGRESSIVE")
    held = run_agent_council(inp, held_position=True, risk_profile="AGGRESSIVE")
    assert no_hold.final_action == CouncilAction.HOLD
    assert held.final_action in (CouncilAction.SELL, CouncilAction.HOLD)
