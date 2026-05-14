"""Agent Trader v1 — desktop sidecar launcher entrypoint (#90).

본 모듈은 Tauri sidecar (PyInstaller `--onefile`) 로 패키징되어 EXE 앱이
시작될 때 함께 spawn 되는 **backend 서버 entrypoint** 다. 매매 로직을 *직접
호출하지 않으며*, 단지 `uvicorn` 으로 `app.main:app` 을 127.0.0.1 에 띄우는
얇은 wrapper.

절대 원칙 (CLAUDE.md):
  - 실거래 활성화 금지: ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION /
    ENABLE_FUTURES_LIVE_TRADING 을 *읽기*만 — 본 launcher 는 어떤 값도
    `os.environ` 에 *주입하지 않는다*. 운영자 .env 가 진실의 단일 출처.
  - KIS_IS_PAPER 강제: launcher 가 *읽어서 검증*만 — false 이면 경고를
    stderr 에 출력하고 진행 (강제 종료가 아니라 backend `RiskManager` +
    `KisPaperReadiness` 가 차단). 검증 자체로 끄지 않음.
  - Secret 출력 금지: API Key / Secret / 계좌번호의 *원문* 을 stdout /
    stderr / log file 에 출력하지 않는다. 존재 여부만 표시.
  - broker / OrderExecutor / route_order 임포트 0건 — uvicorn 의 module
    string ("app.main:app") 으로 *간접* 진입.

설정 파일 우선순위 (`.env`):
  1. `%APPDATA%\\Autotrade\\.env`   (EXE 설치 후 운영자 추가)
  2. `C:\\trade\\autotrade\\backend\\.env`   (개발 환경)
  3. CWD `./backend/.env`
  4. CWD `./.env`
  5. 없으면 안내 출력 (Secret 0 노출)

로그:
  - `%APPDATA%\\Autotrade\\logs\\backend-YYYYMMDD.log` (Windows)
  - 또는 CWD `logs/desktop/backend-YYYYMMDD.log` fallback
  - log 에는 시간 / level / message 만 — Secret / token / key 원문 0건.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
from datetime import datetime
from pathlib import Path

# 본 모듈은 *간접* 진입만 사용 — broker / OrderExecutor 등을 *직접* import 0건.
# 정적 grep 가드: 본 파일에 `from app.brokers` / `from app.execution` /
# `from app.kis_paper.engine` / `route_order` / `place_order` 0건.

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
APP_NAME = "Agent Trader v1"
APPDATA_DIR_NAME = "Autotrade"   # %APPDATA%\Autotrade\

# 안전 flag — 본 launcher 는 *변경하지 않는다*. 진단용으로 *읽기만*.
_SAFETY_FLAG_KEYS = (
    "ENABLE_LIVE_TRADING",
    "ENABLE_AI_EXECUTION",
    "ENABLE_FUTURES_LIVE_TRADING",
    "KIS_IS_PAPER",
    "DEFAULT_MODE",
)

# 출력 금지 키 — 원문이 stdout/stderr/log 에 노출되면 안 됨.
_SECRET_KEYS = (
    "KIS_APP_KEY",
    "KIS_APP_SECRET",
    "KIS_ACCOUNT_NO",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "TELEGRAM_BOT_TOKEN",
)


def candidate_env_paths() -> list[Path]:
    """`.env` 검색 후보. 운영자가 *어느 위치든 하나만* 채우면 됨.

    %APPDATA% 가 없는 (예: 비-Windows 또는 sandbox) 환경에서는 자동으로 skip.
    """
    paths: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(Path(appdata) / APPDATA_DIR_NAME / ".env")
    # 개발 환경 기본
    paths.append(Path(r"C:\trade\autotrade\backend\.env"))
    # CWD 기반 fallback (PyInstaller frozen 일 때도 동작)
    cwd = Path.cwd()
    paths.append(cwd / "backend" / ".env")
    paths.append(cwd / ".env")
    return paths


def resolve_env_path() -> Path | None:
    """후보 중 *처음으로 존재* 하는 .env 경로. 없으면 None."""
    for p in candidate_env_paths():
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def load_env_file(path: Path) -> dict[str, str]:
    """단순 `KEY=VALUE` 파서. 외부 의존성 0건 (python-dotenv 미사용).

    *값을 stdout/log 에 출력하지 않는다* — 본 함수의 호출자는 dict 를 직접
    `os.environ` 에 주입하기 전에 *키 이름* 만 logging 하도록 한다.
    """
    parsed: dict[str, str] = {}
    if not path.is_file():
        return parsed
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return parsed
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1]
        if key:
            parsed[key] = value
    return parsed


def _setup_logging() -> Path | None:
    """로그 파일 경로 결정 + handler 설정. Secret 출력 금지."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        log_dir = Path(appdata) / APPDATA_DIR_NAME / "logs"
    else:
        log_dir = Path.cwd() / "logs" / "desktop"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        log_dir = None
    log_path: Path | None = None
    if log_dir is not None:
        stamp = datetime.now().strftime("%Y%m%d")
        log_path = log_dir / f"backend-{stamp}.log"

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_path is not None:
        try:
            handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
        except OSError:
            log_path = None

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )
    return log_path


