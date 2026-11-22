"""AG2/AG7: 기각 신호 그림자 추적 — 가상 손익 / 추적·완료 분리 / 가격 미확보 / 방식 B."""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import AgentDecisionLog
import app.performance.shadow as sh


@pytest.fixture
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(sh, "_history_path", lambda: tmp_path / "shadow_prices.json")
    monkeypatch.setattr(sh, "effective_per_stock_budget", lambda: 1_000_000)  # qty 결정성
    sh.reset_shadow_cache_for_tests()
    yield
    sh.reset_shadow_cache_for_tests()


def _reject(db, *, symbol, price, at):
    db.add(AgentDecisionLog(
        created_at=at, agent_name="kis_paper_auto_executor", symbol=symbol, mode="PAPER",
        decision="BUY", confidence=None, reasons=["rejected"],
        meta={"final_action": "BUY", "votes": [{"strategy": "ORB", "signal": "BUY"}],
              "broker_order_sent": False, "signal_price": price}))


def _write_hist(tmp_path, mapping):
    (tmp_path / "shadow_prices.json").write_text(json.dumps(mapping), encoding="utf-8")


def test_virtual_pnl_avoided_loss_and_missed_gain(db, tmp_path):
    # 2026-06-01(월) 기각 → +5영업일 = 06-08(월). today=06-08 → 완료.
    _reject(db, symbol="005930", price=1000, at=datetime(2026, 6, 1, 3, tzinfo=timezone.utc))   # 하락 → 회피 손실
    _reject(db, symbol="000660", price=1000, at=datetime(2026, 6, 1, 3, tzinfo=timezone.utc))   # 상승 → 놓친 이익
    db.commit()
    _write_hist(tmp_path, {"005930": {"2026-06-08": 900.0}, "000660": {"2026-06-08": 1200.0}})
    out = sh.compute_shadow(db, start=date(2026, 6, 1), end=date(2026, 6, 8), today=date(2026, 6, 8))
    assert out["completed_count"] == 2
    assert out["avoided_loss_krw"] == 100_000   # (1000-900)*1000
    assert out["missed_gain_krw"] == 200_000    # (1200-1000)*1000
    assert out["correct_rejection_count"] == 1  # 005930 하락 → 기각 옳음
    assert out["correct_rate"] == 0.5


def test_tracking_not_yet_complete(db):
    _reject(db, symbol="005930", price=1000, at=datetime(2026, 6, 5, 3, tzinfo=timezone.utc))
    db.commit()
    out = sh.compute_shadow(db, start=date(2026, 6, 5), end=date(2026, 6, 5), today=date(2026, 6, 5))
    assert out["tracking_count"] == 1 and out["completed_count"] == 0
    assert out["no_data"] is True


def test_price_unavailable_excluded(db, tmp_path):
    _reject(db, symbol="005930", price=1000, at=datetime(2026, 6, 1, 3, tzinfo=timezone.utc))
    db.commit()
    _write_hist(tmp_path, {})  # 종가 미확보
    out = sh.compute_shadow(db, start=date(2026, 6, 1), end=date(2026, 6, 8), today=date(2026, 6, 8))
    assert out["price_unavailable_count"] == 1
    assert out["completed_count"] == 0


def test_method_b_fetch_caches_and_limits_calls(db, monkeypatch):
    # 방식 B: 활성 윈도 고유 종목만, 하루 1회(캐시) — 매 요청 실조회 금지.
    _reject(db, symbol="005930", price=1000, at=datetime(2026, 6, 5, 3, tzinfo=timezone.utc))
    _reject(db, symbol="005930", price=1100, at=datetime(2026, 6, 5, 4, tzinfo=timezone.utc))  # 같은 종목
    db.commit()
    calls = {"symbols": None, "n": 0}

    async def _fetch(symbols):
        calls["n"] += 1
        calls["symbols"] = list(symbols)
        return {s: 1000.0 for s in symbols}

    today = datetime(2026, 6, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(sh, "today_kst", lambda: today.date())
    asyncio.run(sh.refresh_shadow_prices(db, now_ts=1000.0, price_fetcher=_fetch))
    asyncio.run(sh.refresh_shadow_prices(db, now_ts=1000.0, price_fetcher=_fetch))  # 캐시
    assert calls["n"] == 1                       # 매 요청 실조회 아님
    assert calls["symbols"] == ["005930"]        # 고유 종목 1개(중복 제거)


def test_api_shadow_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    from app.db.session import get_db
    from app.main import app
    import app.api.routes_agent_dashboard as rad  # noqa: F401
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, expire_on_commit=False)

    async def _no_refresh(*a, **k):
        return None
    monkeypatch.setattr(sh, "refresh_shadow_prices", _no_refresh)

    def _ov():
        s = TS()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _ov
    try:
        with TestClient(app) as c:
            b = c.get("/api/agent/shadow?period=daily").json()
        assert b["no_data"] is True and b["track_days"] == 5
    finally:
        app.dependency_overrides.pop(get_db, None)
