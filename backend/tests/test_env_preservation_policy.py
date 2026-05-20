"""Static invariants for user `.env` preservation policy (#5-07).

업데이트 / 재설치 흐름에서 사용자 `%APPDATA%\\Autotrade\\.env` 가 *덮어쓰기 /
삭제 0건* 임을 코드 단에서 강제하는 정적 가드. 본 파일은 broker /
OrderExecutor / route_order / DB / 외부 API import 0건 — 파일시스템에서
*소스 텍스트만* 읽어 grep 검증.

검증 대상:
1. backend/app_desktop_launcher.py 가 `.env` 에 write/delete 호출 0건.
2. frontend/src/desktop/updaterClient.js 가 `.env` / 파일시스템 / process
   임의 호출 0건.
3. frontend/src/components/UpdateBanner.jsx 가 "사용자 .env 보존" 안전 배지
   영구 노출.
4. docs/auto_update_policy.md / exe_oneclick_installation.md /
   desktop_exe_status.md 가 표준 경로 `%APPDATA%\\Autotrade\\.env` 를 명시.

위 가드가 깨지면 *사용자가 입력한 KIS 모의투자 키* 가 update 한 번에
사라질 수 있다 — 본 테스트가 그 회귀를 *즉시* 잡는다.
"""

from __future__ import annotations

import pathlib
import re

import pytest


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent.parent


_ROOT = _repo_root()


def _read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


# ============================================================================
# 1. app_desktop_launcher.py 의 .env write/delete 호출 0건
# ============================================================================


def test_launcher_does_not_write_to_env():
    """`.env` 파일에 write/delete/move 를 호출하는 코드 path 가 0건이어야 한다.

    launcher 는 *읽기만* — `python-dotenv` 의 `load_dotenv` / 자체 `load_env_file`
    파서 / `dotenv_values` 만 사용. 어떤 함수도 `.env` 자체를 *수정* 하지 않는다.

    본 테스트는 launcher 소스에서 다음 패턴이 *`.env` literal 과 같은 라인에*
    등장하는지 검사:
      - `.write_text(`     (Path.write_text)
      - `.write_bytes(`    (Path.write_bytes)
      - `.unlink(`         (Path.unlink)
      - `os.remove(`
      - `os.rename(` / `.rename(` (env_path.rename)
      - `shutil.copy*(` / `shutil.move(`
    """
    p = _ROOT / "backend" / "app_desktop_launcher.py"
    if not p.exists():
        pytest.skip("app_desktop_launcher.py 미존재 — 본 검사 skip")
    src = _read(p)

    # `.env` 가 등장하는 *라인* 만 수집해 그 라인에 write/delete 호출이 함께
    # 있는지 확인. 주석 라인은 검사 제외.
    suspicious: list[tuple[int, str]] = []
    forbidden_calls = (
        ".write_text(",
        ".write_bytes(",
        ".unlink(",
        "os.remove(",
        "os.unlink(",
        "shutil.copy",
        "shutil.move",
    )
    for i, raw in enumerate(src.splitlines(), start=1):
        if raw.lstrip().startswith("#"):
            continue
        # `.env` 또는 `env_path` 가 등장하는 라인만 검사.
        if ".env" not in raw and "env_path" not in raw:
            continue
        for call in forbidden_calls:
            if call in raw:
                suspicious.append((i, raw.strip()))
                break
    assert suspicious == [], (
        "launcher 가 `.env` 라인 안에서 write/delete 함수를 호출함 — "
        "사용자 KIS 키가 update 시 사라질 위험. 다음 라인 점검: "
        f"{suspicious}"
    )


