"""체크리스트 #69: DB 백업 정책 정적 검증 + SQLite smoke.

본 테스트는 scripts/backup_db.sh / backup_db.ps1 / restore_db.sh의 *정책
invariant*를 검사한다:

  1. .env / 운영자 토큰 / API key를 백업하지 않는다 (script source 검사)
  2. DATABASE_URL을 redacted form으로만 출력한다 (password 노출 0건)
  3. Secret 패턴이 DATABASE_URL에 들어있으면 즉시 중단
  4. .gitignore가 backup 파일 패턴을 git 추적에서 제외
  5. SQLite 백업 smoke — tmp DB로 실제 실행 (운영 DB 미접근)

본 테스트는 *실제 KIS / Anthropic / Telegram API 호출 0건* + *실제 backups/
디렉터리 미접근*.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR  = PROJECT_ROOT / "scripts"


# ====================================================================
# 1. Script 존재 + 형식 invariant
# ====================================================================


def test_backup_sh_exists_and_starts_with_bash_shebang():
    p = SCRIPTS_DIR / "backup_db.sh"
    assert p.exists(), f"missing: {p}"
    text = p.read_text(encoding="utf-8")
    first = text.splitlines()[0]
    assert first.startswith("#!"), f"missing shebang: {first!r}"
    assert "bash" in first, f"not a bash script: {first!r}"


def test_backup_ps1_exists():
    p = SCRIPTS_DIR / "backup_db.ps1"
    assert p.exists(), f"missing: {p}"


def test_restore_sh_exists_and_bash_shebang():
    p = SCRIPTS_DIR / "restore_db.sh"
    assert p.exists()
    first = p.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("#!") and "bash" in first


# ====================================================================
# 2. Source invariants — Secret 백업 금지
# ====================================================================


@pytest.mark.parametrize("script", ["backup_db.sh", "backup_db.ps1", "restore_db.sh"])
def test_script_does_not_copy_env_files(script):
    """scripts는 .env / .env.staging / secrets를 *복사하지 않는다*. source에
    'cp .env' / 'Copy-Item .env' 같은 패턴이 있으면 invariant 위반."""
    src = (SCRIPTS_DIR / script).read_text(encoding="utf-8")
    forbidden_lines = []
    for line in src.splitlines():
        # 주석은 제외 (bash: #로 시작, PowerShell: #로 시작)
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # .env 파일 직접 복사 / 백업 의심 패턴
        if "cp" in stripped and ".env" in stripped and "tar" not in stripped:
            forbidden_lines.append(line)
        if "Copy-Item" in stripped and ".env" in stripped:
            forbidden_lines.append(line)
        if "tar" in stripped and ".env" in stripped:
            forbidden_lines.append(line)
    assert forbidden_lines == [], (
        f"{script} appears to copy .env files: {forbidden_lines}"
    )


@pytest.mark.parametrize("script", ["backup_db.sh", "backup_db.ps1", "restore_db.sh"])
def test_script_redacts_url_before_logging(script):
    """script는 DATABASE_URL을 redact한 form으로만 출력한다."""
    src = (SCRIPTS_DIR / script).read_text(encoding="utf-8")
    # redaction helper가 존재해야 함
    has_redact = ("redact_url" in src) or ("Redact-Url" in src) or ("Redact_url" in src)
    assert has_redact, f"{script} does not define a redaction helper"

    # log에 raw $DATABASE_URL을 echo하는 패턴 검사 — 운영자가 password 노출
    # bash 패턴: echo .* $DATABASE_URL 또는 log .* $DATABASE_URL
    # PowerShell 패턴: Write-Host .* $DatabaseUrl (Redact 안 거치고)
    lines = src.splitlines()
    violations = []
    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
        if line.startswith("#"):
            continue
        # bash log / echo with raw URL
        if ("log " in line or "echo " in line) and "$DATABASE_URL" in line and "redact" not in line.lower():
            violations.append(f"{script}:{i+1}: {line}")
        # PowerShell raw URL log
        if "Write-Log" in line and "$DatabaseUrl" in line and "Redact" not in line:
            violations.append(f"{script}:{i+1}: {line}")
    assert violations == [], (
        f"raw DATABASE_URL leak detected:\n" + "\n".join(violations)
    )


def test_backup_sh_aborts_on_secret_looking_input():
    """DATABASE_URL에 KIS_APP_SECRET= / TELEGRAM_BOT_TOKEN= 같은 패턴이 있으면
    abort_if_secret_input이 즉시 exit 한다."""
    src = (SCRIPTS_DIR / "backup_db.sh").read_text(encoding="utf-8")
    assert "abort_if_secret_input" in src
    for pat in ("KIS_APP_KEY", "KIS_APP_SECRET", "TELEGRAM_BOT_TOKEN",
                "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        assert pat in src, f"abort guard missing {pat} pattern"


def test_ps1_aborts_on_secret_looking_input():
    src = (SCRIPTS_DIR / "backup_db.ps1").read_text(encoding="utf-8")
    assert "Abort-IfSecretInput" in src
    for pat in ("KIS_APP_KEY", "KIS_APP_SECRET", "TELEGRAM_BOT_TOKEN",
                "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        assert pat in src


# ====================================================================
# 3. .gitignore 검증
# ====================================================================


def test_gitignore_excludes_backup_artifacts():
    gi = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pat in ("backups/*", "*.sql.gz", "*.db.backup", "*.sqlite.bak"):
        assert pat in gi, f".gitignore missing: {pat}"


def test_gitkeep_in_backups_directory():
    p = PROJECT_ROOT / "backups" / ".gitkeep"
    assert p.exists(), "backups/.gitkeep placeholder should exist"


def test_backend_dockerignore_excludes_backups():
    p = PROJECT_ROOT / "backend" / ".dockerignore"
    text = p.read_text(encoding="utf-8")
    assert "backups/" in text, "backend/.dockerignore must exclude backups/"
    for pat in ("*.sql.gz", "*.db.backup", "*.sqlite.bak"):
        assert pat in text, f"backend/.dockerignore missing: {pat}"


# ====================================================================
# 4. SQLite backup smoke (tmp DB)
# ====================================================================


def _has_bash() -> bool:
    """git bash / WSL bash 존재 여부."""
    return shutil.which("bash") is not None


def _path_is_ascii() -> bool:
    """PROJECT_ROOT 절대 경로가 ASCII만 사용하는지. 한글 / 비ASCII 폴더에서는
    Windows + git-bash subprocess가 인코딩 문제로 script를 못 찾는다. 운영
    경로(`C:\\trade\\autotrade`)는 ASCII이므로 정상 동작."""
    try:
        str(PROJECT_ROOT).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


_SMOKE_PRECONDITIONS = (not _has_bash()) or (not _path_is_ascii())
_SMOKE_SKIP_REASON = (
    "bash unavailable or non-ASCII project path "
    "(Windows git-bash subprocess limitation)"
)


@pytest.mark.skipif(_SMOKE_PRECONDITIONS, reason=_SMOKE_SKIP_REASON)
def test_sqlite_backup_smoke_creates_file(tmp_path):
    """tmp SQLite DB에 backup_db.sh 실행 → backup 파일 생성 + 크기 > 0.
    *운영 DB 미접근* — DATABASE_URL을 tmp 경로로 강제."""
    # 1) tmp SQLite DB 생성
    db_path = tmp_path / "test_autotrade.db"
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE smoke (id INTEGER PRIMARY KEY, name TEXT)")
    con.execute("INSERT INTO smoke (name) VALUES (?)", ("hello",))
    con.commit()
    con.close()
    assert db_path.exists() and db_path.stat().st_size > 0

    # 2) backup_db.sh 실행 — BACKUP_DIR도 tmp로 격리
    backup_dir = tmp_path / "out"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    env["BACKUP_DIR"]   = str(backup_dir)
    env["BACKUP_RETENTION_DAYS"] = "0"  # smoke에서는 retention 안 돌림

    result = subprocess.run(
        ["bash", str(SCRIPTS_DIR / "backup_db.sh")],
        env=env, capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, (
        f"backup_db.sh failed: stdout=\n{result.stdout}\nstderr=\n{result.stderr}"
    )

    # 3) backup 파일 생성 확인
    backups = list(backup_dir.glob("autotrade_backup_*.sqlite"))
    assert len(backups) == 1, f"expected 1 backup, got {backups}"
    out_file = backups[0]
    assert out_file.stat().st_size > 0


@pytest.mark.skipif(_SMOKE_PRECONDITIONS, reason=_SMOKE_SKIP_REASON)
def test_sqlite_backup_smoke_does_not_leak_url_in_output(tmp_path):
    """script stdout / stderr 어디에도 password가 들어 있으면 안 됨.
    SQLite URL에는 password가 없지만, 본 검사는 redaction helper가 활성됨을
    확인 — DATABASE_URL을 그대로 echo하지 않음을 검증."""
    db_path = tmp_path / "secret_test.db"
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE t (id INTEGER)")
    con.close()
    backup_dir = tmp_path / "out"
    # 일부러 path에 secret-looking suffix는 아니지만 redaction이 적용되는지
    # 확인용 — sqlite scheme은 redaction이 no-op이지만 helper가 호출된 흔적이
    # log에 있어야 함.
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    env["BACKUP_DIR"]   = str(backup_dir)
    env["BACKUP_RETENTION_DAYS"] = "0"

    result = subprocess.run(
        ["bash", str(SCRIPTS_DIR / "backup_db.sh")],
        env=env, capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace",
    )
    combined = result.stdout + result.stderr
    # 본 SQLite URL은 password 없음 — redaction 적용 X. 단, "url:" 라인은
    # 존재해야 redaction helper가 동작하는 흔적.
    assert "url:" in combined
    # password / token 단어 0건
    forbidden = ("password", "PASSWORD", "secret=", "SECRET=", "Bearer ")
    for needle in forbidden:
        assert needle not in combined, (
            f"forbidden token leaked to stdout/err: {needle!r}\nout: {combined}"
        )


@pytest.mark.skipif(_SMOKE_PRECONDITIONS, reason=_SMOKE_SKIP_REASON)
def test_dry_run_does_not_create_backup_file(tmp_path):
    """BACKUP_DRY_RUN=true → 실제 파일 생성 0건."""
    db_path = tmp_path / "dry_run.db"
    con = sqlite3.connect(str(db_path))
    con.close()
    backup_dir = tmp_path / "out"
    env = os.environ.copy()
    env["DATABASE_URL"]   = f"sqlite:///{db_path}"
    env["BACKUP_DIR"]     = str(backup_dir)
    env["BACKUP_DRY_RUN"] = "true"
    env["BACKUP_RETENTION_DAYS"] = "0"

    result = subprocess.run(
        ["bash", str(SCRIPTS_DIR / "backup_db.sh")],
        env=env, capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stderr
    assert "DRY_RUN" in result.stdout
    files = list(backup_dir.glob("autotrade_backup_*.sqlite")) if backup_dir.exists() else []
    assert files == [], f"DRY_RUN unexpectedly created: {files}"


@pytest.mark.skipif(_SMOKE_PRECONDITIONS, reason=_SMOKE_SKIP_REASON)
def test_backup_aborts_on_secret_looking_url(tmp_path):
    """DATABASE_URL이 'KIS_APP_KEY=...'를 포함하면 즉시 중단."""
    backup_dir = tmp_path / "out"
    env = os.environ.copy()
    env["DATABASE_URL"] = "sqlite:///dummy KIS_APP_KEY=ABCDEFG"
    env["BACKUP_DIR"]   = str(backup_dir)

    result = subprocess.run(
        ["bash", str(SCRIPTS_DIR / "backup_db.sh")],
        env=env, capture_output=True, text=True, timeout=10,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode != 0, "script should reject secret-looking URL"
    assert "secret" in (result.stderr + result.stdout).lower() or \
           "refusing" in (result.stderr + result.stdout).lower()


@pytest.mark.skipif(_SMOKE_PRECONDITIONS, reason=_SMOKE_SKIP_REASON)
def test_backup_aborts_on_missing_database_url():
    """DATABASE_URL 미지정 → exit 1 + 사용법 안내."""
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    result = subprocess.run(
        ["bash", str(SCRIPTS_DIR / "backup_db.sh")],
        env=env, capture_output=True, text=True, timeout=10,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode != 0
    assert "DATABASE_URL" in (result.stderr + result.stdout)
