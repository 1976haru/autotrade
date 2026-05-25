"""0-05: Secret / API key / account_no 노출 가드 — 통합 기준선 테스트.

EXE / UI / 로그 / API 응답 어디에도 민감정보가 *원문* 으로 노출되지 않는지,
그리고 redaction / `*_present` boolean / settings 저장 거부 가드가 유지되는지
단일 파일에서 lock 한다.

본 테스트는 **실제 secret 을 포함하지 않는다** — 가짜(`sk-FAKE...`) 패턴으로
가드 *동작* 만 검증하며, API 응답에서는 *값 패턴* (sk-/계좌번호)이 0건임을
확인한다. credential 노출은 boolean `*_present` 만 허용.
"""

from __future__ import annotations

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

_ENV = Path(__file__).resolve().parents[1] / ".env.example"

# 응답 raw 에 등장하면 *실패* 인 실제 secret/계좌 VALUE 패턴 (key 이름 아님).
_SECRET_VALUE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(r"\b\d{6,}-\d{2,}\b"),     # 한국 계좌번호 형태
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\."),   # JWT
)


def _assert_no_secret_values(raw: str, where: str) -> None:
    for rx in _SECRET_VALUE_PATTERNS:
        m = rx.search(raw)
        assert m is None, f"{where}: secret VALUE 노출 의심 → {m.group()[:12]}…"


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TS = sessionmaker(bind=engine, autoflush=False, autocommit=False,
                      expire_on_commit=False)

    def odb():
        s = TS()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = odb
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


# ─────────────────────────────────────────────────────────────────────────────
# 1. .env.example
# ─────────────────────────────────────────────────────────────────────────────


class TestEnvExample:
    def test_secret_fields_blank(self):
        text = _ENV.read_text(encoding="utf-8")
        for k in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO",
                  "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN",
                  "KIWOOM_APP_KEY", "KIWOOM_APP_SECRET", "KIWOOM_ACCOUNT_NO"):
            m = re.search(rf"^{k}=(.*)$", text, re.MULTILINE)
            if m:
                assert m.group(1).strip() == "", f".env.example {k} 비어있지 않음"

    def test_no_secret_value_patterns(self):
        _assert_no_secret_values(_ENV.read_text(encoding="utf-8"), ".env.example")


# ─────────────────────────────────────────────────────────────────────────────
# 2. API 응답 — secret VALUE 0건 + credential 은 boolean only
# ─────────────────────────────────────────────────────────────────────────────


_READ_ENDPOINTS = [
    "/api/kis-paper/auto/status",
    "/api/kis-paper/readiness",
    "/api/auto-paper/capital-config",
    "/api/auto-paper/paper-capital-settings",
    "/api/auto-paper/portfolio",
    "/api/auto-paper/blocked-reasons/today",
    "/api/status",
]


class TestApiResponses:
    @pytest.mark.parametrize("ep", _READ_ENDPOINTS)
    def test_no_secret_values(self, client, ep):
        r = client.get(ep)
        assert r.status_code == 200
        _assert_no_secret_values(r.text, ep)

    def test_credential_fields_boolean_only(self, client):
        """readiness / auto-status 의 credential 노출은 boolean (`*_present`)만."""
        def _walk(o, p=""):
            if isinstance(o, dict):
                for k, v in o.items():
                    yield from _walk(v, f"{p}.{k}")
            elif isinstance(o, list):
                for i, v in enumerate(o):
                    yield from _walk(v, f"{p}[{i}]")
            else:
                yield (p.lower(), o)

        # `missing_credentials` 는 *어떤 자격이 비어 있는지* 알려주는 필드명
        # 라벨 목록(예: "KIS_APP_KEY")이며 secret VALUE 가 아니다 — 노출 안전.
        # 그 외 credential 계열 필드는 반드시 boolean presence flag(`*_present`).
        safe_credential_labels = {
            "KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO",
            "KIWOOM_APP_KEY", "KIWOOM_APP_SECRET", "KIWOOM_ACCOUNT_NO",
        }
        for ep in ("/api/kis-paper/readiness", "/api/kis-paper/auto/status"):
            for key, val in _walk(client.get(ep).json()):
                if "missing_credential" in key:
                    assert isinstance(val, str) and val in safe_credential_labels, (
                        f"{ep}: missing_credentials 는 알려진 필드명 라벨만 허용 "
                        f"({val!r})"
                    )
                    continue
                if any(s in key for s in ("app_key", "app_secret", "account_no",
                                          "secret", "credential")):
                    assert isinstance(val, bool), (
                        f"{ep}: credential 필드 {key} 가 boolean 이 아님 ({val!r})"
                    )

    def test_auto_status_invariants(self, client):
        b = client.get("/api/kis-paper/auto/status").json()
        assert b["is_live_authorization"] is False
        assert isinstance(b["credentials_present"], bool)
        assert b["broker_order_type"] == "KIS_PAPER"