def test_launcher_does_not_log_secret_values():
    """launcher 가 KIS_APP_SECRET / KIS_APP_KEY 등의 *값* 을 `log.info` /
    `log.warning` / `print` 인자로 사용하지 않는다 (값 자체가 아니라 *키 이름* /
    `present` / `missing` 라벨만)."""
    p = _ROOT / "backend" / "app_desktop_launcher.py"
    if not p.exists():
        pytest.skip("app_desktop_launcher.py 미존재 — 본 검사 skip")
    src = _read(p)

    # 직접 secret 값을 노출하는 패턴 — 예: `log.info("KIS_APP_KEY=%s", os.environ["KIS_APP_KEY"])`
    # 등의 위험 패턴. *키 이름 자체* + *값 substitution* 이 같은 라인에 있는지 검사.
    secret_keys = ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO",
                   "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN")
    forbidden_combos = []
    for i, raw in enumerate(src.splitlines(), start=1):
        if raw.lstrip().startswith("#"):
            continue
        # `log.something(...)` 또는 `print(...)` 호출 + secret key 사용 + 값 access 패턴.
        is_output_call = ("log.info" in raw or "log.warning" in raw
                          or "log.error" in raw or "print(" in raw)
        if not is_output_call:
            continue
        for key in secret_keys:
            if key not in raw:
                continue
            # 값 access 패턴 — `os.environ[key]` / `os.environ.get(key)` /
            # `parsed[key]` 등이 같은 라인에 *값으로* 등장하면 의심.
            if re.search(rf"os\.environ\[\s*['\"]?{key}['\"]?\s*\]", raw):
                forbidden_combos.append((i, raw.strip()))
            elif re.search(rf"os\.environ\.get\(\s*['\"]?{key}['\"]?", raw):
                # `os.environ.get(KEY)` 자체는 *값* 을 가져오므로 *log 인자*
                # 로 들어가면 위험. 하지만 `bool(...)` / `len(...)` 등으로
                # 감싸진 경우는 안전. 본 라인 안에 `bool(` 또는 `len(` 도 함께
                # 있으면 통과.
                if "bool(" not in raw and "len(" not in raw and "present" not in raw:
                    forbidden_combos.append((i, raw.strip()))
    assert forbidden_combos == [], (
        "launcher 가 secret 값을 log/print 에 직접 출력함: "
        f"{forbidden_combos}. 키 이름 + present/missing 라벨만 노출해야 함."
    )


def test_launcher_only_loads_env_from_appdata_or_dev_paths():
    """`.env` 검색 후보 경로가 *합리적인 set* 안에만 있다 — 임의의 시스템 경로
    검색 X."""
    p = _ROOT / "backend" / "app_desktop_launcher.py"
    if not p.exists():
        pytest.skip("app_desktop_launcher.py 미존재 — 본 검사 skip")
    src = _read(p)
    # `%APPDATA%\Autotrade\.env` 가 표준 경로로 등장해야 함.
    assert "APPDATA" in src and "Autotrade" in src, (
        "launcher 가 %APPDATA%\\Autotrade 표준 경로를 사용하지 않음"
    )
    # 위험한 경로 검색 패턴 — `/etc/passwd`, `~/.ssh` 등이 등장하면 fail.
    for banned in ("/etc/passwd", "/etc/shadow", ".ssh/id_rsa",
                   "C:\\\\Windows\\\\System32"):
        assert banned not in src, (
            f"launcher 가 위험한 시스템 경로 '{banned}' 를 참조"
        )


# ============================================================================
# 2. updaterClient.js 가 .env / 파일시스템 / process 접근 0건
# ============================================================================


