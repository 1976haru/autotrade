"""STEP 2-3 (2026-06-03): fast-clearing 결정 helper 테스트.

순수 함수 — broker / route_order / 실주문 0건. mock 입력만 사용 (휴무일 검증).
backward-compatible: 임계 0/None 이면 NO_ACTION (현재 동작 보존) 을 lock.
"""

from datetime import datetime, timezone

import pytest

from app.kis_paper.fast_clearing import (
    FastClearDecision,
    FastClearReason,
    evaluate_fast_clear,
    minutes_to_market_close,
)


# KST 14:00 = UTC 05:00 (장중, 마감 90분 전). KST 15:25 = UTC 06:25 (마감 5분 전).
_KST_1400 = datetime(2026, 6, 3, 5, 0, tzinfo=timezone.utc)
_KST_1525 = datetime(2026, 6, 3, 6, 25, tzinfo=timezone.utc)


# ---------- backward-compat: all thresholds off → NO_ACTION ----------

def test_all_thresholds_off_is_no_action():
    d = evaluate_fast_clear(last_price=10_000)
    assert d.should_clear is False
    assert d.reason is FastClearReason.NONE


def test_off_even_with_price_but_no_tp_sl():
    d = evaluate_fast_clear(last_price=10_000, holding_minutes=999,
                            now=_KST_1525)  # caps still 0 → off
    assert d.should_clear is False
    assert d.reason is FastClearReason.NONE


# ---------- stop loss (loss defense first) ----------

def test_stop_loss_triggers_when_price_at_or_below():
    d = evaluate_fast_clear(last_price=9_500, stop_loss_price=9_500)
    assert d.should_clear is True
    assert d.reason is FastClearReason.STOP_LOSS


def test_stop_loss_not_triggered_above():
    d = evaluate_fast_clear(last_price=9_600, stop_loss_price=9_500)
    assert d.should_clear is False


def test_stop_loss_evaluated_before_take_profit():
    # Degenerate: both would match; SL must win (loss defense priority).
    d = evaluate_fast_clear(last_price=9_000, stop_loss_price=9_500,
                            take_profit_price=8_000)
    assert d.reason is FastClearReason.STOP_LOSS


# ---------- take profit ----------

def test_take_profit_triggers_when_price_at_or_above():
    d = evaluate_fast_clear(last_price=11_000, take_profit_price=11_000)
    assert d.should_clear is True
    assert d.reason is FastClearReason.TAKE_PROFIT


def test_take_profit_not_triggered_below():
    d = evaluate_fast_clear(last_price=10_900, take_profit_price=11_000)
    assert d.should_clear is False


# ---------- EOD flatten ----------

def test_eod_flatten_triggers_near_close():
    # 15:25 KST = 5분 전, 설정 10분 → 발동.
    d = evaluate_fast_clear(last_price=10_000, now=_KST_1525,
                            eod_flatten_minutes_before_close=10)
    assert d.should_clear is True
    assert d.reason is FastClearReason.EOD_FLATTEN


def test_eod_flatten_not_triggered_when_far_from_close():
    # 14:00 KST = 90분 전, 설정 10분 → 미발동.
    d = evaluate_fast_clear(last_price=10_000, now=_KST_1400,
                            eod_flatten_minutes_before_close=10)
    assert d.should_clear is False


def test_eod_flatten_off_when_zero():
    d = evaluate_fast_clear(last_price=10_000, now=_KST_1525,
                            eod_flatten_minutes_before_close=0)
    assert d.should_clear is False


# ---------- time stop ----------

def test_time_stop_triggers_at_or_over_cap():
    d = evaluate_fast_clear(last_price=10_000, holding_minutes=45,
                            max_holding_minutes=45)
    assert d.should_clear is True
    assert d.reason is FastClearReason.TIME_STOP


def test_time_stop_not_triggered_under_cap():
    d = evaluate_fast_clear(last_price=10_000, holding_minutes=44,
                            max_holding_minutes=45)
    assert d.should_clear is False


def test_time_stop_off_when_zero_cap():
    d = evaluate_fast_clear(last_price=10_000, holding_minutes=9999,
                            max_holding_minutes=0)
    assert d.should_clear is False


# ---------- priority: EOD before time-stop ----------

def test_eod_takes_priority_over_time_stop():
    d = evaluate_fast_clear(last_price=10_000, now=_KST_1525,
                            eod_flatten_minutes_before_close=10,
                            holding_minutes=999, max_holding_minutes=30)
    assert d.reason is FastClearReason.EOD_FLATTEN


# ---------- minutes_to_market_close ----------

def test_minutes_to_close_computation():
    assert minutes_to_market_close(_KST_1525) == pytest.approx(5.0, abs=0.1)
    assert minutes_to_market_close(_KST_1400) == pytest.approx(90.0, abs=0.1)


# ---------- advisory invariant ----------

def test_decision_is_never_an_order_signal():
    d = evaluate_fast_clear(last_price=9_000, stop_loss_price=9_500)
    assert d.is_order_signal is False
    assert d.to_dict()["is_order_signal"] is False


def test_cannot_construct_order_signal_decision():
    with pytest.raises(ValueError):
        FastClearDecision(True, FastClearReason.STOP_LOSS, "", is_order_signal=True)
