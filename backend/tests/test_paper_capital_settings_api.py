"""P-15: Paper 자금 설정 API — `/paper-capital-settings/validate` + `/start` body 확장.

검증:
 1. 기본 카드 입력 검증 echo
 2. partial 입력 허용
 3. total_paper_capital ge=1 enforced
 4. per_symbol_allocation ge=1 enforced
 5. max_positions 1~100 enforced
 6. max_daily_buy_amount ge=1 enforced
 7. max_symbol_weight_pct 0 < x <= 1 enforced
 8. allow_additional_buy default False
 9. is_live_authorization=False 영구
10. is_order_signal=False 영구
11. API key/secret/account 필드 0개 — body schema에 없음
12. 정적 grep: 실거래 안전 flag 변경 0건
13. /start body 가 capital_settings 받아도 422 발생 안 함 (추가 carry)
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app


_ROUTES_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "api" / "routes_auto_paper.py"
)


@pytest.fixture
def api_client():
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False, expire_on_commit=False,
    )

    def _override_db():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


class TestValidateEndpoint:
    def test_default_body_echoes_with_invariants(self, api_client):
        r = api_client.post(
            "/api/auto-paper/paper-capital-settings/validate",
            json={
                "total_paper_capital":   10_000_000,
                "per_symbol_allocation": 1_000_000,
                "max_positions":         5,
                "max_daily_buy_amount":  3_000_000,
                "max_symbol_weight_pct": 0.2,
                "allow_additional_buy":  False,
            },
        )
        assert r.status_code == 200
        b = r.json()
        assert b["total_paper_capital"] == 10_000_000
        assert b["per_symbol_allocation"] == 1_000_000
        assert b["max_positions"] == 5
        assert b["max_daily_buy_amount"] == 3_000_000
        assert b["max_symbol_weight_pct"] == 0.2
        assert b["allow_additional_buy"] is False
        # invariants.
        assert b["is_live_authorization"] is False
        assert b["is_order_signal"] is False
        assert b["is_paper_only"] is True
        assert "reason_message" in b
        # notice 는 한국어 안전 안내 — 권한 부여 아님 + 정책 변경 안 함.
        assert "권한" in b["notice"]
        assert "변경" in b["notice"]

    def test_partial_body_echoes_none(self, api_client):
        r = api_client.post(
            "/api/auto-paper/paper-capital-settings/validate",
            json={"max_positions": 8},
        )
        assert r.status_code == 200
        b = r.json()
        assert b["max_positions"] == 8
        assert b["total_paper_capital"] is None
        # allow_additional_buy None → False default carry.
        assert b["allow_additional_buy"] is False

    def test_total_paper_capital_zero_rejected(self, api_client):
        r = api_client.post(
            "/api/auto-paper/paper-capital-settings/validate",
            json={"total_paper_capital": 0},
        )
        assert r.status_code == 422

    def test_per_symbol_allocation_negative_rejected(self, api_client):
        r = api_client.post(
            "/api/auto-paper/paper-capital-settings/validate",
            json={"per_symbol_allocation": -1},
        )
        assert r.status_code == 422

    def test_max_positions_zero_rejected(self, api_client):
        r = api_client.post(
            "/api/auto-paper/paper-capital-settings/validate",
            json={"max_positions": 0},
        )
        assert r.status_code == 422

    def test_max_positions_over_100_rejected(self, api_client):
        r = api_client.post(
            "/api/auto-paper/paper-capital-settings/validate",
            json={"max_positions": 101},
        )
        assert r.status_code == 422

    def test_max_daily_buy_amount_zero_rejected(self, api_client):
        r = api_client.post(
            "/api/auto-paper/paper-capital-settings/validate",
            json={"max_daily_buy_amount": 0},
        )
        assert r.status_code == 422

    def test_max_symbol_weight_pct_zero_rejected(self, api_client):
        r = api_client.post(
            "/api/auto-paper/paper-capital-settings/validate",
            json={"max_symbol_weight_pct": 0},
        )
        assert r.status_code == 422

    def test_max_symbol_weight_pct_over_one_rejected(self, api_client):
        r = api_client.post(
            "/api/auto-paper/paper-capital-settings/validate",
            json={"max_symbol_weight_pct": 1.5},
        )
        assert r.status_code == 422

    def test_max_symbol_weight_pct_one_allowed(self, api_client):
        r = api_client.post(
            "/api/auto-paper/paper-capital-settings/validate",
            json={"max_symbol_weight_pct": 1.0},
        )
        assert r.status_code == 200

    def test_allow_additional_buy_default_false_when_omitted(self, api_client):
        r = api_client.post(
            "/api/auto-paper/paper-capital-settings/validate",
            json={},
        )
        b = r.json()
        assert b["allow_additional_buy"] is False


class TestStartBodyCarriesCapitalSettings:
    def test_start_accepts_capital_settings(self, api_client):
        # /start 가 capital_settings 받아도 422 발생 안 함.
        r = api_client.post(
            "/api/auto-paper/start",
            json={
                "risk_profile": "BALANCED",
                "capital_settings": {
                    "total_paper_capital":   30_000_000,
                    "per_symbol_allocation": 2_000_000,
                    "max_positions":         8,
                    "max_daily_buy_amount":  5_000_000,
                    "max_symbol_weight_pct": 0.10,
                    "allow_additional_buy":  True,
                },
            },
        )
        # status 200 또는 409 (이미 시작 중 등). 422 (validation) 가 아니어야.
        assert r.status_code != 422

    def test_start_accepts_capital_settings_alongside_pre_market(self, api_client):
        r = api_client.post(
            "/api/auto-paper/start",
            json={
                "risk_profile": "BALANCED",
                "pre_market": {
                    "start_allowed": True,
                    "verdict": "READY_TO_START",
                    "blocking_reasons": [],
                    "warnings": [],
                },
                "capital_settings": {
                    "total_paper_capital":   10_000_000,
                    "per_symbol_allocation": 1_000_000,
                    "max_positions":         5,
                    "max_daily_buy_amount":  3_000_000,
                    "max_symbol_weight_pct": 0.2,
                    "allow_additional_buy":  False,
                },
            },
        )
        assert r.status_code != 422


class TestStaticGuards:
    def test_route_no_safety_flag_mutations(self):
        text = _ROUTES_PATH.read_text(encoding="utf-8")
        for pat in [
            r"settings\.enable_live_trading\s*=",
            r"settings\.enable_ai_execution\s*=",
            r"settings\.enable_futures_live_trading\s*=",
            r"settings\.kis_is_paper\s*=",
        ]:
            assert not re.search(pat, text), (
                f"routes_auto_paper.py 안전 flag mutation 의심: /{pat}/"
            )

    def test_capital_settings_body_has_no_secret_fields(self):
        """`_PaperCapitalSettingsBody` 에 secret / api_key / account 필드 0개."""
        text = _ROUTES_PATH.read_text(encoding="utf-8")
        tree = ast.parse(text)

        target_class = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ClassDef)
                and node.name == "_PaperCapitalSettingsBody"
            ):
                target_class = node
                break
        assert target_class is not None, (
            "_PaperCapitalSettingsBody 클래스를 찾을 수 없습니다"
        )

        banned = {
            "api_key", "app_key", "app_secret", "secret",
            "account_no", "account_number", "kis_app_key",
            "anthropic_api_key", "openai_api_key", "telegram_bot_token",
        }
        for item in target_class.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                assert item.target.id not in banned, (
                    f"_PaperCapitalSettingsBody 에 금지 필드 '{item.target.id}'"
                )

    def test_validate_endpoint_does_not_call_broker(self):
        """validate endpoint 본문에서 broker 호출 금지."""
        text = _ROUTES_PATH.read_text(encoding="utf-8")
        # endpoint 함수 본체 격리.
        m = re.search(
            r"def validate_paper_capital_settings_endpoint\(.*?\)"
            r" -> dict:(.*?)def \w",
            text, re.DOTALL,
        )
        assert m is not None
        body = m.group(1)
        for pat in (
            r"\bbroker\.place_order\s*\(",
            r"\bbroker\.cancel_order\s*\(",
            r"\broute_order\s*\(",
            r"OrderExecutor\s*\(",
            r"set_paper_capital_config\s*\(",
            r"set_per_symbol_allocation\s*\(",
        ):
            assert not re.search(pat, body), (
                f"validate endpoint 본문에 금지 패턴: /{pat}/"
            )