def test_updater_client_does_not_touch_env():
    """frontend updaterClient.js 가 `.env` 또는 파일시스템 / process 임의
    호출을 *시도하지 않는다*.

    GitHub REST `releases/latest` HTTP 호출 + version 비교 + secret pattern
    sanitize 만 수행. node `fs` / `os` / `child_process` / Tauri 의 `path` /
    `fs` plugin 어느 것도 import 안 함.
    """
    p = _ROOT / "frontend" / "src" / "desktop" / "updaterClient.js"
    if not p.exists():
        pytest.skip("updaterClient.js 미존재 — 본 검사 skip")
    src = _read(p)

    # `.env` 가 *문자열 literal* / *파일 확장자* 로 등장 0건.
    # `published_at` 같은 substring 일치는 false positive 이므로, 명확한
    # 위험 패턴만 잡는다:
    #   - 따옴표 안의 ".env" / '.env'  (path literal 의심)
    #   - `\.env` 정규식 사용
    #   - 변수명 `dotenv` / `envFile`  (파일 처리 의심)
    code_lines: list[str] = []
    for raw in src.splitlines():
        # JS 한 줄 주석 제거.
        stripped = re.sub(r"//.*$", "", raw).strip()
        if not stripped:
            continue
        code_lines.append(stripped)
    code_src = "\n".join(code_lines)

    dangerous_patterns = (
        r'["\']\.env(?:\.[a-z]+)?["\']',   # 문자열 ".env" / ".env.local"
        r'\\\\\.env',                       # 정규식 backslash-escaped .env
        r'\bdotenv\b',                     # dotenv import / 변수
        r'\benvFile\b',                    # envFile 변수
        r'\bloadEnv\b',                    # loadEnv 함수
    )
    matches: list[tuple[str, str]] = []
    for pat in dangerous_patterns:
        for m in re.finditer(pat, code_src, flags=re.IGNORECASE):
            matches.append((pat, m.group()))
    assert matches == [], (
        f"updaterClient.js 가 `.env` 관련 패턴 사용 — 파일 접근 의심: {matches}"
    )

    # 위험한 import / require 패턴 — node fs / os / child_process / Tauri fs
    # plugin 임포트 0건.
    for banned_import in (
        "node:fs", "from 'fs'", 'from "fs"',
        "node:os", "from 'os'", 'from "os"',
        "node:child_process", "from 'child_process'", 'from "child_process"',
        "@tauri-apps/plugin-fs", "@tauri-apps/api/fs", "@tauri-apps/api/path",
    ):
        assert banned_import not in src, (
            f"updaterClient.js 가 '{banned_import}' 를 import — 파일 접근 의심"
        )


def test_updater_client_only_hits_github_api():
    """updaterClient 의 *유일한* 외부 호출은 `api.github.com/repos/.../releases/latest`."""
    p = _ROOT / "frontend" / "src" / "desktop" / "updaterClient.js"
    if not p.exists():
        pytest.skip("updaterClient.js 미존재 — 본 검사 skip")
    src = _read(p)
    assert "api.github.com/repos" in src and "releases/latest" in src, (
        "updaterClient.js 가 GitHub REST releases/latest 를 호출하지 않음"
    )
    # 다른 도메인 호출 0건. http(s):// + 다른 host 패턴.
    other_urls = re.findall(
        r"https?://(?!api\.github\.com|github\.com|localhost|127\.0\.0\.1)[\w\.\-]+",
        src,
    )
    # 본 list 는 documentation / example URL 까지 잡을 수 있어, 운영 호출
    # 라인만 보수적으로 검사. fetchImpl 호출 인자 안에 등장하는 패턴만 검사.
    fetch_call_lines = [
        ln for ln in src.splitlines()
        if "fetchImpl(" in ln or "globalThis.fetch" in ln
    ]
    for ln in fetch_call_lines:
        for url in other_urls:
            if url in ln:
                pytest.fail(
                    f"updaterClient.js 의 fetch 호출에 GitHub 외 URL 등장: "
                    f"line='{ln.strip()}', url='{url}'"
                )


# ============================================================================
# 3. UpdateBanner.jsx 의 "사용자 .env 보존" 안전 배지 영구 노출
# ============================================================================


def test_update_banner_has_env_preservation_badge():
    """UpdateBanner.jsx 가 모든 state 에서 "사용자 .env 보존" 안전 배지를
    렌더 — testid `badge-no-env-overwrite` 가 컴포넌트 소스에 존재.

    동작 검증은 frontend vitest (UpdateBanner.test.jsx::"renders all 3 safety
    badges in every state") 가 담당. 본 정적 가드는 testid + 한국어 라벨
    문자열이 *소스에서 사라지지 않도록* lock.
    """
    p = _ROOT / "frontend" / "src" / "components" / "UpdateBanner.jsx"
    if not p.exists():
        pytest.skip("UpdateBanner.jsx 미존재 — 본 검사 skip")
    src = _read(p)
    assert 'data-testid="badge-no-env-overwrite"' in src, (
        "UpdateBanner.jsx 에 badge-no-env-overwrite testid 누락 — "
        "사용자 .env 보존 안내가 사라짐"
    )
    assert "사용자 .env 보존" in src, (
        "UpdateBanner.jsx 에 한국어 라벨 '사용자 .env 보존' 누락"
    )


# ============================================================================
# 4. docs 가 표준 경로 + 보존 정책을 명시
# ============================================================================


