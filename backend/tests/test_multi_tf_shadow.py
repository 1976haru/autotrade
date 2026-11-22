"""MULTI-TF-SHADOW-V1 테스트 — 실 KIS 호출 0건(fake client), 주문 경로 미접촉 확인.

results/multi_timeframe/design.md 콤보(5m entry + 60m confirm AGENT_COUNCIL)를
read-only 로 관측하는 app.shadow.multi_tf_shadow 의 순수 로직 + 안전 불변식 테스트.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

import app.shadow.multi_tf_shadow as mtf
from app.shadow.multi_tf_shadow import (
    ShadowTickSummary,
    _bars_per_day,
    _prior_days_needed,
    _resample_to_n,
    evaluate_combo,
    persist_tick,
    run_shadow_tick,
)

NOW = datetime(2026, 7, 9, 1, 0, 0, tzinfo=timezone.utc)   # KST 10:00, 장중


def _minute_bars(day: str, start_hhmm: str, count: int, base_price: float = 74000.0):
    out = []
    h, m = int(start_hhmm[:2]), int(start_hhmm[2:4])
    for i in range(count):
        mm = m + i
        hh = h + mm // 60
        mm = mm % 60
        out.append({"d": day, "t": f"{hh:02d}{mm:02d}00",
                    "o": base_price, "h": base_price + 100, "l": base_price - 100,
                    "c": base_price + i, "v": 1000.0})
    return out


class FakeClient:
    """read-only 시세만 제공 — place_order 없음. inquire_time_dailychartprice 는
    _fetch_full_day_minutes 의 hour-페이징 호출을 하루치 bars 로 한 번에 만족."""

    def __init__(self, *, price="75000", raise_price=False, bars_by_date=None):
        self._price = price
        self._raise = raise_price
        self._bars_by_date = bars_by_date or {}

    async def get_price(self, symbol):
        if self._raise:
            raise RuntimeError("network down")
        return {"output": {
            "stck_prpr": self._price, "stck_oprc": "74000", "stck_hgpr": "76000",
            "stck_lwpr": "73500", "stck_sdpr": "74500", "acml_vol": "1200000",
            "wghn_avrg_stck_prc": "74800",
        }}

    async def inquire_time_dailychartprice(self, symbol, *, date, hour=None, **kw):
        bars = self._bars_by_date.get(date, [])
        # 원 KIS 응답 스키마(output2)로 변환.
        rows = [{"stck_bsop_date": b["d"], "stck_cntg_hour": b["t"],
                  "stck_oprc": b["o"], "stck_hgpr": b["h"], "stck_lwpr": b["l"],
                  "stck_prpr": b["c"], "cntg_vol": b["v"]} for b in bars]
        return {"output2": rows}


def test_resample_to_n_basic():
    bars = _minute_bars("20260709", "0900", 15)
    out5 = _resample_to_n(bars, 5)
    assert len(out5) == 3
    assert out5[0]["t"] == "090000"
    out60 = _resample_to_n(bars, 60)
    assert len(out60) == 1
    assert out60[0]["t"] == "090000"


def test_prior_days_needed_matches_kis_realtime_doc():
    # results/bar_interval_generalize/report.md: 5분=1~2일, 30분=3일, 60분=5~6일.
    assert _prior_days_needed(5) == 2
    assert _prior_days_needed(30) == 4
    assert _prior_days_needed(60) == 6


def test_bars_per_day():
    assert _bars_per_day(60) == 6
    assert _bars_per_day(30) == 13
    assert _bars_per_day(5) == 78


def test_evaluate_combo_quote_failure_returns_hold_and_fetch_error():
    # fetch_realtime_quote 는 절대 raise 하지 않는 계약(실패는 status 로 반환) —
    # get_price 실패는 quote.status=KIS_MARKET_DATA_UNAVAILABLE 로 fetch_error=True.
    res = asyncio.run(evaluate_combo(FakeClient(raise_price=True), "005930", NOW))
    assert res.entry_signal == "HOLD"
    assert res.confirm_signal == "HOLD"
    assert res.fetch_error is True
    assert res.p1_price is None


def test_evaluate_combo_insufficient_bars_returns_hold_no_crash():
    # bars_by_date 비움 -> today_min 빈 리스트 -> HOLD/HOLD, 예외 0.
    res = asyncio.run(evaluate_combo(FakeClient(bars_by_date={}), "005930", NOW))
    assert res.entry_signal == "HOLD"
    assert res.confirm_signal == "HOLD"
    assert res.quote_ok is True   # 시세 자체는 정상 수신


def test_run_shadow_tick_market_closed_short_circuits(monkeypatch):
    monkeypatch.setattr(mtf, "is_market_open", lambda now: False)
    summary = asyncio.run(run_shadow_tick(client=FakeClient(), symbols=["005930"], now=NOW))
    assert summary.symbols_scanned == 0
    assert summary.combo_signals_found == 0


def test_run_shadow_tick_open_scans_symbols(monkeypatch):
    monkeypatch.setattr(mtf, "is_market_open", lambda now: True)
    summary = asyncio.run(run_shadow_tick(
        client=FakeClient(bars_by_date={}), symbols=["005930", "000660"], now=NOW,
    ))
    assert summary.symbols_scanned == 2
    assert isinstance(summary, ShadowTickSummary)


def test_persist_tick_writes_rows_with_safety_invariants():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    from app.db.models import MultiTfShadowSignal, MultiTfShadowTick

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        MultiTfShadowTick.__table__, MultiTfShadowSignal.__table__,
    ])
    Session = sessionmaker(bind=engine)

    summary = ShadowTickSummary(
        symbols_scanned=1, combo_signals_found=1, fetch_errors=0, rate_limited_count=0,
        tick_duration_seconds=1.23,
        signals=[{
            "symbol": "005930", "entry_tf": "5m", "confirm_tf": "60m",
            "entry_signal": "BUY", "confirm_signal": "BUY",
            "p1_price": 75000.0, "p1_timestamp": NOW,
            "p2_price": 75050.0, "p2_timestamp": NOW,
            "elapsed_seconds": 4.0, "slippage_bps": 6.67,
            "prev_close": 74500.0, "confidence": 0.7,
            "is_order_signal": False, "is_live_authorization": False,
        }],
    )
    with Session() as db:
        persist_tick(db, summary)
        tick_row = db.query(MultiTfShadowTick).one()
        sig_row = db.query(MultiTfShadowSignal).one()
        assert tick_row.combo_signals_found == 1
        assert sig_row.symbol == "005930"
        assert sig_row.slippage_bps == pytest.approx(6.67)
        assert sig_row.is_order_signal is False
        assert sig_row.is_live_authorization is False


def test_module_has_no_order_api_imports():
    # 주문 경로 import/호출 0건 — 정적 검사(안전 불변식, docstring 의 설명 언급은 허용).
    src = Path(mtf.__file__).read_text(encoding="utf-8")
    assert ".place_order(" not in src
    assert "route_order(" not in src
    assert "from app.execution" not in src
    assert "OrderExecutor(" not in src
    assert "import OrderExecutor" not in src
    assert "get_risk_manager" not in src
    assert "RiskManager(" not in src
