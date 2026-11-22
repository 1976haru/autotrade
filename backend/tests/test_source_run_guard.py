"""소스 실행 가드(4918dac 에서 이식) — 소스 backend 가 설치 앱의 운영 KIS 인증정보로 시작하지 못한다.

위험 경로: 이 작업폴더(소스)의 app_desktop_launcher.py / `uvicorn app.main:app` 이
%APPDATA%\\Autotrade\\.env(운영 계좌)를 쓰면 소스 DB(긴급정지 OFF)로 운영 계좌에 주문할 수 있었다.
실제 backend·uvicorn·KIS 실행 0 — 임시 APPDATA/CWD + fake uvicorn.
"""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import patch

import pytest

_BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import app_desktop_launcher as launcher  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.system import source_run_guard as guard  # noqa: E402

OPS_KEY = "PS" + "o" * 34
OPS_SECRET = "O" * 180
OPS_ACCT = "4444555501"
DEV_KEY = "PS" + "d" * 34
AUTO_FLAGS = ("ENABLE_KIS_PAPER_AUTO_TRADING=true\nENABLE_AI_PAPER_BACKGROUND_TICK=true\n"
              "MARKET_DATA_PROVIDER=kis\nKIS_PAPER_AUTO_ORDER_DRY_RUN=false\nENABLE_FILL_POLLING=true\n"
              "DATABASE_URL=sqlite:///./data/auto_trader.db\nKIS_IS_PAPER=true\nENABLE_LIVE_TRADING=false\n")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """APPDATA = 설치 앱 운영 .env(신규 계좌), CWD = 소스 backend(.env 에 자동매매 플래그 ON)."""
    appdata = tmp_path / "Roaming"
    (appdata / "Autotrade").mkdir(parents=True)
    (appdata / "Autotrade" / ".env").write_text(
        f"KIS_APP_KEY={OPS_KEY}\nKIS_APP_SECRET={OPS_SECRET}\nKIS_ACCOUNT_NO={OPS_ACCT}\n"
        "KIS_IS_PAPER=true\nENABLE_LIVE_TRADING=false\nDEFAULT_MODE=PAPER\n", encoding="utf-8")
    (appdata / "Autotrade" / "backend-port.json").write_text('{"port": 8000, "instance_id": "exe"}', encoding="utf-8")
    src = tmp_path / "backend"
    src.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.chdir(src)
    monkeypatch.delattr(sys, "frozen", raising=False)
    for k in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO", "DATABASE_URL",
              "ENABLE_KIS_PAPER_AUTO_TRADING", "MARKET_DATA_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    get_settings.cache_clear()
    yield src, appdata / "Autotrade"
    get_settings.cache_clear()


def _run_launcher():
    calls = {"uvicorn": 0, "find_port": 0}

    def fake_run(*a, **k):
        calls["uvicorn"] += 1
    fake_uvicorn = type("U", (), {"run": staticmethod(fake_run)})()
    real_find = launcher.find_free_port

    def spy_find(*a, **k):
        calls["find_port"] += 1
        return real_find(*a, **k)
    with patch.object(launcher, "is_port_open", return_value=False), \
         patch.object(launcher, "find_free_port", side_effect=spy_find), \
         patch.dict(sys.modules, {"uvicorn": fake_uvicorn}):
        rc = launcher.run([])
    return rc, calls


# ── 직접 런처 실행 ──────────────────────────────────────────────────────────

def test_source_launcher_no_longer_loads_ops_env(env):
    """운영 .env 는 더 이상 자동 로드되지 않는다 — 분리된 개발 설정(Mock)은 정상 기동."""
    src, ops_dir = env
    before = (ops_dir / "backend-port.json").read_bytes()
    (src / ".env").write_text(AUTO_FLAGS + f"KIS_APP_KEY={DEV_KEY}\nKIS_ACCOUNT_NO=1111222201\n", encoding="utf-8")
    rc, calls = _run_launcher()
    assert rc == 0 and calls["uvicorn"] == 1
    s = get_settings()
    assert s.kis_app_key == DEV_KEY and s.kis_account_no != OPS_ACCT
    assert (ops_dir / "backend-port.json").read_bytes() == before        # 설치 앱 상태 파일 무변경