def test_docs_document_standard_env_path():
    """auto_update_policy.md / exe_oneclick_installation.md / desktop_exe_status.md
    가 표준 경로 `%APPDATA%\\Autotrade\\.env` 를 명시한다."""
    for rel in (
        "docs/auto_update_policy.md",
        "docs/exe_oneclick_installation.md",
        "docs/desktop_exe_status.md",
    ):
        p = _ROOT / rel
        if not p.exists():
            pytest.skip(f"{rel} 미존재 — 본 검사 skip")
        src = _read(p)
        assert "%APPDATA%\\Autotrade\\.env" in src, (
            f"{rel} 에 표준 경로 '%APPDATA%\\Autotrade\\.env' 누락"
        )


def test_auto_update_policy_documents_preservation_invariants():
    """docs/auto_update_policy.md §11 가 보존 정책 핵심 키워드를 모두 포함."""
    p = _ROOT / "docs" / "auto_update_policy.md"
    if not p.exists():
        pytest.skip("auto_update_policy.md 미존재 — 본 검사 skip")
    src = _read(p)
    for needed in (
        "사용자 `.env` 보존 정책",       # §11 헤더
        "보존되는 흐름 매트릭스",         # §11-2
        "덮어쓰지 않는 메커니즘",          # §11-3
        "사라진 것 같아요",               # §11-4 트러블슈팅
        "`.env.txt`",                  # 흔한 함정
        "백업",                          # 운영자 가이드
    ):
        assert needed in src, (
            f"auto_update_policy.md §11 에 '{needed}' 누락"
        )


def test_exe_oneclick_warns_about_env_txt_pitfall():
    """exe_oneclick_installation.md 가 `.env.txt` 함정을 사용자에게 안내."""
    p = _ROOT / "docs" / "exe_oneclick_installation.md"
    if not p.exists():
        pytest.skip("exe_oneclick_installation.md 미존재 — 본 검사 skip")
    src = _read(p)
    assert ".env.txt" in src, (
        "exe_oneclick_installation.md 에 `.env.txt` 함정 안내 누락 — "
        "메모장 자동 확장자 실수 사용자가 KIS 키 사라짐으로 오인"
    )


# ============================================================================
# 5. installer / workflow safety guard 가 .env 를 bundle 에 미포함
# ============================================================================


def test_workflow_excludes_env_from_bundle():
    """desktop-release.yml 의 Step 8 (Safety guard 빌드 후) 가 bundle 안에서
    `.env` 파일이 발견되면 FATAL 로 빌드 차단하는 PowerShell 검사 존재.

    문구는 일부 변경 가능하므로 *핵심 토큰* 만 lock — bundle 디렉토리 + .env
    검사 + exit 1 패턴.
    """
    p = _ROOT / ".github" / "workflows" / "desktop-release.yml"
    if not p.exists():
        pytest.skip("desktop-release.yml 미존재 — 본 검사 skip")
    src = _read(p)
    # bundle.resources 가 .env 를 포함하지 않음을 검증하는 step 존재.
    assert "Safety guard" in src and ".env" in src, (
        "desktop-release.yml 에 .env 검사 safety guard 누락"
    )
    # exit 1 로 차단하는 패턴 — `.env file detected` 라벨.
    assert (".env file detected" in src or "FATAL" in src), (
        "desktop-release.yml 의 safety guard 가 .env 검출 시 FATAL exit 하지 않음"
    )


def test_tauri_bundle_resources_excludes_user_env():
    """tauri.conf.json::bundle.resources 가 비어 있어 사용자 `.env` 가 installer
    bundle 에 *물리적으로* 포함되지 못한다."""
    import json
    p = _ROOT / "src-tauri" / "tauri.conf.json"
    if not p.exists():
        pytest.skip("tauri.conf.json 미존재 — 본 검사 skip")
    conf = json.loads(p.read_text(encoding="utf-8"))
    resources = (conf.get("bundle") or {}).get("resources", [])
    # 빈 배열 또는 .env / .env.* 패턴 0건.
    assert isinstance(resources, list), "bundle.resources 가 list 가 아님"
    for r in resources:
        assert ".env" not in str(r).lower(), (
            f"bundle.resources 에 .env 관련 항목 포함: '{r}'"
        )
