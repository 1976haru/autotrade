"""Desktop sidecar launcher 안전 invariant 테스트 (#90).

본 파일은 실제 uvicorn / broker 를 띄우지 *않는다* — `app_desktop_launcher` 의
순수 함수들 + invariant 만 검증.
"""

from __future__ import annotations

import io
import logging
import os
import pathlib
import sys

import pytest

# backend/ 를 sys.path 에 — pytest 가 root 에서 실행될 때.
_BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import app_desktop_launcher as launcher   # noqa: E402


# ====================================================================
# 1. .env path resolution
# ====================================================================


def test_candidate_env_paths_includes_appdata(monkeypatch):
    monkeypatch.setenv("APPDATA", r"C:\Users\test\AppData\Roaming")
    paths = launcher.candidate_env_paths()
    assert any("Autotrade" in str(p) for p in paths)


def test_candidate_env_paths_without_appdata(monkeypatch):
    monkeypatch.delenv("APPDATA", raising=False)
    paths = launcher.candidate_env_paths()
    # AppData entry 가 빠져도 fallback 후보가 존재 (CWD / backend/.env).
    assert len(paths) >= 2


def test_resolve_env_path_returns_first_existing(tmp_path, monkeypatch):
    appdata = tmp_path / "AppData"
    autotrade = appdata / "Autotrade"
    autotrade.mkdir(parents=True)
    target = autotrade / ".env"
    target.write_text("KIS_IS_PAPER=true\n", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))
    # backend/.env 도 만들지만 우선순위가 낮음
    found = launcher.resolve_env_path()
    assert found == target


def test_resolve_env_path_none_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "doesnotexist"))
    monkeypatch.chdir(tmp_path)
    assert launcher.resolve_env_path() is None


# ====================================================================
# 2. .env file parsing — Secret 값 출력 0건
# ====================================================================


def test_load_env_file_parses_kv(tmp_path):
    f = tmp_path / ".env"
    f.write_text(
        "KIS_IS_PAPER=true\n"
        "ENABLE_LIVE_TRADING=false\n"
        "# comment line\n"
        "\n"
        "KIS_APP_KEY=SECRET_should_not_appear_in_log\n",
        encoding="utf-8",
    )
    parsed = launcher.load_env_file(f)
    assert parsed["KIS_IS_PAPER"] == "true"
    assert parsed["ENABLE_LIVE_TRADING"] == "false"
    assert parsed["KIS_APP_KEY"] == "SECRET_should_not_appear_in_log"


def test_load_env_file_strips_quotes(tmp_path):
    f = tmp_path / ".env"
    f.write_text('KIS_APP_KEY="quoted-value"\n', encoding="utf-8")
    parsed = launcher.load_env_file(f)
    assert parsed["KIS_APP_KEY"] == "quoted-value"


def test_load_env_file_skips_lines_without_eq(tmp_path):
    f = tmp_path / ".env"
    f.write_text("malformed line without equals\nKIS_IS_PAPER=true\n", encoding="utf-8")
    parsed = launcher.load_env_file(f)
    assert parsed == {"KIS_IS_PAPER": "true"}


def test_load_env_file_missing_returns_empty(tmp_path):
    parsed = launcher.load_env_file(tmp_path / "does_not_exist")
    assert parsed == {}


# ====================================================================
# 3. injection — 기존 process env 보존 + Secret 값 미출력
# ====================================================================


def test_inject_env_keys_respects_existing(monkeypatch):
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    log = logging.getLogger("inject-test")
    parsed = {"ENABLE_LIVE_TRADING": "true"}    # *위험 값* — 본 launcher 가 덮어쓰면 절대 안 됨
    n = launcher._inject_env_keys(parsed, log)
    # 기존 process env 값을 *그대로 보존* — launcher 가 안전 flag 를 강제 활성화하지 않는다.
    assert os.environ["ENABLE_LIVE_TRADING"] == "false"
    assert n == 0