def _print_safety_snapshot(log: logging.Logger, env_path: Path | None) -> None:
    """안전 flag *상태* 만 logging — 값 원문은 .env 그대로지만 log 에는 OK/위험만."""
    if env_path is None:
        log.info("safety: .env not found in any candidate (will use process env only)")
    else:
        log.info("safety: .env resolved from %s", env_path)

    snapshot = {k: os.environ.get(k, "") for k in _SAFETY_FLAG_KEYS}
    enable_live = (snapshot.get("ENABLE_LIVE_TRADING") or "").lower() == "true"
    enable_ai = (snapshot.get("ENABLE_AI_EXECUTION") or "").lower() == "true"
    enable_fut = (snapshot.get("ENABLE_FUTURES_LIVE_TRADING") or "").lower() == "true"
    kis_paper_raw = (snapshot.get("KIS_IS_PAPER") or "true").lower()
    kis_paper = kis_paper_raw != "false"
    default_mode = snapshot.get("DEFAULT_MODE") or "SIMULATION"

    log.info("safety: DEFAULT_MODE=%s", default_mode)
    log.info("safety: KIS_IS_PAPER=%s (paper-only required)", kis_paper)
    if enable_live:
        log.warning("safety: ENABLE_LIVE_TRADING=true detected — KIS Paper test will refuse to start until set to false")
    else:
        log.info("safety: ENABLE_LIVE_TRADING=false (OK)")
    if enable_ai:
        log.warning("safety: ENABLE_AI_EXECUTION=true detected — KIS Paper test will refuse to start until set to false")
    else:
        log.info("safety: ENABLE_AI_EXECUTION=false (OK)")
    if enable_fut:
        log.warning("safety: ENABLE_FUTURES_LIVE_TRADING=true detected — futures LIVE is permanently blocked by policy")
    else:
        log.info("safety: ENABLE_FUTURES_LIVE_TRADING=false (OK)")
    if not kis_paper:
        log.warning("safety: KIS_IS_PAPER=false detected — set to true to enable KIS paper one-click test")

    # secret 존재 여부만 — *원문 금지*
    for key in _SECRET_KEYS:
        present = bool((os.environ.get(key) or "").strip())
        log.info("secret-presence: %s=%s", key, "present" if present else "missing")


def _inject_env_keys(parsed: dict[str, str], log: logging.Logger) -> int:
    """`.env` parsed dict 를 `os.environ` 에 *주입* — 기존 값 우선.

    이미 process env 에 존재하는 키는 덮어쓰지 않는다 (운영자 의도 보호).
    *Secret 키의 값은 log 에 출력하지 않는다* — 키 이름만.
    """
    injected = 0
    for key, value in parsed.items():
        if key in os.environ:
            continue
        os.environ[key] = value
        injected += 1
        if key in _SECRET_KEYS:
            log.info("env: injected key=%s (value redacted)", key)
        elif key in _SAFETY_FLAG_KEYS:
            log.info("env: injected safety-flag %s=%s", key, value)
        else:
            log.info("env: injected key=%s", key)
    return injected


def is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """TCP 포트 in-use 여부 — 이미 backend 가 떠 있으면 sidecar 가 재spawn 하지 않음."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


def _parse_args(argv: list[str]) -> tuple[str, int]:
    host = DEFAULT_HOST
    port = DEFAULT_PORT
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--host",) and i + 1 < len(argv):
            host = argv[i + 1]
            i += 2
            continue
        if a in ("--port",) and i + 1 < len(argv):
            try:
                port = int(argv[i + 1])
            except ValueError:
                pass
            i += 2
            continue
        if a.startswith("--host="):
            host = a.split("=", 1)[1] or host
            i += 1
            continue
        if a.startswith("--port="):
            try:
                port = int(a.split("=", 1)[1])
            except ValueError:
                pass
            i += 1
            continue
        i += 1
    # 환경변수 override (Tauri 가 ENV 로 넘기는 케이스)
    env_port = os.environ.get("AUTOTRADE_BACKEND_PORT")
    if env_port:
        try:
            port = int(env_port)
        except ValueError:
            pass
    env_host = os.environ.get("AUTOTRADE_BACKEND_HOST")
    if env_host:
        host = env_host
    return host, port


def run(argv: list[str] | None = None) -> int:
    """Sidecar entrypoint. 정상 종료 0, port 충돌 5, uvicorn 실패 3, dep 누락 2."""
    argv = list(argv if argv is not None else sys.argv[1:])
    log_path = _setup_logging()
    log = logging.getLogger("autotrade.launcher")
    log.info("=" * 60)
    log.info("%s sidecar launcher starting (pid=%s)", APP_NAME, os.getpid())
    if log_path is not None:
        log.info("log file: %s", log_path)

    env_path = resolve_env_path()
    if env_path is not None:
        parsed = load_env_file(env_path)
        _inject_env_keys(parsed, log)
    _print_safety_snapshot(log, env_path)

    host, port = _parse_args(argv)
    log.info("backend bind: %s:%d", host, port)

    if is_port_open(host, port):
        log.warning(
            "port %s:%d already in use — assuming a previous backend instance "
            "is alive. Sidecar will exit without starting a new one.",
            host, port,
        )
        return 5  # frontend launcher can interpret this as "reuse existing"

    try:
        import uvicorn  # type: ignore
    except Exception as exc:   # pragma: no cover — defensive
        log.error("uvicorn import failed: %s", exc)
        log.error("install dependencies via: pip install -r backend/requirements.txt")
        return 2

    try:
        uvicorn.run(
            "app.main:app",
            host=host,
            port=port,
            log_level="info",
            access_log=False,
        )
    except SystemExit as exc:   # pragma: no cover — uvicorn raises on signal
        log.info("backend exited (SystemExit code=%s)", exc.code)
        return int(exc.code or 0)
    except Exception as exc:   # pragma: no cover — defensive
        log.error("backend crashed: %s", exc)
        return 3
    return 0


if __name__ == "__main__":   # pragma: no cover
    sys.exit(run())