# ─────────────────────────────────────────────────────────────────────────────
# 3. settings 저장 secret 거부 + sanitize 가드 (가짜 secret 으로 동작 검증)
# ─────────────────────────────────────────────────────────────────────────────


class TestRedactionGuards:
    def test_persistent_settings_rejects_secret_key(self):
        from app.settings.persistent_settings import (
            SecretInSettingsError,
            scan_for_secrets,
        )
        with pytest.raises(SecretInSettingsError):
            scan_for_secrets({"api_key": "anything", "max_positions": 5})
        with pytest.raises(SecretInSettingsError):
            scan_for_secrets({"note": "sk-FAKEAAAAAAAAAAAAAAAAAAAA"})

    def test_paper_capital_save_api_rejects_secret(self, client):
        r = client.post(
            "/api/auto-paper/paper-capital-settings",
            json={"kis_app_secret": "sk-FAKEAAAAAAAAAAAAAAAAAAAA", "max_positions": 5},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "secret_in_settings_blocked"

    def test_agent_memory_sanitize_blocks_secret(self):
        from app.agents.agent_memory import SecretLeakError, sanitize_text
        with pytest.raises(SecretLeakError):
            sanitize_text("our key sk-ant-FAKEaaaaaaaaaaaaaaaaaaaa")
        # 평범한 텍스트는 통과.
        assert sanitize_text("정상 메모입니다") == "정상 메모입니다"


# ─────────────────────────────────────────────────────────────────────────────
# 4. security_scan + LIVE flag 불변
# ─────────────────────────────────────────────────────────────────────────────


class TestRepoSafety:
    def test_security_scan_clean(self):
        import os
        import subprocess
        import sys
        root = Path(__file__).resolve().parents[2]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.run(
            [sys.executable, str(root / "scripts" / "security_scan.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(root), env=env,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        # exit code 0 == clean; 출력에 HIGH : 0 도 확인 (이중 가드).
        assert proc.returncode == 0, out[-800:]
        assert "HIGH  : 0" in out, out[-800:]

    def test_env_example_live_flags_safe(self):
        text = _ENV.read_text(encoding="utf-8")
        for pat in (r"ENABLE_LIVE_TRADING\s*=\s*true",
                    r"ENABLE_AI_EXECUTION\s*=\s*true",
                    r"ENABLE_FUTURES_LIVE_TRADING\s*=\s*true",
                    r"KIS_IS_PAPER\s*=\s*false"):
            assert not re.search(pat, text), f".env.example 위험 기본값: /{pat}/"

    def test_only_env_examples_tracked(self):
        import subprocess
        root = Path(__file__).resolve().parents[2]
        out = subprocess.run(
            ["git", "ls-files"], capture_output=True, text=True, cwd=str(root),
        ).stdout
        env_files = [ln for ln in out.splitlines() if re.search(r"(^|/)\.env", ln)]
        for f in env_files:
            assert f.endswith(".example"), f"실제 .env 파일이 추적됨: {f}"
