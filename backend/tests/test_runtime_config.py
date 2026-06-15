"""R1/R4: 런타임 설정 오버라이드 (동시진입 종목 수 / 종목당 투자금) — backend.

주문 경로 미수정 / 안전 플래그 미접촉 검증 포함. 파일 저장은 tmp 로 격리.
"""
from __future__ import annotations

import contextlib
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import runtime_config as rc
from app.db.base import Base
from app.db.session import get_db
from app.main import app


# ★진단/테스트용 공용 클라이언트 — 모든 설정 PUT 은 반드시 이 헬퍼로.
#   (1) get_db 를 *인메모리* 로 격리 → 피드 기록이 라이브 DB 로 새지 않음(누수 차단).
#   (2) _diag_put 이 X-Event-Source: diagnostic 헤더를 강제 → operator 오태그 방지.
@contextlib.contextmanager
def _diag_app():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng)

    def _ov():
        s = TS()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _ov
    try:
        with TestClient(app) as c:
            yield c, TS   # TS: 인메모리 세션 팩토리(피드 검증용)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _diag_put(c, url, **kw):
    headers = {**(kw.pop("headers", None) or {}), "X-Event-Source": "diagnostic"}
    return c.put(url, headers=headers, **kw)


@pytest.fixture(autouse=True)
def _tmp_overrides(tmp_path, monkeypatch):
    # 실 데이터 디렉토리 대신 tmp 파일로 격리.
    path = tmp_path / "runtime_overrides.json"
    monkeypatch.setattr(rc, "overrides_path", lambda: path)
    # 레거시 마이그레이션이 *실* ./data 파일을 읽지 않도록 격리(없는 tmp 경로).
    monkeypatch.setattr(rc, "_legacy_overrides_path", lambda: tmp_path / "legacy_none.json")
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

@pytest.mark.parametrize("v", [0, 16, -1, 100])   # 2026-06-15: 상한 15 → 16 부터 out-of-range
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
    # T2(2026-06-12): daily_buy_limit_krw 가 {value,min,max,source} 객체로 — 스테퍼 meta.
    assert b["daily_buy_limit_krw"]["value"] >= 1  # 하드코딩 아님 — config/override 에서
    assert b["daily_buy_limit_krw"]["min"] == 3_000_000
    assert b["daily_buy_limit_krw"]["max"] == 300_000_000


def test_api_put_valid_returns_reread_effective():
    # ★_diag_app: get_db 인메모리 격리 → 피드 기록이 라이브 DB 로 누수 0(이 테스트가
    #   예전엔 라이브 SessionLocal 에 CONFIG_CHANGE 를 새겼던 12:47/12:52/12:53 누수원).
    with _diag_app() as (c, _):
        r = _diag_put(c, "/api/runtime-config", json={"max_concurrent_positions": 3, "per_stock_budget": 500_000})
    assert r.status_code == 200
    b = r.json()
    assert b["max_concurrent_positions"]["value"] == 3
    assert b["max_concurrent_positions"]["source"] == "override"
    assert b["per_stock_budget"]["value"] == 500_000


@pytest.mark.parametrize("payload,frag", [
    ({"max_concurrent_positions": 0}, "동시진입"),
    ({"max_concurrent_positions": 16}, "동시진입"),   # 2026-06-15: 상한 15 → 16 거부
    ({"per_stock_budget": 50_000}, "종목당"),
    ({"per_stock_budget": 20_000_000}, "종목당"),
])
def test_api_put_out_of_range_400(payload, frag):
    with _diag_app() as (c, _):
        r = _diag_put(c, "/api/runtime-config", json=payload)
    assert r.status_code == 400
    assert frag in r.json()["detail"]


def test_api_put_empty_body_400():
    with _diag_app() as (c, _):
        r = _diag_put(c, "/api/runtime-config", json={})
    assert r.status_code == 400


