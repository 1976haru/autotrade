"""P1-A — 승인/시작이 "성공"인데 KIS 모의주문이 0건인 설정을 사전에 차단.

`order_path_blockers` 는 승인 반영 설정(ApprovedPaperSettings) 기준으로 사유를
반환하고, `create_approval` 은 사유가 있으면 ORDER_PATH_NOT_READY 로 거부(409).
start 응답에는 `order_mode` / `order_blockers` 가 추가 필드로 실린다.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.kis_paper.one_click_session import (
    KST, create_approval, order_path_blockers,
)

TOKEN = "desktop-only-secret"


def ready_settings(**over):
    base = dict(
        kis_is_paper=True, enable_live_trading=False, enable_ai_execution=False,
        kis_account_no="1234567801", kis_product_code="01",
        kis_app_key="FAKE-KEY", kis_app_secret="FAKE-SECRET",
        market_data_provider="kis", enable_ai_paper_background_tick=True,
        kis_paper_fill_polling=True, kis_paper_smoke_mode=False,
        enable_kis_paper_auto_trading=False, kis_paper_auto_order_dry_run=True,
        krx_calendar_override_path="",
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()


@pytest.fixture
def approval_file(tmp_path, monkeypatch):
    path = tmp_path / "approval.json"
    monkeypatch.setenv("AUTOTRADE_PAPER_APPROVAL_FILE", str(path))
    return path


def _risk():
    return SimpleNamespace(emergency_stop=False)


def _today() -> str:
    return datetime.now(KST).date().isoformat()


def test_ready_settings_have_no_blockers():
    assert order_path_blockers(ready_settings()) == []


def test_default_example_mock_provider_is_blocked():
    assert order_path_blockers(ready_settings(market_data_provider="mock")) == [
        "MARKET_DATA_PROVIDER_NOT_KIS"]
    assert order_path_blockers(ready_settings(market_data_provider=" KIS ")) == []


@pytest.mark.parametrize("override,code", [
    ({"market_data_provider": "mock"}, "MARKET_DATA_PROVIDER_NOT_KIS"),
    ({"market_data_provider": "yfinance"}, "MARKET_DATA_PROVIDER_NOT_KIS"),
    ({"enable_ai_paper_background_tick": False}, "BACKGROUND_TICK_DISABLED"),
    ({"kis_app_key": ""}, "KIS_CREDENTIALS_MISSING"),
    ({"kis_app_secret": "  "}, "KIS_CREDENTIALS_MISSING"),
    ({"kis_account_no": ""}, "KIS_CREDENTIALS_MISSING"),
    ({"kis_paper_fill_polling": False}, "FILL_POLLER_DISABLED"),
    ({"kis_paper_smoke_mode": True}, "KIS_PAPER_SMOKE_MODE_ON"),
])
def test_each_blocker_rejects_approval(db, approval_file, override, code):
    s = ready_settings(**override)
    assert code in order_path_blockers(s)
    with pytest.raises(ValueError) as exc:
        create_approval(db=db, settings=s, risk=_risk(), target_date=_today(),
                        actor="op", explicit_ack=True)
    assert str(exc.value).startswith("ORDER_PATH_NOT_READY:")
    assert code in str(exc.value).split(":", 1)[1].split(",")
    assert not approval_file.exists()          # 승인 기록 0건


def test_multiple_blockers_are_all_reported():
    codes = order_path_blockers(ready_settings(
        market_data_provider="mock", enable_ai_paper_background_tick=False))
    assert codes == ["MARKET_DATA_PROVIDER_NOT_KIS", "BACKGROUND_TICK_DISABLED"]


def test_blockers_never_loosen_safety_flags():
    s = ready_settings()
    order_path_blockers(s)
    assert s.enable_live_trading is False and s.kis_is_paper is True
    assert s.enable_kis_paper_auto_trading is False   # 원본 설정 mutate 0건


def _post_approval(client):
    return client.post("/api/auto-paper/paper-session-approval",
                       headers={"X-Autotrade-Desktop-Auth": TOKEN},
                       json={"target_date": _today(), "explicit_ack": True,
                             "actor": "operator"})


def test_http_mock_provider_approval_returns_409(client, approval_file, monkeypatch):
    import app.api.routes_auto_paper as routes
    monkeypatch.setenv("AUTOTRADE_DESKTOP_AUTH_TOKEN", TOKEN)
    monkeypatch.setattr(routes, "get_settings",
                        lambda: ready_settings(market_data_provider="mock"))
    r = _post_approval(client)
    assert r.status_code == 409
    assert r.json()["detail"] == "ORDER_PATH_NOT_READY:MARKET_DATA_PROVIDER_NOT_KIS"
    assert not approval_file.exists()


def test_http_ready_approval_then_start_reports_paper_auto(client, approval_file, monkeypatch):
    import app.api.routes_auto_paper as routes
    from app.auto_paper.loop import get_auto_paper_loop
    monkeypatch.setenv("AUTOTRADE_DESKTOP_AUTH_TOKEN", TOKEN)
    monkeypatch.setattr(routes, "get_settings", lambda: ready_settings())
    r = _post_approval(client)
    assert r.status_code == 200, r.text
    loop = get_auto_paper_loop()
    loop.reset()
    try:
        start = client.post("/api/auto-paper/start")
        assert start.status_code == 200, start.text
        body = start.json()
        assert body["order_mode"] == "KIS_REALTIME_PAPER_AUTO"
        assert body["order_blockers"] == []
        assert "state" in body and "intraday_buy_allowed" in body   # 기존 필드 유지
    finally:
        loop.reset()


def test_start_without_approval_reports_virtual_only(client, approval_file, monkeypatch):
    import app.api.routes_auto_paper as routes
    from app.auto_paper.loop import get_auto_paper_loop
    monkeypatch.setattr(routes, "get_settings",
                        lambda: ready_settings(market_data_provider="mock"))
    loop = get_auto_paper_loop()
    loop.reset()
    try:
        body = client.post("/api/auto-paper/start").json()
        assert body["order_mode"] == "VIRTUAL_ONLY"
        assert body["order_blockers"] == ["MARKET_DATA_PROVIDER_NOT_KIS"]
    finally:
        loop.reset()


def test_run_readiness_exposes_order_blockers(client, approval_file):
    body = client.get("/api/auto-paper/run-readiness").json()
    assert isinstance(body["order_blockers"], list)
    assert "armed_for" in body["loop"]
