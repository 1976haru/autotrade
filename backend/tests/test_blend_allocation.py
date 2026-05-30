"""blend_allocation (분산70+모멘텀30+위험장치) 주문 리스트 *계산* 모듈 테스트.

핵심 검증: 비중 합 ≤ 1, 음수 비중 0, 안전 invariant False, broker import 0건,
lookahead-free, 하락장 방어 / 변동성 타게팅 동작.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.portfolio.blend_allocation import (
    AllocationConfig,
    SignalInputs,
    build_target_allocation,
    build_rebalance_plan,
    momentum_score,
    regime_is_bull,
    vol_target_scale,
    rank_momentum,
    plan_to_dict,
    RebalancePlan,
)

MODULE = Path(__file__).resolve().parents[1] / "app" / "portfolio" / "blend_allocation.py"


def _rising(n: int, start: float = 100.0, step: float = 0.5) -> list[float]:
    return [start + i * step for i in range(n)]


def _flat(n: int, v: float = 100.0) -> list[float]:
    return [v] * n


# ---------------- safety / static guards ----------------
def test_no_broker_or_execution_imports():
    src = MODULE.read_text(encoding="utf-8")
    # Strip docstrings/comments-free check is overkill; instead forbid actual
    # import statements and call sites (prose mentions in docstring are allowed).
    forbidden = [
        r"^\s*from app\.brokers", r"^\s*import.*kis_client", r"^\s*from app\.execution",
        r"^\s*from app\.execution\.order_executor import",
        r"\.place_order\(", r"\.cancel_order\(", r"route_order\(",
        r"^\s*import anthropic", r"^\s*import openai", r"^\s*import httpx", r"^\s*import requests",
    ]
    for pat in forbidden:
        assert not re.search(pat, src, re.MULTILINE), f"forbidden pattern present: {pat}"


def test_rebalance_plan_invariants_always_false():
    sig = SignalInputs(price_history={"A": _rising(300), "B": _rising(300, step=0.3)},
                       index_history=_rising(300))
    tgt = build_target_allocation(["A", "B"], sig)
    plan = build_rebalance_plan(as_of="2026-06-01", total_equity_krw=10_000_000,
                                current_holdings={}, prices={"A": 100, "B": 100}, target=tgt)
    assert plan.is_order_signal is False
    assert plan.is_live_authorization is False
    assert plan.broker_order_sent is False
    assert plan.order_created is False


def test_cannot_construct_plan_with_true_invariant():
    with pytest.raises(ValueError):
        RebalancePlan(as_of="x", total_equity_krw=1.0, target=build_target_allocation(
            ["A"], SignalInputs(price_history={"A": _rising(300)}, index_history=_rising(300))),
            orders=(), total_buy_krw=0, total_sell_krw=0, total_est_cost_krw=0,
            is_live_authorization=True)


# ---------------- config ----------------
def test_config_weights_must_sum_to_one():
    with pytest.raises(ValueError):
        AllocationConfig(diversified_weight=0.7, momentum_weight=0.4)
    AllocationConfig(diversified_weight=0.7, momentum_weight=0.3)  # ok


# ---------------- signals (lookahead-free) ----------------
def test_momentum_score_uses_history_only():
    closes = _rising(300, 100, 1.0)
    sc = momentum_score(closes, lookback=252, gap=21)
    assert sc is not None and sc > 0  # rising -> positive momentum


def test_momentum_score_insufficient_data():
    assert momentum_score(_rising(50), lookback=252, gap=21) is None


def test_regime_bull_bear():
    assert regime_is_bull(_rising(250), ma=200) is True
    assert regime_is_bull(list(reversed(_rising(250))), ma=200) is False
    assert regime_is_bull(_rising(50), ma=200) is None


def test_vol_target_scale_caps_at_one():
    # very low vol -> scale capped at 1.0
    assert vol_target_scale(_flat(120), AllocationConfig()) == 1.0
    # high vol -> scale < 1.0
    noisy = []
    v = 100.0
    for i in range(120):
        v *= 1.05 if i % 2 == 0 else 0.95
        noisy.append(v)
    s = vol_target_scale(noisy, AllocationConfig())
    assert 0.0 < s < 1.0


# ---------------- target allocation ----------------
def test_target_weights_sum_le_one_and_nonneg():
    syms = [f"S{i}" for i in range(20)]
    hist = {s: _rising(300, 100, 0.1 + 0.01 * i) for i, s in enumerate(syms)}
    sig = SignalInputs(price_history=hist, index_history=_rising(300))
    tgt = build_target_allocation(syms, sig)
    total = sum(tgt.weights.values()) + tgt.cash_weight
    assert total <= 1.0 + 1e-6
    assert all(w >= 0 for w in tgt.weights.values())


def test_bear_regime_turns_off_momentum():
    syms = [f"S{i}" for i in range(15)]
    hist = {s: _rising(300, 100, 0.1 + 0.02 * i) for i, s in enumerate(syms)}
    bear_index = list(reversed(_rising(300)))  # declining -> bear
    sig = SignalInputs(price_history=hist, index_history=bear_index)
    tgt = build_target_allocation(syms, sig, AllocationConfig(vol_targeting=False))
    # bear -> momentum off -> pure equalweight; each weight ~ 1/15
    assert tgt.regime_is_bull is False
    expected = 1.0 / len(syms)
    for w in tgt.weights.values():
        assert abs(w - expected) < 1e-6


def test_momentum_picks_get_higher_weight_in_bull():
    syms = [f"S{i}" for i in range(15)]
    # S14 strongest momentum, S0 weakest
    hist = {s: _rising(300, 100, 0.05 + 0.05 * i) for i, s in enumerate(syms)}
    sig = SignalInputs(price_history=hist, index_history=_rising(300, 100, 1.0))
    tgt = build_target_allocation(syms, sig, AllocationConfig(vol_targeting=False))
    assert tgt.regime_is_bull is True
    # a momentum pick should weigh more than a non-pick
    assert len(tgt.momentum_picks) > 0
    pick = tgt.momentum_picks[0]
    nonpick = next(s for s in syms if s not in tgt.momentum_picks)
    assert tgt.weights[pick] > tgt.weights[nonpick]


# ---------------- rebalance plan (order list) ----------------
def test_rebalance_from_all_cash_buys_only():
    syms = ["A", "B", "C"]
    hist = {s: _rising(300, 100, 0.2) for s in syms}
    sig = SignalInputs(price_history=hist, index_history=_rising(300))
    tgt = build_target_allocation(syms, sig, AllocationConfig(vol_targeting=False))
    plan = build_rebalance_plan(as_of="2026-06-01", total_equity_krw=10_000_000,
                                current_holdings={}, prices={s: 100.0 for s in syms}, target=tgt)
    assert all(o.side == "BUY" for o in plan.orders)
    assert plan.total_sell_krw == 0
    # buy notional should not exceed equity
    assert plan.total_buy_krw <= 10_000_000 + 1


def test_rebalance_quantities_are_integers_and_positive():
    syms = ["A", "B"]
    hist = {s: _rising(300, 100, 0.2) for s in syms}
    sig = SignalInputs(price_history=hist, index_history=_rising(300))
    tgt = build_target_allocation(syms, sig, AllocationConfig(vol_targeting=False))
    plan = build_rebalance_plan(as_of="2026-06-01", total_equity_krw=5_000_000,
                                current_holdings={}, prices={"A": 70000, "B": 55000}, target=tgt)
    for o in plan.orders:
        assert isinstance(o.quantity, int) and o.quantity > 0
        assert o.notional == pytest.approx(o.quantity * o.price)


def test_sell_cost_includes_tax():
    # hold too much of A -> should SELL; sell cost includes tax (higher bps than buy)
    syms = ["A", "B"]
    hist = {s: _rising(300, 100, 0.2) for s in syms}
    sig = SignalInputs(price_history=hist, index_history=_rising(300))
    tgt = build_target_allocation(syms, sig, AllocationConfig(vol_targeting=False))
    # overweight A massively
    plan = build_rebalance_plan(as_of="2026-06-01", total_equity_krw=10_000_000,
                                current_holdings={"A": 90}, prices={"A": 100000, "B": 100000},
                                target=tgt)
    sells = [o for o in plan.orders if o.side == "SELL"]
    assert sells, "expected a SELL to trim overweight A"
    o = sells[0]
    # sell cost rate ~ (1.5+5+20)/1e4 = 26.5bps
    assert o.est_cost_krw == pytest.approx(o.notional * (1.5 + 5 + 20) / 1e4, rel=1e-3)


def test_min_trade_filter_skips_tiny_orders():
    syms = ["A", "B"]
    hist = {s: _rising(300, 100, 0.2) for s in syms}
    sig = SignalInputs(price_history=hist, index_history=_rising(300))
    tgt = build_target_allocation(syms, sig, AllocationConfig(vol_targeting=False))
    # holdings already almost exactly at target -> tiny diffs filtered
    eq = 10_000_000
    px = {"A": 100.0, "B": 100.0}
    # first compute plan from cash to learn target qty, then feed those as holdings
    plan0 = build_rebalance_plan(as_of="d", total_equity_krw=eq, current_holdings={},
                                 prices=px, target=tgt)
    holdings = {o.symbol: o.quantity for o in plan0.orders}
    plan1 = build_rebalance_plan(as_of="d", total_equity_krw=eq, current_holdings=holdings,
                                 prices=px, target=tgt, min_trade_krw=50_000)
    # remaining orders should be small/none
    assert len(plan1.orders) <= len(plan0.orders)


def test_plan_to_dict_has_no_broker_fields():
    syms = ["A", "B"]
    hist = {s: _rising(300, 100, 0.2) for s in syms}
    sig = SignalInputs(price_history=hist, index_history=_rising(300))
    tgt = build_target_allocation(syms, sig)
    plan = build_rebalance_plan(as_of="d", total_equity_krw=1_000_000,
                                current_holdings={}, prices={"A": 100, "B": 100}, target=tgt)
    d = plan_to_dict(plan)
    assert d["is_live_authorization"] is False
    assert d["broker_order_sent"] is False
    blob = str(d).lower()
    assert "place_order" not in blob and "route_order" not in blob
