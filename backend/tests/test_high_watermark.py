"""트레일링 1·2단계 — 최고가 추적 + 섀도. 측정 전용(매도 0) 테스트."""
from __future__ import annotations

import ast
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import PositionHighWatermark, TrailingShadowOutcome
from app.positions.high_watermark import (
    update_and_shadow, summarize_trailing_shadow, _seed_hwm,
)

NOW = datetime(2026, 6, 17, 4, 0, tzinfo=timezone.utc)  # 13:00 KST


@pytest.fixture
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _pos(symbol, avg, mkt, qty=10):
    return SimpleNamespace(symbol=symbol, avg_price=avg, market_price=mkt, quantity=qty)


def test_hwm_created_and_ratchets_up(db):
    # 진입 100,000, 현재 102,000 (+2%, 활성선 +3% 미달) → hwm 시드 = max(102000, 103000)=103000.
    update_and_shadow(db, [_pos("005930", 100000, 102000)], now=NOW)
    r = db.get(PositionHighWatermark, "005930")
    assert r.high_watermark == 103000 and r.activated is False
    # 다음 틱 현재 105,000 → hwm 갱신 105000, activated True(래칫).
    update_and_shadow(db, [_pos("005930", 100000, 105000)], now=NOW)
    r = db.get(PositionHighWatermark, "005930")
    assert r.high_watermark == 105000 and r.activated is True
    # 현재 104,000으로 하락 → hwm 유지(105000, max), activated 유지(래칫).
    update_and_shadow(db, [_pos("005930", 100000, 104000)], now=NOW)
    r = db.get(PositionHighWatermark, "005930")
    assert r.high_watermark == 105000 and r.activated is True


def test_shadow_signal_when_activated_and_pullback(db):
    # +10%까지 갔다가(hwm 110000) 2% 꺾임 → 트레일가 110000×0.98=107800. 현재 107000 ≤ → 섀도 청산.
    update_and_shadow(db, [_pos("005930", 100000, 110000)], now=NOW)
    sigs = update_and_shadow(db, [_pos("005930", 100000, 107000)], now=NOW)
    assert len(sigs) == 1
    s = sigs[0]
    assert s.symbol == "005930" and s.trailing_would_exit is True
    assert round(s.trailing_return_pct, 1) == 7.0   # 트레일링 청산 +7%
    assert s.fixed_tp_return_pct == 3.0             # 고정익절은 +3%
    assert s.is_order_signal is False


def test_no_shadow_when_not_activated(db):
    # +2% (활성선 미달) → activated False → 섀도 시그널 없음(역방향 안전).
    sigs = update_and_shadow(db, [_pos("005930", 100000, 102000)], now=NOW)
    assert sigs == []


def test_restart_fallback_reseed(db):
    # 행 없음(재시작 모사) → 폴백 max(현재가, 진입가×1.03). 현재 101000 < 103000 → 103000.
    assert _seed_hwm(100000, 101000, 3.0) == 103000
    # 현재 105000 > 103000 → 105000(현재가).
    assert _seed_hwm(100000, 105000, 3.0) == 105000


def test_cleanup_records_outcome_and_deletes(db):
    # 보유 → 최고점 갱신 → 다음 틱 미보유(청산) → outcome 기록 + 행 삭제.
    update_and_shadow(db, [_pos("005930", 100000, 108000)], now=NOW)
    assert db.get(PositionHighWatermark, "005930") is not None
    update_and_shadow(db, [], now=NOW)   # 005930 청산됨
    assert db.get(PositionHighWatermark, "005930") is None
    outs = db.query(TrailingShadowOutcome).all()
    assert len(outs) == 1
    assert outs[0].symbol == "005930" and outs[0].activated is True
    assert round(outs[0].peak_return_pct, 1) == 8.0   # 최고점 +8%


def test_summarize_t3(db):
    # 두 outcome: 하나 +8%(트레일 우위), 하나 +3.2%(미달).
    update_and_shadow(db, [_pos("005930", 100000, 108000)], now=NOW); update_and_shadow(db, [], now=NOW)
    update_and_shadow(db, [_pos("000660", 100000, 103200)], now=NOW); update_and_shadow(db, [], now=NOW)
    rep = summarize_trailing_shadow(db)
    assert rep["activated_count"] == 2
    assert rep["trailing_would_win_count"] == 1   # +8% > 3+2=5 만 우위
    assert rep["is_order_signal"] is False


def test_static_no_broker_route_order_import():
    src = (Path(__file__).resolve().parents[1] / "app" / "positions" / "high_watermark.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    mods = []
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom): mods.append(n.module or "")
        elif isinstance(n, ast.Import): mods += [a.name for a in n.names]
    for bad in ("order_router", "order_executor", "route_order", "driver_bridge", "brokers"):
        assert not any(bad in (m or "") for m in mods), f"forbidden import: {bad}"
