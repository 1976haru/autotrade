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

import json
import logging
import os
import socket
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

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


def load_env_via_dotenv(
    path: Path,
    log: logging.Logger,
    *,
    override: bool = True,
) -> bool:
    """`python-dotenv` 의 `load_dotenv(path, override=True)` 로 명시 로드.

    fix/desktop-kis-env-readiness-load:
    기존 `_inject_env_keys()` 는 *이미 process env 에 존재* 하는 키 (빈 문자열
    포함) 를 보존했다. Windows EXE 흐름에서 부모 셸이 `KIS_APP_KEY=""` 같은
    빈 값을 carry 하면 .env 의 실제 값이 덮어쓰이지 *않아* readiness 가 missing
    으로 보고되는 회귀가 발생.

    본 함수는 `python-dotenv` 의 `override=True` 모드를 사용해 *항상* .env
    값으로 덮어쓴다. 운영자가 process env 로 명시 override 를 원하면 dotenv
    호출 *후* set 하면 됨. *Secret 원문은 log 에 절대 출력하지 않음* — 키
    이름만.

    Returns: 로드 성공 여부 (파일 존재 + 파싱 성공 → True).
    """
    try:
        from dotenv import dotenv_values, load_dotenv
    except ImportError:   # pragma: no cover — defensive
        log.warning(
            "env: python-dotenv unavailable, falling back to custom parser. "
            "Install via: pip install python-dotenv"
        )
        parsed = load_env_file(path)
        _inject_env_keys(parsed, log)
        return bool(parsed)

    try:
        parsed = dotenv_values(str(path))   # 키 이름 로깅용 (값은 미사용)
    except OSError as exc:
        log.warning("env: dotenv read failed for %s: %s", path, exc)
        return False

    ok = load_dotenv(dotenv_path=str(path), override=override)
    if not ok:
        log.warning("env: load_dotenv returned False for %s", path)
        return False

    # 키 이름만 로그에 표시. Secret 값은 절대 출력 X.
    for key in parsed.keys():
        if key in _SECRET_KEYS:
            log.info("env: loaded key=%s (value redacted, override=%s)",
                     key, override)
        elif key in _SAFETY_FLAG_KEYS:
            log.info("env: loaded safety-flag %s=%s (override=%s)",
                     key, os.environ.get(key, ""), override)
        else:
            log.info("env: loaded key=%s (override=%s)", key, override)

    # Settings cache 강제 invalidate — uvicorn 이 app.main 을 import 하기 *전*
    # 에 호출되어야 함. import 후 lru_cache 가 빈 Settings 를 캐싱하면 endpoint
    # 가 stale 값을 반환.
    try:
        from app.core.config import get_settings
        get_settings.cache_clear()
        log.info("env: get_settings.cache_clear() invoked")
    except Exception as exc:   # noqa: BLE001 — defensive (boot order)
        log.warning("env: settings cache clear skipped: %s", exc)

    return True


