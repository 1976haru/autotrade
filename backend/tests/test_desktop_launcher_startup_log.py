"""app_desktop_launcher startup readiness log markers + uvicorn config invariants.

fix/desktop-backend-startup-readiness: 사용자가 EXE 실행 후 frontend 가 "백엔드
연결 대기" 상태에서 멈춰 보일 때 desktop-backend.log 만 열면 어디까지 진행됐는지
확인할 수 있어야 한다. 본 테스트는 launcher 의 *순수 코드 경로* 만 검증 — 실제
uvicorn / DB / broker 를 띄우지 않는다.

⚠ launcher._setup_logging() 이 `logging.basicConfig(force=True)` 로 root
handler 를 재설정하기 때문에 pytest 의 caplog handler 가 제거된다. 본 테스트는
실제 운영 환경과 동일하게 `%APPDATA%/Autotrade/logs/backend-YYYYMMDD.log` 파일을
읽어 marker 를 검증한다 — 사용자가 실제로 보게 될 로그 내용.
"""

from __future__ import annotations

import datetime as _dt
import pathlib
import sys
from unittest.mock import patch

import pytest

_BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import app_desktop_launcher as launcher   # noqa: E402


def _read_launcher_log(tmp_path: pathlib.Path) -> str:
    """`%APPDATA%/Autotrade/logs/backend-YYYYMMDD.log` 내용을 반환.

    launcher 가 file handler 를 close 하지 않은 채 함수가 끝나면 Windows 상에서
    파일 lock 이 남을 수 있어, 본 helper 는 root handler 를 명시적으로 flush
    하고 close 한 다음 읽는다.
    """
    import logging as _logging
    for h in list(_logging.getLogger().handlers):
        try:
            h.flush()
        except Exception:  # noqa: BLE001
            pass
        if isinstance(h, _logging.FileHandler):
            try:
                h.close()
            except Exception:  # noqa: BLE001
                pass
    stamp = _dt.datetime.now().strftime("%Y%m%d")
    log_path = tmp_path / "Autotrade" / "logs" / f"backend-{stamp}.log"
    if not log_path.is_file():
        return ""
    return log_path.read_text(encoding="utf-8", errors="replace")


def _make_fake_uvicorn(fake_run):
    fake_uvicorn = type("U", (), {})()
    fake_uvicorn.run = fake_run
    return fake_uvicorn


def test_run_logs_uvicorn_starting_marker(tmp_path, monkeypatch):
    """run() 은 uvicorn.run 직전에 명시 marker 를 emit — 사용자가 launcher 로그
    에서 "여기까지 진행" 을 즉시 파악할 수 있다."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    fake_uvicorn = _make_fake_uvicorn(lambda *a, **kw: None)
    with patch.object(launcher, "is_port_open", return_value=False), \
         patch.dict(sys.modules, {"uvicorn": fake_uvicorn}):
        rc = launcher.run([])
    assert rc == 0
    log_text = _read_launcher_log(tmp_path)
    assert "uvicorn.run starting" in log_text, f"missing marker in log:\n{log_text}"
    # 첫 실행 안내가 포함되어 운영자가 alembic 지연을 예상 가능.
    assert "1~2분" in log_text or "1-2분" in log_text


def test_run_passes_log_config_none_to_uvicorn(tmp_path, monkeypatch):
    """uvicorn 의 자체 dictConfig 호출을 건너뛰어 launcher 의 root logger
    handler (FileHandler + StreamHandler) 가 유지되어야 한다 — uvicorn 의
    "Started server process" / "Application startup complete" 가 같은
    desktop-backend.log 에 기록됨."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    fake_calls: dict[str, dict] = {"kwargs": {}}
    def fake_run(*args, **kwargs):  # noqa: ARG001
        fake_calls["kwargs"] = kwargs
        return None
    fake_uvicorn = _make_fake_uvicorn(fake_run)
    with patch.object(launcher, "is_port_open", return_value=False), \
         patch.dict(sys.modules, {"uvicorn": fake_uvicorn}):
        launcher.run([])
    assert "log_config" in fake_calls["kwargs"]
    assert fake_calls["kwargs"]["log_config"] is None, \
        "uvicorn.run must receive log_config=None so root logger handlers persist"


def test_run_logs_uvicorn_returned_normally(tmp_path, monkeypatch):
    """정상 종료 경로에 명시 marker — 운영자가 "uvicorn 이 의도적으로 종료" 인지
    "crash" 인지 구분 가능."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    fake_uvicorn = _make_fake_uvicorn(lambda *a, **kw: None)
    with patch.object(launcher, "is_port_open", return_value=False), \
         patch.dict(sys.modules, {"uvicorn": fake_uvicorn}):
        rc = launcher.run([])
    assert rc == 0
    log_text = _read_launcher_log(tmp_path)
    assert "uvicorn returned normally" in log_text, f"missing marker in log:\n{log_text}"


def test_run_logs_full_stack_on_uvicorn_exception(tmp_path, monkeypatch):
    """uvicorn / lifespan 예외 시 stack trace 전체가 log 에 — migration 실패
    원인을 사용자가 확인 가능."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    def fake_run(*a, **kw):  # noqa: ARG001
        raise RuntimeError("simulated alembic failure during startup")
    fake_uvicorn = _make_fake_uvicorn(fake_run)
    with patch.object(launcher, "is_port_open", return_value=False), \
         patch.dict(sys.modules, {"uvicorn": fake_uvicorn}):
        rc = launcher.run([])
    assert rc == 3
    log_text = _read_launcher_log(tmp_path)
    assert "simulated alembic failure" in log_text
    # log.exception 은 traceback 을 포함한 multi-line 출력 — "Traceback" 또는
    # 예외 모듈 경로가 포함된다.
    assert "Traceback" in log_text or "RuntimeError" in log_text, \
        f"uvicorn exception path must include traceback details:\n{log_text}"


def test_run_does_not_log_secrets_to_file(tmp_path, monkeypatch):
    """launcher 로그 파일에 API key / secret / 계좌번호 *값 원문* 0건."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    fake_uvicorn = _make_fake_uvicorn(lambda *a, **kw: None)
    # secret 키들이 process env 에 있어도 *값* 은 log 에 안 들어가야 함.
    monkeypatch.setenv("KIS_APP_KEY", "FAKE-APP-KEY-DO-NOT-LOG-12345")
    monkeypatch.setenv("KIS_APP_SECRET", "FAKE-APP-SECRET-DO-NOT-LOG-67890")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "FAKE-ANTHROPIC-KEY-NO-LOG-XYZ")
    with patch.object(launcher, "is_port_open", return_value=False), \
         patch.dict(sys.modules, {"uvicorn": fake_uvicorn}):
        launcher.run([])
    log_text = _read_launcher_log(tmp_path)
    assert "FAKE-APP-KEY-DO-NOT-LOG-12345" not in log_text
    assert "FAKE-APP-SECRET-DO-NOT-LOG-67890" not in log_text
    assert "FAKE-ANTHROPIC-KEY-NO-LOG-XYZ" not in log_text
