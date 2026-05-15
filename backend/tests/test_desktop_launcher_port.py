"""app_desktop_launcher port fallback 단위 테스트.

CLAUDE.md invariant:
- broker / OrderExecutor / route_order import 0건 (이미 launcher 가 그 원칙)
- Secret 노출 0건
- 실거래 호출 0건

본 테스트는 *동작 분기*만 검증:
- port free → ok=True, mode="free"
- port in-use + health OK → ok=True, mode="reuse-backend"
- port in-use + health FAIL → 다음 candidate 시도
- 모든 candidate in-use + health FAIL → ok=False
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

import app_desktop_launcher as launcher


@pytest.fixture
def log() -> logging.Logger:
    return logging.getLogger("test_launcher_port")


# ----------------------------------------------------------------------
# find_free_port
# ----------------------------------------------------------------------


class TestFindFreePort:
    def test_first_candidate_free(self, log):
        with patch.object(launcher, "is_port_open", return_value=False):
            port, ok, reason = launcher.find_free_port(
                "127.0.0.1", [8000, 8001, 8002], log
            )
        assert ok is True
        assert port == 8000
        assert reason == "free"

    def test_port_in_use_but_health_ok_reuse(self, log):
        """8000 in-use 인데 /health 응답 → reuse-backend, 같은 port 반환."""
        with patch.object(launcher, "is_port_open", return_value=True), \
             patch.object(launcher, "is_backend_alive", return_value=True):
            port, ok, reason = launcher.find_free_port(
                "127.0.0.1", [8000, 8001, 8002], log
            )
        assert ok is True
        assert port == 8000
        assert reason == "reuse-backend"

    def test_port_in_use_health_fail_fallback_to_next(self, log):
        """8000 in-use + /health 실패 → 8001 free 면 8001 반환."""
        is_open_calls = {"count": 0}

        def fake_is_port_open(host, port, timeout=0.5):  # noqa: ARG001
            # 8000 → in-use, 8001 → free
            return port == 8000

        with patch.object(launcher, "is_port_open", side_effect=fake_is_port_open), \
             patch.object(launcher, "is_backend_alive", return_value=False):
            port, ok, reason = launcher.find_free_port(
                "127.0.0.1", [8000, 8001, 8002], log
            )
        assert ok is True
        assert port == 8001
        assert reason == "free"

    def test_all_in_use_health_fail_returns_not_ok(self, log):
        """8000/8001/8002 모두 in-use + /health 실패 → ok=False."""
        with patch.object(launcher, "is_port_open", return_value=True), \
             patch.object(launcher, "is_backend_alive", return_value=False):
            port, ok, reason = launcher.find_free_port(
                "127.0.0.1", [8000, 8001, 8002], log
            )
        assert ok is False
        assert "in use" in reason.lower() or "all" in reason.lower()

    def test_empty_candidates_returns_default(self, log):
        port, ok, reason = launcher.find_free_port("127.0.0.1", [], log)
        assert ok is False
        assert port == launcher.DEFAULT_PORT


# ----------------------------------------------------------------------
# is_backend_alive
# ----------------------------------------------------------------------


class TestIsBackendAlive:
    def test_returns_false_on_urlerror(self):
        """포트 open 인데 우리 backend 가 아니면 ConnectionError → False."""
        from urllib.error import URLError

        with patch("app_desktop_launcher.urlopen", side_effect=URLError("nope")):
            assert launcher.is_backend_alive("127.0.0.1", 8000) is False

    def test_returns_true_on_200_with_ok_body(self):
        class _Resp:
            status = 200
            def read(self):
                return b'{"ok": true, "status": "ok"}'
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        with patch("app_desktop_launcher.urlopen", return_value=_Resp()):
            assert launcher.is_backend_alive("127.0.0.1", 8000) is True

    def test_returns_false_on_non_200(self):
        class _Resp:
            status = 500
            def read(self):
                return b'{}'
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        with patch("app_desktop_launcher.urlopen", return_value=_Resp()):
            assert launcher.is_backend_alive("127.0.0.1", 8000) is False

    def test_returns_false_on_garbage_body(self):
        """200 이지만 body 가 우리 health response 가 아니면 False."""
        class _Resp:
            status = 200
            def read(self):
                return b"<html>not our backend</html>"
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        with patch("app_desktop_launcher.urlopen", return_value=_Resp()):
            assert launcher.is_backend_alive("127.0.0.1", 8000) is False


# ----------------------------------------------------------------------
# write_backend_port_file
# ----------------------------------------------------------------------


class TestWriteBackendPortFile:
    def test_writes_json_with_no_secrets(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APPDATA", str(tmp_path))
        out = launcher.write_backend_port_file("127.0.0.1", 8001, "free")
        assert out is not None
        assert out.is_file()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["host"] == "127.0.0.1"
        assert data["port"] == 8001
        assert data["mode"] == "free"
        assert "written_at" in data
        # Secret 의심 키 0건.
        text = out.read_text(encoding="utf-8").lower()
        for forbidden in (
            "api_key", "secret", "password", "token",
            "kis_app_key", "anthropic", "openai", "telegram",
        ):
            assert forbidden not in text

    def test_returns_none_when_appdata_missing(self, monkeypatch):
        monkeypatch.delenv("APPDATA", raising=False)
        out = launcher.write_backend_port_file("127.0.0.1", 8000, "free")
        assert out is None


# ----------------------------------------------------------------------
# 정적 import 가드
# ----------------------------------------------------------------------


class TestStaticImportGuards:
    def _src(self) -> str:
        path = Path(launcher.__file__)
        return path.read_text(encoding="utf-8")

    def test_no_broker_imports(self):
        """*실제* import 문만 검사 — docstring/주석 내 정책 문구는 허용."""
        src = self._src()
        # 줄 시작 import 패턴만 잡기 (comments / docstrings 제외).
        import re
        for forbidden_pattern in (
            r"^\s*from\s+app\.brokers",
            r"^\s*from\s+app\.execution\.executor",
            r"^\s*from\s+app\.execution\.order_router",
            r"^\s*import\s+app\.brokers",
        ):
            assert not re.search(forbidden_pattern, src, re.MULTILINE), (
                f"forbidden import pattern matched: {forbidden_pattern}"
            )

    def test_no_order_calls(self):
        src = self._src()
        for forbidden in (
            "broker.place_order(", "route_order(", ".place_order(",
            ".cancel_order(",
        ):
            assert forbidden not in src

    def test_no_external_ai_imports(self):
        src = self._src()
        for forbidden in (
            "import anthropic", "from anthropic",
            "import openai", "from openai",
        ):
            assert forbidden not in src
