"""5-04: 28일/100건 Paper 성과 기준 테스트.

핵심 invariant:
- 100건 미만 / 28일 미만 → INSUFFICIENT_SAMPLE (live promotion 차단)
- PF/win_rate/payoff/expectancy/MDD/연속손실/주문실패율/drift/event integrity/
  secret/flag anomaly 중 하나라도 미달 → BLOCKED
- 모두 충족 → READY_FOR_LIVE_REVIEW + can_review_live_canary=True
- 어떤 경우에도 auto_live_promotion / is_live_authorization = False
"""

from __future__ import annotations

import pytest

from app.governance import paper_gate_performance as pg
from app.governance.paper_gate_performance import (
    PerformanceCriteriaInput as Inp,
    evaluate_paper_gate_performance as ev,
)


def _good(**over) -> Inp:
    base = dict(
        evaluated_trades=120,
        trading_days=30,
        win_rate=0.55,
        payoff_ratio=1.2,
        profit_factor=1.5,
        expectancy=250.0,
        average_return=0.004,
        max_drawdown_pct=0.06,
        max_consecutive_losses=3,
        order_failure_rate=0.01,
        rejected_rate=0.01,
        partial_fill_rate=0.05,
        avg_slippage_bps=10.0,
        daily_loss_limit_breach_count=0,
        kill_switch_trigger_count=0,
        portfolio_drift_critical_count=0,
        event_integrity_high_critical_count=0,
        secret_exposure_count=0,
        broker_order_type_mismatch_count=0,
    )
    base.update(over)
    return Inp(**base)


def _status(res, name):
    for c in res.checks:
        if c.name == name:
            return c.status
    return None


# ── 표본 부족 ─────────────────────────────────────────────────────────────────


def test_under_100_trades_insufficient():
    res = ev(_good(evaluated_trades=99))
    assert res.verdict == pg.INSUFFICIENT_SAMPLE
    assert res.can_review_live_canary is False
    assert "INSUFFICIENT_SAMPLE" in res.reason_codes


def test_under_28_days_insufficient():
    res = ev(_good(trading_days=27))
    assert res.verdict == pg.INSUFFICIENT_SAMPLE
    assert "INSUFFICIENT_DAYS" in res.reason_codes
    assert res.can_review_live_canary is False


# ── 성과 기준 미달 → BLOCKED ─────────────────────────────────────────────────


def test_low_profit_factor_blocked():
    res = ev(_good(profit_factor=1.0))
    assert res.verdict == pg.BLOCKED
    assert _status(res, "profit_factor") == pg.FAIL
    assert res.can_review_live_canary is False


def test_low_win_rate_blocked():
    res = ev(_good(win_rate=0.40))
    assert _status(res, "win_rate") == pg.FAIL
    assert res.verdict == pg.BLOCKED


def test_low_payoff_blocked():
    assert _status(ev(_good(payoff_ratio=0.8)), "payoff_ratio") == pg.FAIL


def test_non_positive_expectancy_blocked():
    res = ev(_good(expectancy=0.0))
    assert _status(res, "expectancy") == pg.FAIL
    assert res.verdict == pg.BLOCKED


def test_high_mdd_blocked():
    res = ev(_good(max_drawdown_pct=0.15))
    assert _status(res, "max_drawdown") == pg.FAIL


def test_high_consecutive_losses_blocked():
    assert _status(ev(_good(max_consecutive_losses=6)), "max_consecutive_losses") == pg.FAIL


def test_high_order_failure_rate_blocked():
    assert _status(ev(_good(order_failure_rate=0.10)), "order_failure_rate") == pg.FAIL


def test_high_rejected_rate_blocked():
    assert _status(ev(_good(rejected_rate=0.10)), "rejected_rate") == pg.FAIL


# ── 무결성/정합성/보안 ───────────────────────────────────────────────────────


def test_portfolio_drift_blocked():
    res = ev(_good(portfolio_drift_critical_count=1))
    assert _status(res, "portfolio_drift") == pg.FAIL
    assert "PORTFOLIO_DRIFT_CRITICAL" in res.reason_codes


def test_event_integrity_blocked():
    res = ev(_good(event_integrity_high_critical_count=2))
    assert _status(res, "event_integrity") == pg.FAIL


def test_secret_exposure_blocked():
    res = ev(_good(secret_exposure_count=1))
    assert _status(res, "secret_exposure") == pg.FAIL
    assert "SECRET_EXPOSURE_DETECTED" in res.reason_codes


def test_broker_order_type_mismatch_blocked():
    assert _status(ev(_good(broker_order_type_mismatch_count=1)),
                   "broker_order_type_mismatch") == pg.FAIL


def test_kill_switch_or_loss_breach_blocked():
    assert _status(ev(_good(kill_switch_trigger_count=1)), "kill_switch_trigger") == pg.FAIL
    assert _status(ev(_good(daily_loss_limit_breach_count=1)),
                   "daily_loss_limit_breach") == pg.FAIL


# ── WARN (차단 아님) ─────────────────────────────────────────────────────────


def test_high_partial_fill_is_warn_not_block():
    res = ev(_good(partial_fill_rate=0.50))
    assert _status(res, "partial_fill_rate") == pg.WARN
    # WARN 만으로는 차단되지 않음.
    assert res.verdict == pg.READY_FOR_LIVE_REVIEW


def test_high_slippage_is_warn_not_block():
    res = ev(_good(avg_slippage_bps=100.0))
    assert _status(res, "avg_slippage_bps") == pg.WARN
    assert res.verdict == pg.READY_FOR_LIVE_REVIEW


# ── 모든 기준 충족 → 검토 가능 (그러나 자동 승인 아님) ──────────────────────


def test_all_criteria_met_ready_for_review():
    res = ev(_good())
    assert res.verdict == pg.READY_FOR_LIVE_REVIEW
    assert res.can_review_live_canary is True
    assert res.live_promotion_allowed is True
    # 안전 invariant — 검토 가능 ≠ 자동 전환/실전 승인.
    assert res.auto_live_promotion is False
    assert res.is_live_authorization is False


def test_to_dict_safe_invariants():
    d = ev(_good()).to_dict()
    assert d["auto_live_promotion"] is False
    assert d["is_live_authorization"] is False
    assert d["contains_secret"] is False
    assert d["can_review_live_canary"] is True


# ── 불변 가드 ────────────────────────────────────────────────────────────────


def test_result_invariants_enforced():
    from app.governance.paper_gate_performance import PaperGatePerformanceResult as R
    with pytest.raises(ValueError):
        R(verdict=pg.READY_FOR_LIVE_REVIEW, checks=[], pass_count=0, warn_count=0,
          fail_count=0, reason_codes=[], can_review_live_canary=True,
          is_live_authorization=True)
    with pytest.raises(ValueError):
        # can_review_live_canary True 인데 verdict 가 READY 가 아니면 모순.
        R(verdict=pg.BLOCKED, checks=[], pass_count=0, warn_count=0,
          fail_count=1, reason_codes=[], can_review_live_canary=True)


def test_no_broker_imports():
    import app.governance.paper_gate_performance as mod
    src = open(mod.__file__, encoding="utf-8").read()
    for forbidden in ("from app.brokers", "from app.execution", ".place_order(",
                      "route_order(", "import httpx", "import requests"):
        assert forbidden not in src