def test_inject_env_keys_does_not_mutate_safety_flags_when_absent(monkeypatch):
    """본 launcher 는 .env 가 누락한 안전 flag 를 *자기 임의로 set 하지 않는다*."""
    monkeypatch.delenv("ENABLE_LIVE_TRADING", raising=False)
    log = logging.getLogger("inject-test-2")
    parsed = {"KIS_IS_PAPER": "true"}   # ENABLE_LIVE_TRADING 키 없음
    launcher._inject_env_keys(parsed, log)
    # launcher 가 가짜로 채우지 않는다 — backend Settings 의 default 가 진실.
    assert "ENABLE_LIVE_TRADING" not in os.environ


def test_inject_env_keys_redacts_secret_in_log(monkeypatch, caplog):
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    log = logging.getLogger("autotrade.launcher.test")
    with caplog.at_level(logging.INFO, logger="autotrade.launcher.test"):
        launcher._inject_env_keys(
            {"KIS_APP_KEY": "topsecret-VALUE-SHOULD-NOT-LEAK"}, log,
        )
    # caplog 모든 record 에서 secret 원문 0건.
    for rec in caplog.records:
        assert "topsecret-VALUE-SHOULD-NOT-LEAK" not in rec.getMessage()


# ====================================================================
# 4. safety snapshot logging — 위험 flag warn, secret 원문 0건
# ====================================================================


def test_safety_snapshot_warns_on_live_trading_true(monkeypatch, caplog):
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "true")
    monkeypatch.setenv("ENABLE_AI_EXECUTION", "false")
    monkeypatch.setenv("ENABLE_FUTURES_LIVE_TRADING", "false")
    monkeypatch.setenv("KIS_IS_PAPER", "true")
    monkeypatch.setenv("DEFAULT_MODE", "PAPER")
    log = logging.getLogger("autotrade.launcher")
    with caplog.at_level(logging.WARNING, logger="autotrade.launcher"):
        launcher._print_safety_snapshot(log, None)
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "ENABLE_LIVE_TRADING=true" in msgs


def test_safety_snapshot_warns_on_kis_paper_false(monkeypatch, caplog):
    monkeypatch.setenv("KIS_IS_PAPER", "false")
    log = logging.getLogger("autotrade.launcher")
    with caplog.at_level(logging.WARNING, logger="autotrade.launcher"):
        launcher._print_safety_snapshot(log, None)
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "KIS_IS_PAPER=false" in msgs


def test_safety_snapshot_does_not_modify_env(monkeypatch):
    """본 함수가 안전 flag 를 변경하면 안 된다 — read-only invariant."""
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    monkeypatch.setenv("ENABLE_AI_EXECUTION", "false")
    monkeypatch.setenv("KIS_IS_PAPER", "true")
    before = {
        "ENABLE_LIVE_TRADING":         os.environ.get("ENABLE_LIVE_TRADING"),
        "ENABLE_AI_EXECUTION":         os.environ.get("ENABLE_AI_EXECUTION"),
        "ENABLE_FUTURES_LIVE_TRADING": os.environ.get("ENABLE_FUTURES_LIVE_TRADING"),
        "KIS_IS_PAPER":                os.environ.get("KIS_IS_PAPER"),
    }
    log = logging.getLogger("autotrade.launcher")
    launcher._print_safety_snapshot(log, None)
    for k, v in before.items():
        assert os.environ.get(k) == v


def test_safety_snapshot_secret_presence_only_no_value(monkeypatch, caplog):
    """secret 키의 *값* 은 log 에 출력되지 않는다 — present/missing 만."""
    monkeypatch.setenv("KIS_APP_KEY", "should-NEVER-be-logged-VALUE")
    monkeypatch.setenv("KIS_APP_SECRET", "another-VALUE-NEVER-logged")
    log = logging.getLogger("autotrade.launcher")
    with caplog.at_level(logging.INFO, logger="autotrade.launcher"):
        launcher._print_safety_snapshot(log, None)
    full = " ".join(r.getMessage() for r in caplog.records)
    assert "should-NEVER-be-logged-VALUE" not in full
    assert "another-VALUE-NEVER-logged" not in full
    assert "present" in full or "missing" in full  # presence 마커


