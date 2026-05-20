"""Tests for `GET /api/market/state` (fix/market-closed-state-distinction).

본 엔드포인트는 *순수 시계산* — DB / broker / 외부 호출 0건이며 항상 200 을
반환해야 한다. 사용자가 장 종료 후 desktop EXE 를 실행했을 때 frontend 가
"조회 실패" 가 아니라 "장 종료로 신규 판단 없음" 으로 안내할 수 있도록
phase 정보를 제공.

검증 시나리오 (사용자 요청 매트릭스):
  - 평일 08:50 KST → PRE_OPEN
  - 평일 09:00 KST → OPEN
  - 평일 15:31 KST → CLOSED
  - 토 / 일       → WEEKEND
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.api.routes_market_state import get_market_state
from app.scheduler.market_clock import MarketPhase


def _kst_to_utc(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    """KST naive 시각 → UTC tz-aware datetime."""
    utc_hour_unwrapped = hour - 9
    day_adjust = 0
    if utc_hour_unwrapped < 0:
        utc_hour_unwrapped += 24
        day_adjust = -1
    return datetime(
        year, month, day + day_adjust,
        utc_hour_unwrapped, minute,
        tzinfo=timezone.utc,
    )


@pytest.fixture
def frozen_utc():
    """Allow each test to freeze datetime.now(timezone.utc) inside the route module."""
    import app.api.routes_market_state as mod

    holder = {"value": datetime(2026, 5, 18, 0, 0, tzinfo=timezone.utc)}

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D401 — stub
            v = holder["value"]
            return v.astimezone(tz) if tz else v

    with patch.object(mod, "datetime", _FakeDatetime):
        yield holder


class TestMarketStateRoute:
    """순수 시계산 endpoint — 항상 200, phase 정확성 + payload contract 검증."""

    # 사용자 요청서 §5 의 4가지 시나리오 매트릭스 ─────────────────────────────

    def test_weekday_0850_kst_returns_pre_open(self, frozen_utc):
        # 2026-05-18 (월) 08:50 KST
        frozen_utc["value"] = _kst_to_utc(2026, 5, 18, 8, 50)
        payload = get_market_state()
        assert payload["phase"] == MarketPhase.PRE_OPEN.value
        assert payload["is_open"] is False
        assert payload["is_closed"] is True
        assert "장 시작 전" in payload["reason"]

    def test_weekday_0900_kst_returns_open(self, frozen_utc):
        # 2026-05-18 (월) 09:00 KST
        frozen_utc["value"] = _kst_to_utc(2026, 5, 18, 9, 0)
        payload = get_market_state()
        assert payload["phase"] == MarketPhase.OPEN.value
        assert payload["is_open"] is True
        assert payload["is_closed"] is False
        assert "정규장" in payload["label"]

    def test_weekday_1531_kst_returns_closed(self, frozen_utc):
        # 2026-05-18 (월) 15:31 KST → 장 종료 직후
        frozen_utc["value"] = _kst_to_utc(2026, 5, 18, 15, 31)
        payload = get_market_state()
        assert payload["phase"] == MarketPhase.CLOSED.value
        assert payload["is_open"] is False
        assert payload["is_closed"] is True
        # 사용자 메시지의 "장 종료로 신규 판단 없음" 의도와 일치하는지 sanity check
        assert "장 종료" in payload["reason"]
        assert "신규 판단 없음" in payload["reason"]

    def test_saturday_returns_weekend(self, frozen_utc):
        # 2026-05-23 (토) 10:00 KST
        frozen_utc["value"] = _kst_to_utc(2026, 5, 23, 10, 0)
        payload = get_market_state()
        assert payload["phase"] == MarketPhase.WEEKEND.value
        assert payload["is_open"] is False
        assert payload["is_closed"] is True
        assert "주말" in payload["label"]

    def test_sunday_returns_weekend(self, frozen_utc):
        # 2026-05-24 (일) 14:00 KST
        frozen_utc["value"] = _kst_to_utc(2026, 5, 24, 14, 0)
        payload = get_market_state()
        assert payload["phase"] == MarketPhase.WEEKEND.value
        assert payload["is_open"] is False

    # Edge case ───────────────────────────────────────────────────────────────

    def test_weekday_1530_kst_boundary_is_closed(self, frozen_utc):
        # 15:30 KST 정각은 OPEN 이 아니라 CLOSED — backend market_clock 정책과 일치.
        frozen_utc["value"] = _kst_to_utc(2026, 5, 18, 15, 30)
        payload = get_market_state()
        assert payload["phase"] == MarketPhase.CLOSED.value

    def test_weekday_1529_kst_is_still_open(self, frozen_utc):
        # 15:29 KST 는 정규장 마지막 분.
        frozen_utc["value"] = _kst_to_utc(2026, 5, 18, 15, 29)
        payload = get_market_state()
        assert payload["phase"] == MarketPhase.OPEN.value
        assert payload["is_open"] is True

    # Payload contract ────────────────────────────────────────────────────────

    def test_payload_has_required_fields(self, frozen_utc):
        frozen_utc["value"] = _kst_to_utc(2026, 5, 18, 9, 30)
        payload = get_market_state()
        for key in (
            "phase", "is_open", "is_closed", "label", "reason",
            "kst_now", "kst_weekday", "market_open_kst", "market_close_kst",
        ):
            assert key in payload, f"missing key: {key}"
        assert payload["market_open_kst"] == "09:00"
        assert payload["market_close_kst"] == "15:30"

    def test_kst_now_uses_plus_nine_offset(self, frozen_utc):
        # 응답에 KST 시각이 들어가야 한다 — UTC 가 아님.
        frozen_utc["value"] = _kst_to_utc(2026, 5, 18, 12, 0)
        payload = get_market_state()
        assert "+09:00" in payload["kst_now"]

    def test_kst_weekday_is_zero_indexed_monday(self, frozen_utc):
        # 2026-05-18 = 월요일 → weekday=0
        frozen_utc["value"] = _kst_to_utc(2026, 5, 18, 12, 0)
        payload = get_market_state()
        assert payload["kst_weekday"] == 0

    # 절대 원칙: 응답에 어떤 secret / API key 도 들어가지 않는다 ───────────────

    def test_payload_contains_no_secret_strings(self, frozen_utc):
        frozen_utc["value"] = _kst_to_utc(2026, 5, 18, 9, 30)
        import json
        serialized = json.dumps(get_market_state()).lower()
        for needle in ("kis_app_key", "app_secret", "anthropic_api_key",
                       "telegram_bot_token", "sk-", "bearer "):
            assert needle not in serialized


class TestMarketStateRouteViaHttp:
    """TestClient 로 라우터 marshalling 까지 통과하는지 확인 (한 케이스만 — 위
    test 들이 다양한 시점 매트릭스를 이미 커버)."""

    def test_endpoint_returns_200(self, client):
        # `client` fixture 는 conftest.py 에서 제공.
        res = client.get("/api/market/state")
        assert res.status_code == 200
        body = res.json()
        assert body["phase"] in {p.value for p in MarketPhase}
        assert isinstance(body["is_open"], bool)
        assert isinstance(body["is_closed"], bool)
        assert body["is_open"] != body["is_closed"]
