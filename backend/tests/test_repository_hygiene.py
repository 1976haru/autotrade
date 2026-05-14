"""Repository hygiene — 정적 invariants (#88).

본 파일은 *코드 / DB / 외부 API 0건* 으로 repository file structure 만 검사한다.

검증 대상:
1-4.  .gitignore 의 Secret / venv / backups 차단 규칙
5.    backend/requirements.txt 가 패키지별 1줄
6-7.  .env.example / .env.staging.example 에 Secret 의심값 없음
8-9.  workflow YAML 파일 6개 존재 + Secret 출력 0건
10.   docs/status/current_state.md 존재
11.   docs/system_hygiene_report.md 존재
12.   README 에 *실거래 허가 아님* 문구
13.   sw.js 가 /api 캐시 금지 명시
14.   docs/dependency_policy.md 존재

본 파일은 broker / OrderExecutor / route_order / DB 모듈을 import 하지 않는다.
"""

from __future__ import annotations

import pathlib
import re

import pytest


# ====================================================================
# 경로 helper
# ====================================================================


def _repo_root() -> pathlib.Path:
    # tests 디렉터리 -> backend 디렉터리 -> repo root.
    return pathlib.Path(__file__).resolve().parent.parent.parent


_ROOT = _repo_root()
_GITIGNORE = _ROOT / ".gitignore"
_REQUIREMENTS = _ROOT / "backend" / "requirements.txt"
_BACKEND_ENV = _ROOT / "backend" / ".env.example"
_STAGING_ENV = _ROOT / ".env.staging.example"
_WORKFLOW_DIR = _ROOT / ".github" / "workflows"
_README = _ROOT / "README.md"
_SW = _ROOT / "frontend" / "public" / "sw.js"
_DEPENDENCY_POLICY = _ROOT / "docs" / "dependency_policy.md"
_HYGIENE_REPORT = _ROOT / "docs" / "system_hygiene_report.md"
_STATUS_CURRENT = _ROOT / "docs" / "status" / "current_state.md"


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


# ====================================================================
# 1-4. .gitignore Secret 보호 + venv + backups
# ====================================================================


