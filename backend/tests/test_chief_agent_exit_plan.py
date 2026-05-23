"""2-09: ChiefTradingAgent BUY 판단 exit_plan 필수화 테스트.

exit_plan 생성/검증 매트릭스(missing/stop/take/strategy/rr) + BUY 시 필수화 +
검증 실패 → HOLD 강등 + pre_exit_plan_action + exit_plan_validation 저장 +
council/episode 연결.
"""

from __future__ import annotations

import pytest

from app.agents.exit_plan import (
    EXIT_PLAN_MISSING,
    EXIT_PLAN_OK,
    EXIT_STRATEGY_MISSING,
    RISK_REWARD_INVALID,
    STOP_LOSS_INVALID,
    STOP_LOSS_MISSING,
    TAKE_PROFIT_INVALID,
    TAKE_PROFIT_MISSING,
    ExitPlan,
    build_default_exit_plan,
    validate_exit_plan,
)

_CUR = 75_000


def _plan(**over):
    base = {"entry_price": _CUR, "stop_loss": 73_500, "take_profit": 77_250,
            "risk_reward_ratio": 1.5, "exit_strategy": "STOP_LOSS_TAKE_PROFIT"}
    base.update(over)
    return base


# ── build_default_exit_plan ──

def test_build_default_plan_absolute_prices():
    p = build_default_exit_plan(entry_price=_CUR, risk_profile="BALANCED")
    assert p is not None
    assert p.stop_loss < _CUR < p.take_profit
    assert p.risk_reward_ratio > 0
    assert p.exit_strategy
    assert p.created_by == "ChiefTradingAgent"


def test_build_uses_explicit_pct_over_profile():
    p = build_default_exit_plan(entry_price=_CUR, risk_profile="AGGRESSIVE",
                                stop_loss_pct=1.0, take_profit_pct=2.0)
    assert p.stop_loss == pytest.approx(_CUR * 0.99, rel=1e-6)
    assert p.take_profit == pytest.approx(_CUR * 1.02, rel=1e-6)


def test_build_invalid_entry_returns_none():
    assert build_default_exit_plan(entry_price=0, risk_profile="BALANCED") is None
    assert build_default_exit_plan(entry_price=None, risk_profile="BALANCED") is None


# ── validate_exit_plan 매트릭스 ──

def test_valid_plan_ok():
    r = validate_exit_plan(_plan(), current_price=_CUR)
    assert r.valid is True
    assert r.reason_code == EXIT_PLAN_OK
    assert r.forced_action is None


def test_missing_plan():
    r = validate_exit_plan(None, current_price=_CUR)
    assert r.valid is False
    assert r.reason_code == EXIT_PLAN_MISSING
    assert r.forced_action == "HOLD"


def test_missing_stop_loss():
    r = validate_exit_plan(_plan(stop_loss=None), current_price=_CUR)
    assert r.reason_code == STOP_LOSS_MISSING
    assert r.forced_action == "HOLD"


def test_missing_take_profit():
    r = validate_exit_plan(_plan(take_profit=None), current_price=_CUR)
    assert r.reason_code == TAKE_PROFIT_MISSING


def test_stop_loss_above_entry_invalid():
    r = validate_exit_plan(_plan(stop_loss=76_000), current_price=_CUR)
    assert r.reason_code == STOP_LOSS_INVALID
    assert r.forced_action == "HOLD"


def test_take_profit_below_entry_invalid():
    r = validate_exit_plan(_plan(take_profit=74_000), current_price=_CUR)
    assert r.reason_code == TAKE_PROFIT_INVALID


def test_missing_exit_strategy():
    r = validate_exit_plan(
        {"entry_price": _CUR, "stop_loss": 73_500, "take_profit": 77_250},
        current_price=_CUR)
    assert r.reason_code == EXIT_STRATEGY_MISSING