def test_no_raw_config_put_without_diagnostic_helper():
    """★lint 가드: 테스트 파일에서 설정 PUT 은 반드시 _diag_put(헤더+격리) 경유.
    raw `_diag_put(c, "/api/runtime-config"...)` 가 새로 들어오면 누수 재발 → 즉시 실패."""
    import pathlib, re
    here = pathlib.Path(__file__)
    src = here.read_text(encoding="utf-8")
    # 헬퍼 정의/호출(_diag_put) 줄을 제외하고, raw c.put 으로 runtime-config 를 PUT 하는 줄 탐지.
    bad = []
    for ln in src.splitlines():
        if "runtime-config" in ln and re.search(r"\.put\(", ln) and "_diag_put" not in ln and "def _diag_put" not in ln:
            bad.append(ln.strip()[:80])
    assert not bad, f"raw config PUT without _diag_put (누수 위험): {bad}"


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
            r = _diag_put(c, "/api/runtime-config", json={"per_stock_budget": 500_000})
        assert r.status_code == 200
        with TS() as s:
            entries = query_paper_decision_log(s, limit=10)
        assert any("종목당 투자금" in (e.reason or "") for e in entries)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_lowering_cap_does_not_liquidate_holdings(monkeypatch):
    """R1 규칙: 동시진입 종목 수를 낮춰도 *기존 보유는 비접촉*(강제 청산 0).
    max_concurrent 는 BUY 게이트에서만 쓰이고 SELL/청산을 트리거하지 않는다."""
    import asyncio
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db.base import Base
    from app.kis_paper import driver_bridge as dbge
    from app.market_data.kis_realtime import KisRealtimeQuote

    rc.set_runtime_overrides(max_concurrent_positions=1, per_stock_budget=300_000)

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)

    async def _hold_fn(symbol, *, client, now, market_is_open, **kw):
        return None, KisRealtimeQuote(symbol=symbol, status="KIS_PRICE_NODATA", price=0.0, is_stale=False)

    sells = {"n": 0}

    async def _route(**kw):
        order = kw.get("order")
        if order is not None and str(getattr(order, "side", "")).upper().endswith("SELL"):
            sells["n"] += 1
        return None

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

    class _HeldBroker:  # 3종목 보유 — cap(1) 보다 많음
        async def get_positions(self):
            return [SimpleNamespace(symbol=s, quantity=1) for s in ("005930", "000660", "000270")]

    out = asyncio.run(dbge.kis_paper_realtime_scan_tick(
        session_factory=Session, broker=_HeldBroker(), risk=object(),
        route_order_fn=_route, settings=settings, market_input_fn=_hold_fn,
        universe_symbols=["005930", "000660", "000270"],
        client=object(), now=datetime(2026, 5, 22, 5, 0, tzinfo=timezone.utc),
    ))
    # cap 을 1 로 낮췄지만 보유 3종목을 *팔지 않는다*(강제 청산 0).
    assert sells["n"] == 0
    assert out["broker_order_sent"] is False


def test_bot_scan_reads_active_profile_at_council(monkeypatch):
    # S1: 봇이 council 호출 시 effective_active_profile() 로 활성 성향을 읽는다.
    import asyncio
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db.base import Base
    from app.kis_paper import driver_bridge as dbge
    from app.agents.agent_council import StrategyMarketInput
    from app.market_data.kis_realtime import KIS_PRICE_OK, KisRealtimeQuote

    rc.set_runtime_overrides(active_profile="conservative")
    calls = {"n": 0}
    real = rc.effective_active_profile
    monkeypatch.setattr(rc, "effective_active_profile",
                        lambda: (calls.__setitem__("n", calls["n"] + 1), real())[1])

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)

    async def _mi(symbol, *, client, now, market_is_open, **kw):
        mi = StrategyMarketInput(
            symbol=symbol, current_price=100.0, prev_close=100.0, open_price=100.0,
            vwap=100.0, opening_range_high=101.0, opening_range_low=99.0,
            recent_closes=(100.0, 100.0, 100.0), current_volume=100.0, avg_volume=100.0,
            market_regime="SIDEWAYS")
        return mi, KisRealtimeQuote(symbol=symbol, status=KIS_PRICE_OK, price=100.0, is_stale=False)

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
        route_order_fn=(lambda **kw: None), settings=settings, market_input_fn=_mi,
        universe_symbols=["005930"], client=object(),
        now=datetime(2026, 5, 22, 5, 0, tzinfo=timezone.utc)))
    assert calls["n"] >= 1