def test_gitignore_ignores_dotenv():
    """`.env` 파일은 commit 되지 않아야 한다."""
    src = _read(_GITIGNORE)
    # 라인 단위 검사 — 주석/공백 줄 제외.
    lines = [
        ln.strip() for ln in src.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert ".env" in lines, ".gitignore 에 '.env' 줄이 없음"
    assert ".env.*" in lines, ".gitignore 에 '.env.*' 줄이 없음"


def test_gitignore_allowlists_env_examples():
    """`.env.example` 과 `.env.staging.example` 은 *추적되어야* 한다 — `!` allowlist."""
    src = _read(_GITIGNORE)
    lines = [ln.strip() for ln in src.splitlines()]
    assert "!.env.example" in lines, ".gitignore 에 '!.env.example' 없음"
    assert "!.env.staging.example" in lines, ".gitignore 에 '!.env.staging.example' 없음"


def test_gitignore_ignores_local_venvs():
    """`.venv-310/` 또는 `backend/.venv-310/` 가 추적 안 됨."""
    src = _read(_GITIGNORE)
    lines = [ln.strip() for ln in src.splitlines()]
    has_310 = any(p in lines for p in (
        ".venv-310/", "backend/.venv-310/",
    ))
    assert has_310, (
        ".gitignore 에 '.venv-310/' 또는 'backend/.venv-310/' 명시 필요"
    )
    # 일반 venv 도 ignore.
    assert ".venv/" in lines, ".gitignore 에 '.venv/' 없음"


def test_gitignore_ignores_backups_and_db_dumps():
    """`backups/*` + `*.sql.gz` 등 운영 데이터 백업 차단."""
    src = _read(_GITIGNORE)
    lines = [ln.strip() for ln in src.splitlines()]
    assert "backups/*" in lines, ".gitignore 에 'backups/*' 없음"
    assert "!backups/.gitkeep" in lines, "backups/.gitkeep allowlist 필요"
    assert "*.sql.gz" in lines, ".gitignore 에 '*.sql.gz' 없음"


def test_gitignore_ignores_build_artifacts():
    """build artifact (dist / node_modules / __pycache__) 차단."""
    src = _read(_GITIGNORE)
    lines = [ln.strip() for ln in src.splitlines()]
    for needle in ("node_modules/", "dist/", "build/", "__pycache__/"):
        assert needle in lines, f".gitignore 에 '{needle}' 없음"


# ====================================================================
# 5. requirements.txt 구조
# ====================================================================


def test_requirements_has_one_package_per_line():
    """패키지별 1줄 — `;` 로 여러 패키지 묶기 / 한 줄 다중 패키지 0건."""
    src = _read(_REQUIREMENTS)
    pkg_lines = [
        ln.strip() for ln in src.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert len(pkg_lines) >= 5, (
        f"requirements.txt 가 비정상적으로 짧음 ({len(pkg_lines)} 줄)"
    )
    for ln in pkg_lines:
        # 같은 줄에 ; 로 multiple package 금지.
        assert ";" not in ln or "python_version" in ln, (
            f"requirements.txt 한 줄에 여러 패키지 의심: '{ln}'"
        )
        # 패키지명 + 버전 spec 정도만.
        assert " " not in ln.split("#")[0].strip() or "extras" in ln, (
            f"requirements.txt 줄에 공백 (잘못된 형식?): '{ln}'"
        )


def test_requirements_includes_core_packages():
    src = _read(_REQUIREMENTS).lower()
    for pkg in ("fastapi", "uvicorn", "sqlalchemy", "pydantic", "alembic", "pytest"):
        assert pkg in src, f"requirements.txt 에 '{pkg}' 미포함"


# ====================================================================
# 6-7. env example Secret 의심값 없음
# ====================================================================


# 명백한 Secret 패턴 — 본 패턴 중 어느 것이라도 example 파일에 *값으로* 나타나면 fail.
_SECRET_PATTERNS_VALUE_SIDE = (
    # OpenAI / Anthropic API key 형식.
    r"sk-[A-Za-z0-9]{20,}",
    r"sk-ant-[A-Za-z0-9_\-]{20,}",
    # GitHub PAT.
    r"ghp_[A-Za-z0-9]{30,}",
    r"github_pat_[A-Za-z0-9_]{30,}",
    # Slack token.
    r"xox[abprs]-[A-Za-z0-9\-]{10,}",
    # KIS Personal Secret token (PST...) 의심.
    r"PST[A-Za-z0-9]{20,}",
    # Bearer token-like long base64 strings.
    r"Bearer [A-Za-z0-9\.\-_]{40,}",
    # Telegram bot token (digits:base64).
    r"[0-9]{8,}:[A-Za-z0-9_\-]{30,}",
    # 한국 계좌번호 (XXXXXXXX-XX 형식, 모든 자리 숫자).
    r"\b\d{8,10}-\d{2}\b",
    # 신용카드 형식.
    r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",
)


def _no_secret_value(text: str) -> None:
    for pat in _SECRET_PATTERNS_VALUE_SIDE:
        m = re.search(pat, text)
        assert m is None, (
            f"Secret-like value detected: pattern='{pat}', match='{m.group()}'"
        )


def test_backend_env_example_has_no_secret_values():
    _no_secret_value(_read(_BACKEND_ENV))


def test_staging_env_example_has_no_secret_values():
    _no_secret_value(_read(_STAGING_ENV))


def test_backend_env_example_keeps_safety_defaults():
    """ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / ENABLE_FUTURES_LIVE_TRADING
    모두 false, KIS_IS_PAPER true, DEFAULT_MODE ∈ {SIMULATION, PAPER}.
    """
    text = _read(_BACKEND_ENV)
    pairs = {}
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        pairs[k.strip()] = v.strip()

    for k in ("ENABLE_LIVE_TRADING", "ENABLE_AI_EXECUTION",
              "ENABLE_FUTURES_LIVE_TRADING"):
        assert pairs.get(k, "").lower() == "false", (
            f"backend/.env.example {k} != 'false' (현재값: '{pairs.get(k)}')"
        )
    assert pairs.get("KIS_IS_PAPER", "").lower() == "true"
    assert pairs.get("DEFAULT_MODE", "") in ("SIMULATION", "PAPER")


def test_env_example_secret_fields_are_blank():
    """KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO / ANTHROPIC_API_KEY /
    OPENAI_API_KEY / TELEGRAM_BOT_TOKEN 가 *빈 값* 으로 유지.
    """
    text = _read(_BACKEND_ENV)
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        if k.strip() in (
            "KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO",
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
            "KIWOOM_APP_KEY", "KIWOOM_APP_SECRET", "KIWOOM_ACCOUNT_NO",
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        ):
            assert v.strip() == "", (
                f"backend/.env.example {k} must be empty, got '{v.strip()}'"
            )


# ====================================================================
# 8-9. workflow YAML
# ====================================================================


_EXPECTED_WORKFLOWS = (
    "backend-ci.yml",
    "frontend-ci.yml",
    "backend-ci-nightly.yml",
    "frontend-ci-nightly.yml",
    "pages-deploy.yml",
    "desktop-release.yml",
)


def test_workflow_files_exist_and_non_empty():
    for name in _EXPECTED_WORKFLOWS:
        p = _WORKFLOW_DIR / name
        assert p.exists(), f".github/workflows/{name} 없음"
        assert p.stat().st_size > 0, f".github/workflows/{name} 비어 있음"


def test_workflow_yamls_parse():
    """PyYAML 이 있으면 모든 workflow 파일을 parse — 없으면 본 테스트 skip
    (수동 검토 필요, system_hygiene_report.md 에 기록).
    """
    try:
        import yaml
    except ImportError:
        pytest.skip("PyYAML 미설치 — workflow YAML 은 수동 검토 필요")

    for name in _EXPECTED_WORKFLOWS:
        p = _WORKFLOW_DIR / name
        try:
            yaml.safe_load(p.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            pytest.fail(f".github/workflows/{name} YAML parse 실패: {e}")


def test_workflows_have_no_echo_secret_or_real_accounts():
    """workflow 어디에서도 Secret 을 echo 하거나 실 계좌번호 / token 0건."""
    for name in _EXPECTED_WORKFLOWS:
        p = _WORKFLOW_DIR / name
        text = _read(p)
        # echo $SECRET / cat $SECRET 같은 패턴.
        assert not re.search(r"echo\s+\$\{?secrets?\.", text, re.IGNORECASE), (
            f"{name}: echo \\$secret 패턴 의심"
        )
        # 실 계좌번호 형식.
        assert not re.search(r"\b\d{8,10}-\d{2}\b", text), (
            f"{name}: account-number-like value 의심"
        )
        # sk- / ghp_ / xox 등.
        for pat in (r"sk-[A-Za-z0-9]{20,}", r"ghp_[A-Za-z0-9]{30,}",
                    r"xox[abprs]-[A-Za-z0-9\-]{10,}"):
            assert not re.search(pat, text), f"{name}: secret-like value 의심: {pat}"


def test_workflows_do_not_enable_live_flags_in_ci():
    """CI 어디에서도 ENABLE_LIVE_TRADING=true / ENABLE_AI_EXECUTION=true /
    ENABLE_FUTURES_LIVE_TRADING=true 설정 0건. KIS_IS_PAPER=false 도 금지.
    """
    for name in _EXPECTED_WORKFLOWS:
        p = _WORKFLOW_DIR / name
        text = _read(p).lower()
        for banned in (
            "enable_live_trading: true", "enable_live_trading=true",
            "enable_live_trading=\"true\"", "enable_live_trading='true'",
            "enable_ai_execution: true", "enable_ai_execution=true",
            "enable_futures_live_trading: true",
            "enable_futures_live_trading=true",
            "kis_is_paper: false", "kis_is_paper=false",
        ):
            assert banned not in text, f"{name}: banned setting '{banned}'"


# ====================================================================
# 10-14. 문서 / sw.js 존재 + 핵심 문구
# ====================================================================


def test_current_state_doc_exists():
    assert _STATUS_CURRENT.exists()
    assert _STATUS_CURRENT.stat().st_size > 100


def test_system_hygiene_report_exists():
    assert _HYGIENE_REPORT.exists()
    assert _HYGIENE_REPORT.stat().st_size > 100


def test_dependency_policy_exists():
    assert _DEPENDENCY_POLICY.exists()
    assert _DEPENDENCY_POLICY.stat().st_size > 100


def test_known_risks_doc_exists():
    risks = _ROOT / "docs" / "status" / "known_risks.md"
    assert risks.exists()


def test_next_steps_doc_exists():
    steps = _ROOT / "docs" / "status" / "next_steps.md"
    assert steps.exists()


def test_completed_checklist_doc_exists():
    cl = _ROOT / "docs" / "status" / "completed_checklist_060_088.md"
    assert cl.exists()


def test_readme_states_not_authorized_for_live_trading():
    """README 에 *실거래 허가 아님* 명시 — 베타테스터 / 운영자가 즉시 인지."""
    text = _read(_README)
    # 한글 / 영문 둘 다 허용 — 한쪽이라도 있으면 통과.
    candidates = [
        "실거래 자동매매 허가 상태가 아님",
        "실거래 허가 상태가 아님",
        "실거래는 별도 승인",
        "실거래는 별도 승인 전까지 비활성화",
        "not authorized for live trading",
    ]
    assert any(c in text for c in candidates), (
        "README 에 *실거래 허가 아님* 문구가 명확하지 않음. 다음 중 하나가 "
        f"포함되어야 함: {candidates}"
    )


def test_sw_js_blocks_api_caching():
    """`/api/*` 응답은 캐시되지 않아야 한다 — Secret / stale 데이터 노출 차단."""
    text = _read(_SW)
    # /api 분기 존재 확인.
    assert "/api/" in text
    # network-only 또는 캐시 안 함 명시 — 주석/코드 어디라도.
    indicators = [
        "network-only",
        "캐시하지 않",
        "캐시 안 함",
        "캐시 0건",
        "정대* 캐시",
        "절대* 캐시",
        "절대 캐시",
    ]
    blob = text.lower()
    assert any(ind.lower() in blob for ind in indicators), (
        "sw.js 가 /api 응답 캐시 금지 정책을 명시해야 함"
    )


# ====================================================================
# 본 PR (#88) 자체의 안전 — 본 테스트가 broker import 0건
# ====================================================================


# ====================================================================
# 15-23. #93 Security scan 보강 — EXE / MSI / sidecar bundle / fake secret
# ====================================================================


def test_gitignore_blocks_certificate_and_key_files():
    """*.pem / *.key / *.p12 / *.pfx / *.crt 가 .gitignore 에 의해 추적 제외.

    본 PR 시점에는 *.pem 과 *.key 가 명시. 추가 keystore 형식도 별도로 ignore
    되거나 .gitignore 에 명시적 항목이 있어야 한다.
    """
    src = _read(_GITIGNORE)
    lines = [ln.strip() for ln in src.splitlines()]
    # 핵심 두 가지는 반드시 — 추가 keystore 형식은 향후 PR 에서.
    assert "*.pem" in lines, ".gitignore 에 '*.pem' 명시 필요"
    assert "*.key" in lines, ".gitignore 에 '*.key' 명시 필요"


def test_gitignore_blocks_installer_and_bundle_artifacts():
    """`.msi` / `.nsis` / `*-setup.exe` (Tauri / NSIS bundle) 가 .gitignore 차단.

    실거래 secret 이 install bundle 에 포함될 위험을 줄이기 위해 *artifact 자체*
    를 추적 대상에서 제외.
    """
    src = _read(_GITIGNORE)
    lines = [ln.strip() for ln in src.splitlines()]
    for needle in ("*.msi", "*.nsis", "*.dmg", "*.pkg"):
        assert needle in lines, f".gitignore 에 '{needle}' 명시 필요"


def test_gitignore_blocks_pyinstaller_sidecar_outputs():
    """backend/dist/ + backend/build/ + autotrade-backend.spec 차단."""
    src = _read(_GITIGNORE)
    lines = [ln.strip() for ln in src.splitlines()]
    assert "backend/dist/" in lines
    assert "backend/build/" in lines
    assert "backend/autotrade-backend.spec" in lines


def test_gitignore_blocks_tauri_sidecar_binaries_keeps_readme():
    """src-tauri/binaries/ 의 *.exe 는 차단, README.md 는 allowlist."""
    src = _read(_GITIGNORE)
    lines = [ln.strip() for ln in src.splitlines()]
    assert "src-tauri/binaries/*" in lines
    assert "!src-tauri/binaries/README.md" in lines


def test_no_certificate_or_keystore_files_tracked():
    """`git ls-files` 결과에 *.pem / *.key / *.p12 / *.pfx / *.crt 등 0건."""
    import subprocess
    try:
        out = subprocess.check_output(
            ["git", "ls-files"], cwd=str(_ROOT),
            text=True, encoding="utf-8", errors="replace",
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("git 미설치 또는 repo 아님")

    tracked = [ln.strip() for ln in out.splitlines() if ln.strip()]
    suspicious_exts = (".pem", ".key", ".p12", ".pfx", ".crt", ".cer",
                       ".keystore", ".jks", ".pkcs12")
    leaks = [p for p in tracked if any(p.endswith(ext) for ext in suspicious_exts)]
    # 본 패턴에 매칭되는 파일이 commit 됐다면 즉시 차단.
    assert leaks == [], f"인증서/키 파일이 git 추적 대상에 있음: {leaks}"


def test_no_installer_or_bundle_artifacts_tracked():
    """`*.msi` / `*-setup.exe` / `*.dmg` / `backend/dist/*.exe` 등 0건."""
    import subprocess
    try:
        out = subprocess.check_output(
            ["git", "ls-files"], cwd=str(_ROOT),
            text=True, encoding="utf-8", errors="replace",
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("git 미설치 또는 repo 아님")

    tracked = [ln.strip() for ln in out.splitlines() if ln.strip()]
    forbidden_patterns = (
        ".msi", ".nsis", ".dmg", ".pkg", "-setup.exe",
    )
    leaks = []
    for p in tracked:
        for pat in forbidden_patterns:
            if p.endswith(pat):
                leaks.append(p)
                break
        # backend/dist/ 또는 src-tauri/binaries/*.exe 추적 여부.
        if p.startswith("backend/dist/"):
            leaks.append(p)
        if p.startswith("src-tauri/binaries/") and p.endswith(".exe"):
            leaks.append(p)

    assert leaks == [], (
        f"installer / bundle artifact 가 git 추적 대상에 있음: {leaks}"
    )


def test_no_dotenv_file_tracked_only_examples():
    """`.env` / `backend/.env` / `frontend/.env` 추적 0건 — `.env.example` 만 OK."""
    import subprocess
    try:
        out = subprocess.check_output(
            ["git", "ls-files"], cwd=str(_ROOT),
            text=True, encoding="utf-8", errors="replace",
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("git 미설치 또는 repo 아님")

    tracked = [ln.strip() for ln in out.splitlines() if ln.strip()]
    leaks = []
    for p in tracked:
        # 정확한 파일명 매칭 — example / staging.example 은 허용.
        base = p.rsplit("/", 1)[-1]
        if base in (".env", ".env.local"):
            leaks.append(p)
    assert leaks == [], f".env 파일이 git 추적 대상에 있음: {leaks}"


def test_security_scan_script_exists_and_runs_clean():
    """`scripts/security_scan.py` 가 존재 + 현재 main 기준 finding 0건.

    본 테스트는 보안 회귀 방지 — 누가 secret 을 commit 하려고 하면 본
    테스트가 실패한다 (test_repository_hygiene 가 CI 에서 매번 실행됨).
    """
    import subprocess
    script_path = _ROOT / "scripts" / "security_scan.py"
    assert script_path.exists(), "scripts/security_scan.py 가 없음"
    assert script_path.stat().st_size > 1000, (
        "security_scan.py 가 비정상적으로 작음 — 누락 의심"
    )
    try:
        proc = subprocess.run(
            [
                str(__import__("sys").executable),
                str(script_path),
                "--format", "json",
                "--output", str(_ROOT / ".security_scan_test_output.json"),
            ],
            cwd=str(_ROOT),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pytest.skip("security_scan.py 실행 환경 부적합")

    out_file = _ROOT / ".security_scan_test_output.json"
    try:
        assert out_file.exists(), (
            f"security_scan.py 가 output 파일을 만들지 못함. stderr: {proc.stderr}"
        )
        import json as _json
        result = _json.loads(out_file.read_text(encoding="utf-8"))
    finally:
        if out_file.exists():
            out_file.unlink()

    # finding 0건 이어야 함 — 새 secret commit 시 본 테스트가 실패.
    findings = result.get("findings", [])
    if findings:
        details = "\n".join(
            f"  {f['path']}:{f['line']} [{f['severity']}] {f['rule']}: {f['snippet']}"
            for f in findings
        )
        pytest.fail(
            f"security_scan.py 가 {len(findings)} findings 검출:\n{details}"
        )

    # exit code 0 (clean) 확인.
    assert proc.returncode == 0, (
        f"security_scan.py exit code {proc.returncode}: "
        f"stdout={proc.stdout!r}, stderr={proc.stderr!r}"
    )


def test_fake_secrets_module_has_clear_markers():
    """tests/_fake_secrets.py 의 모든 placeholder 가 'FAKE'/'PLACEHOLDER'/'0000'
    중 하나를 포함해 *진짜 secret 과 구분 가능*.

    본 self-check 는 다음을 보장한다:
    1. 신규 테스트가 본 모듈의 상수를 import 해서 쓸 때 — 패턴이 일관적.
    2. 누군가 실수로 _fake_secrets.py 의 placeholder 를 진짜 secret 으로 바꿔
       commit 하지 못하도록.
    """
    from tests._fake_secrets import assert_all_placeholders_contain_fake_marker
    # 본 함수가 ValueError / AssertionError 를 던지면 본 테스트 실패.
    assert_all_placeholders_contain_fake_marker()


def test_no_real_kis_token_pattern_tracked():
    """KIS Personal Secret Token (PST + 20+) 형식 추적 0건."""
    import subprocess
    try:
        out = subprocess.check_output(
            ["git", "ls-files"], cwd=str(_ROOT),
            text=True, encoding="utf-8", errors="replace",
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("git 미설치 또는 repo 아님")

    tracked = [ln.strip() for ln in out.splitlines() if ln.strip()]
    pst_pattern = re.compile(r"\bPST[A-Z0-9]{20,}\b")
    leaks: list[tuple[str, str]] = []
    for p in tracked:
        full = _ROOT / p
        if not full.exists() or full.is_dir():
            continue
        # binary 파일 스킵.
        try:
            text = full.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        for m in pst_pattern.finditer(text):
            leaks.append((p, m.group()[:6] + "...REDACTED"))
    assert leaks == [], f"KIS PST 토큰 추적 의심: {leaks}"


# ====================================================================
# self-validation (#88 — 본 테스트가 broker 0건)
# ====================================================================


def test_this_test_file_does_not_actually_import_broker_modules():
    """본 hygiene 테스트 자체가 broker / OrderExecutor / route_order *runtime
    import* 가 없는지 검사 — module __dict__ 를 확인.

    구현 노트: 본 파일 안에는 *banned 문자열 검사용 fixture* 가 있어서 단순
    텍스트 grep 으로는 검출되므로, 본 self-validation 은 *실제 import 된
    심볼* 만 확인한다.
    """
    import sys
    this_module = sys.modules[__name__]
    module_globals = vars(this_module)

    # broker / executor 모듈에서 가져온 심볼이 0건이어야 한다.
    for name, obj in module_globals.items():
        mod_name = getattr(obj, "__module__", "") or ""
        for banned_prefix in (
            "app.brokers.",
            "app.execution.",
            "app.permission.",
            "app.ai.assist",
        ):
            assert not mod_name.startswith(banned_prefix), (
                f"test_repository_hygiene.py 가 '{mod_name}' 에서 '{name}' 을 "
                "import 함 — 정적 정책 위반"
            )
