"""#57 / 7-05 — `GET /api/system/build-info` 정합성 + 안전 invariant 테스트.

backend sidecar 의 build metadata(app version / channel / git commit / branch /
build time / dirty)를 read-only 로 반환하는 endpoint.

검증:
- 표준 필드 존재 + 타입
- contains_secret=False / is_live_authorization=False 불변
- Secret / 계좌번호 / API key 원문 0건
- broker.place_order 호출 0건 (read-only)
- 환경변수 override 동작
- exe-status 무회귀
"""

from __future__ import annotations

import json

import app.api.routes_system as routes_system

_REQUIRED = (
    "version", "channel", "commit", "commit_full", "branch",
    "build_time", "source", "is_dirty",
    "is_live_authorization", "contains_secret",
)


def test_build_info_returns_required_fields(client):
    r = client.get("/api/system/build-info")
    assert r.status_code == 200
    body = r.json()
    for f in _REQUIRED:
        assert f in body, f"missing field: {f}"


def test_build_info_field_types(client):
    body = client.get("/api/system/build-info").json()
    for k in ("version", "channel", "commit", "commit_full", "branch",
              "build_time", "source"):
        assert isinstance(body[k], str)
    assert isinstance(body["is_dirty"], bool)


def test_build_info_safety_invariants(client):
    body = client.get("/api/system/build-info").json()
    assert body["is_live_authorization"] is False
    assert body["contains_secret"] is False


def test_build_info_no_secret_strings(client, monkeypatch):
    # 환경에 진짜처럼 보이는 secret 을 깔아둬도 build-info 응답엔 절대 안 나온다
    # (build_info 는 commit/branch/time 만 읽고 secret env 는 건드리지 않음).
    monkeypatch.setenv("KIS_APP_SECRET", "REALSECRET_should_not_appear_123")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "98765432-11")
    text = json.dumps(client.get("/api/system/build-info").json(), ensure_ascii=False)
    assert "REALSECRET_should_not_appear_123" not in text
    assert "98765432" not in text
    for forbidden in ("app_secret", "api_key", "access_token", "Bearer "):
        assert forbidden not in text


def test_build_info_no_broker_order(client):
    client.get("/api/system/build-info")
    assert len(client.test_broker.orders) == 0


def test_build_info_env_override(client, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_GIT_COMMIT", "deadbeef")
    monkeypatch.setenv("AUTOTRADE_GIT_BRANCH", "release-x")
    monkeypatch.setenv("AUTOTRADE_BUILD_CHANNEL", "paper-beta")
    monkeypatch.setenv("AUTOTRADE_APP_VERSION", "9.9.9")
    body = client.get("/api/system/build-info").json()
    assert body["commit"] == "deadbeef"
    assert body["branch"] == "release-x"
    assert body["channel"] == "paper-beta"
    assert body["version"] == "9.9.9"


def test_build_info_dirty_env_override(client, monkeypatch):
    monkeypatch.setenv("AUTOTRADE_GIT_DIRTY", "true")
    assert client.get("/api/system/build-info").json()["is_dirty"] is True
    monkeypatch.setenv("AUTOTRADE_GIT_DIRTY", "false")
    assert client.get("/api/system/build-info").json()["is_dirty"] is False


def test_build_info_module_get_build_info_no_exception(monkeypatch):
    # git / env / stamp 모두 없더라도 예외 없이 unknown fallback.
    from app.system.build_info import get_build_info, _UNKNOWN
    # git 명령을 강제로 실패시켜 fallback 경로 확인.
    monkeypatch.setattr(routes_system, "get_settings", routes_system.get_settings)
    import app.system.build_info as bi
    monkeypatch.setattr(bi, "_git", lambda *a: None)
    monkeypatch.setattr(bi, "_load_stamp", lambda: {})
    for var in ("AUTOTRADE_GIT_COMMIT", "AUTOTRADE_GIT_COMMIT_FULL",
                "AUTOTRADE_GIT_BRANCH", "AUTOTRADE_BUILD_TIME",
                "AUTOTRADE_APP_VERSION", "AUTOTRADE_GIT_DIRTY"):
        monkeypatch.delenv(var, raising=False)
    info = get_build_info()
    assert info["commit"] == _UNKNOWN
    assert info["version"] == _UNKNOWN
    assert info["is_dirty"] is False
    # channel / source 는 의미있는 default 로 채워짐.
    assert info["channel"] == "paper-beta"
    assert info["source"] in ("local-build", "github-actions")


def test_exe_status_no_regression(client):
    r = client.get("/api/system/exe-status")
    assert r.status_code == 200
    body = r.json()
    assert body["is_live_authorization"] is False
    assert body["contains_secret"] is False