def test_overrides_do_not_touch_safety_flags():
    # 화이트리스트 = 6개(동시진입·종목당·일일한도·손절·익절·성향) — C1(2026-06-10) 손절/익절,
    #   T2(2026-06-12) 일일 매수 한도 추가. 안전 플래그/confidence 는 *여전히* 화이트리스트 밖.
    assert set(rc._OVERRIDE_KEYS) == {
        "max_concurrent_positions", "per_stock_budget", "daily_buy_limit_krw",
        "stop_loss_pct", "take_profit_pct", "active_profile",
    }
    for flag in ("enable_live_trading", "kis_is_paper", "enable_ai_execution"):
        assert flag not in rc._OVERRIDE_KEYS
    with pytest.raises(TypeError):
        rc.set_runtime_overrides(enable_live_trading=True)  # 받지 않는 인자


def test_stop_take_override_roundtrip_and_validation():
    # 손절/익절 오버라이드 저장 → effective 반영, 범위 밖 RuntimeConfigValidationError.
    out = rc.set_runtime_overrides(stop_loss_pct=2.0, take_profit_pct=3.5)
    assert out["stop_loss_pct"]["value"] == 2.0
    assert out["stop_loss_pct"]["source"] == "override"
    assert out["take_profit_pct"]["value"] == 3.5
    assert rc.effective_stop_loss_pct() == 2.0
    assert rc.effective_take_profit_pct() == 3.5
    for bad in (0.1, 10.5, -2.0):
        with pytest.raises(rc.RuntimeConfigValidationError):
            rc.set_runtime_overrides(stop_loss_pct=bad)
    for bad in (0.1, 25.0):
        with pytest.raises(rc.RuntimeConfigValidationError):
            rc.set_runtime_overrides(take_profit_pct=bad)


# ── S1: 성향 런타임 전환 ───────────────────────────────────────────────────────

def test_active_profile_default_and_override():
    assert rc.effective_active_profile() == "balanced"
    rc.set_runtime_overrides(active_profile="aggressive")
    assert rc.effective_active_profile() == "aggressive"
    cfg = rc.get_runtime_config()
    assert cfg["active_profile"]["value"] == "aggressive"
    assert cfg["active_profile"]["source"] == "override"


def test_invalid_profile_raises():
    with pytest.raises(rc.RuntimeConfigValidationError):
        rc.set_runtime_overrides(active_profile="yolo")


def test_profile_restart_restore(_tmp_overrides):
    rc.set_runtime_overrides(active_profile="conservative")
    rc._cache = None  # 재시작 모사
    assert rc.effective_active_profile() == "conservative"


def test_api_profile_blocked_when_bot_running(monkeypatch):
    import app.api.routes_runtime_config as rrc
    monkeypatch.setattr(rrc, "_bot_is_running", lambda: True)
    with TestClient(app) as c:
        r = _diag_put(c, "/api/runtime-config/profile", json={"profile": "aggressive"})
    assert r.status_code == 409
    assert "멈춘" in r.json()["detail"]
    assert rc.effective_active_profile() == "balanced"   # 저장 안 됨


def test_api_profile_clamp_held_when_stopped(monkeypatch):
    import app.api.routes_runtime_config as rrc
    from app.db.session import get_db
    monkeypatch.setattr(rrc, "_bot_is_running", lambda: False)
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
            r = _diag_put(c, "/api/runtime-config/profile", json={"profile": "aggressive"})
        assert r.status_code == 200
        b = r.json()
        assert b["active_profile"]["value"] == "aggressive"
        eff = b["profile_effective"]
        # ★클램프(e158704): aggressive 프리셋(0.45)이 config floor 밑으로 못 내려감.
        assert eff["preset_min_confidence"] == 0.45
        assert eff["effective_min_confidence"] == max(0.45, eff["config_floor"])
        assert eff["effective_min_confidence"] >= eff["config_floor"]
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_api_profile_invalid_400(monkeypatch):
    import app.api.routes_runtime_config as rrc
    monkeypatch.setattr(rrc, "_bot_is_running", lambda: False)
    with TestClient(app) as c:
        r = _diag_put(c, "/api/runtime-config/profile", json={"profile": "x"})
    assert r.status_code == 400