def test_risk_reward_invalid_via_excessive_stop():
    # 손절 폭이 과도(30%)하면 STOP_LOSS_INVALID.
    r = validate_exit_plan(_plan(stop_loss=_CUR * 0.7), current_price=_CUR)
    assert r.reason_code == STOP_LOSS_INVALID


def test_explicit_negative_rr_invalid():
    # 정상 가격이지만 rr 을 음수로 명시 → RISK_REWARD_INVALID.
    r = validate_exit_plan(_plan(risk_reward_ratio=-1.0), current_price=_CUR)
    assert r.reason_code == RISK_REWARD_INVALID


def test_sell_bypasses_exit_plan():
    assert validate_exit_plan(None, current_price=_CUR, side="SELL").valid is True


def test_exit_plan_invariants():
    p = build_default_exit_plan(entry_price=_CUR, risk_profile="BALANCED")
    assert p.is_order_signal is False
    assert p.is_live_authorization is False
    with pytest.raises(ValueError):
        ExitPlan(entry_price=1, stop_loss=1, take_profit=2, stop_loss_pct=1,
                 take_profit_pct=2, risk_reward_ratio=2, is_order_signal=True)


def test_deterministic():
    a = validate_exit_plan(_plan(), current_price=_CUR).to_dict()
    b = validate_exit_plan(_plan(), current_price=_CUR).to_dict()
    assert a == b


# ── Agent Council 통합 ──

def _strong(**kw):
    from app.agents.agent_council import StrategyMarketInput
    base = dict(
        symbol="005930", current_price=1100, prev_close=1000, open_price=1010,
        vwap=1000, opening_range_high=1010, opening_range_low=990,
        recent_closes=(1000, 1030, 1060, 1090, 1100),
        current_volume=150.0, avg_volume=100.0,
        market_regime="TREND_UP", regime_decision="ALLOW",
    )
    base.update(kw)
    return StrategyMarketInput(**base)


def test_council_buy_has_valid_exit_plan():
    from app.agents.agent_council import CouncilAction, run_agent_council
    d = run_agent_council(_strong(), risk_profile="AGGRESSIVE")
    assert d.final_action == CouncilAction.BUY
    assert d.has_exit_plan is True
    assert d.exit_plan_validation["valid"] is True
    ep = d.exit_plan
    assert ep["stop_loss"] < ep["entry_price"] < ep["take_profit"]
    assert ep["exit_strategy"]


def test_council_buy_without_price_downgraded_to_hold():
    from app.agents.agent_council import CouncilAction, run_agent_council
    d = run_agent_council(_strong(current_price=None), risk_profile="AGGRESSIVE")
    assert d.final_action == CouncilAction.HOLD
    assert d.pre_exit_plan_action == "BUY"
    assert d.exit_plan_validation["valid"] is False
    assert d.exit_plan_validation["reason_code"] == EXIT_PLAN_MISSING


def test_council_hold_no_exit_plan_required():
    from app.agents.agent_council import CouncilAction, run_agent_council
    # 약한 신호 → HOLD (exit_plan 불필요).
    d = run_agent_council(
        _strong(recent_closes=(1000, 1000, 1000, 1000, 1000), current_price=1000,
                opening_range_high=1010, opening_range_low=990, vwap=1000),
        risk_profile="CONSERVATIVE")
    assert d.final_action == CouncilAction.HOLD
    assert d.exit_plan == {}


def test_council_to_dict_carries_exit_plan_fields():
    from app.agents.agent_council import run_agent_council
    d = run_agent_council(_strong()).to_dict()
    assert "exit_plan_validation" in d
    assert "pre_exit_plan_action" in d
    assert d["exit_plan_required"] is True


def test_council_buy_exit_plan_invariants():
    from app.agents.agent_council import run_agent_council
    d = run_agent_council(_strong(), risk_profile="AGGRESSIVE")
    assert d.is_live_authorization is False
    assert d.is_order_signal is False
    assert d.auto_apply_allowed is False
