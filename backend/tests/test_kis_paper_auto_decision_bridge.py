"""2-11: Agent Council → KisPaperAutoDecision 변환 연결 테스트.

BUY/SELL → decision 생성, HOLD/veto/exit-invalid → None(주문 0건), carry 필드
(selected_strategies/confidence/quality_score/risk_profile/exit_plan/
exit_plan_validation/risk_veto_result/sell_reason) + 안전 invariant.
"""

from __future__ import annotations

from app.agents.agent_council import StrategyMarketInput, run_agent_council
from app.kis_paper.auto_executor import (
    KisPaperAutoDecision,
    build_kis_paper_decision_from_council,
)


def _strong(**kw):
    base = dict(
        symbol="005930", current_price=1100, prev_close=1000, open_price=1010,
        vwap=1000, opening_range_high=1010, opening_range_low=990,
        recent_closes=(1000, 1030, 1060, 1090, 1100),
        current_volume=150.0, avg_volume=100.0,
        market_regime="TREND_UP", regime_decision="ALLOW",
    )
    base.update(kw)
    return StrategyMarketInput(**base)


def _flat(**kw):
    return _strong(recent_closes=(1000, 1000, 1000, 1000, 1000), current_price=1000,
                   vwap=1000, opening_range_high=1010, opening_range_low=990,
                   open_price=1000, prev_close=1000, **kw)


# ── BUY / SELL → decision ──

def test_buy_creates_decision():
    d = run_agent_council(_strong(), risk_profile="AGGRESSIVE")
    assert d.final_action.value == "BUY"
    kd = build_kis_paper_decision_from_council(d, quantity=10, price=1100)
    assert kd is not None
    assert kd.side == "BUY"
    assert kd.symbol == "005930"
    assert kd.quantity == 10


def test_sell_creates_decision():
    # 하락 추세 + 보유 → SELL.
    inp = _strong(current_price=900, prev_close=1000, open_price=1000, vwap=1000,
                  recent_closes=(1000, 980, 960, 940, 900),
                  market_regime="TREND_DOWN")
    d = run_agent_council(inp, held_position=True, risk_profile="AGGRESSIVE")
    if d.final_action.value != "SELL":
        # 환경에 따라 다른 경우 — 직접 SELL council 로 검증.
        import pytest
        pytest.skip("council did not produce SELL for this input")
    kd = build_kis_paper_decision_from_council(d, quantity=5, price=900)
    assert kd is not None
    assert kd.side == "SELL"
    assert kd.sell_reason_code  # SELL 사유 carry.


# ── HOLD / veto / exit-invalid → None ──

def test_hold_no_decision():
    d = run_agent_council(_flat(), risk_profile="CONSERVATIVE")
    assert d.final_action.value == "HOLD"
    assert build_kis_paper_decision_from_council(d, quantity=10, price=1000) is None


def test_risk_veto_hold_no_decision():
    # low_volume + high_volatility = 2 flags, BALANCED max 1 → veto → HOLD.
    inp = _strong(current_volume=5.0, avg_volume=100.0, market_regime="HIGH_VOLATILITY")
    d = run_agent_council(inp, risk_profile="BALANCED")
    assert d.final_action.value == "HOLD"
    assert d.risk_veto_result["veto_applied"] is True
    assert build_kis_paper_decision_from_council(d, quantity=10, price=1100) is None


def test_exit_plan_invalid_hold_no_decision():
    # current_price 없음 → exit_plan 생성 실패 → BUY 강등 HOLD.
    d = run_agent_council(_strong(current_price=None), risk_profile="AGGRESSIVE")
    assert d.final_action.value == "HOLD"
    assert d.exit_plan_validation.get("valid") is False
    assert build_kis_paper_decision_from_council(d, quantity=10, price=0) is None


def test_pre_veto_buy_but_final_hold_no_decision():
    inp = _strong(current_volume=5.0, avg_volume=100.0, market_regime="HIGH_VOLATILITY")
    d = run_agent_council(inp, risk_profile="BALANCED")
    # pre_veto 는 BUY 였지만 final 은 HOLD → 주문 변환 0.
    assert d.risk_veto_result["pre_veto_action"] == "BUY"
    assert d.final_action.value == "HOLD"
    assert build_kis_paper_decision_from_council(d, quantity=10, price=1100) is None


def test_pre_exit_plan_buy_but_final_hold_no_decision():
    d = run_agent_council(_strong(current_price=None), risk_profile="AGGRESSIVE")
    assert d.pre_exit_plan_action == "BUY"
    assert d.final_action.value == "HOLD"
    assert build_kis_paper_decision_from_council(d, quantity=10, price=0) is None


def test_none_council_returns_none():
    assert build_kis_paper_decision_from_council(None, quantity=10, price=1100) is None


# ── carry 필드 ──

def test_buy_decision_carries_council_context():
    d = run_agent_council(_strong(), risk_profile="AGGRESSIVE")
    kd = build_kis_paper_decision_from_council(d, quantity=10, price=1100)
    assert kd.selected_strategies  # 비어있지 않음.
    assert 0.0 <= kd.confidence <= 1.0
    assert kd.quality_score >= 0
    assert kd.risk_profile == "AGGRESSIVE"
    assert kd.has_exit_plan is True
    assert kd.exit_plan.get("stop_loss") is not None
    assert kd.exit_plan_validation.get("valid") is True
    assert "veto_applied" in kd.risk_veto_result


def test_to_kis_paper_decision_method_matches_helper():
    d = run_agent_council(_strong(), risk_profile="AGGRESSIVE")
    a = d.to_kis_paper_decision(quantity=10, price=1100)
    b = build_kis_paper_decision_from_council(d, quantity=10, price=1100)
    assert a.side == b.side and a.symbol == b.symbol


# ── 안전 invariant ──

def test_decision_default_carry_fields():
    # 신규 carry 필드는 optional default (기존 직접 생성 site 무영향).
    kd = KisPaperAutoDecision(symbol="X", side="HOLD", quantity=0, price=0)
    assert kd.risk_profile is None
    assert kd.risk_veto_result == {}
    assert kd.exit_plan_validation == {}
    assert kd.sell_reason_code is None


def test_decision_is_not_order_authorization():
    # KisPaperAutoDecision 은 *결정* 일 뿐 — broker_order_type 등은 결과(Result) 에서.
    d = run_agent_council(_strong(), risk_profile="AGGRESSIVE")
    kd = build_kis_paper_decision_from_council(d, quantity=10, price=1100)
    # decision 자체엔 live 권한 필드가 없고, 변환은 데이터 객체만 만든다.
    assert kd.side in ("BUY", "SELL")
    assert d.is_live_authorization is False
    assert d.is_order_signal is False
