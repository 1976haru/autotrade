"""0-04: EXE용 .env 기본값 + KIS 모의 자동매매 상태 노출 테스트.

검증:
 - backend/.env.example 의 EXE 기본값(PAPER + KIS 모의 auto ON, LIVE/AI/FUTURES
   OFF, KIS_IS_PAPER true, secret blank)
 - /api/kis-paper/auto/status 가 credentials_present(boolean) + is_live_authorization
   false + broker_order_type=KIS_PAPER + default_mode + paper_broker_kind 노출,
   secret 값 0건
 - KIS readiness 가 credentials 를 *_present boolean 으로만 노출
 - 정적 grep: broker/OrderExecutor live path 호출 0건
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

_ENV = Path(__file__).resolve().parents[1] / ".env.example"


def _pairs() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in _ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


# ─────────────────────────────────────────────────────────────────────────────
# .env.example EXE 기본값
# ─────────────────────────────────────────────────────────────────────────────


class TestEnvExampleDefaults:
    def test_default_mode_paper(self):
        assert _pairs().get("DEFAULT_MODE") == "PAPER"

    def test_live_flags_false(self):
        p = _pairs()
        assert p.get("ENABLE_LIVE_TRADING") == "false"
        assert p.get("ENABLE_AI_EXECUTION") == "false"
        assert p.get("ENABLE_FUTURES_LIVE_TRADING") == "false"

    def test_kis_is_paper_true(self):
        assert _pairs().get("KIS_IS_PAPER") == "true"

    def test_paper_broker_kind_kis_paper(self):
        assert _pairs().get("PAPER_BROKER_KIND") == "KIS_PAPER"

    def test_ai_paper_background_tick_on(self):
        p = _pairs()
        assert p.get("ENABLE_AI_PAPER_BACKGROUND_TICK") == "true"
        assert p.get("AI_PAPER_TICK_DRY_RUN") == "false"

    def test_kis_paper_auto_on_and_not_dry_run(self):
        p = _pairs()
        assert p.get("ENABLE_KIS_PAPER_AUTO_TRADING") == "true"
        assert p.get("KIS_PAPER_AUTO_ORDER_DRY_RUN") == "false"

    def test_kis_paper_fill_polling_on(self):
        assert _pairs().get("KIS_PAPER_FILL_POLLING") == "true"

    def test_secrets_blank(self):
        p = _pairs()
        for k in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO",
                  "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN"):
            assert p.get(k, "") == "", f".env.example {k} must be blank"

    def test_no_secret_like_value_in_env_example(self):
        text = _ENV.read_text(encoding="utf-8")
        for pat in (r"sk-[A-Za-z0-9]{20,}", r"sk-ant-[A-Za-z0-9_\-]{20,}",
                    r"\b\d{6,}-\d{2,}\b"):
            assert not re.search(pat, text), f".env.example secret 의심: /{pat}/"

    def test_risk_limits_documented(self):
        p = _pairs()
        for k in ("RISK_MAX_ORDER_NOTIONAL", "RISK_MAX_DAILY_LOSS",
                  "RISK_MAX_POSITIONS", "RISK_MAX_SYMBOL_EXPOSURE"):
            assert k in p, f".env.example risk limit {k} 누락"


# ─────────────────────────────────────────────────────────────────────────────
# /api/kis-paper/auto/status — 상태 노출 (secret 0건)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


class TestAutoStatusApi:
    def test_status_fields_present(self, client):
        b = client.get("/api/kis-paper/auto/status").json()
        for k in ("enable_kis_paper_auto_trading", "dry_run", "fill_polling",
                  "kis_is_paper", "enable_live_trading", "credentials_present",
                  "is_live_authorization", "broker_order_type",
                  "default_mode", "paper_broker_kind"):
            assert k in b, f"auto/status missing field: {k}"

    def test_is_live_authorization_false(self, client):
        assert client.get("/api/kis-paper/auto/status").json()["is_live_authorization"] is False

    def test_broker_order_type_kis_paper(self, client):
        assert client.get("/api/kis-paper/auto/status").json()["broker_order_type"] == "KIS_PAPER"

    def test_credentials_present_is_boolean(self, client):
        assert isinstance(
            client.get("/api/kis-paper/auto/status").json()["credentials_present"], bool)

    def test_no_secret_or_account_in_response(self, client):
        raw = client.get("/api/kis-paper/auto/status").text.lower()
        for banned in ("kis_app_key", "kis_app_secret", "app_secret",
                       "account_no", "kis_account_no", "access_token"):
            assert banned not in raw

    def test_readiness_credentials_present_only_no_secret(self, client):
        raw = client.get("/api/kis-paper/readiness").text
        low = raw.lower()
        # boolean *_present 만 노출.
        assert "_present" in low
        # 실제 secret 패턴 0건.
        for pat in (r"sk-[A-Za-z0-9]{20,}", r"\b\d{6,}-\d{2,}\b"):
            assert not re.search(pat, raw), f"readiness secret 노출 의심: /{pat}/"


# ─────────────────────────────────────────────────────────────────────────────
# static guards
# ─────────────────────────────────────────────────────────────────────────────


class TestStaticGuards:
    def test_routes_kis_paper_no_live_order_path(self):
        path = (Path(__file__).resolve().parents[1]
                / "app" / "api" / "routes_kis_paper.py")
        text = path.read_text(encoding="utf-8")
        for pat in (r"\bbroker\.place_order\s*\(\s*[^)]*is_paper\s*=\s*False",
                    r"OrderExecutor\s*\(.*live"):
            assert not re.search(pat, text), f"routes_kis_paper live path 의심: /{pat}/"

    def test_env_example_no_live_flag_true(self):
        text = _ENV.read_text(encoding="utf-8")
        for pat in (r"ENABLE_LIVE_TRADING\s*=\s*true",
                    r"ENABLE_AI_EXECUTION\s*=\s*true",
                    r"ENABLE_FUTURES_LIVE_TRADING\s*=\s*true",
                    r"KIS_IS_PAPER\s*=\s*false"):
            assert not re.search(pat, text), f".env.example 위험 기본값: /{pat}/"
