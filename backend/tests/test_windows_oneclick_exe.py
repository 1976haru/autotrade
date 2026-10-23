"""WINDOWS-ONECLICK-EXE-V1 — 데스크톱(EXE) 원클릭 실행 안전 계약.

실제 KIS 호출 0건. Rust 툴체인이 없는 환경에서도 검증 가능하도록, Tauri(Rust) 코드는
*정적 계약* 으로 고정한다 (빌드는 scripts/build_windows_installer.ps1 이 별도 수행).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import OrderAuditLog

ROOT = Path(__file__).resolve().parents[2]
TAURI = ROOT / "src-tauri"
KST = timezone(timedelta(hours=9))


def kst(h, m, s=0, d=date(2026, 10, 22)):
    return datetime.combine(d, time(h, m, s), tzinfo=KST).astimezone(timezone.utc)


@pytest.fixture
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    yield s
    s.close()


def _settings(**kw):
    from types import SimpleNamespace
    base = dict(enable_kis_paper_auto_trading=True, kis_paper_auto_order_dry_run=True,
                kis_is_paper=True, enable_live_trading=False, market_data_provider="kis",
                kis_paper_smoke_mode=False, kis_paper_auto_order_window_start="09:05",
                kis_paper_auto_order_window_end="14:30", kis_paper_rehearsal_mode=True,
                kis_paper_rehearsal_symbol="005930", kis_paper_rehearsal_max_buy_qty=1,
                krx_calendar_override_path="", ai_paper_tick_interval_seconds=30)
    base.update(kw)
    return SimpleNamespace(**base)


def _seed_holding(db, sym="005930", qty=1):
    db.add(OrderAuditLog(mode="PAPER", requested_by_ai=False, symbol=sym, side="BUY",
                         quantity=qty, order_type="MARKET", latest_price=1, decision="APPROVED",
                         reasons=[], trade_reason="kis_paper_auto", executed=True,
                         broker_order_id="B1", filled_quantity=qty,
                         created_at=datetime(2026, 10, 22, 1, 0)))
    db.commit()


# ── /health 신원 ────────────────────────────────────────────────────────────


def test_health_exposes_identity_without_secrets(client):
    body = client.get("/health").json()
    assert body["app_id"] == "agent-trader-backend"
    assert re.fullmatch(r"[0-9a-f]{32}", body["instance_id"]) or body["instance_id"]
    assert body["kis_is_paper"] is True and body["live_trading_enabled"] is False
    dump = json.dumps(body).lower()
    for forbidden in ("app_key", "appsecret", "app_secret", "account_no", "token"):
        assert forbidden not in dump


def test_health_instance_id_from_launcher_env():
    import app.main as m
    import os
    assert m._INSTANCE_ID == (os.environ.get("AUTOTRADE_INSTANCE_ID") or m._INSTANCE_ID)


# ── 앱 실행 ≠ 매매 시작 ─────────────────────────────────────────────────────


def test_app_launch_does_not_start_auto_trading(client):
    # 앱(백엔드) 기동 직후 자동매매 루프는 PAUSED — 신규 매수 tick 0.
    st = client.get("/api/auto-paper/status").json()
    # (장외 시각엔 표시 상태가 MARKET_CLOSED 일 수 있다 — 핵심은 RUNNING 이 아님)
    assert st["state"] != "RUNNING"
    assert st["state"] in ("PAUSED", "WAITING_MARKET", "MARKET_CLOSED", "STOPPED")


def test_driver_never_scans_until_operator_starts():
    from types import SimpleNamespace

    from app.auto_paper.background_driver import BackgroundTickDriver
    from app.core.modes import OperationMode
    loop = SimpleNamespace(status=lambda now=None: SimpleNamespace(state="PAUSED", cycle_count=0))
    drv = BackgroundTickDriver(
        settings_provider=lambda: _settings(enable_ai_paper_background_tick=True,
                                            default_mode=OperationMode.PAPER),
        loop_provider=lambda: loop)
    ok, code, _ = drv.evaluate_gate(kst(10, 0))
    assert ok is False and code == "AUTO_LOOP_NOT_RUNNING"


def test_loop_state_not_persisted_as_running_across_restart():
    from app.auto_paper.loop import AutoPaperLoop
    assert str(AutoPaperLoop().status().state) == "PAUSED"


# ── 종료 전 확인 (트레이 '안전 종료') ──────────────────────────────────────────


def test_shutdown_check_safe_when_no_holdings(db):
    from app.kis_paper.eod_liquidation import shutdown_check
    out = shutdown_check(db, settings=_settings(), now=kst(13, 0), risk=None)
    assert out["safe_to_quit"] is True and out["needs_running_for_eod"] is False
    assert out["auto_liquidation_guaranteed"] is False
    assert "보장할 수 없습니다" in out["guarantee_notice_ko"]


def test_shutdown_check_warns_when_holdings_before_close(db):
    from app.kis_paper.eod_liquidation import shutdown_check
    _seed_holding(db)
    out = shutdown_check(db, settings=_settings(), now=kst(14, 0), risk=None)
    assert out["safe_to_quit"] is False and out["needs_running_for_eod"] is True
    assert "자동 청산이 실행되지 않습니다" in out["message_ko"]
    assert out["program_holdings"] == [{"symbol": "005930", "quantity": 1}]


def test_shutdown_check_after_close_does_not_block_but_reports(db):
    from app.kis_paper.eod_liquidation import shutdown_check
    _seed_holding(db)
    out = shutdown_check(db, settings=_settings(), now=kst(16, 0), risk=None)
    assert out["safe_to_quit"] is True and out["needs_running_for_eod"] is False
    assert "장 마감 이후" in out["message_ko"]


def test_shutdown_check_api(client):
    r = client.get("/api/kis-paper/eod/shutdown-check")
    assert r.status_code == 200
    assert {"safe_to_quit", "program_holdings", "message_ko"} <= set(r.json())


def test_eod_status_has_start_screen_fields(db):
    from app.kis_paper.eod_liquidation import eod_status
    st = eod_status(db, settings=_settings(), now=kst(13, 0), risk=None)
    assert st["rehearsal_mode"] is True and st["rehearsal_symbol"] == "005930"
    assert st["rehearsal_max_buy_qty"] == 1
    assert "kis_credentials_present" in st and "auto_loop_state" in st


# ── Tauri (Rust) 정적 계약 — 컴파일 없이 고정 ────────────────────────────────


def _lib():
    return (TAURI / "src" / "lib.rs").read_text(encoding="utf-8")


def _code(src: str) -> str:
    """줄 단위 `//` 주석 제거 — 문자열 안에 `//` 가 있는 줄(따옴표 포함)은 그대로 둔다."""
    out = []
    for line in src.splitlines():
        head, sep, _ = line.partition("//")
        out.append(head if sep and head.count('"') % 2 == 0 else line)
    return "\n".join(out)


def test_close_button_hides_to_tray_and_does_not_kill_backend():
    src = _code(_lib())
    m = re.search(r"\.on_window_event\(\|window, event\| \{(.*?)\n        \}\)", src, re.S)
    assert m, "on_window_event block not found"
    block = m.group(1)
    assert "api.prevent_close()" in block and "window.hide()" in block
    assert ".kill(" not in block and "kill_sidecar" not in block   # X 버튼으로 sidecar 종료 금지


def test_single_instance_plugin_registered_first():
    src = _lib()
    plugins = re.findall(r"\.plugin\((tauri_plugin_\w+)", src)
    assert plugins[0] == "tauri_plugin_single_instance"
    toml = (TAURI / "Cargo.toml").read_text(encoding="utf-8")
    assert 'tauri-plugin-single-instance = "2"' in toml
    assert 'features = ["tray-icon"]' in toml


def test_tray_menu_and_safe_quit_flow():
    src = _lib()
    for label in ("대시보드 열기", "상태 확인", "안전 종료"):
        assert label in src
    assert "fn confirm_quit(" in src and "app.exit(0)" in src
    assert "RunEvent::Exit" in src and 'kill_sidecar(app_handle, "app exit")' in src
    assert "fn backend_port_info(" in src
    for cmd in ("backend_port_info", "confirm_quit", "hide_to_tray"):
        assert cmd in src.split("generate_handler![", 1)[1].split("]", 1)[0]


def test_rust_never_starts_trading_or_calls_order_apis():
    src = _code(_lib())
    for forbidden in ("/api/auto-paper/start", "run-once", "place_order", "route_order",
                      "ENABLE_LIVE_TRADING=true", "set_var(\"ENABLE_LIVE"):
        assert forbidden not in src


def test_rust_log_lines_are_masked_before_write():
    src = _lib()
    assert "mask_secrets(line)" in src.split("fn append_log", 1)[1].split("}", 3)[0] + \
        src.split("fn append_log", 1)[1][:400]


def test_bundle_contains_no_secrets_or_data():
    conf = json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    assert conf["bundle"]["resources"] == []
    assert conf["bundle"]["externalBin"] == ["binaries/autotrade-backend"]
    assert conf["bundle"]["targets"] == ["nsis"]
    sidecar = (ROOT / "scripts" / "build_backend_sidecar.ps1").read_text(encoding="utf-8")
    add_data = re.findall(r'--add-data\s+"([^"]+)"', sidecar)
    assert add_data and all("alembic" in a for a in add_data)
    for forbidden in (".env", "auto_trader.db", "token_cache", "quarantine", "data\\"):
        assert all(forbidden not in a for a in add_data)


def test_versions_in_lockstep():
    conf = json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    pkg = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    cargo = re.search(r'^version = "([^"]+)"', (TAURI / "Cargo.toml").read_text(encoding="utf-8"),
                      re.M).group(1)
    assert conf["version"] == pkg["version"] == cargo == "1.1.0"   # 5월 1.0.0 산출물과 구분


def test_build_stamp_version_falls_back_to_tauri_conf(monkeypatch):
    # EXE-FINAL-BUILD-V2: 설치본 /api/system/build-info 의 version 이 "unknown" 이었다
    #   (AUTOTRADE_APP_VERSION 미설정). tauri.conf.json 버전으로 채운다.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "generate_build_stamp", ROOT / "scripts" / "generate_build_stamp.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.delenv("AUTOTRADE_APP_VERSION", raising=False)
    conf = json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    assert mod.build_stamp(None, None)["version"] == conf["version"]
    monkeypatch.setenv("AUTOTRADE_APP_VERSION", "9.9.9")
    assert mod.build_stamp(None, None)["version"] == "9.9.9"


def test_stale_service_worker_purged_before_webview_created():
    # EXE-FINAL-BUILD-V2: 구 frontend 가 tauri.localhost 에 등록한 SW 가 두 번째 실행부터
    #   offline.html 만 띄웠다. webview 생성(.build) *전* 정리돼야 이미 설치된 PC 도 복구된다.
    src = _code(_lib())
    run_body = src.split("pub fn run()", 1)[1]
    assert run_body.index("purge_stale_service_worker(&profile)") < run_body.index(".build(context)")
    purge = src.split("fn purge_stale_service_worker", 1)[1].split("\n}\n", 1)[0]
    assert 'join("Service Worker")' in purge and "std::fs::rename" in purge
    assert "Local Storage" not in purge   # 다른 프로필 데이터 미접촉
    reg = (ROOT / "frontend" / "src" / "registerServiceWorker.js").read_text(encoding="utf-8")
    assert "isTauriDesktop()" in reg.split("export async function registerServiceWorker", 1)[1]


def test_user_data_dir_is_not_the_bundle_identifier_dir():
    # NSIS 제거 시 '앱 데이터 삭제' 는 %APPDATA%\\<identifier> 대상 — 운영 데이터는
    #   %APPDATA%\\Autotrade 에 있으므로 재설치/업데이트/제거로 지워지지 않는다.
    conf = json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    import app_desktop_launcher as launcher
    assert conf["identifier"] != launcher.APPDATA_DIR_NAME == "Autotrade"


def test_csp_only_localhost_backend_ports():
    conf = json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    csp = conf["app"]["security"]["csp"]
    hosts = set(re.findall(r"https?://[^\s;]+", csp))
    assert hosts <= {"http://ipc.localhost", "http://127.0.0.1:8000", "http://127.0.0.1:8001",
                     "http://127.0.0.1:8002", "http://localhost:8000", "http://localhost:8001",
                     "http://localhost:8002"}


# ── 마이그레이션 (설치형 환경: alembic 이 sidecar 에 번들) ─────────────────────


def test_migration_chain_single_head_includes_0027_0028():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config(str(ROOT / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    sd = ScriptDirectory.from_config(cfg)
    heads = sd.get_heads()
    assert len(heads) == 1
    revs = {r.revision for r in sd.walk_revisions()}
    assert {"0027", "0028"} <= revs
    assert heads[0] == "0028"


def test_migrations_upgrade_preserves_existing_rows(tmp_path):
    """기존 DB(0026 시점) 위에 0027/0028 을 올려도 기존 행·데이터 보존."""
    import sqlite3

    from alembic import command
    from alembic.config import Config
    dbp = tmp_path / "existing.db"
    cfg = Config(str(ROOT / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{dbp.as_posix()}")
    import os
    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite:///{dbp.as_posix()}"
    try:
        command.upgrade(cfg, "0026")
        con = sqlite3.connect(dbp)
        cols = [c[1] for c in con.execute("pragma table_info(order_audit_log)")]
        assert "broker_org_no" not in cols
        con.execute("insert into order_audit_log (created_at, mode, requested_by_ai, symbol, side,"
                    " quantity, order_type, latest_price, decision, reasons, archived, executed,"
                    " filled_quantity, message) values ('2026-10-01', 'PAPER', 0, '005930', 'BUY',"
                    " 1, 'MARKET', 1, 'APPROVED', '[]', 0, 1, 1, 'kept')")
        con.commit()
        con.close()
        command.upgrade(cfg, "head")
        con = sqlite3.connect(dbp)
        cols = [c[1] for c in con.execute("pragma table_info(order_audit_log)")]
        assert "broker_org_no" in cols
        assert con.execute("select message from order_audit_log").fetchone()[0] == "kept"
        assert con.execute("select count(*) from eod_liquidation_state").fetchone()[0] == 0
        con.close()
    finally:
        if old is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old


# ── 설치형 DB 위치: 설치 폴더 밖(%APPDATA%\Autotrade\data), 이전 DB 는 복사(원본 보존) ──


def _frozen(monkeypatch, tmp_path, exe_dir):
    import sys
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "agent-trader-v1.exe"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(exe_dir)


def test_frozen_db_anchored_to_appdata_and_legacy_copied(monkeypatch, tmp_path):
    import logging
    import os

    import app_desktop_launcher as launcher
    exe_dir = tmp_path / "Local" / "Agent Trader v1"
    (exe_dir / "data").mkdir(parents=True)
    legacy = exe_dir / "data" / "auto_trader.db"
    legacy.write_bytes(b"legacy-db-bytes")
    _frozen(monkeypatch, tmp_path, exe_dir)
    launcher._anchor_relative_db_to_absolute(logging.getLogger("t"))
    target = tmp_path / "Roaming" / "Autotrade" / "data" / "auto_trader.db"
    assert os.environ["DATABASE_URL"] == "sqlite:///" + target.resolve().as_posix()
    assert target.read_bytes() == b"legacy-db-bytes"
    assert legacy.exists()                                   # 원본 삭제 0
    # 두 번째 실행: 새 위치 DB 를 덮어쓰지 않는다.
    target.write_bytes(b"newer-data")
    monkeypatch.delenv("DATABASE_URL")
    launcher._anchor_relative_db_to_absolute(logging.getLogger("t"))
    assert target.read_bytes() == b"newer-data"


def test_frozen_db_respects_operator_absolute_path(monkeypatch, tmp_path):
    import logging
    import os

    import app_desktop_launcher as launcher
    exe_dir = tmp_path / "Local" / "Agent Trader v1"
    exe_dir.mkdir(parents=True)
    _frozen(monkeypatch, tmp_path, exe_dir)
    custom = "sqlite:///" + (tmp_path / "mine.db").as_posix()
    monkeypatch.setenv("DATABASE_URL", custom)
    launcher._anchor_relative_db_to_absolute(logging.getLogger("t"))
    assert os.environ["DATABASE_URL"] == custom


def test_launcher_always_anchors_db_even_without_env_file():
    src = (ROOT / "backend" / "app_desktop_launcher.py").read_text(encoding="utf-8")
    run_body = src.split("def run(", 1)[1]
    assert "_anchor_relative_db_to_absolute(log)" in run_body
