"""R1/R4: 런타임 설정 오버라이드 (동시진입 종목 수 / 종목당 투자금) — backend.

주문 경로 미수정 / 안전 플래그 미접촉 검증 포함. 파일 저장은 tmp 로 격리.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core import runtime_config as rc
from app.main import app


@pytest.fixture(autouse=True)
def _tmp_overrides(tmp_path, monkeypatch):
    # 실 데이터 디렉토리 대신 tmp 파일로 격리.
    path = tmp_path / "runtime_overrides.json"
    monkeypatch.setattr(rc, "overrides_path", lambda: path)
    rc.reset_runtime_overrides_for_tests()
    yield path
    rc.reset_runtime_overrides_for_tests()


# ── effective getters / 저장 / 재조회 ─────────────────────────────────────────

def test_effective_falls_back_to_env_when_no_override():
    cfg = rc.get_runtime_config()
    assert cfg["max_concurrent_positions"]["source"] == "env"
    assert cfg["per_stock_budget"]["source"] == "env"
    assert cfg["max_concurrent_positions"]["value"] >= 1


def test_set_then_reread_is_consistent():
    out = rc.set_runtime_overrides(max_concurrent_positions=3, per_stock_budget=500_000)
    assert out["max_concurrent_positions"]["value"] == 3
    assert out["max_concurrent_positions"]["source"] == "override"
    assert out["per_stock_budget"]["value"] == 500_000
    # 다시 읽어도 동일 (저장 후 재조회 일관성).
    again = rc.get_runtime_config()
    assert again["max_concurrent_positions"]["value"] == 3
    assert again["per_stock_budget"]["value"] == 500_000
    assert rc.effective_max_concurrent_positions() == 3
    assert rc.effective_per_stock_budget() == 500_000


def test_partial_update_keeps_other_value():
    rc.set_runtime_overrides(max_concurrent_positions=2, per_stock_budget=300_000)
    rc.set_runtime_overrides(per_stock_budget=700_000)  # 종목수는 그대로
    assert rc.effective_max_concurrent_positions() == 2
    assert rc.effective_per_stock_budget() == 700_000


def test_changes_carries_before_after():
    rc.set_runtime_overrides(max_concurrent_positions=5)  # env 5 → 5 (변화 없음 가능)
    out = rc.set_runtime_overrides(max_concurrent_positions=3)
    chg = {c["key"]: c for c in out["changes"]}
    assert chg["max_concurrent_positions"]["before"] == 5
    assert chg["max_concurrent_positions"]["after"] == 3


# ── 검증 (서버 측 필수) ───────────────────────────────────────────────────────

@pytest.mark.parametrize("v", [0, 11, -1, 100])
def test_max_concurrent_out_of_range_raises(v):
    with pytest.raises(rc.RuntimeConfigValidationError):
        rc.set_runtime_overrides(max_concurrent_positions=v)


@pytest.mark.parametrize("v", [99_999, 10_000_001, 0])
def test_per_stock_budget_out_of_range_raises(v):
    with pytest.raises(rc.RuntimeConfigValidationError):
        rc.set_runtime_overrides(per_stock_budget=v)


# ── 재시작 복원 / 손상 폴백 ───────────────────────────────────────────────────

def test_restart_restores_from_file(_tmp_overrides):
    rc.set_runtime_overrides(max_concurrent_positions=4, per_stock_budget=200_000)
    # "재시작" 모사 — 인메모리 캐시만 비우고 파일은 유지.
    rc._cache = None
    assert rc.effective_max_concurrent_positions() == 4
    assert rc.effective_per_stock_budget() == 200_000


def test_corrupt_file_falls_back_to_env_with_warning(_tmp_overrides, caplog):
    _tmp_overrides.write_text("{ this is not valid json ", encoding="utf-8")
    rc._cache = None
    import logging
    with caplog.at_level(logging.WARNING):
        val = rc.effective_max_concurrent_positions()  # 폴백 — raise 안 함
    assert val == int(__import__("app.core.config", fromlist=["get_settings"]).get_settings().kis_paper_max_concurrent_positions)
    assert any("runtime_config" in r.message or "손상" in r.message for r in caplog.records)


def test_non_object_json_falls_back(_tmp_overrides):
    _tmp_overrides.write_text("[1, 2, 3]", encoding="utf-8")
    rc._cache = None
    assert rc.get_runtime_config()["max_concurrent_positions"]["source"] == "env"


# ── API ───────────────────────────────────────────────────────────────────────

def test_api_get_runtime_config():
    with TestClient(app) as c:
        r = c.get("/api/runtime-config")
    assert r.status_code == 200
    b = r.json()
    assert b["max_concurrent_positions"]["source"] == "env"
    assert b["is_live_authorization"] is False
    assert b["daily_buy_limit_krw"] >= 1  # 하드코딩 아님 — config 에서


def test_api_put_valid_returns_reread_effective():
    with TestClient(app) as c:
        r = c.put("/api/runtime-config", json={"max_concurrent_positions": 3, "per_stock_budget": 500_000})
    assert r.status_code == 200
    b = r.json()
    assert b["max_concurrent_positions"]["value"] == 3
    assert b["max_concurrent_positions"]["source"] == "override"
    assert b["per_stock_budget"]["value"] == 500_000


@pytest.mark.parametrize("payload,frag", [
    ({"max_concurrent_positions": 0}, "동시진입"),
    ({"max_concurrent_positions": 11}, "동시진입"),
    ({"per_stock_budget": 50_000}, "종목당"),
    ({"per_stock_budget": 20_000_000}, "종목당"),
])
def test_api_put_out_of_range_400(payload, frag):
    with TestClient(app) as c:
        r = c.put("/api/runtime-config", json=payload)
    assert r.status_code == 400
    assert frag in r.json()["detail"]


def test_api_put_empty_body_400():
    with TestClient(app) as c:
        r = c.put("/api/runtime-config", json={})
    assert r.status_code == 400


# ── 봇 반영 (mock) / 기존 보유 비접촉 ──────────────────────────────────────────

def test_bot_scan_reads_effective_getters_each_tick(monkeypatch):
    """봇(kis_paper_realtime_scan_tick)이 매 사이클 effective getter 를 호출 —
    저장 즉시 다음 신규 매수 판단부터 반영됨을 보장(파일 직접 읽기 0)."""
    import asyncio
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db.base import Base
    from app.kis_paper import driver_bridge as dbge
    from app.market_data.kis_realtime import KIS_PRICE_OK, KisRealtimeQuote

    rc.set_runtime_overrides(max_concurrent_positions=2, per_stock_budget=300_000)
    calls = {"mc": 0, "bud": 0}
    real_mc, real_bud = rc.effective_max_concurrent_positions, rc.effective_per_stock_budget
    monkeypatch.setattr(rc, "effective_max_concurrent_positions",
                        lambda: (calls.__setitem__("mc", calls["mc"] + 1), real_mc())[1])
    monkeypatch.setattr(rc, "effective_per_stock_budget",
                        lambda: (calls.__setitem__("bud", calls["bud"] + 1), real_bud())[1])

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)

    async def _mi_fn(symbol, *, client, now, market_is_open, **kw):
        # HOLD 신호여도 무방 — getter 는 루프 진입 전에 호출된다.
        return None, KisRealtimeQuote(symbol=symbol, status="KIS_PRICE_NODATA", price=0.0, is_stale=False)

    settings = SimpleNamespace(
        market_data_provider="kis", enable_kis_paper_auto_trading=True,
        kis_paper_auto_order_dry_run=False, kis_is_paper=True, enable_live_trading=False,
        kis_paper_auto_max_order_notional=1_000_000, kis_paper_auto_max_orders_per_day=10,
        kis_paper_auto_order_window_start="00:00", kis_paper_auto_order_window_end="23:59",
        kis_paper_auto_min_confidence=0.6, kis_paper_auto_min_quality_score=60,
        kis_paper_smoke_mode=False, kis_paper_smoke_symbol="005930", kis_paper_smoke_qty=1,
        kis_paper_daily_buy_limit_krw=3_000_000, kis_paper_max_new_positions_per_tick=1,
        kis_paper_scan_max_symbols=10, kis_app_key="K", kis_app_secret="S",
        kis_account_no="00000000", kis_product_code="01",
    )

    class _B:
        async def get_positions(self):
            return []

    asyncio.run(dbge.kis_paper_realtime_scan_tick(
        session_factory=Session, broker=_B(), risk=object(),
        route_order_fn=(lambda **kw: None), settings=settings,
        market_input_fn=_mi_fn, universe_symbols=["005930"],
        client=object(), now=datetime(2026, 5, 22, 5, 0, tzinfo=timezone.utc),
    ))
    assert calls["mc"] >= 1   # 봇이 동시진입 한도 effective getter 호출
    assert calls["bud"] >= 1  # 봇이 종목당 투자금 effective getter 호출


def _mem_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db.base import Base
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


def test_r2_record_changes_into_activity_feed():
    # R2: 변경을 활동 피드(AgentDecisionLog/paper decision-log)에 기록 — 기존 경로 재사용.
    from app.core.runtime_config_activity import record_runtime_config_changes
    from app.auto_paper.decision_log import query_paper_decision_log
    TS = _mem_session()
    with TS() as db:
        n = record_runtime_config_changes(db, [
            {"key": "per_stock_budget", "before": 1_000_000, "after": 500_000},
            {"key": "max_concurrent_positions", "before": 5, "after": 3},
        ])
        assert n == 2
        entries = query_paper_decision_log(db, limit=10)
        texts = [e.reason for e in entries]
        assert any("종목당 투자금을 100만 원 → 50만 원으로 바꿨어요" in t for t in texts)
        assert any("동시진입 종목 수를 5개 → 3개로 바꿨어요" in t for t in texts)
        assert all(e.decision_action == "CONFIG_CHANGE"
                   for e in entries if e.agent_name == "Operator")


def test_r2_no_change_records_nothing():
    from app.core.runtime_config_activity import record_runtime_config_changes
    TS = _mem_session()
    with TS() as db:
        assert record_runtime_config_changes(db, []) == 0


def test_api_put_records_activity_event():
    # PUT 성공 → decision-log 에 운영자 변경 항목이 남는다.
    from app.db.session import get_db
    from app.auto_paper.decision_log import query_paper_decision_log
    TS = _mem_session()

    def _ov():
        s = TS()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _ov
    try:
        with TestClient(app) as c:
            r = c.put("/api/runtime-config", json={"per_stock_budget": 500_000})
        assert r.status_code == 200
        with TS() as s:
            entries = query_paper_decision_log(s, limit=10)
        assert any("종목당 투자금" in (e.reason or "") for e in entries)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_overrides_do_not_touch_safety_flags():
    # 본 모듈은 안전 플래그를 *읽지도 쓰지도* 않는다 (값 변경 가능 키 화이트리스트).
    assert set(rc._OVERRIDE_KEYS) == {"max_concurrent_positions", "per_stock_budget"}
    with pytest.raises(TypeError):
        rc.set_runtime_overrides(enable_live_trading=True)  # 받지 않는 인자