@pytest.mark.parametrize("line", [f"KIS_APP_KEY={OPS_KEY}\n", f"KIS_ACCOUNT_NO={OPS_ACCT[:8]}-01\n"])
def test_source_launcher_refuses_ops_credentials_before_ports_and_state_file(env, line):
    src, ops_dir = env
    before = (ops_dir / "backend-port.json").read_bytes()
    (src / ".env").write_text(AUTO_FLAGS + line, encoding="utf-8")
    rc, calls = _run_launcher()
    assert rc == launcher.EXIT_SOURCE_USES_OPS_CREDENTIALS == 7
    assert calls == {"uvicorn": 0, "find_port": 0}
    assert (ops_dir / "backend-port.json").read_bytes() == before


def test_source_launcher_refuses_ops_key_injected_via_process_env(env, monkeypatch):
    src, _ = env
    (src / ".env").write_text(AUTO_FLAGS, encoding="utf-8")
    monkeypatch.setenv("KIS_APP_KEY", OPS_KEY)
    rc, calls = _run_launcher()
    assert rc == 7 and calls["uvicorn"] == 0


def test_refusal_reason_names_fields_only(env, monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", OPS_KEY)
    monkeypatch.setenv("KIS_ACCOUNT_NO", OPS_ACCT)
    reason = guard.source_run_block_reason()
    assert reason.startswith("SOURCE_RUN_USES_OPS_CREDENTIALS: KIS_APP_KEY, KIS_ACCOUNT_NO")
    assert OPS_KEY not in reason and OPS_ACCT[:8] not in reason and OPS_SECRET[:20] not in reason


def test_identity_check_failure_is_fail_closed(env, monkeypatch):
    def boom(_s):
        raise OSError("unreadable ops .env")
    monkeypatch.setattr(guard, "ops_credential_overlap", boom)
    assert guard.source_run_block_reason().startswith("SOURCE_OPS_CHECK_FAILED")
    rc, calls = _run_launcher()
    assert rc == 7 and calls["uvicorn"] == 0


def test_installed_app_is_not_affected(env, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("KIS_APP_KEY", OPS_KEY)
    assert guard.source_run_block_reason() is None
    assert launcher.candidate_env_paths()[0] == env[1] / ".env"


def test_no_ops_env_on_machine_allows_source_run(env):
    (env[1] / ".env").unlink()
    assert guard.source_run_block_reason() is None


# ── uvicorn 직접 실행 — lifespan 가드: DB·주문 라우터·API 전부 열리기 전에 거부 ──

def test_lifespan_refuses_ops_credentials_and_no_api_is_served(env, monkeypatch):
    from fastapi.testclient import TestClient

    import app.risk.emergency_stop as es
    from app.main import app
    monkeypatch.setenv("KIS_ACCOUNT_NO", OPS_ACCT)
    get_settings.cache_clear()
    touched = []
    monkeypatch.setattr(es, "restore_kill_switch_from_db", lambda *a, **k: touched.append("db"))
    served = []
    with pytest.raises(RuntimeError, match="SOURCE_RUN_USES_OPS_CREDENTIALS"):
        with TestClient(app) as tc:
            served.append(tc.post("/api/broker/orders", json={"symbol": "005930", "side": "BUY",
                                                              "quantity": 1, "order_type": "MARKET"}).status_code)
    assert served == [] and touched == []        # 수동 주문 API 미개방, 긴급정지 복원(DB) 이전에 중단


def test_lifespan_allows_separated_mock_environment(env, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    (env[0] / "data").mkdir()                    # 임시 소스 폴더의 상대 DB 경로(./data) 준비
    monkeypatch.setenv("KIS_APP_KEY", DEV_KEY)
    monkeypatch.setenv("KIS_ACCOUNT_NO", "1111222201")
    get_settings.cache_clear()
    with TestClient(app) as tc:
        assert tc.get("/health").status_code == 200