def test_api_profile_records_activity_feed(monkeypatch):
    import app.api.routes_runtime_config as rrc
    from app.db.session import get_db
    from app.auto_paper.decision_log import query_paper_decision_log
    monkeypatch.setattr(rrc, "_bot_is_running", lambda: False)
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
            _diag_put(c, "/api/runtime-config/profile", json={"profile": "aggressive"})
        with TS() as s:
            texts = [e.reason for e in query_paper_decision_log(s, limit=10)]
        assert any("운용 성향을 안정적 → 공격적" in t for t in texts)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_overrides_path_is_absolute_cwd_independent(monkeypatch):
    # ★CWD-상대 database_url 이라도 overrides_path 는 절대경로(다른 CWD 호출이 다른
    #   파일을 보지 않게 — 기록 없는 원복 방지).
    import app.core.runtime_config as _rc
    from app.core.config import get_settings
    # _tmp_overrides autouse 가 overrides_path 를 패치하므로 _data_dir 를 직접 검증.
    monkeypatch.setattr(_rc, "overrides_path", _rc.overrides_path)  # noop(명시)
    s = get_settings()
    if str(s.database_url or "").startswith("sqlite:///"):
        d = _rc._data_dir()
        assert d.is_absolute(), f"_data_dir must be absolute, got {d}"


def test_overrides_dir_prefers_appdata(monkeypatch, tmp_path):
    # ★override 는 %APPDATA%\Autotrade (.env·토큰 캐시와 동거, CWD 독립).
    import app.core.runtime_config as _rc
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert _rc._overrides_base_dir() == tmp_path / "Autotrade"


def test_legacy_override_migrates_to_new_location(monkeypatch, _tmp_overrides, tmp_path):
    # 신 위치(=_tmp_overrides) 비었고 구 위치(./data)에 있으면 1회 이전(기존 설정 보존).
    import app.core.runtime_config as _rc
    legacy = tmp_path / "legacy_overrides.json"
    legacy.write_text('{"per_stock_budget": 700000}', encoding="utf-8")
    monkeypatch.setattr(_rc, "_legacy_overrides_path", lambda: legacy)
    _rc._cache = None
    _rc._migrate_legacy_override_if_needed()
    assert _tmp_overrides.exists()
    assert "700000" in _tmp_overrides.read_text(encoding="utf-8")
    # 이전 후 effective 도 반영.
    _rc._cache = None
    assert _rc.effective_per_stock_budget() == 700000


# ── T2(2026-06-12): 일일 매수 한도 런타임 오버라이드 ─────────────────────────────

def test_daily_buy_limit_override_and_effective():
    rc.reset_runtime_overrides_for_tests()
    try:
        out = rc.set_runtime_overrides(daily_buy_limit_krw=50_000_000)
        assert out["daily_buy_limit_krw"]["value"] == 50_000_000
        assert out["daily_buy_limit_krw"]["source"] == "override"
        assert rc.effective_daily_buy_limit() == 50_000_000
    finally:
        rc.reset_runtime_overrides_for_tests()


def test_daily_buy_limit_range_validation():
    rc.reset_runtime_overrides_for_tests()
    with pytest.raises(rc.RuntimeConfigValidationError):
        rc.set_runtime_overrides(daily_buy_limit_krw=1_000_000)      # < 300만 하한
    with pytest.raises(rc.RuntimeConfigValidationError):
        rc.set_runtime_overrides(daily_buy_limit_krw=400_000_000)    # > 3억 상한
    rc.reset_runtime_overrides_for_tests()


def test_driver_bridge_reads_effective_daily_limit():
    # R1 패턴: 봇 스캔이 settings 직접이 아니라 effective getter 를 읽는지(소스 정합).
    rc.reset_runtime_overrides_for_tests()
    try:
        rc.set_runtime_overrides(daily_buy_limit_krw=7_000_000)
        assert rc.effective_daily_buy_limit() == 7_000_000
    finally:
        rc.reset_runtime_overrides_for_tests()
