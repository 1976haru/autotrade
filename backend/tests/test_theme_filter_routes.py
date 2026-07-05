"""테마 런타임 토글 API와 KST 만료."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core import runtime_config as rc


@pytest.fixture(autouse=True)
def _tmp_runtime_file(tmp_path, monkeypatch):
    path = tmp_path / "runtime_overrides.json"
    monkeypatch.setattr(rc, "overrides_path", lambda: path)
    monkeypatch.setattr(rc, "_legacy_overrides_path", lambda: tmp_path / "legacy_none.json")
    rc.reset_runtime_overrides_for_tests()
    yield
    rc.reset_runtime_overrides_for_tests()


def test_theme_toggle_api_off_and_on_without_restart(client):
    before = client.get("/api/theme-filter")
    assert before.status_code == 200
    assert before.json()["restart_required"] is False

    off = client.patch(
        "/api/theme-filter/semiconductor",
        json={"enabled": False, "duration": "until_enabled"},
    )
    assert off.status_code == 200
    assert "semiconductor" in off.json()["effective_disabled_theme_ids"]

    on = client.patch(
        "/api/theme-filter/semiconductor",
        json={"enabled": True},
    )
    assert on.status_code == 200
    assert "semiconductor" not in on.json()["effective_disabled_theme_ids"]


def test_today_expires_at_next_kst_midnight():
    now = datetime(2026, 7, 5, 14, 59, tzinfo=timezone.utc)  # 23:59 KST
    rc.set_theme_enabled("semiconductor", enabled=False, duration="today", now=now)
    assert rc.effective_disabled_theme_ids(now) == {"semiconductor"}
    assert rc.effective_disabled_theme_ids(
        datetime(2026, 7, 5, 15, 0, tzinfo=timezone.utc)
    ) == set()


def test_runtime_off_then_on_changes_scan_without_restart():
    from app.kis_paper.driver_bridge import _scan_universe_symbols

    settings = SimpleNamespace(kis_paper_smoke_mode=False)
    now = datetime(2026, 7, 5, 5, 0, tzinfo=timezone.utc)
    candidates = ["005930", "000660", "005380"]

    rc.set_theme_enabled("semiconductor", enabled=False, duration="until_enabled", now=now)
    assert _scan_universe_symbols(settings, override=candidates, now=now) == ["005380"]

    rc.set_theme_enabled("semiconductor", enabled=True, now=now)
    assert _scan_universe_symbols(settings, override=candidates, now=now) == candidates


def test_theme_toggle_validation(client):
    bad_duration = client.patch(
        "/api/theme-filter/semiconductor",
        json={"enabled": False, "duration": "forever"},
    )
    assert bad_duration.status_code == 400
    unknown = client.patch(
        "/api/theme-filter/no_such_theme",
        json={"enabled": False, "duration": "today"},
    )
    assert unknown.status_code == 404