def is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """TCP 포트 in-use 여부 — connect 시도가 성공하면 누군가 listen 중."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


def is_backend_alive(host: str, port: int, timeout: float = 1.5) -> bool:
    """포트에 listen 중인 프로세스가 *Agent Trader backend* 인지 확인.

    /health 가 200 + body 에 'ok' or 'status' 키를 포함하면 같은 backend 로 간주.
    실패 시 False — 다른 프로세스가 포트를 점유한 것 (예: 이전 stale sidecar /
    다른 앱). 본 함수가 False 면 호출자는 fallback port 로 bind 시도한다.

    *어떤 broker / OrderExecutor / 실거래 API 도 호출하지 않는다* — 단순 HTTP
    GET /health 만.
    """
    url = f"http://{host}:{port}/health"
    try:
        with urlopen(url, timeout=timeout) as resp:  # noqa: S310 — local 127.0.0.1
            if resp.status != 200:
                return False
            try:
                body = json.loads(resp.read().decode("utf-8", errors="replace"))
            except (ValueError, UnicodeDecodeError):
                return False
            if not isinstance(body, dict):
                return False
            return bool(body.get("ok")) or body.get("status") == "ok"
    except (URLError, OSError, TimeoutError):
        return False


def find_free_port(
    host: str,
    candidates: list[int],
    log: logging.Logger,
) -> tuple[int, bool, str]:
    """candidates 를 순회하며 첫 *free* 포트를 반환.

    - 어떤 후보도 free 가 아니면 (host, candidates[0], False, "all in use")
    - free 후보 발견 → (port, True, "free")
    - in-use 후보가 *Agent Trader backend* (health 200) 면 → (port, True, "reuse-backend")

    반환: (port, ok, reason)
    """
    if not candidates:
        return (DEFAULT_PORT, False, "no candidates")

    for p in candidates:
        if not is_port_open(host, p):
            log.info("port probe %s:%d → FREE", host, p)
            return (p, True, "free")
        log.warning("port probe %s:%d → IN USE", host, p)
        if is_backend_alive(host, p):
            log.info(
                "port %s:%d already hosts an Agent Trader backend "
                "(/health responded 200) — sidecar will reuse it.",
                host, p,
            )
            return (p, True, "reuse-backend")
        log.warning(
            "port %s:%d in use but /health does NOT respond — likely a stale "
            "process or non-backend listener. trying next candidate.",
            host, p,
        )
    return (candidates[0], False, "all in use, none responded to /health")


def write_backend_port_file(host: str, port: int, mode: str) -> Path | None:
    """%APPDATA%/Autotrade/backend-port.json 에 현재 bound port 기록.

    `mode`: "free" (새로 bind) / "reuse-backend" (기존 backend 재사용)
    frontend backendLauncher 가 본 파일을 읽어 8000 외 fallback 포트를 알아낸다.

    *Secret 절대 포함 0건* — host, port, mode, written_at 만.
    """
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    try:
        out_dir = Path(appdata) / APPDATA_DIR_NAME
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "backend-port.json"
        out_path.write_text(
            json.dumps({
                "host":        host,
                "port":        port,
                "mode":        mode,
                "written_at":  datetime.utcnow().isoformat() + "Z",
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return out_path
    except OSError:
        return None


# Fallback 후보. 운영자가 명시 --port 를 안 주면 본 순서로 시도.
DEFAULT_PORT_CANDIDATES: list[int] = [8000, 8001, 8002]


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
    env_loaded = False
    if env_path is not None:
        # fix/desktop-kis-env-readiness-load: load_dotenv(override=True) 로
        # 부모 셸의 빈 값 / leftover 환경변수를 .env 값으로 덮어쓰고, 직후
        # get_settings.cache_clear() 로 stale Settings 캐시를 무효화한다.
        env_loaded = load_env_via_dotenv(env_path, log)
        if not env_loaded:
            log.warning(
                "env: load_dotenv reported failure for %s — falling back to "
                "legacy parser",
                env_path,
            )
            parsed = load_env_file(env_path)
            _inject_env_keys(parsed, log)
            env_loaded = bool(parsed)
    _print_safety_snapshot(log, env_path)
    # readiness API 가 본 정보를 carry 할 수 있도록 process env 로 공개.
    # *secret 원문 아님* — 파일 경로 + 로드 성공 여부만.
    if env_path is not None:
        os.environ["AUTOTRADE_ENV_FILE_PATH"] = str(env_path)
    os.environ["AUTOTRADE_ENV_FILE_FOUND"] = "true" if env_path is not None else "false"
    os.environ["AUTOTRADE_ENV_FILE_LOADED"] = "true" if env_loaded else "false"

    # fix/desktop-nonblocking-migration-health: 데스크톱 EXE 흐름에서는
    # `migration_nonblocking=True` 로 lifespan 이 alembic migration 을
    # background thread 로 실행 → `/health` 와 `/api/status` 가 첫 응답부터
    # 200 응답 → frontend launcher 가 "초기 DB 준비 중" UI 를 그릴 수 있음.
    # 운영자가 .env 에 명시 false 로 설정한 경우는 *그대로 존중* (override 0건).
    if "MIGRATION_NONBLOCKING" not in os.environ:
        os.environ["MIGRATION_NONBLOCKING"] = "true"
        log.info("env: MIGRATION_NONBLOCKING=true (desktop default — backend startup non-blocking)")
    else:
        log.info(
            "env: MIGRATION_NONBLOCKING=%s (operator override preserved)",
            os.environ.get("MIGRATION_NONBLOCKING"),
        )

    host, port = _parse_args(argv)
    log.info("backend bind: %s:%d (requested)", host, port)

    # Port fallback:
    # - 운영자가 명시 --port 를 줬다면: 그 포트만 시도 (단일 candidate)
    # - default 면: 8000 → 8001 → 8002 순서 시도
    # - in-use 인데 /health 가 응답 → 기존 backend 재사용 (exit 5)
    # - in-use 인데 /health 실패 → 다음 candidate 로 진행 (stale port 회피)
    if port == DEFAULT_PORT:
        candidates = list(DEFAULT_PORT_CANDIDATES)
    else:
        candidates = [port]

    chosen_port, ok, reason = find_free_port(host, candidates, log)
    if not ok:
        log.error(
            "could not find a usable port for backend (host=%s candidates=%s). "
            "All ports in use and none responded to /health. Possible cause: "
            "stale process holding 8000-8002 / OS firewall / corporate proxy. "
            "Sidecar exits without starting.",
            host, candidates,
        )
        return 5  # 기존 exit code 호환 — frontend 가 port 충돌로 인지

    port_file = write_backend_port_file(host, chosen_port, reason)
    if port_file is not None:
        log.info("backend-port.json written → %s", port_file)
    else:
        log.warning(
            "backend-port.json write skipped (APPDATA missing or write error)"
        )

    if reason == "reuse-backend":
        log.info(
            "existing backend on %s:%d verified via /health — sidecar exits, "
            "frontend should connect to the existing instance.",
            host, chosen_port,
        )
        return 5

    if chosen_port != port:
        log.warning(
            "primary port %d in use without /health response → using fallback "
            "port %d. frontend will read backend-port.json or try 8000/8001/8002.",
            port, chosen_port,
        )

    try:
        import uvicorn  # type: ignore
    except Exception as exc:   # pragma: no cover — defensive
        log.error("uvicorn import failed: %s", exc)
        log.error("install dependencies via: pip install -r backend/requirements.txt")
        return 2

    # fix/desktop-backend-startup-readiness: uvicorn.run 진입 직전 명시 marker.
    # 본 로그 라인은 사용자가 desktop-backend.log 를 열어볼 때 "여기까지 진행"
    # 을 알려준다. 이후 단계는 (1) uvicorn 자체 logger 가 "Started server
    # process" / "Waiting for application startup" / "Application startup
    # complete" 를 emit, (2) FastAPI lifespan 이 "[startup] alembic migration
    # starting" / "complete" / "backend ready" 를 emit. 모두 같은 root logger
    # → 같은 FileHandler 에 기록.
    log.info(
        "uvicorn.run starting on %s:%d — FastAPI lifespan will run alembic "
        "migration next (첫 실행 시 1~2분 걸릴 수 있음)",
        host, chosen_port,
    )

    try:
        # log_config=None: uvicorn 의 자체 dictConfig 호출을 *건너뛰어* 본
        # launcher 의 root logger (FileHandler + StreamHandler) 가 유지된다.
        # 결과적으로 uvicorn 의 "Started server process" / "Application startup
        # complete" 같은 boot marker 가 desktop-backend.log 에 기록된다.
        uvicorn.run(
            "app.main:app",
            host=host,
            port=chosen_port,
            log_level="info",
            access_log=False,
            log_config=None,
        )
    except SystemExit as exc:   # pragma: no cover — uvicorn raises on signal
        log.info("backend exited (SystemExit code=%s)", exc.code)
        return int(exc.code or 0)
    except Exception as exc:   # pragma: no cover — defensive
        # stack trace 전체를 log 에 — migration 실패시 stderr 원문 보존.
        log.exception("backend crashed: %s", exc)
        return 3
    log.info("backend uvicorn returned normally")
    return 0


if __name__ == "__main__":   # pragma: no cover
    sys.exit(run())
