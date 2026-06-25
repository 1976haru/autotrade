"""손절 미작동(STOP_LOSS 0건) 1차 패치 회귀 테스트 — 2026-06-25.

결함 B: 보유 청산 SELL 이 진입용 confidence/quality 게이트에 막혀 손실 포지션이
        청산되지 않던 문제 → held_position 청산 SELL 은 품질/확신 게이트 면제.
결함 A(1순위): KIS inquire-balance 가 보유분 avg_price=0 간헐 반환 → PositionContext
        None → 강제손절 평가 skip. 직전 성공 스냅샷의 비-0 avg 로 복구.

★보호 4계층(RiskManager/PermissionGate/OrderExecutor/route_order) 미접촉 — 본 테스트는
  사전 advisory 게이트(auto_permission)와 보유맵(driver_bridge) 데이터층만 검증한다.
  주문 0건, broker 미접촉(fake).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from app.kis_paper.auto_permission import (
    KisPaperOrderPermissionInput,
    KisPaperPermReason,
    evaluate_kis_paper_order_permission,
)
from app.kis_paper.auto_executor import build_permission_input, KisPaperAutoDecision
from app.kis_paper import driver_bridge


# KST 10:00 목요일(2026-06-25) = 장 OPEN + 기본 시간창(09:05~14:50) 안.
_OPEN_NOW = datetime(2026, 6, 25, 1, 0, 0, tzinfo=timezone.utc)


def _passing_input(**over) -> KisPaperOrderPermissionInput:
    """conf/quality 외 모든 게이트를 통과하는 기준 입력 (장 OPEN now)."""
    base = dict(
        enable_kis_paper_auto_trading=True, dry_run=True, kis_is_paper=True,
        enable_live_trading=False, broker_is_kis_paper=True, credentials_present=True,
        emergency_stop=False, side="SELL", notional_krw=100_000,
        confidence=0.9, quality_score=90, has_exit_plan=True,
        max_order_notional=1_000_000, daily_order_count=0, max_orders_per_day=10,
        window_start="09:05", window_end="14:50",
        min_confidence=0.6, min_quality_score=60, is_risk_exit=False,
        now=_OPEN_NOW,
    )
    base.update(over)
    return KisPaperOrderPermissionInput(**base)


# ── 결함 B: 게이트 면제 동작 (evaluate 레벨) ─────────────────────────────────

def test_B_held_sell_low_quality_is_exempt_and_allowed():
    """T_B1: 보유 청산 SELL + 낮은 conf/quality + is_risk_exit → 허용(면제)."""
    perm = evaluate_kis_paper_order_permission(
        _passing_input(side="SELL", confidence=0.4, quality_score=30, is_risk_exit=True))
    assert perm.allowed is True
    assert perm.reason_code == KisPaperPermReason.KIS_PAPER_ORDER_ALLOWED.value


def test_B_sell_low_quality_not_exempt_is_blocked():
    """대조: is_risk_exit=False 면 낮은 conf 그대로 차단(면제는 플래그가 있어야)."""
    perm = evaluate_kis_paper_order_permission(
        _passing_input(side="SELL", confidence=0.4, quality_score=30, is_risk_exit=False))
    assert perm.allowed is False
    assert perm.reason_code == KisPaperPermReason.LOW_CONFIDENCE.value


def test_B_buy_low_quality_always_blocked():
    """회귀: BUY 는 is_risk_exit 이 켜져도 진입 게이트 그대로 적용(악용 방지)."""
    perm = evaluate_kis_paper_order_permission(
        _passing_input(side="BUY", confidence=0.4, quality_score=30,
                       is_risk_exit=True, has_exit_plan=True))
    assert perm.allowed is False
    assert perm.reason_code == KisPaperPermReason.LOW_CONFIDENCE.value


def test_B_risk_exit_does_not_bypass_emergency_stop():
    """T_B7: 면제는 conf/quality 만 — 긴급정지는 청산 SELL 도 그대로 차단."""
    perm = evaluate_kis_paper_order_permission(
        _passing_input(side="SELL", confidence=0.4, quality_score=30,
                       is_risk_exit=True, emergency_stop=True))
    assert perm.allowed is False
    assert perm.reason_code == KisPaperPermReason.EMERGENCY_STOP_ENABLED.value


# ── 결함 B: is_risk_exit 산출 (build_permission_input 레벨 = 실제 변경부) ──────

def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        enable_kis_paper_auto_trading=True, kis_paper_auto_order_dry_run=True,
        kis_is_paper=True, enable_live_trading=False,
        kis_paper_auto_max_order_notional=1_000_000,
        kis_paper_auto_max_orders_per_day=10,
        kis_paper_auto_order_window_start="09:05",
        kis_paper_auto_order_window_end="14:50",
        kis_paper_auto_min_confidence=0.6, kis_paper_auto_min_quality_score=60,
    )


def _decision(**over) -> KisPaperAutoDecision:
    base = dict(symbol="000990", side="SELL", quantity=10, price=10_000,
                confidence=0.4, quality_score=30, held_position=False,
                sell_reason_code=None, price_source="kis")
    base.update(over)
    return KisPaperAutoDecision(**base)


def _build(decision):
    return build_permission_input(
        settings=_settings(), decision=decision, broker_is_kis_paper=True,
        credentials_present=True, emergency_stop=False, daily_order_count=0,
        now=_OPEN_NOW)


def test_B_build_held_sell_marks_risk_exit():
    """핵심 변경: 보유(held_position=True) 청산 SELL 은 sell_reason 없어도 면제 대상."""
    inp = _build(_decision(side="SELL", held_position=True, sell_reason_code=None))
    assert inp.is_risk_exit is True


def test_B_build_naked_sell_not_risk_exit():
    """미보유(held_position=False)·청산성 reason 없음 SELL 은 면제 아님."""
    inp = _build(_decision(side="SELL", held_position=False, sell_reason_code=None))
    assert inp.is_risk_exit is False


def test_B_build_buy_not_risk_exit():
    """회귀: BUY 는 절대 면제 아님."""
    inp = _build(_decision(side="BUY", held_position=True, sell_reason_code=None))
    assert inp.is_risk_exit is False


def test_B_build_stoploss_reason_still_risk_exit():
    """회귀(레거시 보존): sell_reason=STOP_LOSS 면 held_position 무관하게 면제."""
    inp = _build(_decision(side="SELL", held_position=False, sell_reason_code="STOP_LOSS"))
    assert inp.is_risk_exit is True


# ── 결함 A(1순위): avg_price=0 → 직전 스냅샷 avg 복구 (_kis_held_map) ─────────

class _FakeBroker:
    def __init__(self, positions):
        self._positions = positions

    async def get_positions(self):
        return self._positions


def _pos(symbol, qty, avg, sellable=None):
    return SimpleNamespace(symbol=symbol, quantity=qty, avg_price=avg,
                           sellable_quantity=(sellable if sellable is not None else qty))


def _reset_snapshot():
    driver_bridge._HELD_SNAPSHOT = {}
    driver_bridge._HELD_SNAPSHOT_AT = None


def test_A_recovers_avg_from_prior_snapshot_when_kis_returns_zero():
    """T_A1: 직전 tick avg=10000 → 다음 tick KIS avg=0 → 스냅샷 10000 로 복구."""
    _reset_snapshot()
    # 1틱: 정상 avg → 스냅샷 채움.
    out1 = asyncio.run(driver_bridge._kis_held_map(
        _FakeBroker([_pos("000990", 10, 10_000)]), fallback=set(), now=_OPEN_NOW))
    assert out1["000990"]["avg_price"] == 10_000
    # 2틱: KIS 가 avg=0 반환(간헐 결함) → 복구.
    out2 = asyncio.run(driver_bridge._kis_held_map(
        _FakeBroker([_pos("000990", 10, 0)]), fallback=set(), now=_OPEN_NOW))
    assert out2["000990"]["avg_price"] == 10_000, "avg=0 이면 직전 스냅샷 avg 로 복구해야 함"


def test_A_keeps_kis_avg_when_present():
    """회귀: KIS 가 정상 avg 를 주면 그대로 사용(복구 로직이 정상값 변형 금지)."""
    _reset_snapshot()
    out = asyncio.run(driver_bridge._kis_held_map(
        _FakeBroker([_pos("000990", 10, 12_000)]), fallback=set(), now=_OPEN_NOW))
    assert out["000990"]["avg_price"] == 12_000


def test_A_stays_zero_when_no_prior_snapshot():
    """스냅샷이 없으면 보수적으로 0 유지(결함 B 안전망이 vote 청산 처리)."""
    _reset_snapshot()
    out = asyncio.run(driver_bridge._kis_held_map(
        _FakeBroker([_pos("000990", 10, 0)]), fallback=set(), now=_OPEN_NOW))
    assert out["000990"]["avg_price"] == 0


# ── E2E: 전체 체인 (결함 A+B+C) — 실제 scan tick ────────────────────────────
# 결함 C(2026-06-25): effective_stop_loss_pct=1.0(=1%) 을 PositionContext._norm_pct 가
#   비율(100%)로 오해석 → 손절가 0 → STOP_LOSS 영영 안 뜸(손절 ≤1.0% 설정 시). driver_bridge
#   가 *절대* 손절/익절가를 계산해 넘기도록 수정. 아래 E2E 가 손절 1.0% 에서 실발동 + 오청산 0 검증.

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.kis_paper.driver_bridge import kis_paper_realtime_scan_tick  # noqa: E402
from app.market_data.kis_realtime import KIS_PRICE_OK, KisRealtimeQuote  # noqa: E402
from app.risk.risk_manager import RiskDecision  # noqa: E402

_AVG = 162800
_SYM = "000990"


def _scan_settings():
    return SimpleNamespace(
        market_data_provider="kis", enable_kis_paper_auto_trading=True,
        kis_paper_auto_order_dry_run=False, kis_is_paper=True, enable_live_trading=False,
        kis_paper_auto_max_order_notional=100_000_000, kis_paper_auto_max_orders_per_day=10,
        kis_paper_auto_order_window_start="09:05", kis_paper_auto_order_window_end="14:50",
        kis_paper_auto_min_confidence=0.6, kis_paper_auto_min_quality_score=60,
        kis_paper_smoke_mode=False, kis_paper_smoke_symbol="005930", kis_paper_smoke_qty=1,
        kis_paper_max_concurrent_positions=5, kis_paper_per_symbol_notional_krw=100_000_000,
        kis_paper_daily_buy_limit_krw=3_000_000, kis_paper_max_new_positions_per_tick=1,
        kis_paper_scan_max_symbols=10,
        kis_app_key="FAKE", kis_app_secret="FAKE", kis_account_no="0000000000",
        kis_product_code="01",
    )


def _real_paper_broker(avg):
    """실 KisBrokerAdapter(is_paper=True) → assert_paper_broker 통과, get_positions 만 stub."""
    from app.brokers.kis import KisBrokerAdapter
    b = KisBrokerAdapter(is_paper=True)
    async def _pos():
        return [SimpleNamespace(symbol=_SYM, quantity=12, avg_price=avg, sellable_quantity=12)]
    b.get_positions = _pos
    return b


def _exit_only_input_fn(ret_pct):
    price = round(_AVG * (1 + ret_pct), 0)
    async def _fn(symbol, *, client, now, market_is_open, **kw):
        return None, KisRealtimeQuote(symbol=symbol, status=KIS_PRICE_OK,
                                      price=price, is_stale=False)
    return _fn


def _recording_route(calls):
    async def _fn(**kw):
        calls.append(kw.get("order"))
        audit = SimpleNamespace(id=1, broker_order_id="PAPER-SELL-1",
                                broker_status="FILLED", filled_quantity=12, executed=True)
        return SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[], audit=audit)
    return _fn


def _run_scan(kis_avg, seed_snapshot, ret_pct):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)
    _reset_snapshot()
    if seed_snapshot:
        driver_bridge._HELD_SNAPSHOT = {_SYM: {"hldg": 12, "ord_psbl": 12, "avg_price": _AVG}}
        driver_bridge._HELD_SNAPSHOT_AT = _OPEN_NOW
    calls = []
    out = asyncio.run(kis_paper_realtime_scan_tick(
        session_factory=Session, broker=_real_paper_broker(kis_avg), risk=object(),
        route_order_fn=_recording_route(calls), settings=_scan_settings(),
        market_input_fn=_exit_only_input_fn(ret_pct), universe_symbols=[_SYM],
        client=object(), now=_OPEN_NOW))
    sells = [o for o in calls if str(getattr(o, "side", "")).upper().endswith("SELL")]
    return out, sells


@pytest.fixture(autouse=True)
def _stop_1pct(monkeypatch):
    """손절 1.0% / 익절 2.0% 고정(결함 C 재현 조건: stop ≤ 1.0)."""
    import app.core.runtime_config as rc
    monkeypatch.setattr(rc, "effective_stop_loss_pct", lambda: 1.0)
    monkeypatch.setattr(rc, "effective_take_profit_pct", lambda: 2.0)


def test_E2E_stoploss_fires_at_1pct_with_avg0_recovery():
    """결함 A+B+C: avg=0→스냅샷복구 + 손절 1.0% + 현재가 -2% → STOP_LOSS SELL 전송."""
    out, sells = _run_scan(kis_avg=0, seed_snapshot=True, ret_pct=-0.02)
    assert out["orders_submitted"] == 1, "손절 1.0% 에서 STOP_LOSS 가 실제로 청산돼야 함"
    assert len(sells) == 1 and int(sells[0].quantity) == 12


def test_E2E_stoploss_fires_on_today_actual_case_minus_3_94():
    """오늘(06-25) 미청산 -3.94% 케이스를 패치 적용 시 청산되는지."""
    out, sells = _run_scan(kis_avg=0, seed_snapshot=True, ret_pct=-0.0394)
    assert out["orders_submitted"] == 1 and len(sells) == 1


def test_E2E_no_misfire_on_profit():
    """오청산 0: 정상 avg + +1% 이익(손절·익절 미breach) → SELL 0건."""
    out, sells = _run_scan(kis_avg=_AVG, seed_snapshot=False, ret_pct=+0.01)
    assert out["orders_submitted"] == 0 and len(sells) == 0
