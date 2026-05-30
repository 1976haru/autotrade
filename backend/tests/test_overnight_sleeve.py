"""오버나이트 모의 sleeve — 분리 추적 + 검증미통과 라벨 + 계산전용 테스트."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.auto_paper.overnight_sleeve import (
    OvernightConfig,
    OvernightSignalInputs,
    OvernightPlan,
    build_overnight_plan,
    plan_to_dict,
    OVERNIGHT_TRADE_REASON,
    DAYTRADE_TRADE_REASON,
    VALIDATION_FAILED_LABEL,
    _volume_ratio,
)

MODULE = Path(__file__).resolve().parents[1] / "app" / "auto_paper" / "overnight_sleeve.py"


def _sig(us_ret, n_syms=6, vol_spike=True):
    ph, vh, prices = {}, {}, {}
    for i in range(n_syms):
        s = f"S{i}"
        ph[s] = [100.0 + j for j in range(40)]
        base = [1000.0] * 39
        last = 3000.0 if vol_spike else 500.0   # prev-day volume vs 20d avg
        vh[s] = base + [last]
        prices[s] = 100.0
    return OvernightSignalInputs(price_history=ph, volume_history=vh,
                                 us_prev_return=us_ret, prices=prices)


# ---------- separation from day-trade ----------
def test_trade_reason_is_separate_from_daytrade():
    assert OVERNIGHT_TRADE_REASON == "overnight_paper"
    assert DAYTRADE_TRADE_REASON == "kis_paper_auto"
    assert OVERNIGHT_TRADE_REASON != DAYTRADE_TRADE_REASON
    plan = build_overnight_plan(as_of="2026-06-01", signals=_sig(0.01),
                                cfg=OvernightConfig(overnight_seed_krw=5_000_000))
    assert plan.trade_reason == "overnight_paper"  # never mixes with day-trade


# ---------- validation-failed label always present ----------
def test_validation_failed_label_always_present():
    plan = build_overnight_plan(as_of="d", signals=_sig(0.01),
                                cfg=OvernightConfig(overnight_seed_krw=5_000_000))
    assert plan.validation_passed is False
    assert plan.validation_failed_label == VALIDATION_FAILED_LABEL
    assert "검증 미통과" in plan.validation_failed_label
    assert "학습" in plan.validation_failed_label
    d = plan_to_dict(plan)
    assert d["validation_passed"] is False
    assert "검증 미통과" in d["validation_failed_label"]


def test_cannot_build_plan_with_validation_passed_true():
    with pytest.raises(ValueError):
        OvernightPlan(as_of="d", trade_reason=OVERNIGHT_TRADE_REASON,
                      validation_failed_label=VALIDATION_FAILED_LABEL,
                      us_prev_return=0.01, signal_active=True, overnight_seed_krw=0,
                      orders=(), total_buy_krw=0, total_est_cost_krw=0,
                      validation_passed=True)


def test_cannot_strip_label():
    with pytest.raises(ValueError):
        OvernightPlan(as_of="d", trade_reason=OVERNIGHT_TRADE_REASON,
                      validation_failed_label="",
                      us_prev_return=0.01, signal_active=True, overnight_seed_krw=0,
                      orders=(), total_buy_krw=0, total_est_cost_krw=0)


def test_cannot_use_daytrade_reason():
    with pytest.raises(ValueError):
        OvernightPlan(as_of="d", trade_reason="kis_paper_auto",
                      validation_failed_label=VALIDATION_FAILED_LABEL,
                      us_prev_return=0.01, signal_active=True, overnight_seed_krw=0,
                      orders=(), total_buy_krw=0, total_est_cost_krw=0)


# ---------- safety invariants ----------
def test_safety_invariants_false():
    plan = build_overnight_plan(as_of="d", signals=_sig(0.01),
                                cfg=OvernightConfig(overnight_seed_krw=5_000_000))
    assert plan.is_order_signal is False
    assert plan.is_live_authorization is False
    assert plan.broker_order_sent is False
    assert plan.order_created is False


def test_no_broker_imports():
    src = MODULE.read_text(encoding="utf-8")
    forbidden = [
        r"^\s*from app\.brokers", r"^\s*import.*kis_client", r"^\s*from app\.execution",
        r"\.place_order\(", r"\.cancel_order\(", r"route_order\(",
        r"^\s*import anthropic", r"^\s*import openai", r"^\s*import httpx", r"^\s*import requests",
    ]
    for pat in forbidden:
        assert not re.search(pat, src, re.MULTILINE), f"forbidden: {pat}"


# ---------- signal behaviour ----------
def test_us_down_no_buy():
    plan = build_overnight_plan(as_of="d", signals=_sig(-0.01),
                                cfg=OvernightConfig(overnight_seed_krw=5_000_000))
    assert plan.signal_active is False
    assert len(plan.orders) == 0


def test_us_up_low_volume_no_buy():
    plan = build_overnight_plan(as_of="d", signals=_sig(0.01, vol_spike=False),
                                cfg=OvernightConfig(overnight_seed_krw=5_000_000, volume_min=1.0))
    # volume ratio 0.5 < 1.0 -> filtered out
    assert all(o.volume_ratio >= 1.0 for o in plan.orders)
    assert len(plan.orders) == 0


def test_us_up_high_volume_produces_buys():
    plan = build_overnight_plan(as_of="d", signals=_sig(0.01, vol_spike=True),
                                cfg=OvernightConfig(overnight_seed_krw=5_000_000, max_new_positions=3))
    assert plan.signal_active is True
    assert 1 <= len(plan.orders) <= 3
    assert all(o.side == "BUY" for o in plan.orders)
    assert all(o.quantity > 0 for o in plan.orders)


def test_seed_is_separate_no_seed_no_qty():
    # no seed and no per_symbol -> qty 0
    plan = build_overnight_plan(as_of="d", signals=_sig(0.01),
                                cfg=OvernightConfig())  # seed 0
    assert len(plan.orders) == 0
    assert any("시드" in n for n in plan.notes)


def test_total_buy_within_seed():
    seed = 3_000_000
    plan = build_overnight_plan(as_of="d", signals=_sig(0.01, n_syms=10),
                                cfg=OvernightConfig(overnight_seed_krw=seed, max_new_positions=5))
    assert plan.total_buy_krw <= seed + 1


def test_volume_ratio_helper():
    assert _volume_ratio([1000.0] * 39 + [2000.0], 20) == pytest.approx(2.0)
    assert _volume_ratio([1.0] * 5, 20) is None  # insufficient