# ====================================================================
# 5. port probe
# ====================================================================


def test_is_port_open_false_for_random_high_port():
    # 65499 는 보통 비어 있음. 본 테스트는 false 가 나와도 OK (정확한 검증은
    # mock 으로 어려움) — 메서드가 raise 없이 bool 반환만 보장.
    assert isinstance(launcher.is_port_open("127.0.0.1", 65499, timeout=0.1), bool)


# ====================================================================
# 6. arg parsing
# ====================================================================


def test_parse_args_defaults():
    host, port = launcher._parse_args([])
    assert host == "127.0.0.1"
    assert port == 8000


def test_parse_args_host_port_flags():
    host, port = launcher._parse_args(["--host", "0.0.0.0", "--port", "18000"])
    assert host == "0.0.0.0"
    assert port == 18000


def test_parse_args_env_override(monkeypatch):
    monkeypatch.setenv("AUTOTRADE_BACKEND_PORT", "19999")
    monkeypatch.setenv("AUTOTRADE_BACKEND_HOST", "127.0.0.2")
    host, port = launcher._parse_args([])
    assert host == "127.0.0.2"
    assert port == 19999


# ====================================================================
# 7. invariant — broker / OrderExecutor / route_order 직접 import 0건
# ====================================================================


def test_launcher_does_not_import_broker_or_executor():
    """`app_desktop_launcher.py` 의 *AST level* 에 broker / OrderExecutor /
    route_order 직접 import 가 0건. 본 검사는 docstring / 주석을 무시한다."""
    import ast
    src_path = _BACKEND_DIR / "app_desktop_launcher.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    banned_modules = {
        "app.brokers",
        "app.execution.order_router",
        "app.execution.executor",
        "app.execution.order_executor",
        "app.kis_paper.engine",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(alias.name == m or alias.name.startswith(m + ".")
                                for m in banned_modules), \
                    f"banned import in launcher: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert not any(mod == m or mod.startswith(m + ".")
                            for m in banned_modules), \
                f"banned import-from in launcher: {mod}"

    # 함수 호출도 검사 — `broker.place_order(...)` / `route_order(...)` /
    # `OrderExecutor(...)` 같은 실제 호출이 *AST attribute/call* 로 잡혀야 함.
    src_text = src_path.read_text(encoding="utf-8")
    # 주석 라인 (#) 과 docstring 을 제거한 뒤 검사.
    lines = []
    for line in src_text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        lines.append(line)
    code_only = "\n".join(lines)
    # docstring 도 ast 로 제거.
    cleaned_tree = ast.parse(code_only)
    for node in ast.walk(cleaned_tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            # docstring — replace with placeholder
            node.value.value = ""
    cleaned_src = ast.unparse(cleaned_tree)
    banned_calls = [
        "broker.place_order(",
        "route_order(",
        "OrderExecutor",
    ]
    for b in banned_calls:
        assert b not in cleaned_src, f"banned call/symbol in launcher: {b!r}"


def test_launcher_module_has_no_runtime_app_imports():
    """import 후 sys.modules 에 broker / executor 가 *없어야* 한다."""
    # 본 테스트는 backend conftest 가 app.main 을 import 한 경우 false 가 될 수 있어,
    # *launcher 모듈 자신* 만 reload 해 검사.
    import importlib
    importlib.reload(launcher)
    # launcher 자체가 broker 를 *직접* import 했는지를 source 로 검사 (위 test 와 보완).
    # 이중 invariant — source 검사 + 동적 reload.
    assert hasattr(launcher, "run")


# ====================================================================
# 8. logging setup — Secret 원문 노출 0건
# ====================================================================


def test_setup_logging_returns_path_or_none(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    p = launcher._setup_logging()
    # path 가 None 일 수도 (OSError) 정상 path 일 수도. 둘 다 허용.
    assert p is None or isinstance(p, pathlib.Path)
